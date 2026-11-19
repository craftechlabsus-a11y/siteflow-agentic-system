"""Pruebas de Fase 4: Cartuchos Componibles, Planificador 4D + Curva S Excel y DAG Runner."""
import os

import openpyxl
import pytest

from src.engines.planning_4d.exporters import export_gantt_json, export_schedule_excel
from src.engines.planning_4d.pdm_engine import solve_pdm
from src.engines.planning_4d.primavera_p6 import exportar_primavera_xer, importar_primavera_xer
from src.engines.planning_4d.rendimientos import estimate_duration_days
from src.orchestrator.dag_runner import DAGNode, DAGRunner
from src.specialties.profiles.composer import compose_specialty_profile


def test_compose_specialty_profile_edar_idam():
    prof = compose_specialty_profile("edar_idam")
    assert prof["typology"] == "edar_idam"
    assert "mep_proceso" in prof["disciplines"]
    assert "IfcPipeSegment" in prof["ifc_classes"]
    assert "IfcPump" in prof["ifc_classes"]
    assert "fat_sat" in prof["checks_obligatorios"]
    assert prof["legacy_profiles_loaded"] == 2


def test_planning_4d_rendimientos_gantt_excel_and_xer(tmp_path):
    dur_pipe = estimate_duration_days("tendido_tuberia_dn_grande", 180.0, crews=2)
    assert dur_pipe == 5.0

    acts = [
        {"id": "W1", "name": "Excavación cántara", "duration": 4},
        {"id": "W2", "name": "Tendido tubería DN500", "duration": dur_pipe},
        {"id": "W3", "name": "Comisionado SAT", "duration": 3},
    ]
    deps = [
        {"pred": "W1", "succ": "W2", "type": "FS", "lag": 0},
        {"pred": "W2", "succ": "W3", "type": "FS", "lag": 0},
    ]
    pdm = solve_pdm(acts, deps)
    assert pdm["project_duration"] == 12.0

    wbs_map = {"W2": ["gid_pipe_01", "gid_pipe_02"]}
    gantt = export_gantt_json(pdm, wbs_bim_map=wbs_map)
    assert gantt["s_curve"][-1]["cumulative_pct"] == 100.0
    assert gantt["tasks"][1]["bim_global_ids"] == ["gid_pipe_01", "gid_pipe_02"]

    xlsx_path = str(tmp_path / "plan_4d.xlsx")
    export_schedule_excel(pdm, xlsx_path, project_name="IDAM Costa", wbs_bim_map=wbs_map)
    assert os.path.exists(xlsx_path)

    wb = openpyxl.load_workbook(xlsx_path)
    assert "Cronograma_4D" in wb.sheetnames
    assert "Curva_S" in wb.sheetnames
    assert wb["Cronograma_4D"]["C5"].value == "=SUM(C2:C4)"

    plan = {
        "duracion_total_dias": pdm["project_duration"],
        "actividades": [
            {"id": it["id"], "nombre": it["name"], "duracion_dias": it["duration"], "es": it["ES"], "ef": it["EF"], "predecesoras": []}
            for it in pdm["schedule"]
        ],
    }
    xer = exportar_primavera_xer(plan, project_name="IDAM Costa")
    parsed_xer = importar_primavera_xer(xer)
    assert len(parsed_xer.get("actividades", parsed_xer.get("tasks", []))) == 3


@pytest.mark.asyncio
async def test_dag_runner_parallel_and_human_in_the_loop(tmp_path):
    runner = DAGRunner(db_path=str(tmp_path / "dag.db"))

    def step_a(_params, _deps):
        return {"ok": True, "confianza": 0.95}

    def step_b(_params, _deps):
        return {"ok": True, "confianza": 0.40}  # baja confianza -> pausa

    def step_c(_params, deps):
        return {"merged": True, "a": deps["A"]["ok"], "b": deps["B"]["ok"], "confianza": 0.99}

    nodes = [
        DAGNode("A", step_a),
        DAGNode("B", step_b),
        DAGNode("C", step_c, deps=["A", "B"]),
    ]

    res1 = await runner.execute(nodes, project_id="proj_hitl")
    assert res1["status"] == "awaiting_review"
    assert res1["nodes"]["A"]["status"] == "completed"
    assert res1["nodes"]["B"]["status"] == "awaiting_review"
    assert "C" not in res1["nodes"]

    runner.approve_node(res1["run_id"], "B")
    res2 = await runner.execute(nodes, project_id="proj_hitl", run_id=res1["run_id"])
    assert res2["status"] == "completed"
    assert res2["nodes"]["C"]["status"] == "completed"
    assert res2["nodes"]["C"]["result"]["merged"] is True
