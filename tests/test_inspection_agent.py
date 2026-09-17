"""离线单元测试：agents/inspection_agent.py（模拟巡检工具、报告汇总与统一巡检入口）。

外部边界（LLM / SSH 巡检 / 数据库 / AI 报告）全部打桩，测试不接触网络与真实服务。
"""

import runpy
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

import agents.ai_reporter as ai_reporter
import agents.inspection_agent as inspection_agent
import agents.inspection_real as inspection_real
from agents.inspection_agent import (
    INSPECTION_TOOLS,
    InspectionAgent,
    _parse_inspection_status,
    _try_ai_report,
    check_disk_usage,
    check_memory_usage,
    check_nginx_status,
    check_oa_service,
    check_ports,
    run_unified_inspection,
    save_inspection_to_db,
)
from utils.config import config


class _StubTool:
    """替代 LangChain @tool 对象，用于精确控制巡检工具的返回值或异常。"""

    def __init__(self, name: str, behavior: object) -> None:
        self.name = name
        self._behavior = behavior

    def invoke(self, payload: dict) -> str:
        if isinstance(self._behavior, Exception):
            raise self._behavior
        return str(self._behavior)


def _bare_agent() -> InspectionAgent:
    """构造不触发 LLM 初始化的 InspectionAgent（仅测工具调度/单项检测）。"""
    return InspectionAgent.__new__(InspectionAgent)


def _patch_config_get(monkeypatch, values: dict) -> None:
    """按点号路径替换 config.get 返回值，未覆盖的键回退到真实配置。"""
    original_get = config.get

    def fake_get(path: str, default=None):
        if path in values:
            return values[path]
        return original_get(path, default)

    monkeypatch.setattr(config, "get", fake_get)


def _stub_simulated_tools(monkeypatch) -> None:
    """把 5 个模拟工具替换为固定输出的桩，便于断言报告组装结果。"""
    stubs = {
        "check_ports": "  [正常] 端口 80 (HTTP服务): 监听中，延迟 0.5ms",
        "check_nginx_status": "[正常] Nginx服务运行中",
        "check_oa_service": "[正常] OA应用服务运行正常",
        "check_disk_usage": "[告警] /data: 使用率 92% (超过阈值85%)",
        "check_memory_usage": "[正常] 内存使用正常",
    }
    for tool_name, text in stubs.items():
        monkeypatch.setattr(inspection_agent, tool_name, _StubTool(tool_name, text))


def _stub_ssh_result(mode: str, results: list, success: bool = True) -> dict:
    """统一的 run_ssh_inspection 假返回值（results 传副本，避免跨测试污染）。"""
    return {"success": success, "mode": mode, "results": [dict(r) for r in results], "report": ""}


# ========== 模拟巡检工具（random 打桩保证确定性） ==========


def test_check_ports_all_listening(monkeypatch) -> None:
    monkeypatch.setattr(inspection_agent.random, "choices", lambda population, weights: [True])
    monkeypatch.setattr(inspection_agent.random, "uniform", lambda low, high: 1.234)
    report = check_ports.invoke({})
    assert report.startswith("端口检测完成，状态: 正常")
    assert "异常端口" not in report
    assert report.count("[正常]") == 5
    assert "  [正常] 端口 80 (HTTP服务): 监听中，延迟 1.23ms" in report


def test_check_ports_reports_every_down_port(monkeypatch) -> None:
    monkeypatch.setattr(inspection_agent.random, "choices", lambda population, weights: [False])
    report = check_ports.invoke({})
    assert report.startswith("端口检测完成，状态: 异常")
    assert "异常端口: 80, 443, 8080, 3306, 6379" in report
    assert report.count("[异常]") == 5
    assert "  [异常] 端口 6379 (Redis缓存): 未监听！" in report


def test_check_ports_mixed_marks_only_down_ports(monkeypatch) -> None:
    rolls = iter([True, True, False, True, False])
    monkeypatch.setattr(inspection_agent.random, "choices", lambda population, weights: [next(rolls)])
    monkeypatch.setattr(inspection_agent.random, "uniform", lambda low, high: 0.5)
    report = check_ports.invoke({})
    assert "异常端口: 8080, 6379" in report
    assert "  [正常] 端口 443 (HTTPS服务): 监听中，延迟 0.5ms" in report
    assert "  [异常] 端口 8080 (OA应用端口): 未监听！" in report


