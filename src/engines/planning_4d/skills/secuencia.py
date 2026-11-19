"""Skills (cerebro PLANIFICACIÓN) — Secuencia por tipología, Ruta Crítica (CPM) y Curva S (v2.5.0).

Amplifica la planificación determinista con:
  1. `planificacion_secuencia_obra`: secuencia adaptada al módulo (edificación, canales/tuberías,
     EDAR con arranque biológico, Desaladoras con equipos largos y test 72h).
  2. `planificacion_ruta_critica_y_curva_s`: cálculo CPM (ES/EF/LS/LF/holgura), ruta crítica,
     Curva S sectorial ajustada por capítulos y plan de recuperación EVM si CPI/SPI < 0.90.
"""
import logging
import math
from typing import Dict, List, Tuple

from api.schemas.skills_gestion import PlanificacionInputs, RutaCriticaInputs
from api.skills.base import SkillResult, register_skill

logger = logging.getLogger("api.skills.planificacion.secuencia")

_SECUENCIAS_MODULO: Dict[str, List[Tuple[str, str]]] = {
    "canales_tuberias": [
        ("movimiento_tierras", "Replanteo, servicios afectados y excavación en zanja/desmonte"),
        ("cimentaciones", "Cama de apoyo y obra civil de captación/cámaras"),
        ("tuberias_proceso", "Tendido de tuberías / revestimiento de canal"),
        ("equipos", "Valvulería, arquetas, estaciones de bombeo y antiariete"),
        ("electrico", "Telecontrol, SCADA y acometida eléctrica"),
        ("puesta_marcha", "Pruebas hidráulicas UNE-EN 805, limpieza y desinfección"),
    ],
    "depuradoras_edar": [
        ("movimiento_tierras", "Urbanización, movimiento de tierras y cimentaciones"),
        ("estructura", "Obra civil estanca línea de agua y fangos"),
        ("puesta_marcha_civil", "Pruebas de estanqueidad de recintos (EN 12255 / ACI 350)"),
        ("equipos", "Montaje electromecánico línea de agua y fangos"),
        ("tuberias_proceso", "Tuberías de proceso (AISI 316/PRFV) y valvulería"),
        ("electrico", "Instalación eléctrica AT/BT, CCM, ATEX y SCADA"),
        ("puesta_marcha", "Pruebas en vacío, carga hidráulica y arranque biológico (30-90d)"),
    ],
    "desaladoras_idam": [
        ("movimiento_tierras", "Obra marítima: captación marina y emisario submarino"),
        ("estructura", "Obra civil de naves de proceso y depósitos"),
        ("equipos", "Suministro largo y montaje pretratamiento DAF/UF + bastidores RO/ERD"),
        ("tuberias_proceso", "Tuberías alta presión Super Duplex y baja presión PRFV"),
        ("electrico", "Subestación AT/MT/BT, VFD y SCADA"),
        ("puesta_marcha", "Flushing, test de garantías 72h (SEC kWh/m³, TDS, Boro)"),
    ],
}


@register_skill(
    "planificacion_secuencia_obra",
    cerebro="planificacion", familia="cronograma",
    descripcion="Ordena las actividades del proyecto según la secuencia constructiva del módulo "
                "(edificación, canales/tuberías, EDAR o desaladora), estima duraciones por "
                "rendimiento y señala fases omitidas.",
    tipos_proyecto=["*"], capacidad="calculo",
    inputs_model=PlanificacionInputs, version="2.5",
)
def secuencia_obra(p: PlanificacionInputs, *, db=None, ctx=None) -> SkillResult:
    fuente = {"tipo": "skill", "skill": "planificacion_secuencia_obra"}
    if not p.actividades:
        return SkillResult(datos={"error": "No se recibieron actividades"},
                           requiere_revision=True, fuente=fuente)
    from api.domain.mediciones import (SECUENCIA_CONSTRUCCION, fase_de_wbs, orden_fase,
                                       TIPOLOGIA_A_WBS)

    secuencia_ref = _SECUENCIAS_MODULO.get(p.module_id, SECUENCIA_CONSTRUCCION)
    orden_custom = {k: i for i, (k, _) in enumerate(secuencia_ref)}

    plan = []
    fases_con_actividad = set()
    dur_total = 0.0
    for a in p.actividades:
        wbs = a.categoria_wbs or TIPOLOGIA_A_WBS.get(a.descripcion.strip().lower(), "")
        fase = fase_de_wbs(wbs)
        fases_con_actividad.add(fase)
        dias = None
        if a.rendimiento_ud_dia and a.cantidad:
            dias = math.ceil(a.cantidad / a.rendimiento_ud_dia)
            dur_total += dias
        ord_val = orden_custom.get(fase, orden_fase(fase))
        plan.append({"descripcion": a.descripcion, "fase": fase, "orden": ord_val,
                     "cantidad": a.cantidad, "unidad": a.unidad, "duracion_dias": dias})
    plan.sort(key=lambda x: x["orden"])

    faltan = [nombre for clave, nombre in secuencia_ref
              if clave not in fases_con_actividad]
    semanas = round(dur_total / p.dias_laborables_semana, 1) if dur_total else None

    return SkillResult(
        datos={"module_id": p.module_id or "general",
               "plan": plan,
               "duracion_laborable_dias": dur_total or None,
               "duracion_semanas_aprox": semanas,
               "fases_presentes": sorted(fases_con_actividad, key=lambda f: orden_custom.get(f, orden_fase(f))),
               "fases_sin_actividad": faltan,
               "nota": ("Duraciones sólo para actividades con rendimiento; el resto quedan a None."
                        if any(x["duracion_dias"] is None for x in plan) else "")},
        confianza=0.85 if not faltan else 0.65,
        requiere_revision=bool(faltan),
        fuente={**fuente, "tipo": "planificacion_deterministica", "exacto": True})


