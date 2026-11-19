"""Pruebas de Fase 3: Motor Documental RAG Jerárquico (FTS5 + similitud local) y Auditoría Cruzada."""
import pymupdf
import ifcopenshell
import ifcopenshell.api

from src.engines.bim_graph.graph_store import BimGraphStore
from src.engines.docs_rag.cross_audit import run_cross_audit
from src.engines.docs_rag.doc_index import DocIndex


def _create_spec_pdf(path: str) -> None:
    doc = pymupdf.open()
    p1 = doc.new_page()
    p1.insert_text(
        (50, 60),
        "1. CONDUCCIONES E IMPULSION\n"
        "La tubería principal de impulsión será de fundición dúctil DN500 PN16 según UNE-EN 545.\n"
        "Los depósitos y cántaras de bombeo se ejecutarán con hormigón armado HA-30.\n",
    )
    p2 = doc.new_page()
    p2.insert_text(
        (50, 60),
        "2. ESTACIONES DE BOMBEO Y VALVULERIA\n"
        "Las válvulas de compuerta serán bridadas PN16 con accionamiento eléctrico.\n",
    )
    doc.save(path)
    doc.close()


def _create_bim_mismatch(path: str) -> None:
    model = ifcopenshell.file(schema="IFC4")
    proj = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcProject", name="EDAR")
    ifcopenshell.api.run("unit.assign_unit", model)
    site = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcSite", name="Site")
    bldg = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcBuilding", name="Bldg")
    storey = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcBuildingStorey", name="Nivel 0")
    ifcopenshell.api.run("aggregate.assign_object", model, relating_object=proj, products=[site])
    ifcopenshell.api.run("aggregate.assign_object", model, relating_object=site, products=[bldg])
    ifcopenshell.api.run("aggregate.assign_object", model, relating_object=bldg, products=[storey])

    pipe = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcPipeSegment", name="Impulsión Tramo 1")
    wall = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcWall", name="Muro Cántara")
    ifcopenshell.api.run("spatial.assign_container", model, relating_structure=storey, products=[pipe, wall])

    pset = ifcopenshell.api.run("pset.add_pset", model, product=pipe, name="Pset_PipeSegmentTypeCommon")
    ifcopenshell.api.run("pset.edit_pset", model, pset=pset, properties={"NominalDiameter": 300.0})
    model.write(path)


def test_doc_rag_hierarchical_and_cross_audit(tmp_path):
    pdf_path = str(tmp_path / "pliego_edar.pdf")
    _create_spec_pdf(pdf_path)

    doc_idx = DocIndex(db_path=str(tmp_path / "doc_index.db"))
    res = doc_idx.index_file_path(pdf_path, "doc:pliego1")
    assert res["pages"] == 2
    assert res["chunks_indexed"] >= 2

    toc = doc_idx.get_toc("doc:pliego1")
    assert len(toc) >= 2

    hits = doc_idx.hybrid_search("DN500 fundición dúctil", artifact_id="doc:pliego1")
    assert len(hits) >= 1
    assert "DN500" in hits[0]["text"]
    assert hits[0]["page_number"] == 1

    ifc_path = str(tmp_path / "edar_bim.ifc")
    _create_bim_mismatch(ifc_path)
    bim_store = BimGraphStore(db_path=str(tmp_path / "bim.duckdb"))
    bim_store.index_ifc_file(ifc_path, "bim:edar1")

    audit = run_cross_audit("doc:pliego1", "bim:edar1", doc_index=doc_idx, bim_store=bim_store)
    assert audit["specs_summary"]["diameters_dn"] == [500.0]
    assert audit["specs_summary"]["concrete_grades"] == ["HA-30"]
    assert audit["findings_count"] >= 2
    categories = {f["category"] for f in audit["findings"]}
    assert "DIAMETRO_TUBERIA" in categories
    assert "MATERIAL_ESTRUCTURAL_AUSENTE" in categories
    for f in audit["findings"]:
        assert f["doc_ref"]["sha256"] == res["sha256"]
        assert f["doc_ref"]["page_number"] >= 1
