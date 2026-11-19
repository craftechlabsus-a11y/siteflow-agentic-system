"""Motor BIM-Graph: indexación en DuckDB + grafo topológico en NetworkX (IFC2x3 / IFC4 / IFC4.3)."""
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import duckdb
import networkx as nx

from src.engines.bim_graph.ingesta import (
    DISCIPLINA_BY_IFC_CLASS,
    PRODUCT_CLASSES,
    calcular_sha256_archivo,
)

_FORBIDDEN_SQL_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|ATTACH|DETACH|COPY|PRAGMA|EXEC|CALL)\b",
    re.IGNORECASE,
)


def _default_duckdb_path() -> str:
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    cache_root = os.getenv("WORKSPACE_CACHE_DIR") or os.path.join(root, "workspace_cache")
    os.makedirs(cache_root, exist_ok=True)
    return os.path.join(cache_root, "bim_graph.duckdb")


class BimGraphStore:
    """Almacén analítico DuckDB + grafo topológico NetworkX para modelos BIM masivos."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or _default_duckdb_path()
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        self._init_schema()

    def _connect(self) -> duckdb.DuckDBPyConnection:
        return duckdb.connect(self.db_path)

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS elements (
                    artifact_id VARCHAR,
                    global_id VARCHAR,
                    ifc_class VARCHAR,
                    name VARCHAR,
                    disciplina VARCHAR,
                    storey VARCHAR,
                    system_name VARCHAR,
                    material VARCHAR,
                    volume_m3 DOUBLE,
                    area_m2 DOUBLE,
                    length_m DOUBLE,
                    sha256 VARCHAR,
                    PRIMARY KEY (artifact_id, global_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS properties (
                    artifact_id VARCHAR,
                    global_id VARCHAR,
                    pset_name VARCHAR,
                    prop_name VARCHAR,
                    prop_value VARCHAR
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS quantities (
                    artifact_id VARCHAR,
                    global_id VARCHAR,
                    qto_name VARCHAR,
                    qty_name VARCHAR,
                    qty_value DOUBLE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS relations (
                    artifact_id VARCHAR,
                    rel_type VARCHAR,
                    source_id VARCHAR,
                    target_id VARCHAR,
                    metadata_json VARCHAR
                )
                """
            )

    def index_ifc_file(self, ifc_path: str, artifact_id: str) -> Dict[str, Any]:
        """Indexa elementos, Pset_*, Qto_* y relaciones topológicas de un .ifc en DuckDB."""
        import ifcopenshell
        import ifcopenshell.util.element as ifc_el

        real_path = os.path.realpath(ifc_path)
        sha256 = calcular_sha256_archivo(real_path)
        model = ifcopenshell.open(real_path)

        elem_rows: List[Tuple[Any, ...]] = []
        prop_rows: List[Tuple[Any, ...]] = []
        qty_rows: List[Tuple[Any, ...]] = []
        rel_rows: List[Tuple[Any, ...]] = []

        seen_gids = set()
        for cls in PRODUCT_CLASSES:
            try:
                entities = model.by_type(cls)
            except RuntimeError:
                continue
            for el in entities:
                gid = getattr(el, "GlobalId", None)
                if not gid or gid in seen_gids:
                    continue
                seen_gids.add(gid)
                ifc_class = el.is_a()
                name = getattr(el, "Name", "") or ""
                disciplina = DISCIPLINA_BY_IFC_CLASS.get(ifc_class, "GENERAL")

                storey = ""
                try:
                    container = ifc_el.get_container(el)
                    if container is not None:
                        storey = getattr(container, "Name", "") or container.is_a()
                except Exception:
                    pass

                system_name = ""
                try:
                    for rel in getattr(el, "HasAssignments", ()) or ():
                        if rel.is_a("IfcRelAssignsToGroup"):
                            grp = getattr(rel, "RelatingGroup", None)
                            if grp and grp.is_a("IfcSystem"):
                                system_name = getattr(grp, "Name", "") or ""
                                break
                except Exception:
                    pass

                material = ""
                try:
                    mat = ifc_el.get_material(el)
                    if mat is not None:
                        material = getattr(mat, "Name", "") or mat.is_a()
                except Exception:
                    pass

                vol_m3 = 0.0
                area_m2 = 0.0
                len_m = 0.0
                try:
                    psets = ifc_el.get_psets(el, psets_only=True) or {}
                    for pset_name, props in psets.items():
                        if not isinstance(props, dict):
                            continue
                        for pk, pv in props.items():
                            if pk == "id" or pv is None:
                                continue
                            prop_rows.append((artifact_id, gid, str(pset_name), str(pk), str(pv)))

                    qtos = ifc_el.get_psets(el, qtos_only=True) or {}
                    for qto_name, qdict in qtos.items():
                        if not isinstance(qdict, dict):
                            continue
                        for qk, qv in qdict.items():
                            if qk == "id" or not isinstance(qv, (int, float)):
                                continue
                            val = float(qv)
                            qty_rows.append((artifact_id, gid, str(qto_name), str(qk), val))
                            qkl = qk.lower()
                            if "volume" in qkl and vol_m3 == 0.0:
                                vol_m3 = val
                            elif "area" in qkl and area_m2 == 0.0:
                                area_m2 = val
                            elif "length" in qkl and len_m == 0.0:
                                len_m = val
                except Exception:
                    pass

                elem_rows.append(
                    (
                        artifact_id,
                        gid,
                        ifc_class,
                        str(name),
                        disciplina,
                        str(storey),
                        str(system_name),
                        str(material),
                        vol_m3,
                        area_m2,
                        len_m,
                        sha256,
                    )
                )

        # Relaciones topológicas y espaciales
        rel_specs = (
            ("IfcRelContainedInSpatialStructure", "RelatingStructure", "RelatedElements"),
            ("IfcRelVoidsElement", "RelatingBuildingElement", "RelatedOpeningElement"),
            ("IfcRelConnectsElements", "RelatingElement", "RelatedElement"),
            ("IfcRelConnectsPorts", "RelatingPort", "RelatedPort"),
        )
        for rel_type, src_attr, dst_attr in rel_specs:
            try:
                rels = model.by_type(rel_type)
            except RuntimeError:
                continue
            for r in rels:
                src_obj = getattr(r, src_attr, None)
                dst_obj = getattr(r, dst_attr, None)
                src_gid = getattr(src_obj, "GlobalId", None)
                if not src_gid or dst_obj is None:
                    continue
                targets = dst_obj if isinstance(dst_obj, (list, tuple)) else [dst_obj]
                for t in targets:
                    t_gid = getattr(t, "GlobalId", None)
                    if t_gid:
                        rel_rows.append((artifact_id, rel_type, src_gid, t_gid, "{}"))

        with self._connect() as conn:
            for tbl in ("elements", "properties", "quantities", "relations"):
                conn.execute(f"DELETE FROM {tbl} WHERE artifact_id = ?", [artifact_id])
            if elem_rows:
                conn.executemany(
                    "INSERT INTO elements VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    elem_rows,
                )
            if prop_rows:
                conn.executemany("INSERT INTO properties VALUES (?, ?, ?, ?, ?)", prop_rows)
            if qty_rows:
                conn.executemany("INSERT INTO quantities VALUES (?, ?, ?, ?, ?)", qty_rows)
            if rel_rows:
                conn.executemany("INSERT INTO relations VALUES (?, ?, ?, ?, ?)", rel_rows)

        return {
            "artifact_id": artifact_id,
            "sha256": sha256,
            "schema": getattr(model, "schema", "unknown"),
            "elements_indexed": len(elem_rows),
            "properties_indexed": len(prop_rows),
            "quantities_indexed": len(qty_rows),
            "relations_indexed": len(rel_rows),
        }

    def query_sql(self, sql: str, limit: int = 100) -> Dict[str, Any]:
        """Ejecuta una consulta SELECT de solo lectura sobre DuckDB."""
        cleaned = (sql or "").strip().rstrip(";")
        if not cleaned.upper().startswith("SELECT"):
            raise ValueError("Solo se permiten consultas SELECT de solo lectura.")
        if _FORBIDDEN_SQL_RE.search(cleaned):
            raise ValueError("Consulta SQL rechazada por contener palabras clave no permitidas.")

        limited_sql = f"SELECT * FROM ({cleaned}) AS _q LIMIT {max(1, min(int(limit), 1000))}"
        with self._connect() as conn:
            cur = conn.execute(limited_sql)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        return {"columns": cols, "rows": rows, "count": len(rows)}

    def build_topology_graph(self, artifact_id: str) -> nx.DiGraph:
        """Construye un grafo NetworkX con los elementos y relaciones indexadas."""
        g = nx.DiGraph()
        with self._connect() as conn:
            elems = conn.execute(
                "SELECT global_id, ifc_class, name, disciplina, storey, system_name FROM elements WHERE artifact_id = ?",
                [artifact_id],
            ).fetchall()
            for gid, cls, name, disc, storey, sys_name in elems:
                g.add_node(
                    gid,
                    ifc_class=cls,
                    name=name,
                    disciplina=disc,
                    storey=storey,
                    system_name=sys_name,
                )
            rels = conn.execute(
                "SELECT rel_type, source_id, target_id FROM relations WHERE artifact_id = ?",
                [artifact_id],
            ).fetchall()
            for rel_type, src_id, dst_id in rels:
                g.add_edge(src_id, dst_id, rel_type=rel_type)
        return g

    def trace_system(
        self,
        artifact_id: str,
        start_global_id: str,
        max_hops: int = 10,
    ) -> Dict[str, Any]:
        """Traza nodos conectados aguas arriba/abajo desde un GlobalId usando BFS."""
        g = self.build_topology_graph(artifact_id)
        if start_global_id not in g:
            return {"start_global_id": start_global_id, "found": False, "nodes": [], "edges": []}

        undirected = g.to_undirected()
        lengths = nx.single_source_shortest_path_length(undirected, start_global_id, cutoff=max_hops)
        sub = g.subgraph(lengths.keys())
        nodes = [
            {"global_id": n, "hops": lengths[n], **sub.nodes[n]}
            for n in sorted(lengths.keys(), key=lambda x: (lengths[x], x))
        ]
        edges = [
            {"source": u, "target": v, "rel_type": d.get("rel_type", "")}
            for u, v, d in sub.edges(data=True)
        ]
        return {
            "artifact_id": artifact_id,
            "start_global_id": start_global_id,
            "found": True,
            "nodes": nodes,
            "edges": edges,
        }
