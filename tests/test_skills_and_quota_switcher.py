"""Pruebas de skills adaptadas, orquestación DAG, DLP y Switch de Cuota al 96%."""
import json
from unittest.mock import MagicMock

import pytest
from PIL import Image

from src.core import llm_pool
from src.core.dlp_guard import DLPSanitizer
from src.core.quota_switcher import QuotaSwitcher
from src.mcp_gateway.server import _execute_tool_sync
from src.orchestrator.dag_runner import DAGRunner, skill_to_dag_node


def test_dlp_sanitizer_roundtrip():
    dlp = DLPSanitizer()
    raw = "Empresa B12345678, contacto ing@edar.es, tel 612345678, IBAN ES9121000418450200051332."
    masked = dlp.sanitize_text(raw)
    assert "B12345678" not in masked
    assert "ing@edar.es" not in masked
    assert "ES9121000418450200051332" not in masked
    assert dlp.restore_text(masked) == raw


def test_quota_switcher_proactive_96_percent_key_and_provider_switch(monkeypatch):
    switcher = QuotaSwitcher(switch_ratio=0.96, token_limit=1000, request_limit=100)
    monkeypatch.setattr("src.core.llm_pool.get_quota_switcher", lambda: switcher)
    monkeypatch.setattr("src.core.quota_switcher._DEFAULT_SWITCHER", switcher)

    monkeypatch.setitem(llm_pool.PROVIDERS["groq"], "keys", ["g_key_0", "g_key_1"])
    monkeypatch.setitem(llm_pool.PROVIDERS["openrouter"], "keys", ["or_key_0"])
    monkeypatch.setattr(llm_pool, "_cooldown", {})
    monkeypatch.setattr(llm_pool, "_rr", {"groq": 0, "openrouter": 0})

    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        auth = headers.get("Authorization", "")
        calls.append((url, auth, json["model"]))
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 500, "completion_tokens": 465, "total_tokens": 965},
        }
        return resp

    monkeypatch.setattr(llm_pool.httpx, "post", fake_post)

    # Llamada 1 -> groq key 0 (consume 965/1000 = 96.5% >= 96%)
    r1 = llm_pool.chat([{"role": "user", "content": "hola"}], task="text", dlp_enabled=False)
    assert (r1.provider, r1.key_index) == ("groq", 0)
    assert switcher.should_switch("groq", 0) is True

    # Llamada 2 -> debe saltar proactivamente a groq key 1
    r2 = llm_pool.chat([{"role": "user", "content": "hola"}], task="text", dlp_enabled=False)
    assert (r2.provider, r2.key_index) == ("groq", 1)
    assert switcher.should_switch("groq", 1) is True

    # Llamada 3 -> ambas claves de groq >= 96%, debe cambiar de proveedor/modelo a openrouter
    r3 = llm_pool.chat([{"role": "user", "content": "hola"}], task="text", dlp_enabled=False)
    assert r3.provider == "openrouter"
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_adapted_skills_in_dag_and_mcp(tmp_path, monkeypatch):
    # 1. Coherencia triple-check vía MCP
    coh = _execute_tool_sync(
        "coherencia_triple_check",
        {
            "module_id": "edar_norte",
            "parametros_memoria": {"DN": 500},
            "mediciones_planos": {"tuberia_dn500_ml": 120.0},
            "mediciones_presupuesto": {"tuberia_dn500_ml": 90.0},
            "tolerancia_pct": 5.0,
        },
    )
    assert coh["datos"]["n_hallazgos"] == 1
    assert coh["datos"]["hallazgos"][0]["estado"] == "infradotada"

    # 2. Obra civil vía DAGRunner
    runner = DAGRunner(db_path=str(tmp_path / "dag_skills.db"))
    n1 = skill_to_dag_node(
        "coh",
        "documental_coherencia_triple_check",
        params={
            "mediciones_planos": {"muro_m3": 100.0},
            "mediciones_presupuesto": {"muro_m3": 102.0},
            "tolerancia_pct": 5.0,
        },
    )
    n2 = skill_to_dag_node(
        "civil",
        "planificacion_generar_cronograma_civil",
        params={
            "elementos_bim": [{"ifc_class": "IfcFooting", "volume_m3": 55.0, "zmin": -2.0}],
            "tipo_terreno": "tierra",
        },
        deps=["coh"],
    )
    dag_res = await runner.execute([n1, n2], project_id="proj_skills")
    assert dag_res["status"] == "completed"
    assert dag_res["nodes"]["civil"]["status"] == "completed"

    # 3. Visión Hidráulica P&ID usando LLMPool + QuotaSwitcher (mocked HTTP)
    switcher = QuotaSwitcher(switch_ratio=0.96, token_limit=10000, request_limit=100)
    monkeypatch.setattr("src.core.llm_pool.get_quota_switcher", lambda: switcher)
    monkeypatch.setitem(llm_pool.PROVIDERS["gemini"], "keys", ["gem_key_0"])
    monkeypatch.setattr(llm_pool, "_cooldown", {})
    monkeypatch.setattr(llm_pool, "_rr", {"gemini": 0})

    def fake_vision_post(url, headers=None, json=None, timeout=None):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": '{"equipos":[{"tag":"P-101","tipo":"Bomba","especificacion":"50 m3/h"}],'
                        '"lineas":[{"tag_linea":"L-01","dn":"DN200","pn":"PN16","fluido":"Agua bruta"}],'
                        '"valvulas_instrumentos":["PIT-101"],"observaciones":[],"confianza":0.9}'
                    }
                }
            ],
            "usage": {"prompt_tokens": 200, "completion_tokens": 50, "total_tokens": 250},
        }
        return resp

    monkeypatch.setattr(llm_pool.httpx, "post", fake_vision_post)

    img_path = tmp_path / "pid_test.png"
    Image.new("RGB", (64, 64), color="white").save(img_path)

    pid_res = _execute_tool_sync("hidraulica_pid_equipos", {"file_path": str(img_path), "proceso": "Captación"})
    assert pid_res["confianza"] == 0.9
    assert pid_res["datos"]["equipos"][0]["tag"] == "P-101"
    assert switcher.usage_ratios("gemini", 0)["tokens_used"] == 250
