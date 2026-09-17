"""CLI: 阈值校准（第 3 周）—— dev 网格搜索 → 推荐值 → 落盘曲线。

思路: 阈值门只影响「拒答与否」（命中集合不随阈值变化），因此一次捕获
每题（dense_top, bm25_top, 候选命中）后可在内存中扫完整个网格，无需重复计算。

用法（env_new 环境，工作树根目录）:
    env -u PYTHONPATH env_new/Scripts/python.exe scripts/calibrate_thresholds.py
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evals.dataset import load_corpus  # noqa: E402
from evals.metrics import aggregate, evaluate_item  # noqa: E402
from evals.pipelines import HybridPipeline, load_retrieval_config  # noqa: E402
from evals.runner import load_eval_items  # noqa: E402
from utils.retrieval import apply_evidence_gate  # noqa: E402

DENSE_GRID = [round(0.10 + 0.02 * i, 2) for i in range(26)]  # 0.10 ~ 0.60
BM25_GRID = [round(0.05 + 0.05 * i, 2) for i in range(16)]  # 0.05 ~ 0.80
REFUSAL_FLOOR = 0.85


def capture_channel_data(pipeline: HybridPipeline, items: Sequence[Any], k: int = 5) -> List[Dict[str, Any]]:
    """每题捕获一次: (dense_top, bm25_top, 候选命中[拒答前])。"""
    captured: List[Dict[str, Any]] = []
    for warm in items[:2]:
        pipeline.retrieve(warm.question, k=k)
    for item in items:
        result = pipeline.retriever.retrieve(item.question, top_k=k)  # type: ignore[union-attr]
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


def metrics_for(captured: Sequence[Dict[str, Any]], min_dense: float, min_bm25: float, k: int = 5) -> Dict[str, Any]:
    """在给定阈值下模拟拒绝并聚合指标。"""
    outcomes = []
    for row in captured:
        item = row["item"]
        sufficient = bool(row["hits"]) and apply_evidence_gate(
            row["dense_top"], row["bm25_top"], min_dense, min_bm25
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
    return aggregate(outcomes, k)


def _objective(metrics: Dict[str, Any]) -> Tuple[float, float, float]:
    """排序键：先满足拒答下限，再最大化 Recall@5，再压低误拒率。"""
    refusal = metrics["refusal_rate_unanswerable"]
    feasible = 1.0 if refusal >= REFUSAL_FLOOR else 0.0
    return (feasible, metrics["recall_at_k"], -metrics["false_refusal_rate"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="阈值校准（dev 网格）")
    parser.add_argument("--dataset", type=Path, default=PROJECT_ROOT / "evals" / "dataset" / "qa_v1.jsonl")
    parser.add_argument("--corpus", type=Path, default=PROJECT_ROOT / "evals" / "corpus")
    parser.add_argument("--index-dir", type=Path, default=PROJECT_ROOT / ".eval-index-calib")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    items = load_eval_items(args.dataset, args.corpus, split="dev")
    load_corpus(args.corpus)  # 触发语料可读性校验
    if not items:
        print("dev 集为空，无法校准")
        return 1

    permissive = load_retrieval_config({"thresholds": {"min_dense_similarity": 0.0, "min_bm25_score": 0.0}})
    pipeline = HybridPipeline(args.index_dir, retrieval_config=permissive)
    pipeline.build_index(args.corpus)
    captured = capture_channel_data(pipeline, items)

    grid: List[Dict[str, Any]] = []
    best: Optional[Tuple[Tuple[float, float, float], float, float]] = None
    for min_dense in DENSE_GRID:
        for min_bm25 in BM25_GRID:
            metrics = metrics_for(captured, min_dense, min_bm25)
            key = _objective(metrics)
            grid.append(
                {
                    "min_dense_similarity": min_dense,
                    "min_bm25_score": min_bm25,
                    "recall_at_k": metrics["recall_at_k"],
                    "mrr": metrics["mrr"],
                    "ndcg_at_k": metrics["ndcg_at_k"],
                    "refusal_rate_unanswerable": metrics["refusal_rate_unanswerable"],
                    "false_refusal_rate": metrics["false_refusal_rate"],
                }
            )
            if best is None or key > best[0]:
                best = (key, min_dense, min_bm25)

    assert best is not None
    _, best_dense, best_bm25 = best
    best_metrics = metrics_for(captured, best_dense, best_bm25)
    feasible_cells = [g for g in grid if g["refusal_rate_unanswerable"] >= REFUSAL_FLOOR]
    feasible_cells.sort(key=lambda g: (-g["recall_at_k"], g["false_refusal_rate"]))

    print(f"dev 条目数: {len(items)}；网格 {len(DENSE_GRID)}×{len(BM25_GRID)}")
    print(f"推荐阈值: min_dense_similarity={best_dense}  min_bm25_score={best_bm25}")
    print(f"  满足拒答下限的网格点数: {len(feasible_cells)}")
    for row in feasible_cells[:8]:
        print(
            f"  dense>={row['min_dense_similarity']:.2f} bm25>={row['min_bm25_score']:.2f} "
            f"| recall={row['recall_at_k']:.3f} mrr={row['mrr']:.3f} "
            f"拒答={row['refusal_rate_unanswerable']:.2f} 误拒={row['false_refusal_rate']:.2f}"
        )
    print(
        "推荐点指标: "
        f"recall@{best_metrics['k'] if 'k' in best_metrics else 5}={best_metrics['recall_at_k']:.3f} "
        f"mrr={best_metrics['mrr']:.3f} ndcg={best_metrics['ndcg_at_k']:.3f} "
        f"拒答={best_metrics['refusal_rate_unanswerable']:.3f} 误拒={best_metrics['false_refusal_rate']:.3f}"
    )

    out_path = args.out or (PROJECT_ROOT / "evals" / "results" / "calibration-dev.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "dev_items": len(items),
        "refusal_floor": REFUSAL_FLOOR,
        "recommended": {"min_dense_similarity": best_dense, "min_bm25_score": best_bm25},
        "recommended_metrics": best_metrics,
        "grid": grid,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"校准记录已写入: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
