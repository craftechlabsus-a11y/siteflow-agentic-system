"""Skills Visión Hidráulica y de Proceso (perfiles longitudinales y P&ID EDAR/IDAM) vía LLMPool + QuotaSwitcher."""
import io
import logging
import os
from typing import Any, Dict, List, Optional

from PIL import Image
from pydantic import BaseModel, Field

from src.core.confidence import needs_review
from src.core.skills_base import SkillResult, register_skill
from src.core.structured import structured_call

logger = logging.getLogger("src.engines.cad_vision.skills.hidraulica")


class PerfilHidraulicoInputs(BaseModel):
    file_path: str = Field(..., description="Ruta a imagen (.png/.jpg) o plano .pdf del perfil longitudinal.")
    page_num: int = 0
    tramo: str = ""
    escala_h: str = ""
    escala_v: str = ""


class PuntoPerfil(BaseModel):
    pk: str = ""
    cota_terreno: Optional[float] = None
    cota_rasante: Optional[float] = None
    profundidad_zanja_m: Optional[float] = None


class PerfilHidraulicoLLM(BaseModel):
    puntos: List[PuntoPerfil] = Field(default_factory=list)
    pendiente_pct: Optional[float] = None
    diametro_seccion: str = ""
    observaciones: List[str] = Field(default_factory=list)
    confianza: float = 0.7


class PidEquiposInputs(BaseModel):
    file_path: str = Field(..., description="Ruta a imagen (.png/.jpg) o plano .pdf del diagrama P&ID.")
    page_num: int = 0
    proceso: str = ""


class EquipoPid(BaseModel):
    tag: str
    tipo: str = ""
    especificacion: str = ""


class LineaPid(BaseModel):
    tag_linea: str = ""
    dn: str = ""
    pn: str = ""
    fluido: str = ""


class PidEquiposLLM(BaseModel):
    equipos: List[EquipoPid] = Field(default_factory=list)
    lineas: List[LineaPid] = Field(default_factory=list)
    valvulas_instrumentos: List[str] = Field(default_factory=list)
    observaciones: List[str] = Field(default_factory=list)
    confianza: float = 0.7


def _load_image_from_path(file_path: str, page_num: int = 0) -> Image.Image:
    real_path = os.path.realpath(file_path)
    if not os.path.isfile(real_path):
        raise FileNotFoundError(f"Plano no encontrado: {file_path}")
    ext = os.path.splitext(real_path)[1].lower()
    if ext == ".pdf":
        import pymupdf

        doc = pymupdf.open(real_path)
        try:
            page = doc[page_num]
            pix = page.get_pixmap(dpi=180)
            return Image.open(io.BytesIO(pix.tobytes("png")))
        finally:
            doc.close()
    return Image.open(real_path)


_PROMPT_PERFIL = (
    "Eres ingeniero hidráulico leyendo un PERFIL LONGITUDINAL de canal o conducción de agua. "
    "Extrae de la guitarra del plano: los P.K., cota de terreno, cota de rasante/solera, "
    "profundidad de zanja o desmonte, pendiente (%) y diámetro/sección. Lista también las "
    "observaciones o singularidades (arquetas, sifones, aliviaderos, cruzamientos)."
)

_PROMPT_PID = (
    "Eres ingeniero de procesos leyendo un diagrama P&ID de planta EDAR, ETAP o Desaladora (IDAM). "
    "Extrae de forma estructurada todos los tags de equipos principales (bombas, soplantes, "
    "bastidores RO, filtros, dosificadores), líneas de proceso (DN, PN, fluido) y válvulas "
    "motorizadas/instrumentación (PIT, FIT, LIT, AIT)."
)


@register_skill(
    "vision_perfil_longitudinal_hidraulico",
    cerebro="vision",
    familia="hidraulica",
    descripcion=(
        "Extrae P.K., cotas de terreno y rasante, profundidades de zanja, pendiente y diámetros "
        "desde la guitarra de un perfil longitudinal hidráulico (canales y tuberías)."
    ),
    tipos_proyecto=["canales_tuberias", "depuradoras_edar", "captacion_bombeo"],
    capacidad="vision",
    inputs_model=PerfilHidraulicoInputs,
    version="2.6",
)
def perfil_longitudinal(params: PerfilHidraulicoInputs, *, db=None, ctx=None) -> SkillResult:
    img = _load_image_from_path(params.file_path, page_num=params.page_num)
    prompt = _PROMPT_PERFIL
    if params.tramo or params.escala_h or params.escala_v:
        prompt += f"\nContexto: tramo='{params.tramo}', escala_h='{params.escala_h}', escala_v='{params.escala_v}'."
    res = structured_call(prompt, PerfilHidraulicoLLM, images=[img])
    if res is None:
        return SkillResult(
            datos={"error": "No se pudo interpretar el perfil longitudinal"},
            confianza=0.0,
            requiere_revision=True,
            fuente={"tipo": "vision_hidraulica", "file_path": params.file_path},
        )
    datos = res.model_dump()
    return SkillResult(
        datos=datos,
        confianza=res.confianza,
        requiere_revision=needs_review(res.confianza),
        fuente={"tipo": "vision_hidraulica", "file_path": params.file_path},
    )


@register_skill(
    "vision_pid_equipos_proceso",
    cerebro="vision",
    familia="hidraulica",
    descripcion=(
        "Extrae inventario estructurado de equipos (tags, bombas, membranas RO), líneas (DN/PN) "
        "e instrumentación desde diagramas P&ID de plantas EDAR/ETAP y Desaladoras."
    ),
    tipos_proyecto=["depuradoras_edar", "desaladoras_idam", "captacion_bombeo"],
    capacidad="vision",
    inputs_model=PidEquiposInputs,
    version="2.6",
)
def pid_equipos(params: PidEquiposInputs, *, db=None, ctx=None) -> SkillResult:
    img = _load_image_from_path(params.file_path, page_num=params.page_num)
    prompt = _PROMPT_PID + (f"\nProceso/Área: {params.proceso}." if params.proceso else "")
    res = structured_call(prompt, PidEquiposLLM, images=[img])
    if res is None:
        return SkillResult(
            datos={"error": "No se pudo interpretar el diagrama P&ID"},
            confianza=0.0,
            requiere_revision=True,
            fuente={"tipo": "vision_pid", "file_path": params.file_path},
        )
    datos = res.model_dump()
    return SkillResult(
        datos=datos,
        confianza=res.confianza,
        requiere_revision=needs_review(res.confianza),
        fuente={"tipo": "vision_pid", "file_path": params.file_path},
    )
