"""Exercise real SSH output parsers without contacting a host."""

import socket
import subprocess
import sys
from unittest.mock import Mock

import paramiko
import pytest

import agents.inspection_real as inspection_real
from agents.inspection_agent import _parse_inspection_status
from agents.inspection_real import LocalInspector, RealInspector, _try_local_inspection, run_ssh_inspection
from utils.config import config


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


# ========== 磁盘解析补充分支（负数使用率 / 缺列 / 全部正常） ==========


def test_negative_disk_percentage_is_invalid_not_healthy(inspector, monkeypatch):
    monkeypatch.setattr(inspector, "_exec_command", Mock(return_value=("/data -1% 200G 210G", "")))
    report = inspector.check_disk()
    assert "  [告警] /data: 使用率未知（无效数据）" in report
    assert "  [告警] 总结: 磁盘状态未知，检测数据不完整" in report
    assert "所有磁盘正常" not in report


def test_disk_row_without_percentage_marks_incomplete(inspector, monkeypatch):
    output = "/ 50% 100G 50G\n/data 100G 20G"
    monkeypatch.setattr(inspector, "_exec_command", Mock(return_value=(output, "")))
    report = inspector.check_disk()
    assert "  [正常] /: 50%" in report
    assert "  [告警] 总结: 磁盘状态未知，检测数据不完整" in report


def test_truncated_disk_line_is_marked_incomplete(inspector, monkeypatch):
    # 真实场景：命令输出与其他进程交错，某一行只剩挂载点名（单字段无法解析）
    output = "/ 50% 100G 50G\n/data"
    monkeypatch.setattr(inspector, "_exec_command", Mock(return_value=(output, "")))
    report = inspector.check_disk()
    assert "  [正常] /: 50%" in report
    assert "  [告警] 总结: 磁盘状态未知，检测数据不完整" in report


def test_all_clean_mounts_report_normal_summary(inspector, monkeypatch):
    output = "/ 48% 200G 104G\n/boot 12% 1G 900M"
    monkeypatch.setattr(inspector, "_exec_command", Mock(return_value=(output, "")))
    report = inspector.check_disk()
    assert "  [正常] /boot: 12%" in report
    assert "  总结: 所有磁盘正常" in report
    assert "告警" not in report


# ========== RealInspector：连接 / 断开 / 远程命令 ==========


def test_connect_without_host_is_refused():
    inspector = RealInspector({"name": "oa02"})
    assert inspector.connect() is False
    assert inspector._connected is False


def test_connect_uses_password_and_skips_second_connect(monkeypatch):
    client = Mock()
    monkeypatch.setattr(paramiko, "SSHClient", Mock(return_value=client))
    inspector = RealInspector({
        "name": "oa03", "host": "192.0.2.13", "port": 2222, "user": "ops", "password": "s3cret",
    })
    assert inspector.connect() is True
    assert inspector.connect() is True
    assert client.connect.call_count == 1
    kwargs = client.connect.call_args.kwargs
    assert kwargs["hostname"] == "192.0.2.13"
    assert kwargs["port"] == 2222
    assert kwargs["username"] == "ops"
    assert kwargs["password"] == "s3cret"
    assert "pkey" not in kwargs


def test_connect_prefers_private_key_over_password(monkeypatch, tmp_path):
    client = Mock()
    monkeypatch.setattr(paramiko, "SSHClient", Mock(return_value=client))
    key_file = tmp_path / "id_rsa"
    key_file.write_text("-----BEGIN RSA PRIVATE KEY-----\nbody\n-----END RSA PRIVATE KEY-----\n", encoding="utf-8")
    sentinel_key = object()
    monkeypatch.setattr(paramiko.RSAKey, "from_private_key_file", Mock(return_value=sentinel_key))
    inspector = RealInspector({
        "name": "oa04", "host": "192.0.2.14", "private_key_path": str(key_file), "password": "unused",
    })
    assert inspector.connect() is True
    kwargs = client.connect.call_args.kwargs
    assert kwargs["pkey"] is sentinel_key
    assert "password" not in kwargs


