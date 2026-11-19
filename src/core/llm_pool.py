"""Pool multi-proveedor con failover y rotación de claves (SiteFlow Pro).

Reparte las llamadas LLM de TEXTO entre Groq, OpenRouter y Gemini (los tres
hablan la API compatible con OpenAI). Cuando una clave agota su cuota gratuita
(HTTP 429) o falla por credenciales (401/403), se marca en *cooldown* y se rota
a la siguiente clave del mismo proveedor; si se agotan todas, se pasa al
siguiente proveedor de la cadena definida por tipo de tarea.

Las claves NUNCA se escriben en código: se leen de variables de entorno
(pools separados por comas), p. ej. GROQ_API_KEYS, OPENROUTER_API_KEYS,
GEMINI_API_KEYS. Ver .env / .env.example.
"""
import os
import time
import logging
import threading

import httpx

try:  # cargar .env en ejecución local (en Docker las inyecta compose)
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

logger = logging.getLogger("api.llm_pool")


def _parse_keys(*env_names):
    keys = []
    for name in env_names:
        for k in (os.getenv(name, "") or "").split(","):
            k = k.strip()
            if k and k not in keys:
                keys.append(k)
    return keys


PROVIDERS = {
    "groq": {
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "keys": _parse_keys("GROQ_API_KEYS", "GROQ_API_KEY"),
    },
    "openrouter": {
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "keys": _parse_keys("OPENROUTER_API_KEYS", "OPENROUTER_API_KEY"),
    },
    "gemini": {
        "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "keys": _parse_keys("GEMINI_API_KEYS", "GEMINI_API_KEY", "GOOGLE_API_KEY"),
    },
}

# Cadena de (proveedor, modelo) por tipo de tarea. Failover en este orden.
TASK_CHAINS = {
    "text": [
        ("groq", "llama-3.3-70b-versatile"),
        ("openrouter", "meta-llama/llama-3.3-70b-instruct"),
        ("gemini", "gemini-2.5-flash"),
    ],
    "fast": [
        ("groq", "llama-3.1-8b-instant"),
        ("groq", "llama-3.3-70b-versatile"),
        ("gemini", "gemini-2.5-flash"),
    ],
    "reasoning": [
        ("gemini", "gemini-2.5-pro"),
        ("openrouter", "anthropic/claude-3.5-sonnet"),
        ("groq", "llama-3.3-70b-versatile"),
    ],
    "vision": [
        ("gemini", "gemini-2.5-flash"),
        ("openrouter", "google/gemini-2.5-flash"),
    ],
}
DEFAULT_TASK = "text"

_RATE_COOLDOWN = float(os.getenv("LLM_RATE_COOLDOWN", "120"))      # 429 puntual
_QUOTA_COOLDOWN = float(os.getenv("LLM_QUOTA_COOLDOWN", "21600"))  # cuota/credencial agotada (6 h)
_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "60"))

_lock = threading.Lock()
_cooldown = {}   # (provider, key_index) -> timestamp hasta el que está vetada
_rr = {}         # provider -> índice round-robin


class PoolResponse:
    """Respuesta con .text para ser compatible con el cliente Gemini previo."""
    def __init__(self, text, provider, model, key_index, usage=None):
        self.text = text
        self.provider = provider
        self.model = model
        self.key_index = key_index
        self.usage = usage or {}

    def __str__(self):
        return self.text or ""


class LLMPoolError(RuntimeError):
    pass


from src.core.quota_switcher import get_quota_switcher


def _available_keys(provider):
    now = time.time()
    switcher = get_quota_switcher()
    return [
        (i, k)
        for i, k in enumerate(PROVIDERS[provider]["keys"])
        if _cooldown.get((provider, i), 0) <= now and not switcher.should_switch(provider, i)
    ]


def _mark_cooldown(provider, idx, seconds):
    with _lock:
        _cooldown[(provider, idx)] = time.time() + seconds


def _headers(provider, key):
    h = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if provider == "openrouter":
        h["HTTP-Referer"] = os.getenv("OPENROUTER_REFERER", "https://siteflow.pro")
        h["X-Title"] = "SiteFlow Pro"
    return h