def test_check_nginx_running_branch(monkeypatch) -> None:
    monkeypatch.setattr(inspection_agent.random, "choices", lambda population, weights: ["running"])
    metrics = iter([240, 188])
    monkeypatch.setattr(inspection_agent.random, "randint", lambda low, high: next(metrics))
    report = check_nginx_status.invoke({})
    assert "[正常] Nginx服务运行中" in report
    assert "运行时长: 240小时" in report
    assert "活跃连接数: 188" in report
    assert "配置文件语法: OK" in report


@pytest.mark.parametrize("roll,expected,advice", [
    ("stopped", "[异常] Nginx服务已停止！", "systemctl start nginx"),
    ("config_error", "[异常] Nginx配置文件语法错误！", "nginx -t 检查配置文件"),
])
def test_check_nginx_failure_branches(monkeypatch, roll, expected, advice) -> None:
    monkeypatch.setattr(inspection_agent.random, "choices", lambda population, weights: [roll])
    report = check_nginx_status.invoke({})
    assert expected in report
    assert advice in report


@pytest.mark.parametrize("roll,expected,detail", [
    ("normal", "[正常] OA应用服务运行正常", "HTTP响应时间: 500.0ms"),
    ("slow", "[告警] OA应用服务响应缓慢！", "HTTP响应时间: 8000.0ms (超过阈值2000ms)"),
    ("down", "[严重] OA应用服务疑似宕机！", "HTTP 503 Service Unavailable"),
])
def test_check_oa_service_branches(monkeypatch, roll, expected, detail) -> None:
    monkeypatch.setattr(inspection_agent.random, "choices", lambda population, weights: [roll])
    monkeypatch.setattr(inspection_agent.random, "uniform", lambda low, high: float(high))
    monkeypatch.setattr(inspection_agent.random, "randint", lambda low, high: 42)
    report = check_oa_service.invoke({})
    assert expected in report
    assert detail in report


def test_check_disk_usage_alert_branch(monkeypatch) -> None:
    monkeypatch.setattr(inspection_agent.random, "random", lambda: 0.05)
    monkeypatch.setattr(inspection_agent.random, "randint", lambda low, high: 92)
    report = check_disk_usage.invoke({})
    assert "存在磁盘告警，请及时清理或扩容！" in report
    assert "  [告警] /dev/sda1 (/根目录): 使用率 92% (超过阈值85%)" in report
    assert report.count("[告警]") == 2


def test_check_disk_usage_normal_branch(monkeypatch) -> None:
    monkeypatch.setattr(inspection_agent.random, "random", lambda: 0.9)
    monkeypatch.setattr(inspection_agent.random, "randint", lambda low, high: 47)
    report = check_disk_usage.invoke({})
    assert report.startswith("磁盘检测完成")
    assert "存在磁盘告警" not in report
    assert report.count("[正常]") == 2
    assert "  [正常] /dev/sdb1 (/data数据盘): 使用率 47%" in report


def test_check_memory_usage_alert_branch(monkeypatch) -> None:
    monkeypatch.setattr(inspection_agent.random, "choice", lambda seq: 32)
    monkeypatch.setattr(inspection_agent.random, "random", lambda: 0.05)
    monkeypatch.setattr(inspection_agent.random, "randint", lambda low, high: 95)
    report = check_memory_usage.invoke({})
    assert "[告警] 内存使用率过高！" in report
    assert "总内存: 32GB" in report
    assert "使用率: 95%" in report
    assert "可用内存: 1.6GB" in report
    assert "OOM Killer" in report


def test_check_memory_usage_normal_branch(monkeypatch) -> None:
    monkeypatch.setattr(inspection_agent.random, "choice", lambda seq: 16)
    monkeypatch.setattr(inspection_agent.random, "random", lambda: 0.9)
    monkeypatch.setattr(inspection_agent.random, "randint", lambda low, high: 50)
    report = check_memory_usage.invoke({})
    assert "[正常] 内存使用正常" in report
    assert "总内存: 16GB" in report
    assert "使用率: 50%" in report
    assert "可用内存: 8.0GB" in report


