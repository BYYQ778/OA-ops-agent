import os
import runpy
from pathlib import Path
from unittest.mock import Mock

import pytest

import agents.log_analysis_agent as log_analysis_agent
from agents.log_analysis_agent import (
    FAULT_RULES,
    LOG_ANALYSIS_TOOLS,
    LogAnalysisAgent,
    analyze_log_content,
    scan_log_file,
)


def analyze(text: str) -> str:
    return analyze_log_content.invoke({"log_text": text})


def test_empty_log_returns_helpful_prompt() -> None:
    assert "日志内容为空" in analyze("")


def test_502_log_is_classified() -> None:
    report = analyze("2026-09-16 ERROR 502 Bad Gateway upstream unavailable")
    assert "502 Bad Gateway" in report


def test_oom_log_is_classified() -> None:
    report = analyze("java.lang.OutOfMemoryError: Java heap space")
    assert "OOM内存溢出" in report


# ========== FAULT_RULES：逐条规则命中（中文运维日志真实风格） ==========

SAMPLE_LOGS = {
    "502 Bad Gateway": "2026-09-16 10:23:45 [ERROR] 502 Bad Gateway - /oa/approval/list",
    "502 Bad Gateway（上游超时）": (
        "2026-09-16 10:24:01 [ERROR] upstream timed out (110: Connection timed out) while connecting to upstream"
    ),
    "503 Service Unavailable": "2026-09-16 10:25:12 [ERROR] 503 Service Temporarily Unavailable",
    "端口占用/冲突": "2026-09-16 10:26:30 [ERROR] Address already in use: bind 0.0.0.0:8080",
    "流程卡死/超时": "2026-09-16 10:29:00 [ERROR] 审批流程卡死超过30分钟，未流转到下一节点",
    "OOM内存溢出": "2026-09-16 10:27:30 [ERROR] java.lang.OutOfMemoryError: Java heap space",
    "磁盘空间不足": "2026-09-16 10:28:00 [FATAL] No space left on device - /data/logs/",
    "权限拒绝": "2026-09-16 10:30:00 [ERROR] Permission denied: /etc/nginx/conf.d/oa.conf",
    "数据库连接失败": "2026-09-16 10:31:00 [ERROR] Connection refused: database 192.168.1.100:3306",
    "配置解析错误": "2026-09-16 10:32:00 [ERROR] YAML parse error in oa config: mapping values are not allowed",
    "文件句柄耗尽": "2026-09-16 10:33:00 [ERROR] Too many open files: /var/log/oa/app.log",
}

RULE_SEVERITY = {name: severity for _, name, severity, _ in FAULT_RULES}
RULE_SUGGESTIONS = {name: suggestions for _, name, _, suggestions in FAULT_RULES}


def test_every_fault_rule_has_a_realistic_sample() -> None:
    assert set(SAMPLE_LOGS) == set(RULE_SEVERITY)


@pytest.mark.parametrize("fault_name", sorted(SAMPLE_LOGS))
def test_fault_rule_matches_sample_line(fault_name: str) -> None:
    report = analyze(SAMPLE_LOGS[fault_name])
    severity = RULE_SEVERITY[fault_name]
    assert "扫描行数: 1" in report
    assert "发现故障: 1 处" in report
    assert f"--- 故障 1: [{severity}] {fault_name} (出现 1 次)" in report
    assert f"1. {RULE_SUGGESTIONS[fault_name][0]}" in report
    assert "首次出现在第 1 行" in report


def test_repeated_faults_are_deduplicated_with_count() -> None:
    log_text = "\n".join([
        "2026-09-16 11:00:00 [ERROR] 502 Bad Gateway - /oa/portal",
        "2026-09-16 11:00:05 [INFO] 用户 admin 登录成功",
        "2026-09-16 11:00:09 [ERROR] 502 Bad Gateway - /oa/workflow",
    ])
    report = analyze(log_text)
    assert "扫描行数: 3" in report
    assert "发现故障: 2 处" in report
    assert "--- 故障 1: [高] 502 Bad Gateway (出现 2 次)" in report
    assert "--- 故障 2:" not in report
    assert "首次出现在第 1 行" in report


