"""评测运行器（第 3 周）：编排双管线全量离线评测，产出 results JSON。

用法（env_new 环境）:
    env -u PYTHONPATH python scripts/run_eval.py --split all --out evals/results/xxx.json

流程: 数据集校验 → 建双索引（legacy/hybrid）→ 预热 → 逐题双管线检索 →
      指标聚合（evals.metrics）→ 新旧对比 → 落盘 JSON（含环境元数据）。
"""

from __future__ import annotations

import importlib.metadata as importlib_metadata
import importlib.util
import json
import os
import platform
import shutil
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from evals.dataset import QAItem, load_corpus, load_jsonl, validate
from evals.metrics import ItemOutcome, aggregate, compare_reports, evaluate_item
from evals.pipelines import (
    EMBEDDING_MODEL_NAME,
    HybridPipeline,
    LegacyPipeline,
    load_retrieval_config,
)

K_DEFAULT = 5
WARMUP = 2
SNIPPET_CHARS = 160


def _package_versions() -> Dict[str, Optional[str]]:
    versions: Dict[str, Optional[str]] = {}
    for name in (
        "sentence-transformers",
        "chromadb",
        "langchain",
        "langchain-huggingface",
        "langchain-chroma",
        "jieba",
        "numpy",
    ):
        try:
            versions[name] = importlib_metadata.version(name)
        except importlib_metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def _env_metadata() -> Dict[str, Any]:
    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "processor": platform.processor() or platform.machine(),
        "cpu_count": os.cpu_count(),
        "embedding_model": EMBEDDING_MODEL_NAME,
        "jieba_available": importlib.util.find_spec("jieba") is not None,
        "hf_hub_offline": os.environ.get("HF_HUB_OFFLINE"),
        "packages": _package_versions(),
    }


def load_eval_items(dataset_path: Path, corpus_dir: Path, split: str = "all") -> List[QAItem]:
    """加载 + 校验数据集，并按 split 过滤（all/dev/holdout）。"""
    rows = load_jsonl(dataset_path)
    corpus = load_corpus(corpus_dir)
    report = validate(rows, corpus, strict_counts=True)
    if report.errors:
        raise ValueError("数据集校验未通过: " + "; ".join(report.errors[:5]))
    if split == "all":
        return list(report.items)
    return [item for item in report.items if item.split == split]


def _trim(hits: Sequence[Tuple[str, str]]) -> List[Dict[str, str]]:
    return [
        {"doc": doc, "snippet": (text or "")[:SNIPPET_CHARS]}
        for doc, text in hits
    ]


def run_pipeline(
    pipeline: Any,
    items: Sequence[QAItem],
    *,
    k: int = K_DEFAULT,
    warmup: int = WARMUP,
) -> Dict[str, Any]:
    """逐题检索并整理为 outcomes + 原始命中（供落盘回溯）。"""
    for item in items[: max(int(warmup), 0)]:  # 预热（BM25 建索引/模型加载），不计入延迟
        pipeline.retrieve(item.question, k=k)

    outcomes: List[ItemOutcome] = []
    hits_dump: List[Dict[str, Any]] = []
    for item in items:
        started = time.perf_counter()
        hits, refused = pipeline.retrieve(item.question, k=k)
        latency_ms = (time.perf_counter() - started) * 1000.0
        outcomes.append(
            evaluate_item(
                qid=item.id,
                category=item.category,
                answerable=item.answerable,
                gold_docs=item.gold_docs,
                gold_snippets=item.gold_snippets,
                retrieved=hits,
                refused=refused,
                latency_ms=latency_ms,
            )
        )
        hits_dump.append({"qid": item.id, "refused": refused, "hits": _trim(hits)})
    return {"outcomes": outcomes, "hits": hits_dump}


