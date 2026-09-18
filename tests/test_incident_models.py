"""根因诊断数据模型单测：证据纪律（≥2 条证据才可给确定根因）、只读约束、
引用完整性、边界与 JSON 往返。"""

from datetime import datetime

import pytest
from pydantic import ValidationError

from utils.incident_models import (
    MIN_EVIDENCE_FOR_ROOT_CAUSE,
    CandidateCause,
    DiagnosisReport,
    EventSource,
    Evidence,
    EvidenceKind,
    IncidentEvent,
    ReportStatus,
    Severity,
    new_incident_id,
    now_iso,
)

DEFAULT_SUGGESTION = "清理 /var/log 历史日志（参考《服务器磁盘空间不足处置指南》）"
DEFAULT_CITATION = "《服务器磁盘空间不足处置指南》"


def _evidence(
    eid: str,
    kind: EvidenceKind = EvidenceKind.log_pattern,
    content: str = "磁盘使用率 96.4%，超过阈值 90%",
    strength: float = 0.8,
    source: str = "巡检",
) -> Evidence:
    return Evidence(id=eid, kind=kind, content=content, strength=strength, source=source)


def _cause(cid: str, evidence_ids: list[str], score: float = 0.8) -> CandidateCause:
    return CandidateCause(cause_id=cid, title=f"候选根因 {cid}", score=score, evidence_ids=evidence_ids)


def _two_evidence() -> list[Evidence]:
    return [
        _evidence("EV-1"),
        _evidence(
            "EV-2",
            kind=EvidenceKind.kb_citation,
            content="处置：清理 /var/log 历史日志后复查磁盘",
            source=DEFAULT_CITATION,
        ),
    ]


def _ok_report(
    root_cause: CandidateCause | None = None,
    alternatives: list[CandidateCause] | None = None,
    evidence: list[Evidence] | None = None,
    suggestions: list[str] | None = None,
    uncertain_reasons: list[str] | None = None,
) -> DiagnosisReport:
    return DiagnosisReport(
        incident_id="INC-TEST-0001",
        status=ReportStatus.ok,
        root_cause=root_cause if root_cause is not None else _cause("C1", ["EV-1", "EV-2"], score=0.86),
        alternatives=alternatives if alternatives is not None else [],
        evidence=evidence if evidence is not None else _two_evidence(),
        suggestions=suggestions if suggestions is not None else [DEFAULT_SUGGESTION],
        citations=[DEFAULT_CITATION],
        uncertain_reasons=uncertain_reasons if uncertain_reasons is not None else [],
    )


# ---------- 合法构造 ----------


def test_ok_report_valid_and_confidence_computed() -> None:
    report = _ok_report()
    assert report.status == "ok"
    assert report.root_cause is not None
    assert report.confidence == pytest.approx(0.86)
    assert report.read_only is True
    assert report.model_dump()["confidence"] == pytest.approx(0.86)


def test_uncertain_report_valid() -> None:
    report = DiagnosisReport(
        incident_id="INC-TEST-0002",
        status=ReportStatus.uncertain,
        uncertain_reasons=["仅 1 条证据，不足 2 条，不强凑"],
        evidence=[_evidence("EV-1", kind=EvidenceKind.correlation)],
        suggestions=["补充磁盘巡检数据后重试"],
    )
    assert report.root_cause is None
    assert report.confidence == 0.0
    assert report.status == "uncertain"


def test_json_roundtrip() -> None:
    report = _ok_report()
    loaded = DiagnosisReport.model_validate_json(report.model_dump_json())
    assert loaded.model_dump() == report.model_dump()
    assert loaded.confidence == pytest.approx(0.86)


def test_string_payload_coercion() -> None:
    """原始字符串载荷（JSON/表单风格）可被校验并归一为枚举。"""
    report = DiagnosisReport.model_validate(
        {
            "incident_id": "INC-X",
            "status": "uncertain",
            "uncertain_reasons": ["证据不足"],
            "read_only": True,
        }
    )
    assert report.status == ReportStatus.uncertain


# ---------- 证据纪律 ----------


