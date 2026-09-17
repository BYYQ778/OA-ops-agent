"""报告与图表生成（第 3 周）：从 results / calibration JSON 产出 Markdown 报告与 PNG 图表。

用法（env_new 环境）:
    env -u PYTHONPATH python scripts/gen_report.py --final evals/results/final-holdout.json

产物: docs/reports/rag-eval-report.md + docs/reports/assets/*.png
图表使用英文标注（避免 CJK 字体缺失导致乱码），正文为中文。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

METRIC_ROWS: Sequence[Tuple[str, str, bool]] = (
    ("Recall@5", "recall_at_k", True),
    ("MRR", "mrr", False),
    ("NDCG@5", "ndcg_at_k", False),
    ("引用覆盖率", "citation_coverage", True),
    ("引用准确率（span）", "citation_span_accuracy", True),
    ("无证据拒答率", "refusal_rate_unanswerable", True),
    ("误拒率（可回答）", "false_refusal_rate", True),
)

# 图表标签（英文，避免 CJK 字体缺失导致乱码）；与 METRIC_ROWS 一一对应
_CHART_LABELS: Sequence[str] = (
    "Recall@5",
    "MRR",
    "NDCG@5",
    "Citation\ncoverage",
    "Citation\nspan acc.",
    "Refusal\n(no-evidence)",
    "False\nrefusal",
)


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _fmt(value: Any, pct: bool = True) -> str:
    if value is None:
        return "-"
    return f"{value * 100:.1f}%" if pct else f"{value:.3f}"


def _delta(new: Any, old: Any, pct: bool = True) -> str:
    if not isinstance(new, (int, float)) or not isinstance(old, (int, float)):
        return "-"
    diff = new - old
    return f"{diff * 100:+.1f}pp" if pct else f"{diff:+.3f}"


# ---------------- 图表 ----------------

def _setup_mpl():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def chart_metric_compare(result: Mapping[str, Any], out_png: Path) -> Path:
    """旧版 vs 新版：核心指标分组柱状图。"""
    plt = _setup_mpl()
    legacy = result["legacy"]
    hybrid = result["hybrid"]
    labels = list(_CHART_LABELS)
    old_vals = [float(legacy.get(key) or 0.0) for _, key, _ in METRIC_ROWS]
    new_vals = [float(hybrid.get(key) or 0.0) for _, key, _ in METRIC_ROWS]

    x = range(len(labels))
    fig, ax = plt.subplots(figsize=(10, 4.8))
    width = 0.38
    ax.bar([i - width / 2 for i in x], old_vals, width, label="Legacy (dense-only)")
    ax.bar([i + width / 2 for i in x], new_vals, width, label="Hybrid (BM25+Dense+RRF)")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, rotation=18, ha="right")
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("score")
    ax.set_title("RAG metrics: legacy vs hybrid")
    ax.legend()
    fig.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=150)
    plt.close(fig)
    return out_png


def chart_category_recall(result: Mapping[str, Any], out_png: Path) -> Path:
    """分类别 Recall@5 对比。"""
    plt = _setup_mpl()
    legacy = result["legacy"]["by_category"]
    hybrid = result["hybrid"]["by_category"]
    cats = sorted(set(legacy) | set(hybrid))
    old_vals = [float(legacy.get(c, {}).get("recall_at_k") or 0.0) for c in cats]
    new_vals = [float(hybrid.get(c, {}).get("recall_at_k") or 0.0) for c in cats]

    x = range(len(cats))
    fig, ax = plt.subplots(figsize=(10, 4.8))
    width = 0.38
    ax.bar([i - width / 2 for i in x], old_vals, width, label="Legacy")
    ax.bar([i + width / 2 for i in x], new_vals, width, label="Hybrid")
    ax.set_xticks(list(x))
    ax.set_xticklabels(cats, rotation=30, ha="right")
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("Recall@5")
    ax.set_title("Recall@5 by category")
    ax.legend()
    fig.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=150)
    plt.close(fig)
    return out_png


def chart_latency(result: Mapping[str, Any], out_png: Path) -> Path:
    """检索延迟 mean / P95 对比（毫秒）。"""
    plt = _setup_mpl()
    legacy = result["legacy"]["latency_ms"]
    hybrid = result["hybrid"]["latency_ms"]
    labels = ["mean", "p95"]
    old_vals = [float(legacy.get(key) or 0.0) for key in ("mean", "p95")]
    new_vals = [float(hybrid.get(key) or 0.0) for key in ("mean", "p95")]

    x = range(len(labels))
    fig, ax = plt.subplots(figsize=(6, 4.2))
    width = 0.38
    ax.bar([i - width / 2 for i in x], old_vals, width, label="Legacy")
    ax.bar([i + width / 2 for i in x], new_vals, width, label="Hybrid")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels)
    ax.set_ylabel("ms")
    ax.set_title("Retrieval latency (retrieval only, no LLM)")
    ax.legend()
    fig.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=150)
    plt.close(fig)
    return out_png


def chart_threshold_sweep(calibration: Mapping[str, Any], out_png: Path) -> Path:
    """阈值扫描曲线：固定推荐的 bm25 阈值，横轴为 dense 阈值。"""
    plt = _setup_mpl()
    rec = calibration["recommended"]
    grid = list(calibration["grid"])

    def _nearest(key: str, target: Any) -> Any:
        vals = sorted({row[key] for row in grid if row.get(key) is not None})
        return min(vals, key=lambda v: abs(v - float(target))) if vals else None

    b1 = _nearest("min_bm25_score", rec["min_bm25_score"])
    a2 = _nearest("joint_dense_similarity", rec.get("joint_dense_similarity") or 0.6)
    b2 = _nearest("joint_bm25_score", rec.get("joint_bm25_score") or 8.0)
    rows = [
        row
        for row in grid
        if row["min_bm25_score"] == b1
        and row.get("joint_dense_similarity") == a2
        and row.get("joint_bm25_score") == b2
    ]
    rows.sort(key=lambda r: r["min_dense_similarity"])
    xs = [r["min_dense_similarity"] for r in rows]
    recall = [r["recall_at_k"] for r in rows]
    refusal = [r["refusal_rate_unanswerable"] for r in rows]

    fig, ax = plt.subplots(figsize=(7.5, 4.6))
    ax.plot(xs, recall, marker="o", ms=3, label="Recall@5 (answerable)")
    ax.plot(xs, refusal, marker="s", ms=3, label="Refusal rate (unanswerable)")
    ax.axhline(calibration.get("refusal_floor", 0.85), color="gray", ls="--", lw=1, label="Refusal floor")
    ax.axvline(rec["min_dense_similarity"], color="red", ls=":", lw=1.2, label="Frozen min_dense")
    ax.set_xlabel("min_dense_similarity (bm25 fixed at frozen value)")
    ax.set_ylabel("score")
    ax.set_ylim(0, 1.05)
    ax.set_title("Threshold sweep on dev set")
    ax.legend(fontsize=8)
    fig.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=150)
    plt.close(fig)
    return out_png


# ---------------- Markdown 报告 ----------------

def _metrics_table(result: Mapping[str, Any]) -> str:
    lines = ["| 指标 | 旧版（dense-only） | 新版（hybrid） | Δ |", "|---|---|---|---|"]
    legacy = result["legacy"]
    hybrid = result["hybrid"]
    for label, key, pct in METRIC_ROWS:
        lines.append(
            f"| {label} | {_fmt(legacy.get(key), pct)} | {_fmt(hybrid.get(key), pct)} | "
            f"{_delta(hybrid.get(key), legacy.get(key), pct)} |"
        )
    lo = legacy.get("latency_ms", {})
    ln = hybrid.get("latency_ms", {})
    lines.append(
        f"| 延迟 mean (ms) | {lo.get('mean', 0):.1f} | {ln.get('mean', 0):.1f} | "
        f"{ln.get('mean', 0) - lo.get('mean', 0):+.1f} |"
    )
    lines.append(
        f"| 延迟 P95 (ms) | {lo.get('p95', 0):.1f} | {ln.get('p95', 0):.1f} | "
        f"{ln.get('p95', 0) - lo.get('p95', 0):+.1f} |"
    )
    return "\n".join(lines)


def _category_table(result: Mapping[str, Any]) -> str:
    legacy = result["legacy"]["by_category"]
    hybrid = result["hybrid"]["by_category"]
    cats = sorted(set(legacy) | set(hybrid))
    lines = ["| 类别 | 旧版 Recall@5 | 新版 Recall@5 | Δ |", "|---|---|---|---|"]
    for cat in cats:
        old = legacy.get(cat, {}).get("recall_at_k")
        new = hybrid.get(cat, {}).get("recall_at_k")
        lines.append(f"| {cat} | {_fmt(old)} | {_fmt(new)} | {_delta(new, old)} |")
    return "\n".join(lines)


def render_report(
    result: Mapping[str, Any],
    calibration: Optional[Mapping[str, Any]] = None,
    initial: Optional[Mapping[str, Any]] = None,
    llm_subset: Optional[Mapping[str, Any]] = None,
    *,
    assets: Optional[Dict[str, str]] = None,
    holdout: Optional[Mapping[str, Any]] = None,
) -> str:
    meta = result["meta"]
    rc = meta.get("retrieval_config", {})
    assets = assets or {}

    def _img(key: str, alt: str) -> str:
        rel = assets.get(key)
        return f"![{alt}]({rel})\n" if rel else ""

    def _split_table(data: Mapping[str, Any]) -> str:
        """按 split 汇总的小表（holdout 冻结口径）。"""
        m = data["meta"]
        lines = [
            f"| split={m.get('split')}（{m.get('items')} 条，k={m.get('k')}） | 旧版 | 新版 | Δ |",
            "|---|---|---|---|",
        ]
        for label, key, pct in METRIC_ROWS:
            lines.append(
                f"| {label} | {_fmt(data['legacy'].get(key), pct)} | {_fmt(data['hybrid'].get(key), pct)} | "
                f"{_delta(data['hybrid'].get(key), data['legacy'].get(key), pct)} |"
            )
        return "\n".join(lines)

    parts: List[str] = []
    parts.append("# RAG 检索评测报告（第 3 周 · feat/rag-evaluation）\n")
    parts.append(
        "本报告由 `scripts/gen_report.py` 从评测结果 JSON 自动生成；"
        "评测对象为 OA 智能根因诊断平台的检索链升级（RAG 2.0：BM25 + Dense + RRF + 阈值拒答）。\n"
    )

    parts.append("## 一、评测设置\n")
    parts.append(
        f"- 数据：`{meta.get('corpus_docs')}` 篇合成运维文档（公开安全），"
        f"语义切块 {meta.get('hybrid_chunks')} 块 / 旧版固定切块 {meta.get('legacy_chunks')} 块；\n"
        f"- 评测集：{meta.get('items')} 条（split={meta.get('split')}），其中无证据负例按类别分布；\n"
        f"- 两版共用同一嵌入模型（{meta.get('embedding_model')}）与同一硬件（{meta.get('processor')}，"
        f"{meta.get('cpu_count')} 核，Python {meta.get('python')}）；\n"
        f"- 新版阈值：强证据 dense≥{rc.get('min_dense_similarity')} 或 bm25≥{rc.get('min_bm25_score')}；"
        f"互证 dense≥{rc.get('joint_dense_similarity')} 且 bm25≥{rc.get('joint_bm25_score')}"
        f"（dev 校准后冻结）；`rerank: {rc.get('rerank')}`（重排序模型未下载，未纳入本轮）；\n"
        f"- 说明：解析层不参与对比（两版读取同一原文）；BM25 分词器："
        f"{'jieba' if meta.get('jieba_available') else 'bigram 降级'}；评测时间 {meta.get('timestamp')}。\n"
    )

    parts.append("## 二、总体结果\n")
    parts.append(_metrics_table(result) + "\n")
    parts.append(_img("metrics", "核心指标对比") + "\n")
    if holdout:
        parts.append("### holdout 冻结口径终评\n")
        parts.append(
            "dev 划分用于阈值校准；以下为 holdout（未参与任何调参）在冻结阈值下的终评结果：\n"
        )
        parts.append(_split_table(holdout) + "\n")

    parts.append("## 三、分类别 Recall@5\n")
    parts.append(_category_table(result) + "\n")
    parts.append(_img("category", "分类别 Recall@5") + "\n")

    parts.append("## 四、延迟\n")
    quiet = ""
    if initial is not None:
        li = initial["legacy"]["latency_ms"]
        hi = initial["hybrid"]["latency_ms"]
        quiet = (
            f"同一代码在较安静环境下（早期全量运行，阈值差异不影响检索计算量）的参考："
            f"旧版 mean {li.get('mean', 0):.1f}ms / P95 {li.get('p95', 0):.1f}ms；"
            f"新版 mean {hi.get('mean', 0):.1f}ms / P95 {hi.get('p95', 0):.1f}ms。"
        )
    parts.append(
        "延迟为**纯检索耗时**（不含 LLM 生成）。当前测量于共享桌面环境（测量期间本机存在其他负载），"
        "绝对值为参考，两版**相对差**为主要结论。" + quiet + "\n"
    )
    parts.append(_img("latency", "检索延迟") + "\n")

    if calibration:
        parts.append("## 五、阈值校准（dev）\n")
        rec = calibration["recommended"]
        parts.append(
            f"- 分层门控网格：单通道强证据（dense/bm25）× 双通道互证（joint_dense/joint_bm25）全组合，"
            f"目标「拒答率（无证据）达标且误拒率 ≤{calibration.get('false_refusal_budget', 0.07):.0%} "
            f"前提下最大化拒答率」；\n"
            f"- 推荐并冻结：**强证据 dense≥{rec['min_dense_similarity']} 或 bm25≥{rec['min_bm25_score']}；"
            f"互证 dense≥{rec.get('joint_dense_similarity')} 且 bm25≥{rec.get('joint_bm25_score')}**；\n"
            f"- 对照（单层 OR 门控同预算最优）：dense≥{calibration.get('or_only_reference', {}).get('min_dense_similarity')}、"
            f"bm25≥{calibration.get('or_only_reference', {}).get('min_bm25_score')} → 拒答率 "
            f"{calibration.get('or_only_reference', {}).get('metrics', {}).get('refusal_rate_unanswerable', 0):.1%}；\n"
            f"- holdout 终评使用冻结值（未参与调参）。\n"
        )
        parts.append(_img("sweep", "阈值扫描") + "\n")

    if initial:
        parts.append("### 附：初始阈值下的全量参考\n")
        parts.append(
            f"（初始值 0.35 / 0.30，全量 {initial['meta'].get('items')} 条；"
            "用于对照校准带来的变化）\n"
        )
        parts.append(_metrics_table(initial) + "\n")

    if llm_subset:
        parts.append("## 六、LLM 端到端子集\n")
        parts.append(llm_subset.get("markdown", "（无数据）") + "\n")

    parts.append("## 七、局限与说明\n")
    parts.append(
        "- 语料为合成文档（24 篇，覆盖 9 个运维类别），评测结论针对本语料集，不代表任意生产语料；\n"
        "- dev/holdout 分层冻结：holdout 全程未参与调参，用作最终报告口径；\n"
        "- 旧版为行为重建（fixed 切块 + 纯稠密 top-5，无阈值/无引用），与 master 代码路径逐点对齐；\n"
        "- reranker（BGE-reranker-v2-m3）与 BGE-M3 属 quality 模式，模型未下载，本轮未评估；\n"
        "- 引用覆盖率/准确率在「未拒答」条目上统计（拒答影响单列误拒率）；\n"
        "- LLM 端到端子集使用**云端 DeepSeek**（用户授权；本机 Ollama 对比跑未做，见维护日志待办），"
        "其回答级数字不代表本地 qwen3:8b 行为；路由平均步数偏高源于路由器重复检索（改进项）；\n"
        "- 延迟为纯检索耗时（不含 LLM 生成），CPU 环境、共享桌面负载下测得。\n"
    )

    parts.append("## 八、复现\n")
    parts.append(
        "```bash\n"
        "# 环境：项目 env_new（Python 3.11）+ jieba\n"
        "env -u PYTHONPATH env_new/Scripts/python.exe scripts/validate_eval_set.py          # 校验评测集\n"
        "env -u PYTHONPATH env_new/Scripts/python.exe scripts/run_eval.py --split holdout    # 终评\n"
        "env -u PYTHONPATH env_new/Scripts/python.exe scripts/calibrate_thresholds.py        # 阈值校准\n"
        "env -u PYTHONPATH env_new/Scripts/python.exe scripts/run_llm_subset.py              # LLM 子集（需本地 Ollama）\n"
        "env -u PYTHONPATH env_new/Scripts/python.exe scripts/gen_report.py --final <结果.json>\n"
        "```\n"
    )
    return "\n".join(parts)


def write_report(
    result_path: Path,
    out_md: Path,
    *,
    calibration_path: Optional[Path] = None,
    initial_path: Optional[Path] = None,
    llm_path: Optional[Path] = None,
    holdout_path: Optional[Path] = None,
) -> Tuple[Path, List[Path]]:
    result = load_json(result_path)
    calibration = load_json(calibration_path) if calibration_path else None
    initial = load_json(initial_path) if initial_path else None
    llm_subset = load_json(llm_path) if llm_path else None
    holdout = load_json(holdout_path) if holdout_path else None

    assets_dir = out_md.parent / "assets"
    images: Dict[str, str] = {}
    made: List[Path] = []
    p = chart_metric_compare(result, assets_dir / "rag-metrics-compare.png")
    images["metrics"] = "assets/rag-metrics-compare.png"
    made.append(p)
    p = chart_category_recall(result, assets_dir / "rag-recall-by-category.png")
    images["category"] = "assets/rag-recall-by-category.png"
    made.append(p)
    p = chart_latency(result, assets_dir / "rag-latency.png")
    images["latency"] = "assets/rag-latency.png"
    made.append(p)
    if calibration:
        p = chart_threshold_sweep(calibration, assets_dir / "rag-threshold-sweep.png")
        images["sweep"] = "assets/rag-threshold-sweep.png"
        made.append(p)

    md = render_report(result, calibration, initial, llm_subset, assets=images, holdout=holdout)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(md, encoding="utf-8")
    return out_md, made