def test_connect_without_credentials_is_refused(monkeypatch):
    client = Mock()
    monkeypatch.setattr(paramiko, "SSHClient", Mock(return_value=client))
    inspector = RealInspector({"name": "oa05", "host": "192.0.2.15"})
    assert inspector.connect() is False
    client.connect.assert_not_called()


def test_connect_failure_is_reported_as_false(monkeypatch):
    client = Mock()
    client.connect.side_effect = paramiko.SSHException("Authentication failed")
    monkeypatch.setattr(paramiko, "SSHClient", Mock(return_value=client))
    inspector = RealInspector({"name": "oa06", "host": "192.0.2.16", "password": "wrong-password"})
    assert inspector.connect() is False
    assert inspector._connected is False


def test_connect_without_paramiko_installed_returns_false(monkeypatch):
    monkeypatch.setitem(sys.modules, "paramiko", None)
    inspector = RealInspector({"name": "oa07", "host": "192.0.2.17", "password": "x"})
    assert inspector.connect() is False


def test_disconnect_closes_client_and_resets_state(inspector):
    client = Mock()
    inspector._client = client
    inspector._connected = True
    inspector.disconnect()
    client.close.assert_called_once()
    assert inspector._client is None
    assert inspector._connected is False


def test_disconnect_without_client_is_noop(inspector):
    inspector.disconnect()
    assert inspector._client is None
    assert inspector._connected is False


def test_disconnect_swallows_close_error(inspector):
    client = Mock()
    client.close.side_effect = RuntimeError("socket already closed")
    inspector._client = client
    inspector._connected = True
    inspector.disconnect()
    assert inspector._connected is False


def test_exec_command_without_connection_returns_error():
    inspector = RealInspector({"name": "oa08"})
    assert inspector._exec_command("uptime") == ("", "SSH 未连接")


def test_exec_command_decodes_stdout_stderr_and_passes_timeout(inspector):
    client = Mock()
    client.exec_command.return_value = (
        Mock(),
        Mock(read=Mock(return_value=b"  PORT_OK:80\n")),
        Mock(read=Mock(return_value=b"warning: deprecated option\n")),
    )
    inspector._client = client
    inspector._connected = True
    out, err = inspector._exec_command("ss -tlnp", timeout=7)
    assert out == "PORT_OK:80"
    assert err == "warning: deprecated option"
    assert client.exec_command.call_args.kwargs["timeout"] == 7


def test_exec_command_transport_error_is_captured(inspector):
    client = Mock()
    client.exec_command.side_effect = OSError("channel closed")
    inspector._client = client
    inspector._connected = True
    assert inspector._exec_command("free -m") == ("", "channel closed")


# ========== run_ssh_inspection / _try_local_inspection 模式调度 ==========


class _FakeLocalInspector:
    """桩：替代本机 wmic/netstat 检测。"""

    def __init__(self) -> None:
        self.hostname = "oa-win-test"

    def run_full_inspection(self) -> list[dict]:
        return [{
            "check_type": "ports",
            "check_type_cn": "端口检测",
            "target": self.hostname,
            "result": "[正常] 端口 80 监听中",
            "is_simulated": False,
        }]


class _BrokenLocalInspector:
    """桩：构造即失败，模拟本机检测不可用。"""

    def __init__(self) -> None:
        raise RuntimeError("本机检测不可用")


class _FakeRealInspector:
    """桩：替代 paramiko SSH 巡检并成功返回。"""

    def __init__(self, host_config: dict) -> None:
        self.host_config = host_config

    def run_full_inspection(self) -> list[dict]:
        name = self.host_config.get("name", "unknown")
        return [{
            "check_type": "ports",
            "check_type_cn": "端口检测",
            "target": name,
            "result": f"[正常] {name} 端口 80 监听中",
            "is_simulated": False,
        }]


class _FailingRealInspector:
    """桩：替代 paramiko SSH 巡检并抛出连接错误。"""

    def __init__(self, host_config: dict) -> None:
        self.host_config = host_config

    def run_full_inspection(self) -> list[dict]:
        raise RuntimeError("SSH 连接超时")


