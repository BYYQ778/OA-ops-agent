"""incident_agent 管线单测：全部离线（fake 检索/历史/LLM），验证证据纪律与降级路径。"""

from agents.incident_agent import (
    FunctionRetriever,
    KbHit,
    analyze_incident,
    build_events,
)
from agents.incident_rules import CAUSE_CATALOG, match_log_signals
from utils.incident_models import DiagnosisReport, EventSource, EvidenceKind, ReportStatus

DISK_LOG = "2026-09-18 09:12:44 [FATAL] No space left on device - /data/logs/oa.log"
DISK_LOG_2 = "2026-09-18 09:13:02 [ERROR] write failed: No space left on device (/data/upload)"


def disk_hit() -> KbHit:
    return KbHit(
        doc="服务器磁盘空间不足处置指南",
        text="磁盘空间不足时：查看磁盘使用情况 df -h，清理过期日志与临时文件，必要时扩容或归档历史数据。",
        score=0.86,
    )


class FakeRetriever:
    def __init__(self, hits: list[KbHit] | None = None) -> None:
        self.hits = hits or []
        self.queries: list[str] = []

    def search(self, query: str, k: int = 3) -> list[KbHit]:
        self.queries.append(query)
        return self.hits[:k]


# ---------- 基本路径 ----------


def test_ok_report_with_signal_and_kb() -> None:
    report = analyze_incident(log_text=DISK_LOG, retriever=FakeRetriever([disk_hit()]))
    assert report.status == ReportStatus.ok
    assert report.root_cause is not None
    assert report.root_cause.cause_id == "disk_full"
    assert len(report.root_cause.evidence_ids) >= 2
    assert "《服务器磁盘空间不足处置指南》" in report.citations
    assert report.suggestions
    assert report.read_only is True
    assert report.meta["degraded"] is False
    kinds = {item.kind for item in report.evidence}
    assert EvidenceKind.log_pattern in kinds
    assert EvidenceKind.kb_citation in kinds


def test_single_signal_without_kb_is_uncertain() -> None:
    report = analyze_incident(log_text=DISK_LOG)
    assert report.status == ReportStatus.uncertain
    assert report.root_cause is None
    assert any("不足" in reason for reason in report.uncertain_reasons)
    assert report.alternatives
    assert report.alternatives[0].cause_id == "disk_full"
    assert report.confidence == 0.0


def test_two_signals_same_cause_reach_ok() -> None:
    report = analyze_incident(log_text=f"{DISK_LOG}\n{DISK_LOG_2}")
    assert report.status == ReportStatus.ok
    assert report.root_cause is not None
    assert len(set(report.root_cause.evidence_ids)) >= 2


def test_multi_source_bumps_score_and_adds_correlation() -> None:
    inspection = [{"check_type": "disk", "result": "  [告警] /: 使用率 92% (超过阈值85%)"}]
    report = analyze_incident(log_text=DISK_LOG, inspection_results=inspection)
    assert report.status == ReportStatus.ok
    assert report.root_cause is not None
    assert report.root_cause.score > 0.85  # 0.85 + 0.05(多源) + 0.03(多条)
    kinds = {item.kind for item in report.evidence}
    assert EvidenceKind.correlation in kinds
    assert report.meta["correlations"] == 1


# ---------- 不确定路径 ----------


def test_no_signals_uncertain() -> None:
    report = analyze_incident(log_text="2026-09-18 [INFO] 一切正常，心跳检查通过")
    assert report.status == ReportStatus.uncertain
    assert report.root_cause is None
    assert report.evidence == []
    assert report.alternatives == []
    assert report.uncertain_reasons


def test_weak_single_signal_below_score_floor() -> None:
    report = analyze_incident(log_text="Permission denied: /etc/nginx/conf.d/oa.conf")
    assert report.status == ReportStatus.uncertain
    assert any("置信度" in reason for reason in report.uncertain_reasons)


def test_ranking_puts_higher_score_first() -> None:
    report = analyze_incident(log_text=f"{DISK_LOG}\nPermission denied: /etc/nginx/conf.d/oa.conf")
    assert report.alternatives
    assert report.alternatives[0].cause_id == "disk_full"
    by_id = {candidate.cause_id: candidate for candidate in report.alternatives}
    assert by_id["disk_full"].score > by_id["perm_denied"].score


