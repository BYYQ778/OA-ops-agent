import asyncio
import re
from collections import deque
from unittest.mock import Mock

import pytest

import utils.dashboard as dashboard
from utils.dashboard import parse_inspection_to_metrics


def test_parse_inspection_text_into_dashboard_metrics() -> None:
    report = """巡检时间: 2026-09-16 09:30:00
[server01] 端口检测:
[正常] 端口 80 正常监听
[server01] Nginx检测:
[正常] Nginx 服务运行正常
[server01] OA应用检测:
[告警] OA应用响应时间 2500ms
[server01] 磁盘检测:
[告警] /dev/sdb1 (/数据盘): 使用率 88%
[server01] 内存检测:
[正常] 总内存 8192MB, 已用 4096MB, 使用率 50%
"""

    metrics = parse_inspection_to_metrics(report)

    assert metrics["timestamp"] == "2026-09-16 09:30:00"
    assert metrics["summary"]["total_checks"] == 5
    assert metrics["summary"]["warning"] == 2
    assert {check["type"] for check in metrics["checks"]} == {
        "ports", "nginx", "oa", "disk", "memory"
    }


# ========== 追加：各格式解析分支、时间序列与 DashboardManager ==========

FULL_SAMPLE = """=======================================================
OA系统巡检报告（模拟数据）
巡检时间: 2026-06-15 14:30:00
巡检模式: 模拟数据
=======================================================

端口检测完成，状态: 正常
  [正常] 端口 80 (HTTP服务): 监听中，延迟 0.5ms
  [正常] 端口 443 (HTTPS服务): 监听中，延迟 0.8ms

[正常] Nginx服务运行中
  运行时长: 120小时
  活跃连接数: 234

[告警] OA应用服务响应缓慢！
  HTTP响应时间: 4500ms (超过阈值2000ms)

磁盘检测完成 —— 存在磁盘告警，请及时清理或扩容！
  [正常] /dev/sda1 (/根目录): 使用率 45%
  [告警] /dev/sdb1 (/数据盘): 使用率 88% (超过阈值85%)

[正常] 内存使用正常
  总内存: 32GB
  使用率: 62%
======================================================="""


def test_extract_number_reads_first_number() -> None:
    assert dashboard._extract_number("使用率 88.5%") == 88.5
    assert dashboard._extract_number("无数据") is None


@pytest.mark.parametrize(
    "text,expected",
    [
        ("[严重] 磁盘已满", "error"),
        ("[异常] 端口不通", "error"),
        ("[告警] 内存偏高", "warning"),
        ("[正常] 服务运行中", "normal"),
        ("[信息] 巡检完成", "normal"),
    ],
)
def test_parse_status_levels(text, expected) -> None:
    assert dashboard._parse_status(text) == expected


