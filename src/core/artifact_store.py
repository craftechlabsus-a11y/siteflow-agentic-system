"""Artifact Store determinista con deduplicación por SHA-256 en workspace_cache/."""
import hashlib
import json
import os
import re
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_filename(filename: str) -> str:
    """Normaliza un nombre de archivo eliminando rutas y caracteres inseguros."""
    base = os.path.basename(filename or "archivo.bin").strip()
    cleaned = _SAFE_FILENAME_RE.sub("_", base).strip("._")
    return cleaned or "archivo.bin"


def validate_file_magic(head: bytes, file_type: str) -> bool:
    """Valida firmas básicas (magic bytes) para formatos de ingeniería soportados."""
    ftype = (file_type or "").lower().lstrip(".")
    if not head:
        return False
    if ftype == "pdf":
        return head.lstrip().startswith(b"%PDF-")
    if ftype == "ifc":
        return b"ISO-10303-21" in head[:512]
    if ftype == "png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if ftype in ("jpg", "jpeg"):
        return head.startswith(b"\xff\xd8\xff")
    if ftype in ("docx", "xlsx", "zip"):
        return head.startswith(b"PK\x03\x04")
    if ftype == "dwg":
        return head.startswith(b"AC10")
    if ftype == "xer":
        return b"ERMHDR" in head[:256]
    return True


def _default_store_dir() -> str:
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cache_root = os.getenv("WORKSPACE_CACHE_DIR") or os.path.join(root, "workspace_cache")
    return os.getenv("ARTIFACT_STORE_DIR") or os.path.join(cache_root, "artifacts")


@dataclass
class ArtifactMeta:
    artifact_id: str
    sha256: str
    project_id: str
    owner_id: str
    filename: str
    file_type: str
    size_bytes: int
    storage_path: str
    index_status: str
    metadata: Dict[str, Any]
    created_at: str


