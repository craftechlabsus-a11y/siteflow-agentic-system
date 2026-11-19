"""Pipeline documental jerárquico e índice híbrido FTS5 + similitud local sin truncado."""
import hashlib
import io
import math
import os
import re
import sqlite3
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

_HEADING_RE = re.compile(
    r"^(?:(?:\d+(?:\.\d+)*\.?\s+)[A-ZÁÉÍÓÚÑ].{2,90}|#{1,4}\s+.+|CAP[IÍ]TULO\s+\d+.*)$"
)
_FTS_SAFE_TOKEN_RE = re.compile(r"[^\w\sáéíóúÁÉÍÓÚñÑüÜ\-]", re.UNICODE)


@dataclass
class DocChunk:
    chunk_id: str
    artifact_id: str
    sha256: str
    page_number: int
    section_title: str
    text: str


def extract_pdf_pages(pdf_bytes: bytes) -> List[Dict[str, Any]]:
    """Extrae texto página a página de un PDF usando PyMuPDF (si existe) o PyPDF2."""
    pages: List[Dict[str, Any]] = []
    try:
        import pymupdf as fitz  # PyMuPDF

        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            for i, page in enumerate(doc, start=1):
                txt = page.get_text("text") or ""
                pages.append({"page": i, "text": txt, "needs_ocr": len(txt.strip()) < 25})
            return pages
    except ImportError:
        pass

    from PyPDF2 import PdfReader

    reader = PdfReader(io.BytesIO(pdf_bytes))
    for i, page in enumerate(reader.pages, start=1):
        txt = page.extract_text() or ""
        pages.append({"page": i, "text": txt, "needs_ocr": len(txt.strip()) < 25})
    return pages


def chunk_document_pages(
    pages: List[Dict[str, Any]],
    *,
    artifact_id: str,
    sha256: str,
    max_chunk_chars: int = 2500,
    overlap_chars: int = 200,
) -> List[DocChunk]:
    """Trocea páginas preservando capítulos/secciones y número de página sin truncar el documento."""
    chunks: List[DocChunk] = []
    current_section = "Introducción"
    idx = 0

    for p in pages:
        page_num = int(p.get("page", 1))
        raw = (p.get("text") or "").strip()
        if not raw:
            continue

        lines = raw.splitlines()
        buf: List[str] = []
        buf_len = 0

        for line in lines:
            stripped = line.strip()
            if _HEADING_RE.match(stripped) and buf_len > 200:
                idx += 1
                chunks.append(
                    DocChunk(
                        chunk_id=f"{artifact_id}#c{idx}",
                        artifact_id=artifact_id,
                        sha256=sha256,
                        page_number=page_num,
                        section_title=current_section,
                        text="\n".join(buf).strip(),
                    )
                )
                buf = []
                buf_len = 0
                current_section = stripped[:120]
            elif _HEADING_RE.match(stripped) and buf_len == 0:
                current_section = stripped[:120]

            buf.append(line)
            buf_len += len(line) + 1

            if buf_len >= max_chunk_chars:
                idx += 1
                joined = "\n".join(buf).strip()
                chunks.append(
                    DocChunk(
                        chunk_id=f"{artifact_id}#c{idx}",
                        artifact_id=artifact_id,
                        sha256=sha256,
                        page_number=page_num,
                        section_title=current_section,
                        text=joined,
                    )
                )
                tail = joined[-overlap_chars:] if overlap_chars > 0 else ""
                buf = [tail] if tail else []
                buf_len = len(tail)

        if buf:
            idx += 1
            chunks.append(
                DocChunk(
                    chunk_id=f"{artifact_id}#c{idx}",
                    artifact_id=artifact_id,
                    sha256=sha256,
                    page_number=page_num,
                    section_title=current_section,
                    text="\n".join(buf).strip(),
                )
            )
    return chunks