def test_parse_ports_standard_and_ssh_formats() -> None:
    standard = "[正常] 端口 80 (HTTP服务): 监听中，延迟 0.5ms\n[异常] 端口 3306 (MySQL数据库): 未监听"

    parsed = dashboard._parse_ports(standard)

    assert parsed["summary"] == "1/2 端口正常"
    assert [item["label"] for item in parsed["items"]] == ["80 HTTP服务", "3306 MySQL数据库"]
    assert [item["status"] for item in parsed["items"]] == ["ok", "error"]
    assert parsed["items"][0]["detail"] == "监听中，延迟 0.5ms"

    fallback = dashboard._parse_ports("[信息] PORT_OK:80 PORT_DOWN:3306")
    assert [item["detail"] for item in fallback["items"]] == ["监听中", "未监听"]
    assert fallback["summary"] == "1/2 端口正常"

    assert dashboard._parse_ports("[信息] 端口检测不可用")["summary"] == "无数据"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("[正常] Nginx服务运行中\n运行时长: 120小时\n活跃连接数: 234", "运行 120h · 连接 234"),
        ("[正常] Nginx服务运行中", "运行中"),
        ("[告警] Nginx 已停止", "已停止"),
        ("[异常] Nginx 配置文件存在错误", "配置异常"),
        ("[信息] Nginx 未安装", "未安装/未运行"),
        ("[信息] NGINX_RUNNING\nNGINX_CONN:88", "运行中 · 连接 88"),
        ("[信息] NGINX_RUNNING", "运行中"),
        ("[信息] NGINX_STOPPED", "已停止"),
        ("[信息] Nginx 服务检查通过", "Nginx 服务检查通过"),
        ("[web01] Nginx 巡检完成", "Nginx 巡检完成"),
    ],
)
def test_parse_nginx_formats(text, expected) -> None:
    parsed = dashboard._parse_nginx(text)

    assert parsed["summary"] == expected
    assert parsed["name"] == "Nginx服务"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("[正常] OA应用服务运行正常\nHTTP响应时间: 260ms", "正常 · 260ms"),
        ("[正常] OA应用运行正常", "正常"),
        ("[告警] OA应用服务响应缓慢！\nHTTP响应时间: 4500ms (超过阈值2000ms)", "响应慢 · 4500ms"),
        ("[告警] OA应用响应缓慢", "响应缓慢"),
        ("[严重] OA应用疑似宕机", "疑似宕机"),
        ("[信息] 未检测到 Java/Tomcat 进程", "未检测到进程"),
        ("[信息] OA_RUNNING", "运行中"),
        ("[信息] OA_STOPPED", "已停止"),
        ("[app01] OA应用巡检完成", "OA应用巡检完成"),
    ],
)
def test_parse_oa_service_formats(text, expected) -> None:
    assert dashboard._parse_oa_service(text)["summary"] == expected


def test_parse_disk_formats_and_summary() -> None:
    simulated = (
        "磁盘检测完成\n"
        "  [正常] /dev/sda1 (/根目录): 使用率 45%\n"
        "  [告警] /dev/sdb1 (/数据盘): 使用率 88% (超过阈值85%)"
    )

    parsed = dashboard._parse_disk(simulated)

    assert [item["label"] for item in parsed["items"]] == ["/dev/sda1", "/dev/sdb1"]
    assert [item["status"] for item in parsed["items"]] == ["ok", "warning"]
    assert parsed["summary"] == "⚠ /dev/sdb1 88%"

    parsed_windows = dashboard._parse_disk("[异常] C:: 94.4% (200.8GB, 阈值85%)")
    assert parsed_windows["items"] == [{"label": "C::", "status": "error", "detail": "94.4%"}]
    assert parsed_windows["summary"] == "⚠ C:: 94.4%"
    assert parsed_windows["status"] == "error"

    assert dashboard._parse_disk("[正常] /dev/sda1 (/根目录): 使用率 30%")["summary"] == "全部正常"
    assert dashboard._parse_disk("[信息] 磁盘检测跳过")["summary"] == "无数据"


def test_parse_memory_formats() -> None:
    simulated = dashboard._parse_memory("[正常] 内存使用正常\n总内存: 32GB\n使用率: 62%\n可用内存: 12.2GB")
    assert simulated["summary"] == "62% · 32GB"
    assert simulated["status"] == "normal"

    local = dashboard._parse_memory("[告警] 内存使用率: 87.5%\n总内存: 31.7GB | 已用: 27.8GB")
    assert local["summary"] == "87.5% · 31.7GB"
    assert local["status"] == "warning"

    assert dashboard._parse_memory("[正常] 内存使用率: 43.3%")["summary"] == "43.3%"
    assert dashboard._parse_memory("[正常] 内存数据暂不可用")["summary"] == "内存数据暂不可用"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("端口检测完成，状态: 正常", "ports"),
        ("Nginx 服务运行中", "nginx"),
        ("OA应用响应缓慢", "oa"),
        ("内存使用率 62%", "memory"),
        ("磁盘 /dev/sda1 使用率 45%", "disk"),
        ("系统时间同步正常", None),
    ],
)
def test_classify_chunk_types(text, expected) -> None:
    assert dashboard._classify_chunk(text) == expected


