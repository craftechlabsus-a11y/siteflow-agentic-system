"""Conector Orquestador Determinista: ingesta paralela por DAGRunner + filtrado selectivo para IDE/Claude."""
import asyncio
import os
from typing import Any, Dict, List, Optional

from src.core.artifact_store import ArtifactStore
from src.core.llm_pool import status as llm_pool_status
from src.core.project_graph import ProjectGraph
from src.engines.bim_graph.graph_store import BimGraphStore
from src.engines.cad_vision.parser import parse_dxf
from src.engines.cad_vision.pdf_pipeline import classify_pdf
from src.engines.docs_rag.doc_index import DocIndex
from src.orchestrator.dag_runner import DAGNode, DAGRunner
from src.specialties.profiles.composer import compose_specialty_profile


def _process_single_file(
    file_path: str,
    *,
    project_id: str,
    store: ArtifactStore,
    bim_store: BimGraphStore,
    doc_index: DocIndex,
    graph: ProjectGraph,
) -> Dict[str, Any]:
    meta = store.register_file_path(file_path, project_id=project_id)
    ext = meta.file_type.lower()
    summary: Dict[str, Any] = {
        "artifact_id": meta.artifact_id,
        "filename": meta.filename,
        "file_type": ext,
        "sha256": meta.sha256,
        "size_bytes": meta.size_bytes,
    }
    graph.upsert_node(meta.artifact_id, f"artifact:{ext}", meta.filename, source_sha256=meta.sha256)

    if ext == "ifc":
        idx_res = bim_store.index_ifc_file(meta.storage_path, meta.artifact_id)
        summary["bim_index"] = idx_res
    elif ext == "dxf":
        parsed = parse_dxf(meta.storage_path)
        summary["dxf_summary"] = {
            "unidades": parsed["unidades"],
            "capas_count": len(parsed["capas"]),
            "por_capa": parsed["por_capa"],
            "bloques": parsed["bloques"],
            "n_cotas": len(parsed.get("cotas", [])),
        }
    elif ext == "pdf":
        cls = classify_pdf(meta.storage_path)
        summary["pdf_classification"] = cls["classification"]
        summary["total_pages"] = cls["total_pages"]
        if cls["classification"] in ("vectorial", "mixto"):
            summary["doc_index"] = doc_index.index_file_path(
                meta.storage_path, meta.artifact_id, sha256=meta.sha256
            )
    elif ext in ("docx", "txt", "md"):
        summary["doc_index"] = doc_index.index_file_path(
            meta.storage_path, meta.artifact_id, sha256=meta.sha256
        )

    return summary


async def orchestrate_and_filter_async(
    *,
    project_id: str = "default",
    typology: Optional[str] = None,
    disciplines: Optional[List[str]] = None,
    file_paths: Optional[List[str]] = None,
    filters: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Orquesta ingesta paralela (DAGRunner) y devuelve únicamente información filtrada al IDE."""
    flt = dict(filters or {})
    limit = max(1, min(int(flt.get("limit", 25)), 200))

    store = ArtifactStore()
    bim_store = BimGraphStore()
    doc_index = DocIndex()
    graph = ProjectGraph(project_id)

    specialty_profile: Optional[Dict[str, Any]] = None
    if typology:
        prof = compose_specialty_profile(typology, disciplines=disciplines)
        specialty_profile = {
            "typology": prof["typology"],
            "label": prof["label"],
            "disciplines": prof["disciplines"],
            "ifc_classes_count": len(prof["ifc_classes"]),
            "checks_obligatorios": prof["checks_obligatorios"],
        }

    artifacts_processed: List[Dict[str, Any]] = []
    if file_paths:
        runner = DAGRunner()
        nodes: List[DAGNode] = []
        for idx, fp in enumerate(file_paths):
            def _make_handler(path_val: str):
                def _h(_params, _deps):
                    return _process_single_file(
                        path_val,
                        project_id=project_id,
                        store=store,
                        bim_store=bim_store,
                        doc_index=doc_index,
                        graph=graph,
                    )
                return _h

            nodes.append(DAGNode(node_id=f"ingest_{idx}", handler=_make_handler(fp)))

        dag_res = await runner.execute(nodes, project_id=project_id)
        for nid in sorted(dag_res["nodes"].keys()):
            n_info = dag_res["nodes"][nid]
            if n_info["status"] == "completed" and n_info["result"]:
                artifacts_processed.append(n_info["result"])
            elif n_info["status"] == "failed":
                artifacts_processed.append({"node_id": nid, "error": n_info["error"]})

    # Filtrado selectivo sobre BIM DuckDB
    bim_ids = [a["artifact_id"] for a in artifacts_processed if a.get("file_type") == "ifc"]
    if not bim_ids:
        bim_ids = [a.artifact_id for a in store.list_by_project(project_id) if a.file_type == "ifc"]

    bim_filtered: Dict[str, Any] = {"summary_by_class": [], "elements": []}
    if bim_ids:
        id_list = ", ".join(f"'{aid.replace(chr(39), chr(39)*2)}'" for aid in bim_ids)
        where_clauses = [f"artifact_id IN ({id_list})"]
        if flt.get("ifc_class"):
            cls_val = str(flt["ifc_class"]).replace("'", "''")
            where_clauses.append(f"ifc_class = '{cls_val}'")
        if flt.get("storey"):
            st_val = str(flt["storey"]).replace("'", "''")
            where_clauses.append(f"storey = '{st_val}'")
        if flt.get("disciplina"):
            disc_val = str(flt["disciplina"]).replace("'", "''")
            where_clauses.append(f"disciplina = '{disc_val}'")
        where_sql = " AND ".join(where_clauses)

        bim_filtered["summary_by_class"] = bim_store.query_sql(
            f"SELECT ifc_class, disciplina, COUNT(*) AS count, ROUND(SUM(volume_m3), 3) AS total_vol_m3 "
            f"FROM elements WHERE {where_sql} GROUP BY ifc_class, disciplina ORDER BY count DESC",
            limit=limit,
        )["rows"]
        bim_filtered["elements"] = bim_store.query_sql(
            f"SELECT artifact_id, global_id, ifc_class, name, disciplina, storey, material, volume_m3, area_m2, length_m "
            f"FROM elements WHERE {where_sql}",
            limit=limit,
        )["rows"]

    # Filtrado selectivo sobre Documentación FTS5
    doc_hits: List[Dict[str, Any]] = []
    if flt.get("doc_query"):
        doc_hits = doc_index.hybrid_search(str(flt["doc_query"]), limit=limit)
        for h in doc_hits:
            h["text"] = h["text"][:600]

    doc_tocs: List[Dict[str, Any]] = []
    for a in store.list_by_project(project_id):
        if a.file_type in ("pdf", "docx", "txt", "md"):
            toc = doc_index.get_toc(a.artifact_id)
            if toc:
                doc_tocs.append({"artifact_id": a.artifact_id, "filename": a.filename, "toc": toc[:20]})

    return {
        "project_id": project_id,
        "specialty_profile": specialty_profile,
        "artifacts_processed": artifacts_processed,
        "bim_filtered": bim_filtered,
        "doc_hits": doc_hits,
        "doc_tocs": doc_tocs,
        "graph_summary": graph.summary(),
        "llm_quota_status": llm_pool_status(),
    }


def orchestrate_and_filter(**kwargs: Any) -> Dict[str, Any]:
    """Wrapper síncrono para ejecución desde asyncio.to_thread en MCP."""
    return asyncio.run(orchestrate_and_filter_async(**kwargs))