def test_inspection_tool_registry_exposes_five_checks() -> None:
    assert [tool.name for tool in INSPECTION_TOOLS] == [
        "check_ports",
        "check_nginx_status",
        "check_oa_service",
        "check_disk_usage",
        "check_memory_usage",
    ]


# ========== 单项检测调度（run_single_check） ==========


def test_run_single_check_supports_all_documented_checks(monkeypatch) -> None:
    monkeypatch.setattr(inspection_agent.random, "choices", lambda population, weights: [population[0]])
    monkeypatch.setattr(inspection_agent.random, "uniform", lambda low, high: float(high))
    monkeypatch.setattr(inspection_agent.random, "random", lambda: 0.9)
    monkeypatch.setattr(inspection_agent.random, "randint", lambda low, high: 40)
    monkeypatch.setattr(inspection_agent.random, "choice", lambda seq: seq[0])
    agent = _bare_agent()
    assert "端口检测完成，状态: 正常" in agent.run_single_check("ports")
    assert "[正常] Nginx服务运行中" in agent.run_single_check("nginx")
    assert "[正常] OA应用服务运行正常" in agent.run_single_check("oa")
    assert "磁盘检测完成" in agent.run_single_check("disk")
    assert "[正常] 内存使用正常" in agent.run_single_check("memory")


def test_run_single_check_unknown_name_lists_options() -> None:
    report = _bare_agent().run_single_check("cpu")
    assert report.startswith("不支持的检测项: cpu")
    assert "['ports', 'nginx', 'oa', 'disk', 'memory']" in report


def test_run_single_check_wraps_tool_failure(monkeypatch) -> None:
    monkeypatch.setattr(inspection_agent, "check_ports", _StubTool("check_ports", RuntimeError("ss 命令不可用")))
    assert _bare_agent().run_single_check("ports") == "检测失败: ss 命令不可用"


# ========== InspectionAgent：LLM 边界打桩 ==========


@pytest.fixture
def stubbed_agent(monkeypatch):
    """构造 InspectionAgent，但把 ChatOpenAI / create_agent 打桩为本地 Mock。"""
    recorded: dict = {}

    class _FakeChatOpenAI:
        def __init__(self, **kwargs):
            recorded["llm_kwargs"] = kwargs

    def _fake_create_agent(**kwargs):
        recorded["agent_kwargs"] = kwargs
        graph = Mock()
        graph.invoke.return_value = {"messages": [Mock(content="巡检报告: 全部正常")]}
        return graph

    monkeypatch.setattr(inspection_agent, "ChatOpenAI", _FakeChatOpenAI)
    monkeypatch.setattr(inspection_agent, "create_agent", _fake_create_agent)
    agent = InspectionAgent(llm_api_key="test-key", llm_base_url="http://127.0.0.1:9999/v1", llm_model="test-model")
    return agent, recorded


def test_agent_init_wires_llm_and_inspection_tools(stubbed_agent) -> None:
    _, recorded = stubbed_agent
    assert recorded["llm_kwargs"] == {
        "api_key": "test-key",
        "base_url": "http://127.0.0.1:9999/v1",
        "model": "test-model",
        "temperature": 0.3,
        "max_tokens": 2048,
    }
    assert recorded["agent_kwargs"]["tools"] == INSPECTION_TOOLS
    system_prompt = recorded["agent_kwargs"]["system_prompt"]
    assert "OA系统巡检报告" in system_prompt
    assert "请务必调用全部5个工具后再生成报告。" in system_prompt
    assert "安全规则（必须遵守）" in system_prompt


