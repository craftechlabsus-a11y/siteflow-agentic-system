"""Auditoría Cruzada Determinista: Memoria/Pliego (DocIndex) vs Modelo BIM (BimGraphStore)."""
import re
from typing import Any, Dict, List, Optional

from src.engines.bim_graph.graph_store import BimGraphStore
from src.engines.docs_rag.doc_index import DocIndex

_DN_RE = re.compile(r"\bDN\s*[-:]?\s*(\d{2,4})\b", re.IGNORECASE)
_PN_RE = re.compile(r"\bPN\s*[-:]?\s*(\d{1,3})\b", re.IGNORECASE)
_HA_RE = re.compile(r"\b(H[AM]-\d{2})\b", re.IGNORECASE)
_NORM_RE = re.compile(r"\b((?:UNE|EN|ISO|CTE)[-\s][A-Z0-9\-\.]{2,20})\b")


def extract_doc_specifications(
    doc_artifact_id: str,
    *,
    doc_index: Optional[DocIndex] = None,
) -> Dict[str, Any]:
    """Extrae especificaciones técnicas deterministas (DN, PN, HA/HM, normas) con página y SHA-256."""
    idx = doc_index or DocIndex()
    with idx._connect() as conn:
        rows = conn.execute(
            """
            SELECT chunk_id, sha256, page_number, section_title, text
            FROM doc_chunks
            WHERE artifact_id = ?
            ORDER BY page_number ASC
            """,
            (doc_artifact_id,),
        ).fetchall()

    diameters: List[Dict[str, Any]] = []
    pressures: List[Dict[str, Any]] = []
    concretes: List[Dict[str, Any]] = []
    standards: List[Dict[str, Any]] = []

    for r in rows:
        txt = r["text"]
        ref = {
            "artifact_id": doc_artifact_id,
            "sha256": r["sha256"],
            "page_number": r["page_number"],
            "section_title": r["section_title"],
            "chunk_id": r["chunk_id"],
        }
        for m in _DN_RE.finditer(txt):
            diameters.append({"dn_mm": float(m.group(1)), **ref})
        for m in _PN_RE.finditer(txt):
            pressures.append({"pn_bar": float(m.group(1)), **ref})
        for m in _HA_RE.finditer(txt):
            concretes.append({"grade": m.group(1).upper(), **ref})
        for m in _NORM_RE.finditer(txt):
            standards.append({"standard": m.group(1).strip(), **ref})

    return {
        "doc_artifact_id": doc_artifact_id,
        "chunks_scanned": len(rows),
        "diameters_dn": diameters,
        "pressures_pn": pressures,
        "concrete_grades": concretes,
        "standards": standards,
    }


def run_cross_audit(
    doc_artifact_id: str,
    bim_artifact_id: str,
    *,
    doc_index: Optional[DocIndex] = None,
    bim_store: Optional[BimGraphStore] = None,
) -> Dict[str, Any]:
    """Cruza especificaciones de Memoria/Pliego contra elementos BIM y emite hallazgos trazables."""
    idx = doc_index or DocIndex()
    store = bim_store or BimGraphStore()

    specs = extract_doc_specifications(doc_artifact_id, doc_index=idx)
    safe_bim_id = bim_artifact_id.replace("'", "''")
    findings: List[Dict[str, Any]] = []
    f_seq = 0

    def add_finding(severity: str, category: str, message: str, doc_ref: Dict[str, Any], bim_ref: Dict[str, Any]):
        nonlocal f_seq
        f_seq += 1
        findings.append(
            {
                "finding_id": f"AUD-{f_seq:03d}",
                "severity": severity,
                "category": category,
                "message": message,
                "doc_ref": doc_ref,
                "bim_ref": bim_ref,
            }
        )

    # 1. Cruce de diámetros DN en tuberías/válvulas
    doc_dns = {d["dn_mm"]: d for d in specs["diameters_dn"]}
    if doc_dns:
        q_pipes = store.query_sql(
            f"""
            SELECT e.global_id, e.ifc_class, e.name, p.prop_name, p.prop_value
            FROM elements e
            LEFT JOIN properties p
              ON e.artifact_id = p.artifact_id AND e.global_id = p.global_id
             AND LOWER(p.prop_name) LIKE '%diameter%'
            WHERE e.artifact_id = '{safe_bim_id}'
              AND e.ifc_class IN ('IfcPipeSegment', 'IfcPipeFitting', 'IfcValve', 'IfcFlowSegment')
            """,
            limit=500,
        )
        for row in q_pipes["rows"]:
            val_raw = row.get("prop_value")
            if val_raw is None:
                continue
            try:
                bim_dn = float(val_raw)
            except (TypeError, ValueError):
                continue
            if bim_dn not in doc_dns:
                sample_doc = next(iter(doc_dns.values()))
                add_finding(
                    "HIGH",
                    "DIAMETRO_TUBERIA",
                    f"Elemento BIM '{row['name']}' ({row['ifc_class']}) tiene diámetro {bim_dn} mm, "
                    f"no coincidente con los DN especificados en el pliego ({sorted(doc_dns.keys())}).",
                    sample_doc,
                    {
                        "artifact_id": bim_artifact_id,
                        "global_id": row["global_id"],
                        "ifc_class": row["ifc_class"],
                        "name": row["name"],
                        "bim_diameter_mm": bim_dn,
                    },
                )

    # 2. Cruce de calidades de hormigón (HA/HM) en elementos estructurales
    doc_grades = {c["grade"]: c for c in specs["concrete_grades"]}
    if doc_grades:
        q_struct = store.query_sql(
            f"""
            SELECT global_id, ifc_class, name, material
            FROM elements
            WHERE artifact_id = '{safe_bim_id}'
              AND ifc_class IN ('IfcWall', 'IfcSlab', 'IfcFooting', 'IfcColumn', 'IfcBeam', 'IfcPile')
            """,
            limit=500,
        )
        for row in q_struct["rows"]:
            mat = (row.get("material") or "").upper()
            if not mat:
                sample_doc = next(iter(doc_grades.values()))
                add_finding(
                    "MEDIUM",
                    "MATERIAL_ESTRUCTURAL_AUSENTE",
                    f"Elemento estructural '{row['name']}' ({row['ifc_class']}) sin material asignado en BIM; "
                    f"el pliego exige {sorted(doc_grades.keys())}.",
                    sample_doc,
                    {
                        "artifact_id": bim_artifact_id,
                        "global_id": row["global_id"],
                        "ifc_class": row["ifc_class"],
                        "name": row["name"],
                    },
                )
            elif any(g in mat for g in ("HA-", "HM-")) and not any(g in mat for g in doc_grades):
                sample_doc = next(iter(doc_grades.values()))
                add_finding(
                    "HIGH",
                    "CALIDAD_HORMIGON",
                    f"Elemento '{row['name']}' usa '{row['material']}' en BIM, distinto a {sorted(doc_grades.keys())} del pliego.",
                    sample_doc,
                    {
                        "artifact_id": bim_artifact_id,
                        "global_id": row["global_id"],
                        "ifc_class": row["ifc_class"],
                        "name": row["name"],
                        "material": row["material"],
                    },
                )

    return {
        "doc_artifact_id": doc_artifact_id,
        "bim_artifact_id": bim_artifact_id,
        "specs_summary": {
            "diameters_dn": sorted(doc_dns.keys()),
            "pressures_pn": sorted({p["pn_bar"] for p in specs["pressures_pn"]}),
            "concrete_grades": sorted(doc_grades.keys()),
            "standards_count": len(specs["standards"]),
        },
        "findings_count": len(findings),
        "findings": findings,
    }