def run_evaluation(
    *,
    dataset_path: Path,
    corpus_dir: Path,
    index_dir: Path,
    split: str = "all",
    k: int = K_DEFAULT,
    out_path: Optional[Path] = None,
    retrieval_overrides: Optional[Mapping[str, Any]] = None,
    rebuild: bool = True,
) -> Dict[str, Any]:
    """全量评测。返回结果 dict，并按 out_path 落盘（默认 evals/results/rag-eval-<ts>.json）。"""
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

    items = load_eval_items(dataset_path, corpus_dir, split)
    if not items:
        raise ValueError(f"split={split} 没有可用条目")

    if rebuild and Path(index_dir).exists():
        shutil.rmtree(index_dir, ignore_errors=True)

    legacy_pipeline = LegacyPipeline(Path(index_dir) / "legacy")
    hybrid_pipeline = HybridPipeline(
        Path(index_dir) / "hybrid",
        retrieval_config=load_retrieval_config(retrieval_overrides),
    )
    legacy_chunks = legacy_pipeline.build_index(corpus_dir)
    hybrid_chunks = hybrid_pipeline.build_index(corpus_dir)

    legacy_run = run_pipeline(legacy_pipeline, items, k=k)
    hybrid_run = run_pipeline(hybrid_pipeline, items, k=k)

    legacy_metrics = aggregate(legacy_run["outcomes"], k)
    hybrid_metrics = aggregate(hybrid_run["outcomes"], k)

    legacy_by_qid = {row["qid"]: row for row in legacy_run["hits"]}
    hybrid_by_qid = {row["qid"]: row for row in hybrid_run["hits"]}

    meta = _env_metadata()
    meta.update(
        {
            "split": split,
            "k": int(k),
            "items": len(items),
            "corpus_docs": len(load_corpus(corpus_dir)),
            "legacy_chunks": legacy_chunks,
            "hybrid_chunks": hybrid_chunks,
            "retrieval_config": asdict(hybrid_pipeline.retrieval_config),
        }
    )

    result: Dict[str, Any] = {
        "meta": meta,
        "legacy": legacy_metrics,
        "hybrid": hybrid_metrics,
        "comparison": compare_reports(legacy_metrics, hybrid_metrics, k=k),
        "per_item": [
            {
                "qid": item.id,
                "category": item.category,
                "split": item.split,
                "answerable": item.answerable,
                "gold_docs": list(item.gold_docs),
                "legacy": legacy_by_qid.get(item.id, {}),
                "hybrid": hybrid_by_qid.get(item.id, {}),
            }
            for item in items
        ],
    }

    if out_path is None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        out_path = Path(index_dir).parent / "evals" / "results" / f"rag-eval-{stamp}.json"
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    latest = out_path.parent / "latest.json"
    latest.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result["_out_path"] = str(out_path)
    return result


def summary_lines(result: Mapping[str, Any]) -> List[str]:
    """双版本对比摘要（CLI 打印用）。"""
    legacy = result["legacy"]
    hybrid = result["hybrid"]
    rows: List[Tuple[str, str, str, str]] = []

    def _fmt(value: Any, pct: bool = False) -> str:
        if value is None:
            return "-"
        return f"{value:.1%}" if pct else f"{value:.3f}"

    def _delta(new: Any, old: Any, pct: bool = False) -> str:
        if not isinstance(new, (int, float)) or not isinstance(old, (int, float)):
            return "-"
        diff = new - old
        return f"{diff * 100:+.1f}pp" if pct else f"{diff:+.3f}"

    pairs = [
        ("Recall@5", "recall_at_k", True),
        ("MRR", "mrr", False),
        ("NDCG@5", "ndcg_at_k", False),
        ("引用覆盖率", "citation_coverage", True),
        ("引用准确率(span)", "citation_span_accuracy", True),
        ("拒答率(无证据)", "refusal_rate_unanswerable", True),
        ("误拒率(可回答)", "false_refusal_rate", True),
    ]
    for label, key, pct in pairs:
        rows.append((label, _fmt(legacy.get(key), pct), _fmt(hybrid.get(key), pct), _delta(hybrid.get(key), legacy.get(key), pct)))

    lat_old = legacy.get("latency_ms", {}).get("mean", 0.0)
    lat_new = hybrid.get("latency_ms", {}).get("mean", 0.0)
    rows.append(("延迟 mean(ms)", f"{lat_old:.1f}", f"{lat_new:.1f}", f"{lat_new - lat_old:+.1f}"))
    p95_old = legacy.get("latency_ms", {}).get("p95", 0.0)
    p95_new = hybrid.get("latency_ms", {}).get("p95", 0.0)
    rows.append(("延迟 P95(ms)", f"{p95_old:.1f}", f"{p95_new:.1f}", f"{p95_new - p95_old:+.1f}"))

    meta = result["meta"]
    lines = [
        f"== RAG 评测（split={meta['split']}, k={meta['k']}, 条目={meta['items']}, "
        f"语料={meta['corpus_docs']} 篇/{meta['legacy_chunks']}↔{meta['hybrid_chunks']} 块）==",
    ]
    width = max(len(row[0]) for row in rows)
    lines.append(f"{'指标'.ljust(width)}  旧版(dense)   新版(hybrid)   Δ")
    for label, old, new, delta in rows:
        lines.append(f"{label.ljust(width)}  {old:>10}  {new:>12}  {delta:>8}")
    return lines
