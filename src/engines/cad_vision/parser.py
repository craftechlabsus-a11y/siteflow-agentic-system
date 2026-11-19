"""Parser de geometría CAD (DXF) con ezdxf → cantidades EXACTAS y deterministas.

Extrae por capa: longitudes, áreas (polilíneas cerradas/círculos) y conteos; por bloque:
conteos (p. ej. nº de pilares); y textos. Convierte a metros según las unidades del dibujo.
A diferencia de la visión sobre raster, esto es exacto (alta confianza, trazable).

APLANADO RECURSIVO: los planos reales cuelgan casi toda su geometría de bloques y
referencias externas (XREF) incrustadas — en el modelspace solo hay INSERTs. Por eso se
recorre el árbol de INSERTs con `virtual_entities()` (que aplica escala/rotación/posición
y devuelve la geometría en coordenadas reales) hasta cierta profundidad y presupuesto de
entidades, midiendo la geometría allí donde realmente está.
"""
import math
import logging

logger = logging.getLogger("api.cad.parser")

# $INSUNITS -> factor a metros
_UNIT_FACTOR = {0: 1.0, 1: 0.0254, 2: 0.3048, 4: 0.001, 5: 0.01, 6: 1.0, 8: 1e-6, 13: 1e-9}
_UNIT_NAME = {0: "sin_especificar", 1: "in", 2: "ft", 4: "mm", 5: "cm", 6: "m"}


def _shoelace(pts):
    n = len(pts)
    s = 0.0
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def _perimeter(pts, closed):
    n = len(pts)
    total = 0.0
    rng = range(n) if closed else range(n - 1)
    for i in rng:
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        total += math.hypot(x2 - x1, y2 - y1)
    return total


def parse_dxf(path, max_depth=12, max_entidades=500000, max_textos=200):
    """Devuelve un dict con geometría exacta. Lanza si el archivo no es un DXF legible.

    Recorre recursivamente los INSERTs (bloques/xrefs) aplicando sus transformaciones, con
    tope de profundidad y de nº de entidades para evitar explosiones patológicas.
    """
    import ezdxf
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()

    insunits = int(doc.header.get("$INSUNITS", 0) or 0)
    f = _UNIT_FACTOR.get(insunits, 1.0)        # factor a metros (longitud)
    fa = f * f                                  # factor a m² (área)

    por_capa = {}
    bloques = {}
    textos = []
    atributos_bloques = []
    cotas = []
    stats = {"entidades_medidas": 0, "inserts_expandidos": 0, "profundidad_max": 0,
             "truncado": False}

    def acc(capa):
        return por_capa.setdefault(capa, {"longitud_m": 0.0, "area_m2": 0.0,
                                          "n_lineas": 0, "n_polilineas": 0,
                                          "n_circulos": 0, "n_arcos": 0})

    def medir(e):
        """Mide una entidad primitiva (ya en coordenadas reales/WCS)."""
        t = e.dxftype()
        capa = getattr(e.dxf, "layer", "0")
        try:
            if t == "LINE":
                a = acc(capa); a["n_lineas"] += 1
                s, en = e.dxf.start, e.dxf.end
                a["longitud_m"] += math.hypot(en[0] - s[0], en[1] - s[1]) * f
            elif t == "LWPOLYLINE":
                a = acc(capa); a["n_polilineas"] += 1
                pts = [(p[0], p[1]) for p in e.get_points()]
                a["longitud_m"] += _perimeter(pts, bool(e.closed)) * f
                if e.closed and len(pts) >= 3:
                    a["area_m2"] += _shoelace(pts) * fa
            elif t == "POLYLINE":
                a = acc(capa); a["n_polilineas"] += 1
                pts = [(v.dxf.location[0], v.dxf.location[1]) for v in e.vertices]
                closed = bool(e.is_closed)
                a["longitud_m"] += _perimeter(pts, closed) * f
                if closed and len(pts) >= 3:
                    a["area_m2"] += _shoelace(pts) * fa
            elif t == "CIRCLE":
                a = acc(capa); a["n_circulos"] += 1
                r = e.dxf.radius
                a["longitud_m"] += 2 * math.pi * r * f
                a["area_m2"] += math.pi * r * r * fa
            elif t == "ARC":
                a = acc(capa); a["n_arcos"] += 1
                ang = (e.dxf.end_angle - e.dxf.start_angle) % 360.0
                a["longitud_m"] += math.radians(ang) * e.dxf.radius * f
            elif t in ("TEXT", "MTEXT"):
                if len(textos) < max_textos:
                    if t == "TEXT":
                        txt = e.dxf.text
                    else:
                        txt = e.plain_text() if hasattr(e, "plain_text") else e.text
                    textos.append({"capa": capa, "texto": (txt or "").strip()[:120]})
            elif t == "DIMENSION":
                if len(cotas) < max_textos:
                    val = None
                    try:
                        val = float(e.get_measurement()) * f
                    except Exception:
                        pass
                    cotas.append({
                        "capa": capa,
                        "medida_m": round(val, 4) if val is not None else None,
                        "texto": (getattr(e.dxf, "text", "") or "")[:80],
                    })
        except Exception as ex:
            logger.debug(f"entidad {t} omitida: {ex}")

    def walk(entidades, depth):
        for e in entidades:
            if stats["entidades_medidas"] >= max_entidades:
                stats["truncado"] = True
                return
            if e.dxftype() == "INSERT":
                bloques[e.dxf.name] = bloques.get(e.dxf.name, 0) + 1
                if hasattr(e, "attribs") and e.attribs and len(atributos_bloques) < max_textos:
                    attrs = {
                        a.dxf.tag: (a.dxf.text or "").strip()
                        for a in e.attribs
                        if getattr(a.dxf, "tag", None)
                    }
                    if attrs:
                        atributos_bloques.append({
                            "bloque": e.dxf.name,
                            "capa": getattr(e.dxf, "layer", "0"),
                            "atributos": attrs,
                        })
                if depth < max_depth:
                    stats["inserts_expandidos"] += 1
                    stats["profundidad_max"] = max(stats["profundidad_max"], depth + 1)
                    try:
                        walk(e.virtual_entities(), depth + 1)
                    except Exception as ex:
                        logger.debug(f"INSERT {getattr(e.dxf,'name','?')} no expandido: {ex}")
            else:
                stats["entidades_medidas"] += 1
                medir(e)

    walk(msp, 0)

    # redondeo
    for a in por_capa.values():
        a["longitud_m"] = round(a["longitud_m"], 3)
        a["area_m2"] = round(a["area_m2"], 3)

    return {
        "version": doc.dxfversion,
        "unidades": _UNIT_NAME.get(insunits, str(insunits)),
        "unidades_especificadas": insunits != 0,
        "capas": [l.dxf.name for l in doc.layers],
        "por_capa": por_capa,
        "bloques": bloques,
        "atributos_bloques": atributos_bloques[:100],
        "cotas": cotas[:100],
        "n_textos": len(textos),
        "textos": textos[:50],
        "stats": stats,
    }