def test_run_inspection_returns_final_message_and_writes_log(stubbed_agent, monkeypatch, tmp_path) -> None:
    agent, _ = stubbed_agent
    monkeypatch.setattr(inspection_agent, "get_app_root", lambda: str(tmp_path))
    report = agent.run_inspection()
    assert report == "巡检报告: 全部正常"
    payload = agent.agent.invoke.call_args.args[0]
    assert payload["messages"][0]["role"] == "user"
    assert "请执行完整的OA系统巡检" in payload["messages"][0]["content"]
    log_files = list((tmp_path / "data" / "inspection_logs").glob("inspection_*.log"))
    assert len(log_files) == 1
    saved_text = log_files[0].read_text(encoding="utf-8")
    assert "巡检报告: 全部正常" in saved_text
    assert "巡检时间: " in saved_text


def test_run_inspection_handles_empty_message_list(stubbed_agent, monkeypatch, tmp_path) -> None:
    agent, _ = stubbed_agent
    monkeypatch.setattr(inspection_agent, "get_app_root", lambda: str(tmp_path))
    agent.agent.invoke.return_value = {"messages": []}
    assert agent.run_inspection() == "巡检未返回有效结果"


def test_run_inspection_converts_llm_failure_into_error_text(stubbed_agent, monkeypatch, tmp_path) -> None:
    agent, _ = stubbed_agent
    monkeypatch.setattr(inspection_agent, "get_app_root", lambda: str(tmp_path))
    agent.agent.invoke.side_effect = RuntimeError("LLM 请求超时")
    assert agent.run_inspection() == "巡检执行异常: LLM 请求超时"


# ========== 状态解析与入库 ==========


@pytest.mark.parametrize("text,expected", [
    ("[严重] OA应用服务疑似宕机！", "error"),
    ("[异常] 端口 8080 (OA应用端口): 未监听！", "error"),
    ("[告警] 磁盘 /data 使用率 92%", "warning"),
    ("[正常] Nginx服务运行中", "normal"),
    ("巡检报告: 未检测到异常项", "normal"),
])
def test_parse_inspection_status_mapping(text: str, expected: str) -> None:
    assert _parse_inspection_status(text) == expected


def test_save_inspection_to_db_derives_status_per_record(monkeypatch) -> None:
    fake_db = Mock()
    monkeypatch.setattr(inspection_agent, "db", fake_db)
    records = [
        {"check_type": "ports", "check_type_cn": "端口检测", "target": "", "result": "[异常] 端口 80 未监听！"},
        {"check_type": "disk", "check_type_cn": "磁盘使用", "target": "", "result": "[告警] /data 使用率 92%"},
        {"check_type": "memory", "check_type_cn": "内存使用", "target": "", "result": "[正常] 内存使用率: 55%"},
    ]
    save_inspection_to_db(records)
    assert [r["status"] for r in records] == ["error", "warning", "normal"]
    fake_db.save_inspection_batch.assert_called_once_with(records)


def test_save_inspection_to_db_does_not_raise_on_backend_error(monkeypatch) -> None:
    fake_db = Mock()
    fake_db.save_inspection_batch.side_effect = RuntimeError("database is locked")
    monkeypatch.setattr(inspection_agent, "db", fake_db)
    save_inspection_to_db([{"check_type": "ports", "result": "[正常] 端口 80 监听中"}])
    assert fake_db.save_inspection_batch.called


# ========== 统一巡检入口：run_unified_inspection ==========

_SSH_RESULTS = [
    {"check_type": "ports", "check_type_cn": "端口检测", "target": "OA服务器1",
     "result": "[正常] 端口 80 监听中", "is_simulated": False},
    {"check_type": "disk", "check_type_cn": "磁盘使用", "target": "OA服务器1",
     "result": "[告警] /data 使用率 92%", "is_simulated": False},
]

_LOCAL_RESULTS = [
    {"check_type": "ports", "check_type_cn": "端口检测", "target": "oa-win-test",
     "result": "[正常] 端口 80 监听中", "is_simulated": False},
    {"check_type": "oa", "check_type_cn": "OA应用服务", "target": "oa-win-test",
     "result": "[信息] 未检测到 Java 进程", "is_simulated": False},
]


