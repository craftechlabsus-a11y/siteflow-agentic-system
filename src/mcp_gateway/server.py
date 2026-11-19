"""MCP Gateway Dual (stdio + Streamable HTTP) para Antigravity IDE y Claude.

Expone Tools deterministas por ruta (`file_path` + `project_id`, nunca `file_bytes` en base64),
Resources (`bim://`, `doc://`, `project://`) y Prompts de ingeniería multi-especialidad.

Uso:
    python -m src.mcp_gateway.server --transport stdio
    python -m src.mcp_gateway.server --transport http --host 127.0.0.1 --port 8765
"""
import argparse
import asyncio
import hmac
import json
import logging
import os
from typing import Any, Dict, List, Optional
from urllib.parse import unquote

logger = logging.getLogger("src.mcp_gateway.server")

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    import mcp.types as types

    MCP_DISPONIBLE = True
except ImportError:
    MCP_DISPONIBLE = False
    Server = None
    types = None


def verify_mcp_http_auth(auth_header: Optional[str]) -> bool:
    """Valida Bearer token para transporte MCP HTTP usando comparación en tiempo constante."""
    expected = os.getenv("MCP_AUTH_TOKEN", "").strip()
    if not expected:
        return False
    if not auth_header or not auth_header.startswith("Bearer "):
        return False
    provided = auth_header[7:].strip()
    return hmac.compare_digest(provided, expected)


