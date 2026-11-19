"""Pipeline Híbrido 2D para planos PDF: Clasificador Vectorial vs Escaneado + PyMuPDF + Tiling."""
import os
from typing import Any, Dict, List, Optional

import pymupdf as fitz  # PyMuPDF

from src.engines.cad_vision.tiling import tile_image_bytes


def _default_tiles_dir() -> str:
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    cache_root = os.getenv("WORKSPACE_CACHE_DIR") or os.path.join(root, "workspace_cache")
    d = os.path.join(cache_root, "tiles")
    os.makedirs(d, exist_ok=True)
    return d


def classify_pdf(file_path: str, sample_pages: int = 10) -> Dict[str, Any]:
    """Clasifica un PDF técnico como 'vectorial', 'escaneado' o 'mixto'."""
    real_path = os.path.realpath(file_path)
    doc = fitz.open(real_path)
    total_pages = len(doc)
    pages_info: List[Dict[str, Any]] = []
    vec_count = 0
    scan_count = 0

    for idx in range(min(total_pages, sample_pages)):
        page = doc[idx]
        text_len = len((page.get_text("text") or "").strip())
        n_drawings = len(page.get_drawings())
        n_images = len(page.get_images())

        if text_len >= 40 or n_drawings >= 20:
            kind = "vectorial"
            vec_count += 1
        elif n_images >= 1:
            kind = "escaneado"
            scan_count += 1
        else:
            kind = "vacio_o_raster"
            scan_count += 1

        pages_info.append(
            {
                "page": idx + 1,
                "kind": kind,
                "text_chars": text_len,
                "vector_drawings": n_drawings,
                "images": n_images,
            }
        )

    doc.close()
    if vec_count > 0 and scan_count == 0:
        overall = "vectorial"
    elif scan_count > 0 and vec_count == 0:
        overall = "escaneado"
    else:
        overall = "mixto"

    return {
        "file_path": real_path,
        "total_pages": total_pages,
        "classification": overall,
        "sampled_pages": pages_info,
    }


def extract_vectorial_pdf(file_path: str, max_pages: int = 50) -> Dict[str, Any]:
    """Extrae capas OCG, bloques de texto y conteo de trazos vectoriales de un PDF."""
    real_path = os.path.realpath(file_path)
    doc = fitz.open(real_path)
    ocgs = {}
    try:
        ocgs = {str(k): v.get("name", "") for k, v in (doc.get_ocgs() or {}).items()}
    except Exception:
        ocgs = {}

    pages: List[Dict[str, Any]] = []
    for idx in range(min(len(doc), max_pages)):
        page = doc[idx]
        blocks = [
            {
                "bbox": [round(float(x), 2) for x in b[:4]],
                "text": (b[4] or "").strip(),
            }
            for b in page.get_text("blocks")
            if len(b) >= 5 and (b[4] or "").strip()
        ]
        pages.append(
            {
                "page": idx + 1,
                "width_pt": round(float(page.rect.width), 2),
                "height_pt": round(float(page.rect.height), 2),
                "vector_drawings": len(page.get_drawings()),
                "text_blocks": blocks[:200],
            }
        )

    total = len(doc)
    doc.close()
    return {
        "file_path": real_path,
        "total_pages": total,
        "layers_ocg": ocgs,
        "pages": pages,
    }


def render_pdf_tiles(
    file_path: str,
    *,
    page_num: int = 0,
    dpi: int = 200,
    grid_cols: int = 2,
    grid_rows: int = 2,
    overlap_ratio: float = 0.15,
    out_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Renderiza vista general y cuadrantes con solape en workspace_cache/tiles/ para visión multimodal."""
    real_path = os.path.realpath(file_path)
    doc = fitz.open(real_path)
    if page_num < 0 or page_num >= len(doc):
        doc.close()
        raise IndexError(f"Página {page_num} fuera de rango (total: {len(doc)})")

    page = doc[page_num]
    pix = page.get_pixmap(dpi=dpi)
    img_bytes = pix.tobytes("png")
    doc.close()

    target_dir = out_dir or _default_tiles_dir()
    os.makedirs(target_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(real_path))[0]

    overview_path = os.path.join(target_dir, f"{stem}_p{page_num + 1}_overview.png")
    with open(overview_path, "wb") as f:
        f.write(img_bytes)

    tiles = tile_image_bytes(
        img_bytes,
        grid_cols=grid_cols,
        grid_rows=grid_rows,
        overlap_ratio=overlap_ratio,
    )
    saved_tiles: List[Dict[str, Any]] = []
    for t in tiles:
        tile_path = os.path.join(target_dir, f"{stem}_p{page_num + 1}_{t['tile_id']}.png")
        with open(tile_path, "wb") as f:
            f.write(t["image_bytes"])
        saved_tiles.append(
            {
                "tile_id": t["tile_id"],
                "index": t["index"],
                "x_offset": t["x_offset"],
                "y_offset": t["y_offset"],
                "width": t["width"],
                "height": t["height"],
                "tile_path": tile_path,
            }
        )

    return {
        "file_path": real_path,
        "page": page_num + 1,
        "dpi": dpi,
        "overview_path": overview_path,
        "tiles": saved_tiles,
    }