def test_report_sorts_faults_by_severity() -> None:
    log_text = "\n".join([
        "2026-09-16 12:00:00 [ERROR] Permission denied: /data/oa/upload",
        "2026-09-16 12:00:03 [ERROR] java.lang.OutOfMemoryError: Java heap space",
        "2026-09-16 12:00:06 [WARN] 磁盘空间不足，剩余 0 字节",
    ])
    report = analyze(log_text)
    assert "严重: 1 | 高: 1 | 中: 1" in report
    assert report.index("[严重] OOM内存溢出") < report.index("[高] 磁盘空间不足") < report.index("[中] 权限拒绝")
    assert "存在 1 处严重故障，建议立即处理！" in report
    assert "存在 1 处高风险故障，请尽快排查。" in report


def test_summary_omits_absent_severities() -> None:
    report = analyze("2026-09-16 13:00:00 [ERROR] Permission denied: /etc/nginx/nginx.conf")
    summary = report.split("[总结]")[1]
    assert "严重故障" not in summary
    assert "高风险故障" not in summary
    assert "请按上述排查建议逐项处理" in summary


def test_log_without_known_faults_reports_guidance() -> None:
    log_text = "\n".join([
        "",
        "2026-09-16 14:00:00 [INFO] OA系统启动完成，耗时 42 秒",
        "2026-09-16 14:00:05 [INFO] 定时任务调度器就绪",
        "   ",
        "",
    ])
    report = analyze(log_text)
    assert "未发现已知的故障模式" in report
    assert "1. 确认日志文件为OA系统相关日志" in report
    assert "扫描行数" not in report


def test_long_line_is_truncated_in_report() -> None:
    long_tail = "X" * 300
    report = analyze(f"2026-09-16 15:00:00 [ERROR] 502 Bad Gateway {long_tail}")
    assert "[ERROR] 502 Bad Gateway" in report
    summary_line = next(line for line in report.splitlines() if "日志摘要:" in line)
    assert long_tail not in summary_line
    assert len(summary_line) <= len("    日志摘要: ") + 120


# ========== scan_log_file：文件读取与降级 ==========


def test_scan_log_file_missing_file(tmp_path) -> None:
    report = scan_log_file.invoke({"file_path": str(tmp_path / "not-exist.log")})
    assert "[错误] 文件不存在" in report


def test_scan_log_file_reads_and_analyzes(tmp_path) -> None:
    log_file = tmp_path / "oa_error.log"
    log_file.write_text("2026-09-16 16:00:00 [ERROR] 502 Bad Gateway - /oa/approval\n", encoding="utf-8")
    report = scan_log_file.invoke({"file_path": str(log_file)})
    assert "[高] 502 Bad Gateway" in report
    assert "发现故障: 1 处" in report


def test_scan_log_file_uses_chunked_reader_for_large_files(tmp_path, monkeypatch) -> None:
    log_file = tmp_path / "big.log"
    log_file.write_text("2026-09-16 16:10:00 [FATAL] No space left on device - /data\n", encoding="utf-8")
    monkeypatch.setattr(os.path, "getsize", lambda path: 60 * 1024 * 1024)
    report = scan_log_file.invoke({"file_path": str(log_file)})
    assert "磁盘空间不足" in report


def test_scan_log_file_reports_read_failure(tmp_path) -> None:
    target = tmp_path / "logs_dir"
    target.mkdir()
    report = scan_log_file.invoke({"file_path": str(target)})
    assert "[错误] 读取文件失败" in report


def test_scan_log_file_stops_after_100_chunks(tmp_path, monkeypatch) -> None:
    log_file = tmp_path / "huge.log"
    log_file.write_text("2026-09-16 18:00:00 [INFO] 任务调度器心跳正常\n", encoding="utf-8")

    class _EndlessFile:
        """模拟永远读不完的超大日志（每次 read 都返回内容）。"""

        def __init__(self) -> None:
            self.chunks_read = 0

        def read(self, size: int = -1) -> str:
            self.chunks_read += 1
            return "2026-09-16 18:00:00 [INFO] 任务调度器心跳正常\n"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    endless = _EndlessFile()
    monkeypatch.setattr(os.path, "getsize", lambda path: 200 * 1024 * 1024)
    monkeypatch.setattr("builtins.open", lambda *args, **kwargs: endless)
    report = scan_log_file.invoke({"file_path": str(log_file)})
    assert endless.chunks_read == 100
    assert "未发现已知的故障模式" in report