def test_ok_requires_root_cause() -> None:
    with pytest.raises(ValidationError) as excinfo:
        DiagnosisReport(
            incident_id="INC-X",
            status=ReportStatus.ok,
            evidence=_two_evidence(),
            suggestions=[DEFAULT_SUGGESTION],
        )
    assert "根因" in str(excinfo.value)


def test_ok_requires_two_evidence_on_root_cause() -> None:
    with pytest.raises(ValidationError) as excinfo:
        _ok_report(root_cause=_cause("C1", ["EV-1"], score=0.9))
    assert "证据" in str(excinfo.value)


def test_ok_rejects_uncertain_reasons() -> None:
    with pytest.raises(ValidationError):
        _ok_report(uncertain_reasons=["这里不该出现"])


def test_ok_requires_suggestion() -> None:
    with pytest.raises(ValidationError):
        _ok_report(suggestions=[])


def test_uncertain_requires_reasons() -> None:
    with pytest.raises(ValidationError):
        DiagnosisReport(incident_id="INC-X", status=ReportStatus.uncertain)


def test_uncertain_rejects_root_cause() -> None:
    with pytest.raises(ValidationError):
        DiagnosisReport(
            incident_id="INC-X",
            status=ReportStatus.uncertain,
            uncertain_reasons=["证据不足"],
            root_cause=_cause("C1", ["EV-1"]),
            evidence=[_evidence("EV-1")],
        )


def test_min_evidence_constant_is_two() -> None:
    assert MIN_EVIDENCE_FOR_ROOT_CAUSE == 2


# ---------- 只读约束 ----------


def test_read_only_cannot_be_disabled() -> None:
    with pytest.raises(ValidationError) as excinfo:
        DiagnosisReport(
            incident_id="INC-X",
            status=ReportStatus.uncertain,
            uncertain_reasons=["x"],
            read_only=False,
        )
    assert "只读" in str(excinfo.value)


# ---------- 引用完整性 ----------


def test_root_cause_refs_must_exist() -> None:
    with pytest.raises(ValidationError):
        _ok_report(root_cause=_cause("C1", ["EV-1", "EV-404"]))


def test_alternative_refs_must_exist() -> None:
    with pytest.raises(ValidationError):
        _ok_report(alternatives=[_cause("C2", ["EV-404"])])


def test_duplicate_evidence_ids_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        _ok_report(
            evidence=[_evidence("EV-1"), _evidence("EV-1", content="另一段证据"), _evidence("EV-2")],
        )
    assert "证据 id 重复" in str(excinfo.value)


def test_duplicate_cause_ids_rejected() -> None:
    with pytest.raises(ValidationError):
        _ok_report(alternatives=[_cause("C1", ["EV-1"])])


# ---------- 边界 ----------


def test_strength_and_score_bounds() -> None:
    with pytest.raises(ValidationError):
        _evidence("EV-1", strength=1.5)
    with pytest.raises(ValidationError):
        _cause("C1", ["EV-1"], score=1.2)


def test_evidence_content_required() -> None:
    with pytest.raises(ValidationError):
        Evidence(kind=EvidenceKind.log_pattern, content="")


def test_candidate_requires_at_least_one_evidence() -> None:
    with pytest.raises(ValidationError):
        CandidateCause(cause_id="C1", title="凭空结论", evidence_ids=[])


def test_event_requires_raw_and_valid_timestamp() -> None:
    event = IncidentEvent(
        source=EventSource.log,
        raw="ERROR disk usage 96%",
        service="oa-web",
        timestamp="2026-09-18 10:00:00",
    )
    assert event.severity == Severity.warning
    with pytest.raises(ValidationError):
        IncidentEvent(source=EventSource.log, raw="")
    with pytest.raises(ValidationError):
        IncidentEvent(source=EventSource.log, raw="x", timestamp="not-a-date")


def test_ids_and_time_helpers() -> None:
    incident_id = new_incident_id()
    assert incident_id.startswith("INC-")
    assert len(incident_id) == len("INC-YYYYMMDDHHMMSS-xxxxxx")
    datetime.fromisoformat(now_iso())
