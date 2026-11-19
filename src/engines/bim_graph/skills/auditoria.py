"""Skill BIM — Auditoría Cruzada Pericial entre Modelo 3D (.ifc) y Presupuesto Oficial."""
import logging
import os
import tempfile

from api.domain.bim_harness import (
    auditar_modelo_bim_contra_pliego,
    ejecutar_harness_bim,
    validar_calidad_bim,
)
from api.domain.bim_ingesta import extraer_elementos_ifc
from api.schemas.skills_bim import BimAuditoriaInputs
from api.skills.base import SkillResult, register_skill

logger = logging.getLogger("api.skills.bim.auditoria")


@register_skill(
    "bim_auditoria_cruzada",
    cerebro="costes",
    familia="auditoria",
    descripcion=(
        "Audita un presupuesto oficial de licitación contra la realidad geométrica 3D de un "
        "modelo BIM (.ifc) tras filtrar duplicados y outliers, clasificando cada partida como "
        "cuadrada, sobredotada, infradotada u omitida_en_pliego."
    ),
    tipos_proyecto=["*"],
    capacidad="calculo",
    inputs_model=BimAuditoriaInputs,
    outputs_model=None,
    depende_de=["bim_presupuesto_hibrido"],
    version="2.3.0",
)
def bim_auditoria(params: BimAuditoriaInputs, *, db=None, ctx=None) -> SkillResult:
    ctx = ctx or {}
    file_bytes = ctx.get("file_bytes")
    filename = params.filename or ctx.get("filename") or "modelo.ifc"
    if not file_bytes:
        return SkillResult(
            datos={"error": "No se recibió archivo BIM (.ifc)"},
            requiere_revision=True,
            fuente={"tipo": "skill", "skill": "bim_auditoria_cruzada"},
        )

    suffix = os.path.splitext(filename)[1].lower() or ".ifc"
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        ingesta = extraer_elementos_ifc(tmp_path)
        elements = validar_calidad_bim(ingesta["elements"], qto_tol=params.qto_tolerance)
        harness = ejecutar_harness_bim(
            elements,
            use_ml=params.use_ml_brains,
            use_rerank=params.use_structured_rerank,
            auto_threshold=params.auto_threshold,
            review_threshold=params.review_threshold,
            incluir_duplicados=params.incluir_duplicados,
            incluir_outliers=params.incluir_outliers,
        )
        partidas_dict = [p.model_dump() for p in params.partidas_oficiales]
        auditoria = auditar_modelo_bim_contra_pliego(
            harness,
            partidas_dict,
            umbral_pct=params.umbral_desviacion_pct,
            usar_auto_mas_revision=params.incluir_revision_en_auditoria,
        )

        hay_desviaciones = any(
            k != "cuadrada" and v > 0 for k, v in auditoria["conteo_clasificacion"].items()
        )
        return SkillResult(
            datos={
                "sha256": ingesta["sha256"],
                "project_name": ingesta["project_name"],
                "total_elements": ingesta["total_elements"],
                "pem_bim_verificado_eur": harness["pem_auto_mas_revision_eur"],
                "conteo_defectos_ifc": harness["conteo_defectos"],
                "auditoria": auditoria,
            },
            confianza=0.95,
            requiere_revision=hay_desviaciones,
            fuente={
                "tipo": "bim_auditoria_cruzada",
                "skill": "bim_auditoria_cruzada",
                "sha256": ingesta["sha256"],
            },
        )
    except Exception as e:
        logger.warning(f"bim_auditoria falló: {e}")
        return SkillResult(
            datos={"error": f"Error en auditoría BIM: {e}"},
            requiere_revision=True,
            fuente={"tipo": "skill", "skill": "bim_auditoria_cruzada"},
        )
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
