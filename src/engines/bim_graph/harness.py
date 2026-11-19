"""Harness Determinista y Orquestador Híbrido del Módulo BIM (Capas H2 y H3).

Garantiza que:
1. Todos los volúmenes y áreas provienen de mallas geométricas 3D verificadas (nunca de un LLM).
2. Los defectos de modelado (`sin_qto`, `qto_miente`, `sin_material`, `proxy_ambiguo`,
   `sin_planta`, `duplicado`, `outlier_geometrico`) se auditan con reglas deterministas y
   cerebros ML aislados.
3. Cada elemento se enruta por confianza calibrada hacia `auto` (>= 0.80), `revision`
   (0.55..0.79) o `revision_humana` (< 0.55).
"""
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from api.domain.auditoria_mediciones import auditar_partida_cruzada
from api.domain.bim_ml_brains import (
    NameBrain,
    TipologiaBrain,
    detectar_outliers_geometricos,
    etiqueta_debil_ifc,
    obtener_disciplina,
)
from api.domain.bim_rag import UNIT_FOR_TIPOLOGIA, BimHybridRAG


def validar_calidad_bim(
    elements: List[Dict[str, Any]],
    qto_tol: float = 0.03,
) -> List[Dict[str, Any]]:
    """Aplica los validadores deterministas de integridad sobre la lista de elementos IFC."""
    seen_spatial: Dict[Tuple[Any, ...], str] = {}
    for el in elements:
        flags: List[str] = []
        vol = float(el.get("vol", 0.0))
        decl_vol = el.get("declared_vol")

        if decl_vol is None:
            flags.append("sin_qto")
        elif vol > 0.0 and abs(float(decl_vol) - vol) / vol > qto_tol:
            flags.append("qto_miente")

        if not el.get("material"):
            flags.append("sin_material")
        if el.get("ifc_class") == "IfcBuildingElementProxy":
            flags.append("proxy_ambiguo")
        if not el.get("storey"):
            flags.append("sin_planta")

        spatial_key = (
            el.get("ifc_class"),
            tuple(np.round(el.get("bmin", [0.0, 0.0, 0.0]), 2)),
            tuple(np.round(el.get("bmax", [0.0, 0.0, 0.0]), 2)),
        )
        if spatial_key in seen_spatial:
            flags.append("duplicado")
            el["dup_of"] = seen_spatial[spatial_key]
        else:
            seen_spatial[spatial_key] = el["gid"]

        el["flags"] = flags
    return elements


def calcular_bucket_dimensional(tipologia: str, element: Dict[str, Any]) -> int:
    """Calcula el bucket dimensional en centímetros (espesor, sección o canto) desde el sólido 3D."""
    d1 = float(element.get("d1", 0.0))
    d2 = float(element.get("d2", 0.0))
    d3 = float(element.get("d3", 0.0))

    if tipologia in ("muro_hormigon", "tabique", "losa", "muro_cortina", "revestimiento"):
        return max(1, int(round(d1 * 100)))
    if tipologia == "pilar":
        return max(1, int(round(min(d1, d2) * 100)))
    if tipologia == "viga":
        if d3 > 2.0:
            return max(1, int(round(max(d1, d2) * 100)))
        return max(1, int(round(d1 * 100)))
    if tipologia == "zapata":
        return 180
    if tipologia in ("puerta", "ventana"):
        width_m = d2 if 0.5 <= d2 <= 2.5 else d1
        return max(60, int(round(width_m * 100)))
    if tipologia in ("tuberia", "accesorio_tuberia", "tuberia_presion", "valvula_hidraulica"):
        psets = element.get("psets") or {}
        if psets.get("nominal_diameter"):
            return int(round(psets["nominal_diameter"]))
        diam_m = min(d1, d2)
        return max(20, int(round(diam_m * 1000)))
    if tipologia == "canal_hidraulico":
        return 30
    if tipologia == "deposito_reactor":
        return 40
    if tipologia == "arqueta_camara":
        return 120
    if tipologia == "bomba_equipo_proceso":
        return 110
    if tipologia == "bastidor_osmosis":
        return 100
    if tipologia == "conducto_clima":
        return max(15, int(round(d2 * 100)))
    if tipologia == "bandeja_cables":
        return max(10, int(round(d2 * 100)))
    if tipologia == "cubierta":
        return 30
    if tipologia == "escalera":
        return 120
    if tipologia in ("luminaria", "difusor_rejilla"):
        return 60
    if tipologia in ("cuadro_electrico", "equipo_clima"):
        return int(round(d3 * 100)) if d3 < 2.0 else 80
    if tipologia == "aparato_sanitario":
        return 40
    return max(1, int(round(d1 * 100)))