def _patch_config(monkeypatch, values: dict) -> None:
    """按点号路径替换 config.get 返回值，未覆盖的键回退到真实配置。"""
    original_get = config.get

    def fake_get(path: str, default=None):
        if path in values:
            return values[path]
        return original_get(path, default)

    monkeypatch.setattr(config, "get", fake_get)


def test_run_ssh_inspection_simulated_mode_returns_empty_result(monkeypatch):
    _patch_config(monkeypatch, {"inspection.mode": "simulated"})
    assert run_ssh_inspection() == {"success": True, "mode": "simulated", "results": [], "report": ""}


def test_run_ssh_inspection_unknown_mode_defaults_to_simulated(monkeypatch):
    _patch_config(monkeypatch, {"inspection.mode": "not-a-mode"})
    result = run_ssh_inspection()
    assert result["success"] is True
    assert result["mode"] == "simulated"
    assert result["results"] == []


def test_run_ssh_inspection_ssh_mode_without_hosts_fails(monkeypatch):
    _patch_config(monkeypatch, {"inspection.mode": "ssh", "inspection.ssh_hosts": []})
    result = run_ssh_inspection()
    assert result["success"] is False
    assert result["mode"] == "ssh"
    assert "未配置 SSH 主机" in result["error"]


def test_run_ssh_inspection_local_mode_delegates_to_local(monkeypatch):
    _patch_config(monkeypatch, {"inspection.mode": "local"})
    monkeypatch.setattr(inspection_real, "LocalInspector", _FakeLocalInspector)
    result = run_ssh_inspection()
    assert result["success"] is True
    assert result["mode"] == "local"
    assert result["results"][0]["target"] == "oa-win-test"
    assert "OA系统巡检报告（本机真实检测）" in result["report"]


def test_run_ssh_inspection_auto_mode_without_hosts_uses_local(monkeypatch):
    _patch_config(monkeypatch, {"inspection.mode": "auto", "inspection.ssh_hosts": []})
    monkeypatch.setattr(inspection_real, "LocalInspector", _FakeLocalInspector)
    assert run_ssh_inspection()["mode"] == "local"


def test_run_ssh_inspection_collects_all_hosts(monkeypatch):
    _patch_config(monkeypatch, {
        "inspection.mode": "ssh",
        "inspection.ssh_hosts": [
            {"name": "OA服务器1", "host": "192.0.2.21"},
            {"name": "OA服务器2", "host": "192.0.2.22"},
        ],
    })
    monkeypatch.setattr(inspection_real, "RealInspector", _FakeRealInspector)
    result = run_ssh_inspection()
    assert result["success"] is True
    assert result["mode"] == "ssh"
    assert [r["target"] for r in result["results"]] == ["OA服务器1", "OA服务器2"]
    assert "成功: 2/2 台主机" in result["report"]
    assert "共 2 项检测完成" in result["report"]


def test_run_ssh_inspection_reports_host_failures_in_ssh_mode(monkeypatch):
    _patch_config(monkeypatch, {
        "inspection.mode": "ssh",
        "inspection.ssh_hosts": [{"name": "OA服务器9", "host": "192.0.2.29"}],
    })
    monkeypatch.setattr(inspection_real, "RealInspector", _FailingRealInspector)
    result = run_ssh_inspection()
    assert result["success"] is False
    assert result["mode"] == "ssh"
    assert "OA服务器9" in result["error"]
    assert "SSH 连接超时" in result["error"]


def test_run_ssh_inspection_auto_mode_falls_back_to_local(monkeypatch):
    _patch_config(monkeypatch, {
        "inspection.mode": "auto",
        "inspection.ssh_hosts": [{"name": "OA服务器9", "host": "192.0.2.29"}],
    })
    monkeypatch.setattr(inspection_real, "RealInspector", _FailingRealInspector)
    monkeypatch.setattr(inspection_real, "LocalInspector", _FakeLocalInspector)
    result = run_ssh_inspection()
    assert result["success"] is True
    assert result["mode"] == "local"


