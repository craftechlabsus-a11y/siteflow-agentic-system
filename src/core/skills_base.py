"""Framework de Skills (capacidades especializadas por cerebro).

Una Skill compone tools + structured_call + conocimiento de dominio. Devuelve siempre
un SkillResult estandarizado (datos + confianza + requiere_revision + fuente + recortes),
heredando la robustez de la Fase 2. El registro permite descubrirlas por cerebro y tipo
de proyecto (y exponerlas por API/MCP).
"""
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Type

from pydantic import BaseModel, Field


class SkillResult(BaseModel):
    """Salida estándar de toda skill."""
    datos: Dict[str, Any] = Field(default_factory=dict)
    confianza: float = 0.0
    requiere_revision: bool = True
    fuente: Dict[str, Any] = Field(default_factory=dict)
    recortes: List[Dict[str, Any]] = Field(default_factory=list)


@dataclass
class SkillSpec:
    name: str
    cerebro: str            # vision | costes | planificacion | contratos | documental | campo | coordinador
    familia: str            # p.ej. medicion | takeoff
    descripcion: str
    tipos_proyecto: List[str]   # edificacion, nave_industrial, estructura_hidraulica, ... o ["*"]
    capacidad: str          # vision | texto | cad | calculo
    inputs_model: Type[BaseModel]
    outputs_model: Optional[Type[BaseModel]]
    depende_de: List[str]
    version: str
    func: Callable[..., Any]


_REGISTRY: Dict[str, SkillSpec] = {}


def register_skill(name: str, *, cerebro: str, familia: str, descripcion: str,
                   tipos_proyecto: List[str], capacidad: str, inputs_model: Type[BaseModel],
                   outputs_model: Optional[Type[BaseModel]] = None,
                   depende_de: Optional[List[str]] = None, version: str = "1.0"):
    def deco(fn):
        _REGISTRY[name] = SkillSpec(name, cerebro, familia, descripcion, list(tipos_proyecto),
                                    capacidad, inputs_model, outputs_model,
                                    list(depende_de or []), version, fn)
        return fn
    return deco


def get_skills(cerebro: Optional[str] = None, tipo_proyecto: Optional[str] = None) -> List[SkillSpec]:
    out = []
    for s in _REGISTRY.values():
        if cerebro and s.cerebro != cerebro:
            continue
        if tipo_proyecto and tipo_proyecto not in s.tipos_proyecto and "*" not in s.tipos_proyecto:
            continue
        out.append(s)
    return out


def get_skill_schemas(cerebro: Optional[str] = None, tipo_proyecto: Optional[str] = None) -> List[Dict[str, Any]]:
    """Esquemas de skills (para API/MCP y para que el agente las descubra)."""
    return [{
        "name": s.name, "cerebro": s.cerebro, "familia": s.familia,
        "descripcion": s.descripcion, "tipos_proyecto": s.tipos_proyecto,
        "capacidad": s.capacidad, "depende_de": s.depende_de, "version": s.version,
        "inputs": s.inputs_model.model_json_schema(),
    } for s in get_skills(cerebro, tipo_proyecto)]


def run_skill(name: str, inputs: Dict[str, Any], *, db=None, ctx: Optional[Dict[str, Any]] = None) -> SkillResult:
    """Valida los inputs contra el esquema y ejecuta la skill. Nunca devuelve algo no-SkillResult."""
    spec = _REGISTRY.get(name)
    if not spec:
        raise ValueError(f"Skill desconocida: {name}")
    params = spec.inputs_model.model_validate(inputs or {})
    result = spec.func(params, db=db, ctx=ctx or {})
    if isinstance(result, SkillResult):
        return result
    if isinstance(result, dict):
        return SkillResult(**result)
    return SkillResult(datos={"valor": result})
