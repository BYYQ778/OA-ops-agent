"""纯 ASGI 中间件（第 5 周）：请求上下文 + 访问日志。

- 纯 ASGI 实现（非 BaseHTTPMiddleware）：不缓冲响应体，SSE 等流式端点安全透传；
- 不读取请求 body，只做上下文绑定、响应头回写与访问日志；
- 后续步骤（w5-2 / w5-6）在同一文件追加指标与认证门中间件。
"""

from __future__ import annotations

import time
from http.cookies import SimpleCookie
from urllib.parse import quote

from starlette.responses import JSONResponse, RedirectResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from utils.config import config as app_config
from utils.database import db
from utils.logger import get_logger
from utils.metrics import metrics, normalize_route
from utils.request_context import new_request_id, reset_request_id, set_request_id
from utils.security import (
    SESSION_COOKIE_NAME,
    auth_enabled,
    bypass_loopback,
    hash_session_token,
    is_loopback_host,
)
from utils.tracing import span as tracing_span

_access_logger = get_logger("oa.access")

#: 遥测静默路径（静态资源与开发热刷新轮询，避免噪音与基数浪费）
_SILENT_PREFIXES = ("/static/", "/favicon.ico")
_SILENT_EXACT = {"/api/dev/version"}


def _should_track(path: str) -> bool:
    if path in _SILENT_EXACT:
        return False
    return not path.startswith(_SILENT_PREFIXES)


class RequestContextMiddleware:
    """绑定 request-id（响应头 X-Request-ID）并记录访问日志。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = new_request_id()
        token = set_request_id(request_id)
        start = time.perf_counter()
        status_code = 500
        logged = False
        path = scope.get("path", "")
        method = str(scope.get("method", "?"))

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code, logged
            if message["type"] == "http.response.start":
                status_code = int(message.get("status", 500))
                headers = list(message.get("headers") or [])
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message["headers"] = headers
            elif message["type"] == "http.response.body" and not message.get("more_body") and not logged:
                logged = True
                if _should_track(path):
                    duration_ms = (time.perf_counter() - start) * 1000
                    client = scope.get("client")
                    client_host = client[0] if client else ""
                    _access_logger.info(
                        "%s %s -> %d %.1fms client=%s req=%s",
                        method, path, status_code,
                        duration_ms, client_host, request_id,
                    )
            await send(message)

        try:
            with tracing_span("http.request", {"http.method": method, "http.path": path}) as active:
                await self.app(scope, receive, send_wrapper)
                if active is not None:
                    active.set_attribute("http.status_code", status_code)
        finally:
            reset_request_id(token)


class MetricsMiddleware:
    """统计 HTTP 请求计数与时长（路由名归一化，防标签基数爆炸）。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if not _should_track(path):
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method", "?"))
        start = time.perf_counter()
        status_code = 500
        recorded = False

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code, recorded
            if message["type"] == "http.response.start":
                status_code = int(message.get("status", 500))
            elif message["type"] == "http.response.body" and not message.get("more_body") and not recorded:
                recorded = True
                labels = {"method": method, "route": normalize_route(path)}
                metrics.inc_labeled("oa_http_requests_total", {**labels, "status": str(status_code)})
                metrics.observe("oa_http_request_duration_seconds", time.perf_counter() - start, labels)
            await send(message)

        await self.app(scope, receive, send_wrapper)


# ---- 认证与 RBAC 门（第 5 周）----

#: 豁免路径（公开）：健康检查/开发热刷新/认证入口/登录页/API 文档
_PUBLIC_EXACT = frozenset(
    {
        "/api/health",
        "/api/dev/version",
        "/api/auth/login",
        "/api/auth/logout",
        "/api/auth/session",
        "/login",
        "/favicon.ico",
        "/docs",
        "/redoc",
        "/openapi.json",
    }
)
_PUBLIC_PREFIXES = ("/static/",)

#: viewer 可用的只读查询/诊断类 POST（其余写操作一律要求 admin）
_VIEWER_POSTS = frozenset(
    {
        "/api/log/analyze",
        "/api/log/ocr",
        "/api/kb/ask",
        "/api/kb/chat",
        "/api/kb/chat/stream",
        "/api/kb/batch-ask",
        "/api/kb/conversation",
        "/api/kb/conversation/delete",
        "/api/kb/conversation/rename",
        "/api/net/dns",
        "/api/net/http",
        "/api/net/ping",
        "/api/net/port",
        "/api/net/trace",
        "/api/ssl/check",
        "/api/ssl/batch",
        "/api/sec/all",
        "/api/sec/cron",
        "/api/sec/firewall",
        "/api/sec/login",
        "/api/sec/ports",
        "/api/sec/ssh",
        "/api/db/mssql",
        "/api/db/mysql",
        "/api/db/mysql/slow",
        "/api/db/oracle",
        "/api/db/redis",
        "/api/kg/explore",
        "/api/kg/path",
        "/api/kg/search",
        "/api/v1/rag/query",
        "/api/v1/incidents/analyze",
    }
)


def is_public_path(path: str) -> bool:
    """认证门豁免路径（公开访问）。"""
    if path in _PUBLIC_EXACT:
        return True
    return path.startswith(_PUBLIC_PREFIXES)


def required_role(method: str, path: str) -> str:
    """路由所需最低角色：GET/HEAD/OPTIONS 与白名单 POST = viewer；其余 = admin。"""
    if method in ("GET", "HEAD", "OPTIONS"):
        return "viewer"
    if path in _VIEWER_POSTS:
        return "viewer"
    return "admin"


def _cookie_token(scope: Scope) -> str:
    for key, value in scope.get("headers", []):
        if key == b"cookie":
            cookie: SimpleCookie = SimpleCookie()
            try:
                cookie.load(value.decode("latin-1"))
            except Exception:  # noqa: BLE001 - 畸形 cookie 按无 cookie 处理
                return ""
            morsel = cookie.get(SESSION_COOKIE_NAME)
            return morsel.value if morsel is not None else ""
    return ""


def _client_host(scope: Scope) -> str:
    client = scope.get("client")
    return client[0] if client else ""


class AuthGateMiddleware:
    """认证与 RBAC 门。

    - 认证关闭 → 直通；回环客户端（bypass_loopback=true）→ 直通（桌面壳/本机浏览器）；
    - 远端客户端：要求有效会话 cookie，并按路由所需角色校验；
      API 返回 401/403 JSON，页面 302 → /login?next=...
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if is_public_path(path) or not auth_enabled(app_config):
            await self.app(scope, receive, send)
            return

        if bypass_loopback(app_config) and is_loopback_host(_client_host(scope)):
            await self.app(scope, receive, send)
            return

        record = None
        token = _cookie_token(scope)
        if token:
            try:
                record = db.get_session(hash_session_token(token))
            except Exception:  # noqa: BLE001 - 会话库异常按未登录处理，避免 500
                record = None

        if record is None:
            if path.startswith("/api/"):
                response = JSONResponse({"detail": "需要登录", "login_url": "/login"}, status_code=401)
            else:
                response = RedirectResponse(f"/login?next={quote(path, safe='')}", status_code=302)
            await response(scope, receive, send)
            return

        role = str(record.get("role") or "viewer")
        if required_role(str(scope.get("method", "GET")), path) == "admin" and role != "admin":
            response = JSONResponse({"detail": "权限不足（需要 admin 角色）"}, status_code=403)
            await response(scope, receive, send)
            return

        state = scope.setdefault("state", {})
        if isinstance(state, dict):
            state["oa_user"] = {"username": record.get("username"), "role": role}
        await self.app(scope, receive, send)
