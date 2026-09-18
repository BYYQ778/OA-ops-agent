"""第 5 周 w5-3：OpenTelemetry 追踪薄封装（启用判定/降级/span 与 trace_id 贯通）。"""

from __future__ import annotations

import sys
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import utils.tracing as tracing_mod
from utils.request_context import get_trace_id
from utils.tracing import span, tracing_enabled

pytestmark = pytest.mark.allow_hosts(["127.0.0.1", "::1", "localhost"])

_FAKE_TRACE_ID = 0x0123456789ABCDEF0123456789ABCDEF


class _FakeSpanContext:
    def __init__(self, trace_id: int) -> None:
        self.trace_id = trace_id


class _FakeSpan:
    def __init__(self, trace_id: int) -> None:
        self.attributes: dict[str, Any] = {}
        self._context = _FakeSpanContext(trace_id)

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def get_span_context(self) -> _FakeSpanContext:
        return self._context


class _FakeSpanCM:
    def __init__(self, span_obj: _FakeSpan) -> None:
        self.span = span_obj
        self.exited = False

    def __enter__(self) -> _FakeSpan:
        return self.span

    def __exit__(self, *exc: Any) -> bool:
        self.exited = True
        return False


class _FakeTracer:
    def __init__(self) -> None:
        self.spans: list[tuple[str, _FakeSpan]] = []
        self.last_cm: _FakeSpanCM | None = None

    def start_as_current_span(self, name: str) -> _FakeSpanCM:
        span_obj = _FakeSpan(_FAKE_TRACE_ID)
        cm = _FakeSpanCM(span_obj)
        self.spans.append((name, span_obj))
        self.last_cm = cm
        return cm


@pytest.fixture()
def fake_tracer(monkeypatch: pytest.MonkeyPatch) -> _FakeTracer:
    tracer = _FakeTracer()
    monkeypatch.setattr(tracing_mod, "_tracer", tracer)
    return tracer


def test_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OA_TRACING", raising=False)
    assert tracing_enabled() is False
    with span("x") as active:
        assert active is None


def test_enabled_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OA_TRACING", "1")
    assert tracing_enabled() is True
    monkeypatch.setenv("OA_TRACING", "0")
    assert tracing_enabled() is False


def test_configure_degrades_without_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OA_TRACING", "1")
    monkeypatch.setitem(sys.modules, "opentelemetry", None)  # 强制 ImportError
    monkeypatch.setattr(tracing_mod, "_configured", False)
    monkeypatch.setattr(tracing_mod, "_tracer", None)
    assert tracing_mod.configure_tracing() is False
    assert tracing_mod._tracer is None


def test_span_sets_and_resets_trace_id(fake_tracer: _FakeTracer) -> None:
    with span("incident.analyze", {"incident.service": "oa"}) as active:
        assert active is not None
        assert active.attributes["incident.service"] == "oa"
        assert get_trace_id() == format(_FAKE_TRACE_ID, "032x")
    assert get_trace_id() == ""
    assert fake_tracer.spans[0][0] == "incident.analyze"


def test_span_resets_on_exception(fake_tracer: _FakeTracer) -> None:
    with pytest.raises(ValueError):
        with span("boom"):
            raise ValueError("boom")
    assert get_trace_id() == ""
    assert fake_tracer.last_cm is not None and fake_tracer.last_cm.exited


def test_nested_spans_restore_outer_trace_id(fake_tracer: _FakeTracer) -> None:
    with span("outer"):
        outer_id = get_trace_id()
        with span("inner"):
            assert get_trace_id() == outer_id  # 同一 trace
        assert get_trace_id() == outer_id
    assert get_trace_id() == ""


def test_http_middleware_emits_span(fake_tracer: _FakeTracer) -> None:
    from ui.middleware import RequestContextMiddleware

    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)

    @app.get("/api/ping")
    def ping() -> dict[str, str]:
        return {"trace_id": get_trace_id()}

    client = TestClient(app)
    response = client.get("/api/ping")
    assert response.json()["trace_id"] == format(_FAKE_TRACE_ID, "032x")
    span_names = [name for name, _ in fake_tracer.spans]
    assert "http.request" in span_names
