"""Skill (cerebro DOCUMENTAL) — Extracción de especificaciones técnicas.

Un presupuesto casi nunca contiene la información necesaria para juzgar si un precio es
razonable: eso vive en memorias valoradas, memorias técnicas, anexos, pliegos y mediciones.
Esta skill hace el puente: lee esa documentación y estructura, POR PARTIDA, el alcance, las
dimensiones, la cantidad y el material especificado, con su referencia de origen.

Es la primera de la cadena del cerebro de costes:
    documental_extraccion_specs -> costes_benchmark_precios -> costes_revision_anomalias

Los huecos son un HALLAZGO, no un error a ocultar: una partida sin respaldo documental se
marca `encontrado=False` en vez de rellenarse a ojo.
"""
import logging

from api.skills.base import register_skill, SkillResult
from api.skills.vision.util import image_from
from api.schemas.skills_costes import ExtraccionSpecsInputs, ExtraccionSpecsLLM
from api.structured import structured_call
from api.confidence import needs_review

logger = logging.getLogger("api.skills.documental.extraccion")

_PROMPT = (
    "Eres ingeniero de costes leyendo documentación técnica de un proyecto de obra civil "
    "(memoria valorada, memoria técnica, anexo, pliego o mediciones). Para cada partida que se "
    "te indique, localiza en el documento y extrae: el ALCANCE (qué incluye exactamente), las "
    "DIMENSIONES (longitudes, superficies, volúmenes, diámetros, espesores), la CANTIDAD y su "
    "UNIDAD, el MATERIAL o producto especificado (marca incluida si aparece) y las CONDICIONES "
    "técnicas relevantes (tipo de terreno, normativa, condiciones de instalación). Indica la "
    "referencia de origen (página o sección). "
    "Extrae también las CIFRAS BASE del proyecto que se usan de denominador (población servida "
    "frente a población total, caudal real frente a caudal de diseño, capacidad real frente a "
    "capacidad de diseño): distínguelas con claridad, porque usar la equivocada distorsiona "
    "todos los ratios. "
    "REGLA CRÍTICA: si una partida NO tiene respaldo en el documento, márcala con "
    "encontrado=false y deja sus campos vacíos. NUNCA inventes ni estimes un dato que no esté "
    "escrito: un hueco es un hallazgo, no un error a tapar."
)


@register_skill(
    "documental_extraccion_specs",
    cerebro="documental", familia="extraccion",
    descripcion="Extrae de memorias valoradas, memorias técnicas, anexos, pliegos y mediciones "
                "el alcance, dimensiones, cantidades y materiales de cada partida, y los "
                "estructura junto a ella con su referencia de origen. Marca explícitamente las "
                "partidas sin respaldo documental.",
    tipos_proyecto=["*"],
    capacidad="texto",
    inputs_model=ExtraccionSpecsInputs,
    outputs_model=ExtraccionSpecsLLM,
    version="1.0",
)
def _recuperar_contexto_masivo(texto: str, partidas_objetivo, artifact_id: str = "", sha256: str = "") -> str:
    """Elimina el truncado ciego a 20k chars: indexa en FTS5 y recupera fragmentos relevantes."""
    from api.domain.doc_index import DocIndex, chunk_document_pages
    from api.security.dlp import wrap_untrusted_text

    if not artifact_id and len(texto) <= 20000:
        return wrap_untrusted_text(texto, source_sha256=sha256)

    idx = DocIndex()
    art_key = artifact_id or f"inline:{sha256 or 'doc'}"
    if texto and not artifact_id:
        pages = [{"page": 1, "text": texto}]
        chunks = chunk_document_pages(pages, artifact_id=art_key, sha256=sha256 or "inline")
        idx.index_chunks(art_key, chunks)

    queries = list(partidas_objetivo or []) or ["especificaciones dimensiones cantidad material alcance"]
    seen = set()
    hits = []
    for q in queries[:12]:
        for h in idx.search(q, artifact_id=art_key, limit=4):
            if h["chunk_id"] not in seen:
                seen.add(h["chunk_id"])
                hits.append(f"[Pág. {h['page_number']} | {h['section_title']}]\n{h['text']}")

    if not hits and texto:
        return wrap_untrusted_text(texto[:20000], source_sha256=sha256)
    return wrap_untrusted_text("\n\n---\n\n".join(hits), source_sha256=sha256)


def extraccion_specs(params: ExtraccionSpecsInputs, *, db=None, ctx=None) -> SkillResult:
    fuente = {"tipo": "skill", "skill": "documental_extraccion_specs"}
    ctx = ctx or {}
    imagen = image_from(ctx, params.image_b64)
    texto = params.texto or (ctx.get("texto") if isinstance(ctx.get("texto"), str) else None)
    artifact_id = ctx.get("artifact_id") or ""
    sha256 = ctx.get("sha256") or ""

    if not texto and not artifact_id and imagen is None:
        return SkillResult(datos={"error": "No se recibió ni texto, ni artifact_id, ni imagen del documento"},
                           requiere_revision=True, fuente=fuente)

    prompt = _PROMPT
    if params.partidas_objetivo:
        prompt += "\n\nPartidas a buscar:\n" + "\n".join(f"- {p}" for p in params.partidas_objetivo)
    if params.documento:
        prompt += f"\n\nDocumento: {params.documento}"
    if texto or artifact_id:
        bloque_doc = _recuperar_contexto_masivo(
            texto or "",
            params.partidas_objetivo,
            artifact_id=artifact_id,
            sha256=sha256,
        )
        prompt += f"\n\n--- CONTENIDO DEL DOCUMENTO ---\n{bloque_doc}"

    data = structured_call(prompt, ExtraccionSpecsLLM,
                           images=[imagen] if imagen is not None else None, temperature=0.1)
    if data is None:
        return SkillResult(datos={"error": "Sin extracción estructurada del documento"},
                           requiere_revision=True, fuente=fuente)

    partidas = [p.model_dump() for p in data.partidas]
    sin_respaldo = [p["partida"] for p in partidas if not p.get("encontrado")]
    conf = float(data.confianza or 0)

    return SkillResult(
        datos={
            "partidas": partidas,
            "n_partidas": len(partidas),
            "sin_respaldo_documental": sin_respaldo,
            "bases_referencia": data.bases_referencia,
            "notas": data.notas,
            "documento": params.documento,
        },
        confianza=conf,
        # un hueco documental exige mirada humana: puede ser un problema de alcance
        requiere_revision=needs_review(conf) or bool(sin_respaldo) or not partidas,
        fuente={**fuente, "modelo": "gemini-2.5-flash", "exacto": False,
                "documento": params.documento},
    )
