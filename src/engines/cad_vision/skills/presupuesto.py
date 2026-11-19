"""Skill CAD — Takeoff con PRESUPUESTO: cierra el bucle plano → cantidades → coste.

Encadena las piezas que ya existían y que estaban sueltas:
    parse_dxf (geometría exacta por capa)
      → mapeo_capas (capa → tipología estructural)
      → volumen (área en planta × canto)
      → cuantias_acero (m³ → ton de acero)
      → pricing.lookup_price (coste por país)

Honestidad del resultado: el plano 2D NO contiene el canto/altura. Si el usuario no los
aporta en `espesores_m`, se usan supuestos de predimensionado y el resultado se marca
`requiere_revision=True`, dejando explícito en `supuestos` qué se asumió.

La función `calcular_presupuesto_desde_agrupado` es el núcleo reutilizable: opera sobre
un `agrup` (salida de `mapeo_capas.agrupar_por_tipologia` o `.combinar_multi_plano`) ya
resuelto, sin saber si viene de UN plano (esta skill) o de VARIOS planos de una misma
`ProjectZone` combinados (`api/routers/zonas.py`, que cierra el hueco de "una zona
necesita todos sus planos" -- ver HANDOFF_DIARIO_TRABAJO.md #6.8).
"""
import os
import tempfile
import logging

from api.skills.base import register_skill, SkillResult
from api.schemas.skills_cad import CadPresupuestoInputs

logger = logging.getLogger("api.skills.cad.presupuesto")


# Término de búsqueda en las bases de precios oficiales y UNIDAD ESTÁNDAR de cada concepto
# (manual de mediciones, cap. 4). La unidad NO es opcional: sin ella no se puede descartar una
# referencia en €/ud, y una sola de esas dispara el rango y arruina el presupuesto.
_TERMINO_BASE = {
    "hormigon": ("hormigón para armar", "m3"),
    "acero": ("acero corrugado b 500 s", "kg"),
    "encofrado": ("encofrado", "m2"),
    "impermeabilizacion": ("impermeabilización", "m2"),
}
# Las bases dan el acero en €/kg, pero el takeoff lo mide en toneladas.
_FACTOR_UNIDAD = {"acero": 1000.0}


def _precio_benchmark(term, pais, region, db, clase_aace):
    """Valora con el RANGO multi-fuente de las bases de precios (cerebro de costes).

    Devuelve (precio, fuente) o None si no hay ninguna referencia utilizable.
    """
    try:
        from api.pricing import buscar_referencias, parametros_benchmark
        from api.domain.benchmark import seleccionar_precio
        busqueda, unidad = _TERMINO_BASE.get(term, (term, ""))
        refs = buscar_referencias(busqueda, pais, db=db)
        if not refs:
            return None
        params = parametros_benchmark(db=db)
        res = seleccionar_precio(None, refs, unidad_partida=unidad, clase_aace=clase_aace,
                                 region_proyecto=region or "", params=params, partida=term)
        if not res.get("precio") or res["rango"]["n_fuentes"] == 0:
            return None
        precio = res["precio"] * _FACTOR_UNIDAD.get(term, 1.0)
        return precio, {
            "tipo": "benchmark_multifuente", "metodo": res["metodo"],
            "explicacion": res["explicacion"], "rango": res["rango"],
            "confianza": res["confianza"], "anomalias": res["anomalias"],
        }
    except Exception as e:
        logger.debug(f"benchmark no disponible para {term}: {e}")
        return None


def _precio(term, pais, default, db, *, con_benchmark=False, region=None, clase_aace=None):
    """Resuelve un precio por país; nunca lanza.

    Prioridad: 1) rango multi-fuente de las bases oficiales (si se pide y hay referencias),
    2) catálogo/índice geográfico sobre el precio base de dominio.
    """
    if con_benchmark:
        bm = _precio_benchmark(term, pais, region, db, clase_aace)
        if bm:
            return bm

    from api.domain.precios_base import precio_base
    base = default if (default and float(default) > 0) else precio_base(term)
    try:
        from api.pricing import lookup_price
        return lookup_price(term, pais, default=base, db=db)
    except Exception as e:
        logger.warning(f"precio {term} no resuelto: {e}")
        return float(base or 0.0), {"tipo": "desconocido", "confianza": 0.2}


