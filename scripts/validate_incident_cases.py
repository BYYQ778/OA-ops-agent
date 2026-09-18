"""CLI: 校验第 4 周根因诊断案例集（结构 + 锚点 + 信号证据 sanity + 配额）。

用法:
    env -u PYTHONPATH python scripts/validate_incident_cases.py
    env -u PYTHONPATH python scripts/validate_incident_cases.py --cases PATH --min-cases 30

退出码: 0 = 通过（含仅警告）；1 = 存在错误。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:  # 脚本直跑时保证可导入 evals
    sys.path.insert(0, str(PROJECT_ROOT))

from evals.incident_cases import load_incident_cases, validate_incident_cases  # noqa: E402


def _print_stats(stats: dict) -> None:
    by_status = stats["by_status"]
    print("== 案例集统计 ==")
    print(f"总条数: {stats['total']}（ok {by_status['ok']} / uncertain {by_status['uncertain']}）")
    print(f"划分: {stats['by_split']}")
    print(f"含 ≥2 规则信号证据的 ok 案例: {stats['ok_cases_with_two_signals']}")
    print("按类别:")
    for category, count in stats["by_category"].items():
        print(f"  {category:<12} {count}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验根因诊断案例集（结构 + 锚点 + 配额）")
    parser.add_argument(
        "--cases",
        type=Path,
        default=PROJECT_ROOT / "evals" / "cases" / "incidents_v1.jsonl",
    )
    parser.add_argument("--min-cases", type=int, default=30, help="最少案例条数（验收线）")
    args = parser.parse_args(argv)

    cases = load_incident_cases(args.cases)
    errors, warnings, stats = validate_incident_cases(cases, min_cases=args.min_cases)

    _print_stats(stats)
    if warnings:
        print("\n== 警告 ==")
        for item in warnings:
            print(f"  - {item}")
    if errors:
        print("\n== 错误 ==")
        for item in errors:
            print(f"  - {item}")
        print(f"\n结果: 不通过（errors={len(errors)}, warnings={len(warnings)}）")
        return 1
    print(f"\n结果: 通过（errors=0, warnings={len(warnings)}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
