"""CLI: 第 4 周根因诊断标准案例实跑（离线可复现；复用第 3 周语料作为知识库）。

用法（需 RAG 依赖环境，CI core 不跑本脚本）:
    env -u PYTHONPATH <python> scripts/run_incident_eval.py
    env -u PYTHONPATH <python> scripts/run_incident_eval.py --split holdout

说明:
- 知识库检索使用「诊断宽松门」（召回优先，为报告提供引用与背书）；
  问答链的冻结阈值（config.yaml）不受影响。
- 结果 JSON 写入 evals/results/incidents-v1.json（含逐案例明细与元数据）。
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:  # 脚本直跑时保证可导入 evals/agents
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.incident_agent import analyze_incident  # noqa: E402
from agents.incident_kb import RetrievalKbAdapter  # noqa: E402
from evals.incident_cases import IncidentCase, load_incident_cases  # noqa: E402
from evals.incident_runner import run_incident_eval  # noqa: E402
from utils.retrieval import RetrievalConfig  # noqa: E402


def _env_meta() -> dict:
    meta: dict = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "cpu_count": __import__("os").cpu_count(),
    }
    for name in ("sentence_transformers", "chromadb", "jieba"):
        try:
            module = __import__(name)
            meta[name] = getattr(module, "__version__", "unknown")
        except Exception:  # noqa: BLE001 - 元数据缺省不影响评测
            meta[name] = None
    return meta


def _print_summary(summary: dict) -> None:
    counts = summary["counts"]
    top1 = summary["top1"]
    uncertain = summary["uncertain"]
    print("=" * 60)
    print(f"案例: {counts['total']}（ok {counts['ok_cases']} / uncertain {counts['uncertain_cases']}）")
    if top1["accuracy"] is not None:
        print(f"Top-1 根因准确率: {top1['correct']}/{top1['total']} = {top1['accuracy']:.1%}（目标 ≥80%）")
    else:
        print("Top-1 根因准确率: 无 ok 案例")
    if uncertain["accuracy"] is not None:
        print(f"不确定判定正确率: {uncertain['correct']}/{uncertain['total']} = {uncertain['accuracy']:.1%}")
    print(f"报告完整性违规: {summary['report_violations']}（应为 0）")
    print(f"平均信号数: {summary['avg_signals']} ｜ 平均单例耗时: {summary['avg_duration_ms']}ms")
    print("-- 分拆 --")
    for name, item in summary["by_split"].items():
        print(
            f"  {name}: top1 {item['top1_correct']}/{item['top1_total']}"
            f" ｜ uncertain {item['uncertain_correct']}/{item['uncertain_total']}"
        )
    print("-- 逐案例 --")
    for row in summary["cases"]:
        hit = row["top1_hit"] if row["expected_status"] == "ok" else row["uncertain_correct"]
        mark = "✓" if hit else "✗"
        expected = row["expected_cause_id"] or "uncertain"
        got = row["root_cause_id"] or row["status"]
        print(
            f"  {mark} {row['id']} [{row['split']}] 期望 {expected} → 实得 {got}"
            f"（证据 {row['evidence_count']}, KB {row['kb_assigned']}, {row['duration_ms']}ms）"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="第 4 周根因诊断标准案例实跑")
    parser.add_argument("--cases", type=Path, default=PROJECT_ROOT / "evals" / "cases" / "incidents_v1.jsonl")
    parser.add_argument("--corpus", type=Path, default=PROJECT_ROOT / "evals" / "corpus")
    parser.add_argument("--index-dir", type=Path, default=PROJECT_ROOT / ".eval-index-incidents")
    parser.add_argument("--out", type=Path, default=PROJECT_ROOT / "evals" / "results" / "incidents-v1.json")
    parser.add_argument("--split", default="all", choices=["all", "dev", "holdout"])
    parser.add_argument("--k", type=int, default=3, help="知识库检索条数")
    parser.add_argument("--min-dense", type=float, default=0.45, help="诊断宽松门 dense 阈值")
    parser.add_argument("--min-bm25", type=float, default=5.0, help="诊断宽松门 bm25 阈值")
    args = parser.parse_args(argv)

    from evals.pipelines import HybridPipeline

    config = RetrievalConfig(
        mode="lite",
        hybrid_enabled=True,
        final_top_k=args.k,
        min_dense_similarity=args.min_dense,
        min_bm25_score=args.min_bm25,
    )
    started = time.time()
    pipeline = HybridPipeline(args.index_dir, retrieval_config=config)
    chunk_count = pipeline.build_index(args.corpus)
    build_seconds = round(time.time() - started, 1)
    print(f"[索引] {chunk_count} 块，用时 {build_seconds}s（{args.index_dir}）")

    adapter = RetrievalKbAdapter(pipeline.retriever, top_k=args.k)
    cases = load_incident_cases(args.cases)

    def analyze_fn(case: IncidentCase) -> Any:
        return analyze_incident(
            log_text=case.log_text,
            inspection_results=case.inspection_results or None,
            alerts=case.alerts or None,
            service=case.service,
            host=case.host,
            retriever=adapter,
        )

    summary = run_incident_eval(cases, analyze_fn, split=args.split)
    summary["meta"] = {
        **_env_meta(),
        "cases_file": str(args.cases),
        "corpus_dir": str(args.corpus),
        "corpus_chunks": chunk_count,
        "index_build_s": build_seconds,
        "retrieval": {
            "mode": "diagnosis-relaxed",
            "min_dense_similarity": args.min_dense,
            "min_bm25_score": args.min_bm25,
            "top_k": args.k,
        },
        "split": args.split,
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    _print_summary(summary)
    print(f"\n结果已写入: {out_path}")
    top1 = summary["top1"]
    if top1["accuracy"] is not None and top1["accuracy"] >= 0.8:
        print("验收: Top-1 ≥80% ✔")
    else:
        print("验收: Top-1 未达 80%（如实记录，不做修饰）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
