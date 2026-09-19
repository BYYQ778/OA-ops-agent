"""第 4 周根因诊断评测执行器：逐案例诊断 → Top-1 / 不确定判定 / 报告完整性统计。

- analyze_fn 由调用方注入（真实 = analyze_incident；测试 = fake），因此本模块离线可测。
- 统计口径:
  · Top-1 准确率 = 可诊断（expected ok）案例中 status=ok 且根因等于预期的比例；
  · 不确定判定正确率 = expected uncertain 案例中 status=uncertain 的比例；
  · report_violations = 报告完整性违规数（ok 报告需 ≥2 证据 + ≥1 建议；应为 0）。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence

from evals.incident_cases import IncidentCase

AnalyzeFn = Callable[[IncidentCase], Any]


def evaluate_case(case: IncidentCase, report: Any) -> Dict[str, Any]:
    """单案例评分：对比报告与预期，产出可写入结果 JSON 的明细行。"""
    status = getattr(report.status, "value", str(report.status))
    root = report.root_cause
    root_id = str(getattr(root, "cause_id", "") or "") if root is not None else ""
    evidence_count = len(set(root.evidence_ids)) if root is not None else 0
    evidence_ok = status != "ok" or evidence_count >= 2
    suggestions_ok = status != "ok" or bool(report.suggestions)
    row: Dict[str, Any] = {
        "id": case.id,
        "category": case.category,
        "split": case.split,
        "expected_status": case.expected_status,
        "expected_cause_id": case.expected_cause_id,
        "status": status,
        "root_cause_id": root_id,
        "evidence_count": evidence_count,
        "evidence_ok": evidence_ok,
        "suggestions_ok": suggestions_ok,
        "citations": len(report.citations),
        "signals": int(report.meta.get("signals", 0)),
        "kb_assigned": int(report.meta.get("kb_assigned", 0)),
        "duration_ms": int(report.meta.get("duration_ms", 0)),
    }
    if case.expected_status == "ok":
        row["top1_hit"] = status == "ok" and root_id == case.expected_cause_id
        row["uncertain_correct"] = None
    else:
        row["top1_hit"] = None
        row["uncertain_correct"] = status == "uncertain"
    return row


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """把逐案例明细聚合为总体/分拆/分类统计。"""
    ok_rows = [r for r in rows if r["expected_status"] == "ok"]
    uncertain_rows = [r for r in rows if r["expected_status"] == "uncertain"]
    top1_correct = sum(1 for r in ok_rows if r["top1_hit"])
    uncertain_correct = sum(1 for r in uncertain_rows if r["uncertain_correct"])
    violations = sum(1 for r in rows if not r["evidence_ok"] or not r["suggestions_ok"])

    def _rate(numerator: int, denominator: int) -> Optional[float]:
        return round(numerator / denominator, 4) if denominator else None

    by_split: Dict[str, Any] = {}
    for split_name in ("dev", "holdout"):
        subset = [r for r in rows if r["split"] == split_name]
        if not subset:
            continue
        split_ok = [r for r in subset if r["expected_status"] == "ok"]
        split_uncertain = [r for r in subset if r["expected_status"] == "uncertain"]
        by_split[split_name] = {
            "total": len(subset),
            "top1_correct": sum(1 for r in split_ok if r["top1_hit"]),
            "top1_total": len(split_ok),
            "uncertain_correct": sum(1 for r in split_uncertain if r["uncertain_correct"]),
            "uncertain_total": len(split_uncertain),
        }

    by_category: Dict[str, Any] = {}
    for row in rows:
        bucket = by_category.setdefault(row["category"], {"total": 0, "ok_cases": 0, "top1_correct": 0})
        bucket["total"] += 1
        if row["expected_status"] == "ok":
            bucket["ok_cases"] += 1
            if row["top1_hit"]:
                bucket["top1_correct"] += 1

    return {
        "counts": {"total": len(rows), "ok_cases": len(ok_rows), "uncertain_cases": len(uncertain_rows)},
        "top1": {"correct": top1_correct, "total": len(ok_rows), "accuracy": _rate(top1_correct, len(ok_rows))},
        "uncertain": {
            "correct": uncertain_correct,
            "total": len(uncertain_rows),
            "accuracy": _rate(uncertain_correct, len(uncertain_rows)),
        },
        "report_violations": violations,
        "avg_signals": round(sum(r["signals"] for r in rows) / len(rows), 2) if rows else 0.0,
        "avg_duration_ms": int(sum(r["duration_ms"] for r in rows) / len(rows)) if rows else 0,
        "by_split": by_split,
        "by_category": by_category,
        "cases": rows,
    }


def run_incident_eval(cases: Sequence[IncidentCase], analyze_fn: AnalyzeFn, split: str = "all") -> Dict[str, Any]:
    """执行评测（split: all / dev / holdout）。"""
    selected = [case for case in cases if split == "all" or case.split == split]
    rows = [evaluate_case(case, analyze_fn(case)) for case in selected]
    return summarize(rows)
