"""评测集校验器测试（完全离线）。

覆盖：正常通过、字段缺失/类型错误、非法类别与划分、gold 文档不存在、
锚点过短/未命中/跨换行命中、无证据条目带 gold、id 与问题重复、配额硬线。
"""

from pathlib import Path

import pytest

from evals.dataset import load_jsonl, validate

CORPUS = {
    "排查手册.md": "第一步：检查 catalina.out 日志。\n第二步：确认端口 8080 未被占用。",
    "备份手册.md": "每日凌晨 2 点执行全量备份，保留 7 天。",
}


def _row(**overrides: object) -> dict:
    base = {
        "id": "q001",
        "question": "OA 门户出现 502 时先检查哪个日志文件？",
        "category": "http_5xx",
        "answerable": True,
        "gold_docs": ["排查手册.md"],
        "gold_snippets": ["检查 catalina.out 日志"],
        "split": "dev",
    }
    base.update(overrides)
    return base


def _validate(rows: list, *, strict: bool = False):
    return validate(list(enumerate(rows, start=1)), CORPUS, strict_counts=strict)


def _corpus(tmp_path: Path, docs: dict | None = None) -> Path:
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    for name, text in (docs or CORPUS).items():
        (corpus_dir / name).write_text(text, encoding="utf-8")
    return corpus_dir


def test_valid_item_passes() -> None:
    report = _validate([_row()])
    assert report.ok, report.errors
    item = report.items[0]
    assert item.id == "q001"
    assert item.gold_docs == ["排查手册.md"]
    assert item.split == "dev"


def test_valid_unanswerable_item() -> None:
    report = _validate([_row(answerable=False, gold_docs=[], gold_snippets=[], category="oracle")])
    assert report.ok, report.errors


def test_missing_required_field() -> None:
    row = _row()
    del row["category"]
    report = _validate([row])
    assert not report.ok
    assert any("缺少必填字段" in e for e in report.errors)


def test_answerable_must_be_bool() -> None:
    report = _validate([_row(answerable="true")])
    assert not report.ok
    assert any("answerable" in e for e in report.errors)


def test_unknown_category_rejected() -> None:
    report = _validate([_row(category="k8s")])
    assert not report.ok
    assert any("category 非法值" in e for e in report.errors)


def test_bad_split_rejected() -> None:
    report = _validate([_row(split="test")])
    assert not report.ok
    assert any("split 非法值" in e for e in report.errors)


def test_gold_doc_missing_in_corpus() -> None:
    report = _validate([_row(gold_docs=["不存在的文档.md"])])
    assert not report.ok
    assert any("语料中不存在" in e for e in report.errors)


def test_snippet_not_found() -> None:
    report = _validate([_row(gold_snippets=["这句话不在文档里"])])
    assert not report.ok
    assert any("未在 gold_docs 原文中找到" in e for e in report.errors)


def test_snippet_too_short() -> None:
    report = _validate([_row(gold_snippets=["abc"])])
    assert not report.ok
    assert any("锚点过短" in e for e in report.errors)


def test_snippet_matches_across_newline() -> None:
    report = _validate([_row(gold_snippets=["检查 catalina.out 日志。 第二步：确认端口"])])
    assert report.ok, report.errors


def test_unanswerable_with_gold_rejected() -> None:
    report = _validate([_row(answerable=False)])
    assert not report.ok
    assert any("无证据条目不应给出 gold_docs" in e for e in report.errors)


def test_duplicate_id_rejected() -> None:
    second = _row(question="另一个足够长的问题内容是什么？")
    report = _validate([_row(), second])
    assert not report.ok
    assert any("id 重复" in e for e in report.errors)


def test_duplicate_question_rejected() -> None:
    second = _row(id="q002")
    report = _validate([_row(), second])
    assert not report.ok
    assert any("question 重复" in e for e in report.errors)


def test_strict_counts_enforced() -> None:
    report = _validate([_row()], strict=True)
    assert not report.ok
    assert any("总条数" in e for e in report.errors)
    assert any("无证据条目" in e for e in report.errors)


def test_gold_doc_without_snippet_coverage_warns() -> None:
    report = _validate([_row(gold_docs=["排查手册.md", "备份手册.md"])])
    assert report.ok, report.errors
    assert any("没有被任何锚点覆盖" in w for w in report.warnings)


def test_load_jsonl_skips_blank_and_comments(tmp_path: Path) -> None:
    path = tmp_path / "qa.jsonl"
    lines = [
        "",
        "// 注释行",
        '{"id": "q001", "question": "x", "category": "mysql", "answerable": false}',
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    rows = load_jsonl(path)
    assert len(rows) == 1
    assert rows[0][0] == 3


def test_load_jsonl_reports_bad_json(tmp_path: Path) -> None:
    path = tmp_path / "qa.jsonl"
    path.write_text('{"id": "q001", broken}\n', encoding="utf-8")
    with pytest.raises(ValueError):
        load_jsonl(path)