def ejecutar_harness_bim(
    elements: List[Dict[str, Any]],
    *,
    tipo_brain: Optional[TipologiaBrain] = None,
    name_brain: Optional[NameBrain] = None,
    rag: Optional[BimHybridRAG] = None,
    use_ml: bool = True,
    use_rerank: bool = True,
    auto_threshold: float = 0.80,
    review_threshold: float = 0.55,
    incluir_duplicados: bool = False,
    incluir_outliers: bool = False,
) -> Dict[str, Any]:
    """Ejecuta el pipeline mixto completo (Harness + Cerebros ML + RAG Estructurado).

    Devuelve los resultados por `GlobalId`, el resumen de rutas de confianza, las anomalías
    detectadas, el presupuesto desglosado por partida y las líneas de medición analíticas.
    """
    if tipo_brain is None and use_ml:
        tipo_brain = TipologiaBrain()
        tipo_brain.fit(elements)
    if name_brain is None and use_ml:
        name_brain = NameBrain()
        name_brain.fit(elements)
    if rag is None:
        rag = BimHybridRAG()

    # 1. Inferencia de tipología y detección de outliers geométricos (M1 + M2)
    predicted_tips: List[str] = []
    tip_probs: List[float] = []
    for el in elements:
        if el.get("ifc_class") != "IfcBuildingElementProxy":
            t = etiqueta_debil_ifc(el)
            p = 1.0 if t else 0.0
            if not t and use_ml and name_brain is not None and el.get("name"):
                t, p = name_brain.predict(el["name"])
        elif use_ml and tipo_brain is not None:
            t, p = tipo_brain.predict(el)
        else:
            t, p = None, 0.0
        predicted_tips.append(t or "")
        tip_probs.append(float(p))

    outlier_gids = detectar_outliers_geometricos(elements, predicted_tips) if use_ml else set()
    for el in elements:
        if el["gid"] in outlier_gids and "outlier_geometrico" not in el.get("flags", []):
            el.setdefault("flags", []).append("outlier_geometrico")

    # 2. Emparejamiento RAG con caché por tupla (tipología, material, bucket_cm) y enrutado
    qcache: Dict[Tuple[str, str, int, bool], List[Tuple[int, float]]] = {}
    results_by_gid: Dict[str, Dict[str, Any]] = {}
    audit_trail: List[Dict[str, Any]] = []
    escalated_count = 0

    partidas_auto: Dict[str, float] = defaultdict(float)
    partidas_auto_rev: Dict[str, float] = defaultdict(float)
    partidas_meta: Dict[str, Dict[str, Any]] = {}
    mediciones_analiticas: List[Dict[str, Any]] = []

    for el, tip, tp in zip(elements, predicted_tips, tip_probs):
        gid = el["gid"]
        flags = el.get("flags", [])
        is_dup = "duplicado" in flags
        is_out = gid in outlier_gids or "outlier_geometrico" in flags

        if not tip:
            results_by_gid[gid] = {
                "gid": gid,
                "tip": None,
                "bucket_cm": None,
                "code": None,
                "desc": None,
                "unit": None,
                "price": 0.0,
                "conf": 0.0,
                "route": "revision_humana",
                "tip_prob": 0.0,
                "flags": flags,
                "excluded": is_dup or is_out,
            }
            escalated_count += 1
            continue

        bk = calcular_bucket_dimensional(tip, el)
        mat = el.get("material") or ""
        cache_key = (tip, mat, bk, use_rerank)
        if cache_key not in qcache:
            qcache[cache_key] = rag.retrieve(tip, mat, bk, rerank=use_rerank)
        hits = qcache[cache_key]
        best_idx, best_score = hits[0]
        margin = best_score - (hits[1][1] if len(hits) > 1 else 0.0)

        q_ok = 1.0 if "sin_material" not in flags else 0.70
        conf = min(
            1.0,
            (
                0.55 * min(1.0, best_score / 0.75)
                + 0.25 * tp
                + 0.20 * min(1.0, margin * 8.0)
            )
            * q_ok,
        )
        if conf >= auto_threshold:
            route = "auto"
        elif conf >= review_threshold:
            route = "revision"
        else:
            route = "revision_humana"

        if route != "auto":
            escalated_count += 1

        cat_item = rag.cat[best_idx]
        code = cat_item["code"]
        unit = cat_item["unit"]
        price = float(cat_item["price"])
        disciplina = obtener_disciplina(tip)

        if unit == "m3":
            qty_val = float(el.get("vol", 0.0))
        elif unit == "m2":
            qty_val = float(el.get("area", 0.0))
        elif unit == "m":
            qty_val = float(el.get("d3", 0.0))
        elif unit == "ud":
            qty_val = 1.0
        else:
            qty_val = float(el.get("vol", 0.0))

        excluded = (is_dup and not incluir_duplicados) or (is_out and not incluir_outliers)

        results_by_gid[gid] = {
            "gid": gid,
            "tip": tip,
            "disciplina": disciplina,
            "bucket_cm": bk,
            "code": code,
            "desc": cat_item["desc"],
            "unit": unit,
            "price": price,
            "qty": round(qty_val, 4),
            "importe_eur": round(qty_val * price, 2),
            "conf": round(conf, 4),
            "route": route,
            "tip_prob": round(tp, 4),
            "flags": flags,
            "excluded": excluded,
        }
        audit_trail.append({
            "gid": gid,
            "name": el.get("name", ""),
            "storey": el.get("storey"),
            "tipologia": tip,
            "disciplina": disciplina,
            "bucket_cm": bk,
            "codigo_partida": code,
            "unidad": unit,
            "cantidad": round(qty_val, 4),
            "confianza": round(conf, 3),
            "ruta": route,
            "flags": flags,
            "excluido": excluded,
        })

        if not excluded:
            partidas_meta[code] = {
                "codigo": code,
                "descripcion": cat_item["desc"],
                "unidad": unit,
                "precio_unitario": price,
                "tipologia": tip,
                "disciplina": disciplina,
                "bucket_cm": bk,
            }
            if route == "auto":
                partidas_auto[code] += qty_val
            if route in ("auto", "revision"):
                partidas_auto_rev[code] += qty_val

            mediciones_analiticas.append({
                "global_id": gid,
                "sub_zona": el.get("storey") or "Sin Planta",
                "codigo_partida": code,
                "tipologia": tip,
                "disciplina": disciplina,
                "descripcion_detalle": f"{el.get('name') or tip} [{gid}] ({bk} cm)",
                "unidad": unit,
                "unidades_iguales": 1.0,
                "largo": round(float(el.get("d3", 0.0)), 3),
                "ancho": round(float(el.get("d2", 0.0)), 3),
                "alto": round(float(el.get("d1", 0.0)), 3),
                "subtotal": round(qty_val, 4),
                "confianza": round(conf, 3),
                "ruta": route,
            })

    # 3. Resumen de partidas y PEM
    resumen_partidas: List[Dict[str, Any]] = []
    pem_auto_eur = 0.0
    pem_auto_revision_eur = 0.0
    pem_por_disciplina: Dict[str, float] = defaultdict(float)
    elementos_por_disciplina: Dict[str, int] = defaultdict(int)

    for r in results_by_gid.values():
        if not r["excluded"] and r.get("disciplina"):
            elementos_por_disciplina[r["disciplina"]] += 1

    for code in sorted(partidas_meta.keys()):
        meta = partidas_meta[code]
        q_auto = round(partidas_auto.get(code, 0.0), 3)
        q_all = round(partidas_auto_rev.get(code, 0.0), 3)
        pu = float(meta["precio_unitario"])
        imp_auto = round(q_auto * pu, 2)
        imp_all = round(q_all * pu, 2)
        pem_auto_eur += imp_auto
        pem_auto_revision_eur += imp_all
        disc = meta.get("disciplina", "ESTRUCTURAS")
        pem_por_disciplina[disc] += imp_all
        resumen_partidas.append({
            **meta,
            "cantidad_auto": q_auto,
            "importe_auto_eur": imp_auto,
            "cantidad_auto_mas_revision": q_all,
            "importe_auto_mas_revision_eur": imp_all,
        })

    total_pem = pem_auto_revision_eur or 1.0
    resumen_disciplinas = [
        {
            "disciplina": disc,
            "elementos": elementos_por_disciplina.get(disc, 0),
            "pem_eur": round(pem_por_disciplina.get(disc, 0.0), 2),
            "porcentaje_pem": round((pem_por_disciplina.get(disc, 0.0) / total_pem) * 100.0, 1),
        }
        for disc in sorted(set(list(elementos_por_disciplina.keys()) + list(pem_por_disciplina.keys())))
    ]

    defect_counts = dict(Counter(f for el in elements for f in el.get("flags", [])))
    route_counts = dict(Counter(r["route"] for r in results_by_gid.values()))

    return {
        "results_by_gid": results_by_gid,
        "audit_trail": audit_trail,
        "resumen_partidas": resumen_partidas,
        "mediciones_analiticas": mediciones_analiticas,
        "pem_auto_eur": round(pem_auto_eur, 2),
        "pem_auto_mas_revision_eur": round(pem_auto_revision_eur, 2),
        "pem_por_disciplina": dict(pem_por_disciplina),
        "elementos_por_disciplina": dict(elementos_por_disciplina),
        "resumen_disciplinas": resumen_disciplinas,
        "conteo_defectos": defect_counts,
        "conteo_rutas": route_counts,
        "escalados_a_revision": escalated_count,
        "outliers_detectados": sorted(outlier_gids),
        "consultas_rag_unicas": len(qcache),
    }


