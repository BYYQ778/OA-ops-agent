"""Operational endpoint smoke tests with stubbed diagnostic tools.

The LangChain tool objects (ping_host, check_mysql_status, ...) are replaced
by deterministic stubs so the HTTP contract of the legacy router is verified
offline: payload shapes, defaults, and error branches.
"""

import asyncio
import os
from collections.abc import AsyncGenerator
from importlib import import_module
from typing import cast

import pytest
from fastapi.testclient import TestClient

import ui.server as server
from ui.server import create_app
from utils.dashboard import dashboard_manager
from utils.scheduler import InspectionScheduler

# utils 包命名空间把 "config" 绑定为 Config 实例（utils/__init__.py），
# 字符串形式的 monkeypatch 路径会被解析到实例上，因此直接持有模块对象。
config_module = import_module("utils.config")

pytestmark = pytest.mark.allow_hosts(["127.0.0.1", "::1", "localhost"])


class StubTool:
    """Stand-in for a LangChain StructuredTool: records invoke payloads."""

    def __init__(self, result: str = "stub-result") -> None:
        self.result = result
        self.payloads: list = []

    def invoke(self, payload: dict) -> str:
        self.payloads.append(payload)
        return self.result


@pytest.fixture()
def client():
    with TestClient(create_app(enable_background_services=False)) as test_client:
        yield test_client


# ============ 巡检 API ============


def test_inspect_run_returns_report_and_pushes_to_dashboard(client, monkeypatch) -> None:
    report = "巡检报告文本"
    pushed: list = []
    monkeypatch.setattr(server, "run_unified_inspection", lambda: report)
    monkeypatch.setattr(dashboard_manager, "push", pushed.append)

    response = client.post("/api/inspect/run")

    assert response.status_code == 200
    assert response.json() == {"result": report}
    assert pushed == [report]


def test_inspect_scheduler_endpoints_cover_lifecycle(client, monkeypatch) -> None:
    scheduler = InspectionScheduler()
    monkeypatch.setattr(server, "scheduler", scheduler)
    try:
        # 未运行时的分支
        assert client.get("/api/inspect/status").json()["running"] is False
        assert client.post("/api/inspect/stop").json()["ok"] is False
        assert client.post("/api/inspect/adjust", data={"interval": 60}).json()["ok"] is False

        started = client.post("/api/inspect/start", data={"interval": 1200}).json()
        assert started == {"ok": True, "msg": "已启动"}
        assert client.post("/api/inspect/start", data={"interval": 1200}).json() == {"ok": False, "msg": "已在运行中"}

        status = client.get("/api/inspect/status").json()
        assert status["running"] is True
        assert status["interval"] == 1200
        assert "运行中" in status["status"]

        adjusted = client.post("/api/inspect/adjust", data={"interval": 900}).json()
        assert adjusted == {"ok": True, "msg": "间隔已调整: 900秒"}

        stopped = client.post("/api/inspect/stop").json()
        assert stopped["ok"] is True
        assert stopped["msg"] == "已停止"
        assert "已停止" in stopped["status"]
    finally:
        scheduler.shutdown()


def test_inspect_history_uses_real_database(client) -> None:
    body = client.get("/api/inspect/history", params={"days": 3}).json()

    assert "summary" in body
    assert "records" in body


def test_inspect_history_reports_database_errors(client, monkeypatch) -> None:
    def boom(days: int):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(server.db, "get_inspection_summary", boom)

    assert client.get("/api/inspect/history").json() == {"error": "db unavailable"}


def test_db_overview_returns_stats_and_reports_errors(client, monkeypatch) -> None:
    body = client.get("/api/db/overview").json()
    assert "db_path" in body

    def boom():
        raise RuntimeError("stats broken")

    monkeypatch.setattr(server.db, "get_db_stats", boom)
    assert client.get("/api/db/overview").json() == {"error": "stats broken"}


# ============ 仪表盘 API ============


def test_dashboard_metrics_without_data_and_with_latest(client, monkeypatch) -> None:
    monkeypatch.setattr(dashboard_manager, "get_latest", lambda: None)
    assert client.get("/api/dashboard/metrics").json() == {
        "timestamp": None,
        "summary": None,
        "checks": [],
        "alerts": [],
        "mode": None,
    }

    latest = {
        "timestamp": "2026-09-17 10:00:00",
        "summary": {"total_checks": 0},
        "checks": [],
        "alerts": [],
        "mode": "simulated",
    }
    monkeypatch.setattr(dashboard_manager, "get_latest", lambda: latest)
    assert client.get("/api/dashboard/metrics").json() == latest


