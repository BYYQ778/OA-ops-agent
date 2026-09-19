"""结构化输出解析测试（完全离线）。

覆盖：纯 JSON / markdown 围栏 / 前后杂文 / 字符串内大括号 / 尾随逗号修复 /
Literal 校验拒绝非法值 / 缺字段默认值 / 非 JSON 返回 None。
"""

from typing import Literal, Optional

from pydantic import BaseModel

from utils.structured import extract_json_object, parse_structured


class Decision(BaseModel):
    action: Literal["search_kb", "search_kg", "answer"]
    query: str = ""
    reasoning: str = ""


def test_plain_json() -> None:
    out = parse_structured('{"action": "search_kb", "query": "磁盘"}', Decision)
    assert out is not None
    assert out.action == "search_kb"
    assert out.query == "磁盘"


def test_markdown_fenced_json() -> None:
    text = '好的，我的决策如下：\n```json\n{"action": "answer", "query": ""}\n```'
    out = parse_structured(text, Decision)
    assert out is not None and out.action == "answer"


def test_json_embedded_in_prose() -> None:
    text = '我决定：{"action": "search_kb", "query": "nginx 502"} 以上。'
    out = parse_structured(text, Decision)
    assert out is not None and out.query == "nginx 502"


def test_braces_inside_strings_do_not_break_pairing() -> None:
    text = '{"action": "search_kb", "query": "排查 {502} 错误 {nginx}"}'
    out = parse_structured(text, Decision)
    assert out is not None
    assert out.query == "排查 {502} 错误 {nginx}"


def test_escaped_quote_in_string() -> None:
    text = r'{"action": "search_kb", "query": "引号 \" 转义"}'
    out = parse_structured(text, Decision)
    assert out is not None and '"' in out.query


def test_trailing_comma_repair() -> None:
    text = '{"action": "answer", "query": "",}'
    out = parse_structured(text, Decision)
    assert out is not None and out.action == "answer"


def test_invalid_literal_rejected() -> None:
    assert parse_structured('{"action": "delete_all"}', Decision) is None


def test_missing_required_field_returns_none() -> None:
    assert parse_structured('{"query": "abc"}', Decision) is None


def test_defaults_applied() -> None:
    out = parse_structured('{"action": "search_kg"}', Decision)
    assert out is not None
    assert out.query == ""
    assert out.reasoning == ""


def test_non_json_text_returns_none() -> None:
    assert parse_structured("NEXT_ACTION: search_kb\n磁盘排查", Decision) is None
    assert parse_structured("", Decision) is None
    assert parse_structured("   ", Decision) is None


def test_extract_json_object_nested() -> None:
    raw = extract_json_object('前文 {"a": {"b": 1}, "c": [1, 2]} 后文')
    assert raw == '{"a": {"b": 1}, "c": [1, 2]}'


def test_optional_field_none_default() -> None:
    class WithOptional(BaseModel):
        action: str
        note: Optional[str] = None

    out = parse_structured('{"action": "x"}', WithOptional)
    assert out is not None and out.note is None
