"""案例加载/校验与评测统计单测（fixture 案例 + 真实诊断管线小样本）。"""

import json
from pathlib import Path
from typing import Any

from agents.incident_agent import analyze_incident
from evals.incident_cases import IncidentCase, load_incident_cases, validate_incident_cases
from evals.incident_runner import evaluate_case, run_incident_eval, summarize

DISK_LOG = "2026-09-18 09:12:44 [FATAL] No space left on device - /data/logs/oa.log"

OK_CASE: dict[str, Any] = {
    "id": "t-ok-1",
    "category": "resource",
    "title": "磁盘写满",
    "split": "dev",
    "expected_status": "ok",
    "expected_cause_id": "disk_full",
    "expected_keywords": ["No space left on device"],
    "log_text": DISK_LOG + "\n2026-09-18 09:13:02 [ERROR] No space left on device - /data/upload",
}


def _case(**overrides: Any) -> IncidentCase:
    data: dict[str, Any] = dict(OK_CASE)
    data.update(overrides)
    return IncidentCase(**data)


def _write_jsonl(tmp_path: Path, rows: list) -> Path:
    path = tmp_path / "cases.jsonl"
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")
    return path


def _analyze(case: IncidentCase):
    return analyze_incident(
        log_text=case.log_text,
        inspection_results=case.inspection_results or None,
        alerts=case.alerts or None,
        service=case.service,
        host=case.host,
    )


# ---------- 加载与校验 ----------


def test_load_and_validate_valid_case(tmp_path: Path) -> None:
    path = _write_jsonl(tmp_path, [OK_CASE])
    cases = load_incident_cases(path)
    assert len(cases) == 1
    assert cases[0].expected_cause_id == "disk_full"
    errors, warnings, stats = validate_incident_cases(cases, min_cases=1)
    assert errors == []
    assert warnings == []
    assert stats["total"] == 1
    assert stats["ok_cases_with_two_signals"] == 1


def test_load_rejects_unknown_field(tmp_path: Path) -> None:
    bad: dict[str, Any] = dict(OK_CASE)
    bad["unexpected_key"] = 1
    path = _write_jsonl(tmp_path, [bad])
    try:
        load_incident_cases(path)
        raise AssertionError("应当抛出 ValueError")
    except ValueError as exc:
        assert "未知字段" in str(exc)


def test_validate_catches_structural_errors() -> None:
    cases = [
        IncidentCase(id="a", category="resource", title="x", expected_cause_id="not_exist", expected_keywords=["k"],
                     log_text="k"),
        IncidentCase(id="a", category="resource", title="x", expected_status="weird", log_text="x"),
        IncidentCase(id="b", category="resource", title="x", expected_status="uncertain",
                     expected_cause_id="disk_full", log_text="x"),
    ]
    errors, _warnings, _stats = validate_incident_cases(cases, min_cases=1)
    joined = " ".join(errors)
    assert "id 重复" in joined
    assert "不在候选目录" in joined
    assert "expected_status 非法" in joined
    assert "不应指定 expected_cause_id" in joined


def test_validate_catches_keyword_not_in_input() -> None:
    cases = [_case(expected_keywords=["不存在的锚点"])]
    errors, _warnings, _stats = validate_incident_cases(cases, min_cases=1)
    assert any("锚点未出现在输入中" in err for err in errors)


def test_validate_warns_on_single_signal() -> None:
    cases = [
        _case(
            id="t-weak",
            category="oa_service",
            title="单条弱信号",
            expected_cause_id="service_down",
            expected_keywords=["Connection refused"],
            log_text="2026-09-18 [WARN] Connection refused",
        )
    ]
    errors, warnings, _stats = validate_incident_cases(cases, min_cases=1)
    assert errors == []
    assert any("信号证据仅" in w for w in warnings)


def test_validate_enforces_min_cases() -> None:
    cases = [_case(id="a", expected_keywords=["No space"], log_text="No space left on device")]
    errors, _warnings, _stats = validate_incident_cases(cases, min_cases=30)
    assert any("案例总数" in err for err in errors)


# ---------- 评测统计 ----------


def test_runner_metrics_with_real_analyze() -> None:
    cases = [
        _case(),
        _case(
            id="t-wrong",
            category="oa_service",
            title="预期错误（输入为磁盘问题）",
            expected_cause_id="oom",
            expected_keywords=["No space left on device"],
            log_text=DISK_LOG,
        ),
        _case(
            id="t-uncertain",
            category="resource",
            title="无信号",
            expected_status="uncertain",
            expected_cause_id="",
            expected_keywords=[],
            log_text="2026-09-18 [INFO] 一切正常",
        ),
    ]
    summary = run_incident_eval(cases, _analyze)
    assert summary["counts"] == {"total": 3, "ok_cases": 2, "uncertain_cases": 1}
    assert summary["top1"]["correct"] == 1
    assert summary["top1"]["total"] == 2
    assert summary["top1"]["accuracy"] == 0.5
    assert summary["uncertain"]["correct"] == 1
    assert summary["uncertain"]["accuracy"] == 1.0
    assert summary["report_violations"] == 0


def test_runner_split_filter() -> None:
    cases = [
        _case(),
        _case(id="t-ok-2", split="holdout"),
    ]
    summary = run_incident_eval(cases, _analyze, split="holdout")
    assert summary["counts"]["total"] == 1
    assert summary["by_split"]["holdout"]["top1_correct"] == 1


def test_evaluate_case_flags_report_violation() -> None:
    class FakeRoot:
        cause_id = "disk_full"
        evidence_ids = ["EV-only-one"]

    class FakeReport:
        status = type("S", (), {"value": "ok"})()
        root_cause = FakeRoot()
        suggestions: list = []
        citations: list = []
        meta = {"signals": 1, "kb_assigned": 0, "duration_ms": 5}

    row = evaluate_case(_case(), FakeReport())
    assert row["evidence_ok"] is False
    assert row["suggestions_ok"] is False
    assert row["top1_hit"] is True


def test_summarize_empty_rows() -> None:
    summary = summarize([])
    assert summary["top1"]["accuracy"] is None
    assert summary["report_violations"] == 0
