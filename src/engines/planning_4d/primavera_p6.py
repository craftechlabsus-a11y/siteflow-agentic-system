"""Interoperabilidad bidireccional con Oracle Primavera P6 y MS Project (v2.5.0).

Soporta tres vías complementarias para departamentos de planificación:
  1. Exportación nativa Primavera P6 `.xer` (ERMHDR, CURRTYPE, PROJECT, CALENDAR, PROJWBS, TASK, TASKPRED, RSRC, TASKRSRC).
  2. Exportación `.xml` MSPDI (compatible con Primavera P6 Import XML y MS Project).
  3. Importación/parseo determinista de archivos `.xer` exportados desde Primavera P6.
"""
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from xml.sax.saxutils import escape


def _add_workdays(start: datetime, workdays: int) -> datetime:
    """Suma días laborables (L-V) a una fecha base."""
    cur = start
    added = 0
    while added < max(0, int(workdays)):
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            added += 1
    return cur


def _fmt_p6_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M")


def _fmt_iso_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def exportar_primavera_xer(
    plan: Dict[str, Any],
    project_name: str = "SiteFlow_Pro_Obra",
    start_date_iso: Optional[str] = None,
) -> str:
    """Genera un archivo `.xer` válido para importación directa en Oracle Primavera P6."""
    base_dt = datetime.fromisoformat(start_date_iso) if start_date_iso else datetime(2026, 1, 5, 8, 0)
    proj_id = "1001"
    wbs_id = "2001"
    cal_id = "3001"
    curr_id = "1"

    actividades = plan.get("actividades") or []
    dur_total = int(plan.get("duracion_total_dias") or 1)
    proj_end = _add_workdays(base_dt, dur_total)

    lines: List[str] = [
        f"ERMHDR\t20.12\t{datetime.now().strftime('%Y-%m-%d')}\tProject\tSiteFlowPro\tSiteFlow\tEUR",
        "%T\tCURRTYPE",
        "%F\tcurr_id\tdecimal_digit_cnt\tcurr_symbol\tdecimal_symbol\tdigit_group_symbol\tpos_curr_fmt_type\tneg_curr_fmt_type\tcurr_type\tcurr_short_name\tgroup_digit_cnt\tbase_exch_rate",
        f"%R\t{curr_id}\t2\t€\t,\t.\t#1.1\t(#1.1)\tEuro\tEUR\t3\t1.0",
        "%T\tCALENDAR",
        "%F\tclndr_id\tdefault_flag\tclndr_name\tproj_id\tbase_clndr_id\tlast_chng_date\tclndr_type\tday_hr_cnt\tweek_hr_cnt\tmonth_hr_cnt\tyear_hr_cnt",
        f"%R\t{cal_id}\tY\tEstandar_5d_8h\t\t\t{_fmt_p6_dt(base_dt)}\tCA_Base\t8.0\t40.0\t172.0\t2080.0",
        "%T\tPROJECT",
        "%F\tproj_id\tproj_short_name\torig_proj_id\tclndr_id\tplan_start_date\tplan_end_date\tsum_base_proj_id\tlast_recalc_date",
        f"%R\t{proj_id}\t{project_name[:20]}\t\t{cal_id}\t{_fmt_p6_dt(base_dt)}\t{_fmt_p6_dt(proj_end)}\t\t{_fmt_p6_dt(base_dt)}",
        "%T\tPROJWBS",
        "%F\twbs_id\tproj_id\tobs_id\tseq_num\test_wt\tproj_node_flag\tsum_data_flag\tstatus_code\twbs_short_name\twbs_name\tparent_wbs_id",
        f"%R\t{wbs_id}\t{proj_id}\t\t10\t1\tY\tN\tWS_Open\tSFPRO\t{project_name}\t",
        "%T\tTASK",
        "%F\ttask_id\tproj_id\twbs_id\tclndr_id\ttask_code\ttask_name\ttask_type\tduration_type\tstatus_code\ttarget_drtn_hr_cnt\tremain_drtn_hr_cnt\tearly_start_date\tearly_end_date\tlate_start_date\tlate_end_date\ttotal_float_hr_cnt",
    ]

    task_id_by_code: Dict[str, int] = {}
    for idx, act in enumerate(actividades, start=1):
        tid = 10000 + idx
        code = str(act.get("id") or f"T-{idx:02d}")
        task_id_by_code[code] = tid
        name = str(act.get("nombre") or code).replace("\t", " ")
        dur_h = round(float(act.get("duracion_dias") or 1) * 8.0, 2)
        es_dt = _add_workdays(base_dt, int(act.get("es") or 0))
        ef_dt = _add_workdays(base_dt, int(act.get("ef") or 1))
        ls_dt = _add_workdays(base_dt, int(act.get("ls") or 0))
        lf_dt = _add_workdays(base_dt, int(act.get("lf") or 1))
        float_h = round(float(act.get("holgura_dias") or 0) * 8.0, 2)
        lines.append(
            f"%R\t{tid}\t{proj_id}\t{wbs_id}\t{cal_id}\t{code}\t{name}\tTT_Task\tDT_FixedDrtn\tTK_NotStart\t"
            f"{dur_h}\t{dur_h}\t{_fmt_p6_dt(es_dt)}\t{_fmt_p6_dt(ef_dt)}\t{_fmt_p6_dt(ls_dt)}\t{_fmt_p6_dt(lf_dt)}\t{float_h}"
        )

    lines.append("%T\tTASKPRED")
    lines.append("%F\ttask_pred_id\ttask_id\tpred_task_id\tproj_id\tpred_proj_id\tpred_type\tlag_hr_cnt")
    pred_seq = 1
    for act in actividades:
        tid = task_id_by_code.get(str(act.get("id")))
        if not tid:
            continue
        for pr in act.get("predecesoras") or []:
            ptid = task_id_by_code.get(str(pr))
            if ptid:
                lines.append(f"%R\t{pred_seq}\t{tid}\t{ptid}\t{proj_id}\t{proj_id}\tPR_FS\t0.0")
                pred_seq += 1

    lines.append("%T\tRSRC")
    lines.append("%F\trsrc_id\trsrc_short_name\trsrc_name\trsrc_type\tunit_id")
    lines.append("%R\t5001\tOP-CIVIL\tOperario Obra Civil\tRT_Labor\t")
    lines.append("%T\tTASKRSRC")
    lines.append("%F\ttaskrsrc_id\ttask_id\tproj_id\trsrc_id\ttarget_qty\tremain_qty")
    for idx, act in enumerate(actividades, start=1):
        tid = 10000 + idx
        ops = float(act.get("operarios_totales") or 4) * float(act.get("duracion_dias") or 1) * 8.0
        lines.append(f"%R\t{7000 + idx}\t{tid}\t{proj_id}\t5001\t{round(ops, 1)}\t{round(ops, 1)}")

    lines.append("%E")
    return "\n".join(lines) + "\n"


