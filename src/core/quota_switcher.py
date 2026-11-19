"""Switch proactivo de cuota de tokens y solicitudes por clave API / modelo (umbral 96%)."""
import os
import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple


@dataclass
class KeyQuotaStats:
    tokens_used: int = 0
    requests_used: int = 0
    last_updated: float = 0.0


class QuotaSwitcher:
    """Monitorea consumo por (provider, key_index) y dispara switch proactivo al alcanzar el 96%."""

    def __init__(
        self,
        *,
        switch_ratio: Optional[float] = None,
        token_limit: Optional[int] = None,
        request_limit: Optional[int] = None,
    ) -> None:
        self.switch_ratio = float(
            switch_ratio if switch_ratio is not None else os.getenv("LLM_QUOTA_SWITCH_RATIO", "0.96")
        )
        self.token_limit = int(
            token_limit if token_limit is not None else os.getenv("LLM_KEY_TOKEN_LIMIT", "500000")
        )
        self.request_limit = int(
            request_limit if request_limit is not None else os.getenv("LLM_KEY_REQ_LIMIT", "1000")
        )
        self._lock = threading.Lock()
        self._stats: Dict[Tuple[str, int], KeyQuotaStats] = {}

    def record_usage(
        self,
        provider: str,
        key_index: int,
        *,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: Optional[int] = None,
        requests: int = 1,
    ) -> KeyQuotaStats:
        tokens = int(total_tokens) if total_tokens is not None else int(prompt_tokens) + int(completion_tokens)
        key = (provider, int(key_index))
        with self._lock:
            st = self._stats.setdefault(key, KeyQuotaStats())
            st.tokens_used += max(0, tokens)
            st.requests_used += max(0, int(requests))
            st.last_updated = time.time()
            return KeyQuotaStats(st.tokens_used, st.requests_used, st.last_updated)

    def usage_ratios(self, provider: str, key_index: int) -> Dict[str, float]:
        with self._lock:
            st = self._stats.get((provider, int(key_index)), KeyQuotaStats())
            tok_ratio = st.tokens_used / max(1, self.token_limit)
            req_ratio = st.requests_used / max(1, self.request_limit)
            return {
                "token_ratio": round(tok_ratio, 4),
                "request_ratio": round(req_ratio, 4),
                "tokens_used": st.tokens_used,
                "requests_used": st.requests_used,
            }

    def should_switch(self, provider: str, key_index: int) -> bool:
        """Devuelve True si la clave ha alcanzado el umbral (96%) de tokens o solicitudes."""
        ratios = self.usage_ratios(provider, key_index)
        return (
            ratios["token_ratio"] >= self.switch_ratio
            or ratios["request_ratio"] >= self.switch_ratio
        )

    def reset_key(self, provider: str, key_index: int) -> None:
        with self._lock:
            self._stats.pop((provider, int(key_index)), None)

    def snapshot(self) -> Dict[str, Dict[str, float]]:
        with self._lock:
            out: Dict[str, Dict[str, float]] = {}
            for (prov, idx), st in self._stats.items():
                out[f"{prov}[{idx}]"] = {
                    "tokens_used": st.tokens_used,
                    "requests_used": st.requests_used,
                    "token_ratio": round(st.tokens_used / max(1, self.token_limit), 4),
                    "request_ratio": round(st.requests_used / max(1, self.request_limit), 4),
                    "switch_triggered": (
                        (st.tokens_used / max(1, self.token_limit)) >= self.switch_ratio
                        or (st.requests_used / max(1, self.request_limit)) >= self.switch_ratio
                    ),
                }
            return out


_DEFAULT_SWITCHER = QuotaSwitcher()


def get_quota_switcher() -> QuotaSwitcher:
    return _DEFAULT_SWITCHER
