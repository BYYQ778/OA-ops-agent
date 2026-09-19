"""第 5 周 w5-6：RBAC 认证门（回环旁路/远端 401/登录放行/角色 403/豁免/跳转）。"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from ui.routers.auth import create_auth_router
from ui.server import create_app
from utils.security import AuthUser, LoginRateLimiter

pytestmark = pytest.mark.allow_hosts(["127.0.0.1", "::1", "localhost"])

_ADMIN_PW = "admin-pass-1"
_VIEWER_PW = "viewer-pass-1"


class _Config:
    """最小 config 替身（点号路径 get）。"""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def get(self, path: str, default: Any = None) -> Any:
        value: Any = self._data
        for key in path.split("."):
            if not isinstance(value, dict):
                return default
            value = value.get(key)
            if value is None:
                return default
        return value


def _fake_app() -> FastAPI:
    """真实 create_app（门用真实 config：enabled=True/bypass_loopback=True）+ 注入假用户。"""
    users = [
        AuthUser(username="admin", password=_ADMIN_PW, role="admin"),
        AuthUser(username="viewer", password=_VIEWER_PW, role="viewer"),
    ]
    cfg = _Config({"auth": {"enabled": True, "bypass_loopback": True}})

    def factory() -> APIRouter:
        return create_auth_router(
            users_provider=lambda: users,
            limiter=LoginRateLimiter(max_attempts=50, window_seconds=60),
            get_config=lambda: cfg,
        )

    return create_app(enable_background_services=False, kb_agent_factory=lambda: None, auth_router_factory=factory)


def _remote(app: FastAPI) -> TestClient:
    return TestClient(app, client=("10.0.0.9", 5000))


def _login(client: TestClient, username: str, password: str) -> None:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text


def test_loopback_bypass_passthrough() -> None:
    client = TestClient(_fake_app())  # 默认主机 "testclient" 视同回环
    assert client.get("/api/v1/incidents").status_code == 200
    assert client.get("/api/auth/session").json()["mode"] == "loopback"


def test_remote_anonymous_gets_401_with_request_id() -> None:
    client = _remote(_fake_app())
    response = client.get("/api/v1/incidents")
    assert response.status_code == 401
    assert response.json()["detail"] == "需要登录"
    assert response.headers.get("x-request-id")  # 中间件顺序证明：上下文在最外层


def test_remote_login_then_access() -> None:
    client = _remote(_fake_app())
    _login(client, "viewer", _VIEWER_PW)
    assert client.get("/api/v1/incidents").status_code == 200


def test_remote_viewer_forbidden_on_admin_route() -> None:
    client = _remote(_fake_app())
    _login(client, "viewer", _VIEWER_PW)
    response = client.post("/api/inspect/stop")
    assert response.status_code == 403
    assert "权限不足" in response.json()["detail"]


def test_remote_admin_allowed_on_admin_route() -> None:
    client = _remote(_fake_app())
    _login(client, "admin", _ADMIN_PW)
    response = client.post("/api/inspect/stop")
    assert response.status_code == 200  # 调度器未运行 → ok False，但门已放行
    assert response.json()["ok"] is False


def test_remote_viewer_can_use_query_whitelist() -> None:
    client = _remote(_fake_app())
    _login(client, "viewer", _VIEWER_PW)
    response = client.post("/api/v1/rag/query", json={"question": "x"})
    assert response.status_code == 200  # kb 未注入 → available False，但非 401/403


def test_public_paths_reachable_anonymously() -> None:
    client = _remote(_fake_app())
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/auth/session").status_code == 200
    assert client.get("/api/dev/version").status_code == 200
    assert client.get("/docs").status_code == 200
    assert client.get("/static/nonexistent.css").status_code == 404  # 豁免：404 而非 401


def test_page_redirect_to_login() -> None:
    client = _remote(_fake_app())
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/login?next=%2F"


def test_bad_cookie_rejected() -> None:
    client = _remote(_fake_app())
    client.cookies.set("oa_session", "garbage-token")
    assert client.get("/api/v1/incidents").status_code == 401


def test_auth_disabled_gate_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ui.middleware.auth_enabled", lambda cfg: False)
    client = _remote(_fake_app())
    assert client.get("/api/v1/incidents").status_code == 200


def test_bypass_loopback_disabled_strict_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ui.middleware.bypass_loopback", lambda cfg: False)
    client = TestClient(_fake_app())  # 主机为 testclient，但严格模式下不再直通
    assert client.get("/api/v1/incidents").status_code == 401


def test_login_page_renders_anonymously() -> None:
    client = _remote(_fake_app())
    response = client.get("/login")
    assert response.status_code == 200
    assert "login-form" in response.text
    assert "OA运维Agent" in response.text
