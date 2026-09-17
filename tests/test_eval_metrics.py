"""指标模块测试（完全离线）：Recall/MRR/NDCG、引用、拒答、延迟、聚合与对比。"""

import math

import pytest

from evals.metrics import (
    ItemOutcome,
    aggregate,
    compare_reports,
    count_snippet_hits,
    dedupe_docs_in_order,
    evaluate_item,
    latency_stats,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank,
    snippet_hit,
)


def test_dedupe_preserves_first_order() -> None:
    assert dedupe_docs_in_order(["b", "a", "b", "c", "a"]) == ["b", "a", "c"]


def test_recall_at_k_cutoff() -> None:
    docs = ["x", "y", "gold", "z"]
    assert recall_at_k(docs, ["gold"], k=3) == 1.0
    assert recall_at_k(docs, ["gold"], k=2) == 0.0
    assert recall_at_k(docs, ["missing"], k=4) == 0.0
    assert recall_at_k(docs, [], k=4) == 0.0


def test_reciprocal_rank_first_gold() -> None:
    assert reciprocal_rank(["x", "gold", "gold2"], ["gold", "gold2"]) == pytest.approx(0.5)
    assert reciprocal_rank(["x", "y"], ["gold"]) == 0.0


def test_ndcg_perfect_is_one() -> None:
    assert ndcg_at_k(["gold", "x"], ["gold"], k=2) == pytest.approx(1.0)


def test_ndcg_gold_at_second_rank() -> None:
    value = ndcg_at_k(["x", "gold"], ["gold"], k=5)
    assert value == pytest.approx(1.0 / math.log2(3))


def test_ndcg_two_gold_docs() -> None:
    value = ndcg_at_k(["x", "gold1", "gold2"], ["gold1", "gold2"], k=3)
    dcg = 1.0 / math.log2(3) + 1.0 / math.log2(4)
    idcg = 1.0 + 1.0 / math.log2(3)
    assert value == pytest.approx(dcg / idcg)


def test_snippet_hit_folds_whitespace() -> None:
    assert snippet_hit(["查看\n日志内容如下"], ["查看 日志"])
    assert not snippet_hit(["无关内容"], ["查看 日志"])


def test_count_snippet_hits() -> None:
    assert count_snippet_hits(["甲锚点在这里", "别的文本"], ["甲锚点", "乙锚点", "丙"]) == 1


def test_evaluate_item_builds_outcome() -> None:
    outcome = evaluate_item(
        qid="q001",
        category="mysql",
        answerable=True,
        gold_docs=["b.md"],
        gold_snippets=["锚点"],
        retrieved=[("a", "没有"), ("a", "也没有"), ("b", "这里有锚点")],
        refused=False,
        latency_ms=12.5,
    )
    assert outcome.retrieved_docs == ["a", "b"]
    assert outcome.snippet_hits == 1
    assert outcome.snippet_total == 1


def _outcome(
    qid: str = "q1",
    category: str = "mysql",
    answerable: bool = True,
    refused: bool = False,
    gold_docs: list[str] | None = None,
    retrieved_docs: list[str] | None = None,
    snippet_hits: int = 1,
    snippet_total: int = 1,
    latency_ms: float = 10.0,
) -> ItemOutcome:
    """构造 ItemOutcome，未指定字段用一组「命中」默认值。"""
    return ItemOutcome(
        qid=qid,
        category=category,
        answerable=answerable,
        refused=refused,
        gold_docs=list(gold_docs) if gold_docs is not None else ["g.md"],
        retrieved_docs=list(retrieved_docs) if retrieved_docs is not None else ["g.md", "x.md"],
        snippet_hits=snippet_hits,
        snippet_total=snippet_total,
        latency_ms=latency_ms,
    )


def test_aggregate_counts_and_rates() -> None:
    outcomes = [
        _outcome(qid="a1"),
        _outcome(qid="a2", refused=True, retrieved_docs=[], snippet_hits=0),
        _outcome(qid="u1", answerable=False, refused=True, gold_docs=[], snippet_hits=0, snippet_total=0),
        _outcome(qid="u2", answerable=False, refused=False, gold_docs=[], snippet_hits=0, snippet_total=0),
    ]
    report = aggregate(outcomes, k=5)
    assert report["counts"] == {"total": 4, "answerable": 2, "unanswerable": 2, "answered": 1}
    assert report["recall_at_k"] == pytest.approx(0.5)
    assert report["false_refusal_rate"] == pytest.approx(0.5)
    assert report["refusal_rate_unanswerable"] == pytest.approx(0.5)
    assert report["citation_coverage"] == pytest.approx(1.0)
    assert report["citation_span_accuracy"] == pytest.approx(1.0)
    assert report["latency_ms"]["count"] == 4


def test_aggregate_keeps_refused_out_of_coverage_denominator() -> None:
    outcomes = [
        _outcome(qid="a1"),
        _outcome(qid="a2", refused=True, retrieved_docs=[], snippet_hits=0),
    ]
    report = aggregate(outcomes)
    assert report["citation_coverage"] == pytest.approx(1.0)
    assert report["recall_at_k"] == pytest.approx(0.5)


def test_aggregate_by_category() -> None:
    outcomes = [
        _outcome(qid="m1", category="mysql"),
        _outcome(qid="r1", category="redis", retrieved_docs=["x.md"], snippet_hits=0),
    ]
    report = aggregate(outcomes)
    assert set(report["by_category"]) == {"mysql", "redis"}
    assert report["by_category"]["mysql"]["recall_at_k"] == pytest.approx(1.0)
    assert report["by_category"]["redis"]["recall_at_k"] == pytest.approx(0.0)


def test_latency_stats_nearest_rank() -> None:
    stats = latency_stats([float(v) for v in range(1, 11)])
    assert stats["count"] == 10
    assert stats["p50"] == pytest.approx(5.0)
    assert stats["p95"] == pytest.approx(10.0)
    assert stats["max"] == pytest.approx(10.0)
    assert latency_stats([])["count"] == 0


def test_compare_reports_deltas() -> None:
    old = aggregate([_outcome(qid="a1", retrieved_docs=["x.md"], snippet_hits=0)])
    new = aggregate([_outcome(qid="a1")])
    diff = compare_reports(old, new)
    assert diff["metrics"]["recall_at_k"]["delta"] == pytest.approx(1.0)
    assert diff["k"] == 5
