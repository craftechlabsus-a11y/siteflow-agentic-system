"""Conversor DWG → DXF multiplataforma con FALLBACK AUTOMÁTICO y validación.

DWG es propietario/binario: ezdxf no lo lee directo. Se prueba una cadena de
conversores y, tras cada uno, se VALIDA que el DXF resultante sea legible y no vacío;
si no, se pasa automáticamente al siguiente:
  1) LibreDWG (dwg2dxf) — libre, compilado en la imagen.
  2) ODA File Converter (headless con xvfb) — si está instalado.
  3) Puente AutoCAD COM (solo Windows).
Si el archivo ya es DXF, se devuelve tal cual. Si ninguno da un DXF válido, None.
"""
import os
import shutil
import tempfile
import subprocess
import logging

logger = logging.getLogger("api.cad.converter")


def _find_oda():
    return os.getenv("ODA_CONVERTER_PATH") or shutil.which("ODAFileConverter")


def _run(cmd, timeout=180):
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=timeout)
        return True
    except Exception as e:
        logger.warning(f"conversor falló ({cmd[0]}): {str(e)[:150]}")
        return False


def _valid_dxf(dxf_path):
    """El DXF resultante debe ser legible y con contenido (evita 'éxito' con basura/vacío)."""
    if not dxf_path or not os.path.exists(dxf_path):
        return False
    try:
        import ezdxf
        doc = ezdxf.readfile(dxf_path)
        n_ent = sum(1 for _ in doc.modelspace())
        n_lay = sum(1 for _ in doc.layers)
        return n_ent > 0 or n_lay > 1
    except Exception as e:
        logger.warning(f"DXF resultante no válido: {str(e)[:120]}")
        return False


# --- conversores concretos ---
def _convert_libredwg(dwg_path):
    with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as tf:
        out = tf.name
    if _run(["dwg2dxf", "-y", "-o", out, dwg_path]) and os.path.exists(out):
        return out
    if os.path.exists(out):
        try:
            os.remove(out)
        except Exception:
            pass
    return None


def _convert_oda(oda, dwg_path):
    in_dir = tempfile.mkdtemp(prefix="cad_in_")
    out_dir = tempfile.mkdtemp(prefix="cad_out_")
    shutil.copy(dwg_path, in_dir)
    base = ["xvfb-run", "-a", oda] if shutil.which("xvfb-run") else [oda]
    if _run(base + [in_dir, out_dir, "ACAD2018", "DXF", "0", "1"]):
        for fn in os.listdir(out_dir):
            if fn.lower().endswith(".dxf"):
                return os.path.join(out_dir, fn)
    return None


def _convert_autocad(dwg_path):
    try:
        from api.autocad_bridge import get_autocad_bridge
        bridge = get_autocad_bridge()
        if bridge and getattr(bridge, "is_available", False):
            return bridge.convert_dwg_to_dxf(dwg_path)
    except Exception:
        pass
    return None


def _cadena():
    """Cadena de conversores disponibles, en orden de preferencia."""
    chain = []
    if shutil.which("dwg2dxf"):
        chain.append(("libredwg", _convert_libredwg))
    oda = _find_oda()
    if oda:
        chain.append(("oda", lambda p: _convert_oda(oda, p)))
    chain.append(("autocad", _convert_autocad))
    return chain


def to_dxf(path):
    """Devuelve la ruta a un DXF VÁLIDO (o el mismo si ya lo es), o None si no se puede."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".dxf":
        return path if os.path.exists(path) else None
    if ext != ".dwg":
        logger.warning(f"Extensión no soportada para CAD: {ext}")
        return None

    for nombre, fn in _cadena():
        try:
            out = fn(path)
        except Exception as e:
            logger.warning(f"conversor {nombre} error: {str(e)[:120]}")
            out = None
        if out and _valid_dxf(out):
            logger.info(f"✅ DWG→DXF con {nombre}")
            return out
        if out:
            logger.warning(f"{nombre} produjo un DXF inválido/vacío; pruebo el siguiente.")

    logger.warning("Sin conversor DWG→DXF válido (instala ODA File Converter o usa DXF).")
    return None