def _builtin_tools() -> List[Dict[str, Any]]:
    """Catálogo de herramientas MCP deterministas (entrada por file_path/project_id)."""
    return [
        {
            "name": "register_artifact",
            "description": (
                "Registra un archivo local (.ifc, .dxf, .dwg, .pdf, .docx, .xlsx, .xer) "
                "en workspace_cache/ con deduplicación SHA-256 por project_id."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Ruta absoluta al archivo local."},
                    "project_id": {"type": "string", "default": "default"},
                    "file_type": {"type": "string", "description": "Extensión opcional (ifc, pdf, dxf, etc.)."},
                },
                "required": ["file_path"],
            },
        },
        {
            "name": "bim_query_elements",
            "description": (
                "Consulta elementos BIM indexados con filtros (ifc_class, disciplina, storey), "
                "paginación o agregación SQL segura."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "artifact_id": {"type": "string"},
                    "ifc_class": {"type": "string"},
                    "disciplina": {"type": "string"},
                    "storey": {"type": "string"},
                    "group_by": {
                        "type": "string",
                        "enum": ["ifc_class", "disciplina", "storey", "system_name", "material"],
                    },
                    "limit": {"type": "integer", "default": 50},
                    "offset": {"type": "integer", "default": 0},
                },
                "required": ["artifact_id"],
            },
        },
        {
            "name": "bim_detect_clashes",
            "description": "Ejecuta detección determinista de interferencias (AABB) entre elementos BIM.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "elements": {"type": "array", "items": {"type": "object"}},
                    "tolerance_m": {"type": "number", "default": 0.005},
                    "clearance_m": {"type": "number", "default": 0.0},
                },
                "required": ["elements"],
            },
        },
        {
            "name": "doc_search",
            "description": "Busca en documentos técnicos indexados (FTS5 BM25) sin límite de caracteres.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "artifact_id": {"type": "string"},
                    "limit": {"type": "integer", "default": 10},
                },
                "required": ["query"],
            },
        },
        {
            "name": "planificacion_pdm",
            "description": "Resuelve un grafo PDM completo (FS, SS, FF, SF + lags) y devuelve ruta crítica.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "activities": {"type": "array", "items": {"type": "object"}},
                    "dependencies": {"type": "array", "items": {"type": "object"}},
                },
                "required": ["activities", "dependencies"],
            },
        },
        {
            "name": "export_planning",
            "description": "Exporta una planificación 4D a MS Project XML o Primavera P6 (.xer).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "format": {"type": "string", "enum": ["msproject_xml", "primavera_xer"]},
                    "project_name": {"type": "string"},
                    "activities": {"type": "array", "items": {"type": "object"}},
                    "dependencies": {"type": "array", "items": {"type": "object"}},
                    "start_date": {"type": "string", "description": "Fecha inicio YYYY-MM-DD (opcional)."},
                },
                "required": ["format", "project_name", "activities"],
            },
        },
        {
            "name": "project_graph_summary",
            "description": "Devuelve el resumen del grafo de conocimiento del proyecto (BIM/CAD/Docs/WBS).",
            "inputSchema": {
                "type": "object",
                "properties": {"project_id": {"type": "string", "default": "default"}},
            },
        },
        {
            "name": "bim_index_ifc",
            "description": "Indexa un archivo .ifc (IFC2x3/4/4.3) en DuckDB + NetworkX (elementos, Pset_*, Qto_* y relaciones).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "project_id": {"type": "string", "default": "default"},
                },
                "required": ["file_path"],
            },
        },
        {
            "name": "bim_query_sql",
            "description": "Ejecuta una consulta SQL SELECT de solo lectura sobre DuckDB (elements, properties, quantities, relations).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "sql": {"type": "string"},
                    "limit": {"type": "integer", "default": 100},
                },
                "required": ["sql"],
            },
        },
        {
            "name": "bim_trace_system",
            "description": "Traza la conectividad topológica (NetworkX) aguas arriba/abajo desde un GlobalId IFC.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "artifact_id": {"type": "string"},
                    "start_global_id": {"type": "string"},
                    "max_hops": {"type": "integer", "default": 10},
                },
                "required": ["artifact_id", "start_global_id"],
            },
        },
        {
            "name": "cad_parse_dxf",
            "description": "Extrae geometría exacta (longitudes, áreas, bloques, ATTRIB, DIMENSION, MTEXT) de un plano .dxf.",
            "inputSchema": {
                "type": "object",
                "properties": {"file_path": {"type": "string"}},
                "required": ["file_path"],
            },
        },
        {
            "name": "pdf_inspect_2d",
            "description": "Clasifica un plano PDF (vectorial vs escaneado), extrae trazos/capas OCG o genera tiles con solape.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "mode": {"type": "string", "enum": ["classify", "vectorial", "tiles"], "default": "classify"},
                    "page_num": {"type": "integer", "default": 0},
                    "grid_cols": {"type": "integer", "default": 2},
                    "grid_rows": {"type": "integer", "default": 2},
                },
                "required": ["file_path"],
            },
        },
        {
            "name": "doc_index_file",
            "description": "Indexa un documento técnico (PDF, DOCX, TXT/MD) en chunks jerárquicos + FTS5 por SHA-256.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "project_id": {"type": "string", "default": "default"},
                },
                "required": ["file_path"],
            },
        },
        {
            "name": "doc_cross_audit",
            "description": "Cruza especificaciones del Pliego/Memoria (DN, PN, HA/HM, normas) contra BIM DuckDB y emite hallazgos trazables.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "doc_artifact_id": {"type": "string"},
                    "bim_artifact_id": {"type": "string"},
                },
                "required": ["doc_artifact_id", "bim_artifact_id"],
            },
        },
        {
            "name": "compose_specialty_profile",
            "description": "Compone un cartucho multi-especialidad (Tipología + Disciplinas) con clases IFC, normativas y checks.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "typology": {"type": "string"},
                    "disciplines": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["typology"],
            },
        },
        {
            "name": "export_4d_excel",
            "description": "Genera un Excel (.xlsx) con Cronograma 4D, vinculación GlobalId y Curva S acumulada.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_name": {"type": "string"},
                    "output_path": {"type": "string"},
                    "activities": {"type": "array", "items": {"type": "object"}},
                    "dependencies": {"type": "array", "items": {"type": "object"}},
                    "wbs_bim_map": {"type": "object"},
                },
                "required": ["project_name", "output_path", "activities"],
            },
        },
        {
            "name": "planificacion_obra_civil",
            "description": "Genera cronograma PDM/CPM de Obra Civil y Movimiento de Tierras desde elementos BIM y cotas zmin.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "elementos_bim": {"type": "array", "items": {"type": "object"}},
                    "tipo_terreno": {"type": "string", "default": "tierra"},
                    "plazo_objetivo_dias": {"type": "number"},
                    "frentes": {"type": "integer", "default": 1},
                },
                "required": ["elementos_bim"],
            },
        },
        {
            "name": "coherencia_triple_check",
            "description": "Cruza mediciones de Planos vs Presupuesto con trazabilidad SHA-256 y tolerancia configurable.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "module_id": {"type": "string", "default": "general"},
                    "parametros_memoria": {"type": "object"},
                    "mediciones_planos": {"type": "object"},
                    "mediciones_presupuesto": {"type": "object"},
                    "tolerancia_pct": {"type": "number", "default": 5.0},
                },
                "required": ["mediciones_planos", "mediciones_presupuesto"],
            },
        },
        {
            "name": "hidraulica_perfil_longitudinal",
            "description": "Interpreta un perfil longitudinal hidráulico (canales/tuberías) por visión LLM con switch de cuota al 96%.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "page_num": {"type": "integer", "default": 0},
                    "tramo": {"type": "string", "default": ""},
                },
                "required": ["file_path"],
            },
        },
        {
            "name": "hidraulica_pid_equipos",
            "description": "Extrae equipos, líneas DN/PN e instrumentación de un P&ID (EDAR/IDAM) por visión LLM con switch de cuota al 96%.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "page_num": {"type": "integer", "default": 0},
                    "proceso": {"type": "string", "default": ""},
                },
                "required": ["file_path"],
            },
        },
        {
            "name": "llm_quota_status",
            "description": "Consulta el estado del pool de claves LLM y el switch proactivo de cuota al 96%.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "project_orchestrate_filter",
            "description": (
                "Orquestador conector único: ingesta archivos en paralelo vía DAGRunner, carga el "
                "cartucho de tipología y devuelve únicamente resúmenes filtrados (BIM, DXF, Docs, Grafo) sin saturar el contexto."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string", "default": "default"},
                    "typology": {"type": "string"},
                    "disciplines": {"type": "array", "items": {"type": "string"}},
                    "file_paths": {"type": "array", "items": {"type": "string"}},
                    "filters": {
                        "type": "object",
                        "properties": {
                            "ifc_class": {"type": "string"},
                            "storey": {"type": "string"},
                            "disciplina": {"type": "string"},
                            "doc_query": {"type": "string"},
                            "limit": {"type": "integer", "default": 25},
                        },
                    },
                },
            },
        },
    ]


