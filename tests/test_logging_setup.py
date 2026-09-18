"""第 5 周 w5-1：结构化日志（JSON formatter / 密钥脱敏 filter / 格式开关）。"""

from __future__ import annotations

import json
import logging
import os
import sys
import uuid

from utils.logger import JsonFormatter, RedactionFilter, collect_secret_values, get_logger
from utils.request_context import get_request_id, reset_request_id, set_request_id


def _record(msg: str, args: tuple = (), level: int = logging.INFO) -> logging.LogRecord:
    return logging.LogRecord("test", level, __file__, 1, msg, args, None)


# ---------- request context ----------


def test_request_context_set_and_reset() -> None:
    assert get_request_id() == ""
    token = set_request_id("abc123")
    try:
        assert get_request_id() == "abc123"
    finally:
        reset_request_id(token)
    assert get_request_id() == ""


# ---------- JSON formatter ----------


def test_json_formatter_basic_fields() -> None:
    payload = json.loads(JsonFormatter().format(_record("磁盘告警处理")))
    assert payload["level"] == "INFO"
    assert payload["logger"] == "test"
    assert payload["msg"] == "磁盘告警处理"
    assert "ts" in payload
    assert "request_id" not in payload


def test_json_formatter_with_args_and_request_id() -> None:
    token = set_request_id("req-999")
    try:
        payload = json.loads(JsonFormatter().format(_record("清理 %s 完成", ("临时文件",))))
    finally:
        reset_request_id(token)
    assert payload["msg"] == "清理 临时文件 完成"
    assert payload["request_id"] == "req-999"


def test_json_formatter_exc_info() -> None:
    try:
        raise ValueError("boom-secret")
    except ValueError:
        record = logging.LogRecord("test", logging.ERROR, __file__, 1, "失败", (), sys.exc_info())
    payload = json.loads(JsonFormatter().format(record))
    assert "boom-secret" in payload["exc"]


# ---------- 脱敏 filter ----------


def test_redaction_filter_masks_long_values() -> None:
    record = _record("密码是 supersecret123，短值 ok123 保留")
    RedactionFilter(["supersecret123", "ok123"]).filter(record)
    message = record.getMessage()
    assert "supersecret123" not in message
    assert "***" in message
    assert "ok123" in message  # 少于 8 位不脱敏


def test_collect_secret_values(monkeypatch) -> None:
    monkeypatch.setenv("OA_TEST_PASSWORD", "verysecretpw")
    monkeypatch.setenv("OA_TEST_SHORT_PASSWORD", "tiny")
    monkeypatch.setenv("OA_TEST_PLAIN", "not-a-secret")
    values = collect_secret_values()
    assert "verysecretpw" in values
    assert "tiny" not in values
    assert "not-a-secret" not in values


# ---------- get_logger 接线 ----------


def test_get_logger_json_format_switch(monkeypatch) -> None:
    monkeypatch.setenv("OA_LOG_FORMAT", "json")
    logger = get_logger(f"test-json-{uuid.uuid4().hex}")
    assert logger.handlers
    assert all(isinstance(h.formatter, JsonFormatter) for h in logger.handlers)


def test_get_logger_default_text_format(monkeypatch) -> None:
    monkeypatch.delenv("OA_LOG_FORMAT", raising=False)
    logger = get_logger(f"test-text-{uuid.uuid4().hex}")
    assert logger.handlers
    assert all(not isinstance(h.formatter, JsonFormatter) for h in logger.handlers)


def test_get_logger_attaches_redaction_filter(monkeypatch) -> None:
    monkeypatch.setenv("OA_TEST_SECRET_TOKEN", "topsecrettoken1")
    logger = get_logger(f"test-redact-{uuid.uuid4().hex}")
    assert any(any(isinstance(f, RedactionFilter) for f in h.filters) for h in logger.handlers)


def test_logger_file_redaction_end_to_end(monkeypatch) -> None:
    """真实链路：get_logger 的文件 handler 必须把密钥值写成 ***。"""
    monkeypatch.setenv("OA_TEST_API_KEY", "abcdefgh-very-secret")
    logger = get_logger(f"test-e2e-{uuid.uuid4().hex}")
    file_handler = next(h for h in logger.handlers if isinstance(h, logging.FileHandler))
    path = file_handler.baseFilename
    before = os.path.getsize(path) if os.path.exists(path) else 0

    logger.info("dump key %s", "abcdefgh-very-secret")
    for handler in logger.handlers:
        handler.flush()

    with open(path, "r", encoding="utf-8") as f:
        f.seek(before)
        written = f.read()
    assert "abcdefgh-very-secret" not in written
    assert "***" in written