def test_run_ssh_inspection_auto_mode_fails_honestly_when_all_sources_fail(monkeypatch):
    """第 4 周行为确认：auto 的 SSH 与 local 全部失败时返回明确错误
    （mode=auto、合并两路原因），不降级伪造模拟数据。"""
    _patch_config(monkeypatch, {
        "inspection.mode": "auto",
        "inspection.ssh_hosts": [{"name": "OA服务器9", "host": "192.0.2.29"}],
    })
    monkeypatch.setattr(inspection_real, "RealInspector", _FailingRealInspector)
    monkeypatch.setattr(inspection_real, "LocalInspector", _BrokenLocalInspector)
    result = run_ssh_inspection()
    assert result["success"] is False
    assert result["mode"] == "auto"
    assert "SSH 连接超时" in result["error"]
    assert "本机检测不可用" in result["error"]


def test_try_local_inspection_builds_report_from_results(monkeypatch):
    monkeypatch.setattr(inspection_real, "LocalInspector", _FakeLocalInspector)
    result = _try_local_inspection()
    assert result["success"] is True
    assert result["mode"] == "local"
    assert "主机名: oa-win-test" in result["report"]
    assert "[正常] 端口 80 监听中" in result["report"]
    assert "共 1 项检测完成" in result["report"]


def test_try_local_inspection_wraps_failure(monkeypatch):
    monkeypatch.setattr(inspection_real, "LocalInspector", _BrokenLocalInspector)
    assert _try_local_inspection() == {"success": False, "mode": "local", "error": "本机检测不可用"}


# ========== LocalInspector：本机 Windows 检测 ==========


def _make_local_inspector(hostname: str = "oa-win-test") -> LocalInspector:
    inspector = LocalInspector.__new__(LocalInspector)
    inspector.hostname = hostname
    return inspector


def _patch_run_cmd(monkeypatch, inspector, mapping: dict) -> None:
    def fake_run_cmd(command: str, timeout: int = 10) -> str:
        for marker, output in mapping.items():
            if marker in command:
                return output
        return ""

    monkeypatch.setattr(inspector, "_run_cmd", fake_run_cmd)


def test_local_inspector_hostname_comes_from_socket(monkeypatch):
    monkeypatch.setattr(socket, "gethostname", lambda: "oa-win-test")
    assert LocalInspector().hostname == "oa-win-test"


def test_local_run_cmd_returns_stdout_and_falls_back_to_stderr(monkeypatch):
    inspector = _make_local_inspector()
    monkeypatch.setattr(subprocess, "run", Mock(return_value=Mock(stdout="  C:\\  \n", stderr="")))
    assert inspector._run_cmd("dir") == "C:\\"
    kwargs = subprocess.run.call_args.kwargs
    assert kwargs["shell"] is True
    assert kwargs["encoding"] == "gbk"
    assert kwargs["timeout"] == 10

    monkeypatch.setattr(subprocess, "run", Mock(return_value=Mock(stdout="", stderr="错误: 命令未找到")))
    assert inspector._run_cmd("foobar") == "错误: 命令未找到"


def test_local_run_cmd_handles_timeout_and_crash(monkeypatch):
    inspector = _make_local_inspector()
    monkeypatch.setattr(subprocess, "run", Mock(side_effect=subprocess.TimeoutExpired(cmd="netstat", timeout=10)))
    assert inspector._run_cmd("netstat -ano") == ""

    monkeypatch.setattr(subprocess, "run", Mock(side_effect=OSError("WinError 5 拒绝访问")))
    assert inspector._run_cmd("netstat -ano") == "命令执行失败: WinError 5 拒绝访问"


def test_local_check_ports_marks_listening_and_missing(monkeypatch):
    inspector = _make_local_inspector()
    netstat_out = (
        "  TCP    0.0.0.0:80           0.0.0.0:0              LISTENING       4\n"
        "  TCP    0.0.0.0:8080         0.0.0.0:0              LISTENING       8800\r\n"
        "  TCP    127.0.0.1:6379       0.0.0.0:0              LISTENING       5210\n"
        "  TCP    0.0.0.0:3306         0.0.0.0:0              LISTENING       7100\r\n"
    )
    _patch_run_cmd(monkeypatch, inspector, {"netstat": netstat_out})
    report = inspector.check_ports()
    assert report.startswith("[oa-win-test] 端口检测:")
    assert "  [正常] 端口 80 (HTTP): 监听中" in report
    assert "  [正常] 端口 8080 (OA应用): 监听中" in report
    assert "  [正常] 端口 6379 (Redis): 监听中" in report
    assert "  [正常] 端口 3306 (MySQL): 监听中" in report
    assert "  [异常] 端口 443 (HTTPS): 未监听" in report


