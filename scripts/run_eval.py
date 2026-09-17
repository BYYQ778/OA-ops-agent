"""CLI: 全量离线 RAG 评测（第 3 周）。

用法（env_new 环境，工作树根目录）:
    env -u PYTHONPATH env_new/Scripts/python.exe scripts/run_eval.py --split all
    ... --split dev --min-dense 0.38 --min-bm25 0.35 --out evals/results/calib-dev.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evals.runner import run_evaluation, summary_lines  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="RAG 离线评测（旧版 vs 新版）")
    parser.add_argument("--dataset", type=Path, default=PROJECT_ROOT / "evals" / "dataset" / "qa_v1.jsonl")
    parser.add_argument("--corpus", type=Path, default=PROJECT_ROOT / "evals" / "corpus")
    parser.add_argument("--index-dir", type=Path, default=PROJECT_ROOT / ".eval-index")
    parser.add_argument("--split", choices=["all", "dev", "holdout"], default="all")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--min-dense", type=float, default=None, help="覆盖 min_dense_similarity（校准用）")
    parser.add_argument("--min-bm25", type=float, default=None, help="覆盖 min_bm25_score（校准用）")
    parser.add_argument("--keep-index", action="store_true", help="复用已有索引（默认重建）")
    args = parser.parse_args(argv)

    overrides = {}
    thresholds = {}
    if args.min_dense is not None:
        thresholds["min_dense_similarity"] = float(args.min_dense)
    if args.min_bm25 is not None:
        thresholds["min_bm25_score"] = float(args.min_bm25)
    if thresholds:
        overrides["thresholds"] = thresholds

    result = run_evaluation(
        dataset_path=args.dataset,
        corpus_dir=args.corpus,
        index_dir=args.index_dir,
        split=args.split,
        k=args.k,
        out_path=args.out,
        retrieval_overrides=overrides or None,
        rebuild=not args.keep_index,
    )
    for line in summary_lines(result):
        print(line)
    print(f"\n结果已写入: {result['_out_path']}")
    if result["meta"].get("jieba_available") is False:
        print("[提示] 当前环境未安装 jieba，BM25 使用 bigram 降级分词（报告需注明）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
