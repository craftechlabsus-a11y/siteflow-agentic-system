"""DLP Zero-Leakage reversible para llamadas LLM externas (CIF/NIF, IBAN, emails, teléfonos)."""
import re
from typing import Any, Dict, List

_PATTERNS = [
    ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")),
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("CIF_NIF", re.compile(r"\b(?:[A-HJNPQRSUVW]\d{7}[0-9A-J]|\d{8}[A-Z]|[XYZ]\d{7}[A-Z])\b")),
    ("PHONE", re.compile(r"(?:\+34[\s\-]?)?\b[6789]\d{2}[\s\-]?\d{3}[\s\-]?\d{3}\b")),
]


class DLPSanitizer:
    """Tokeniza datos sensibles antes de salir al LLM y los restaura en local."""

    def __init__(self) -> None:
        self._reverse: Dict[str, str] = {}
        self._forward: Dict[str, str] = {}
        self._counters: Dict[str, int] = {}

    def sanitize_text(self, text: str) -> str:
        if not text:
            return text
        out = text
        for label, pat in _PATTERNS:
            def _repl(m: re.Match) -> str:
                raw = m.group(0)
                if raw in self._forward:
                    return self._forward[raw]
                idx = self._counters.get(label, 0) + 1
                self._counters[label] = idx
                tok = f"<{label}_{idx}>"
                self._forward[raw] = tok
                self._reverse[tok] = raw
                return tok

            out = pat.sub(_repl, out)
        return out

    def restore_text(self, text: str) -> str:
        if not text or not self._reverse:
            return text
        out = text
        for tok, raw in self._reverse.items():
            out = out.replace(tok, raw)
        return out

    def sanitize_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        sanitized: List[Dict[str, Any]] = []
        for msg in messages:
            copy_msg = dict(msg)
            content = copy_msg.get("content")
            if isinstance(content, str):
                copy_msg["content"] = self.sanitize_text(content)
            elif isinstance(content, list):
                new_parts = []
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                        p_copy = dict(part)
                        p_copy["text"] = self.sanitize_text(part["text"])
                        new_parts.append(p_copy)
                    else:
                        new_parts.append(part)
                copy_msg["content"] = new_parts
            sanitized.append(copy_msg)
        return sanitized