def test_dashboard_history_passes_window_to_manager(client, monkeypatch) -> None:
    captured: list = []
    monkeypatch.setattr(
        dashboard_manager,
        "get_history",
        lambda minutes: captured.append(minutes) or [{"time": "10:00:00"}],
    )

    body = client.get("/api/dashboard/history", params={"minutes": 15}).json()

    assert captured == [15]
    assert body == {"timeline": [{"time": "10:00:00"}]}


def test_dashboard_stream_endpoint_yields_metrics_and_unsubscribes(monkeypatch) -> None:
    # starlette（本版本）的 TestClient 会把流式响应整体缓冲到结束才返回，
    # 无限 SSE 端点不能走 HTTP 调用；这里直接驱动端点协程的 body_iterator。
    metric = {
        "timestamp": "2026-09-17 10:00:00",
        "summary": {"total_checks": 0},
        "checks": [],
        "alerts": [],
        "mode": "simulated",
    }
    real_subscribe = dashboard_manager.subscribe

    def preloaded_subscribe():
        queue = real_subscribe()
        queue.put_nowait(metric)
        return queue

    monkeypatch.setattr(dashboard_manager, "subscribe", preloaded_subscribe)

    async def read_first_event():
        response = await server.api_dashboard_stream()
        assert response.media_type == "text/event-stream"
        assert response.headers["cache-control"] == "no-cache"
        iterator = cast(AsyncGenerator[str, None], response.body_iterator)
        try:
            return await iterator.__anext__()
        finally:
            await iterator.aclose()

    first_line = asyncio.run(read_first_event())

    assert first_line.startswith("data: ")
    assert '"simulated"' in first_line
    assert dashboard_manager._subscribers == []


def test_stream_routes_are_registered() -> None:
    app = create_app(enable_background_services=False)
    # FastAPI 0.141 起 include_router 采用惰性 _IncludedRouter 包装，
    # app.routes 不再展开子路由；用 OpenAPI 路径表断言注册结果。
    paths = set(app.openapi()["paths"])

    assert "/api/dashboard/stream" in paths
    assert "/api/kb/chat/stream" in paths


# ============ 日志 OCR API ============


def test_log_ocr_success_runs_real_log_analyzer(client, monkeypatch) -> None:
    monkeypatch.setattr(
        "utils.ocr.extract_text_from_bytes",
        lambda content: "2026-09-17 ERROR 502 Bad Gateway upstream unavailable",
    )

    body = client.post("/api/log/ocr", files={"file": ("screenshot.png", b"fake-png", "image/png")}).json()

    assert body["text"].startswith("2026-09-17 ERROR 502")
    assert "502 Bad Gateway" in body["result"]


def test_log_ocr_failure_is_reported(client, monkeypatch) -> None:
    def broken(content):
        raise RuntimeError("ocr engine missing")

    monkeypatch.setattr("utils.ocr.extract_text_from_bytes", broken)

    body = client.post("/api/log/ocr", files={"file": ("a.png", b"x", "image/png")}).json()

    assert body == {"text": "", "result": "OCR 识别失败: ocr engine missing"}


def test_log_ocr_empty_result_asks_for_clearer_image(client, monkeypatch) -> None:
    monkeypatch.setattr("utils.ocr.extract_text_from_bytes", lambda content: "   ")

    body = client.post("/api/log/ocr", files={"file": ("a.png", b"x", "image/png")}).json()

    assert body == {"text": "", "result": "OCR 未能识别到文字，请确认图片清晰度并重试"}


# ============ SSL 证书 API ============


def test_ssl_check_and_batch_delegate_to_tools(client, monkeypatch) -> None:
    single = StubTool("证书有效")
    batch = StubTool("批量结果")
    monkeypatch.setattr(server, "check_cert_expiry", single)
    monkeypatch.setattr(server, "batch_check_certs", batch)

    assert client.post("/api/ssl/check", data={"domain": "oa.example.com"}).json() == {"result": "证书有效"}
    assert single.payloads == [{"domain": "oa.example.com"}]

    assert client.post("/api/ssl/batch", data={"domains": "a.com\nb.com"}).json() == {"result": "批量结果"}
    assert batch.payloads == [{"domains_text": "a.com\nb.com"}]


# ============ 网络诊断 API ============


