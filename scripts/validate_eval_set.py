"""CLI: 校验评测集与语料一致性（第 3 周评测体系）。

用法:
    env -u PYTHONPATH python scripts/validate_eval_set.py
    env -u PYTHONPATH python scripts/validate_eval_set.py --dataset PATH --corpus DIR --no-strict

退出码: 0 = 通过（含仅警告）；1 = 存在错误。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:  # 脚本直跑时保证可导入 evals
    sys.path.insert(0, str(PROJECT_ROOT))

from evals.dataset import CATEGORIES, load_and_validate  # noqa: E402


def _print_stats(stats: dict) -> None:
    print("== 评测集统计 ==")
    print(f"总条数: {stats['total']}（可回答 {stats['answerable']} / 无证据 {stats['unanswerable']}）")
    print(f"划分: dev {stats['split_dev']} / holdout {stats['split_holdout']}；锚点总数 {stats['snippets_total']}")
    print(f"语料文档数: {stats['corpus_docs']}")
    print("按类别（可回答/无证据）:")
    for category in CATEGORIES:
        bucket = stats["by_category"].get(category, {"answerable": 0, "unanswerable": 0})
        print(f"  {category:<14} {bucket['answerable']:>3} / {bucket['unanswerable']:>3}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验评测集（结构 + 锚点 + 配额）")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=PROJECT_ROOT / "evals" / "dataset" / "qa_v1.jsonl",
    )
    parser.add_argument("--corpus", type=Path, default=PROJECT_ROOT / "evals" / "corpus")
    parser.add_argument("--no-strict", action="store_true", help="只查结构，跳过配额校验")
    args = parser.parse_args(argv)

    report = load_and_validate(args.dataset, args.corpus, strict_counts=not args.no_strict)
    _print_stats(report.stats)

    if report.warnings:
        print(f"\n== 警告（{len(report.warnings)}） ==")
        for warning in report.warnings:
            print(f"  [WARN] {warning}")
    if report.errors:
        print(f"\n== 错误（{len(report.errors)}） ==")
        for error in report.errors:
            print(f"  [ERR] {error}")
        print("\n结果: 未通过")
        return 1
    print("\n结果: 通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
