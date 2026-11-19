"""Skill BIM — Presupuesto Híbrido (Harness Determinista + Cerebros ML + RAG Estructurado)."""
import logging
import os
import tempfile

from api.domain.bim_harness import (
    derivar_partidas_implicitas_bim,
    ejecutar_harness_bim,
    extraer_defectos_iso19650,
    validar_calidad_bim,
)
from api.domain.bim_ingesta import extraer_elementos_ifc
from api.schemas.skills_bim import BimPresupuestoInputs
from api.skills.base import SkillResult, register_skill

logger = logging.getLogger("api.skills.bim.presupuesto")


@register_skill(
    "bim_presupuesto_hibrido",
    cerebro="costes",
    familia="presupuesto",
    descripcion=(
        "Genera el presupuesto verificado (PEM) y las líneas analíticas de medición desde un "
        "modelo BIM (.ifc) combinando el Harness Determinista 3D, los 3 Cerebros ML "
        "(TipologiaBrain, NameBrain, OutlierBrain) y el motor RAG Híbrido Estructurado."
    ),
    tipos_proyecto=["*"],
    capacidad="calculo",
    inputs_model=BimPresupuestoInputs,
    outputs_model=None,
    depende_de=["bim_takeoff_geometrico"],
    version="2.3.0",
)
def bim_presupuesto(params: BimPresupuestoInputs, *, db=None, ctx=None) -> SkillResult:
    ctx = ctx or {}
    file_bytes = ctx.get("file_bytes")
    filename = params.filename or ctx.get("filename") or "modelo.ifc"
    if not file_bytes:
        return SkillResult(
            datos={"error": "No se recibió archivo BIM (.ifc)"},
            requiere_revision=True,
            fuente={"tipo": "skill", "skill": "bim_presupuesto_hibrido"},
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

        total_el = max(1, len(elements))
        auto_n = harness["conteo_rutas"].get("auto", 0)
        conf_media = round(auto_n / total_el, 3)
        requiere_rev = harness["escalados_a_revision"] > 0 or len(harness["outliers_detectados"]) > 0

        return SkillResult(
            datos={
                "sha256": ingesta["sha256"],
                "schema": ingesta["schema"],
                "project_name": ingesta["project_name"],
                "total_elements": ingesta["total_elements"],
                "pem_auto_eur": harness["pem_auto_eur"],
                "pem_auto_mas_revision_eur": harness["pem_auto_mas_revision_eur"],
                "conteo_rutas": harness["conteo_rutas"],
                "conteo_defectos": harness["conteo_defectos"],
                "escalados_a_revision": harness["escalados_a_revision"],
                "outliers_detectados": harness["outliers_detectados"],
                "consultas_rag_unicas": harness["consultas_rag_unicas"],
                "resumen_partidas": harness["resumen_partidas"],
                "mediciones_analiticas": harness["mediciones_analiticas"],
                "pem_por_disciplina": harness.get("pem_por_disciplina", {}),
                "elementos_por_disciplina": harness.get("elementos_por_disciplina", {}),
                "resumen_disciplinas": harness.get("resumen_disciplinas", []),
                "partidas_implicitas": derivar_partidas_implicitas_bim(elements, harness),
                "defectos_iso19650": extraer_defectos_iso19650(harness),
            },
            confianza=max(0.60, conf_media),
            requiere_revision=requiere_rev,
            fuente={
                "tipo": "bim_harness_rag_ml",
                "skill": "bim_presupuesto_hibrido",
                "sha256": ingesta["sha256"],
                "pais": params.pais,
            },
        )
    except Exception as e:
        logger.warning(f"bim_presupuesto falló: {e}")
        return SkillResult(
            datos={"error": f"Error calculando presupuesto BIM: {e}"},
            requiere_revision=True,
            fuente={"tipo": "skill", "skill": "bim_presupuesto_hibrido"},
        )
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