def test_local_check_ports_without_any_listener(monkeypatch):
    inspector = _make_local_inspector()
    _patch_run_cmd(monkeypatch, inspector, {"netstat": "  TCP    127.0.0.1:1433   0.0.0.0:0   LISTENING   4\n"})
    report = inspector.check_ports()
    assert report.count("[异常]") == 5
    assert "[正常]" not in report


def test_local_check_nginx_running_via_sc_query(monkeypatch):
    inspector = _make_local_inspector()
    _patch_run_cmd(monkeypatch, inspector, {
        "sc query": "SERVICE_NAME: nginx\r\n        STATE              : 4  RUNNING\r\n",
        "tasklist": "\r\nnginx.exe                    4321 Services                   0      5,432 K\r\n",
    })
    report = inspector.check_nginx()
    assert "  [正常] Nginx 进程运行中" in report
    assert "  PID: 4321" in report
    assert "PID: K" not in report


def test_local_check_nginx_detected_by_tasklist_only(monkeypatch):
    inspector = _make_local_inspector()
    _patch_run_cmd(monkeypatch, inspector, {
        "sc query": "服务不存在",
        "tasklist": "nginx.exe                    7777 Console                    1      1,024 K\r\n",
    })
    assert "  [正常] Nginx 进程运行中" in inspector.check_nginx()


def test_local_check_nginx_not_installed(monkeypatch):
    inspector = _make_local_inspector()
    _patch_run_cmd(monkeypatch, inspector, {
        "sc query": "",
        "tasklist": "信息: 没有运行的任务匹配指定标准。\r\n",
    })
    report = inspector.check_nginx()
    assert "  [信息] Nginx 未安装或未运行" in report
    assert "正常" not in report


def test_local_check_oa_service_with_java_process(monkeypatch):
    inspector = _make_local_inspector()
    tasklist_out = (
        "映像名称                       PID 会话名              会话#       内存使用\r\n"
        "========================= ======== ================ =========== ============\r\n"
        "java.exe                      9876 Console                    1  1,234,567 K\r\n"
    )
    _patch_run_cmd(monkeypatch, inspector, {"java.exe": tasklist_out})
    report = inspector.check_oa_service()
    assert "  [正常] Java 进程运行中" in report
    assert "  Java 进程数: 1" in report
    assert "  内存: 1,234,567 KB" in report


def test_local_check_oa_service_memory_without_unit_column(monkeypatch) -> None:
    """第 4 周修复：内存列固定为第 5 列；旧实现按 parts[-2] 取值，
    缺单位列时会误取「会话#」列。"""
    inspector = _make_local_inspector()
    tasklist_out = "java.exe 9876 Console 1 1,234,567"
    _patch_run_cmd(monkeypatch, inspector, {"java.exe": tasklist_out})
    report = inspector.check_oa_service()
    assert "  内存: 1,234,567 KB" in report
    assert "  内存: 1 KB" not in report


def test_local_check_oa_service_without_java(monkeypatch):
    inspector = _make_local_inspector()
    _patch_run_cmd(monkeypatch, inspector, {"java.exe": "信息: 没有运行的任务匹配指定标准。\r\n"})
    report = inspector.check_oa_service()
    assert "  [信息] 未检测到 Java 进程" in report
    assert "Tomcat/Java" in report


