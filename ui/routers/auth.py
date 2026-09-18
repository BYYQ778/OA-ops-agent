"""第 5 周认证端点：登录 / 登出 / 会话自述（RBAC 门控在 ui/middleware.py）。

- 登录成功后下发 oa_session cookie（HttpOnly / SameSite=Lax / TTL 可配）；
- 登录失败按客户端 IP 限速（滑动窗口），成功/失败/登出全部记入 auth_events 审计；
- GET /api/auth/session 供前端判断会话模式（loopback / session / disabled / anonymous）。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from utils.config import config as app_config
from utils.database import db
from utils.request_context import get_request_id
from utils.security import (
    SESSION_COOKIE_NAME,
    AuthUser,
    LoginRateLimiter,
    auth_enabled,
    authenticate,
    bypass_loopback,
    generate_session_token,
    hash_session_token,
    is_loopback_host,
    load_users,
    session_ttl_seconds,
)


class LoginRequest(BaseModel):
    """POST /api/auth/login 请求体。"""

    username: str
    password: str


def _client_host(request: Request) -> str:
    client = request.client
    return client.host if client is not None else ""


def _configured_max_attempts(config: Any) -> int:
    try:
        return max(1, int(config.get("auth.max_attempts_per_minute", 10)))
    except (TypeError, ValueError):
        return 10


def create_auth_router(
    users_provider: Optional[Callable[[], List[AuthUser]]] = None,
    limiter: Optional[LoginRateLimiter] = None,
    get_config: Optional[Callable[[], Any]] = None,
) -> APIRouter:
    """构建认证路由；依赖均可注入（测试 / 多用户扩展）。"""
    config_getter = get_config or (lambda: app_config)
    provide_users = users_provider or (lambda: load_users(config_getter()))
    rate_limiter = limiter or LoginRateLimiter(max_attempts=_configured_max_attempts(config_getter()))
    router = APIRouter(prefix="/api/auth", tags=["auth"])

    @router.post("/login")
    def login(payload: LoginRequest, request: Request) -> JSONResponse:
        """登录：下发会话 cookie；失败限速并审计。"""
        client = _client_host(request)
        cfg = config_getter()
        if not rate_limiter.allow(client):
            db.record_auth_event(
                "login_rate_limited", username=payload.username, client=client, request_id=get_request_id()
            )
            raise HTTPException(status_code=429, detail="尝试过于频繁，请稍后再试")

        user = authenticate(provide_users(), payload.username, payload.password)
        if user is None:
            rate_limiter.record_failure(client)
            db.record_auth_event("login_fail", username=payload.username, client=client, request_id=get_request_id())
            raise HTTPException(status_code=401, detail="用户名或密码错误")

        rate_limiter.reset(client)
        db.delete_expired_sessions()
        ttl = session_ttl_seconds(cfg)
        token = generate_session_token()
        db.create_session(hash_session_token(token), user.username, user.role, ttl_seconds=ttl, client=client)
        db.record_auth_event("login_ok", username=user.username, client=client, request_id=get_request_id())

        response = JSONResponse({"ok": True, "username": user.username, "role": user.role})
        response.set_cookie(
            SESSION_COOKIE_NAME,
            token,
            max_age=int(ttl),
            httponly=True,
            samesite="lax",
            secure=bool(cfg.get("auth.cookie_secure", False)),
            path="/",
        )
        return response

    @router.post("/logout")
    def logout(request: Request) -> JSONResponse:
        """登出：删除服务端会话并清 cookie。"""
        token = request.cookies.get(SESSION_COOKIE_NAME, "")
        if token:
            db.delete_session(hash_session_token(token))
            db.record_auth_event("logout", client=_client_host(request), request_id=get_request_id())
        response = JSONResponse({"ok": True})
        response.delete_cookie(SESSION_COOKIE_NAME, path="/")
        return response

    @router.get("/session")
    def session_info(request: Request) -> Dict[str, Any]:
        """会话自述：前端据此切换「登录态 / 本机直通 / 匿名」。"""
        cfg = config_getter()
        token = request.cookies.get(SESSION_COOKIE_NAME, "")
        if token:
            record = db.get_session(hash_session_token(token))
            if record is not None:
                return {
                    "authenticated": True,
                    "mode": "session",
                    "username": record["username"],
                    "role": record["role"],
                }
        if not auth_enabled(cfg):
            return {"authenticated": True, "mode": "disabled", "username": "", "role": "admin"}
        if bypass_loopback(cfg) and is_loopback_host(_client_host(request)):
            return {"authenticated": True, "mode": "loopback", "username": "", "role": "admin"}
        return {"authenticated": False, "mode": "anonymous", "username": "", "role": ""}

    return router
