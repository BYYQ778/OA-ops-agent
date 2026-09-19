"""CLI: 生成评测报告 + 图表（第 3 周）。

用法（env_new 环境，工作树根目录）:
    env -u PYTHONPATH env_new/Scripts/python.exe scripts/gen_report.py \
        --final evals/results/final-holdout.json \
        --calibration evals/results/calibration-dev.json \
        --initial evals/results/rag-eval-full-v1.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evals.report import write_report  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 RAG 评测报告与图表")
    parser.add_argument("--final", type=Path, required=True, help="终评结果 JSON（holdout，冻结阈值）")
    parser.add_argument("--calibration", type=Path, default=None, help="阈值校准 JSON（可选）")
    parser.add_argument("--initial", type=Path, default=None, help="初始阈值全量结果 JSON（可选附注）")
    parser.add_argument("--holdout", type=Path, default=None, help="holdout 冻结口径终评 JSON（可选）")
    parser.add_argument("--llm", type=Path, default=None, help="LLM 子集结果 JSON（可选）")
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "docs" / "reports" / "rag-eval-report.md",
    )
    args = parser.parse_args(argv)

    md_path, images = write_report(
        args.final,
        args.output,
        calibration_path=args.calibration,
        initial_path=args.initial,
        llm_path=args.llm,
    )
    print(f"报告已生成: {md_path}")
    for image in images:
        print(f"  图表: {image}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
