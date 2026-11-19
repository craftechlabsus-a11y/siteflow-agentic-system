"""Llamadas LLM con salida estructurada y validada (Fase 2, Workstream A).

Enrutado por tipo de tarea (decisión del proyecto):
- Información GRÁFICA (con imágenes) -> Gemini con response_schema (JSON garantizado).
- Información de TEXTO/descriptiva     -> pool (Groq) en modo JSON + validación + reintento.

Devuelve SIEMPRE una instancia validada del esquema o None (nunca revienta ni
devuelve ceros mudos). El llamador decide qué hacer con None (marcar requiere_revision).
"""
import re
import json
import logging
from typing import Optional, Type, List, Any

from pydantic import BaseModel, ValidationError

logger = logging.getLogger("api.structured")


def _strip_fences(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    return text


def _schema_hint(schema: Type[BaseModel]) -> str:
    try:
        return json.dumps(schema.model_json_schema(), ensure_ascii=False)
    except Exception:
        return ""


def structured_call(
    prompt: str,
    schema: Type[BaseModel],
    *,
    images: Optional[List[Any]] = None,
    task: str = "text",
    temperature: float = 0.2,
    max_retries: int = 2,
) -> Optional[BaseModel]:
    """Obtiene una instancia validada de `schema`, o None si no se logra."""
    if images:
        return _gemini_structured(prompt, schema, images, temperature)
    return _text_structured(prompt, schema, task, temperature, max_retries)


def robust_json_call(prompt, *, images=None, task="text", temperature=0.2, max_retries=2):
    """Devuelve un dict JSON validado (o None) con reintento. Para sitios que
    construyen su objeto con data.get(...): se reemplaza el json.loads frágil por esto.
    Gráfico (con imágenes) -> Gemini; texto -> Groq (pool)."""
    if images:
        return _gemini_json(prompt, images, temperature, max_retries)
    return _text_json(prompt, task, temperature, max_retries)


def _encode_image_part(img) -> dict:
    """Convierte PIL.Image, bytes o ruta a bloque OpenAI vision `image_url`."""
    import base64
    import io

    if isinstance(img, str):
        with open(img, "rb") as f:
            raw = f.read()
    elif isinstance(img, (bytes, bytearray)):
        raw = bytes(img)
    else:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        raw = buf.getvalue()
    b64 = base64.b64encode(raw).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}


def _gemini_json(prompt, images, temperature, max_retries):
    from src.core.llm_pool import chat

    content = [{"type": "text", "text": prompt}] + [_encode_image_part(im) for im in images]
    messages = [
        {"role": "system", "content": "Responde ÚNICAMENTE con un JSON válido, sin markdown ni texto extra."},
        {"role": "user", "content": content},
    ]
    last = None
    for _ in range(max_retries + 1):
        try:
            resp = chat(messages, task="vision", temperature=temperature)
            return json.loads(_strip_fences(getattr(resp, "text", "") or ""))
        except Exception as e:
            last = e
            messages.append({"role": "user", "content": "El JSON anterior no era válido. Devuelve SOLO el JSON correcto."})
    logger.warning(f"robust_json_call (vision) falló: {last}")
    return None


def _text_json(prompt, task, temperature, max_retries):
    from src.core.llm_pool import chat
    messages = [{"role": "system", "content": "Responde ÚNICAMENTE con un JSON válido, sin markdown ni texto extra."},
                {"role": "user", "content": prompt}]
    last = None
    for _ in range(max_retries + 1):
        try:
            resp = chat(messages, task=task, temperature=temperature)
            return json.loads(_strip_fences(getattr(resp, "text", "") or ""))
        except Exception as e:
            last = e
            messages.append({"role": "user", "content": "El JSON anterior no era válido. Devuelve SOLO el JSON correcto."})
    logger.warning(f"robust_json_call (texto) falló: {last}")
    return None


def _gemini_structured(prompt, schema, images, temperature):
    from src.core.llm_pool import chat

    system = (
        "Eres un extractor técnico de planos. Responde ÚNICAMENTE con un JSON válido que "
        "cumpla EXACTAMENTE este JSON Schema (sin texto extra, sin markdown):\n"
        + _schema_hint(schema)
    )
    content = [{"type": "text", "text": prompt}] + [_encode_image_part(im) for im in images]
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": content},
    ]
    try:
        resp = chat(messages, task="vision", temperature=temperature)
        text = _strip_fences(getattr(resp, "text", "") or "")
        return schema.model_validate_json(text)
    except Exception as e:
        logger.warning(f"structured_call (vision) falló: {e}")
        return None


def _text_structured(prompt, schema, task, temperature, max_retries):
    from src.core.llm_pool import chat
    system = ("Eres un extractor de datos. Responde ÚNICAMENTE con un JSON válido que "
              "cumpla EXACTAMENTE este JSON Schema (sin texto extra, sin markdown):\n"
              + _schema_hint(schema))
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": prompt}]
    last_err = None
    for _ in range(max_retries + 1):
        try:
            resp = chat(messages, task=task, temperature=temperature)
            text = _strip_fences(getattr(resp, "text", "") or "")
            return schema.model_validate_json(text)
        except (ValidationError, ValueError, json.JSONDecodeError) as e:
            last_err = e
            messages.append({"role": "user",
                             "content": f"El JSON anterior no era válido ({str(e)[:150]}). "
                                        "Devuelve SOLO el JSON correcto que cumpla el esquema."})
        except Exception as e:
            last_err = e
            break
    logger.warning(f"structured_call (texto) falló tras reintentos: {last_err}")
    return None
