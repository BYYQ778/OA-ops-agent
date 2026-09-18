"""根因诊断管线（只读）：标准化 → 关联 → 检索 → 候选 → 证据验证与排序 → 报告。

流程与纪律:
- 输入：日志文本 / 巡检结果 / 告警记录；候选根因只来自规则信号（incident_rules），
  禁止凭模型常识输出根因；
- 每条候选必须携带 ≥1 条证据；确定根因需 ≥2 条证据且置信度达标，否则输出
  「不确定」报告并说明原因（不强凑证据）；
- 知识库（KbRetriever）与历史故障为可选增强：不可用/为空时降级不报错；
- LLM 叙述为可选（llm_narrator 注入），只允许在既有候选与证据范围内组织语言；
- 只读：本模块不产生任何命令执行路径。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol, Sequence

from agents.incident_rules import CAUSE_CATALOG, RuleSignal, collect_signals
from utils.incident_models import (
    MIN_EVIDENCE_FOR_ROOT_CAUSE,
    CandidateCause,
    DiagnosisReport,
    EventSource,
    Evidence,
    EvidenceKind,
    IncidentEvent,
    ReportStatus,
    now_iso,
)

#: 根因判定的最低置信度（低于此值即使证据够也标不确定）
MIN_ROOT_SCORE = 0.6
#: 知识库检索条数
KB_TOP_K = 3
#: KB 片段与候选标题的 2-gram 重合数达到该值才认为相关
KB_TITLE_OVERLAP_MIN = 2
#: 历史故障条目处理条数上限
HISTORY_MAX_ENTRIES = 20

#: 各候选根因的标准处置建议（报告用；root/最高候选命中）
CAUSE_SUGGESTIONS: Dict[str, tuple[str, ...]] = {
    "disk_full": (
        "查看磁盘使用情况（df -h）定位已满分区",
        "清理过期日志与临时文件（如 /var/log、/tmp 及归档目录）",
        "如为数据盘满，评估扩容或将历史数据归档迁移",
    ),
    "mem_high": (
        "用 top -o %MEM 定位内存占用最高的进程",
        "排查内存泄漏与缓存异常增长（应用与数据库）",
        "评估扩容或调整服务内存上限",
    ),
    "cpu_high": (
        "用 top -o %CPU 定位高负载进程",
        "排查慢 SQL、死循环或定时任务叠加",
        "必要时限流或错峰调度，观察负载回落",
    ),
    "oom": (
        "先保存现场日志，导出堆转储（jmap -dump）分析",
        "检查 JVM -Xmx/-Xms 配置与内存泄漏点",
        "临时提高堆上限并重启服务（按变更流程）",
    ),
    "jvm_heap": (
        "检查 Full GC 频率与 GC 日志",
        "分析堆内大对象与缓存占用",
        "调整堆参数或修复内存泄漏后观察",
    ),
    "oa_slow": (
        "检查数据库连接池与慢 SQL",
        "查看应用线程堆栈定位阻塞点",
        "检查 GC 停顿与外部接口超时",
    ),
    "http_502": (
        "检查上游应用服务是否存活、端口是否监听",
        "查看 Nginx error.log 中 upstream 报错",
        "核对 upstream 超时配置与后端负载",
    ),
    "http_503": (
        "立即检查 OA 应用进程与服务器资源水位",
        "查看 catalina.out 报错定位根因",
        "资源恢复后按流程重启并验证（先留现场日志）",
    ),
    "service_down": (
        "确认进程与服务状态（ps / systemctl status）",
        "查看服务启动日志定位退出原因",
        "修复后按变更流程启动并验证端口监听",
    ),
    "port_conflict": (
        "用 netstat / lsof 找出占用端口的进程",
        "确认是否为残留进程或配置冲突",
        "释放端口或调整服务端口配置",
    ),
    "mysql_conn": (
        "检查 max_connections 与当前连接数",
        "排查应用连接泄漏与连接池配置",
        "确认账号权限与网络可达性",
    ),
    "mysql_slow": (
        "开启慢查询日志定位 TOP SQL",
        "检查锁等待（SHOW ENGINE INNODB STATUS）",
        "优化索引或拆分大事务",
    ),
    "redis_mem": (
        "查看 info memory 与 maxmemory 策略",
        "排查大 key 与未设置过期时间的 key",
        "调整淘汰策略或评估扩容",
    ),
    "oracle_tablespace": (
        "查看表空间使用率（dba_tablespace_usage_metrics）",
        "为表空间添加数据文件或开启自动扩展",
        "清理或归档历史数据，缓解增长",
    ),
    "oracle_undo": (
        "检查 v$undostat 与 UNDO 表空间使用",
        "优化长查询与批处理任务",
        "调整 undo_retention 或扩容 UNDO 表空间",
    ),
    "sqlserver_log": (
        "用 DBCC SQLPERF(LOGSPACE) 查看日志占用",
        "执行事务日志备份截断（完整恢复模式）",
        "排查长事务与大事务来源",
    ),
    "ssl_expired": (
        "用 openssl s_client 确认证书到期时间",
        "更新证书并重载对应服务",
        "配置证书到期监控告警",
    ),
    "dns_fail": (
        "用 nslookup / dig 验证解析结果",
        "检查 /etc/resolv.conf 与 DNS 服务状态",
        "核对域名记录与 hosts 配置",
    ),
    "net_unreach": (
        "用 ping / traceroute 分段定位不通点",
        "检查防火墙与安全组规则",
        "确认对端服务与链路状态",
    ),
    "security_bruteforce": (
        "统计失败来源 IP 与频率（lastb / 日志统计）",
        "封禁来源地址或启用 fail2ban 类工具",
        "检查账号口令强度并开启登录告警",
    ),
    "file_handles": (
        "检查 ulimit -n 与进程句柄数（lsof | wc -l）",
        "排查句柄泄漏（未关闭的连接/文件）",
        "调整 limits.conf 后重启服务",
    ),
    "perm_denied": (
        "检查文件/目录属主与权限（ls -la）",
        "确认服务运行用户与所需权限",
        "按最小权限原则修复（chown/chmod/SELinux）",
    ),
    "config_error": (
        "用对应工具校验配置（如 nginx -t）",
        "对比最近变更定位问题点",
        "回滚或修正配置后重载服务",
    ),
}


@dataclass
class KbHit:
    """知识库命中片段。"""

    doc: str
    text: str
    score: float = 0.8
    chunk_uid: str = ""


class KbRetriever(Protocol):
    """知识库检索口子（依赖注入；实现见评测脚本与 API 接线）。"""

    def search(self, query: str, k: int = KB_TOP_K) -> List[KbHit]:
        ...


@dataclass
class FunctionRetriever:
    """把纯函数包装为 KbRetriever（测试与评测接线用）。"""

    fn: Callable[[str, int], List[KbHit]]

    def search(self, query: str, k: int = KB_TOP_K) -> List[KbHit]:
        return list(self.fn(query, k))


# ---------- 内部工具 ----------


def _clip_text(text: str, limit: int = 200) -> str:
    return " ".join((text or "").split())[:limit]


def _source_kind(source: str) -> EventSource:
    prefix = source.split(":", 1)[0].strip()
    if prefix == "日志":
        return EventSource.log
    if prefix == "巡检":
        return EventSource.inspection
    if prefix == "告警":
        return EventSource.alert
    return EventSource.manual


_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}")


def _extract_timestamp(text: str) -> Optional[str]:
    match = _TIMESTAMP_RE.search(text or "")
    return match.group(0) if match else None


def _bigrams(text: str) -> set:
    cleaned = "".join(ch for ch in text if ch.isalnum())
    if len(cleaned) < 2:
        return set()
    return {cleaned[i : i + 2] for i in range(len(cleaned) - 1)}


def _title_overlap(text: str, title: str) -> int:
    return sum(1 for gram in _bigrams(title) if gram in text)


def _score_signals(signals: Sequence[RuleSignal]) -> float:
    """候选基础分 = 最强信号 + 多源/多条加成，封顶 0.95。"""
    score = max(signal.base_score for signal in signals)
    kinds = {_source_kind(signal.source) for signal in signals}
    if len(kinds) > 1:
        score += 0.05
    if len(signals) >= 2:
        score += 0.03
    return min(round(score, 3), 0.95)


def build_events(signals: Sequence[RuleSignal], service: str = "", host: str = "") -> List[IncidentEvent]:
    """把信号标准化为 IncidentEvent（按证据原文去重）。"""
    events: List[IncidentEvent] = []
    seen: set = set()
    for signal in signals:
        key = (signal.evidence_text, signal.source)
        if key in seen:
            continue
        seen.add(key)
        events.append(
            IncidentEvent(
                source=_source_kind(signal.source),
                service=service,
                host=host,
                metric=signal.category,
                severity=signal.severity,
                raw=signal.evidence_text,
                tags=[signal.rule_id],
                timestamp=_extract_timestamp(signal.evidence_text) or now_iso(),
            )
        )
    return events


def _kb_query(candidates: Sequence[CandidateCause], service: str) -> str:
    titles = " ".join(candidate.title for candidate in candidates[:3])
    return f"{service} {titles}".strip()


def _best_candidate_for(text: str, candidates: Sequence[CandidateCause]) -> Optional[CandidateCause]:
    best: Optional[CandidateCause] = None
    best_overlap = 0
    for candidate in candidates:
        overlap = _title_overlap(text, candidate.title)
        if overlap >= KB_TITLE_OVERLAP_MIN and overlap > best_overlap:
            best = candidate
            best_overlap = overlap
    return best


def _history_cause_id(entry: Mapping[str, Any]) -> str:
    root = entry.get("root_cause")
    if isinstance(root, Mapping):
        return str(root.get("cause_id") or "")
    return str(entry.get("cause_id") or "")


def _suggestions_for(cause: Optional[CandidateCause]) -> List[str]:
    if cause is None:
        return []
    items = CAUSE_SUGGESTIONS.get(cause.cause_id)
    if items:
        return list(items)[:3]
    return [f"依据知识库与处置手册进一步核实「{cause.title}」，按标准流程处置"]


def _narrative_prompt(candidates: Sequence[CandidateCause], evidence: Sequence[Evidence]) -> str:
    lines = [
        "你是运维根因诊断助手。只允许基于以下已有候选与证据组织中文叙述，禁止新增候选或根因：",
        "",
    ]
    for candidate in candidates:
        lines.append(f"- 候选：{candidate.title}（置信度 {candidate.score:.2f}）")
    lines.append("证据：")
    for item in evidence[:10]:
        lines.append(f"- [{item.kind.value}] {item.content}")
    lines.append("")
    lines.append("请输出不超过 150 字的诊断叙述，并明确指出证据不足之处（如有）。")
    return "\n".join(lines)


# ---------- 主管线 ----------


def analyze_incident(
    log_text: str = "",
    inspection_results: Optional[Sequence[Mapping[str, Any]]] = None,
    alerts: Optional[Sequence[Mapping[str, Any]]] = None,
    service: str = "",
    host: str = "",
    retriever: Optional[KbRetriever] = None,
    history: Optional[Sequence[Mapping[str, Any]]] = None,
    llm_narrator: Optional[Callable[[str], str]] = None,
) -> DiagnosisReport:
    """执行只读根因诊断，返回 DiagnosisReport（确定性；增强项均可降级）。"""
    started = time.perf_counter()
    signals = collect_signals(log_text=log_text, inspection_results=inspection_results, alerts=alerts)
    events = build_events(signals, service=service, host=host)

    evidence: List[Evidence] = []
    by_cause: Dict[str, List[RuleSignal]] = {}
    cause_evidence_ids: Dict[str, List[str]] = {}
    for signal in signals:
        by_cause.setdefault(signal.cause_id, []).append(signal)
        item = Evidence(
            kind=signal.evidence_kind,
            content=signal.evidence_text,
            source=signal.source,
            strength=signal.base_score,
            refs=[signal.rule_id],
        )
        evidence.append(item)
        cause_evidence_ids.setdefault(signal.cause_id, []).append(item.id)

    order = {cause_id: index for index, cause_id in enumerate(CAUSE_CATALOG)}
    candidates: List[CandidateCause] = []
    for cause_id, cause_signals in by_cause.items():
        info = CAUSE_CATALOG[cause_id]
        rule_ids = ", ".join(sorted({s.rule_id for s in cause_signals}))
        candidates.append(
            CandidateCause(
                cause_id=cause_id,
                title=info.title,
                category=info.category,
                score=_score_signals(cause_signals),
                evidence_ids=list(cause_evidence_ids[cause_id]),
                rationale=f"规则信号 {len(cause_signals)} 条（{rule_ids}）",
            )
        )

    meta: Dict[str, Any] = {}
    citations: List[str] = []
    kb_assigned = 0

    # ---- 知识库增强（可选；失败降级为无 KB） ----
    if retriever is not None and candidates:
        try:
            hits = list(retriever.search(_kb_query(candidates, service), k=KB_TOP_K))
        except Exception as exc:  # noqa: BLE001 - 检索失败不阻断诊断
            hits = []
            meta["kb_error"] = str(exc)[:200]
        for hit in hits[:KB_TOP_K]:
            target = _best_candidate_for(hit.text, candidates)
            if target is None:
                continue
            item = Evidence(
                kind=EvidenceKind.kb_citation,
                content=_clip_text(hit.text),
                source=hit.doc,
                strength=min(0.95, max(0.3, float(hit.score or 0.5))),
                refs=[hit.chunk_uid] if hit.chunk_uid else [],
            )
            evidence.append(item)
            target.evidence_ids.append(item.id)
            target.score = min(0.95, round(target.score + 0.02, 3))
            doc_ref = hit.doc if hit.doc.startswith("《") else f"《{hit.doc}》"
            if doc_ref not in citations:
                citations.append(doc_ref)
            kb_assigned += 1

    # ---- 历史故障增强（可选；同一原因只取最早一条，防重复刷分） ----
    history_matched = 0
    history_seen: set = set()
    if history and candidates:
        for entry in list(history)[:HISTORY_MAX_ENTRIES]:
            cause_id = _history_cause_id(entry)
            if cause_id in history_seen:
                continue
            target = next((c for c in candidates if c.cause_id == cause_id), None)
            if target is None:
                continue
            history_seen.add(cause_id)
            incident_ref = str(entry.get("incident_id") or "历史诊断")
            item = Evidence(
                kind=EvidenceKind.history_match,
                content=f"历史诊断 {incident_ref} 的根因为「{target.title}」",
                source=incident_ref,
                strength=0.6,
            )
            evidence.append(item)
            target.evidence_ids.append(item.id)
            target.score = min(0.95, round(target.score + 0.03, 3))
            history_matched += 1

    # ---- 关联（多源互证）证据 ----
    correlations = 0
    for cause_id, cause_signals in by_cause.items():
        kinds = {_source_kind(s.source) for s in cause_signals}
        if len(kinds) < 2:
            continue
        labels = "、".join(sorted(kind.value for kind in kinds))
        evidence.append(
            Evidence(
                kind=EvidenceKind.correlation,
                content=f"多源信号互证（{labels}）同时指向「{CAUSE_CATALOG[cause_id].title}」",
                source="关联",
                strength=0.7,
            )
        )
        correlations += 1

    # ---- 排序与决策 ----
    candidates.sort(key=lambda c: (-c.score, order.get(c.cause_id, 999)))
    root: Optional[CandidateCause] = None
    alternatives: List[CandidateCause] = []
    uncertain_reasons: List[str] = []
    if not candidates:
        uncertain_reasons.append("未从输入中识别到已知故障信号；请补充日志 / 巡检 / 告警数据后重试")
    else:
        top = candidates[0]
        unique_evidence = len(set(top.evidence_ids))
        if unique_evidence >= MIN_EVIDENCE_FOR_ROOT_CAUSE and top.score >= MIN_ROOT_SCORE:
            root = top
            alternatives = candidates[1:]
        else:
            if unique_evidence < MIN_EVIDENCE_FOR_ROOT_CAUSE:
                uncertain_reasons.append(
                    f"最高候选「{top.title}」仅 {unique_evidence} 条证据"
                    f"（不足 {MIN_EVIDENCE_FOR_ROOT_CAUSE} 条），不强凑证据"
                )
            if top.score < MIN_ROOT_SCORE:
                uncertain_reasons.append(
                    f"最高候选「{top.title}」置信度 {top.score:.2f} 低于阈值 {MIN_ROOT_SCORE}"
                )
            alternatives = candidates

    suggestions = _suggestions_for(root or (candidates[0] if candidates else None))

    meta.update(
        {
            "signals": len(signals),
            "candidates": len(candidates),
            "kb_assigned": kb_assigned,
            "history_matched": history_matched,
            "correlations": correlations,
            "degraded": retriever is None or bool(meta.get("kb_error")),
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
    )

    if llm_narrator is not None:
        try:
            narrative = str(llm_narrator(_narrative_prompt(candidates, evidence)))
            meta["llm_narrative"] = narrative[:2000]
            meta["llm_used"] = True
        except Exception as exc:  # noqa: BLE001 - LLM 失败不影响确定性报告
            meta["llm_error"] = str(exc)[:200]
            meta["llm_used"] = False
            meta["degraded"] = True

    return DiagnosisReport(
        status=ReportStatus.ok if root is not None else ReportStatus.uncertain,
        root_cause=root,
        alternatives=alternatives,
        evidence=evidence,
        events=events,
        suggestions=suggestions,
        citations=citations,
        uncertain_reasons=uncertain_reasons,
        meta=meta,
    )
