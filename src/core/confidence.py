"""Confianza y human-in-the-loop (Fase 2, Sprint 2.4).

Convención unificada: todo resultado con 'confianza' puede marcarse para revisión
humana si está por debajo del umbral, evitando que datos poco fiables contaminen
los cálculos sin aprobación.
"""
import os

UMBRAL_REVISION = float(os.getenv("CONFIANZA_UMBRAL_REVISION", "0.6"))


def needs_review(confianza) -> bool:
    """True si la confianza está por debajo del umbral (requiere validación humana)."""
    try:
        return float(confianza or 0) < UMBRAL_REVISION
    except (TypeError, ValueError):
        return True


def etiqueta(confianza) -> str:
    try:
        c = float(confianza or 0)
    except (TypeError, ValueError):
        c = 0.0
    if c >= 0.85:
        return "alta"
    if c >= UMBRAL_REVISION:
        return "media"
    return "baja"


def flag_review(result: dict) -> dict:
    """Añade 'requiere_revision' y 'nivel_confianza' a un dict con 'confianza'."""
    if not isinstance(result, dict):
        return result
    c = result.get("confianza", result.get("confidence", 0))
    result["requiere_revision"] = needs_review(c)
    result["nivel_confianza"] = etiqueta(c)
    return result
