"""Skill BIM — Takeoff geométrico 3D EXACTO y validación determinista desde archivos IFC."""
from collections import Counter
import logging
import os
import tempfile

from api.domain.bim_harness import validar_calidad_bim
from api.domain.bim_ingesta import extraer_elementos_ifc
from api.schemas.skills_bim import BimTakeoffInputs
from api.skills.base import SkillResult, register_skill

logger = logging.getLogger("api.skills.bim.takeoff")


@register_skill(
    "bim_takeoff_geometrico",
    cerebro="vision",
    familia="takeoff",
    descripcion=(
        "Takeoff 3D EXACTO desde un modelo BIM (IFC2x3/IFC4): extrae sólidos con OpenCascade C++, "
        "volúmenes netos, áreas, plantas y audita defectos deterministas (sin_qto, qto_miente, "
        "sin_material, proxy_ambiguo, duplicado)."
    ),
    tipos_proyecto=["*"],
    capacidad="cad",
    inputs_model=BimTakeoffInputs,
    outputs_model=None,
    version="2.3.0",
)
def bim_takeoff(params: BimTakeoffInputs, *, db=None, ctx=None) -> SkillResult:
    ctx = ctx or {}
    file_bytes = ctx.get("file_bytes")
    filename = params.filename or ctx.get("filename") or "modelo.ifc"
    if not file_bytes:
        return SkillResult(
            datos={"error": "No se recibió archivo BIM (.ifc)"},
            requiere_revision=True,
            fuente={"tipo": "skill", "skill": "bim_takeoff_geometrico"},
        )

    suffix = os.path.splitext(filename)[1].lower() or ".ifc"
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        ingesta = extraer_elementos_ifc(tmp_path)
        elements = validar_calidad_bim(ingesta["elements"], qto_tol=params.qto_tolerance)
        defect_counts = dict(Counter(f for e in elements for f in e.get("flags", [])))
        por_clase = dict(Counter(e["ifc_class"] for e in elements))
        por_planta = dict(Counter(e.get("storey") or "Sin Planta" for e in elements))

        vol_total = sum(
            e["vol"]
            for e in elements
            if params.incluir_duplicados or "duplicado" not in e.get("flags", [])
        )
        requiere_rev = any(
            k in defect_counts for k in ("qto_miente", "sin_material", "proxy_ambiguo", "duplicado")
        )
        confianza = 0.96 if not requiere_rev else 0.85

        return SkillResult(
            datos={
                "sha256": ingesta["sha256"],
                "schema": ingesta["schema"],
                "project_name": ingesta["project_name"],
                "unit_scale": ingesta["unit_scale"],
                "storeys": ingesta["storeys"],
                "total_elements": ingesta["total_elements"],
                "volumen_total_m3": round(vol_total, 3),
                "por_clase_ifc": por_clase,
                "por_planta": por_planta,
                "conteo_defectos": defect_counts,
                "elements": elements,
            },
            confianza=confianza,
            requiere_revision=requiere_rev,
            fuente={
                "tipo": "bim_geometria_3d",
                "skill": "bim_takeoff_geometrico",
                "exacto": True,
                "sha256": ingesta["sha256"],
                "formato": suffix.lstrip("."),
            },
        )
    except Exception as e:
        logger.warning(f"bim_takeoff falló: {e}")
        return SkillResult(
            datos={"error": f"No se pudo procesar el modelo IFC: {e}"},
            requiere_revision=True,
            fuente={"tipo": "skill", "skill": "bim_takeoff_geometrico"},
        )
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