def test_unified_simulated_mode_reports_all_checks_and_persists(monkeypatch) -> None:
    _patch_config_get(monkeypatch, {"inspection.mode": "simulated"})
    _stub_simulated_tools(monkeypatch)
    saved = Mock()
    monkeypatch.setattr(inspection_agent, "save_inspection_to_db", saved)
    monkeypatch.setattr(inspection_agent, "_try_ai_report", lambda records: "")
    report = run_unified_inspection()
    assert "OA系统巡检报告（模拟数据）" in report
    assert "巡检模式: 模拟数据" in report
    assert "[告警] /data: 使用率 92% (超过阈值85%)" in report
    assert "提示: 模拟模式使用随机数据。配置 SSH 或切换 local 模式启用真实检测。" in report
    records = saved.call_args.args[0]
    assert [r["check_type"] for r in records] == ["ports", "nginx", "oa", "disk", "memory"]
    assert [r["check_type_cn"] for r in records] == ["端口检测", "Nginx服务", "OA应用服务", "磁盘使用", "内存使用"]
    assert all(r["is_simulated"] is True and r["target"] == "" for r in records)


def test_unified_simulated_mode_appends_ai_report(monkeypatch) -> None:
    _patch_config_get(monkeypatch, {"inspection.mode": "simulated"})
    _stub_simulated_tools(monkeypatch)
    monkeypatch.setattr(inspection_agent, "save_inspection_to_db", Mock())
    monkeypatch.setattr(inspection_agent, "_try_ai_report", lambda records: "AI 改进建议: 建议扩容 /data 数据盘")
    report = run_unified_inspection()
    assert report.rstrip().endswith("AI 改进建议: 建议扩容 /data 数据盘")


def test_unified_simulated_mode_isolates_tool_failure(monkeypatch) -> None:
    _patch_config_get(monkeypatch, {"inspection.mode": "simulated"})
    _stub_simulated_tools(monkeypatch)
    broken_nginx = _StubTool("check_nginx_status", RuntimeError("nginx 命令不可用"))
    monkeypatch.setattr(inspection_agent, "check_nginx_status", broken_nginx)
    saved = Mock()
    monkeypatch.setattr(inspection_agent, "save_inspection_to_db", saved)
    monkeypatch.setattr(inspection_agent, "_try_ai_report", lambda records: "")
    report = run_unified_inspection()
    assert "[错误] check_nginx_status: nginx 命令不可用" in report
    records = saved.call_args.args[0]
    assert len(records) == 5
    assert records[1]["result"] == "[错误] check_nginx_status: nginx 命令不可用"
    assert records[1]["check_type"] == "nginx"


def test_unified_simulated_mode_survives_db_failure(monkeypatch) -> None:
    _patch_config_get(monkeypatch, {"inspection.mode": "simulated"})
    _stub_simulated_tools(monkeypatch)
    monkeypatch.setattr(inspection_agent, "save_inspection_to_db", Mock(side_effect=RuntimeError("db down")))
    monkeypatch.setattr(inspection_agent, "_try_ai_report", lambda records: "")
    report = run_unified_inspection()
    assert "OA系统巡检报告（模拟数据）" in report
    assert report.rstrip().endswith("提示: 模拟模式使用随机数据。配置 SSH 或切换 local 模式启用真实检测。")


def test_unified_auto_mode_degrades_to_simulated(monkeypatch) -> None:
    _patch_config_get(monkeypatch, {"inspection.mode": "auto"})
    monkeypatch.setattr(inspection_real, "run_ssh_inspection",
                        lambda: {"success": True, "mode": "simulated", "results": [], "report": ""})
    _stub_simulated_tools(monkeypatch)
    monkeypatch.setattr(inspection_agent, "save_inspection_to_db", Mock())
    monkeypatch.setattr(inspection_agent, "_try_ai_report", lambda records: "")
    report = run_unified_inspection()
    assert "OA系统巡检报告（自动降级 - 模拟数据）" in report
    assert "巡检模式: SSH 不可用，已自动降级为模拟" in report
    assert "提示: SSH 不可用，已自动使用模拟数据。可改为 local 使用本机检测。" in report


