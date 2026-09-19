"""根因诊断统一数据模型：IncidentEvent / Evidence / CandidateCause / DiagnosisReport。

设计原则:
- **证据纪律写进模型层**：status=ok ⇒ 根因存在、其证据链 ≥2 条、必须有处置建议；
  status=uncertain ⇒ 必须给出不确定原因，且不允许给出确定根因（不硬凑证据）。
- **只读诊断**：read_only 恒为 True，模型拒绝任何关闭只读的构造（本轮禁止自动执行修复）。
- **禁止凭空结论**：候选根因必须至少引用 1 条证据，且引用的证据必须真实存在于报告。
- 纯 pydantic + stdlib，离线可测；不 import 任何重依赖，供 rules/agent/API 共用。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator

#: 确定根因所需的最低证据条数：低于此数只能给出「不确定」报告（不强凑证据）。
MIN_EVIDENCE_FOR_ROOT_CAUSE: int = 2


class EventSource(str, Enum):
    """事件来源。"""

    log = "log"
    inspection = "inspection"
    alert = "alert"
    manual = "manual"


class Severity(str, Enum):
    """事件严重级别。"""

    info = "info"
    warning = "warning"
    error = "error"
    critical = "critical"


class EvidenceKind(str, Enum):
    """证据类型。"""

    log_pattern = "log_pattern"
    inspection_metric = "inspection_metric"
    kb_citation = "kb_citation"
    history_match = "history_match"
    correlation = "correlation"
    alert = "alert"


class ReportStatus(str, Enum):
    """报告状态：ok=有确定根因；uncertain=证据不足（如实标注，不强凑）。"""

    ok = "ok"
    uncertain = "uncertain"


def now_iso() -> str:
    """当前本地时间 ISO8601 字符串（秒级）。"""
    return datetime.now().isoformat(timespec="seconds")


def new_incident_id() -> str:
    """生成诊断编号：``INC-YYYYMMDDHHMMSS-xxxxxx``。"""
    return f"INC-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"


def _new_evidence_id() -> str:
    return f"EV-{uuid.uuid4().hex[:8]}"


def _new_event_id() -> str:
    return f"EVT-{uuid.uuid4().hex[:8]}"


def _ensure_iso(value: str) -> str:
    """校验 ISO8601 时间串，供各模型复用。"""
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"时间需为 ISO8601 字符串: {value!r}") from exc
    return value


class IncidentEvent(BaseModel):
    """标准化事件：巡检 / 日志 / 告警 / 手工输入统一后的最小单元。"""

    id: str = Field(default_factory=_new_event_id)
    timestamp: str = Field(default_factory=now_iso)
    source: EventSource
    service: str = ""
    host: str = ""
    metric: str = ""
    severity: Severity = Severity.warning
    raw: str = Field(min_length=1, description="原始证据原文（日志行、指标描述等）")
    tags: List[str] = Field(default_factory=list)

    @field_validator("timestamp")
    @classmethod
    def _validate_timestamp(cls, value: str) -> str:
        return _ensure_iso(value)


class Evidence(BaseModel):
    """单条证据：类型 / 内容 / 来源 / 时间 / 关联强度。"""

    id: str = Field(default_factory=_new_evidence_id)
    kind: EvidenceKind
    content: str = Field(min_length=1, description="证据原文（日志行 / 指标描述 / 文档摘录）")
    source: str = ""
    timestamp: Optional[str] = None
    strength: float = Field(default=0.5, ge=0.0, le=1.0)
    refs: List[str] = Field(default_factory=list, description="关联事件 id / 文档名等引用")

    @field_validator("timestamp")
    @classmethod
    def _validate_timestamp(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return _ensure_iso(value)


class CandidateCause(BaseModel):
    """候选根因：必须至少有一条证据背书（禁止仅凭模型常识输出根因）。"""

    cause_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    category: str = ""
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_ids: List[str] = Field(min_length=1, description="背书证据 id 列表（≥1）")
    rationale: str = ""


class DiagnosisReport(BaseModel):
    """只读诊断报告。

    纪律（构造即校验）:
    - status=ok：必须给出 root_cause、其证据 ≥2 条、且至少有 1 条处置建议；
    - status=uncertain：必须给出 uncertain_reasons，且不得给出 root_cause（可放 alternatives）；
    - 候选根因引用的证据 id 必须真实存在于 evidence 列表；证据/候选 id 不得重复。
    """

    incident_id: str = Field(default_factory=new_incident_id)
    created_at: str = Field(default_factory=now_iso)
    status: ReportStatus = ReportStatus.uncertain
    root_cause: Optional[CandidateCause] = None
    alternatives: List[CandidateCause] = Field(default_factory=list)
    evidence: List[Evidence] = Field(default_factory=list)
    events: List[IncidentEvent] = Field(default_factory=list)
    suggestions: List[str] = Field(default_factory=list)
    citations: List[str] = Field(default_factory=list)
    uncertain_reasons: List[str] = Field(default_factory=list)
    meta: Dict[str, Any] = Field(default_factory=dict)
    read_only: bool = True

    @computed_field  # type: ignore[prop-decorator]
    @property
    def confidence(self) -> float:
        """根因置信度（= root_cause.score；不确定时恒为 0）。"""
        return self.root_cause.score if self.root_cause is not None else 0.0

    @field_validator("created_at")
    @classmethod
    def _validate_created_at(cls, value: str) -> str:
        return _ensure_iso(value)

    @field_validator("read_only")
    @classmethod
    def _enforce_read_only(cls, value: bool) -> bool:
        if not value:
            raise ValueError("只读诊断：read_only 不允许关闭（本轮禁止自动执行修复）")
        return value

    @model_validator(mode="after")
    def _enforce_evidence_discipline(self) -> "DiagnosisReport":
        evidence_ids = [item.id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("证据 id 重复：evidence 列表内 id 必须唯一")
        known = set(evidence_ids)

        candidates: List[CandidateCause] = list(self.alternatives)
        if self.root_cause is not None:
            candidates.insert(0, self.root_cause)
        seen: set[str] = set()
        for cause in candidates:
            missing = [eid for eid in cause.evidence_ids if eid not in known]
            if missing:
                raise ValueError(f"候选根因 {cause.cause_id} 引用了不存在的证据: {missing}")
            if cause.cause_id in seen:
                raise ValueError(f"候选根因 id 重复: {cause.cause_id}")
            seen.add(cause.cause_id)

        if self.status == ReportStatus.ok:
            if self.root_cause is None:
                raise ValueError("status=ok 必须给出根因（root_cause）")
            if len(self.root_cause.evidence_ids) < MIN_EVIDENCE_FOR_ROOT_CAUSE:
                raise ValueError(
                    f"status=ok 的根因需 ≥{MIN_EVIDENCE_FOR_ROOT_CAUSE} 条证据背书"
                    "（证据不足请标 uncertain，不强凑）"
                )
            if self.uncertain_reasons:
                raise ValueError("status=ok 不应携带 uncertain_reasons")
            if not self.suggestions or any(not item.strip() for item in self.suggestions):
                raise ValueError("status=ok 必须给出至少一条处置建议")
        elif self.status == ReportStatus.uncertain:
            if self.root_cause is not None:
                raise ValueError("status=uncertain 不允许给出 root_cause（可放 alternatives）")
            if not self.uncertain_reasons:
                raise ValueError("status=uncertain 必须说明不确定原因（uncertain_reasons）")
        return self