def calcular_presupuesto_desde_agrupado(agrup, params, db, *, unidades_especificadas,
                                        unidades=None, stats_cad=None, solapes=None):
    """Núcleo determinista: de un `agrup` (por_tipologia/ignoradas/sin_clasificar) YA
    resuelto -- de un solo plano o de varios combinados -- calcula cantidades y coste.

    Devuelve (datos: dict, confianza: float, requiere_revision: bool).
    """
    from api.domain.mapeo_capas import espesor_para, factor_correccion_para, TIPOLOGIAS_SIN_VOLUMEN
    from api.domain.cuantias_acero import cuantia_acero

    solapes = solapes or []
    detalle = []
    supuestos = []
    tot_m3 = tot_acero = tot_encofrado_m2 = tot_impermeab_m2 = 0.0
    es_hidraulica = (params.tipologia_zona or "").strip().lower() == "estructura_hidraulica"
    factores = getattr(params, "factores_correccion", None) or {}

    for tip, g in agrup["por_tipologia"].items():
        factor = factor_correccion_para(tip, factores)
        area_bruta = float(g["area_m2"])
        area = round(area_bruta * factor, 3)
        contribuciones = g.get("contribuciones")  # sólo presente en combinaciones multi-plano
        if tip in TIPOLOGIAS_SIN_VOLUMEN:            # encofrado/impermeabilización: sólo m²
            if tip == "impermeabilizacion":
                tot_impermeab_m2 += area
            else:
                tot_encofrado_m2 += area
            item = {"tipologia": tip, "medida": "m2", "area_m2": round(area, 3),
                    "capas": g["capas"]}
            if factor != 1.0:
                item["factor_correccion"] = factor
                item["area_bruta_m2"] = round(area_bruta, 3)
            if contribuciones is not None:
                item["contribuciones"] = contribuciones
            detalle.append(item)
            continue

        # En una zona hidráulica el hormigón es ESTANCO: si un elemento no trae cuantía
        # propia, manda la de la estructura hidráulica (medida en Riyadh), no la genérica.
        tip_cuantia = tip
        if es_hidraulica and tip in ("muro", "muro piscina", "losa", "cimentacion"):
            tip_cuantia = "estructura_hidraulica"

        canto, es_supuesto = espesor_para(tip, params.espesores_m)
        if not canto:
            item = {"tipologia": tip, "area_m2": round(area, 3), "volumen_m3": None,
                    "aviso": "sin canto/altura: no se puede calcular volumen",
                    "capas": g["capas"]}
            if contribuciones is not None:
                item["contribuciones"] = contribuciones
            detalle.append(item)
            continue
        if es_supuesto:
            supuestos.append({"tipologia": tip, "canto_m": canto,
                              "origen": "predimensionado por defecto (NO viene del plano)"})

        m3 = round(area * canto, 3)
        cu = cuantia_acero(tip_cuantia, (params.cuantias_kg_m3 or {}).get(tip))
        acero_t = round(m3 * cu / 1000.0, 3)
        tot_m3 += m3
        tot_acero += acero_t
        item = {
            "tipologia": tip, "medida": "m3", "area_m2": round(area, 3),
            "canto_m": canto, "canto_es_supuesto": es_supuesto,
            "volumen_m3": m3, "cuantia_kg_m3": cu, "acero_ton": acero_t,
            "capas": g["capas"],
        }
        if factor != 1.0:
            item["factor_correccion"] = factor
            item["area_bruta_m2"] = round(area_bruta, 3)
        if contribuciones is not None:
            item["contribuciones"] = contribuciones
        detalle.append(item)

    # --- valoración por país ---
    # Clase de estimado AACE deducida de la EVIDENCIA que hay de verdad (manual de
    # mediciones, cap. 6): con cantos reales el estimado vale más que con supuestos.
    from api.domain.mediciones import clase_por_evidencia
    clase = clase_por_evidencia(tiene_geometria_cad=True,
                                tiene_cantos_reales=bool(params.espesores_m))

    kw = {"con_benchmark": params.con_benchmark, "region": params.region,
          "clase_aace": clase.clase}
    p_h, f_h = _precio("hormigon", params.pais, params.precio_hormigon_m3, db, **kw)
    p_a, f_a = _precio("acero", params.pais, params.precio_acero_ton, db, **kw)
    p_e, f_e = _precio("encofrado", params.pais, params.precio_encofrado_m2, db, **kw)
    p_i, f_i = _precio("impermeabilizacion", params.pais, None, db, **kw)

    coste_h = round(tot_m3 * p_h, 2)
    coste_a = round(tot_acero * p_a, 2)
    coste_e = round(tot_encofrado_m2 * p_e, 2)
    coste_i = round(tot_impermeab_m2 * p_i, 2)
    total = round(coste_h + coste_a + coste_e + coste_i, 2)

    partidas = [
        {"concepto": "Hormigón", "unidad": "m3", "cantidad": round(tot_m3, 2),
         "precio_unitario": round(p_h, 2), "importe": coste_h, "fuente_precio": f_h},
        {"concepto": "Acero de refuerzo", "unidad": "ton", "cantidad": round(tot_acero, 3),
         "precio_unitario": round(p_a, 2), "importe": coste_a, "fuente_precio": f_a},
        {"concepto": "Encofrado", "unidad": "m2", "cantidad": round(tot_encofrado_m2, 2),
         "precio_unitario": round(p_e, 2), "importe": coste_e, "fuente_precio": f_e},
    ]
    if tot_impermeab_m2 > 0:
        partidas.append({"concepto": "Impermeabilización", "unidad": "m2",
                         "cantidad": round(tot_impermeab_m2, 2),
                         "precio_unitario": round(p_i, 2), "importe": coste_i,
                         "fuente_precio": f_i})

    # Sin precios (ni catálogo ni base) el presupuesto no es utilizable.
    sin_precio = all(p <= 0 for p in (p_h, p_a, p_e))
    requiere = bool(supuestos) or sin_precio or not unidades_especificadas or bool(solapes)

    avisos = []
    if supuestos:
        avisos.append("Se usaron cantos de predimensionado por defecto: el plano 2D no "
                      "contiene el canto/altura. Aporta 'espesores_m' reales para cerrar el "
                      "presupuesto sin supuestos.")
    if sin_precio:
        avisos.append("Sin precios de catálogo para el país ni precios base: el importe es 0. "
                      "Carga el catálogo o pasa precio_hormigon_m3/precio_acero_ton/precio_encofrado_m2.")
    if not unidades_especificadas:
        avisos.append("El plano no declara unidades ($INSUNITS): se asumió metro.")
    if solapes:
        avisos.append(
            "Más de un plano aporta geometría a la(s) tipología(s) "
            f"{', '.join(sorted(solapes))}: puede ser legítimo (elementos distintos en cada "
            "plano) o un doble conteo del mismo elemento. Revisa 'contribuciones' en el "
            "detalle antes de dar el presupuesto por bueno.")
    # Comprobación de dominio: una estructura que retiene agua sin impermeabilización
    # dibujada es una omisión que hay que mirar (no se inventa la medición: se avisa).
    if es_hidraulica and tot_impermeab_m2 <= 0:
        avisos.append("Zona hidráulica sin ninguna capa de impermeabilización en el plano: "
                      "revisa la estanqueidad (no se ha presupuestado impermeabilización).")
        requiere = True

    datos = {
        "zona": params.zona,
        "tipologia_zona": params.tipologia_zona,
        "pais": params.pais,
        "unidades": unidades,
        "detalle_por_tipologia": detalle,
        "totales": {
            "hormigon_m3": round(tot_m3, 2),
            "acero_ton": round(tot_acero, 3),
            "encofrado_m2": round(tot_encofrado_m2, 2),
            "impermeabilizacion_m2": round(tot_impermeab_m2, 2),
        },
        "partidas": partidas,
        "coste_total": total,
        "supuestos": supuestos,
        "avisos": avisos,
        # clase de estimado AACE: qué fiabilidad tiene este presupuesto y por qué
        "clase_estimado": {
            "clase": clase.clase, "metodo": clase.metodo, "uso": clase.uso,
            "exactitud_pct": clase.exactitud,
            "motivo": ("cantos reales aportados" if params.espesores_m
                       else "geometría CAD sin cantos: se usan supuestos"),
        },
        "capas_ignoradas": agrup["ignoradas"],
        "capas_sin_clasificar": agrup["sin_clasificar"],
    }
    if stats_cad is not None:
        datos["stats_cad"] = stats_cad
    if solapes:
        datos["tipologias_solapadas"] = sorted(solapes)

    confianza = 0.9 if not requiere else 0.55
    return datos, confianza, requiere