def _execute_tool_sync(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    if name == "register_artifact":
        from src.core.artifact_store import ArtifactStore

        meta = ArtifactStore().register_file_path(
            arguments["file_path"],
            project_id=arguments.get("project_id") or "default",
            file_type=arguments.get("file_type"),
        )
        return {
            "artifact_id": meta.artifact_id,
            "sha256": meta.sha256,
            "project_id": meta.project_id,
            "filename": meta.filename,
            "file_type": meta.file_type,
            "size_bytes": meta.size_bytes,
            "storage_path": meta.storage_path,
            "index_status": meta.index_status,
        }

    if name == "bim_query_elements":
        from src.engines.bim_graph.bim_index import BimIndex

        return BimIndex().query_elements(
            arguments["artifact_id"],
            ifc_class=arguments.get("ifc_class"),
            disciplina=arguments.get("disciplina"),
            storey=arguments.get("storey"),
            group_by=arguments.get("group_by"),
            limit=int(arguments.get("limit", 50)),
            offset=int(arguments.get("offset", 0)),
        )

    if name == "bim_detect_clashes":
        from src.engines.bim_graph.clashes import detect_clashes

        clashes = detect_clashes(
            arguments.get("elements", []),
            tolerance_m=float(arguments.get("tolerance_m", 0.005)),
            clearance_m=float(arguments.get("clearance_m", 0.0)),
        )
        return {"clashes": clashes, "count": len(clashes)}

    if name == "doc_search":
        from src.engines.docs_rag.doc_index import DocIndex

        hits = DocIndex().hybrid_search(
            arguments["query"],
            artifact_id=arguments.get("artifact_id"),
            limit=int(arguments.get("limit", 10)),
        )
        return {"hits": hits, "count": len(hits)}

    if name == "planificacion_pdm":
        from src.engines.planning_4d.pdm_engine import solve_pdm

        return solve_pdm(arguments.get("activities", []), arguments.get("dependencies", []))

    if name == "export_planning":
        fmt = arguments["format"]
        if fmt == "msproject_xml":
            from src.engines.planning_4d.pdm_engine import export_msproject_xml, solve_pdm

            deps = arguments.get("dependencies", [])
            pdm = solve_pdm(arguments.get("activities", []), deps)
            xml_text = export_msproject_xml(
                arguments["project_name"],
                pdm,
                deps,
            )
            return {"format": fmt, "content": xml_text}
        if fmt == "primavera_xer":
            from src.engines.planning_4d.pdm_engine import solve_pdm
            from src.engines.planning_4d.primavera_p6 import exportar_primavera_xer

            deps = arguments.get("dependencies", [])
            pdm = solve_pdm(arguments.get("activities", []), deps)
            preds_map: Dict[str, List[str]] = {}
            for d in deps:
                preds_map.setdefault(str(d["succ"]), []).append(str(d["pred"]))
            plan = {
                "duracion_total_dias": pdm["project_duration"],
                "actividades": [
                    {
                        "id": it["id"],
                        "nombre": it.get("name", it["id"]),
                        "duracion_dias": it["duration"],
                        "es": it["ES"],
                        "ef": it["EF"],
                        "predecesoras": preds_map.get(str(it["id"]), []),
                    }
                    for it in pdm.get("schedule", [])
                ],
            }
            xer_text = exportar_primavera_xer(
                plan,
                project_name=arguments["project_name"],
                start_date_iso=arguments.get("start_date"),
            )
            return {"format": fmt, "content": xer_text}
        raise ValueError(f"Formato de exportación no soportado: {fmt}")

    if name == "project_graph_summary":
        from src.core.project_graph import ProjectGraph

        return ProjectGraph(arguments.get("project_id") or "default").summary()

    if name == "bim_index_ifc":
        from src.core.artifact_store import ArtifactStore
        from src.engines.bim_graph.graph_store import BimGraphStore

        meta = ArtifactStore().register_file_path(
            arguments["file_path"],
            project_id=arguments.get("project_id") or "default",
            file_type="ifc",
        )
        return BimGraphStore().index_ifc_file(meta.storage_path, meta.artifact_id)

    if name == "bim_query_sql":
        from src.engines.bim_graph.graph_store import BimGraphStore

        return BimGraphStore().query_sql(arguments["sql"], limit=int(arguments.get("limit", 100)))

    if name == "bim_trace_system":
        from src.engines.bim_graph.graph_store import BimGraphStore

        return BimGraphStore().trace_system(
            arguments["artifact_id"],
            arguments["start_global_id"],
            max_hops=int(arguments.get("max_hops", 10)),
        )

    if name == "cad_parse_dxf":
        from src.engines.cad_vision.parser import parse_dxf

        return parse_dxf(arguments["file_path"])

    if name == "pdf_inspect_2d":
        from src.engines.cad_vision.pdf_pipeline import (
            classify_pdf,
            extract_vectorial_pdf,
            render_pdf_tiles,
        )

        mode = arguments.get("mode", "classify")
        if mode == "classify":
            return classify_pdf(arguments["file_path"])
        if mode == "vectorial":
            return extract_vectorial_pdf(arguments["file_path"])
        if mode == "tiles":
            return render_pdf_tiles(
                arguments["file_path"],
                page_num=int(arguments.get("page_num", 0)),
                grid_cols=int(arguments.get("grid_cols", 2)),
                grid_rows=int(arguments.get("grid_rows", 2)),
            )
        raise ValueError(f"Modo pdf_inspect_2d no soportado: {mode}")

    if name == "doc_index_file":
        from src.core.artifact_store import ArtifactStore
        from src.engines.docs_rag.doc_index import DocIndex

        meta = ArtifactStore().register_file_path(
            arguments["file_path"],
            project_id=arguments.get("project_id") or "default",
        )
        return DocIndex().index_file_path(
            meta.storage_path,
            meta.artifact_id,
            sha256=meta.sha256,
        )

    if name == "doc_cross_audit":
        from src.engines.docs_rag.cross_audit import run_cross_audit

        return run_cross_audit(
            arguments["doc_artifact_id"],
            arguments["bim_artifact_id"],
        )

    if name == "compose_specialty_profile":
        from src.specialties.profiles.composer import compose_specialty_profile

        return compose_specialty_profile(
            arguments["typology"],
            disciplines=arguments.get("disciplines"),
        )

    if name == "export_4d_excel":
        from src.engines.planning_4d.exporters import export_schedule_excel
        from src.engines.planning_4d.pdm_engine import solve_pdm

        pdm = solve_pdm(arguments.get("activities", []), arguments.get("dependencies", []))
        saved = export_schedule_excel(
            pdm,
            arguments["output_path"],
            project_name=arguments["project_name"],
            wbs_bim_map=arguments.get("wbs_bim_map"),
        )
        return {
            "output_path": saved,
            "project_duration": pdm["project_duration"],
            "critical_path": pdm["critical_path"],
        }

    if name == "planificacion_obra_civil":
        from src.core.skills_base import run_skill
        from src.orchestrator.dag_runner import ensure_skills_loaded

        ensure_skills_loaded()
        return run_skill("planificacion_generar_cronograma_civil", arguments).model_dump()

    if name == "coherencia_triple_check":
        from src.core.skills_base import run_skill
        from src.orchestrator.dag_runner import ensure_skills_loaded

        ensure_skills_loaded()
        return run_skill("documental_coherencia_triple_check", arguments).model_dump()

    if name == "hidraulica_perfil_longitudinal":
        from src.core.skills_base import run_skill
        from src.orchestrator.dag_runner import ensure_skills_loaded

        ensure_skills_loaded()
        return run_skill("vision_perfil_longitudinal_hidraulico", arguments).model_dump()

    if name == "hidraulica_pid_equipos":
        from src.core.skills_base import run_skill
        from src.orchestrator.dag_runner import ensure_skills_loaded

        ensure_skills_loaded()
        return run_skill("vision_pid_equipos_proceso", arguments).model_dump()

    if name == "llm_quota_status":
        from src.core.llm_pool import status

        return status()

    if name == "project_orchestrate_filter":
        from src.orchestrator.project_connector import orchestrate_and_filter

        return orchestrate_and_filter(
            project_id=arguments.get("project_id") or "default",
            typology=arguments.get("typology"),
            disciplines=arguments.get("disciplines"),
            file_paths=arguments.get("file_paths"),
            filters=arguments.get("filters"),
        )

    raise ValueError(f"Herramienta MCP desconocida: {name}")


def _read_resource_sync(uri: str) -> str:
    if uri.startswith("project://") and uri.endswith("/graph_summary"):
        project_id = uri[len("project://") : -len("/graph_summary")]
        from src.core.project_graph import ProjectGraph

        return json.dumps(ProjectGraph(project_id).summary(), ensure_ascii=False)

    if uri.startswith("bim://"):
        rest = uri[len("bim://") :]
        parts = rest.split("/", 2)
        if len(parts) >= 2:
            project_id, action = parts[0], parts[1]
            from src.core.artifact_store import ArtifactStore
            from src.engines.bim_graph.bim_index import BimIndex

            artifacts = [
                a for a in ArtifactStore().list_by_project(project_id) if a.file_type == "ifc"
            ]
            if action == "summary":
                bim = BimIndex()
                summaries = [
                    {
                        "artifact_id": a.artifact_id,
                        "filename": a.filename,
                        "by_class": bim.query_elements(a.artifact_id, group_by="ifc_class"),
                    }
                    for a in artifacts
                ]
                return json.dumps({"project_id": project_id, "models": summaries}, ensure_ascii=False)
            if action == "storeys" and len(parts) == 3:
                storey = unquote(parts[2])
                bim = BimIndex()
                items = [
                    {
                        "artifact_id": a.artifact_id,
                        "elements": bim.query_elements(a.artifact_id, storey=storey, limit=200),
                    }
                    for a in artifacts
                ]
                return json.dumps({"project_id": project_id, "storey": storey, "models": items}, ensure_ascii=False)
            if action == "query" and len(parts) == 3:
                ifc_class = unquote(parts[2])
                bim = BimIndex()
                items = [
                    {
                        "artifact_id": a.artifact_id,
                        "elements": bim.query_elements(a.artifact_id, ifc_class=ifc_class, limit=200),
                    }
                    for a in artifacts
                ]
                return json.dumps({"project_id": project_id, "filter": ifc_class, "models": items}, ensure_ascii=False)

    if uri.startswith("doc://"):
        rest = uri[len("doc://") :]
        parts = rest.split("/", 2)
        if len(parts) >= 2:
            project_id, action = parts[0], parts[1]
            from src.core.artifact_store import ArtifactStore
            from src.engines.docs_rag.doc_index import DocIndex

            artifacts = [
                a for a in ArtifactStore().list_by_project(project_id) if a.file_type in ("pdf", "docx")
            ]
            doc = DocIndex()
            if action == "toc":
                tocs = [
                    {"artifact_id": a.artifact_id, "filename": a.filename, "toc": doc.get_toc(a.artifact_id)}
                    for a in artifacts
                ]
                return json.dumps({"project_id": project_id, "documents": tocs}, ensure_ascii=False)
            if action == "section" and len(parts) == 3:
                section_id = unquote(parts[2])
                hits = doc.search(section_id, limit=20)
                return json.dumps({"project_id": project_id, "section": section_id, "hits": hits}, ensure_ascii=False)

    raise ValueError(f"Resource URI no soportada: {uri}")


_PROMPTS: Dict[str, Dict[str, str]] = {
    "auditar-bim": {
        "description": "Audita un modelo BIM (.ifc) frente a requisitos de ingeniería y detecta interferencias.",
        "template": (
            "Proyecto: {project_id}\n"
            "1. Registra el modelo IFC con `register_artifact(file_path, project_id)` si aún no está indexado.\n"
            "2. Consulta `bim://{project_id}/summary` y `bim_query_elements` agrupando por `ifc_class` y `storey`.\n"
            "3. Ejecuta `bim_detect_clashes` entre disciplinas (estructuras vs MEP/tuberías).\n"
            "4. Reporta únicamente cifras deterministas observadas con trazabilidad SHA-256."
        ),
    },
    "extraer-mediciones": {
        "description": "Extrae mediciones deterministas desde BIM y/o planos 2D con trazabilidad.",
        "template": (
            "Proyecto: {project_id}\n"
            "1. Registra los archivos fuente (.ifc, .dxf, .pdf) con `register_artifact`.\n"
            "2. Consulta mediciones por planta/sistema con `bim_query_elements`.\n"
            "3. Verifica coherencia de unidades (m, m2, m3, kg, ud) y marca cualquier elemento sin cuantificar."
        ),
    },
    "planificar-4d": {
        "description": "Genera una planificación 4D (PDM/CPM) vinculada a elementos BIM/obra civil.",
        "template": (
            "Proyecto: {project_id}\n"
            "1. Obtén volúmenes y elementos desde `bim_query_elements`.\n"
            "2. Define actividades y dependencias FS/SS/FF/SF con lags.\n"
            "3. Ejecuta `planificacion_pdm` para calcular ruta crítica y holguras.\n"
            "4. Exporta el cronograma con `export_planning` (`msproject_xml` o `primavera_xer`)."
        ),
    },
    "cruzar-memoria-planos": {
        "description": "Cruza especificaciones del Pliego/Memoria contra elementos BIM y planos 2D.",
        "template": (
            "Proyecto: {project_id}\n"
            "1. Consulta `doc://{project_id}/toc` y busca parámetros clave (DN, PN, hormigón, normativa) con `doc_search`.\n"
            "2. Contrasta cada requisito con `bim_query_elements`.\n"
            "3. Documenta cada discrepancia con referencia exacta a página/sección, SHA-256 e impacto."
        ),
    },
}


def crear_servidor():
    """Construye el servidor MCP Gateway con Tools, Resources y Prompts (mcp 2.x)."""
    if not MCP_DISPONIBLE:
        raise RuntimeError("El paquete 'mcp' no está instalado (pip install mcp)")

    async def handle_list_tools(_ctx, _params):
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=s["name"],
                    description=s["description"],
                    inputSchema=s["inputSchema"],
                )
                for s in _builtin_tools()
            ]
        )

    async def handle_call_tool(_ctx, params):
        name = params.name
        arguments = dict(params.arguments or {})
        try:
            payload = await asyncio.to_thread(_execute_tool_sync, name, arguments)
        except (ValueError, FileNotFoundError, KeyError) as e:
            payload = {"error": str(e)}
        except Exception as e:
            logger.warning("call_tool %s falló: %s", name, e)
            payload = {"error": f"Fallo ejecutando '{name}': {e}"}
        return types.CallToolResult(
            content=[
                types.TextContent(
                    type="text",
                    text=json.dumps(payload, ensure_ascii=False, default=str),
                )
            ]
        )

    async def handle_list_resources(_ctx, _params):
        return types.ListResourcesResult(
            resources=[
                types.Resource(
                    uri="bim://default/summary",
                    name="BIM Summary (default)",
                    description="Resumen de modelos BIM indexados por clase IFC y disciplina.",
                    mimeType="application/json",
                ),
                types.Resource(
                    uri="doc://default/toc",
                    name="Documents TOC (default)",
                    description="Índice jerárquico de memorias y pliegos indexados.",
                    mimeType="application/json",
                ),
                types.Resource(
                    uri="project://default/graph_summary",
                    name="Project Graph Summary (default)",
                    description="Resumen del grafo transversal de conocimiento del proyecto.",
                    mimeType="application/json",
                ),
            ]
        )

    async def handle_list_resource_templates(_ctx, _params):
        return types.ListResourceTemplatesResult(
            resourceTemplates=[
                types.ResourceTemplate(
                    uriTemplate="bim://{project_id}/summary",
                    name="BIM Summary",
                    description="Resumen BIM por proyecto.",
                    mimeType="application/json",
                ),
                types.ResourceTemplate(
                    uriTemplate="bim://{project_id}/storeys/{storey}",
                    name="BIM Elements by Storey",
                    description="Elementos BIM filtrados por planta/nivel.",
                    mimeType="application/json",
                ),
                types.ResourceTemplate(
                    uriTemplate="bim://{project_id}/query/{filter_expr}",
                    name="BIM Query by Class",
                    description="Elementos BIM filtrados por clase IFC.",
                    mimeType="application/json",
                ),
                types.ResourceTemplate(
                    uriTemplate="doc://{project_id}/toc",
                    name="Document TOC",
                    description="Tabla de contenidos de documentación técnica.",
                    mimeType="application/json",
                ),
                types.ResourceTemplate(
                    uriTemplate="doc://{project_id}/section/{section_id}",
                    name="Document Section",
                    description="Consulta de sección documental.",
                    mimeType="application/json",
                ),
                types.ResourceTemplate(
                    uriTemplate="project://{project_id}/graph_summary",
                    name="Project Graph Summary",
                    description="Resumen del grafo transversal del proyecto.",
                    mimeType="application/json",
                ),
            ]
        )

    async def handle_read_resource(_ctx, params):
        uri_str = str(params.uri)
        text = await asyncio.to_thread(_read_resource_sync, uri_str)
        return types.ReadResourceResult(
            contents=[
                types.TextResourceContents(
                    uri=params.uri,
                    text=text,
                    mimeType="application/json",
                )
            ]
        )

    async def handle_list_prompts(_ctx, _params):
        return types.ListPromptsResult(
            prompts=[
                types.Prompt(
                    name=name,
                    description=cfg["description"],
                    arguments=[
                        types.PromptArgument(
                            name="project_id",
                            description="Identificador del proyecto (por defecto 'default').",
                            required=False,
                        )
                    ],
                )
                for name, cfg in _PROMPTS.items()
            ]
        )

    async def handle_get_prompt(_ctx, params):
        name = params.name
        if name not in _PROMPTS:
            raise ValueError(f"Prompt desconocido: {name}")
        project_id = (params.arguments or {}).get("project_id") or "default"
        text = _PROMPTS[name]["template"].format(project_id=project_id)
        return types.GetPromptResult(
            description=_PROMPTS[name]["description"],
            messages=[
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(type="text", text=text),
                )
            ],
        )

    return Server(
        "siteflow-agentic-gateway",
        on_list_tools=handle_list_tools,
        on_call_tool=handle_call_tool,
        on_list_resources=handle_list_resources,
        on_list_resource_templates=handle_list_resource_templates,
        on_read_resource=handle_read_resource,
        on_list_prompts=handle_list_prompts,
        on_get_prompt=handle_get_prompt,
    )


