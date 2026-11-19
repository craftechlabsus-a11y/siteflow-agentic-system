"""Skill (cerebro PLANIFICACIÓN) — Generación automática de cronograma de Obra Civil y Movimiento de Tierras."""
import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from src.core.skills_base import SkillResult, register_skill
from src.engines.planning_4d.cpm_engine import generar_planificacion_obra_civil

logger = logging.getLogger("src.engines.planning_4d.skills.obra_civil")


class PlanificacionObraCivilInputs(BaseModel):
    elementos_bim: List[Dict[str, Any]] = Field(default_factory=list)
    tipo_terreno: str = "tierra"
    plazo_objetivo_dias: Optional[float] = None
    frentes: int = 1


@register_skill(
    "planificacion_generar_cronograma_civil",
    cerebro="planificacion",
    familia="cronograma_civil",
    descripcion=(
        "Genera un cronograma PDM/CPM de ejecución para Obra Civil y Movimiento de Tierras "
        "desde elementos BIM, planos CAD o mediciones, deduciendo excavación/relleno por "
        "cotas zmin y auto-ajustando frentes al plazo del pliego."
    ),
    tipos_proyecto=["*"],
    capacidad="calculo",
    inputs_model=PlanificacionObraCivilInputs,
    version="2.6",
)
def generar_cronograma_civil(p: PlanificacionObraCivilInputs, *, db=None, ctx=None) -> SkillResult:
    plan = generar_planificacion_obra_civil(
        elements=p.elementos_bim,
        tipo_terreno=p.tipo_terreno,
        plazo_objetivo_dias=int(p.plazo_objetivo_dias) if p.plazo_objetivo_dias is not None else None,
        max_frentes=p.frentes,
    )
    return SkillResult(
        datos=plan,
        confianza=0.92,
        requiere_revision=False,
        fuente={"tipo": "skill", "skill": "planificacion_generar_cronograma_civil"},
    )