def test_split_inspection_cuts_ai_report_section() -> None:
    raw = (
        "[server01] 端口检测:\n"
        "[正常] 端口 80 (HTTP服务): 监听中\n"
        "预警分析: 端口 80 正常\n"
        "详细数据\n"
        "[server01] 磁盘检测:\n"
        "[告警] /dev/sdb1 (/数据盘): 使用率 88%\n"
    )

    chunks = dashboard._split_inspection_text(raw)

    assert set(chunks) == {"ports"}
    assert "88%" not in chunks["ports"]


def test_split_inspection_handles_status_lines_and_ssh_markers() -> None:
    status_text = (
        "[正常] 端口 80 (HTTP服务): 监听中\n"
        "[正常] Nginx服务运行中\n"
        "[告警] OA应用服务响应缓慢\n"
        "[正常] /dev/sda1 (/根目录): 使用率 45%\n"
        "[正常] 内存使用正常\n"
    )

    chunks = dashboard._split_inspection_text(status_text)

    assert set(chunks) == {"ports", "nginx", "oa", "disk", "memory"}

    ssh_chunks = dashboard._split_inspection_text("=== PORTS ===\nPORT_OK:80\n=== UPTIME ===\nup 32 days\n")

    assert set(ssh_chunks) == {"ports"}
    assert dashboard._split_inspection_text("系统运行一切正常") == {}


def test_split_inspection_merges_repeated_sections() -> None:
    raw = (
        "[server01] 端口检测:\n"
        "[正常] 端口 80 (HTTP服务): 监听中\n"
        "[server01] 端口检测:\n"
        "[告警] 端口 8080 (OA应用端口): 未监听\n"
    )

    chunks = dashboard._split_inspection_text(raw)

    assert set(chunks) == {"ports"}
    assert "端口 80 (HTTP服务)" in chunks["ports"]
    assert "端口 8080 (OA应用端口)" in chunks["ports"]


def test_extract_timestamp_falls_back_to_now() -> None:
    stamp = dashboard._extract_timestamp("[server01] 端口检测:\n[正常] 端口 80 (HTTP服务): 监听中")

    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", stamp)

    assert dashboard._extract_timestamp("巡检时间: 2026-06-15 14:30:00\n[正常] 端口 80") == "2026-06-15 14:30:00"


def test_parse_inspection_full_report() -> None:
    metrics = parse_inspection_to_metrics(FULL_SAMPLE)

    assert metrics["timestamp"] == "2026-06-15 14:30:00"
    assert metrics["mode"] == "simulated"
    assert metrics["summary"] == {"total_checks": 5, "normal": 3, "warning": 2, "error": 0}
    assert [check["type"] for check in metrics["checks"]] == ["ports", "nginx", "oa", "disk", "memory"]
    assert [alert["severity"] for alert in metrics["alerts"]] == ["warning", "warning"]
    assert metrics["alerts"][0]["title"] == "OA应用: 响应慢 · 4500ms"


@pytest.mark.parametrize(
    "header,expected_mode",
    [("[server01] SSH 远程巡检报告", "ssh"), ("[local] 本机巡检报告", "local")],
)
def test_parse_inspection_detects_mode(header, expected_mode) -> None:
    metrics = parse_inspection_to_metrics(f"{header}\n[正常] 端口 80 (HTTP服务): 监听中\n")

    assert metrics["mode"] == expected_mode


def test_parse_inspection_with_partial_chunks() -> None:
    metrics = parse_inspection_to_metrics("[server01] 端口检测:\n[正常] 端口 80 (HTTP服务): 监听中\n")

    assert metrics["summary"]["total_checks"] == 1
    assert metrics["summary"]["normal"] == 1
    assert metrics["alerts"] == []


def test_parse_inspection_falls_back_when_parser_raises(monkeypatch) -> None:
    def boom(chunk: str) -> dict:
        raise ValueError("端口格式无法解析")

    monkeypatch.setitem(dashboard._CHECK_PARSERS, "ports", boom)

    metrics = parse_inspection_to_metrics("[server01] 端口检测:\n[异常] 端口 80 (HTTP服务): 连接被拒绝\n")

    fallback = metrics["checks"][0]
    assert fallback["type"] == "ports"
    assert fallback["name"] == "端口检测"
    assert fallback["status"] == "error"
    assert fallback["summary"] == "[server01] 端口检测:"
    assert fallback["items"] == []
    assert metrics["alerts"][0]["severity"] == "error"


