"""第 4 周根因诊断标准案例：加载、校验与统计（离线可测，不依赖重依赖）。

案例字段（JSONL 一行一条）:
    id / category / title / split(dev|holdout) / expected_status(ok|uncertain) /
    expected_cause_id / expected_keywords[] / log_text / inspection_results[] /
    alerts[] / service / host / notes

校验口径:
- 错误：结构缺失、id 重复、expected_status 非法、ok 案例原因不在候选目录、
  ok 案例关键字缺失或未出现在输入中、uncertain 案例不应给确定原因、split 非法；
- 警告：ok 案例的规则信号证据 <2（可由知识库引用补足）；<30 条总数报错误。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Tuple

from agents.incident_rules import CAUSE_CATALOG, collect_signals

VALID_SPLITS = ("dev", "holdout")
VALID_STATUSES = ("ok", "uncertain")


@dataclass
class IncidentCase:
    id: str
    category: str
    title: str
    split: str = "dev"
    expected_status: str = "ok"
    expected_cause_id: str = ""
    expected_keywords: List[str] = field(default_factory=list)
    log_text: str = ""
    inspection_results: List[Dict[str, Any]] = field(default_factory=list)
    alerts: List[Dict[str, Any]] = field(default_factory=list)
    service: str = ""
    host: str = ""
    notes: str = ""

    def haystack(self) -> str:
        """全部输入的拼接文本（锚点校验用）。"""
        return "\n".join(
            [
                self.log_text,
                json.dumps(self.inspection_results, ensure_ascii=False),
                json.dumps(self.alerts, ensure_ascii=False),
            ]
        )

    def signal_evidence_count(self) -> int:
        """预期原因在输入中的规则信号证据条数（按 rule_id + 证据原文去重）。"""
        signals = collect_signals(
            log_text=self.log_text,
            inspection_results=self.inspection_results or None,
            alerts=self.alerts or None,
        )
        seen = {(s.rule_id, s.evidence_text) for s in signals if s.cause_id == self.expected_cause_id}
        return len(seen)


def load_incident_cases(path: Path) -> List[IncidentCase]:
    """从 JSONL 加载案例；结构错误立即抛 ValueError（附行号）。"""
    cases: List[IncidentCase] = []
    fields = set(IncidentCase.__dataclass_fields__)
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"第 {line_no} 行 JSON 解析失败: {exc}") from exc
        if not isinstance(data, dict) or not data.get("id"):
            raise ValueError(f"第 {line_no} 行缺少 id 字段")
        unknown = set(data) - fields
        if unknown:
            raise ValueError(f"第 {line_no} 行存在未知字段: {sorted(unknown)}")
        cases.append(IncidentCase(**data))
    return cases


def validate_incident_cases(
    cases: List[IncidentCase], min_cases: int = 30
) -> Tuple[List[str], List[str], Dict[str, Any]]:
    """校验案例集；返回 (errors, warnings, stats)。"""
    errors: List[str] = []
    warnings: List[str] = []
    if len(cases) < min_cases:
        errors.append(f"案例总数 {len(cases)} < 要求 {min_cases}")

    seen_ids: set = set()
    split_counts: Dict[str, int] = {}
    status_counts: Dict[str, int] = {"ok": 0, "uncertain": 0}
    category_counts: Dict[str, int] = {}
    ok_with_two_signals = 0
    for case in cases:
        where = case.id or "(无 id)"
        if case.id in seen_ids:
            errors.append(f"[{where}] id 重复")
        seen_ids.add(case.id)
        if not case.title.strip():
            errors.append(f"[{where}] title 为空")
        if case.split not in VALID_SPLITS:
            errors.append(f"[{where}] split 非法: {case.split!r}")
        if case.expected_status not in VALID_STATUSES:
            errors.append(f"[{where}] expected_status 非法: {case.expected_status!r}")
        if not (case.log_text.strip() or case.inspection_results or case.alerts):
            errors.append(f"[{where}] 输入为空（log_text / inspection_results / alerts 至少一项）")

        haystack = case.haystack()
        for keyword in case.expected_keywords:
            if keyword and keyword not in haystack:
                errors.append(f"[{where}] 锚点未出现在输入中: {keyword!r}")

        if case.expected_status == "ok":
            if case.expected_cause_id not in CAUSE_CATALOG:
                errors.append(f"[{where}] expected_cause_id 不在候选目录: {case.expected_cause_id!r}")
            if not case.expected_keywords:
                errors.append(f"[{where}] ok 案例必须给出 expected_keywords 锚点")
            evidence = case.signal_evidence_count()
            if evidence >= 2:
                ok_with_two_signals += 1
            else:
                warnings.append(
                    f"[{where}] 规则信号证据仅 {evidence} 条（<2），需知识库引用补足才能给出确定根因"
                )
        else:
            if case.expected_cause_id:
                errors.append(f"[{where}] uncertain 案例不应指定 expected_cause_id")

        split_counts[case.split] = split_counts.get(case.split, 0) + 1
        if case.expected_status in status_counts:
            status_counts[case.expected_status] += 1
        category_counts[case.category] = category_counts.get(case.category, 0) + 1

    stats: Dict[str, Any] = {
        "total": len(cases),
        "by_split": split_counts,
        "by_status": status_counts,
        "by_category": dict(sorted(category_counts.items())),
        "ok_cases_with_two_signals": ok_with_two_signals,
    }
    return errors, warnings, stats
