"""第 5 周 w5-2：Prometheus 指标原语扩展（gauge/histogram/标签/路由归一）。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ui.middleware import MetricsMiddleware, RequestContextMiddleware
from utils.metrics import MetricsRegistry, init_default_metrics, metrics, normalize_route

pytestmark = pytest.mark.allow_hosts(["127.0.0.1", "::1", "localhost"])


def test_labeled_counter_accumulates_and_renders() -> None:
    registry = MetricsRegistry()
    registry.inc_labeled("oa_x_total", {"method": "GET", "route": "/a"})
    registry.inc_labeled("oa_x_total", {"route": "/a", "method": "GET"}, value=2)
    assert registry.get_labeled("oa_x_total", {"method": "GET", "route": "/a"}) == 3
    text = registry.render()
    assert "# TYPE oa_x_total counter" in text
    assert 'oa_x_total{method="GET",route="/a"} 3' in text


def test_histogram_cumulative_render() -> None:
    registry = MetricsRegistry()
    for value in (0.001, 0.02, 0.3, 30.0):
        registry.observe("oa_latency_seconds", value)
    state = registry.get_histogram("oa_latency_seconds")
    assert state is not None
    count, total, buckets = state
    assert count == 4
    assert total == pytest.approx(30.321)
    assert sum(buckets) == 3  # 超最大桶的那个只进 count/sum

    text = registry.render()
    assert "# TYPE oa_latency_seconds histogram" in text
    assert 'oa_latency_seconds_bucket{le="0.005"} 1' in text
    assert 'oa_latency_seconds_bucket{le="0.025"} 2' in text
    assert 'oa_latency_seconds_bucket{le="+Inf"} 4' in text
    assert "oa_latency_seconds_sum 30.321" in text
    assert "oa_latency_seconds_count 4" in text


def test_histogram_with_labels() -> None:
    registry = MetricsRegistry()
    registry.observe("oa_latency_seconds", 0.01, {"method": "GET", "route": "/ping"})
    registry.observe("oa_latency_seconds", 0.02, {"method": "GET", "route": "/ping"})
    text = registry.render()
    assert 'oa_latency_seconds_count{method="GET",route="/ping"} 2' in text
    assert 'oa_latency_seconds_bucket{method="GET",route="/ping",le="+Inf"} 2' in text


def test_gauge_set_and_render() -> None:
    registry = MetricsRegistry()
    registry.set_gauge("oa_build_info", 1, {"version": "9.9.9"})
    registry.set_gauge("oa_build_info", 1, {"version": "9.9.9"})  # 幂等覆盖
    registry.set_gauge("oa_plain_gauge", 42)
    assert registry.get_gauge("oa_plain_gauge") == 42
    text = registry.render()
    assert "# TYPE oa_build_info gauge" in text
    assert 'oa_build_info{version="9.9.9"} 1' in text
    assert "oa_plain_gauge 42" in text


def test_label_value_escaping() -> None:
    registry = MetricsRegistry()
    registry.inc_labeled("oa_escape_total", {"path": 'a"b\\c'})
    line = next(line for line in registry.render().splitlines() if line.startswith("oa_escape_total"))
    assert line == 'oa_escape_total{path="a\\"b\\\\c"} 1'


def test_reset_clears_all_kinds() -> None:
    registry = MetricsRegistry()
    registry.inc("c_total")
    registry.inc_labeled("lc_total", {"a": "1"})
    registry.set_gauge("g", 1)
    registry.observe("h_seconds", 0.1)
    registry.reset()
    assert registry.render() == ""
    assert registry.get_histogram("h_seconds") is None


def test_legacy_counter_format_unchanged() -> None:
    registry = MetricsRegistry()
    registry.inc("oa_incidents_analyze_total")
    assert registry.render() == "# TYPE oa_incidents_analyze_total counter\noa_incidents_analyze_total 1\n"
    assert registry.snapshot() == {"oa_incidents_analyze_total": 1}
    assert registry.get("oa_incidents_analyze_total") == 1


def test_normalize_route_collapses_ids() -> None:
    cases = {
        "/": "/",
        "": "/",
        "/api/health": "/api/health",
        "/api/v1/incidents/INC-2026-ABCDEF": "/api/v1/incidents/{id}",
        "/api/v1/incidents/12345": "/api/v1/incidents/{id}",
        "/api/v1/incidents/deadbeef1234": "/api/v1/incidents/{id}",
        "/login": "/login",
        "/random/unknown/path": "/<other>",
    }
    for raw, expected in cases.items():
        assert normalize_route(raw) == expected


def test_init_default_metrics_idempotent() -> None:
    metrics.reset()
    try:
        init_default_metrics("9.9.9")
        init_default_metrics("9.9.9")
        text = metrics.render()
        assert 'oa_build_info{version="9.9.9"} 1' in text
        assert "oa_process_start_time_seconds" in text
    finally:
        metrics.reset()


def test_metrics_middleware_counts_requests() -> None:
    metrics.reset()
    try:
        app = FastAPI()
        app.add_middleware(MetricsMiddleware)  # 先加 = 更内层
        app.add_middleware(RequestContextMiddleware)  # 后加 = 更外层

        @app.get("/api/ping")
        def ping() -> dict[str, bool]:
            return {"ok": True}

        @app.get("/api/v1/incidents/{incident_id}")
        def incident(incident_id: str) -> dict[str, str]:
            return {"id": incident_id}

        client = TestClient(app)
        client.get("/api/ping")
        client.get("/api/v1/incidents/INC-1")

        text = metrics.render()
        assert 'oa_http_requests_total{method="GET",route="/api/ping",status="200"} 1' in text
        assert 'oa_http_requests_total{method="GET",route="/api/v1/incidents/{id}",status="200"} 1' in text
        assert 'oa_http_request_duration_seconds_count{method="GET",route="/api/ping"} 1' in text
    finally:
        metrics.reset()


def test_metrics_middleware_skips_noise_paths() -> None:
    metrics.reset()
    try:
        app = FastAPI()
        app.add_middleware(MetricsMiddleware)

        @app.get("/api/dev/version")
        def dev_version() -> dict[str, str]:
            return {"version": "0"}

        client = TestClient(app)
        client.get("/api/dev/version")
        client.get("/static/nonexistent.css")
        assert "oa_http_requests_total" not in metrics.render()
    finally:
        metrics.reset()