def test_unified_unknown_mode_falls_back_to_degraded_report(monkeypatch) -> None:
    _patch_config_get(monkeypatch, {"inspection.mode": "bogus"})
    _stub_simulated_tools(monkeypatch)
    monkeypatch.setattr(inspection_agent, "save_inspection_to_db", Mock())
    monkeypatch.setattr(inspection_agent, "_try_ai_report", lambda records: "")
    report = run_unified_inspection()
    assert "OA系统巡检报告（降级模式）" in report
    assert "提示: 模拟模式使用随机数据。" in report


def test_unified_ssh_mode_builds_real_report_and_persists(monkeypatch) -> None:
    _patch_config_get(monkeypatch, {"inspection.mode": "ssh"})
    monkeypatch.setattr(inspection_real, "run_ssh_inspection",
                        lambda: {"success": True, "mode": "ssh",
                                 "results": [dict(r) for r in _SSH_RESULTS], "report": "ignored"})
    saved = Mock()
    monkeypatch.setattr(inspection_agent, "save_inspection_to_db", saved)
    monkeypatch.setattr(inspection_agent, "_try_ai_report", lambda records: "")
    report = run_unified_inspection()
    assert "OA系统巡检报告（SSH 真实检测）" in report
    assert "巡检模式: SSH 真实服务器" in report
    assert "[正常] 端口 80 监听中" in report
    assert "提示: SSH 真实巡检完成，数据来自远程服务器" in report
    assert saved.call_args.args[0] == _SSH_RESULTS


def test_unified_ssh_mode_keeps_report_when_persist_fails(monkeypatch) -> None:
    _patch_config_get(monkeypatch, {"inspection.mode": "ssh"})
    monkeypatch.setattr(inspection_real, "run_ssh_inspection", lambda: _stub_ssh_result("ssh", _SSH_RESULTS))
    monkeypatch.setattr(inspection_agent, "save_inspection_to_db", Mock(side_effect=RuntimeError("db down")))
    monkeypatch.setattr(inspection_agent, "_try_ai_report", lambda records: "")
    report = run_unified_inspection()
    assert "提示: SSH 真实巡检完成，数据来自远程服务器" in report


def test_unified_local_mode_reports_host_check_summary(monkeypatch) -> None:
    _patch_config_get(monkeypatch, {"inspection.mode": "local"})
    monkeypatch.setattr(inspection_real, "run_ssh_inspection",
                        lambda: {"success": True, "mode": "local",
                                 "results": [dict(r) for r in _LOCAL_RESULTS], "report": "ignored"})
    monkeypatch.setattr(inspection_agent, "save_inspection_to_db", Mock())
    monkeypatch.setattr(inspection_agent, "_try_ai_report", lambda records: "")
    report = run_unified_inspection()
    assert "OA系统巡检报告（本机真实检测）" in report
    assert "巡检模式: 本机 Windows 命令检测" in report
    assert "共 2 项检测完成" in report


@pytest.mark.parametrize("mode,header", [
    ("ssh", "OA系统巡检报告（SSH 真实检测）"),
    ("local", "OA系统巡检报告（本机真实检测）"),
])
def test_unified_real_report_appends_ai_analysis(monkeypatch, mode, header) -> None:
    _patch_config_get(monkeypatch, {"inspection.mode": mode})
    monkeypatch.setattr(inspection_real, "run_ssh_inspection", lambda: _stub_ssh_result(mode, _SSH_RESULTS))
    monkeypatch.setattr(inspection_agent, "save_inspection_to_db", Mock())
    monkeypatch.setattr(inspection_agent, "_try_ai_report", lambda records: "AI 分析: 建议在 22:00 后执行数据归档")
    report = run_unified_inspection()
    assert header in report
    assert report.rstrip().endswith("AI 分析: 建议在 22:00 后执行数据归档")


def test_unified_local_mode_keeps_report_when_persist_fails(monkeypatch) -> None:
    _patch_config_get(monkeypatch, {"inspection.mode": "local"})
    monkeypatch.setattr(inspection_real, "run_ssh_inspection", lambda: _stub_ssh_result("local", _LOCAL_RESULTS))
    monkeypatch.setattr(inspection_agent, "save_inspection_to_db", Mock(side_effect=RuntimeError("db down")))
    monkeypatch.setattr(inspection_agent, "_try_ai_report", lambda records: "")
    report = run_unified_inspection()
    assert "共 2 项检测完成" in report


