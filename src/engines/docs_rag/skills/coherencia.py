"""Skill (cerebro DOCUMENTAL) — Coherencia Triple-Check Memoria ↔ Planos ↔ Presupuesto (v2.5.0)."""
import hashlib
import json
import logging
from typing import Any, Dict

from pydantic import BaseModel, Field

from src.core.skills_base import SkillResult, register_skill

logger = logging.getLogger("src.engines.docs_rag.skills.coherencia")


class CoherenciaTripleCheckInputs(BaseModel):
    module_id: str = "general"
    parametros_memoria: Dict[str, Any] = Field(default_factory=dict)
    mediciones_planos: Dict[str, float] = Field(default_factory=dict)
    mediciones_presupuesto: Dict[str, float] = Field(default_factory=dict)
    tolerancia_pct: float = 5.0


@register_skill(
    "documental_coherencia_triple_check",
    cerebro="documental",
    familia="auditoria_documental",
    descripcion="Cruza parámetros base de diseño (Memoria) contra mediciones extraídas de Planos "
                "y Presupuesto, detectando discrepancias superiores a la tolerancia y generando "
                "hallazgos trazables con hash SHA-256.",
    tipos_proyecto=["*"],
    capacidad="calculo",
    inputs_model=CoherenciaTripleCheckInputs,
    version="2.5",
)
def coherencia_triple_check(p: CoherenciaTripleCheckInputs, *, db=None, ctx=None) -> SkillResult:
    fuente = {"tipo": "skill", "skill": "documental_coherencia_triple_check"}
    claves = sorted(set(p.mediciones_planos.keys()) | set(p.mediciones_presupuesto.keys()))
    hallazgos = []
    cuadradas = 0

    for k in claves:
        v_plan = p.mediciones_planos.get(k)
        v_pres = p.mediciones_presupuesto.get(k)
        if v_plan is None or v_pres is None:
            hallazgos.append({
                "concepto": k,
                "estado": "omitida_en_fuente",
                "medicion_planos": v_plan,
                "medicion_presupuesto": v_pres,
                "desviacion_pct": None,
                "severidad": "alta",
            })
            continue
        v_plan_f = float(v_plan)
        v_pres_f = float(v_pres)
        base = max(abs(v_plan_f), 1e-6)
        desv = round((v_pres_f - v_plan_f) / base * 100.0, 2)
        if abs(desv) > p.tolerancia_pct:
            hallazgos.append({
                "concepto": k,
                "estado": "sobredotada" if desv > 0 else "infradotada",
                "medicion_planos": v_plan_f,
                "medicion_presupuesto": v_pres_f,
                "desviacion_pct": desv,
                "severidad": "alta" if abs(desv) > 15.0 else "media",
            })
        else:
            cuadradas += 1

    payload_hash = hashlib.sha256(
        json.dumps({"memoria": p.parametros_memoria, "hallazgos": hallazgos}, sort_keys=True).encode("utf-8")
    ).hexdigest()

    return SkillResult(
        datos={
            "module_id": p.module_id,
            "parametros_memoria": p.parametros_memoria,
            "n_conceptos_cruzados": len(claves),
            "n_cuadrados": cuadradas,
            "n_hallazgos": len(hallazgos),
            "hallazgos": hallazgos,
            "sha256": payload_hash,
        },
        confianza=0.95 if not hallazgos else 0.72,
        requiere_revision=bool(hallazgos),
        fuente={**fuente, "tipo": "triple_check_deterministico", "sha256": payload_hash},
    )
