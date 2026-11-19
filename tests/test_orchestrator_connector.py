"""Pruebas del Conector Orquestador (siteflow-orquestador / project_orchestrate_filter)."""
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


def _make_project_files(tmp_path):
    ifc_path = str(tmp_path / "edar_modelo.ifc")
    m = ifcopenshell.file(schema="IFC4")
    proj = ifcopenshell.api.run("root.create_entity", m, ifc_class="IfcProject", name="EDAR")
    ifcopenshell.api.run("unit.assign_unit", m)
    site = ifcopenshell.api.run("root.create_entity", m, ifc_class="IfcSite", name="Site")
    bldg = ifcopenshell.api.run("root.create_entity", m, ifc_class="IfcBuilding", name="Bldg")
    storey = ifcopenshell.api.run("root.create_entity", m, ifc_class="IfcBuildingStorey", name="Nivel 0")
    ifcopenshell.api.run("aggregate.assign_object", m, relating_object=proj, products=[site])
    ifcopenshell.api.run("aggregate.assign_object", m, relating_object=site, products=[bldg])
    ifcopenshell.api.run("aggregate.assign_object", m, relating_object=bldg, products=[storey])

    pump = ifcopenshell.api.run("root.create_entity", m, ifc_class="IfcPump", name="Bomba B-01")
    pipe = ifcopenshell.api.run("root.create_entity", m, ifc_class="IfcPipeSegment", name="Colector DN500")
    wall = ifcopenshell.api.run("root.create_entity", m, ifc_class="IfcWall", name="Muro Reactor")
    ifcopenshell.api.run("spatial.assign_container", m, relating_structure=storey, products=[pump, pipe, wall])
    m.write(ifc_path)

    dxf_path = str(tmp_path / "red_tuberias.dxf")
    dxf = ezdxf.new("R2010")
    dxf.header["$INSUNITS"] = 6
    dxf.modelspace().add_line((0, 0), (42, 0), dxfattribs={"layer": "COLECTOR_DN500"})
    dxf.saveas(dxf_path)

    pdf_path = str(tmp_path / "pliego_edar.pdf")
    pdf = pymupdf.open()
    p = pdf.new_page()
    p.insert_text((40, 60), "1. COLECTORES\nTubería de impulsión DN500 PN16 en EDAR.\n")
    pdf.save(pdf_path)
    pdf.close()

    return [ifc_path, dxf_path, pdf_path]


@pytest.mark.asyncio
async def test_mcp_project_orchestrate_filter_end_to_end(tmp_path):
    files = _make_project_files(tmp_path)
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

            call = await session.call_tool(
                "project_orchestrate_filter",
                {
                    "project_id": "edar_test",
                    "typology": "edar_idam",
                    "file_paths": files,
                    "filters": {
                        "ifc_class": "IfcPipeSegment",
                        "doc_query": "DN500 PN16",
                        "limit": 10,
                    },
                },
            )
            data = json.loads(call.content[0].text)

            assert data["project_id"] == "edar_test"
            assert data["specialty_profile"]["typology"] == "edar_idam"
            assert len(data["artifacts_processed"]) == 3

            # Solo debe devolver elementos filtrados por IfcPipeSegment (1 de 3)
            assert len(data["bim_filtered"]["elements"]) == 1
            assert data["bim_filtered"]["elements"][0]["ifc_class"] == "IfcPipeSegment"

            # Resumen DXF y búsqueda documental filtrada
            dxf_art = next(a for a in data["artifacts_processed"] if a["file_type"] == "dxf")
            assert dxf_art["dxf_summary"]["por_capa"]["COLECTOR_DN500"]["longitud_m"] == 42.0
            assert len(data["doc_hits"]) >= 1
            assert "DN500" in data["doc_hits"][0]["text"]
            assert len(data["doc_tocs"]) == 1
            assert sum(data["graph_summary"]["nodes_by_type"].values()) == 3