def extract_docx_pages(file_path: str) -> List[Dict[str, Any]]:
    """Extrae párrafos y tablas de un .docx en bloques lógicos de página/sección."""
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    lines: List[str] = []
    with zipfile.ZipFile(file_path, "r") as zf:
        if "word/document.xml" not in zf.namelist():
            return []
        root = ET.fromstring(zf.read("word/document.xml"))
        body = root.find("w:body", ns)
        if body is None:
            return []
        for child in body:
            tag = child.tag.split("}")[-1]
            if tag == "p":
                txt = "".join(t.text or "" for t in child.findall(".//w:t", ns)).strip()
                if txt:
                    lines.append(txt)
            elif tag == "tbl":
                for row in child.findall(".//w:tr", ns):
                    cells = [
                        "".join(t.text or "" for t in cell.findall(".//w:t", ns)).strip()
                        for cell in row.findall(".//w:tc", ns)
                    ]
                    if any(cells):
                        lines.append(" | ".join(cells))
    if not lines:
        return []
    # Agrupar en bloques de ~60 líneas como páginas lógicas
    pages: List[Dict[str, Any]] = []
    step = 60
    for idx in range(0, len(lines), step):
        pages.append(
            {
                "page": (idx // step) + 1,
                "text": "\n".join(lines[idx : idx + step]),
                "needs_ocr": False,
            }
        )
    return pages


def _token_vector(text: str) -> Counter:
    tokens = re.findall(r"[a-z0-9áéíóúñü\-]{2,}", (text or "").lower())
    return Counter(tokens)


def _cosine_sim(v1: Counter, v2: Counter) -> float:
    if not v1 or not v2:
        return 0.0
    common = set(v1.keys()) & set(v2.keys())
    num = sum(v1[k] * v2[k] for k in common)
    if num == 0:
        return 0.0
    den = math.sqrt(sum(c * c for c in v1.values())) * math.sqrt(sum(c * c for c in v2.values()))
    return num / den if den > 0 else 0.0


def _sanitize_fts_query(query: str) -> str:
    """Convierte cualquier entrada de usuario en términos seguros para MATCH de FTS5."""
    cleaned = _FTS_SAFE_TOKEN_RE.sub(" ", query or "")
    tokens = [t for t in cleaned.split() if len(t) >= 2]
    if not tokens:
        return ""
    return " OR ".join(f'"{t}"' for t in tokens[:16])


class DocIndex:
    """Índice documental SQLite FTS5 (BM25) con consultas 100% parametrizadas."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        if db_path is None:
            root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
            cache_root = os.getenv("WORKSPACE_CACHE_DIR") or os.path.join(root, "workspace_cache")
            idx_dir = os.getenv("ARTIFACT_STORE_DIR") or os.path.join(cache_root, "artifacts")
            os.makedirs(idx_dir, exist_ok=True)
            db_path = os.path.join(idx_dir, "doc_index.db")
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS doc_chunks (
                    chunk_id TEXT PRIMARY KEY,
                    artifact_id TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    page_number INTEGER NOT NULL,
                    section_title TEXT NOT NULL,
                    text TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_doc_chunks_artifact ON doc_chunks(artifact_id, page_number)"
            )
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS doc_chunks_fts USING fts5(
                    chunk_id UNINDEXED,
                    artifact_id UNINDEXED,
                    section_title,
                    text
                )
                """
            )

    def index_chunks(self, artifact_id: str, chunks: List[DocChunk]) -> int:
        """Indexa (o reindexa) todos los chunks de un artefacto."""
        with self._connect() as conn:
            conn.execute("DELETE FROM doc_chunks WHERE artifact_id = ?", (artifact_id,))
            conn.execute("DELETE FROM doc_chunks_fts WHERE artifact_id = ?", (artifact_id,))
            for c in chunks:
                conn.execute(
                    """
                    INSERT INTO doc_chunks (chunk_id, artifact_id, sha256, page_number, section_title, text)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (c.chunk_id, c.artifact_id, c.sha256, c.page_number, c.section_title, c.text),
                )
                conn.execute(
                    """
                    INSERT INTO doc_chunks_fts (chunk_id, artifact_id, section_title, text)
                    VALUES (?, ?, ?, ?)
                    """,
                    (c.chunk_id, c.artifact_id, c.section_title, c.text),
                )
        return len(chunks)

    def search(
        self,
        query: str,
        *,
        artifact_id: Optional[str] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Búsqueda BM25 segura sobre el índice FTS5 sin límite de tamaño de documento."""
        fts_query = _sanitize_fts_query(query)
        if not fts_query:
            return []

        sql = (
            "SELECT d.chunk_id, d.artifact_id, d.sha256, d.page_number, d.section_title, "
            "d.text, bm25(doc_chunks_fts) AS rank "
            "FROM doc_chunks_fts f "
            "JOIN doc_chunks d ON d.chunk_id = f.chunk_id "
            "WHERE doc_chunks_fts MATCH ? "
        )
        params: List[Any] = [fts_query]
        if artifact_id is not None:
            sql += "AND d.artifact_id = ? "
            params.append(artifact_id)
        sql += "ORDER BY rank LIMIT ?"
        params.append(max(1, min(int(limit), 100)))

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def index_file_path(
        self,
        file_path: str,
        artifact_id: str,
        *,
        sha256: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Extrae e indexa un PDF o DOCX local en chunks jerárquicos + FTS5."""
        real_path = os.path.realpath(file_path)
        if not sha256:
            h = hashlib.sha256()
            with open(real_path, "rb") as f:
                while block := f.read(65536):
                    h.update(block)
            sha256 = h.hexdigest()

        ext = os.path.splitext(real_path)[1].lower()
        if ext == ".pdf":
            with open(real_path, "rb") as f:
                pages = extract_pdf_pages(f.read())
        elif ext == ".docx":
            pages = extract_docx_pages(real_path)
        elif ext in (".txt", ".md"):
            with open(real_path, "r", encoding="utf-8", errors="replace") as f:
                pages = [{"page": 1, "text": f.read(), "needs_ocr": False}]
        else:
            raise ValueError(f"Formato documental no soportado para indexación: {ext}")

        chunks = chunk_document_pages(pages, artifact_id=artifact_id, sha256=sha256)
        count = self.index_chunks(artifact_id, chunks)
        return {
            "artifact_id": artifact_id,
            "sha256": sha256,
            "pages": len(pages),
            "chunks_indexed": count,
        }

    def hybrid_search(
        self,
        query: str,
        *,
        artifact_id: Optional[str] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Combina FTS5 BM25 con similitud coseno local sobre tokens para robustez léxica."""
        fts_hits = self.search(query, artifact_id=artifact_id, limit=max(limit * 2, 20))
        q_vec = _token_vector(query)

        if fts_hits:
            for h in fts_hits:
                h["cosine_score"] = round(_cosine_sim(q_vec, _token_vector(h["text"])), 4)
            fts_hits.sort(key=lambda x: (x["cosine_score"], -float(x.get("rank", 0.0))), reverse=True)
            return fts_hits[:limit]

        # Fallback por similitud local si FTS5 exacto no devuelve resultados
        sql = "SELECT chunk_id, artifact_id, sha256, page_number, section_title, text FROM doc_chunks"
        params: List[Any] = []
        if artifact_id is not None:
            sql += " WHERE artifact_id = ?"
            params.append(artifact_id)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        scored: List[Dict[str, Any]] = []
        for r in rows:
            sim = _cosine_sim(q_vec, _token_vector(r["text"]))
            if sim > 0.0:
                d = dict(r)
                d["rank"] = 0.0
                d["cosine_score"] = round(sim, 4)
                scored.append(d)
        scored.sort(key=lambda x: x["cosine_score"], reverse=True)
        return scored[:limit]

    def get_toc(self, artifact_id: str) -> List[Dict[str, Any]]:
        """Devuelve el índice de secciones detectadas y su página inicial."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT section_title, MIN(page_number) AS start_page, COUNT(*) AS chunks
                FROM doc_chunks
                WHERE artifact_id = ?
                GROUP BY section_title
                ORDER BY start_page ASC
                """,
                (artifact_id,),
            ).fetchall()
        return [dict(r) for r in rows]
