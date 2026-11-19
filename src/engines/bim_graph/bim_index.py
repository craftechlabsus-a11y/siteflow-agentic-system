"""Índice BIM relacional paginado y extracción de PropertySets / IfcSystem (v2.6.0)."""
import json
import os
import sqlite3
from typing import Any, Dict, List, Optional

_ALLOWED_GROUP_COLS = {
    "ifc_class",
    "disciplina",
    "storey",
    "system_name",
    "material",
}


def extract_element_semantics(ifc_element) -> Dict[str, Any]:
    """Extrae PropertySets (Pset_*) y sistemas (IfcSystem) de un elemento IFC."""
    psets: Dict[str, Any] = {}
    system_name = ""
    try:
        import ifcopenshell.util.element as ifc_el

        raw_psets = ifc_el.get_psets(ifc_element, psets_only=True) or {}
        for k, v in raw_psets.items():
            if isinstance(v, dict):
                psets[k] = {pk: pv for pk, pv in v.items() if pk != "id"}
    except Exception:
        pass

    try:
        for rel in getattr(ifc_element, "HasAssignments", ()) or ():
            if rel.is_a("IfcRelAssignsToGroup"):
                grp = getattr(rel, "RelatingGroup", None)
                if grp and grp.is_a("IfcSystem"):
                    system_name = getattr(grp, "Name", "") or ""
                    break
    except Exception:
        pass

    return {"psets": psets, "system_name": system_name}


class BimIndex:
    """Índice SQLite para consultar miles de elementos BIM sin saturar RAM ni contexto LLM."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        if db_path is None:
            root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
            cache_root = os.getenv("WORKSPACE_CACHE_DIR") or os.path.join(root, "workspace_cache")
            base = os.getenv("ARTIFACT_STORE_DIR") or os.path.join(cache_root, "artifacts")
            os.makedirs(base, exist_ok=True)
            db_path = os.path.join(base, "bim_index.db")
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
                CREATE TABLE IF NOT EXISTS bim_elements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    artifact_id TEXT NOT NULL,
                    global_id TEXT NOT NULL,
                    ifc_class TEXT NOT NULL,
                    disciplina TEXT NOT NULL,
                    storey TEXT NOT NULL,
                    system_name TEXT NOT NULL DEFAULT '',
                    material TEXT NOT NULL DEFAULT '',
                    name TEXT NOT NULL DEFAULT '',
                    vol REAL NOT NULL DEFAULT 0.0,
                    area REAL NOT NULL DEFAULT 0.0,
                    bbox_min_json TEXT NOT NULL DEFAULT '[0,0,0]',
                    bbox_max_json TEXT NOT NULL DEFAULT '[0,0,0]',
                    psets_json TEXT NOT NULL DEFAULT '{}',
                    flags_json TEXT NOT NULL DEFAULT '[]'
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_bim_art_class ON bim_elements(artifact_id, ifc_class, storey)"
            )

    def index_elements(self, artifact_id: str, elements: List[Dict[str, Any]]) -> int:
        with self._connect() as conn:
            conn.execute("DELETE FROM bim_elements WHERE artifact_id = ?", (artifact_id,))
            rows = []
            for e in elements:
                rows.append(
                    (
                        artifact_id,
                        str(e.get("global_id") or ""),
                        str(e.get("ifc_class") or "Unknown"),
                        str(e.get("disciplina") or "GENERAL"),
                        str(e.get("storey") or "Sin Planta"),
                        str(e.get("system_name") or ""),
                        str(e.get("material") or ""),
                        str(e.get("name") or ""),
                        float(e.get("vol") or 0.0),
                        float(e.get("area") or 0.0),
                        json.dumps(e.get("bbox_min") or [0.0, 0.0, 0.0]),
                        json.dumps(e.get("bbox_max") or [0.0, 0.0, 0.0]),
                        json.dumps(e.get("psets") or {}, ensure_ascii=False, default=str),
                        json.dumps(e.get("flags") or [], ensure_ascii=False),
                    )
                )
            conn.executemany(
                """
                INSERT INTO bim_elements (
                    artifact_id, global_id, ifc_class, disciplina, storey, system_name,
                    material, name, vol, area, bbox_min_json, bbox_max_json, psets_json, flags_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def query_elements(
        self,
        artifact_id: str,
        *,
        ifc_class: Optional[str] = None,
        disciplina: Optional[str] = None,
        storey: Optional[str] = None,
        system_name: Optional[str] = None,
        group_by: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Consulta filtrada, paginada o agregada sobre elementos BIM indexados."""
        where = ["artifact_id = ?"]
        params: List[Any] = [artifact_id]
        for col, val in (
            ("ifc_class", ifc_class),
            ("disciplina", disciplina),
            ("storey", storey),
            ("system_name", system_name),
        ):
            if val is not None:
                where.append(f"{col} = ?")
                params.append(val)
        where_sql = " AND ".join(where)

        if group_by:
            cols = [c.strip() for c in group_by.split(",") if c.strip()]
            invalid = [c for c in cols if c not in _ALLOWED_GROUP_COLS]
            if invalid:
                raise ValueError(f"Columnas de agrupación no permitidas: {invalid}")
            if not cols:
                raise ValueError("group_by no puede estar vacío.")
            sel = ", ".join(cols)
            sql = (
                f"SELECT {sel}, COUNT(*) AS count, ROUND(SUM(vol), 4) AS total_vol, "
                f"ROUND(SUM(area), 4) AS total_area "
                f"FROM bim_elements WHERE {where_sql} GROUP BY {sel} "
                f"ORDER BY count DESC LIMIT ? OFFSET ?"
            )
            params.extend([max(1, min(int(limit), 500)), max(0, int(offset))])
            with self._connect() as conn:
                rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
            return {"mode": "grouped", "group_by": cols, "rows": rows}

        sql = (
            f"SELECT global_id, ifc_class, disciplina, storey, system_name, material, name, "
            f"vol, area, bbox_min_json, bbox_max_json, psets_json, flags_json "
            f"FROM bim_elements WHERE {where_sql} ORDER BY id ASC LIMIT ? OFFSET ?"
        )
        params.extend([max(1, min(int(limit), 500)), max(0, int(offset))])
        with self._connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM bim_elements WHERE {where_sql}", params[:-2]
            ).fetchone()[0]
            raw_rows = conn.execute(sql, params).fetchall()

        items = []
        for r in raw_rows:
            items.append(
                {
                    "global_id": r["global_id"],
                    "ifc_class": r["ifc_class"],
                    "disciplina": r["disciplina"],
                    "storey": r["storey"],
                    "system_name": r["system_name"],
                    "material": r["material"],
                    "name": r["name"],
                    "vol": r["vol"],
                    "area": r["area"],
                    "bbox_min": json.loads(r["bbox_min_json"]),
                    "bbox_max": json.loads(r["bbox_max_json"]),
                    "psets": json.loads(r["psets_json"]),
                    "flags": json.loads(r["flags_json"]),
                }
            )
        return {"mode": "items", "total": total, "limit": limit, "offset": offset, "items": items}