def test_extract_timeseries_point_reads_items_and_memory() -> None:
    checks = [
        {
            "type": "disk",
            "items": [
                {"label": "/dev/sdb1", "status": "warning", "detail": "88%"},
                {"label": "C:", "status": "warning", "detail": "94.4%"},
                {"label": "异常值", "status": "error", "detail": "120%"},
            ],
            "summary": "⚠ /dev/sdb1 88%",
        },
        {"type": "memory", "summary": "62% · 32GB", "items": []},
    ]

    point = dashboard._extract_timeseries_point(checks, "2026-06-15 14:30:00")

    assert point["time"] == "14:30:00"
    assert point["_dev_sdb1"] == 88
    assert point["c:"] == 94.4
    assert "异常值" not in point
    assert point["memory_pct"] == 62
    assert dashboard._extract_timeseries_point([], "14:30")["time"] == "14:30"

    no_percentage = dashboard._extract_timeseries_point(
        [{"type": "memory", "summary": "内存数据暂不可用", "items": []}], "14:30"
    )
    assert "memory_pct" not in no_percentage


def test_dashboard_manager_is_singleton_and_manages_subscribers() -> None:
    manager = dashboard.DashboardManager()

    assert manager is dashboard.dashboard_manager

    queue = manager.subscribe()
    assert queue.maxsize == 16
    assert queue in manager._subscribers

    manager.unsubscribe(queue)
    assert queue not in manager._subscribers

    manager.unsubscribe(queue)
    assert queue not in manager._subscribers


def test_dashboard_latest_is_none_before_first_push(monkeypatch) -> None:
    manager = dashboard.DashboardManager()
    monkeypatch.setattr(manager, "_latest_metrics", None)

    assert manager.get_latest() is None


def test_dashboard_push_parses_and_broadcasts(monkeypatch) -> None:
    manager = dashboard.DashboardManager()
    monkeypatch.setattr(manager, "_subscribers", [])
    monkeypatch.setattr(manager, "_history", deque(maxlen=120))
    queue = manager.subscribe()

    manager.push(FULL_SAMPLE)

    latest = manager.get_latest()
    assert latest is not None
    assert latest["timestamp"] == "2026-06-15 14:30:00"
    assert queue.get_nowait()["timestamp"] == "2026-06-15 14:30:00"

    history = manager.get_history(minutes=60)
    assert len(history) == 1
    assert history[0]["time"] == "14:30:00"
    assert history[0]["_dev_sdb1"] == 88
    assert history[0]["memory_pct"] == 62
    assert len(manager.get_history(minutes=1)) == 1


def test_dashboard_push_tolerates_slow_and_broken_subscribers(monkeypatch) -> None:
    manager = dashboard.DashboardManager()
    monkeypatch.setattr(manager, "_subscribers", [])
    monkeypatch.setattr(manager, "_history", deque(maxlen=120))

    slow_queue = asyncio.Queue(maxsize=1)
    slow_queue.put_nowait({"occupied": True})
    broken = Mock()
    broken.put_nowait = Mock(side_effect=RuntimeError("连接已断开"))
    manager._subscribers.extend([slow_queue, broken])

    manager.push(FULL_SAMPLE)

    assert manager._subscribers == [slow_queue]
    latest = manager.get_latest()
    assert latest is not None
    assert latest["timestamp"] == "2026-06-15 14:30:00"


def test_dashboard_push_ignores_parse_failures(monkeypatch) -> None:
    manager = dashboard.DashboardManager()
    monkeypatch.setattr(manager, "_latest_metrics", None)
    monkeypatch.setattr(
        dashboard, "parse_inspection_to_metrics", Mock(side_effect=ValueError("报告格式异常"))
    )

    manager.push("损坏的巡检报告")

    assert manager.get_latest() is None
