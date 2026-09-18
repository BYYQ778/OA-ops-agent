"""根因诊断信号规则：把 日志 / 巡检 / 告警 输入转换为带证据的候选根因信号。

设计原则:
- 纯正则 + 纯函数，零重依赖（不 import LLM/向量库/网络），CI core 与离线单测可完整运行。
- 每条规则给出：rule_id、cause_id、标题、类别、基础分、证据类型与原文、来源；
  incident_agent 负责把信号汇总为 CandidateCause / Evidence 并排序成报告。
- 规则与分数是初值：第 4 周标准案例评测（Top-1）会检验效果，可按评测结果调整本文件。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence

from utils.incident_models import EvidenceKind, Severity

#: 同一条规则最多产出几条证据（防刷屏）
MAX_EVIDENCE_PER_RULE = 3
#: 证据原文截断长度（与日志分析报告一致）
SNIPPET_MAX = 200

DISK_WARN_PCT = 85.0
DISK_CRIT_PCT = 90.0
MEM_WARN_PCT = 85.0
MEM_CRIT_PCT = 90.0
JVM_CRIT_PCT = 90.0


@dataclass(frozen=True)
class CauseInfo:
    """候选根因目录条目：id、中文标题与类别。"""

    cause_id: str
    title: str
    category: str


#: 候选根因目录（规则与评测案例共用；category 用于案例覆盖统计与报告分组）
CAUSE_CATALOG: Dict[str, CauseInfo] = {
    "disk_full": CauseInfo("disk_full", "服务器磁盘空间不足", "resource"),
    "mem_high": CauseInfo("mem_high", "服务器内存使用率过高", "resource"),
    "cpu_high": CauseInfo("cpu_high", "服务器 CPU 负载过高", "resource"),
    "oom": CauseInfo("oom", "应用内存溢出（OOM）", "oa_service"),
    "jvm_heap": CauseInfo("jvm_heap", "JVM 堆内存过高/GC 频繁", "oa_service"),
    "oa_slow": CauseInfo("oa_slow", "OA 应用响应缓慢", "oa_service"),
    "http_502": CauseInfo("http_502", "502 网关错误（上游不可达/超时）", "http_5xx"),
    "http_503": CauseInfo("http_503", "503 服务不可用（宕机/过载）", "http_5xx"),
    "service_down": CauseInfo("service_down", "服务未运行/进程缺失", "oa_service"),
    "port_conflict": CauseInfo("port_conflict", "端口占用/冲突", "os"),
    "mysql_conn": CauseInfo("mysql_conn", "MySQL 连接异常（认证/网络/连接数）", "mysql"),
    "mysql_slow": CauseInfo("mysql_slow", "MySQL 慢查询/锁等待", "mysql"),
    "redis_mem": CauseInfo("redis_mem", "Redis 内存不足", "redis"),
    "oracle_tablespace": CauseInfo("oracle_tablespace", "Oracle 表空间不足", "oracle"),
    "oracle_undo": CauseInfo("oracle_undo", "Oracle 快照过旧（UNDO 不足）", "oracle"),
    "sqlserver_log": CauseInfo("sqlserver_log", "SQL Server 事务日志已满/备份问题", "sqlserver"),
    "ssl_expired": CauseInfo("ssl_expired", "SSL 证书过期/校验失败", "network"),
    "dns_fail": CauseInfo("dns_fail", "DNS 解析失败", "network"),
    "net_unreach": CauseInfo("net_unreach", "网络不通/超时", "network"),
    "security_bruteforce": CauseInfo("security_bruteforce", "疑似暴力破解/异常登录", "security"),
    "file_handles": CauseInfo("file_handles", "文件句柄耗尽", "os"),
    "perm_denied": CauseInfo("perm_denied", "权限不足", "os"),
    "config_error": CauseInfo("config_error", "配置解析错误", "os"),
}


def get_cause_info(cause_id: str) -> Optional[CauseInfo]:
    """按 id 取候选根因目录条目。"""
    return CAUSE_CATALOG.get(cause_id)


@dataclass(frozen=True)
class RuleSignal:
    """单条规则信号：指向一个候选根因，并携带其证据原文。"""

    rule_id: str
    cause_id: str
    title: str
    category: str
    base_score: float
    evidence_kind: EvidenceKind
    evidence_text: str
    source: str
    severity: Severity = Severity.warning


@dataclass(frozen=True)
class _LogRule:
    rule_id: str
    cause_id: str
    pattern: str
    score: float
    severity: Severity


#: 日志/告警文本规则（顺序即输出顺序；一条行可命中多条规则）
LOG_RULES: tuple[_LogRule, ...] = (
    _LogRule(
        "LOG-ORA-TS", "oracle_tablespace",
        r"ORA-0165[234]|表空间[^\n]{0,10}(?:不足|已满|无法扩展)",
        0.90, Severity.error,
    ),
    _LogRule(
        "LOG-ORA-UNDO", "oracle_undo",
        r"ORA-01555|snapshot\s+too\s+old|快照过旧",
        0.80, Severity.warning,
    ),
    _LogRule(
        "LOG-MSSQL", "sqlserver_log",
        r"Msg\s*9002|错误\s*9002|transaction\s+log\s+for\s+database"
        r"|事务日志[^\n]{0,10}(?:已满|不足)|log\s+file\s+is\s+full",
        0.85, Severity.error,
    ),
    _LogRule(
        "LOG-MYSQL-CONN", "mysql_conn",
        r"Access\s+denied\s+for\s+user|Can't\s+connect\s+to\s+(?:local\s+)?MySQL"
        r"|Communications\s+link\s+failure|CommunicationsException|Too\s+many\s+connections",
        0.80, Severity.error,
    ),
    _LogRule(
        "LOG-MYSQL-SLOW", "mysql_slow",
        r"slow\s+query|慢查询|Lock\s+wait\s+timeout|Deadlock\s+found",
        0.85, Severity.warning,
    ),
    _LogRule(
        "LOG-REDIS", "redis_mem",
        r"OOM\s+command\s+not\s+allowed|maxmemory[^\n]{0,20}(?:exceeded|reached)"
        r"|Redis[^\n]{0,15}内存[^\n]{0,10}(?:不足|已满)",
        0.85, Severity.error,
    ),
    _LogRule(
        "LOG-OOM", "oom",
        r"OutOfMemoryError|Out\s+of\s+memory|OOMKilled|内存溢出|out_of_memory",
        0.90, Severity.critical,
    ),
    _LogRule(
        "LOG-DISK", "disk_full",
        r"No\s+space\s+left\s+on\s+device|磁盘空间不足|磁盘已满|磁盘写满"
        r"|磁盘[^\n]{0,20}使用率\s*(?:8[5-9]|9\d|100)(?:\.\d+)?%|disk\s+full",
        0.85, Severity.critical,
    ),
    _LogRule(
        "LOG-MEM", "mem_high",
        r"内存使用率过高|内存[^\n]{0,20}使用率\s*(?:8[5-9]|9\d|100)(?:\.\d+)?%"
        r"|memory\s+usage[^\n]{0,10}(?:8[5-9]|9\d|100)(?:\.\d+)?%",
        0.75, Severity.warning,
    ),
    _LogRule(
        "LOG-CPU", "cpu_high",
        r"load\s+average[:\s]+(?:[1-9]\d(?:\.\d+)?|\d{3,}(?:\.\d+)?)"
        r"|CPU\s*使用率\s*(?:8[5-9]|9\d|100)(?:\.\d+)?%"
        r"|cpu\s+usage[^\n]{0,10}(?:8[5-9]|9\d|100)(?:\.\d+)?%",
        0.75, Severity.warning,
    ),
    _LogRule(
        "LOG-JVM", "jvm_heap",
        r"JVM[^\n]{0,12}堆内存使用率[:\s]*(?:8[5-9]|9\d|100)(?:\.\d+)?%"
        r"|堆内存使用率[:\s]*(?:8[5-9]|9\d|100)(?:\.\d+)?%",
        0.75, Severity.warning,
    ),
    _LogRule(
        "LOG-502", "http_502",
        r"502\s+Bad\s+Gateway|Bad\s+Gateway",
        0.85, Severity.error,
    ),
    _LogRule(
        "LOG-UPSTREAM-TIMEOUT", "http_502",
        r"upstream\s+timed\s+out|upstream[^\n]{0,20}超时",
        0.80, Severity.error,
    ),
    _LogRule(
        "LOG-503", "http_503",
        r"503\s+Service\s+(?:Temporarily\s+)?Unavailable",
        0.85, Severity.error,
    ),
    _LogRule(
        "LOG-PORT", "port_conflict",
        r"Address\s+already\s+in\s+use|端口[^\n]{0,10}占用",
        0.70, Severity.warning,
    ),
    _LogRule(
        "LOG-SVC", "service_down",
        r"Connection\s+refused|连接被拒绝|服务未启动|服务已停止"
        r"|进程不存在|no\s+such\s+process",
        0.55, Severity.error,
    ),
    _LogRule(
        "LOG-SSL", "ssl_expired",
        r"certificate\s+has\s+expired|certificate\s+verify\s+failed"
        r"|SSL[^\n]{0,20}证书[^\n]{0,10}(?:过期|失效)|证书[^\n]{0,10}(?:已过期|过期)",
        0.90, Severity.error,
    ),
    _LogRule(
        "LOG-DNS", "dns_fail",
        r"Temporary\s+failure\s+in\s+name\s+resolution|Name\s+or\s+service\s+not\s+known"
        r"|no\s+such\s+host|DNS[^\n]{0,12}解析[^\n]{0,6}(?:失败|异常)"
        r"|getaddrinfo[^\n]{0,15}(?:failed|error)",
        0.85, Severity.error,
    ),
    _LogRule(
        "LOG-NET", "net_unreach",
        r"Destination\s+Host\s+Unreachable|No\s+route\s+to\s+host"
        r"|Connection\s+timed\s+out|网络不通|请求超时",
        0.70, Severity.warning,
    ),
    _LogRule(
        "LOG-SEC", "security_bruteforce",
        r"Failed\s+password\s+for|authentication\s+failure"
        r"|Invalid\s+user[^\n]{0,15}from|疑似暴力破解|暴力破解",
        0.85, Severity.error,
    ),
    _LogRule(
        "LOG-FD", "file_handles",
        r"Too\s+many\s+open\s+files|文件句柄[^\n]{0,6}(?:不足|耗尽)|EMFILE",
        0.80, Severity.warning,
    ),
    _LogRule(
        "LOG-PERM", "perm_denied",
        r"Permission\s+denied|权限不够|权限拒绝",
        0.50, Severity.warning,
    ),
    _LogRule(
        "LOG-CONF", "config_error",
        r"nginx[^\n]{0,25}(?:configuration|config)[^\n]{0,15}(?:error|test\s+failed)"
        r"|YAML[^\n]{0,10}parse\s+error|XML[^\n]{0,10}parse\s+error"
        r"|配置[^\n]{0,6}(?:解析)?错误",
        0.60, Severity.warning,
    ),
)

_COMPILED_LOG: tuple[tuple[_LogRule, "re.Pattern[str]"], ...] = tuple(
    (rule, re.compile(rule.pattern, re.IGNORECASE)) for rule in LOG_RULES
)

_PCT_RE = re.compile(r"使用率\s*[:：]?\s*(\d+(?:\.\d+)?)\s*%")
_JVM_PCT_RE = re.compile(r"JVM\s*堆内存使用率\s*[:：]?\s*(\d+(?:\.\d+)?)\s*%")
_PORT_NOT_LISTEN_RE = re.compile(r"端口\s*(\d+)[^\n]*未监听")

_ALERT_SEVERITY: Dict[str, Severity] = {
    "info": Severity.info,
    "warning": Severity.warning,
    "critical": Severity.critical,
}


def _clip(text: str) -> str:
    """压平空白并截断证据原文。"""
    return " ".join((text or "").split())[:SNIPPET_MAX]


def _line_containing(text: str, index: int) -> str:
    """取包含给定字符位置的整行（用于在巡检文本中定位证据行）。"""
    start = text.rfind("\n", 0, index) + 1
    end = text.find("\n", index)
    if end == -1:
        end = len(text)
    return text[start:end].strip()


def _first_line_with(text: str, keyword: str) -> str:
    for line in text.splitlines():
        if keyword in line:
            return line.strip()
    return _clip(text)


def _mk(
    rule_id: str,
    cause_id: str,
    evidence_text: str,
    source: str,
    kind: EvidenceKind,
    score: float,
    severity: Severity,
) -> RuleSignal:
    info = CAUSE_CATALOG[cause_id]
    return RuleSignal(
        rule_id=rule_id,
        cause_id=cause_id,
        title=info.title,
        category=info.category,
        base_score=score,
        evidence_kind=kind,
        evidence_text=_clip(evidence_text),
        source=source,
        severity=severity,
    )


# ---------- 日志 ----------


def match_log_signals(log_text: str) -> List[RuleSignal]:
    """逐行匹配日志文本 → 信号（同规则最多 MAX_EVIDENCE_PER_RULE 条证据）。"""
    if not log_text or not log_text.strip():
        return []
    lines = [line.strip() for line in log_text.splitlines()]
    out: List[RuleSignal] = []
    seen: set[tuple[str, str]] = set()
    for rule, pattern in _COMPILED_LOG:
        hits = 0
        for line in lines:
            if hits >= MAX_EVIDENCE_PER_RULE:
                break
            if not line or not pattern.search(line):
                continue
            key = (rule.rule_id, line[:SNIPPET_MAX])
            if key in seen:
                continue
            seen.add(key)
            out.append(
                _mk(rule.rule_id, rule.cause_id, line, "日志", EvidenceKind.log_pattern, rule.score, rule.severity)
            )
            hits += 1
    return out


# ---------- 巡检 ----------


def _match_disk(text: str) -> List[RuleSignal]:
    out: List[RuleSignal] = []
    for match in _PCT_RE.finditer(text):
        pct = float(match.group(1))
        if pct >= DISK_CRIT_PCT:
            score, severity = 0.85, Severity.error
        elif pct >= DISK_WARN_PCT:
            score, severity = 0.60, Severity.warning
        else:
            continue
        out.append(
            _mk("INSPECT-DISK", "disk_full", _line_containing(text, match.start()),
                "巡检:磁盘", EvidenceKind.inspection_metric, score, severity)
        )
        if len(out) >= MAX_EVIDENCE_PER_RULE:
            break
    return out


def _match_memory(text: str) -> List[RuleSignal]:
    out: List[RuleSignal] = []
    for match in _PCT_RE.finditer(text):
        pct = float(match.group(1))
        if pct >= MEM_CRIT_PCT:
            score, severity = 0.80, Severity.error
        elif pct >= MEM_WARN_PCT:
            score, severity = 0.60, Severity.warning
        else:
            continue
        out.append(
            _mk("INSPECT-MEM", "mem_high", _line_containing(text, match.start()),
                "巡检:内存", EvidenceKind.inspection_metric, score, severity)
        )
        if len(out) >= MAX_EVIDENCE_PER_RULE:
            break
    return out


def _match_nginx(text: str) -> List[RuleSignal]:
    out: List[RuleSignal] = []
    if "已停止" in text or "未运行" in text or "not running" in text:
        keyword = "已停止" if "已停止" in text else ("未运行" if "未运行" in text else "not running")
        out.append(
            _mk("INSPECT-NGINX-DOWN", "service_down", _first_line_with(text, keyword),
                "巡检:Nginx", EvidenceKind.inspection_metric, 0.85, Severity.error)
        )
    if "语法错误" in text or "test failed" in text:
        keyword = "语法错误" if "语法错误" in text else "test failed"
        out.append(
            _mk("INSPECT-NGINX-CONF", "config_error", _first_line_with(text, keyword),
                "巡检:Nginx", EvidenceKind.inspection_metric, 0.70, Severity.warning)
        )
    return out


def _match_oa_service(text: str) -> List[RuleSignal]:
    out: List[RuleSignal] = []
    if "宕机" in text:
        out.append(
            _mk("INSPECT-OA-DOWN", "service_down", _first_line_with(text, "宕机"),
                "巡检:OA服务", EvidenceKind.inspection_metric, 0.70, Severity.error)
        )
        if "503" in text:
            out.append(
                _mk("INSPECT-OA-503", "http_503", _first_line_with(text, "503"),
                    "巡检:OA服务", EvidenceKind.inspection_metric, 0.70, Severity.error)
            )
    if "响应缓慢" in text:
        out.append(
            _mk("INSPECT-OA-SLOW", "oa_slow", _first_line_with(text, "响应缓慢"),
                "巡检:OA服务", EvidenceKind.inspection_metric, 0.70, Severity.warning)
        )
        jvm = _JVM_PCT_RE.search(text)
        if jvm and float(jvm.group(1)) >= JVM_CRIT_PCT:
            out.append(
                _mk("INSPECT-OA-JVM", "jvm_heap", _line_containing(text, jvm.start()),
                    "巡检:OA服务", EvidenceKind.inspection_metric, 0.75, Severity.warning)
            )
    return out


def _match_ports(text: str) -> List[RuleSignal]:
    out: List[RuleSignal] = []
    for match in _PORT_NOT_LISTEN_RE.finditer(text):
        out.append(
            _mk("INSPECT-PORT", "service_down", _line_containing(text, match.start()),
                "巡检:端口", EvidenceKind.inspection_metric, 0.80, Severity.error)
        )
        if len(out) >= MAX_EVIDENCE_PER_RULE:
            break
    return out


def match_inspection_signals(results: Sequence[Mapping[str, Any]]) -> List[RuleSignal]:
    """从巡检结果（check_type + result 文本）提取信号。"""
    signals: List[RuleSignal] = []
    for item in results:
        text = str(item.get("result") or "")
        check_type = str(item.get("check_type") or "").strip().lower()
        if not text:
            continue
        if "disk" in check_type:
            signals.extend(_match_disk(text))
        elif "memory" in check_type or "mem" in check_type:
            signals.extend(_match_memory(text))
        elif "nginx" in check_type:
            signals.extend(_match_nginx(text))
        elif "oa_service" in check_type or "service" in check_type:
            signals.extend(_match_oa_service(text))
        elif "port" in check_type:
            signals.extend(_match_ports(text))
    return signals


# ---------- 告警 ----------


def match_alert_signals(alerts: Sequence[Mapping[str, Any]]) -> List[RuleSignal]:
    """从告警记录（title + detail 文本）提取信号；severity 沿用告警级别。"""
    out: List[RuleSignal] = []
    for alert in alerts:
        title = str(alert.get("title") or "").strip()
        detail = str(alert.get("detail") or "").strip()
        combined = f"{title} {detail}".strip()
        if not combined:
            continue
        severity = _ALERT_SEVERITY.get(str(alert.get("severity") or "").strip().lower())
        source = f"告警:{title[:40]}" if title else "告警"
        for rule, pattern in _COMPILED_LOG:
            if not pattern.search(combined):
                continue
            info = CAUSE_CATALOG[rule.cause_id]
            out.append(
                RuleSignal(
                    rule_id=f"ALERT-{rule.rule_id}",
                    cause_id=rule.cause_id,
                    title=info.title,
                    category=info.category,
                    base_score=rule.score,
                    evidence_kind=EvidenceKind.alert,
                    evidence_text=_clip(combined),
                    source=source,
                    severity=severity or rule.severity,
                )
            )
    return out


# ---------- 汇总 ----------


def collect_signals(
    log_text: str = "",
    inspection_results: Optional[Sequence[Mapping[str, Any]]] = None,
    alerts: Optional[Sequence[Mapping[str, Any]]] = None,
) -> List[RuleSignal]:
    """汇总三类输入的信号（日志 → 巡检 → 告警 顺序）。"""
    signals: List[RuleSignal] = []
    if log_text:
        signals.extend(match_log_signals(log_text))
    if inspection_results:
        signals.extend(match_inspection_signals(inspection_results))
    if alerts:
        signals.extend(match_alert_signals(alerts))
    return signals
