"""结构化输出解析：Pydantic 校验 + JSON 修复回退（纯 Python，离线可测）。

用于把 LLM 的自由文本输出解析为强类型决策，替代 NEXT_ACTION 字符串 + 正则路由。
三级稳健性:
1) 调用方首选 with_structured_output（模型原生函数调用 / JSON 模式）
2) 本模块从文本稳健提取 JSON（支持 markdown 围栏、前后杂文、字符串内大括号）
   → 简单修复（尾随逗号）→ Pydantic 校验
3) 解析/校验失败返回 None，由调用方执行安全默认动作
"""

import json
import re
from typing import Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def _brace_extract(text: str) -> Optional[str]:
    """提取第一个完整的最外层 JSON 对象（字符串内的花括号不参与配对）。"""
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
        start = text.find("{", start + 1)
    return None


def extract_json_object(text: str) -> Optional[str]:
    """从文本中提取 JSON 对象文本：优先围栏内容，其次全文扫描。"""
    if not text:
        return None
    candidates = [match.group(1) for match in _FENCE_RE.finditer(text)]
    candidates.append(text)
    for candidate in candidates:
        found = _brace_extract(candidate)
        if found:
            return found
    return None


def _repair_json(raw: str) -> str:
    """常见小问题修复：尾随逗号、首尾空白。"""
    repaired = re.sub(r",\s*([}\]])", r"\1", raw)
    return repaired.strip()


def parse_structured(text: str, schema: Type[T]) -> Optional[T]:
    """从 LLM 文本输出解析并校验为 schema 实例；任何失败返回 None。"""
    raw = extract_json_object(text or "")
    if raw is None:
        return None
    for attempt in (raw, _repair_json(raw)):
        try:
            data = json.loads(attempt)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        try:
            return schema.model_validate(data)
        except ValidationError:
            continue
    return None
