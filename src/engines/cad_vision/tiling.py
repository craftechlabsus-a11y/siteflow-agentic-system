"""Tiling con solapamiento para planos raster de gran formato (v2.6.0)."""
import io
from typing import Any, Dict, List


def tile_image_bytes(
    image_bytes: bytes,
    *,
    grid_cols: int = 2,
    grid_rows: int = 2,
    overlap_ratio: float = 0.15,
) -> List[Dict[str, Any]]:
    """Divide una imagen de plano en cuadrantes con solapamiento y coordenadas globales."""
    from PIL import Image

    img = Image.open(io.BytesIO(image_bytes))
    w, h = img.size
    cols = max(1, int(grid_cols))
    rows = max(1, int(grid_rows))
    step_x = w / cols
    step_y = h / rows
    pad_x = int(step_x * overlap_ratio)
    pad_y = int(step_y * overlap_ratio)

    tiles: List[Dict[str, Any]] = []
    idx = 0
    for r in range(rows):
        for c in range(cols):
            idx += 1
            x0 = max(0, int(c * step_x) - pad_x)
            y0 = max(0, int(r * step_y) - pad_y)
            x1 = min(w, int((c + 1) * step_x) + pad_x)
            y1 = min(h, int((r + 1) * step_y) + pad_y)
            crop = img.crop((x0, y0, x1, y1))
            buf = io.BytesIO()
            crop.save(buf, format="PNG")
            tiles.append(
                {
                    "tile_id": f"r{r}_c{c}",
                    "index": idx,
                    "x_offset": x0,
                    "y_offset": y0,
                    "width": x1 - x0,
                    "height": y1 - y0,
                    "image_bytes": buf.getvalue(),
                }
            )
    return tiles
