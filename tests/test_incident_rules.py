"""incident_rules 单测：日志/巡检/告警 → 信号（cause_id、证据原文、分数、封顶、目录一致性）。"""

from agents.incident_rules import (
    CAUSE_CATALOG,
    LOG_RULES,
    RuleSignal,
    collect_signals,
    match_alert_signals,
    match_inspection_signals,
    match_log_signals,
)
from utils.incident_models import EvidenceKind, Severity


def causes(signals: list[RuleSignal]) -> list[str]:
    return [s.cause_id for s in signals]


# ---------- 日志规则 ----------

LOG_SAMPLES = {
    "oracle_tablespace": "ORA-01653: unable to extend table OA.ACT_HIS by 128 in tablespace TS_OA",
    "oracle_undo": "ORA-01555: snapshot too old: rollback segment number 12 with name _SYSSMU12_",
    "sqlserver_log": "Msg 9002, Level 17, State 2: The transaction log for database 'OA_DB' is full",
    "mysql_conn": "java.sql.SQLException: Access denied for user 'oa'@'10.20.3.15'",
    "mysql_slow": "Lock wait timeout exceeded; try restarting transaction",
    "redis_mem": "OOM command not allowed when used memory > 'maxmemory'.",
    "oom": "java.lang.OutOfMemoryError: Java heap space",
    "disk_full": "2026-09-18 09:12:44 [FATAL] No space left on device - /data/logs/oa.log",
    "mem_high": "内存告警: 内存使用率 93%，可用内存不足 2GB",
    "cpu_high": "10:02:01 up 42 days, load average: 15.72, 12.30, 9.88",
    "jvm_heap": "JVM堆内存使用率: 92% (超过阈值85%)，Full GC 频繁",
    "http_502": "2026-09-18 09:31:02 [error] 502 Bad Gateway: /oa/approval/list",
    "http_503": "2026-09-18 09:32:12 [ERROR] 503 Service Temporarily Unavailable",
    "port_conflict": "[ERROR] Address already in use: bind 0.0.0.0:8080",
    "service_down": "nginx: [error] connect() failed (111: Connection refused) while connecting to upstream",
    "ssl_expired": "SSL certificate verify failed: certificate has expired",
    "dns_fail": "Temporary failure in name resolution: oa.example.com",
    "net_unreach": "ping: Destination Host Unreachable",
    "security_bruteforce": "Failed password for root from 10.20.9.99 port 52336 ssh2",
    "file_handles": "java.io.IOException: Too many open files",
    "perm_denied": "Permission denied: /etc/nginx/conf.d/oa.conf",
    "config_error": "nginx: [emerg] configuration file test failed",
}


def test_rule_causes_are_covered_by_samples() -> None:
    assert {rule.cause_id for rule in LOG_RULES} == set(LOG_SAMPLES)


def test_log_samples_match_expected_causes() -> None:
    for cause_id, line in LOG_SAMPLES.items():
        signals = match_log_signals(line)
        assert cause_id in causes(signals), f"{cause_id} 未命中: {line}"


def test_upstream_timeout_maps_to_502() -> None:
    line = "[error] upstream timed out (110: Connection timed out) while reading response header"
    signals = match_log_signals(line)
    assert "http_502" in causes(signals)


def test_evidence_is_matched_line_with_metadata() -> None:
    text = "line1\n2026-09-18 [ERROR] No space left on device - /data\nline3"
    signal = next(s for s in match_log_signals(text) if s.cause_id == "disk_full")
    assert "No space left on device" in signal.evidence_text
    assert signal.evidence_kind == EvidenceKind.log_pattern
    assert signal.source == "日志"
    assert signal.severity == Severity.critical
    assert signal.base_score > 0.5


def test_per_rule_evidence_cap() -> None:
    text = "\n".join(f"2026-09-18 ERROR OOMKilled pod-{i}" for i in range(6))
    oom_signals = [s for s in match_log_signals(text) if s.cause_id == "oom"]
    assert len(oom_signals) == 3


def test_empty_log_returns_no_signal() -> None:
    assert match_log_signals("") == []
    assert match_log_signals("   \n  ") == []


# ---------- 巡检信号 ----------

DISK_ALERT_RESULT = (
    "磁盘检测完成 —— 存在磁盘告警，请及时清理或扩容！\n"
    "  [告警] /: 使用率 92% (超过阈值85%)\n"
    "  [正常] /data: 使用率 61%"
)