@register_skill(
    "planificacion_ruta_critica_y_curva_s",
    cerebro="planificacion", familia="cpm_evm",
    descripcion="Calcula la ruta crítica (CPM: ES, EF, LS, LF, holgura total), genera la Curva S "
                "sectorial del módulo y propone acciones de recuperación si CPI/SPI < 0.90.",
    tipos_proyecto=["*"], capacidad="calculo",
    inputs_model=RutaCriticaInputs, version="2.5",
)
def ruta_critica_y_curva_s(p: RutaCriticaInputs, *, db=None, ctx=None) -> SkillResult:
    fuente = {"tipo": "skill", "skill": "planificacion_ruta_critica_y_curva_s"}
    if not p.actividades:
        return SkillResult(datos={"error": "No se recibieron actividades CPM"},
                           requiere_revision=True, fuente=fuente)

    nodos = {a.id: a for a in p.actividades}
    sucesoras: Dict[str, List[str]] = {a.id: [] for a in p.actividades}
    for a in p.actividades:
        for pred in a.predecesoras:
            if pred in sucesoras:
                sucesoras[pred].append(a.id)

    # Orden topológico simple (asume grafo acíclico)
    es: Dict[str, float] = {}
    ef: Dict[str, float] = {}
    for _ in range(len(p.actividades)):
        for a in p.actividades:
            preds = [pr for pr in a.predecesoras if pr in nodos]
            if all(pr in ef for pr in preds):
                start = max((ef[pr] for pr in preds), default=0.0)
                es[a.id] = start
                ef[a.id] = start + float(a.duracion_dias)

    duracion_proyecto = max(ef.values(), default=0.0)
    ls: Dict[str, float] = {}
    lf: Dict[str, float] = {}
    for _ in range(len(p.actividades)):
        for a in reversed(p.actividades):
            succs = sucesoras.get(a.id, [])
            if all(sc in ls for sc in succs):
                finish = min((ls[sc] for sc in succs), default=duracion_proyecto)
                lf[a.id] = finish
                ls[a.id] = finish - float(a.duracion_dias)

    tabla_cpm = []
    ruta_critica = []
    for a in p.actividades:
        holgura = round(ls.get(a.id, 0.0) - es.get(a.id, 0.0), 2)
        critica = abs(holgura) < 1e-6
        if critica:
            ruta_critica.append(a.id)
        tabla_cpm.append({
            "id": a.id, "descripcion": a.descripcion, "duracion_dias": a.duracion_dias,
            "es": round(es.get(a.id, 0.0), 2), "ef": round(ef.get(a.id, 0.0), 2),
            "ls": round(ls.get(a.id, 0.0), 2), "lf": round(lf.get(a.id, 0.0), 2),
            "holgura_dias": holgura, "critica": critica,
        })

    # Curva S sectorial
    curva_s = None
    total_eur = sum(float(a.importe_eur or 0.0) for a in p.actividades)
    if total_eur > 0 and duracion_proyecto > 0:
        try:
            from api.ml_scurve_generator import get_scurve_generator
            meses = max(3, int(math.ceil(duracion_proyecto / 22.0)))
            caps = [{"nombre": a.descripcion, "monto": a.importe_eur} for a in p.actividades if a.importe_eur > 0]
            curva_s = get_scurve_generator().generate_investment_curve(
                {"tipo": p.module_id or "default", "duracion_meses": meses,
                 "presupuesto_total": total_eur, "capitulos": caps},
                num_periods=meses,
            )
        except Exception:
            curva_s = None

    plan_recuperacion = []
    if p.spi_actual is not None and p.spi_actual < 0.90:
        plan_recuperacion.append(
            f"SPI={p.spi_actual:.2f} (<0.90): aplicar fast-tracking o doble turno en actividades críticas {ruta_critica}."
        )
    if p.cpi_actual is not None and p.cpi_actual < 0.90:
        plan_recuperacion.append(
            f"CPI={p.cpi_actual:.2f} (<0.90): renegociar partidas de mayor peso y auditar sobremediciones en campo."
        )

    return SkillResult(
        datos={
            "duracion_total_dias": round(duracion_proyecto, 2),
            "ruta_critica_ids": ruta_critica,
            "actividades_cpm": tabla_cpm,
            "curva_s": curva_s,
            "plan_recuperacion_evm": plan_recuperacion,
        },
        confianza=0.95,
        requiere_revision=bool(plan_recuperacion),
        fuente={**fuente, "tipo": "cpm_deterministico", "exacto": True},
    )
