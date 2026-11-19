"""Ejecutor DAG Real con estado persistente, ejecución paralela por niveles y human-in-the-loop."""
import asyncio
import inspect
import json
import os
import sqlite3
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional


def _default_dag_db() -> str:
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cache_root = os.getenv("WORKSPACE_CACHE_DIR") or os.path.join(root, "workspace_cache")
    os.makedirs(cache_root, exist_ok=True)
    return os.path.join(cache_root, "dag_runs.db")


@dataclass
class DAGNode:
    node_id: str
    handler: Callable[..., Any]
    deps: List[str] = field(default_factory=list)
    params: Dict[str, Any] = field(default_factory=dict)
    confidence_threshold: float = 0.75


class DAGRunner:
    """Orquestador DAG multi-especialidad con persistencia SQLite y pausa por baja confianza."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or _default_dag_db()
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS dag_runs (
                    run_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS dag_nodes (
                    run_id TEXT NOT NULL,
                    node_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    confidence REAL,
                    result_json TEXT,
                    error TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, node_id)
                )
                """
            )

    @staticmethod
    def topological_waves(nodes: List[DAGNode]) -> List[List[DAGNode]]:
        """Agrupa los nodos en oleadas paralelas y detecta ciclos."""
        by_id = {n.node_id: n for n in nodes}
        if len(by_id) != len(nodes):
            raise ValueError("Existen node_id duplicados en el DAG.")

        indeg: Dict[str, int] = {n.node_id: 0 for n in nodes}
        children: Dict[str, List[str]] = defaultdict(list)
        for n in nodes:
            for d in n.deps:
                if d not in by_id:
                    raise ValueError(f"Dependencia '{d}' no existe para nodo '{n.node_id}'.")
                indeg[n.node_id] += 1
                children[d].append(n.node_id)

        q = deque([nid for nid, deg in indeg.items() if deg == 0])
        waves: List[List[DAGNode]] = []
        visited = 0
        while q:
            wave_ids = sorted(q)
            q.clear()
            waves.append([by_id[nid] for nid in wave_ids])
            visited += len(wave_ids)
            for nid in wave_ids:
                for ch in children[nid]:
                    indeg[ch] -= 1
                    if indeg[ch] == 0:
                        q.append(ch)

        if visited != len(nodes):
            raise ValueError("El grafo DAG contiene un ciclo.")
        return waves

    def _save_run(self, run_id: str, project_id: str, status: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO dag_runs (run_id, project_id, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET status=excluded.status, updated_at=excluded.updated_at
                """,
                (run_id, project_id, status, now, now),
            )

    def _save_node(
        self,
        run_id: str,
        node_id: str,
        status: str,
        *,
        confidence: Optional[float] = None,
        result: Any = None,
        error: Optional[str] = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        res_json = json.dumps(result, ensure_ascii=False, default=str) if result is not None else None
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO dag_nodes (run_id, node_id, status, confidence, result_json, error, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, node_id) DO UPDATE SET
                    status=excluded.status,
                    confidence=excluded.confidence,
                    result_json=excluded.result_json,
                    error=excluded.error,
                    updated_at=excluded.updated_at
                """,
                (run_id, node_id, status, confidence, res_json, error, now),
            )

    def approve_node(self, run_id: str, node_id: str) -> None:
        """Aprueba manualmente un nodo en estado 'awaiting_review'."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "UPDATE dag_nodes SET status='completed', updated_at=? WHERE run_id=? AND node_id=? AND status='awaiting_review'",
                (now, run_id, node_id),
            )

    def get_run(self, run_id: str) -> Dict[str, Any]:
        with self._connect() as conn:
            run = conn.execute("SELECT * FROM dag_runs WHERE run_id = ?", (run_id,)).fetchone()
            if run is None:
                raise KeyError(f"Ejecución DAG '{run_id}' no encontrada.")
            nodes = conn.execute(
                "SELECT node_id, status, confidence, result_json, error, updated_at FROM dag_nodes WHERE run_id = ?",
                (run_id,),
            ).fetchall()
        parsed_nodes = {}
        for r in nodes:
            parsed_nodes[r["node_id"]] = {
                "status": r["status"],
                "confidence": r["confidence"],
                "result": json.loads(r["result_json"]) if r["result_json"] else None,
                "error": r["error"],
                "updated_at": r["updated_at"],
            }
        return {
            "run_id": run["run_id"],
            "project_id": run["project_id"],
            "status": run["status"],
            "nodes": parsed_nodes,
        }

    async def execute(
        self,
        nodes: List[DAGNode],
        *,
        project_id: str = "default",
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Ejecuta el DAG por oleadas paralelas respetando dependencias y revisión humana."""
        waves = self.topological_waves(nodes)
        rid = run_id or f"dag_{uuid.uuid4().hex[:12]}"
        self._save_run(rid, project_id, "running")

        existing = {}
        try:
            existing = self.get_run(rid)["nodes"]
        except KeyError:
            pass

        results: Dict[str, Any] = {
            nid: info["result"]
            for nid, info in existing.items()
            if info["status"] == "completed"
        }

        async def _run_one(node: DAGNode) -> str:
            if existing.get(node.node_id, {}).get("status") == "completed":
                return "completed"
            self._save_node(rid, node.node_id, "running")
            dep_results = {d: results.get(d) for d in node.deps}
            try:
                if inspect.iscoroutinefunction(node.handler):
                    out = await node.handler(node.params, dep_results)
                else:
                    out = await asyncio.to_thread(node.handler, node.params, dep_results)
                conf = None
                if isinstance(out, dict) and "confianza" in out and out["confianza"] is not None:
                    conf = float(out["confianza"])
                if conf is not None and conf < node.confidence_threshold:
                    self._save_node(rid, node.node_id, "awaiting_review", confidence=conf, result=out)
                    return "awaiting_review"
                results[node.node_id] = out
                self._save_node(rid, node.node_id, "completed", confidence=conf, result=out)
                return "completed"
            except Exception as exc:
                self._save_node(rid, node.node_id, "failed", error=str(exc))
                return "failed"

        for wave in waves:
            statuses = await asyncio.gather(*(_run_one(n) for n in wave))
            if "failed" in statuses:
                self._save_run(rid, project_id, "failed")
                return self.get_run(rid)
            if "awaiting_review" in statuses:
                self._save_run(rid, project_id, "awaiting_review")
                return self.get_run(rid)

        self._save_run(rid, project_id, "completed")
        return self.get_run(rid)


def ensure_skills_loaded() -> None:
    """Importa y registra las skills adaptadas del sistema agéntico."""
    import src.engines.cad_vision.skills.hidraulica  # noqa: F401
    import src.engines.docs_rag.skills.coherencia  # noqa: F401
    import src.engines.planning_4d.skills.obra_civil  # noqa: F401


def skill_to_dag_node(
    node_id: str,
    skill_name: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    deps: Optional[List[str]] = None,
    confidence_threshold: float = 0.75,
) -> DAGNode:
    """Envuelve una Skill registrada como un nodo ejecutable del DAGRunner."""

    def _handler(node_params: Dict[str, Any], _dep_results: Dict[str, Any]) -> Dict[str, Any]:
        from src.core.skills_base import run_skill

        ensure_skills_loaded()
        res = run_skill(skill_name, node_params)
        return res.model_dump()

    return DAGNode(
        node_id=node_id,
        handler=_handler,
        deps=list(deps or []),
        params=dict(params or {}),
        confidence_threshold=confidence_threshold,
    )