def build_http_app():
    """Construye la app ASGI Streamable HTTP con autenticación Bearer opcional."""
    from contextlib import asynccontextmanager
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Mount, Route

    server = crear_servidor()
    session_manager = StreamableHTTPSessionManager(app=server, stateless=True)

    @asynccontextmanager
    async def lifespan(_app):
        async with session_manager.run():
            yield

    async def mcp_asgi(scope, receive, send):
        if os.getenv("MCP_AUTH_TOKEN", "").strip():
            headers = dict(scope.get("headers", []))
            auth = headers.get(b"authorization", b"").decode("utf-8", errors="ignore")
            if not verify_mcp_http_auth(auth):
                resp = JSONResponse({"error": "Unauthorized"}, status_code=401)
                await resp(scope, receive, send)
                return
        await session_manager.handle_request(scope, receive, send)

    async def health(_request):
        return JSONResponse({"status": "ok", "service": "siteflow-agentic-gateway"})

    return Starlette(
        lifespan=lifespan,
        routes=[
            Route("/health", health, methods=["GET"]),
            Mount("/mcp", app=mcp_asgi),
        ],
    )


async def main(transport: str = "stdio", host: str = "127.0.0.1", port: int = 8765):
    if transport == "stdio":
        server = crear_servidor()
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())
    else:
        import uvicorn

        app = build_http_app()
        config = uvicorn.Config(app, host=host, port=port, log_level="info")
        srv = uvicorn.Server(config)
        await srv.serve()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SiteFlow Agentic MCP Gateway")
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main(transport=args.transport, host=args.host, port=args.port))