def test_inspection_disk_alert_signal() -> None:
    signals = match_inspection_signals([{"check_type": "disk", "result": DISK_ALERT_RESULT}])
    disk_signals = [s for s in signals if s.cause_id == "disk_full"]
    assert len(disk_signals) == 1
    assert "92%" in disk_signals[0].evidence_text
    assert disk_signals[0].evidence_kind == EvidenceKind.inspection_metric
    assert disk_signals[0].source == "巡检:磁盘"
    assert disk_signals[0].severity == Severity.error


def test_inspection_disk_normal_no_signal() -> None:
    signals = match_inspection_signals(
        [{"check_type": "disk", "result": "磁盘检测完成\n  [正常] /: 使用率 61%"}]
    )
    assert signals == []


def test_inspection_nginx_stopped() -> None:
    signals = match_inspection_signals(
        [{"check_type": "nginx", "result": "[异常] Nginx服务已停止！\n  建议: systemctl start nginx"}]
    )
    down = [s for s in signals if s.cause_id == "service_down"]
    assert down and down[0].severity == Severity.error


def test_inspection_nginx_config_error() -> None:
    signals = match_inspection_signals(
        [{"check_type": "nginx", "result": "[异常] Nginx配置文件语法错误！"}]
    )
    assert "config_error" in causes(signals)


def test_inspection_oa_slow_and_jvm() -> None:
    text = (
        "[告警] OA应用服务响应缓慢！\n"
        "  HTTP响应时间: 5432ms (超过阈值2000ms)\n"
        "  JVM堆内存使用率: 92%"
    )
    signals = match_inspection_signals([{"check_type": "oa_service", "result": text}])
    got = causes(signals)
    assert "oa_slow" in got
    assert "jvm_heap" in got


def test_inspection_oa_down_maps_to_service_and_503() -> None:
    text = "[严重] OA应用服务疑似宕机！\n  HTTP 503 Service Unavailable"
    signals = match_inspection_signals([{"check_type": "oa_service", "result": text}])
    got = causes(signals)
    assert "service_down" in got
    assert "http_503" in got


def test_inspection_ports_not_listening() -> None:
    text = (
        "端口检测完成，状态: 异常\n"
        "  [正常] 端口 80 (Nginx): 监听中，延迟 1.2ms\n"
        "  [异常] 端口 8080 (OA应用): 未监听！"
    )
    signals = match_inspection_signals([{"check_type": "ports", "result": text}])
    down = [s for s in signals if s.cause_id == "service_down"]
    assert down and "8080" in down[0].evidence_text


def test_inspection_memory_warning() -> None:
    signals = match_inspection_signals(
        [{"check_type": "memory", "result": "[告警] 内存使用率过高！\n  使用率: 93%"}]
    )
    assert "mem_high" in causes(signals)


def test_inspection_ignores_empty_and_unknown() -> None:
    assert match_inspection_signals([{"check_type": "disk", "result": ""}]) == []
    assert match_inspection_signals([{"check_type": "unknown", "result": "全部正常"}]) == []


# ---------- 告警信号 ----------


def test_alert_disk_critical() -> None:
    alerts = [{"severity": "critical", "title": "磁盘告警", "detail": "C盘使用率 96.4%，超过阈值"}]
    signals = match_alert_signals(alerts)
    disk_signals = [s for s in signals if s.cause_id == "disk_full"]
    assert disk_signals
    assert disk_signals[0].evidence_kind == EvidenceKind.alert
    assert disk_signals[0].severity == Severity.critical
    assert "96.4%" in disk_signals[0].evidence_text


def test_alert_unknown_text_returns_nothing() -> None:
    assert match_alert_signals([{"severity": "info", "title": "例行巡检", "detail": "一切正常"}]) == []


# ---------- 汇总与目录 ----------


def test_collect_signals_combines_three_sources() -> None:
    out = collect_signals(
        log_text="2026-09-18 [FATAL] No space left on device",
        inspection_results=[{"check_type": "memory", "result": " 使用率: 93%"}],
        alerts=[{"severity": "warning", "title": "OA响应缓慢", "detail": "HTTP响应超时"}],
    )
    got = causes(out)
    assert "disk_full" in got
    assert "mem_high" in got


def test_cause_catalog_consistency() -> None:
    assert all(rule.cause_id in CAUSE_CATALOG for rule in LOG_RULES)
    assert len({rule.rule_id for rule in LOG_RULES}) == len(LOG_RULES)
    info = CAUSE_CATALOG["disk_full"]
    assert info.title and info.category == "resource"
    assert len(CAUSE_CATALOG) >= 20
