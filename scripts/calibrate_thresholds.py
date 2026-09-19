"""CLI: 阈值校准（第 3 周）—— dev 网格（分层证据门）→ 推荐值 → 落盘曲线。

分层门控 pass 条件（utils.retrieval.apply_evidence_gate）:
    单通道强证据：dense ≥ min_dense_similarity 或 bm25 ≥ min_bm25_score
    双通道互证： dense ≥ joint_dense_similarity 且 bm25 ≥ joint_bm25_score

一次捕获每题 (dense_top, bm25_top, 候选命中) 后在内存中扫完全部网格，
无需重复计算（阈值只影响「拒答与否」，命中集合不变）。

用法（env_new 环境，工作树根目录）:
    env -u PYTHONPATH env_new/Scripts/python.exe scripts/calibrate_thresholds.py
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evals.metrics import aggregate, evaluate_item  # noqa: E402
from evals.pipelines import HybridPipeline, load_retrieval_config  # noqa: E402
from evals.runner import load_eval_items  # noqa: E402
from utils.retrieval import apply_evidence_gate  # noqa: E402

# 细网格（推荐值搜索）
STRONG_DENSE_GRID = [round(0.84 + 0.01 * i, 2) for i in range(11)]   # 0.84 ~ 0.94
STRONG_BM25_GRID = [round(10.0 + 0.25 * i, 2) for i in range(11)]    # 10.0 ~ 12.5
JOINT_DENSE_GRID = [round(0.50 + 0.025 * i, 3) for i in range(9)]    # 0.500 ~ 0.700
JOINT_BM25_GRID = [round(7.0 + 0.25 * i, 2) for i in range(9)]       # 7.0 ~ 9.0

# 粗网格（图表/曲线留档）
COARSE_DENSE = [round(0.84 + 0.02 * i, 2) for i in range(6)]
COARSE_BM25 = [round(10.0 + 0.5 * i, 2) for i in range(6)]
COARSE_JOINT_DENSE = [round(0.50 + 0.05 * i, 2) for i in range(5)]
COARSE_JOINT_BM25 = [round(7.0 + 0.5 * i, 2) for i in range(5)]

FALSE_REFUSAL_BUDGET = 0.07  # dev 误拒率预算（平衡拒答能力与可用性）
K = 5


def capture_channel_data(pipeline: HybridPipeline, items: Sequence[Any]) -> List[Dict[str, Any]]:
    """每题捕获一次: (dense_top, bm25_top, 候选命中[拒答前])。"""
    captured: List[Dict[str, Any]] = []
    for warm in items[:2]:
        pipeline.retrieve(warm.question, k=K)
    for item in items:
        result = pipeline.retriever.retrieve(item.question, top_k=K)  # type: ignore[union-attr]
        hits = [(str(h.metadata.get("source", "未知")), h.text) for h in result.hits]
        captured.append(
            {
                "item": item,
                "dense_top": result.debug.get("dense_top_similarity"),
                "bm25_top": result.debug.get("bm25_top_score"),
                "hits": hits,
            }
        )
    return captured


def metrics_for(
    captured: Sequence[Dict[str, Any]],
    min_dense: float,
    min_bm25: float,
    joint_dense: Optional[float] = None,
    joint_bm25: Optional[float] = None,
) -> Dict[str, Any]:
    """在给定阈值下模拟拒绝并聚合指标。"""
    outcomes = []
    for row in captured:
        item = row["item"]
        sufficient = bool(row["hits"]) and apply_evidence_gate(
            row["dense_top"], row["bm25_top"], min_dense, min_bm25, joint_dense, joint_bm25
        )
        outcomes.append(
            evaluate_item(
                qid=item.id,
                category=item.category,
                answerable=item.answerable,
                gold_docs=item.gold_docs,
                gold_snippets=item.gold_snippets,
                retrieved=[] if not sufficient else row["hits"],
                refused=not sufficient,
                latency_ms=0.0,
            )
        )
    return aggregate(outcomes, K)


def _score(metrics: Dict[str, Any]) -> tuple:
    """排序键：先满足误拒预算，再最大化拒答率，再压低误拒率，再保召回。"""
    within_budget = 1.0 if metrics["false_refusal_rate"] <= FALSE_REFUSAL_BUDGET else 0.0
    return (
        within_budget,
        metrics["refusal_rate_unanswerable"],
        -metrics["false_refusal_rate"],
        metrics["recall_at_k"],
    )


def _row(cell_metrics: Dict[str, Any], a1: float, b1: float, a2: Optional[float], b2: Optional[float]) -> Dict[str, Any]:
    return {
        "min_dense_similarity": a1,
        "min_bm25_score": b1,
        "joint_dense_similarity": a2,
        "joint_bm25_score": b2,
        "recall_at_k": cell_metrics["recall_at_k"],
        "mrr": cell_metrics["mrr"],
        "ndcg_at_k": cell_metrics["ndcg_at_k"],
        "refusal_rate_unanswerable": cell_metrics["refusal_rate_unanswerable"],
        "false_refusal_rate": cell_metrics["false_refusal_rate"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="阈值校准（dev 网格，分层证据门）")
    parser.add_argument("--dataset", type=Path, default=PROJECT_ROOT / "evals" / "dataset" / "qa_v1.jsonl")
    parser.add_argument("--corpus", type=Path, default=PROJECT_ROOT / "evals" / "corpus")
    parser.add_argument("--index-dir", type=Path, default=PROJECT_ROOT / ".eval-index-calib")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    items = load_eval_items(args.dataset, args.corpus, split="dev")
    if not items:
        print("dev 集为空，无法校准")
        return 1

    permissive = load_retrieval_config({"thresholds": {"min_dense_similarity": 0.0, "min_bm25_score": 0.0}})
    pipeline = HybridPipeline(args.index_dir, retrieval_config=permissive)
    pipeline.build_index(args.corpus)
    captured = capture_channel_data(pipeline, items)

    # ---- 细网格：分层门控 ----
    best: Optional[tuple] = None
    best_cfg: Optional[tuple] = None
    for a1 in STRONG_DENSE_GRID:
        for b1 in STRONG_BM25_GRID:
            for a2 in JOINT_DENSE_GRID:
                for b2 in JOINT_BM25_GRID:
                    metrics = metrics_for(captured, a1, b1, a2, b2)
                    key = _score(metrics)
                    if best is None or key > best:
                        best, best_cfg = key, (a1, b1, a2, b2)
    assert best_cfg is not None
    a1, b1, a2, b2 = best_cfg
    recommended = metrics_for(captured, a1, b1, a2, b2)

    # ---- 对照：纯 OR 单层门控的参考最优 ----
    or_best: Optional[tuple] = None
    or_cfg: Optional[tuple] = None
    for a1o in [round(0.40 + 0.01 * i, 2) for i in range(56)]:
        for b1o in [round(4.0 + 0.1 * i, 1) for i in range(211)]:
            metrics = metrics_for(captured, a1o, b1o)
            key = _score(metrics)
            if or_best is None or key > or_best:
                or_best, or_cfg = key, (a1o, b1o)
    assert or_cfg is not None
    or_metrics = metrics_for(captured, or_cfg[0], or_cfg[1])

    # ---- 粗网格（曲线留档）----
    grid = []
    for ca in COARSE_DENSE:
        for cb in COARSE_BM25:
            for cja in COARSE_JOINT_DENSE:
                for cjb in COARSE_JOINT_BM25:
                    grid.append(_row(metrics_for(captured, ca, cb, cja, cjb), ca, cb, cja, cjb))
    grid.append(_row(recommended, a1, b1, a2, b2))

    print(f"dev 条目数: {len(items)}（可回答 {sum(1 for i in items if i.answerable)} / 无证据 {sum(1 for i in items if not i.answerable)}）")
    print(f"推荐（分层门控）: dense>={a1} | bm25>={b1} | 双通道 dense>={a2} 且 bm25>={b2}")
    print(
        f"  拒答率(无证据)={recommended['refusal_rate_unanswerable']:.1%} "
        f"误拒率={recommended['false_refusal_rate']:.1%} "
        f"recall@5={recommended['recall_at_k']:.3f} mrr={recommended['mrr']:.3f} ndcg={recommended['ndcg_at_k']:.3f}"
    )
    print(
        f"对照（纯单层 OR）: dense>={or_cfg[0]} bm25>={or_cfg[1]} → "
        f"拒答率={or_metrics['refusal_rate_unanswerable']:.1%} 误拒率={or_metrics['false_refusal_rate']:.1%}"
    )

    out_path = args.out or (PROJECT_ROOT / "evals" / "results" / "calibration-dev.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "dev_items": len(items),
        "false_refusal_budget": FALSE_REFUSAL_BUDGET,
        "mode": "joint",
        "recommended": {
            "min_dense_similarity": a1,
            "min_bm25_score": b1,
            "joint_dense_similarity": a2,
            "joint_bm25_score": b2,
        },
        "recommended_metrics": recommended,
        "or_only_reference": {
            "min_dense_similarity": or_cfg[0],
            "min_bm25_score": or_cfg[1],
            "metrics": {
                "refusal_rate_unanswerable": or_metrics["refusal_rate_unanswerable"],
                "false_refusal_rate": or_metrics["false_refusal_rate"],
                "recall_at_k": or_metrics["recall_at_k"],
            },
        },
        "grid": grid,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"校准记录已写入: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