def auditar_modelo_bim_contra_pliego(
    harness_output: Dict[str, Any],
    partidas_oficiales: List[Dict[str, Any]],
    *,
    umbral_pct: float = 3.0,
    usar_auto_mas_revision: bool = True,
) -> Dict[str, Any]:
    """Cruza las mediciones 3D verificadas por el Harness BIM contra las partidas de un
    presupuesto oficial empleando el motor real `auditar_partida_cruzada`."""
    medidas_bim: Dict[str, float] = {}
    meta_bim: Dict[str, Dict[str, Any]] = {}
    for p in harness_output.get("resumen_partidas", []):
        code = p["codigo"]
        qty = p["cantidad_auto_mas_revision"] if usar_auto_mas_revision else p["cantidad_auto"]
        medidas_bim[code] = float(qty)
        meta_bim[code] = p

    resultados_auditoria: List[Dict[str, Any]] = []
    codigos_oficiales = set()
    impacto_neto_total = 0.0

    for item in partidas_oficiales:
        code = item["codigo"]
        codigos_oficiales.add(code)
        unidad = item.get("unidad") or meta_bim.get(code, {}).get("unidad", "m3")
        resumen = item.get("resumen") or meta_bim.get(code, {}).get("descripcion", "")
        cant_oficial = float(item.get("cantidad_oficial", 0.0))
        pu = float(item.get("precio_unitario") or meta_bim.get(code, {}).get("precio_unitario", 0.0))
        cant_bim = float(medidas_bim.get(code, 0.0))

        aud = auditar_partida_cruzada(
            code,
            resumen,
            unidad,
            cant_oficial,
            cant_bim,
            pu,
            umbral_pct=umbral_pct,
        )
        impacto_neto_total += float(aud.get("impacto_economico_eur", 0.0))
        resultados_auditoria.append(aud)

    # Detectar partidas presentes en el modelo BIM 3D pero omitidas por completo en la lista oficial
    for code, cant_bim in sorted(medidas_bim.items()):
        if code not in codigos_oficiales and cant_bim > 0:
            meta = meta_bim[code]
            pu = float(meta.get("precio_unitario", 0.0))
            aud = auditar_partida_cruzada(
                code,
                meta.get("descripcion", ""),
                meta.get("unidad", "m3"),
                0.0,
                cant_bim,
                pu,
                umbral_pct=umbral_pct,
            )
            impacto_neto_total += float(aud.get("impacto_economico_eur", 0.0))
            resultados_auditoria.append(aud)

    conteo_clasificacion = dict(Counter(r["clasificacion"] for r in resultados_auditoria))
    return {
        "total_partidas_auditadas": len(resultados_auditoria),
        "conteo_clasificacion": conteo_clasificacion,
        "impacto_economico_neto_eur": round(impacto_neto_total, 2),
        "partidas": resultados_auditoria,
    }


