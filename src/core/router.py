"""Enrutador de skills: selecciona las aplicables a un cerebro y tipo de proyecto.

Para proyectos COMPUESTOS (p. ej. plantas de agua = edificación + nave + estructura
hidráulica), se selecciona por zona/tipología. Ordena por dependencias (las que no
dependen de otras, primero — p. ej. 'calibración' antes que 'medición').
"""
from typing import List, Optional
from src.core.skills_base import get_skills, SkillSpec


def select_skills(cerebro: Optional[str] = None, tipo_proyecto: Optional[str] = None,
                  intencion: Optional[str] = None) -> List[SkillSpec]:
    skills = get_skills(cerebro, tipo_proyecto)
    # dependencias primero (menos depende_de => antes)
    skills.sort(key=lambda s: len(s.depende_de))
    return skills


def select_for_zonas(cerebro: str, zonas_tipologias: List[str]) -> List[SkillSpec]:
    """Une las skills aplicables a las tipologías presentes en un proyecto compuesto."""
    vistos = {}
    for tipo in zonas_tipologias or ["*"]:
        for s in select_skills(cerebro, tipo):
            vistos[s.name] = s
    return sorted(vistos.values(), key=lambda s: len(s.depende_de))
