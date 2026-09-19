"""第 5 周 w5-5：认证端点（登录/登出/会话自述 + 限速 + 审计）。"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from ui.routers.auth import create_auth_router
from ui.server import create_app
from utils.database import db
from utils.security import AuthUser, LoginRateLimiter

pytestmark = pytest.mark.allow_hosts(["127.0.0.1", "::1", "localhost"])

_GOOD_PASSWORD = "strong-pass-1"


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


def _users() -> list[AuthUser]:
    return [
        AuthUser(username="admin", password=_GOOD_PASSWORD, role="admin"),
        AuthUser(username="viewer", password="view-pass-1", role="viewer"),
    ]


def _app(config: _Config | None = None, limiter: LoginRateLimiter | None = None) -> FastAPI:
    cfg = config or _Config({"auth": {"enabled": True, "bypass_loopback": False, "session_ttl_hours": 1}})
    rate_limiter = limiter or LoginRateLimiter(max_attempts=5, window_seconds=60)

    def factory() -> APIRouter:
        return create_auth_router(users_provider=_users, limiter=rate_limiter, get_config=lambda: cfg)

    return create_app(enable_background_services=False, kb_agent_factory=lambda: None, auth_router_factory=factory)


def _remote_client(app: FastAPI) -> TestClient:
    return TestClient(app, client=("10.0.0.9", 5000))


def test_login_success_sets_cookie_and_audit() -> None:
    client = _remote_client(_app())
    response = client.post("/api/auth/login", json={"username": "admin", "password": _GOOD_PASSWORD})
    assert response.status_code == 200
    assert response.json()["role"] == "admin"
    set_cookie = response.headers["set-cookie"]
    assert "oa_session=" in set_cookie
    assert "HttpOnly" in set_cookie and "Max-Age=3600" in set_cookie

    request_id = response.headers.get("x-request-id", "")
    events = [e for e in db.list_auth_events(limit=50) if e["event"] == "login_ok" and e["username"] == "admin"]
    assert events and events[0]["request_id"] == request_id

    session = client.get("/api/auth/session").json()
    assert session["mode"] == "session" and session["username"] == "admin"


def test_login_failure_401_and_audit() -> None:
    client = _remote_client(_app())
    response = client.post("/api/auth/login", json={"username": "admin", "password": "nope"})
    assert response.status_code == 401
    assert "oa_session" not in response.cookies
    events = [e for e in db.list_auth_events(limit=20) if e["event"] == "login_fail" and e["username"] == "admin"]
    assert events


def test_login_rate_limited_after_failures() -> None:
    limiter = LoginRateLimiter(max_attempts=3, window_seconds=60)
    client = _remote_client(_app(limiter=limiter))
    for _ in range(3):
        assert client.post("/api/auth/login", json={"username": "admin", "password": "bad"}).status_code == 401
    blocked = client.post("/api/auth/login", json={"username": "admin", "password": _GOOD_PASSWORD})
    assert blocked.status_code == 429
    limited = [e for e in db.list_auth_events(limit=20) if e["event"] == "login_rate_limited"]
    assert limited


def test_logout_clears_session() -> None:
    client = _remote_client(_app())
    client.post("/api/auth/login", json={"username": "viewer", "password": "view-pass-1"})
    assert client.get("/api/auth/session").json()["mode"] == "session"

    assert client.post("/api/auth/logout").status_code == 200
    after = client.get("/api/auth/session").json()
    assert after["mode"] == "anonymous" and after["authenticated"] is False
    assert any(e["event"] == "logout" for e in db.list_auth_events(limit=20))


def test_session_modes() -> None:
    remote = _remote_client(_app())
    assert remote.get("/api/auth/session").json()["mode"] == "anonymous"

    loopback_app = _app(config=_Config({"auth": {"enabled": True, "bypass_loopback": True}}))
    loopback = TestClient(loopback_app).get("/api/auth/session").json()
    assert loopback["mode"] == "loopback" and loopback["role"] == "admin"

    disabled_app = _app(config=_Config({"auth": {"enabled": False}}))
    disabled = TestClient(disabled_app).get("/api/auth/session").json()
    assert disabled["mode"] == "disabled" and disabled["authenticated"] is True


def test_cookie_secure_flag_configurable() -> None:
    app = _app(config=_Config({"auth": {"enabled": True, "bypass_loopback": False, "cookie_secure": True}}))
    response = _remote_client(app).post("/api/auth/login", json={"username": "admin", "password": _GOOD_PASSWORD})
    assert "Secure" in response.headers["set-cookie"]
