"""纯 ASGI 中间件（第 5 周）：请求上下文 + 访问日志。

- 纯 ASGI 实现（非 BaseHTTPMiddleware）：不缓冲响应体，SSE 等流式端点安全透传；
- 不读取请求 body，只做上下文绑定、响应头回写与访问日志；
- 后续步骤（w5-2 / w5-6）在同一文件追加指标与认证门中间件。
"""

from __future__ import annotations

import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from utils.logger import get_logger
from utils.metrics import metrics, normalize_route
from utils.request_context import new_request_id, reset_request_id, set_request_id
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