def exportar_mspdi_xml(
    plan: Dict[str, Any],
    project_name: str = "SiteFlow Pro Obra",
    start_date_iso: Optional[str] = None,
) -> str:
    """Genera un archivo `.xml` MSPDI importable tanto por Primavera P6 como por MS Project."""
    base_dt = datetime.fromisoformat(start_date_iso) if start_date_iso else datetime(2026, 1, 5, 8, 0)
    actividades = plan.get("actividades") or []
    dur_total = int(plan.get("duracion_total_dias") or 1)
    finish_dt = _add_workdays(base_dt, dur_total)

    uid_by_code: Dict[str, int] = {str(a.get("id")): idx for idx, a in enumerate(actividades, start=1)}
    tasks_xml: List[str] = [
        f"""    <Task>
      <UID>0</UID>
      <ID>0</ID>
      <Name>{escape(project_name)}</Name>
      <Type>1</Type>
      <OutlineLevel>0</OutlineLevel>
      <Summary>1</Summary>
      <Start>{_fmt_iso_dt(base_dt)}</Start>
      <Finish>{_fmt_iso_dt(finish_dt)}</Finish>
    </Task>"""
    ]

    for idx, act in enumerate(actividades, start=1):
        code = str(act.get("id") or f"T-{idx:02d}")
        name = escape(f"{code} - {act.get('nombre') or code}")
        dur_d = int(act.get("duracion_dias") or 1)
        es_dt = _add_workdays(base_dt, int(act.get("es") or 0))
        ef_dt = _add_workdays(base_dt, int(act.get("ef") or 1))
        crit = 1 if act.get("critica") else 0
        preds_xml = ""
        for pr in act.get("predecesoras") or []:
            puid = uid_by_code.get(str(pr))
            if puid:
                preds_xml += f"""
      <PredecessorLink>
        <PredecessorUID>{puid}</PredecessorUID>
        <Type>1</Type>
      </PredecessorLink>"""
        tasks_xml.append(
            f"""    <Task>
      <UID>{idx}</UID>
      <ID>{idx}</ID>
      <Name>{name}</Name>
      <OutlineLevel>1</OutlineLevel>
      <Start>{_fmt_iso_dt(es_dt)}</Start>
      <Finish>{_fmt_iso_dt(ef_dt)}</Finish>
      <Duration>PT{dur_d * 8}H0M0S</Duration>
      <Critical>{crit}</Critical>{preds_xml}
    </Task>"""
        )

    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Project xmlns="http://schemas.microsoft.com/project">
  <Name>{escape(project_name)}</Name>
  <StartDate>{_fmt_iso_dt(base_dt)}</StartDate>
  <FinishDate>{_fmt_iso_dt(finish_dt)}</FinishDate>
  <Tasks>
{chr(10).join(tasks_xml)}
  </Tasks>