# ---------- 检索/历史/LLM 增强 ----------


def test_unrelated_kb_hit_is_not_cited() -> None:
    hit = KbHit(doc="网络连通性排查手册", text="网络不通时检查防火墙规则与链路状态。", score=0.9)
    report = analyze_incident(log_text=DISK_LOG, retriever=FakeRetriever([hit]))
    assert report.citations == []
    assert all(item.kind != EvidenceKind.kb_citation for item in report.evidence)


def test_history_match_counts_as_evidence() -> None:
    history = [{"incident_id": "INC-OLD-1", "root_cause": {"cause_id": "disk_full"}}]
    report = analyze_incident(log_text=DISK_LOG, history=history)
    assert report.status == ReportStatus.ok
    assert any(item.kind == EvidenceKind.history_match for item in report.evidence)
    assert report.meta["history_matched"] == 1


def test_kb_error_degrades_gracefully() -> None:
    class BrokenRetriever:
        def search(self, query: str, k: int = 3) -> list[KbHit]:
            raise RuntimeError("索引不可用")

    report = analyze_incident(log_text=f"{DISK_LOG}\n{DISK_LOG_2}", retriever=BrokenRetriever())
    assert report.status == ReportStatus.ok
    assert "kb_error" in report.meta
    assert report.meta["degraded"] is True


def test_llm_narrator_recorded() -> None:
    def narrator(prompt: str) -> str:
        assert "禁止新增候选或根因" in prompt
        return "磁盘已满导致应用写入失败。"

    report = analyze_incident(log_text=DISK_LOG, retriever=FakeRetriever([disk_hit()]), llm_narrator=narrator)
    assert report.meta["llm_used"] is True
    assert "磁盘已满" in report.meta["llm_narrative"]


def test_llm_narrator_failure_degrades_only() -> None:
    def boom(prompt: str) -> str:
        raise RuntimeError("模型不可用")

    report = analyze_incident(log_text=DISK_LOG, retriever=FakeRetriever([disk_hit()]), llm_narrator=boom)
    assert report.status == ReportStatus.ok
    assert "llm_error" in report.meta
    assert report.meta["degraded"] is True


def test_function_retriever_wraps_callable() -> None:
    def fn(query: str, k: int) -> list[KbHit]:
        return [disk_hit()]

    report = analyze_incident(log_text=DISK_LOG, retriever=FunctionRetriever(fn))
    assert report.status == ReportStatus.ok


# ---------- 事件与确定性 ----------


def test_events_built_with_metadata() -> None:
    report = analyze_incident(log_text=DISK_LOG, service="oa-web", host="10.20.1.11")
    assert report.events
    event = report.events[0]
    assert event.source == EventSource.log
    assert event.service == "oa-web"
    assert event.host == "10.20.1.11"
    assert event.timestamp.startswith("2026-09-18")


def test_build_events_deduplicates_same_line() -> None:
    signals = match_log_signals(f"{DISK_LOG}\n{DISK_LOG}")
    events = build_events(signals, service="oa-web")
    assert len(events) == 1


def test_deterministic_repeat() -> None:
    first = analyze_incident(log_text=DISK_LOG, retriever=FakeRetriever([disk_hit()]), service="oa")
    second = analyze_incident(log_text=DISK_LOG, retriever=FakeRetriever([disk_hit()]), service="oa")
    assert first.status == second.status
    assert first.root_cause is not None and second.root_cause is not None
    assert first.root_cause.cause_id == second.root_cause.cause_id
    assert first.root_cause.score == second.root_cause.score
    assert len(first.evidence) == len(second.evidence)


def test_report_json_roundtrip_via_agent() -> None:
    report = analyze_incident(log_text=DISK_LOG, retriever=FakeRetriever([disk_hit()]))
    loaded = DiagnosisReport.model_validate_json(report.model_dump_json())
    assert loaded.status == report.status
    assert loaded.root_cause is not None
    assert loaded.root_cause.cause_id == "disk_full"


def test_suggestions_table_covers_catalog() -> None:
    from agents.incident_agent import CAUSE_SUGGESTIONS

    assert set(CAUSE_SUGGESTIONS) == set(CAUSE_CATALOG)
    assert all(len(items) >= 1 for items in CAUSE_SUGGESTIONS.values())
