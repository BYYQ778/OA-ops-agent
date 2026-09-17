"""检索与回答质量指标（第 3 周评测体系 · 纯函数、离线可用）。

口径（与 docs/reports/ 评测报告一致）:
- 文档级 Recall@k / MRR / NDCG@k：把 Top-k 命中按名次折叠为唯一文档序列后与 gold_docs 比对；
- 引用覆盖率：可回答问题中「未被拒答」者，其回答上下文含 ≥1 条 gold 文档引用的比例；
- 引用准确率（span 级）：同上分母，Top-k 命中文本中命中 gold 锚点的比例（逐条二值再平均）；
- 无证据拒答率：无证据条目中引擎拒答的比例；误拒率：可回答问题被拒答的比例；
- 延迟：mean / p50 / p95（最近秩法）/ max（毫秒）。

被拒答的可回答条目在 Recall/MRR/NDCG 中计为完全未命中（空结果），
其影响单独由「误拒率」与「引用覆盖率分母（剔除拒答）」呈现，避免重复惩罚。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from evals.dataset import normalize_ws


@dataclass
class ItemOutcome:
    """单条评测的结局：检索折叠结果 + 引用命中 + 拒答 + 延迟。"""

    qid: str
    category: str
    answerable: bool
    refused: bool
    gold_docs: List[str] = field(default_factory=list)
    retrieved_docs: List[str] = field(default_factory=list)
    snippet_hits: int = 0
    snippet_total: int = 0
    latency_ms: float = 0.0


def dedupe_docs_in_order(doc_ids: Sequence[str]) -> List[str]:
    """按出现顺序折叠重复文档（首个名次即文档的最终名次）。"""
    seen: set[str] = set()
    out: List[str] = []
    for doc in doc_ids:
        if doc not in seen:
            seen.add(doc)
            out.append(doc)
    return out


def recall_at_k(retrieved_docs: Sequence[str], gold_docs: Sequence[str], k: int = 5) -> float:
    """文档级 Recall@k（单答案集为二值：gold 任一命中即 1.0）。"""
    if not gold_docs:
        return 0.0
    top = list(retrieved_docs)[: max(int(k), 0)]
    return 1.0 if any(doc in gold_docs for doc in top) else 0.0


def reciprocal_rank(retrieved_docs: Sequence[str], gold_docs: Sequence[str]) -> float:
    """MRR 单项：首个 gold 文档名次的倒数；未命中为 0。"""
    for rank, doc in enumerate(retrieved_docs, start=1):
        if doc in gold_docs:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(retrieved_docs: Sequence[str], gold_docs: Sequence[str], k: int = 5) -> float:
    """NDCG@k（二值相关性）：DCG = Σ rel_i / log2(i+1)；IDCG 取理想排序。"""
    if not gold_docs:
        return 0.0
    top = list(retrieved_docs)[: max(int(k), 0)]
    dcg = sum(1.0 / math.log2(i + 1) for i, doc in enumerate(top, start=1) if doc in gold_docs)
    ideal_hits = min(len(set(gold_docs)), max(int(k), 0))
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_hits + 1))
    if idcg <= 0:
        return 0.0
    return dcg / idcg


def snippet_hit(texts: Sequence[str], snippets: Sequence[str]) -> bool:
    """任一锚点（空白折叠后）出现在任一段检索文本中即命中。"""
    joined = normalize_ws("\n".join(texts))
    return any(normalize_ws(s) in joined for s in snippets if normalize_ws(s))


def count_snippet_hits(texts: Sequence[str], snippets: Sequence[str]) -> int:
    """命中锚点数（逐锚点判定）。"""
    joined = normalize_ws("\n".join(texts))
    return sum(1 for s in snippets if normalize_ws(s) in joined)


def evaluate_item(
    *,
    qid: str,
    category: str,
    answerable: bool,
    gold_docs: Sequence[str],
    gold_snippets: Sequence[str],
    retrieved: Sequence[Tuple[str, str]],
    refused: bool,
    latency_ms: float,
) -> ItemOutcome:
    """把一次管线输出整理为 ItemOutcome。retrieved 为 (文档名, 命中文本) 有序序列。"""
    return ItemOutcome(
        qid=qid,
        category=category,
        answerable=answerable,
        refused=refused,
        gold_docs=list(gold_docs),
        retrieved_docs=dedupe_docs_in_order([doc for doc, _ in retrieved]),
        snippet_hits=count_snippet_hits([text for _, text in retrieved], gold_snippets),
        snippet_total=len(gold_snippets),
        latency_ms=float(latency_ms),
    )


def latency_stats(samples_ms: Sequence[float]) -> Dict[str, Any]:
    """延迟统计：mean / p50 / p95（最近秩法）/ max；空序列返回全 0。"""
    xs = sorted(float(x) for x in samples_ms)
    if not xs:
        return {"count": 0, "mean": 0.0, "p50": 0.0, "p95": 0.0, "max": 0.0}

    def _pct(p: float) -> float:
        idx = max(0, min(len(xs) - 1, math.ceil(p * len(xs)) - 1))
        return xs[idx]

    return {
        "count": len(xs),
        "mean": sum(xs) / len(xs),
        "p50": _pct(0.50),
        "p95": _pct(0.95),
        "max": xs[-1],
    }


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _aggregate_subset(outcomes: Sequence[ItemOutcome], k: int) -> Dict[str, Any]:
    answerable = [o for o in outcomes if o.answerable]
    unanswerable = [o for o in outcomes if not o.answerable]
    answered = [o for o in answerable if not o.refused]
    return {
        "counts": {
            "total": len(outcomes),
            "answerable": len(answerable),
            "unanswerable": len(unanswerable),
            "answered": len(answered),
        },
        "recall_at_k": _mean([recall_at_k(o.retrieved_docs, o.gold_docs, k) for o in answerable]),
        "mrr": _mean([reciprocal_rank(o.retrieved_docs, o.gold_docs) for o in answerable]),
        "ndcg_at_k": _mean([ndcg_at_k(o.retrieved_docs, o.gold_docs, k) for o in answerable]),
        "citation_coverage": _mean(
            [
                1.0 if any(doc in o.gold_docs for doc in o.retrieved_docs) else 0.0
                for o in answered
            ]
        ),
        "citation_span_accuracy": _mean([1.0 if o.snippet_hits > 0 else 0.0 for o in answered]),
        "false_refusal_rate": (
            (len(answerable) - len(answered)) / len(answerable) if answerable else 0.0
        ),
        "refusal_rate_unanswerable": _mean([1.0 if o.refused else 0.0 for o in unanswerable]),
        "latency_ms": latency_stats([o.latency_ms for o in outcomes]),
    }


def aggregate(outcomes: Sequence[ItemOutcome], k: int = 5) -> Dict[str, Any]:
    """汇总全部指标；含总体与按类别细分（键与总体一致）。"""
    overall = _aggregate_subset(outcomes, k)
    overall["k"] = int(k)
    by_category: Dict[str, Dict[str, Any]] = {}
    for category in sorted({o.category for o in outcomes}):
        subset = [o for o in outcomes if o.category == category]
        by_category[category] = _aggregate_subset(subset, k)
    overall["by_category"] = by_category
    return overall


def compare_reports(
    old: Dict[str, Any], new: Dict[str, Any], *, k: int = 5
) -> Dict[str, Any]:
    """新旧对比摘要（用于报告标题区与图表数据源）。"""
    metrics = ("recall_at_k", "mrr", "ndcg_at_k", "citation_coverage", "citation_span_accuracy")
    deltas: Dict[str, Dict[str, Optional[float]]] = {}
    for name in metrics:
        ov = old.get(name)
        nv = new.get(name)
        deltas[name] = {
            "old": ov,
            "new": nv,
            "delta": (nv - ov) if (isinstance(ov, (int, float)) and isinstance(nv, (int, float))) else None,
        }
    return {
        "k": int(k),
        "metrics": deltas,
        "old_refusal_rate_unanswerable": old.get("refusal_rate_unanswerable"),
        "new_refusal_rate_unanswerable": new.get("refusal_rate_unanswerable"),
        "old_false_refusal_rate": old.get("false_refusal_rate"),
        "new_false_refusal_rate": new.get("false_refusal_rate"),
    }