def test_network_endpoints_delegate_to_tools(client, monkeypatch) -> None:
    cases = [
        ("/api/net/ping", {"host": "10.0.0.1"}, "ping_host", {"host": "10.0.0.1"}),
        ("/api/net/port", {"host_port": "10.0.0.1:443"}, "check_tcp_port", {"host_port": "10.0.0.1:443"}),
        ("/api/net/dns", {"domain": "oa.example.com"}, "dns_resolve", {"domain": "oa.example.com"}),
        ("/api/net/trace", {"host": "10.0.0.1"}, "traceroute_host", {"host": "10.0.0.1"}),
        ("/api/net/http", {"url": "https://oa.example.com"}, "http_health_check", {"url": "https://oa.example.com"}),
    ]
    for path, form, attribute, expected_payload in cases:
        stub = StubTool(f"result-for-{attribute}")
        monkeypatch.setattr(server, attribute, stub)

        body = client.post(path, data=form).json()

        assert body == {"result": f"result-for-{attribute}"}
        assert stub.payloads == [expected_payload]


# ============ 数据库诊断 API ============


def test_database_tool_endpoints_build_config_text(client, monkeypatch) -> None:
    cases = [
        (
            "/api/db/mysql",
            {"host": "db1", "port": 3307, "user": "ops", "password": "pw"},
            "check_mysql_status",
            "host=db1 port=3307 user=ops password=pw",
        ),
        (
            "/api/db/mysql/slow",
            {"host": "db1", "port": 3307, "user": "ops", "password": "pw"},
            "show_mysql_slow_queries",
            "host=db1 port=3307 user=ops password=pw limit=20",
        ),
        (
            "/api/db/redis",
            {"host": "c1", "port": 6380, "password": "rp"},
            "check_redis_status",
            "host=c1 port=6380 password=rp",
        ),
        (
            "/api/db/mssql",
            {"host": "m1", "port": 1433, "user": "sa", "password": "mp"},
            "check_mssql_status",
            "host=m1 port=1433 user=sa password=mp",
        ),
        (
            "/api/db/oracle",
            {"host": "o1", "port": 1521, "user": "system", "password": "op", "service": "orcl"},
            "check_oracle_status",
            "host=o1 port=1521 user=system password=op service=orcl",
        ),
    ]
    for path, form, attribute, expected_config in cases:
        stub = StubTool(f"result-for-{attribute}")
        monkeypatch.setattr(server, attribute, stub)

        body = client.post(path, data=form).json()

        assert body == {"result": f"result-for-{attribute}"}
        assert stub.payloads == [{"config_text": expected_config}]


def test_database_tool_endpoints_apply_defaults(client, monkeypatch) -> None:
    stub = StubTool("mysql-ok")
    monkeypatch.setattr(server, "check_mysql_status", stub)

    body = client.post("/api/db/mysql").json()

    assert body == {"result": "mysql-ok"}
    assert stub.payloads == [{"config_text": "host=127.0.0.1 port=3306 user=root password="}]


# ============ 安全基线 API ============


def test_security_endpoints_delegate_to_tools(client, monkeypatch) -> None:
    names = [
        "audit_ssh_config",
        "check_failed_logins",
        "audit_firewall_rules",
        "check_listening_ports",
        "audit_cron_jobs",
    ]
    routes = [
        ("/api/sec/ssh", "audit_ssh_config"),
        ("/api/sec/login", "check_failed_logins"),
        ("/api/sec/firewall", "audit_firewall_rules"),
        ("/api/sec/ports", "check_listening_ports"),
        ("/api/sec/cron", "audit_cron_jobs"),
    ]
    stubs = {name: StubTool(f"<{name}>") for name in names}
    for name, stub in stubs.items():
        monkeypatch.setattr(server, name, stub)

    for path, name in routes:
        assert client.post(path).json() == {"result": f"<{name}>"}
        assert stubs[name].payloads == [{}]

    full = client.post("/api/sec/all").json()["result"]
    assert "全量安全基线审计报告" in full
    for name in names:
        assert f"<{name}>" in full


# ============ 系统配置 API ============


def test_config_save_rejects_unknown_provider(client) -> None:
    body = client.post("/api/config/save", data={"provider": "nope"}).json()

    assert body["ok"] is False
    assert "无效的 provider" in body["error"]


def test_config_save_ollama_applies_explicit_values(client, monkeypatch) -> None:
    recorded: dict = {}
    monkeypatch.setattr(server.app_config, "update_file", lambda updates: recorded.update(updates) or True)
    monkeypatch.setattr(server, "_prewarm_kb", lambda: None)

    body = client.post(
        "/api/config/save",
        data={"provider": "ollama", "model": "qwen3:4b", "base_url": "http://127.0.0.1:11434/v1"},
    ).json()

    assert body == {"ok": True, "provider": "ollama", "model": "qwen3:4b"}
    assert recorded["llm.provider"] == "ollama"
    assert recorded["llm.ollama.model"] == "qwen3:4b"
    assert recorded["llm.ollama.base_url"] == "http://127.0.0.1:11434/v1"
    assert recorded["llm.ollama.api_key"] == "ollama"