_CUANTIA_ACERO_KG_M3: Dict[str, float] = {
    "zapata": 70.0,
    "losa": 95.0,
    "muro_hormigon": 100.0,
    "pilar": 140.0,
    "viga": 135.0,
    "canal_hidraulico": 105.0,
    "deposito_reactor": 125.0,
}

_RATIO_ENCOFRADO_M2_M3: Dict[str, float] = {
    "zapata": 2.2,
    "losa": 3.5,
    "muro_hormigon": 7.5,
    "pilar": 10.5,
    "viga": 8.0,
    "canal_hidraulico": 6.5,
    "deposito_reactor": 5.5,
}


def derivar_partidas_implicitas_bim(
    elements: List[Dict[str, Any]],
    harness_output: Dict[str, Any],
    tipo_terreno: str = "tierra",
) -> List[Dict[str, Any]]:
    """Deriva determinísticamente Encofrado (m²), Acero B-500S (kg) y Excavación (m³) desde el modelo 3D."""
    results_by_gid = harness_output.get("results_by_gid") or {}
    encofrado_m2 = 0.0
    acero_kg = 0.0

    for gid, r in results_by_gid.items():
        if r.get("excluded"):
            continue
        tip = r.get("tip", "")
        vol = float(r.get("qty", 0.0)) if r.get("unit") == "m3" else 0.0
        if vol <= 0:
            continue
        if tip in _CUANTIA_ACERO_KG_M3:
            acero_kg += vol * _CUANTIA_ACERO_KG_M3[tip]
        if tip in _RATIO_ENCOFRADO_M2_M3:
            encofrado_m2 += vol * _RATIO_ENCOFRADO_M2_M3[tip]

    from src.engines.planning_4d.cpm_engine import estimar_movimiento_tierras_desde_bim
    mt = estimar_movimiento_tierras_desde_bim(elements, tipo_terreno=tipo_terreno)

    return [
        {
            "codigo": "IMP-ENC",
            "descripcion": "Encofrado y desencofrado de elementos estructurales e hidráulicos (derivado 3D)",
            "unidad": "m2",
            "cantidad": round(encofrado_m2, 2),
            "criterio": "Superficie de contacto estimada por tipología y espesor",
        },
        {
            "codigo": "IMP-ACE",
            "descripcion": "Acero corrugado B-500S elaborado y colocado (cuantías medias normativas)",
            "unidad": "kg",
            "cantidad": round(acero_kg, 2),
            "criterio": "Cuantías kg/m³ por tipología estructural (EHE/CE)",
        },
        {
            "codigo": "IMP-EXC",
            "descripcion": f"Excavación en desmonte y zanjas bajo rasante (terreno {tipo_terreno})",
            "unidad": "m3",
            "cantidad": round(mt["vol_excavacion_neto_m3"], 2),
            "criterio": "Huella bmin/bmax + zmin < 0 con sobreancho y talud",
        },
        {
            "codigo": "IMP-REL",
            "descripcion": "Relleno trasdós y compactación por tongadas (derivado 3D)",
            "unidad": "m3",
            "cantidad": round(mt["vol_relleno_compactado_m3"], 2),
            "criterio": "Volumen excavado menos hormigón bajo rasante × compactación",
        },
    ]


def extraer_defectos_iso19650(harness_output: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extrae el listado detallado de elementos BIM con defectos de calidad ISO 19650."""
    defectos = []
    for row in harness_output.get("audit_trail", []):
        flags = row.get("flags") or []
        if flags:
            defectos.append({
                "global_id": row.get("gid"),
                "nombre": row.get("name") or row.get("tipologia"),
                "nivel": row.get("storey") or "Sin Planta",
                "disciplina": row.get("disciplina"),
                "tipologia": row.get("tipologia"),
                "flags": flags,
                "ruta": row.get("ruta"),
                "excluido": bool(row.get("excluido")),
            })
    return defectos
