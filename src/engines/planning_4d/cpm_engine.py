"""Planificador Determinista de Ejecución para Obra Civil y Movimiento de Tierras (v2.5.0).

Convierte geometría BIM 3D (`zmin`, `bmin/bmax`, `vol`, `area`, `tipologia`) o mediciones CAD/manuales
en un cronograma PDM/CPM ejecutable:
  1. Deduce el volumen geométrico de excavación y relleno cuando no viene explícito.
  2. Genera la red de precedencias físicas (PDM) con lags de curado/estanqueidad.
  3. Calcula duración por rendimiento y auto-ajusta el nº de frentes si supera el plazo objetivo.
  4. Resuelve la ruta crítica (ES, EF, LS, LF, holgura) y devuelve tareas listas para Gantt y EVM.
"""
import math
from typing import Any, Dict, List, Optional

from src.engines.planning_4d.rendimientos import FACTORES_TERRENO, RENDIMIENTOS_OBRA_CIVIL


def estimar_movimiento_tierras_desde_bim(
    elements: List[Dict[str, Any]],
    tipo_terreno: str = "tierra",
    cota_terreno_m: float = 0.0,
) -> Dict[str, float]:
    """Deduce excavación, entibación y relleno trasdós desde cotas `zmin` y `bmin/bmax` del IFC."""
    f = FACTORES_TERRENO.get(tipo_terreno, FACTORES_TERRENO["tierra"])
    sobre = f["sobreancho_m"]
    talud = f["talud_h_v"]

    area_desbroce = 0.0
    vol_exc_neto = 0.0
    vol_hormigon_bajo_rasante = 0.0
    area_entibacion = 0.0

    for el in elements or []:
        bmin = el.get("bmin") or [0.0, 0.0, 0.0]
        bmax = el.get("bmax") or [0.0, 0.0, 0.0]
        dx = max(0.0, float(bmax[0]) - float(bmin[0]))
        dy = max(0.0, float(bmax[1]) - float(bmin[1]))
        zmin = float(el.get("zmin", bmin[2]))
        vol_el = float(el.get("vol", 0.0))

        huella = dx * dy
        area_desbroce += huella

        if zmin < cota_terreno_m - 0.05:
            prof = min(12.0, cota_terreno_m - zmin)
            ancho_base = dx + 2.0 * sobre
            largo_base = dy + 2.0 * sobre
            ancho_top = ancho_base + 2.0 * talud * prof
            largo_top = largo_base + 2.0 * talud * prof
            vol_tronco = prof * ((ancho_base * largo_base) + (ancho_top * largo_top)) / 2.0
            vol_exc_neto += vol_tronco
            vol_hormigon_bajo_rasante += vol_el
            if prof >= 1.50:
                area_entibacion += 2.0 * (ancho_base + largo_base) * prof

    vol_esponjado = vol_exc_neto * f["esponjamiento"]
    vol_relleno = max(0.0, (vol_exc_neto - vol_hormigon_bajo_rasante) * f["compactacion"])
    return {
        "area_desbroce_m2": round(area_desbroce, 2),
        "vol_excavacion_neto_m3": round(vol_exc_neto, 2),
        "vol_excavacion_esponjado_m3": round(vol_esponjado, 2),
        "area_entibacion_m2": round(area_entibacion, 2),
        "vol_relleno_compactado_m3": round(vol_relleno, 2),
    }