# ========== LogAnalysisAgent：LLM 边界打桩 ==========


@pytest.fixture
def log_agent(monkeypatch):
    """构造 LogAnalysisAgent，但把 ChatOpenAI / create_agent 打桩为本地 Mock。"""
    recorded: dict = {}

    class _FakeChatOpenAI:
        def __init__(self, **kwargs):
            recorded["llm_kwargs"] = kwargs

    def _fake_create_agent(**kwargs):
        recorded["agent_kwargs"] = kwargs
        graph = Mock()
        graph.invoke.return_value = {"messages": [Mock(content="LLM 分析结论: 存在 502 故障")]}
        return graph

    monkeypatch.setattr(log_analysis_agent, "ChatOpenAI", _FakeChatOpenAI)
    monkeypatch.setattr(log_analysis_agent, "create_agent", _fake_create_agent)
    agent = LogAnalysisAgent(llm_api_key="test-key", llm_base_url="http://127.0.0.1:9999/v1", llm_model="test-model")
    return agent, recorded


def test_agent_init_wires_tools_and_guardrails(log_agent) -> None:
    _, recorded = log_agent
    assert recorded["llm_kwargs"] == {
        "api_key": "test-key",
        "base_url": "http://127.0.0.1:9999/v1",
        "model": "test-model",
        "temperature": 0.2,
        "max_tokens": 2048,
    }
    assert recorded["agent_kwargs"]["tools"] == LOG_ANALYSIS_TOOLS
    system_prompt = recorded["agent_kwargs"]["system_prompt"]
    assert "资深OA系统运维工程师" in system_prompt
    assert "analyze_log_content" in system_prompt
    assert "安全规则（必须遵守）" in system_prompt


def test_agent_analyze_returns_llm_message(log_agent) -> None:
    agent, _ = log_agent
    report = agent.analyze("2026-09-16 [ERROR] 502 Bad Gateway - /oa/portal")
    assert report == "LLM 分析结论: 存在 502 故障"
    payload = agent.agent.invoke.call_args.args[0]
    content = payload["messages"][0]["content"]
    assert content.startswith("请分析以下OA系统日志内容:")
    assert '<untrusted_data type="日志内容">' in content
    assert "</untrusted_data>" in content


def test_agent_analyze_empty_text_short_circuits(log_agent) -> None:
    agent, _ = log_agent
    assert agent.analyze("   ") == "[提示] 请提供需要分析的日志内容。"
    agent.agent.invoke.assert_not_called()


def test_agent_analyze_handles_empty_message_list(log_agent) -> None:
    agent, _ = log_agent
    agent.agent.invoke.return_value = {"messages": []}
    assert agent.analyze("ERROR 502 Bad Gateway") == "分析未返回有效结果"


def test_agent_analyze_falls_back_to_regex_when_llm_fails(log_agent) -> None:
    agent, _ = log_agent
    agent.agent.invoke.side_effect = RuntimeError("LLM 服务不可用")
    report = agent.analyze("2026-09-16 [ERROR] java.lang.OutOfMemoryError: Java heap space")
    assert "[严重] OOM内存溢出" in report
    assert "排查建议" in report


def test_agent_analyze_truncates_oversized_log(log_agent) -> None:
    agent, _ = log_agent
    agent.analyze("Z" * 9000)
    content = agent.agent.invoke.call_args.args[0]["messages"][0]["content"]
    assert content.count("Z") == 8000


def test_agent_analyze_file_uses_scanner(log_agent, tmp_path) -> None:
    agent, _ = log_agent
    log_file = tmp_path / "oa.log"
    log_file.write_text("2026-09-16 17:00:00 [ERROR] 502 Bad Gateway - /oa/login\n", encoding="utf-8")
    assert "[高] 502 Bad Gateway" in agent.analyze_file(str(log_file))
    assert "[错误] 文件不存在" in agent.analyze_file(str(tmp_path / "missing.log"))


def test_module_main_prints_rule_self_test(capsys) -> None:
    runpy.run_path(str(Path(log_analysis_agent.__file__)), run_name="__main__")
    output = capsys.readouterr().out
    assert "=== 正则规则直接测试 ===" in output
    assert "502 Bad Gateway" in output
    assert "OOM内存溢出" in output
