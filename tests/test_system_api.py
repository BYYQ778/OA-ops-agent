import pytest
from fastapi.testclient import TestClient

from ui.server import create_app

pytestmark = pytest.mark.allow_hosts(["127.0.0.1", "::1", "localhost"])


def test_health_endpoint_does_not_start_background_services(monkeypatch) -> None:
    calls: list[str] = []

    def record_ollama_call() -> None:
        calls.append("ollama")

    def record_prewarm_call() -> None:
        calls.append("prewarm")

    monkeypatch.setattr("ui.server._ensure_ollama_running", record_ollama_call)
    monkeypatch.setattr("ui.server._prewarm_kb", record_prewarm_call)
    app = create_app(enable_background_services=False)

    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert calls == []


def test_homepage_and_legacy_routes_are_registered() -> None:
    with TestClient(create_app(enable_background_services=False)) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "OA" in response.text


def test_log_endpoint_uses_real_offline_analyzer() -> None:
    with TestClient(create_app(enable_background_services=False)) as client:
        response = client.post(
            "/api/log/analyze",
            data={"log_text": "2026-09-16 ERROR 502 Bad Gateway upstream unavailable"},
        )

    assert response.status_code == 200
    assert "502 Bad Gateway" in response.json()["result"]


def test_empty_log_endpoint_returns_validation_message() -> None:
    with TestClient(create_app(enable_background_services=False)) as client:
        response = client.post("/api/log/analyze", data={"log_text": "  "})

    assert response.status_code == 200
    assert response.json() == {"result": "请输入需要分析的日志内容"}


def test_kb_endpoint_reports_loading_without_initializing_models(monkeypatch) -> None:
    monkeypatch.setattr("ui.server.get_kb_agent", lambda: None)
    monkeypatch.setattr("ui.server._kb_state", {"state": "loading", "error": None})

    with TestClient(create_app(enable_background_services=False)) as client:
        response = client.post("/api/kb/ask", data={"question": "OA 如何恢复？"})

    assert response.status_code == 200
    assert response.json()["state"] == "loading"


def test_inspection_status_is_available_without_running_inspection() -> None:
    with TestClient(create_app(enable_background_services=False)) as client:
        response = client.get("/api/inspect/status")

    assert response.status_code == 200
    assert response.json()["running"] is False
    assert response.json()["interval"] > 0
