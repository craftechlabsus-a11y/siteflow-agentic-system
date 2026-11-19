"""Exportadores 4D: JSON Gantt con Curva S, Excel (.xlsx) con fórmulas y vinculación WBS ↔ GlobalId."""
import math
import os
from typing import Any, Dict, List, Optional

import openpyxl
from openpyxl.styles import Font, PatternFill


def build_s_curve(schedule_result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Genera la Curva S acumulada (%) día a día a partir de ES/EF de solve_pdm."""
    items = schedule_result.get("schedule", [])
    total_dur = int(math.ceil(float(schedule_result.get("project_duration", 0.0))))
    if not items or total_dur <= 0:
        return []

    total_work = sum(max(0.0, float(it.get("duration", 0.0))) for it in items)
    if total_work <= 0.0:
        return []

    curve: List[Dict[str, Any]] = []
    cum = 0.0
    for day in range(1, total_dur + 1):
        day_work = 0.0
        for it in items:
            es = float(it.get("ES", 0.0))
            ef = float(it.get("EF", 0.0))
            dur = float(it.get("duration", 0.0))
            if dur <= 0.0:
                continue
            overlap = max(0.0, min(float(day), ef) - max(float(day - 1), es))
            day_work += overlap
        pct_day = round((day_work / total_work) * 100.0, 2)
        cum = min(100.0, round(cum + pct_day, 2))
        curve.append(
            {
                "day": day,
                "daily_pct": pct_day,
                "cumulative_pct": cum,
            }
        )
    if curve:
        curve[-1]["cumulative_pct"] = 100.0
    return curve


def export_gantt_json(
    schedule_result: Dict[str, Any],
    *,
    wbs_bim_map: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, Any]:
    """Devuelve estructura Gantt JSON enriquecida con GlobalIds BIM y Curva S."""
    wbs_map = wbs_bim_map or {}
    tasks: List[Dict[str, Any]] = []
    for it in schedule_result.get("schedule", []):
        aid = str(it["id"])
        tasks.append(
            {
                **it,
                "bim_global_ids": wbs_map.get(aid, []),
            }
        )
    return {
        "project_duration": schedule_result.get("project_duration", 0.0),
        "critical_path": schedule_result.get("critical_path", []),
        "tasks": tasks,
        "s_curve": build_s_curve(schedule_result),
    }


def export_schedule_excel(
    schedule_result: Dict[str, Any],
    output_path: str,
    *,
    project_name: str = "Planificación 4D",
    wbs_bim_map: Optional[Dict[str, List[str]]] = None,
) -> str:
    """Genera un libro Excel (.xlsx) con Cronograma 4D, vinculación GlobalId y Curva S."""
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Cronograma_4D"

    header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)

    headers = [
        "ID",
        "Actividad",
        "Duración (d)",
        "ES",
        "EF",
        "LS",
        "LF",
        "Holgura",
        "Crítica",
        "GlobalIds_BIM",
    ]
    ws.append(headers)
    for col_idx in range(1, len(headers) + 1):
        c = ws.cell(row=1, column=col_idx)
        c.fill = header_fill
        c.font = header_font

    wbs_map = wbs_bim_map or {}
    items = schedule_result.get("schedule", [])
    for it in items:
        aid = str(it["id"])
        ws.append(
            [
                aid,
                it.get("name", aid),
                float(it.get("duration", 0.0)),
                float(it.get("ES", 0.0)),
                float(it.get("EF", 0.0)),
                float(it.get("LS", 0.0)),
                float(it.get("LF", 0.0)),
                float(it.get("total_float", 0.0)),
                "SÍ" if it.get("is_critical") else "NO",
                ", ".join(wbs_map.get(aid, [])),
            ]
        )

    last_row = len(items) + 1
    if last_row >= 2:
        ws.append(["TOTAL", project_name, f"=SUM(C2:C{last_row})", "", f"=MAX(E2:E{last_row})"])
        ws.cell(row=last_row + 1, column=1).font = Font(bold=True)

    ws.freeze_panes = "A2"

    # Hoja 2: Curva S
    ws_s = wb.create_sheet("Curva_S")
    s_headers = ["Día", "Avance Diario (%)", "Avance Acumulado (%)"]
    ws_s.append(s_headers)
    for col_idx in range(1, len(s_headers) + 1):
        c = ws_s.cell(row=1, column=col_idx)
        c.fill = header_fill
        c.font = header_font

    for pt in build_s_curve(schedule_result):
        ws_s.append([pt["day"], pt["daily_pct"], pt["cumulative_pct"]])
    ws_s.freeze_panes = "A2"

    wb.save(output_path)
    return os.path.abspath(output_path)
