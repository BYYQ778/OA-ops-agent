"""第 5 周 w5-1：请求上下文中间件（request-id 贯通 + 访问日志）。"""

from __future__ import annotations

import asyncio
import io
import json
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.types import Message, Receive, Scope, Send

from ui.middleware import RequestContextMiddleware
from utils.logger import JsonFormatter, get_logger
from utils.request_context import get_request_id

pytestmark = pytest.mark.allow_hosts(["127.0.0.1", "::1", "localhost"])


def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)

    @app.get("/ping")
    def ping() -> dict[str, str]:
        get_logger("test.middleware").info("ping handled")
        return {"request_id": get_request_id()}

    return app


def _capture(logger: logging.Logger, formatter: logging.Formatter) -> tuple[io.StringIO, logging.Handler]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return stream, handler


def test_request_id_header_and_per_request_isolation() -> None:
    client = TestClient(_make_app())
    first = client.get("/ping")
    rid = first.headers.get("x-request-id")
    assert rid and len(rid) == 16
    assert first.json()["request_id"] == rid

    second = client.get("/ping")
    assert second.headers["x-request-id"] != rid


def test_request_id_flows_into_json_logs() -> None:
    logger = get_logger("test.middleware")
    stream, handler = _capture(logger, JsonFormatter())
    try:
        client = TestClient(_make_app())
        response = client.get("/ping")
        handler.flush()
        lines = [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
    finally:
        logger.removeHandler(handler)

    assert lines, "应至少产出 1 条日志"
    ping_lines = [line for line in lines if line["msg"] == "ping handled"]
    assert ping_lines and ping_lines[0]["request_id"] == response.headers["x-request-id"]


def test_access_log_line() -> None:
    logger = get_logger("oa.access")
    stream, handler = _capture(logger, logging.Formatter("%(message)s"))
    try:
        client = TestClient(_make_app())
        client.get("/ping")
    finally:
        logger.removeHandler(handler)

    output = stream.getvalue()
    assert "GET /ping -> 200" in output
    assert "req=" in output


def test_static_and_dev_noise_not_access_logged() -> None:
    logger = get_logger("oa.access")
    stream, handler = _capture(logger, logging.Formatter("%(message)s"))
    try:
        client = TestClient(_make_app())
        client.get("/static/nonexistent.css")
        client.get("/api/dev/version")
    finally:
        logger.removeHandler(handler)

    assert stream.getvalue() == ""


def test_non_http_scope_passes_through() -> None:
    """websocket/lifespan 等非 http scope 不生成 request-id，直接透传。"""
    captured: list[Scope] = []
    messages: list[Message] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        captured.append(scope)
        await send({"type": "websocket.close", "code": 1000})

    async def _send(message: Message) -> None:
        messages.append(message)

    async def _receive() -> Message:
        return {"type": "websocket.connect"}

    middleware = RequestContextMiddleware(app)
    asyncio.run(middleware({"type": "websocket", "path": "/ws"}, _receive, _send))
    assert captured and captured[0]["type"] == "websocket"
    assert messages and messages[0]["type"] == "websocket.close"
