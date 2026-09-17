"""评测集加载与校验（第 3 周 · RAG 与 Agent 评测体系）。

设计原则:
- 纯 stdlib、零重依赖：CI core 环境与全离线单测可直接运行；
- 数据集为 JSONL（一行一条）；语料为 evals/corpus/*.md（UTF-8）；
- 校验口径：结构性错误进 errors（阻断），统计性异常进 warnings；
  锚点匹配按「空白折叠后的精确子串」判定（对换行/多空格容错，不改字符）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

CATEGORIES: Tuple[str, ...] = (
    "oa_service",
    "http_5xx",
    "resource",
    "mysql",
    "redis",
    "oracle",
    "sqlserver",
    "network",
    "security_ops",
)
SPLITS: Tuple[str, ...] = ("dev", "holdout")

REQUIRED_FIELDS: Tuple[str, ...] = ("id", "question", "category", "answerable")

MIN_TOTAL = 100
MIN_UNANSWERABLE = 20
MIN_SNIPPET_LEN = 4
MIN_QUESTION_LEN = 6
MIN_CATEGORY_ANSWERABLE = 6
SPLIT_RATIO_RANGE = (0.6, 0.8)


@dataclass
class QAItem:
    """单条评测问答。gold_snippets 为面向引用核验的原文锚点（空白折叠后精确子串）。"""

    id: str
    question: str
    category: str
    answerable: bool
    gold_docs: List[str] = field(default_factory=list)
    gold_snippets: List[str] = field(default_factory=list)
    split: str = "dev"
    route_expected: Optional[str] = None
    notes: str = ""


@dataclass
class ValidationReport:
    """校验结果：items 仅在无 errors 时可信；stats 供 CLI 打印与报告引用。"""

    items: List[QAItem] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors


def normalize_ws(text: str) -> str:
    """空白折叠（含换行/制表）——锚点与语料匹配前的统一处理。"""
    return " ".join((text or "").split())


def load_jsonl(path: Path) -> List[Tuple[int, Dict[str, Any]]]:
    """读取 JSONL，返回 [(行号, 原始对象)]；解析失败抛 ValueError；空行与 // 注释行跳过。"""
    rows: List[Tuple[int, Dict[str, Any]]] = []
    text = Path(path).read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{lineno} JSON 解析失败: {exc}") from exc
        if not isinstance(obj, dict):
            raise ValueError(f"{path}:{lineno} 期望 JSON 对象，实际为 {type(obj).__name__}")
        rows.append((lineno, obj))
    return rows


def load_corpus(corpus_dir: Path) -> Dict[str, str]:
    """读取语料目录下全部 *.md（UTF-8），返回 {文件名: 文本}。"""
    corpus: Dict[str, str] = {}
    for path in sorted(Path(corpus_dir).glob("*.md")):
        corpus[path.name] = path.read_text(encoding="utf-8")
    return corpus


def _str_list(value: Any, where: str, name: str, errors: List[str]) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
        errors.append(f"{where}: {name} 必须为字符串数组")
        return []
    return [v.strip() for v in value if v.strip()]


def _check_item(
    lineno: int,
    data: Mapping[str, Any],
    corpus_norm: Mapping[str, str],
    errors: List[str],
    warnings: List[str],
) -> Optional[QAItem]:
    where = f"行{lineno}"
    missing = [k for k in REQUIRED_FIELDS if k not in data]
    if missing:
        errors.append(f"{where}: 缺少必填字段 {missing}")
        return None

    answerable_raw = data.get("answerable")
    if not isinstance(answerable_raw, bool):
        errors.append(f"{where}: answerable 必须为 JSON 布尔值（true/false）")
        return None

    item = QAItem(
        id=str(data.get("id") or "").strip(),
        question=str(data.get("question") or "").strip(),
        category=str(data.get("category") or "").strip(),
        answerable=answerable_raw,
        gold_docs=_str_list(data.get("gold_docs"), where, "gold_docs", errors),
        gold_snippets=_str_list(data.get("gold_snippets"), where, "gold_snippets", errors),
        split=str(data.get("split") or "dev").strip(),
        route_expected=(str(data.get("route_expected")).strip() if data.get("route_expected") else None),
        notes=str(data.get("notes") or ""),
    )

    if not item.id:
        errors.append(f"{where}: id 不能为空")
    if len(item.question) < MIN_QUESTION_LEN:
        errors.append(f"{where}: question 过短（<{MIN_QUESTION_LEN} 字符）")
    if item.category not in CATEGORIES:
        errors.append(f"{where}: category 非法值 {item.category!r}（允许: {', '.join(CATEGORIES)}）")
    if item.split not in SPLITS:
        errors.append(f"{where}: split 非法值 {item.split!r}（允许: {', '.join(SPLITS)}）")

    if item.answerable:
        if not item.gold_docs:
            errors.append(f"{where}: 可回答条目必须给出 gold_docs")
        unknown = [d for d in item.gold_docs if d not in corpus_norm]
        if unknown:
            errors.append(f"{where}: gold_docs 在语料中不存在: {unknown}")
        if not item.gold_snippets:
            errors.append(f"{where}: 可回答条目必须给出 gold_snippets 锚点")
        covered = {d: False for d in item.gold_docs if d in corpus_norm}
        for snippet in item.gold_snippets:
            if len(snippet) < MIN_SNIPPET_LEN:
                errors.append(f"{where}: 锚点过短（<{MIN_SNIPPET_LEN} 字符）: {snippet!r}")
                continue
            needle = normalize_ws(snippet)
            hit_docs = [d for d, text in corpus_norm.items() if d in covered and needle in text]
            if not hit_docs:
                errors.append(f"{where}: 锚点未在 gold_docs 原文中找到: {snippet!r}")
            for d in hit_docs:
                covered[d] = True
        for doc, is_covered in covered.items():
            if not is_covered:
                warnings.append(f"{where}: gold 文档 {doc!r} 没有被任何锚点覆盖")
    else:
        if item.gold_docs:
            errors.append(f"{where}: 无证据条目不应给出 gold_docs")
        if item.gold_snippets:
            errors.append(f"{where}: 无证据条目不应给出 gold_snippets")

    return item


