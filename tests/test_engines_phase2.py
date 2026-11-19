"""Pruebas de Fase 2: BIM-Graph (DuckDB + NetworkX + IFC) y Planos 2D Híbrido (DXF + PyMuPDF + Tiling)."""
import os

import ezdxf
import pymupdf
import ifcopenshell
import ifcopenshell.api
import pytest

from src.engines.bim_graph.graph_store import BimGraphStore
from src.engines.bim_graph.ingesta import PRODUCT_CLASSES
from src.engines.cad_vision.parser import parse_dxf
from src.engines.cad_vision.pdf_pipeline import (
    classify_pdf,
    extract_vectorial_pdf,
    render_pdf_tiles,
)


def _create_sample_ifc(path: str) -> str:
    model = ifcopenshell.file(schema="IFC4")
    project = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcProject", name="EDAR")
    ifcopenshell.api.run("unit.assign_unit", model)
    site = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcSite", name="Parcela")
    building = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcBuilding", name="Estación Bombeo")
    storey = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcBuildingStorey", name="Cota -4.50")

    ifcopenshell.api.run("aggregate.assign_object", model, relating_object=project, products=[site])
    ifcopenshell.api.run("aggregate.assign_object", model, relating_object=site, products=[building])
    ifcopenshell.api.run("aggregate.assign_object", model, relating_object=building, products=[storey])

    pump = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcPump", name="Bomba B-01")
    pipe = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcPipeSegment", name="Impulsión DN300")
    ifcopenshell.api.run("spatial.assign_container", model, relating_structure=storey, products=[pump, pipe])

    pset = ifcopenshell.api.run("pset.add_pset", model, product=pipe, name="Pset_PipeSegmentTypeCommon")
    ifcopenshell.api.run("pset.edit_pset", model, pset=pset, properties={"NominalDiameter": 300.0})

    rel_conn = model.create_entity(
        "IfcRelConnectsElements",
        GlobalId=ifcopenshell.guid.new(),
        RelatingElement=pump,
        RelatedElement=pipe,
    )
    assert rel_conn is not None
    model.write(path)
    return pump.GlobalId


def test_bim_graph_ifc43_duckdb_and_networkx(tmp_path):
    assert "IfcBridge" in PRODUCT_CLASSES
    assert "IfcAlignment" in PRODUCT_CLASSES
    assert "IfcEarthworksCut" in PRODUCT_CLASSES

    ifc_path = str(tmp_path / "bombeo.ifc")
    pump_gid = _create_sample_ifc(ifc_path)

    store = BimGraphStore(db_path=str(tmp_path / "bim.duckdb"))
    res = store.index_ifc_file(ifc_path, "proj:bombeo")
    assert res["elements_indexed"] == 2
    assert res["properties_indexed"] >= 1
    assert res["relations_indexed"] >= 2

    q = store.query_sql("SELECT ifc_class, name, disciplina, storey FROM elements ORDER BY ifc_class")
    assert q["count"] == 2
    assert {r["ifc_class"] for r in q["rows"]} == {"IfcPipeSegment", "IfcPump"}

    with pytest.raises(ValueError):
        store.query_sql("DROP TABLE elements")

    trace = store.trace_system("proj:bombeo", pump_gid, max_hops=3)
    assert trace["found"] is True
    assert len(trace["nodes"]) >= 2


def test_cad_dxf_parser_with_attrib_mtext_and_dimension(tmp_path):
    dxf_path = str(tmp_path / "canal.dxf")
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 6  # metros
    msp = doc.modelspace()
    msp.add_line((0, 0), (10, 0), dxfattribs={"layer": "TUBERIA_DN400"})
    msp.add_lwpolyline([(0, 0), (4, 0), (4, 3), (0, 3)], close=True, dxfattribs={"layer": "DEPOSITO"})
    msp.add_mtext("CANAL CAPTACION", dxfattribs={"layer": "TEXTOS"})
    msp.add_linear_dim(base=(0, -1), p1=(0, 0), p2=(10, 0), dxfattribs={"layer": "COTAS"}).render()

    blk = doc.blocks.new("VALVULA_COMP")
    blk.add_attdef("TAG", (0, 0))
    ins = msp.add_blockref("VALVULA_COMP", (5, 0), dxfattribs={"layer": "VALVULAS"})
    ins.add_auto_attribs({"TAG": "V-101"})
    doc.saveas(dxf_path)

    parsed = parse_dxf(dxf_path)
    assert parsed["unidades"] == "m"
    assert parsed["por_capa"]["TUBERIA_DN400"]["longitud_m"] == 10.0
    assert parsed["por_capa"]["DEPOSITO"]["area_m2"] == 12.0
    assert any("CANAL CAPTACION" in t["texto"] for t in parsed["textos"])
    assert any(a["atributos"].get("TAG") == "V-101" for a in parsed["atributos_bloques"])
    assert len(parsed["cotas"]) >= 1


def test_pdf_hybrid_pipeline_vectorial_and_tiles(tmp_path):
    pdf_path = str(tmp_path / "plano_idar.pdf")
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=420)
    page.insert_text((50, 50), "PLANTA DESALADORA IDAM - TUBERIA IMPULSION DN500 PN16")
    for i in range(25):
        page.draw_line((40, 80 + i * 10), (500, 80 + i * 10))
    doc.save(pdf_path)
    doc.close()

    cls_res = classify_pdf(pdf_path)
    assert cls_res["classification"] == "vectorial"
    assert cls_res["total_pages"] == 1

    vec_res = extract_vectorial_pdf(pdf_path)
    assert vec_res["pages"][0]["vector_drawings"] >= 25
    assert any("IDAM" in b["text"] for b in vec_res["pages"][0]["text_blocks"])

    tiles_res = render_pdf_tiles(
        pdf_path,
        page_num=0,
        dpi=100,
        grid_cols=2,
        grid_rows=2,
        overlap_ratio=0.15,
        out_dir=str(tmp_path / "tiles"),
    )
    assert os.path.exists(tiles_res["overview_path"])
    assert len(tiles_res["tiles"]) == 4
    assert all(os.path.exists(t["tile_path"]) for t in tiles_res["tiles"])