def test_config_save_ollama_uses_defaults(client, monkeypatch) -> None:
    recorded: dict = {}
    monkeypatch.setattr(server.app_config, "update_file", lambda updates: recorded.update(updates) or True)
    monkeypatch.setattr(server, "_prewarm_kb", lambda: None)

    body = client.post("/api/config/save", data={"provider": "ollama"}).json()

    assert body["model"] == "qwen3:8b"
    assert recorded["llm.ollama.model"] == "qwen3:8b"
    assert recorded["llm.ollama.base_url"] == "http://localhost:11434/v1"


def test_config_save_cloud_provider_stores_key_via_env(client, monkeypatch) -> None:
    recorded: dict = {}
    saved_keys: list = []
    monkeypatch.setattr(server, "_save_api_key_to_env", lambda key: saved_keys.append(key) or True)
    monkeypatch.setattr(server.app_config, "update_file", lambda updates: recorded.update(updates) or True)
    monkeypatch.setattr(server, "_prewarm_kb", lambda: None)

    body = client.post(
        "/api/config/save",
        data={"provider": "deepseek", "api_key": "sk-test-only", "model": "deepseek-chat"},
    ).json()

    assert body == {"ok": True, "provider": "deepseek", "model": "deepseek-chat"}
    assert saved_keys == ["sk-test-only"]
    assert recorded["llm.model"] == "deepseek-chat"
    assert "api_key" not in recorded  # 明文 Key 只进 .env，不进 config.yaml


def test_config_save_stops_when_env_key_write_fails(client, monkeypatch) -> None:
    file_writes: list = []
    monkeypatch.setattr(server, "_save_api_key_to_env", lambda key: False)
    monkeypatch.setattr(server.app_config, "update_file", lambda updates: file_writes.append(updates) or True)
    monkeypatch.setattr(server, "_prewarm_kb", lambda: None)

    body = client.post("/api/config/save", data={"provider": "deepseek", "api_key": "sk-x"}).json()

    assert body == {"ok": False, "error": "API Key 写入 .env 失败，未保存配置"}
    assert file_writes == []


def test_config_save_reports_config_file_failure(client, monkeypatch) -> None:
    monkeypatch.setattr(server, "_save_api_key_to_env", lambda key: True)
    monkeypatch.setattr(server.app_config, "update_file", lambda updates: False)
    monkeypatch.setattr(server, "_prewarm_kb", lambda: None)

    body = client.post("/api/config/save", data={"provider": "deepseek", "api_key": "sk-x"}).json()

    assert body == {"ok": False, "error": "配置文件写入失败，请检查 config.yaml 是否被占用"}


def test_save_api_key_to_env_rewrites_existing_entry(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OA_OTHER=1\nOA_LLM_API_KEY=old-key\n", encoding="utf-8")
    monkeypatch.setattr(config_module, "PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("OA_LLM_API_KEY", "placeholder")

    assert server._save_api_key_to_env("new-key") is True

    content = env_file.read_text(encoding="utf-8")
    assert "OA_LLM_API_KEY=new-key\n" in content
    assert "old-key" not in content
    assert "OA_OTHER=1" in content
    assert os.environ["OA_LLM_API_KEY"] == "new-key"


def test_save_api_key_to_env_creates_missing_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config_module, "PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("OA_LLM_API_KEY", "placeholder")

    assert server._save_api_key_to_env("fresh-key") is True

    assert (tmp_path / ".env").read_text(encoding="utf-8") == "OA_LLM_API_KEY=fresh-key\n"
    assert os.environ["OA_LLM_API_KEY"] == "fresh-key"


def test_save_api_key_to_env_reports_write_failure(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config_module, "PROJECT_ROOT", str(tmp_path / "missing-directory"))

    assert server._save_api_key_to_env("x") is False


# ============ 运维命令大全 API ============


def test_commands_endpoint_parses_bundled_command_data(client) -> None:
    body = client.get("/api/commands").json()

    assert "error" not in body
    assert body["categories"]


def test_commands_endpoint_reports_missing_static_file(client, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(server, "BASE_DIR", str(tmp_path))

    body = client.get("/api/commands").json()

    assert body["categories"] == []
    assert body["commands"] == []
    assert "error" in body
