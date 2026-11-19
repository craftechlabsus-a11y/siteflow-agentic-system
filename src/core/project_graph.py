"""Grafo extensible de conocimiento del proyecto (ProjectGraph) con trazabilidad (v2.6.0)."""
import json
import os
import sqlite3
from typing import Any, Dict, List, Optional


class ProjectGraph:
    """Grafo persistente de entidades (BIM, CAD, Documentación, WBS, Actividades) y relaciones."""

    def __init__(self, project_id: str, db_path: Optional[str] = None) -> None:
        self.project_id = project_id
        if db_path is None:
            root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            cache_root = os.getenv("WORKSPACE_CACHE_DIR") or os.path.join(root, "workspace_cache")
            base = os.getenv("ARTIFACT_STORE_DIR") or os.path.join(cache_root, "artifacts")
            os.makedirs(base, exist_ok=True)
            db_path = os.path.join(base, "project_graph.db")
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
                CREATE TABLE IF NOT EXISTS graph_nodes (
                    project_id TEXT NOT NULL,
                    node_id TEXT NOT NULL,
                    node_type TEXT NOT NULL,
                    label TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL DEFAULT '',
                    attrs_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY (project_id, node_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS graph_edges (
                    project_id TEXT NOT NULL,
                    src_id TEXT NOT NULL,
                    dst_id TEXT NOT NULL,
                    edge_type TEXT NOT NULL,
                    attrs_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY (project_id, src_id, dst_id, edge_type)
                )
                """
            )

    def upsert_node(
        self,
        node_id: str,
        node_type: str,
        label: str,
        *,
        source_sha256: str = "",
        attrs: Optional[Dict[str, Any]] = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO graph_nodes (project_id, node_id, node_type, label, source_sha256, attrs_json)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, node_id) DO UPDATE SET
                    node_type = excluded.node_type,
                    label = excluded.label,
                    source_sha256 = excluded.source_sha256,
                    attrs_json = excluded.attrs_json
                """,
                (
                    self.project_id,
                    node_id,
                    node_type,
                    label,
                    source_sha256,
                    json.dumps(attrs or {}, ensure_ascii=False, default=str),
                ),
            )

    def add_edge(
        self,
        src_id: str,
        dst_id: str,
        edge_type: str,
        *,
        attrs: Optional[Dict[str, Any]] = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO graph_edges (project_id, src_id, dst_id, edge_type, attrs_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(project_id, src_id, dst_id, edge_type) DO UPDATE SET
                    attrs_json = excluded.attrs_json
                """,
                (
                    self.project_id,
                    src_id,
                    dst_id,
                    edge_type,
                    json.dumps(attrs or {}, ensure_ascii=False, default=str),
                ),
            )

    def find_unbacked_elements(self, node_type: str = "BimElement") -> List[Dict[str, Any]]:
        """Detecta elementos BIM/CAD que carecen de arista SPECIFIED_BY hacia un requisito documental."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT n.node_id, n.node_type, n.label, n.source_sha256, n.attrs_json
                FROM graph_nodes n
                WHERE n.project_id = ? AND n.node_type = ?
                  AND NOT EXISTS (
                      SELECT 1 FROM graph_edges e
                      WHERE e.project_id = n.project_id
                        AND e.src_id = n.node_id
                        AND e.edge_type = 'SPECIFIED_BY'
                  )
                """,
                (self.project_id, node_type),
            ).fetchall()
        return [
            {
                "node_id": r["node_id"],
                "node_type": r["node_type"],
                "label": r["label"],
                "source_sha256": r["source_sha256"],
                "attrs": json.loads(r["attrs_json"] or "{}"),
            }
            for r in rows
        ]

    def summary(self) -> Dict[str, Any]:
        with self._connect() as conn:
            nodes = conn.execute(
                "SELECT node_type, COUNT(*) AS cnt FROM graph_nodes WHERE project_id = ? GROUP BY node_type",
                (self.project_id,),
            ).fetchall()
            edges = conn.execute(
                "SELECT edge_type, COUNT(*) AS cnt FROM graph_edges WHERE project_id = ? GROUP BY edge_type",
                (self.project_id,),
            ).fetchall()
        return {
            "project_id": self.project_id,
            "nodes_by_type": {r["node_type"]: r["cnt"] for r in nodes},
            "edges_by_type": {r["edge_type"]: r["cnt"] for r in edges},
        }