class ArtifactStore:
    """Almacén local indexado de artefactos por SHA-256 para evitar re-parseo y file_bytes en RAM."""

    def __init__(self, base_dir: Optional[str] = None) -> None:
        self.base_dir = os.path.realpath(base_dir or _default_store_dir())
        self.blobs_dir = os.path.join(self.base_dir, "blobs")
        self.db_path = os.path.join(self.base_dir, "artifacts.db")
        os.makedirs(self.blobs_dir, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    sha256 TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    file_type TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    storage_path TEXT NOT NULL,
                    index_status TEXT NOT NULL DEFAULT 'pending',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_artifacts_sha_proj ON artifacts(sha256, project_id)"
            )

    @staticmethod
    def compute_sha256(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def compute_file_sha256(file_path: str, chunk_size: int = 1024 * 1024) -> str:
        """Calcula SHA-256 por bloques para archivos BIM/PDF masivos sin saturar la RAM."""
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(chunk_size):
                h.update(chunk)
        return h.hexdigest()

    def register_file_path(
        self,
        file_path: str,
        *,
        project_id: str = "default",
        owner_id: str = "agent",
        file_type: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        validate_magic: bool = True,
    ) -> ArtifactMeta:
        """Registra un archivo local por ruta (sin pasar file_bytes por JSON/MCP)."""
        real_path = os.path.realpath(file_path)
        if not os.path.isfile(real_path):
            raise FileNotFoundError(f"Archivo no encontrado: {file_path}")
        size_bytes = os.path.getsize(real_path)
        if size_bytes == 0:
            raise ValueError(f"El archivo está vacío: {file_path}")

        clean_name = sanitize_filename(os.path.basename(real_path))
        ext = os.path.splitext(clean_name)[1].lstrip(".").lower() or (file_type or "bin")
        ftype = (file_type or ext).lower()

        if validate_magic:
            with open(real_path, "rb") as f:
                head = f.read(512)
            if not validate_file_magic(head, ftype):
                raise ValueError(
                    f"El contenido de '{clean_name}' no coincide con el formato esperado ({ftype})."
                )

        sha = self.compute_file_sha256(real_path)
        artifact_id = f"{project_id}:{sha[:16]}"

        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM artifacts WHERE sha256 = ? AND project_id = ?",
                (sha, project_id),
            ).fetchone()
            if row is not None:
                return self._row_to_meta(row)

            blob_path = os.path.join(self.blobs_dir, f"{sha}.{ext}")
            if not os.path.exists(blob_path):
                shutil.copy2(real_path, blob_path)

            now = datetime.now(timezone.utc).isoformat()
            meta = dict(metadata or {})
            meta.setdefault("source_path", real_path)
            meta_json = json.dumps(meta, ensure_ascii=False)
            conn.execute(
                """
                INSERT INTO artifacts (
                    artifact_id, sha256, project_id, owner_id, filename, file_type,
                    size_bytes, storage_path, index_status, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'registered', ?, ?)
                """,
                (
                    artifact_id,
                    sha,
                    project_id,
                    owner_id,
                    clean_name,
                    ftype,
                    size_bytes,
                    blob_path,
                    meta_json,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()
            return self._row_to_meta(row)

    def register_bytes(
        self,
        content: bytes,
        filename: str,
        *,
        project_id: str = "default",
        owner_id: str = "system",
        file_type: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        validate_magic: bool = True,
    ) -> ArtifactMeta:
        """Registra un artefacto de forma idempotente por (sha256, project_id)."""
        if not content:
            raise ValueError("El archivo está vacío.")

        clean_name = sanitize_filename(filename or "archivo.bin")
        ext = os.path.splitext(clean_name)[1].lstrip(".").lower() or (file_type or "bin")
        ftype = (file_type or ext).lower()

        if validate_magic and not validate_file_magic(content[:512], ftype):
            raise ValueError(
                f"El contenido de '{clean_name}' no coincide con el formato esperado ({ftype})."
            )

        sha = self.compute_sha256(content)
        artifact_id = f"{project_id}:{sha[:16]}"

        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM artifacts WHERE sha256 = ? AND project_id = ?",
                (sha, project_id),
            ).fetchone()
            if row is not None:
                return self._row_to_meta(row)

            blob_path = os.path.join(self.blobs_dir, f"{sha}.{ext}")
            if not os.path.exists(blob_path):
                with open(blob_path, "wb") as f:
                    f.write(content)

            now = datetime.now(timezone.utc).isoformat()
            meta_json = json.dumps(metadata or {}, ensure_ascii=False)
            conn.execute(
                """
                INSERT INTO artifacts (
                    artifact_id, sha256, project_id, owner_id, filename, file_type,
                    size_bytes, storage_path, index_status, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'registered', ?, ?)
                """,
                (
                    artifact_id,
                    sha,
                    project_id,
                    owner_id,
                    clean_name,
                    ftype,
                    len(content),
                    blob_path,
                    meta_json,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()
            return self._row_to_meta(row)

    def get_artifact(
        self,
        artifact_id: str,
        *,
        owner_id: Optional[str] = None,
        verify_integrity: bool = False,
    ) -> ArtifactMeta:
        """Obtiene metadatos de un artefacto, verificando propietario e integridad SHA-256."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Artefacto '{artifact_id}' no encontrado.")
        meta = self._row_to_meta(row)
        if owner_id is not None and meta.owner_id not in (owner_id, "system", "agent"):
            raise PermissionError("No tiene permisos sobre este artefacto.")
        if verify_integrity:
            if not os.path.exists(meta.storage_path):
                raise FileNotFoundError("Blob de artefacto ausente en disco.")
            actual = self.compute_file_sha256(meta.storage_path)
            if actual != meta.sha256:
                raise RuntimeError("Corrupción detectada: SHA-256 no coincide.")
        return meta

    def read_bytes(self, artifact_id: str, *, owner_id: Optional[str] = None) -> bytes:
        meta = self.get_artifact(artifact_id, owner_id=owner_id, verify_integrity=True)
        with open(meta.storage_path, "rb") as f:
            return f.read()

    def update_status(
        self,
        artifact_id: str,
        status: str,
        metadata_patch: Optional[Dict[str, Any]] = None,
    ) -> None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT metadata_json FROM artifacts WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()
            if row is None:
                return
            current = json.loads(row["metadata_json"] or "{}")
            if metadata_patch:
                current.update(metadata_patch)
            conn.execute(
                "UPDATE artifacts SET index_status = ?, metadata_json = ? WHERE artifact_id = ?",
                (status, json.dumps(current, ensure_ascii=False), artifact_id),
            )

    def list_by_project(self, project_id: str, *, owner_id: Optional[str] = None) -> List[ArtifactMeta]:
        with self._connect() as conn:
            if owner_id is not None:
                rows = conn.execute(
                    "SELECT * FROM artifacts WHERE project_id = ? AND owner_id IN (?, 'system') ORDER BY created_at DESC",
                    (project_id, owner_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM artifacts WHERE project_id = ? ORDER BY created_at DESC",
                    (project_id,),
                ).fetchall()
        return [self._row_to_meta(r) for r in rows]

    @staticmethod
    def _row_to_meta(row: sqlite3.Row) -> ArtifactMeta:
        return ArtifactMeta(
            artifact_id=row["artifact_id"],
            sha256=row["sha256"],
            project_id=row["project_id"],
            owner_id=row["owner_id"],
            filename=row["filename"],
            file_type=row["file_type"],
            size_bytes=row["size_bytes"],
            storage_path=row["storage_path"],
            index_status=row["index_status"],
            metadata=json.loads(row["metadata_json"] or "{}"),
            created_at=row["created_at"],
        )