def test_local_check_disk_reports_percent_threshold_and_size(monkeypatch):
    _patch_config(monkeypatch, {"inspection.disk_threshold": 85})
    inspector = _make_local_inspector()
    _patch_run_cmd(monkeypatch, inspector, {
        "logicaldisk": (
            "\r\nNode,Caption,FreeSpace,Size\r\n"
            "OA-WEB-01,C:,50000000000,100000000000\r\n"
            "OA-WEB-01,D:,9000000000,100000000000\r\n"
        ),
    })
    report = inspector.check_disk()
    assert "  [正常] C:: 50.0% (93.1GB)" in report
    assert "  [告警] D:: 91.0% (93.1GB, 阈值85%)" in report


def test_local_check_disk_reports_parse_failure_and_skips_empty_drive(monkeypatch):
    _patch_config(monkeypatch, {"inspection.disk_threshold": 85})
    inspector = _make_local_inspector()
    _patch_run_cmd(monkeypatch, inspector, {
        "logicaldisk": "OA-WEB-01,E:,not-a-number,100000000000\r\nOA-WEB-01,F:,0,0\r\n",
    })
    report = inspector.check_disk()
    assert "  E:: 解析失败" in report
    assert "F:" not in report


def test_local_check_disk_without_data(monkeypatch):
    inspector = _make_local_inspector()
    _patch_run_cmd(monkeypatch, inspector, {"logicaldisk": ""})
    assert inspector.check_disk() == "[oa-win-test] 磁盘检测: 无数据"


def test_local_check_memory_normal_branch(monkeypatch):
    _patch_config(monkeypatch, {"inspection.memory_threshold": 90})
    inspector = _make_local_inspector()
    _patch_run_cmd(monkeypatch, inspector, {
        "OS get": "\r\nNode,FreePhysicalMemory,TotalVisibleMemorySize\r\nOA-WEB-01,4000000,16000000\r\n",
    })
    report = inspector.check_memory()
    assert "  [正常] 内存使用率: 75.0%" in report
    assert "  总内存: 15.3GB | 已用: 11.4GB" in report


def test_local_check_memory_warning_branch(monkeypatch):
    _patch_config(monkeypatch, {"inspection.memory_threshold": 90})
    inspector = _make_local_inspector()
    _patch_run_cmd(monkeypatch, inspector, {"OS get": "OA-WEB-01,500000,16000000\r\n"})
    assert "  [告警] 内存使用率: 96.9% (阈值90%)" in inspector.check_memory()


def test_local_check_memory_parse_failures_and_no_data(monkeypatch):
    inspector = _make_local_inspector()
    _patch_run_cmd(monkeypatch, inspector, {"OS get": "OA-WEB-01,abc,def\r\nOA-WEB-01,0,0\r\n"})
    assert "  内存数据解析失败" in inspector.check_memory()

    _patch_run_cmd(monkeypatch, inspector, {"OS get": ""})
    assert inspector.check_memory() == "[oa-win-test] 内存检测: 无数据"


def test_local_full_inspection_collects_five_checks(monkeypatch):
    inspector = _make_local_inspector()
    for method_name in ["check_ports", "check_nginx", "check_oa_service", "check_disk", "check_memory"]:
        monkeypatch.setattr(inspector, method_name, Mock(return_value=f"[正常] {method_name}"))
    results = inspector.run_full_inspection()
    assert [r["check_type"] for r in results] == ["ports", "nginx", "oa", "disk", "memory"]
    assert [r["check_type_cn"] for r in results] == ["端口检测", "Nginx服务", "OA应用服务", "磁盘使用", "内存使用"]
    assert all(r["target"] == "oa-win-test" and r["is_simulated"] is False for r in results)
    assert results[2]["result"] == "[正常] check_oa_service"


def test_local_full_inspection_isolates_method_failure(monkeypatch):
    inspector = _make_local_inspector()
    monkeypatch.setattr(inspector, "check_disk", Mock(side_effect=RuntimeError("wmic 缺失")))
    for method_name in ["check_ports", "check_nginx", "check_oa_service", "check_memory"]:
        monkeypatch.setattr(inspector, method_name, Mock(return_value="[正常] ok"))
    results = inspector.run_full_inspection()
    assert len(results) == 5
    assert results[3]["result"] == "[错误] 磁盘使用 检测异常: wmic 缺失"
