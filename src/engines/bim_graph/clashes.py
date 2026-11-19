"""Detección determinista de interferencias (Clash Detection) en 2 fases (v2.6.0)."""
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


def detect_clashes(
    elements: List[Dict[str, Any]],
    *,
    discipline_pairs: Optional[List[Tuple[str, str]]] = None,
    tolerance_m: float = 0.005,
    clearance_m: float = 0.0,
) -> List[Dict[str, Any]]:
    """Detecta colisiones entre elementos BIM usando AABB vectorizado en NumPy.

    - tolerance_m: penetración mínima (m) para considerar hard_clash.
    - clearance_m: margen adicional (m) para detectar soft_clash / holgura insuficiente.
    """
    valid = [
        e
        for e in elements
        if isinstance(e.get("bbox_min"), (list, tuple))
        and isinstance(e.get("bbox_max"), (list, tuple))
        and len(e["bbox_min"]) == 3
        and len(e["bbox_max"]) == 3
    ]
    n = len(valid)
    if n < 2:
        return []

    mins = np.array([e["bbox_min"] for e in valid], dtype=float)
    maxs = np.array([e["bbox_max"] for e in valid], dtype=float)
    pad = float(clearance_m)

    pair_set = None
    if discipline_pairs:
        pair_set = {tuple(sorted((a.upper(), b.upper()))) for a, b in discipline_pairs}

    clashes: List[Dict[str, Any]] = []
    for i in range(n):
        ei = valid[i]
        di = str(ei.get("disciplina") or "GENERAL").upper()
        # Vectorizado contra j > i
        lo = np.maximum(mins[i] - pad, mins[i + 1 :] - pad)
        hi = np.minimum(maxs[i] + pad, maxs[i + 1 :] + pad)
        overlap = hi - lo
        mask = np.all(overlap > 0.0, axis=1)
        cand_indices = np.where(mask)[0]

        for offset in cand_indices:
            j = i + 1 + int(offset)
            ej = valid[j]
            if ei.get("global_id") and ei.get("global_id") == ej.get("global_id"):
                continue
            dj = str(ej.get("disciplina") or "GENERAL").upper()
            if pair_set is not None and tuple(sorted((di, dj))) not in pair_set:
                continue

            # Penetración real sin padding de clearance
            real_lo = np.maximum(mins[i], mins[j])
            real_hi = np.minimum(maxs[i], maxs[j])
            real_ov = real_hi - real_lo
            is_hard = bool(np.all(real_ov > tolerance_m))
            ov_vol = float(np.prod(np.maximum(real_ov, 0.0))) if is_hard else 0.0

            if not is_hard and clearance_m <= 0.0:
                continue

            clashes.append(
                {
                    "element_a": ei.get("global_id") or f"idx_{i}",
                    "class_a": ei.get("ifc_class"),
                    "disciplina_a": di,
                    "element_b": ej.get("global_id") or f"idx_{j}",
                    "class_b": ej.get("ifc_class"),
                    "disciplina_b": dj,
                    "storey": ei.get("storey") or ej.get("storey") or "",
                    "clash_type": "hard_clash" if is_hard else "clearance",
                    "overlap_volume_m3": round(ov_vol, 6),
                    "min_penetration_m": round(float(np.min(real_ov)) if is_hard else 0.0, 4),
                }
            )
    return clashes
