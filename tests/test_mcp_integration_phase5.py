"""Pruebas de Fase 5: Integración real MCP (stdio ClientSession) de punta a punta."""
import json
import os
import sys

import ezdxf
import ifcopenshell
import ifcopenshell.api
import pymupdf
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def _build_fixtures(tmp_path):
    ifc_path = str(tmp_path / "planta_captacion.ifc")
    model = ifcopenshell.file(schema="IFC4")
    proj = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcProject", name="Captación")
    ifcopenshell.api.run("unit.assign_unit", model)
    site = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcSite", name="Río")
    bldg = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcBuilding", name="Estación")
    storey = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcBuildingStorey", name="Cota 0")
    ifcopenshell.api.run("aggregate.assign_object", model, relating_object=proj, products=[site])
    ifcopenshell.api.run("aggregate.assign_object", model, relating_object=site, products=[bldg])
    ifcopenshell.api.run("aggregate.assign_object", model, relating_object=bldg, products=[storey])

    pump = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcPump", name="Bomba Captación B1")
    pipe = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcPipeSegment", name="Impulsión DN400")
    ifcopenshell.api.run("spatial.assign_container", model, relating_structure=storey, products=[pump, pipe])
    pset = ifcopenshell.api.run("pset.add_pset", model, product=pipe, name="Pset_PipeSegmentTypeCommon")
    ifcopenshell.api.run("pset.edit_pset", model, pset=pset, properties={"NominalDiameter": 400.0})
    model.create_entity(
        "IfcRelConnectsElements",
        GlobalId=ifcopenshell.guid.new(),
        RelatingElement=pump,
        RelatedElement=pipe,
    )
    model.write(ifc_path)

    dxf_path = str(tmp_path / "planta_captacion.dxf")
    dxf = ezdxf.new("R2010")
    dxf.header["$INSUNITS"] = 6
    dxf.modelspace().add_line((0, 0), (25, 0), dxfattribs={"layer": "IMPULSION_DN400"})
    dxf.saveas(dxf_path)

    pdf_path = str(tmp_path / "memoria_captacion.pdf")
    pdf = pymupdf.open()
    p = pdf.new_page()
    p.insert_text(
        (40, 60),
        "1. MEMORIA DESCRIPTIVA\n"
        "La conducción de impulsión será de acero DN600 PN16 según UNE-EN 10224.\n",
    )
    pdf.save(pdf_path)
    pdf.close()

    return ifc_path, dxf_path, pdf_path, pump.GlobalId


@pytest.mark.asyncio
async def test_mcp_stdio_end_to_end_engineering_flow(tmp_path):
    ifc_path, dxf_path, pdf_path, pump_gid = _build_fixtures(tmp_path)
    proj_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ws_cache = str(tmp_path / "ws_cache")

    env = dict(os.environ)
    env["PYTHONPATH"] = proj_root
    env["WORKSPACE_CACHE_DIR"] = ws_cache

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "src.mcp_gateway.server", "--transport", "stdio"],
        cwd=proj_root,
        env=env,
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools_res = await session.list_tools()
            tool_names = {t.name for t in tools_res.tools}
            assert {
                "register_artifact",
                "bim_index_ifc",
                "bim_query_sql",
                "bim_trace_system",
                "cad_parse_dxf",
                "pdf_inspect_2d",
                "doc_index_file",
                "doc_search",
                "doc_cross_audit",
                "compose_specialty_profile",
                "planificacion_pdm",
                "export_planning",
                "export_4d_excel",
            }.issubset(tool_names)

            resources_res = await session.list_resources()
            assert len(resources_res.resources) >= 3

            prompts_res = await session.list_prompts()
            prompt_names = {p.name for p in prompts_res.prompts}
            assert {"auditar-bim", "extraer-mediciones", "planificar-4d", "cruzar-memoria-planos"} <= prompt_names

            # 1. Ingesta IFC vía MCP
            bim_call = await session.call_tool("bim_index_ifc", {"file_path": ifc_path, "project_id": "cap01"})
            bim_data = json.loads(bim_call.content[0].text)
            assert bim_data["elements_indexed"] == 2
            bim_id = bim_data["artifact_id"]

            # 2. Trazado topológico NetworkX vía MCP
            trace_call = await session.call_tool("bim_trace_system", {"artifact_id": bim_id, "start_global_id": pump_gid})
            trace_data = json.loads(trace_call.content[0].text)
            assert trace_data["found"] is True
            assert len(trace_data["nodes"]) >= 2
            traced_classes = {n.get("ifc_class") for n in trace_data["nodes"]}
            assert {"IfcPump", "IfcPipeSegment"}.issubset(traced_classes)

            # 3. Parseo DXF vía MCP
            dxf_call = await session.call_tool("cad_parse_dxf", {"file_path": dxf_path})
            dxf_data = json.loads(dxf_call.content[0].text)
            assert dxf_data["por_capa"]["IMPULSION_DN400"]["longitud_m"] == 25.0

            # 4. Indexación de Memoria PDF + Auditoría Cruzada vía MCP
            doc_call = await session.call_tool("doc_index_file", {"file_path": pdf_path, "project_id": "cap01"})
            doc_data = json.loads(doc_call.content[0].text)
            doc_id = doc_data["artifact_id"]

            audit_call = await session.call_tool("doc_cross_audit", {"doc_artifact_id": doc_id, "bim_artifact_id": bim_id})
            audit_data = json.loads(audit_call.content[0].text)
            assert audit_data["findings_count"] >= 1
            assert audit_data["findings"][0]["category"] == "DIAMETRO_TUBERIA"

            # 5. Planificación 4D + Excel Curva S vía MCP
            xlsx_out = str(tmp_path / "captacion_4d.xlsx")
            plan_call = await session.call_tool(
                "export_4d_excel",
                {
                    "project_name": "Captación Río",
                    "output_path": xlsx_out,
                    "activities": [
                        {"id": "A1", "name": "Obra civil captación", "duration": 6},
                        {"id": "A2", "name": "Montaje bombeo e impulsión", "duration": 4},
                    ],
                    "dependencies": [{"pred": "A1", "succ": "A2", "type": "FS", "lag": 0}],
                    "wbs_bim_map": {"A2": [pump_gid]},
                },
            )
            plan_data = json.loads(plan_call.content[0].text)
            assert plan_data["project_duration"] == 10.0
            assert os.path.exists(plan_data["output_path"])