def chat(messages, task="text", temperature=None, max_tokens=None, model=None, dlp_enabled=None):
    """Envía mensajes (formato OpenAI) con failover de clave y de proveedor.

    Aplica DLP Zero-Leakage reversible por defecto (DLP_ENABLED=true) para no filtrar
    CIF/NIF, IBAN, emails ni teléfonos a proveedores externos.
    Devuelve PoolResponse o lanza LLMPoolError si TODO falla.
    """
    use_dlp = dlp_enabled if dlp_enabled is not None else (os.getenv("DLP_ENABLED", "true").lower() == "true")
    sanitizer = None
    outbound_messages = messages
    if use_dlp:
        from src.core.dlp_guard import DLPSanitizer
        sanitizer = DLPSanitizer()
        outbound_messages = sanitizer.sanitize_messages(messages)

    chain = TASK_CHAINS.get(task, TASK_CHAINS[DEFAULT_TASK])
    errors = []
    for provider, default_model in chain:
        cfg = PROVIDERS.get(provider)
        if not cfg or not cfg["keys"]:
            continue
        use_model = model or default_model
        avail = _available_keys(provider)
        if not avail:
            errors.append(f"{provider}: todas las claves en cooldown")
            continue
        n = len(cfg["keys"])
        with _lock:
            start = _rr.get(provider, 0)
            _rr[provider] = (start + 1) % n
        avail.sort(key=lambda t: (t[0] - start) % n)  # empezar por el round-robin
        for idx, key in avail:
            payload = {"model": use_model, "messages": outbound_messages}
            if temperature is not None:
                payload["temperature"] = temperature
            if max_tokens is not None:
                payload["max_tokens"] = max_tokens
            try:
                r = httpx.post(cfg["url"], headers=_headers(provider, key),
                               json=payload, timeout=_TIMEOUT)
            except Exception as e:
                errors.append(f"{provider}#{idx}: red {e}")
                continue
            if r.status_code == 200:
                try:
                    data = r.json()
                    text = data["choices"][0]["message"]["content"]
                    if sanitizer is not None and isinstance(text, str):
                        text = sanitizer.restore_text(text)
                    usage = data.get("usage") or {}
                    get_quota_switcher().record_usage(
                        provider,
                        idx,
                        prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
                        completion_tokens=int(usage.get("completion_tokens", 0) or 0),
                        total_tokens=usage.get("total_tokens"),
                        requests=1,
                    )
                except Exception as e:
                    errors.append(f"{provider}#{idx}: respuesta ilegible {e}")
                    continue
                logger.info(f"✅ LLM provider={provider} model={use_model} key#{idx}")
                return PoolResponse(text, provider, use_model, idx, data.get("usage"))
            if r.status_code == 429:
                _mark_cooldown(provider, idx, _RATE_COOLDOWN)
                errors.append(f"{provider}#{idx}: 429 (rate/cuota) -> rotando")
                logger.warning(f"⚠️ {provider} clave #{idx} 429; rotando")
                continue
            if r.status_code in (401, 403):
                _mark_cooldown(provider, idx, _QUOTA_COOLDOWN)
                errors.append(f"{provider}#{idx}: {r.status_code} credencial/cuota")
                continue
            errors.append(f"{provider}#{idx}: HTTP {r.status_code} {r.text[:120]}")
    raise LLMPoolError("Todos los proveedores/claves fallaron: " + " | ".join(errors[-8:]))


def chat_text(prompt, task="text", **kw):
    return chat([{"role": "user", "content": prompt}], task=task, **kw)


def has_text_keys():
    return any(PROVIDERS[p]["keys"] for p in PROVIDERS)


def status():
    now = time.time()
    switcher = get_quota_switcher()
    return {
        "providers": {
            p: {
                "keys": len(PROVIDERS[p]["keys"]),
                "disponibles": len(_available_keys(p)),
                "en_cooldown": sum(1 for (pp, _i), t in _cooldown.items() if pp == p and t > now),
                "en_switch_96pct": sum(
                    1 for i in range(len(PROVIDERS[p]["keys"])) if switcher.should_switch(p, i)
                ),
            }
            for p in PROVIDERS
        },
        "quota_usage": switcher.snapshot(),
    }