def generar_planificacion_obra_civil(
    *,
    elements: Optional[List[Dict[str, Any]]] = None,
    mediciones_manuales: Optional[Dict[str, float]] = None,
    tipo_terreno: str = "tierra",
    nivel_freatico: bool = False,
    plazo_objetivo_dias: Optional[int] = None,
    max_frentes: int = 3,
    module_id: str = "edificacion",
) -> Dict[str, Any]:
    """Genera el cronograma PDM/CPM completo de Obra Civil y Movimiento de Tierras."""
    elements = elements or []
    manual = mediciones_manuales or {}
    f_terreno = FACTORES_TERRENO.get(tipo_terreno, FACTORES_TERRENO["tierra"])

    mt = estimar_movimiento_tierras_desde_bim(elements, tipo_terreno=tipo_terreno)

    # Clasificar volúmenes de obra civil desde BIM
    vol_cim = 0.0
    vol_muros = 0.0
    vol_canales = 0.0
    for el in elements:
        tip = (el.get("tipologia") or el.get("ifc_class") or "").lower()
        vol = float(el.get("vol", 0.0))
        if any(k in tip for k in ("zapata", "footing", "pile", "losa", "slab")):
            vol_cim += vol
        elif any(k in tip for k in ("canal", "arqueta", "chamber")):
            vol_canales += vol
        elif any(k in tip for k in ("muro", "wall", "tank", "deposito", "reactor", "pilar", "column", "viga", "beam")):
            vol_muros += vol

    # Permitir sobreescritura/complemento desde mediciones explícitas de planos/presupuesto
    area_desbroce = float(manual.get("area_desbroce_m2", mt["area_desbroce_m2"])) or 500.0
    vol_exc = float(manual.get("vol_excavacion_m3", mt["vol_excavacion_neto_m3"])) or 350.0
    area_entib = float(manual.get("area_entibacion_m2", mt["area_entibacion_m2"]))
    vol_cim = float(manual.get("vol_cimentacion_m3", vol_cim)) or 120.0
    vol_limpieza = round(max(10.0, vol_cim * 0.12), 2)
    vol_muros = float(manual.get("vol_muros_m3", vol_muros))
    vol_canales = float(manual.get("vol_canales_m3", vol_canales))
    vol_relleno = float(manual.get("vol_relleno_m3", mt["vol_relleno_compactado_m3"])) or round(vol_exc * 0.45, 2)

    # Construcción de actividades PDM base
    tareas_def = [
        ("OC-01", "Desbroce, limpieza y replanteo topográfico", "desbroce_y_replanteo", area_desbroce, []),
        ("OC-02", f"Excavación en desmonte y zanjas ({tipo_terreno})", "excavacion_tierras", vol_exc, ["OC-01"]),
    ]
    preds_limpieza = ["OC-02"]
    if area_entib > 0 or nivel_freatico:
        med_entib = area_entib if area_entib > 0 else round(vol_exc * 0.25, 2)
        tareas_def.append(("OC-03", "Entibación de zanjas y agotamiento freático" if nivel_freatico else "Entibación de zanjas",
                           "entibacion_y_agotamiento", med_entib, ["OC-02"]))
        preds_limpieza = ["OC-03"]

    tareas_def.append(("OC-04", "Refino de fondo y hormigón de limpieza HL-150", "hormigon_limpieza", vol_limpieza, preds_limpieza))
    tareas_def.append(("OC-05", "Cimentación: ferralla, encofrado y hormigón (zapatas/losas)", "cimentacion_zapatas_losas", vol_cim, ["OC-04"]))

    preds_relleno = ["OC-05"]
    if vol_muros > 0:
        tareas_def.append(("OC-06", "Alzados y muros estructurales / hidráulicos", "muros_y_estructuras_hidraulicas", vol_muros, ["OC-05"]))
        preds_relleno = ["OC-06"]
    if vol_canales > 0:
        tareas_def.append(("OC-07", "Hormigonado de canales, cámaras y arquetas", "canales_y_arquetas", vol_canales, ["OC-05"]))
        preds_relleno.append("OC-07")

    if module_id in ("depuradoras_edar", "desaladoras_idam", "canales_tuberias") or vol_canales > 0:
        tareas_def.append(("OC-08", "Pruebas hidráulicas de estanqueidad (EN 12255 / UNE-EN 805)", "pruebas_estanqueidad", 1.0, list(preds_relleno)))
        preds_relleno = ["OC-08"]

    tareas_def.append(("OC-09", "Relleno trasdós y compactación por tongadas", "relleno_y_compactacion", vol_relleno, preds_relleno))

    def _resolver_cpm(n_frentes: int) -> Dict[str, Any]:
        nodos = []
        for tid, nombre, clave_rend, med, preds in tareas_def:
            r_info = RENDIMIENTOS_OBRA_CIVIL[clave_rend]
            rend = float(r_info["rendimiento_dia"])
            if clave_rend == "excavacion_tierras":
                rend *= f_terreno["factor_rendimiento_exc"]
                if nivel_freatico:
                    rend *= 0.80
            frentes_eff = 1 if clave_rend == "pruebas_estanqueidad" else max(1, n_frentes)
            dur = max(1, math.ceil(med / max(1e-6, rend * frentes_eff)))
            nodos.append({
                "id": tid,
                "nombre": nombre,
                "clave": clave_rend,
                "medicion": round(med, 2),
                "unidad": r_info["unidad"],
                "equipo": r_info["equipo"],
                "frentes": frentes_eff,
                "operarios_totales": int(r_info.get("operarios", 4)) * frentes_eff,
                "maquinas_totales": int(r_info.get("maquinas", 1)) * frentes_eff,
                "duracion_dias": dur,
                "lag_post_dias": int(r_info["lag_dias"]),
                "predecesoras": preds,
            })

        by_id = {t["id"]: t for t in nodos}
        succs: Dict[str, List[str]] = {t["id"]: [] for t in nodos}
        for t in nodos:
            for pr in t["predecesoras"]:
                if pr in succs:
                    succs[pr].append(t["id"])

        for t in nodos:
            es = max((by_id[pr]["ef"] + by_id[pr]["lag_post_dias"] for pr in t["predecesoras"] if pr in by_id), default=0)
            t["es"] = es
            t["ef"] = es + t["duracion_dias"]

        dur_total = max((t["ef"] for t in nodos), default=0)
        for t in reversed(nodos):
            s_list = succs[t["id"]]
            lf = min((by_id[s]["ls"] - t["lag_post_dias"] for s in s_list), default=dur_total)
            t["lf"] = lf
            t["ls"] = lf - t["duracion_dias"]
            t["holgura_dias"] = t["ls"] - t["es"]
            t["critica"] = t["holgura_dias"] == 0

        return {"duracion_total_dias": dur_total, "nodos": nodos}

    frentes_usados = 1
    sol = _resolver_cpm(frentes_usados)
    if plazo_objetivo_dias and sol["duracion_total_dias"] > plazo_objetivo_dias:
        for nf in range(2, max(2, max_frentes) + 1):
            frentes_usados = nf
            sol = _resolver_cpm(nf)
            if sol["duracion_total_dias"] <= plazo_objetivo_dias:
                break

    ruta_critica = [t["id"] for t in sol["nodos"] if t["critica"]]
    cumple_plazo = True if not plazo_objetivo_dias else sol["duracion_total_dias"] <= plazo_objetivo_dias

    # Histograma semanal de recursos (PMI / AACE 14R-90)
    n_semanas = max(1, math.ceil(sol["duracion_total_dias"] / 5.0))
    histograma_recursos = []
    for sem in range(1, n_semanas + 1):
        d_ini = (sem - 1) * 5
        d_fin = sem * 5
        activas = [t for t in sol["nodos"] if t["es"] < d_fin and t["ef"] > d_ini]
        histograma_recursos.append({
            "semana": sem,
            "operarios": sum(t["operarios_totales"] for t in activas),
            "maquinas": sum(t["maquinas_totales"] for t in activas),
            "actividades_activas": [t["id"] for t in activas],
        })

    # Análisis de restricciones Lookahead (Last Planner System - LPS)
    restricciones_lps = [
        {"codigo": "LPS-01", "categoria": "Ingeniería / Planos", "descripcion": "Planos de replanteo y cimentación aprobados para construcción", "critica": True},
        {"codigo": "LPS-02", "categoria": "Seguridad y Permisos", "descripcion": "Acta de replanteo, Plan de Seguridad y Salud y apertura centro trabajo", "critica": True},
        {"codigo": "LPS-03", "categoria": "Servicios Afectados", "descripcion": "Calicatas de localización de conducciones eléctricas, gas y fibra", "critica": True},
    ]
    if nivel_freatico:
        restricciones_lps.append({"codigo": "LPS-04", "categoria": "Geotecnia / Agua", "descripcion": "Permiso de vertido de agotamiento freático y equipos wellpoint en obra", "critica": True})
    if module_id in ("canales_tuberias", "depuradoras_edar", "desaladoras_idam"):
        restricciones_lps.append({"codigo": "LPS-05", "categoria": "Calidad / Suministros", "descripcion": "Aprobación técnica de juntas waterstop / tuberías DN y plan de ensayos", "critica": False})

    return {
        "module_id": module_id,
        "tipo_terreno": tipo_terreno,
        "nivel_freatico": nivel_freatico,
        "frentes_asignados": frentes_usados,
        "duracion_total_dias": sol["duracion_total_dias"],
        "duracion_semanas_aprox": round(sol["duracion_total_dias"] / 5.0, 1),
        "plazo_objetivo_dias": plazo_objetivo_dias,
        "cumple_plazo": cumple_plazo,
        "ruta_critica_ids": ruta_critica,
        "pico_operarios": max((h["operarios"] for h in histograma_recursos), default=0),
        "pico_maquinas": max((h["maquinas"] for h in histograma_recursos), default=0),
        "histograma_recursos": histograma_recursos,
        "restricciones_lookahead": restricciones_lps,
        "movimiento_tierras_derivado": mt,
        "actividades": sol["nodos"],
    }