def test_unified_non_ssh_mode_ignores_missing_paramiko(monkeypatch) -> None:
    _patch_config_get(monkeypatch, {"inspection.mode": "auto"})
    monkeypatch.setitem(sys.modules, "agents.inspection_real", None)
    _stub_simulated_tools(monkeypatch)
    monkeypatch.setattr(inspection_agent, "save_inspection_to_db", Mock())
    monkeypatch.setattr(inspection_agent, "_try_ai_report", lambda records: "")
    report = run_unified_inspection()
    assert "OA系统巡检报告（自动降级 - 模拟数据）" in report


def test_unified_local_mode_falls_back_when_inspector_crashes(monkeypatch) -> None:
    _patch_config_get(monkeypatch, {"inspection.mode": "local"})

    def _boom():
        raise RuntimeError("本机检测模块崩溃")

    monkeypatch.setattr(inspection_real, "run_ssh_inspection", _boom)
    _stub_simulated_tools(monkeypatch)
    monkeypatch.setattr(inspection_agent, "save_inspection_to_db", Mock())
    monkeypatch.setattr(inspection_agent, "_try_ai_report", lambda records: "")
    report = run_unified_inspection()
    assert "OA系统巡检报告（降级模式）" in report


def test_unified_ssh_mode_surfaces_unexpected_error(monkeypatch) -> None:
    _patch_config_get(monkeypatch, {"inspection.mode": "ssh"})

    def _boom():
        raise RuntimeError("巡检器内部崩溃")

    monkeypatch.setattr(inspection_real, "run_ssh_inspection", _boom)
    assert run_unified_inspection() == "SSH 巡检失败: 巡检器内部崩溃"


def test_unified_ssh_mode_hints_when_paramiko_missing(monkeypatch) -> None:
    _patch_config_get(monkeypatch, {"inspection.mode": "ssh"})
    monkeypatch.setitem(sys.modules, "agents.inspection_real", None)
    report = run_unified_inspection()
    assert report == "SSH 巡检失败: paramiko 未安装。请执行 pip install paramiko"


@pytest.mark.parametrize("ssh_result,expected", [
    ({"success": False, "mode": "ssh", "results": [], "error": "认证失败: 密码错误"}, "认证失败: 密码错误"),
    ({"success": False, "mode": "ssh", "results": []}, "SSH 连接失败"),
])
def test_unified_ssh_connection_failure_returns_troubleshooting(monkeypatch, ssh_result, expected) -> None:
    _patch_config_get(monkeypatch, {"inspection.mode": "ssh"})
    monkeypatch.setattr(inspection_real, "run_ssh_inspection", lambda: dict(ssh_result))
    report = run_unified_inspection()
    assert "OA系统巡检报告（SSH 连接失败）" in report
    assert expected in report
    assert "1. 检查 config.yaml → inspection.ssh_hosts 配置" in report
    assert "4. 如需使用模拟模式: 修改 inspection.mode 为 simulated" in report
    assert "SSH 真实服务器" not in report


# ========== AI 报告兜底与模块自检入口 ==========


def test_try_ai_report_returns_generated_text(monkeypatch) -> None:
    monkeypatch.setattr(ai_reporter, "generate_ai_report", lambda records: f"AI 预警报告({len(records)} 项)")
    assert _try_ai_report([{"check_type": "ports"}]) == "AI 预警报告(1 项)"


def test_try_ai_report_swallows_exception(monkeypatch) -> None:
    def _broken(records):
        raise RuntimeError("LLM 不可用")

    monkeypatch.setattr(ai_reporter, "generate_ai_report", _broken)
    assert _try_ai_report([{"check_type": "ports"}]) == ""


def test_module_main_self_check_runs_offline(capsys) -> None:
    runpy.run_path(str(Path(inspection_agent.__file__)), run_name="__main__")
    output = capsys.readouterr().out
    assert "=== 端口检测 ===" in output
    assert "=== 内存检测 ===" in output
    assert "端口检测完成" in output
