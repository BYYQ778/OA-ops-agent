"""请求级上下文（contextvars）：request-id / trace-id 贯穿日志与指标。

第 5 周引入：纯 ASGI 请求中间件（ui/middleware.py）为每个请求生成 request-id；
追踪模块（utils/tracing.py）在 span 生效时写入 trace-id。日志 formatter
（utils/logger.py）从本模块读取并落进结构化字段——不依赖任何框架。
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Iterator

_request_id: ContextVar[str] = ContextVar("oa_request_id", default="")
_trace_id: ContextVar[str] = ContextVar("oa_trace_id", default="")


def new_request_id() -> str:
    """生成短 request-id（16 位 hex，日志可读性优先）。"""
    return uuid.uuid4().hex[:16]


def get_request_id() -> str:
    return _request_id.get()


def set_request_id(value: str) -> Token[str]:
    return _request_id.set(value)


def reset_request_id(token: Token[str]) -> None:
    _request_id.reset(token)


def get_trace_id() -> str:
    return _trace_id.get()


def set_trace_id(value: str) -> Token[str]:
    return _trace_id.set(value)


def reset_trace_id(token: Token[str]) -> None:
    _trace_id.reset(token)


@contextmanager
def request_context(request_id: str = "") -> Iterator[str]:
    """在 with 块内绑定 request-id（可指定或自动生成），出块自动复位。"""
    rid = request_id or new_request_id()
    token = set_request_id(rid)
    try:
        yield rid
    finally:
        reset_request_id(token)
