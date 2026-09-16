"""Exercise real SSH output parsers without contacting a host."""

from unittest.mock import Mock

import pytest

from agents.inspection_agent import _parse_inspection_status
from agents.inspection_real import RealInspector


@pytest.fixture
def inspector():
    return RealInspector({"name": "oa01", "host": "192.0.2.10"})


@pytest.mark.parametrize("method", [
    "check_ports", "check_nginx", "check_oa_service", "check_disk", "check_memory",
])
def test_transport_failure_is_not_reported_as_healthy(inspector, monkeypatch, method):
    monkeypatch.setattr(inspector, "_exec_command", Mock(return_value=("", "SSH 未连接")))
    report = getattr(inspector, method)()
    assert "失败" in report
    assert "正常" not in report
    assert _parse_inspection_status(report) == "error"


@pytest.mark.parametrize("method", ["check_nginx", "check_oa_service", "check_memory"])
@pytest.mark.parametrize("output", ["", "unexpected remote output"])
def test_missing_service_evidence_is_explicit_warning(inspector, monkeypatch, method, output):
    monkeypatch.setattr(inspector, "_exec_command", Mock(return_value=(output, "")))
    report = getattr(inspector, method)()
    assert "[告警]" in report
    assert "未知" in report


@pytest.mark.parametrize("output", [
    "MEM_TOTAL:bad MEM_PCT:50", "MEM_TOTAL:0 MEM_USED:0 MEM_FREE:0 MEM_PCT:50",
    "MEM_TOTAL:8192 MEM_USED:bad MEM_FREE:4096 MEM_PCT:50",
    "MEM_TOTAL:8192 MEM_USED:4096 MEM_FREE:4096",
])
def test_invalid_or_incomplete_memory_fields_are_unknown(inspector, monkeypatch, output):
    monkeypatch.setattr(inspector, "_exec_command", Mock(return_value=(output, "")))
    report = inspector.check_memory()
    assert "[正常]" not in report
    assert "[告警]" in report and "未知" in report


def test_ports_preserve_service_and_down_port(inspector, monkeypatch):
    output = "PORT_OK:80\nPORT_OK:443\nPORT_DOWN:8080\nPORT_OK:3306\nPORT_OK:6379"
    monkeypatch.setattr(inspector, "_exec_command", Mock(return_value=(output, "")))
    report = inspector.check_ports()
    assert "异常端口: 8080" in report
    assert "端口 3306 (MySQL): 监听中" in report


@pytest.mark.parametrize("output", ["", "=== PORTS ===\npermission denied", "PORT_OK:80"])
def test_incomplete_port_evidence_cannot_claim_healthy(inspector, monkeypatch, output):
    monkeypatch.setattr(inspector, "_exec_command", Mock(return_value=(output, "")))
    report = inspector.check_ports()
    assert "状态: 正常" not in report
    assert "未知" in report


@pytest.mark.parametrize("output", [
    "", "=== DISK ===", "/data invalid% 100G 20G",
    "/ 50% 100G 50G\n/data missing 100G 20G", "/ 50% 100G 50G\n/data % 100G 20G",
])
def test_missing_or_invalid_disk_readings_cannot_claim_healthy(inspector, monkeypatch, output):
    monkeypatch.setattr(inspector, "_exec_command", Mock(return_value=(output, "")))
    report = inspector.check_disk()
    assert "所有磁盘正常" not in report
    assert "未知" in report


def test_disk_threshold_and_multiple_mounts(inspector, monkeypatch):
    monkeypatch.setattr(inspector, "_exec_command", Mock(return_value=("/ 50% 100G 50G\n/data 99% 100G 1G", "")))
    report = inspector.check_disk()
    assert "[正常] /: 50%" in report
    assert "[告警] /data: 99%" in report


def test_disk_overcommit_preserves_df_percentage(inspector, monkeypatch):
    monkeypatch.setattr(inspector, "_exec_command", Mock(return_value=("/data 101% 100G -1G", "")))
    report = inspector.check_disk()
    assert "[告警] /data: 101%" in report
    assert "未知" not in report


@pytest.mark.parametrize("pct", ["invalid", "nan", "inf", "-1", "101"])
def test_bad_memory_percent_is_unknown_not_exception_or_healthy(inspector, monkeypatch, pct):
    output = f"MEM_TOTAL:8192 MEM_USED:4096 MEM_FREE:4096 MEM_PCT:{pct}"
    monkeypatch.setattr(inspector, "_exec_command", Mock(return_value=(output, "")))
    report = inspector.check_memory()
    assert "未知" in report
    assert "[正常]" not in report
    assert "排查内存泄漏" not in report


@pytest.mark.parametrize("pct,expected", [(50, "[正常]"), (99, "[告警]")])
def test_memory_usage_and_capacity(inspector, monkeypatch, pct, expected):
    output = f"MEM_TOTAL:8192 MEM_USED:4096 MEM_FREE:4096 MEM_PCT:{pct}"
    monkeypatch.setattr(inspector, "_exec_command", Mock(return_value=(output, "")))
    report = inspector.check_memory()
    assert expected in report
    assert "总内存: 8192MB" in report


@pytest.mark.parametrize("method,output,expected", [
    ("check_nginx", "NGINX_STOPPED\nnginx: configuration test failed", "[异常]"),
    ("check_nginx", "NGINX_RUNNING\nsyntax is ok\nNGINX_CONN:2", "监听端口数: 2"),
    ("check_oa_service", "OA_STOPPED", "[严重]"),
    ("check_oa_service", "OA_RUNNING\nPID:123\nUPTIME:01:20\nLISTEN_PORTS:2", "进程PID: 123"),
])
def test_service_output_parsing(inspector, monkeypatch, method, output, expected):
    monkeypatch.setattr(inspector, "_exec_command", Mock(return_value=(output, "")))
    assert expected in getattr(inspector, method)()


def test_full_inspection_isolates_failed_check_and_disconnects(inspector, monkeypatch):
    monkeypatch.setattr(inspector, "_exec_command", Mock(return_value=("", "SSH 未连接")))
    monkeypatch.setattr(inspector, "check_ports", Mock(side_effect=RuntimeError("failed check")))
    disconnect = Mock()
    monkeypatch.setattr(inspector, "disconnect", disconnect)
    results = inspector.run_full_inspection()
    assert len(results) == 5
    assert "检测异常" in results[0]["result"]
    assert all(item["target"] == "oa01" and item["is_simulated"] is False for item in results)
    disconnect.assert_called_once()
