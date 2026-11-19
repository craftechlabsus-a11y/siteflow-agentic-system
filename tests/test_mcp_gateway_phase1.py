"""Pruebas de Fase 1: ArtifactStore por ruta y MCP Gateway Dual (Tools, Resources, Prompts, HTTP)."""
import json
import os

import pytest

from src.core.artifact_store import ArtifactStore
from src.mcp_gateway.server import (
    _builtin_tools,
    _execute_tool_sync,
    build_http_app,
    crear_servidor,
    verify_mcp_http_auth,
)


def test_artifact_store_register_file_path_dedup(tmp_path):
    sample = tmp_path / "canal_captacion.ifc"
    sample.write_text("ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n", encoding="utf-8")

    store = ArtifactStore(base_dir=str(tmp_path / "cache"))
    m1 = store.register_file_path(str(sample), project_id="edar_norte")
    m2 = store.register_file_path(str(sample), project_id="edar_norte")

    assert m1.artifact_id == m2.artifact_id
    assert m1.sha256 == m2.sha256
    assert m1.file_type == "ifc"
    assert os.path.exists(m1.storage_path)


def test_mcp_gateway_tools_and_pdm_execution(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_CACHE_DIR", str(tmp_path / "ws_cache"))
    sample = tmp_path / "estacion_bombeo.ifc"
    sample.write_text("ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n", encoding="utf-8")

    reg = _execute_tool_sync("register_artifact", {"file_path": str(sample), "project_id": "eb_01"})
    assert reg["project_id"] == "eb_01"
    assert reg["file_type"] == "ifc"
    assert len(reg["sha256"]) == 64

    pdm = _execute_tool_sync(
        "planificacion_pdm",
        {
            "activities": [
                {"id": "A1", "name": "Excavación cántara", "duration": 5},
                {"id": "A2", "name": "Solera hormigón armado", "duration": 4},
            ],
            "dependencies": [{"pred": "A1", "succ": "A2", "type": "FS", "lag": 0}],
        },
    )
    assert pdm["project_duration"] == 9.0
    assert pdm["critical_path"] == ["A1", "A2"]

    xml_out = _execute_tool_sync(
        "export_planning",
        {
            "format": "msproject_xml",
            "project_name": "EDAR Prueba",
            "activities": [{"id": "A1", "name": "Excavación", "duration": 5}],
            "dependencies": [],
        },
    )
    assert xml_out["format"] == "msproject_xml"
    assert "<Project" in xml_out["content"]


def test_mcp_server_creation_and_http_auth(monkeypatch):
    server = crear_servidor()
    assert server.name == "siteflow-agentic-gateway"

    tool_names = {t["name"] for t in _builtin_tools()}
    assert {"register_artifact", "bim_query_elements", "doc_search", "planificacion_pdm"}.issubset(tool_names)

    monkeypatch.setenv("MCP_AUTH_TOKEN", "secret-token-123")
    assert verify_mcp_http_auth("Bearer secret-token-123") is True
    assert verify_mcp_http_auth("Bearer wrong") is False
    assert verify_mcp_http_auth(None) is False

    app = build_http_app()
    assert app is not None