@register_skill(
    "takeoff_zona_presupuesto",
    cerebro="vision", familia="takeoff",
    descripcion="Del plano CAD al PRESUPUESTO: lee la geometría exacta (DWG/DXF), clasifica las "
                "capas en tipologías estructurales, calcula hormigón (m³), acero (ton) por cuantías "
                "de dominio y encofrado (m²), y los valora con el catálogo de precios del país.",
    tipos_proyecto=["*"],
    capacidad="cad",
    inputs_model=CadPresupuestoInputs,
    outputs_model=None,
    depende_de=["cad_takeoff_geometrico"],
    version="1.0",
)
def takeoff_zona_presupuesto(params: CadPresupuestoInputs, *, db=None, ctx=None) -> SkillResult:
    ctx = ctx or {}
    file_bytes = ctx.get("file_bytes")
    filename = params.filename or ctx.get("filename") or "plano.dxf"
    fuente_base = {"tipo": "skill", "skill": "takeoff_zona_presupuesto"}

    if not file_bytes:
        return SkillResult(datos={"error": "No se recibió archivo CAD (DWG/DXF)"},
                           requiere_revision=True, fuente=fuente_base)

    suffix = os.path.splitext(filename)[1].lower() or ".dxf"
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        from api.cad.converter import to_dxf
        from api.cad.parser import parse_dxf
        from api.domain.mapeo_capas import agrupar_por_tipologia

        dxf_path = to_dxf(tmp_path)
        if dxf_path is None:
            return SkillResult(
                datos={"error": "No se pudo leer/convertir el CAD. Si es DWG falta el conversor "
                                "(dwg2dxf/ODA) en el servidor; el DXF siempre funciona."},
                requiere_revision=True, fuente=fuente_base)

        geo = parse_dxf(dxf_path)
        agrup = agrupar_por_tipologia(geo.get("por_capa") or {})

        datos, confianza, requiere = calcular_presupuesto_desde_agrupado(
            agrup, params, db,
            unidades_especificadas=geo.get("unidades_especificadas", False),
            unidades=geo.get("unidades"), stats_cad=geo.get("stats"))

        return SkillResult(
            datos=datos,
            confianza=confianza,
            requiere_revision=requiere,
            fuente={"tipo": "cad_geometria+dominio+catalogo",
                    "skill": "takeoff_zona_presupuesto",
                    "geometria_exacta": True,
                    "volumen_depende_de_canto": True,
                    "formato": suffix.lstrip(".")},
        )

    except Exception as e:
        logger.warning(f"takeoff_zona_presupuesto falló: {e}")
        return SkillResult(datos={"error": f"No es un CAD legible: {e}"},
                           requiere_revision=True, fuente=fuente_base)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