def _build_stats(items: Sequence[QAItem], corpus: Mapping[str, str]) -> Dict[str, Any]:
    by_category: Dict[str, Dict[str, int]] = {}
    for item in items:
        bucket = by_category.setdefault(item.category, {"answerable": 0, "unanswerable": 0})
        bucket["answerable" if item.answerable else "unanswerable"] += 1

    def _split_count(name: str) -> int:
        return sum(1 for it in items if it.split == name)

    total = len(items)
    answerable = sum(1 for it in items if it.answerable)
    return {
        "total": total,
        "answerable": answerable,
        "unanswerable": total - answerable,
        "snippets_total": sum(len(it.gold_snippets) for it in items),
        "split_dev": _split_count("dev"),
        "split_holdout": _split_count("holdout"),
        "by_category": by_category,
        "corpus_docs": len(corpus),
    }


def validate(
    rows: Sequence[Tuple[int, Dict[str, Any]]],
    corpus: Mapping[str, str],
    *,
    strict_counts: bool = True,
) -> ValidationReport:
    """校验评测集（结构 + 锚点 + 配额）。

    strict_counts=True 时执行硬性配额（总数 ≥100、无证据 ≥20、划分非空等）；
    写作与单测阶段可用 False 只查结构。
    """
    report = ValidationReport()
    corpus_norm = {name: normalize_ws(text) for name, text in corpus.items()}
    seen_ids: Dict[str, int] = {}
    seen_questions: Dict[str, int] = {}

    for lineno, data in rows:
        item = _check_item(lineno, data, corpus_norm, report.errors, report.warnings)
        if item is None:
            continue
        if item.id in seen_ids:
            report.errors.append(f"行{lineno}: id 重复（首次出现于行{seen_ids[item.id]}）: {item.id}")
        else:
            seen_ids[item.id] = lineno
        qkey = normalize_ws(item.question)
        if qkey in seen_questions:
            report.errors.append(f"行{lineno}: question 重复（首次出现于行{seen_questions[qkey]}）")
        else:
            seen_questions[qkey] = lineno
        report.items.append(item)

    report.stats = _build_stats(report.items, corpus)

    if strict_counts:
        stats = report.stats
        if stats["total"] < MIN_TOTAL:
            report.errors.append(f"配额: 评测集总条数 {stats['total']} < {MIN_TOTAL}")
        if stats["unanswerable"] < MIN_UNANSWERABLE:
            report.errors.append(f"配额: 无证据条目 {stats['unanswerable']} < {MIN_UNANSWERABLE}")
        if stats["split_dev"] == 0 or stats["split_holdout"] == 0:
            report.errors.append("配额: dev / holdout 两个划分都必须非空")
        else:
            dev_share = stats["split_dev"] / max(stats["total"], 1)
            lo, hi = SPLIT_RATIO_RANGE
            if not (lo <= dev_share <= hi):
                report.warnings.append(f"配额: dev 占比 {dev_share:.0%} 不在 {lo:.0%}~{hi:.0%} 区间")
        for category in CATEGORIES:
            count = report.stats["by_category"].get(category, {}).get("answerable", 0)
            if count < MIN_CATEGORY_ANSWERABLE:
                report.warnings.append(f"配额: 类别 {category} 可回答条目仅 {count} 条（目标 ≥{MIN_CATEGORY_ANSWERABLE}）")

    return report


def load_and_validate(
    dataset_path: Path,
    corpus_dir: Path,
    *,
    strict_counts: bool = True,
) -> ValidationReport:
    """便捷入口：读 JSONL + 读语料 + 校验；供 CLI 与运行器复用。"""
    rows = load_jsonl(dataset_path)
    corpus = load_corpus(corpus_dir)
    return validate(rows, corpus, strict_counts=strict_counts)