</Project>
"""


def importar_primavera_xer(xer_text: str) -> Dict[str, Any]:
    """Parsea un archivo `.xer` exportado desde Primavera P6 y extrae tareas, holguras y predecesoras."""
    current_table = None
    fields: List[str] = []
    tables: Dict[str, List[Dict[str, str]]] = {}

    for raw in xer_text.splitlines():
        if not raw.strip():
            continue
        parts = raw.split("\t")
        tag = parts[0]
        if tag == "%T":
            current_table = parts[1].strip()
            fields = []
            tables.setdefault(current_table, [])
        elif tag == "%F" and current_table:
            fields = [f.strip() for f in parts[1:]]
        elif tag == "%R" and current_table and fields:
            vals = parts[1:]
            row = {fields[i]: (vals[i] if i < len(vals) else "") for i in range(len(fields))}
            tables[current_table].append(row)

    preds_by_task: Dict[str, List[str]] = {}
    task_code_by_id: Dict[str, str] = {}
    for t in tables.get("TASK", []):
        task_code_by_id[t.get("task_id", "")] = t.get("task_code") or t.get("task_id", "")

    for pr in tables.get("TASKPRED", []):
        tid = pr.get("task_id", "")
        ptid = pr.get("pred_task_id", "")
        if tid and ptid:
            preds_by_task.setdefault(tid, []).append(task_code_by_id.get(ptid, ptid))

    actividades = []
    max_ef = 0
    for idx, t in enumerate(tables.get("TASK", []), start=1):
        tid = t.get("task_id", str(idx))
        code = t.get("task_code") or f"P6-{idx}"
        dur_h = float(t.get("target_drtn_hr_cnt") or 8.0)
        dur_d = max(1, int(round(dur_h / 8.0)))
        float_h = float(t.get("total_float_hr_cnt") or 0.0)
        holgura_d = round(float_h / 8.0, 1)
        crit = holgura_d <= 0.0
        es = max_ef if not preds_by_task.get(tid) else 0
        ef = es + dur_d
        max_ef = max(max_ef, ef)
        actividades.append({
            "id": code,
            "nombre": t.get("task_name") or code,
            "clave": "primavera_p6",
            "medicion": dur_d,
            "unidad": "d",
            "equipo": "Importado Primavera P6",
            "frentes": 1,
            "duracion_dias": dur_d,
            "es": es,
            "ef": ef,
            "holgura_dias": holgura_d,
            "critica": crit,
            "predecesoras": preds_by_task.get(tid, []),
        })

    ruta_critica = [a["id"] for a in actividades if a["critica"]]
    proj_row = (tables.get("PROJECT") or [{}])[0]
    return {
        "origen": "primavera_p6_xer",
        "project_short_name": proj_row.get("proj_short_name", "P6_IMPORT"),
        "n_tareas": len(actividades),
        "duracion_total_dias": max_ef,
        "duracion_semanas_aprox": round(max_ef / 5.0, 1),
        "ruta_critica_ids": ruta_critica,
        "cumple_plazo": True,
        "actividades": actividades,
    }
