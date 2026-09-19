"""Unit tests for the extracted system router (health / dev hot-reload)."""

import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ui.routers.system import create_system_router

pytestmark = pytest.mark.allow_hosts(["127.0.0.1", "::1", "localhost"])


def _client_for(base_dir: Path, status) -> TestClient:
    app = FastAPI()
    app.include_router(create_system_router(base_dir, status))
    return TestClient(app)


def test_health_reports_kb_state_and_error() -> None:
    with _client_for(Path("."), lambda: {"state": "unavailable", "error": "模型缺失"}) as client:
        body = client.get("/api/health").json()

    assert body == {
        "status": "ok",
        "version": "3.0.0",
        "kb_state": "unavailable",
        "kb_error": "模型缺失",
    }


def test_health_defaults_to_loading_without_state() -> None:
    with _client_for(Path("."), lambda: {}) as client:
        body = client.get("/api/health").json()

    assert body["kb_state"] == "loading"
    assert body["kb_error"] is None


def test_dev_version_returns_latest_mtime_across_directories(tmp_path) -> None:
    templates = tmp_path / "templates"
    templates.mkdir()
    vendor = tmp_path / "static" / "vendor"
    vendor.mkdir(parents=True)
    (templates / "index.html").write_text("x", encoding="utf-8")
    (vendor / "app.js").write_text("y", encoding="utf-8")
    os.utime(templates / "index.html", (1000, 1000))
    os.utime(vendor / "app.js", (2500, 2500))

    with _client_for(tmp_path, lambda: {"state": "loading"}) as client:
        body = client.get("/api/dev/version").json()

    assert body["ts"] == 2500.0
    assert body["version"] == "2500.000"


def test_dev_version_is_zero_without_ui_directories(tmp_path) -> None:
    with _client_for(tmp_path, lambda: {"state": "loading"}) as client:
        body = client.get("/api/dev/version").json()

    assert body["ts"] == 0.0
    assert body["version"] == "0.000"
