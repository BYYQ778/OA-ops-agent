"""
日志工具模块
-----------
提供统一的日志记录功能：
- 控制台输出（INFO级别）
- 文件持久化（按天轮转，保留最近7天）
- 支持获取专属logger实例，避免各模块日志混乱
- 第 5 周：结构化日志（JSON Lines 可选）、request-id/trace-id 注入、密钥脱敏
"""

import json
import logging
import os
import re
from logging.handlers import TimedRotatingFileHandler
from typing import Iterable, List, Mapping

from utils.request_context import get_request_id, get_trace_id


class RobustTimedRotatingFileHandler(TimedRotatingFileHandler):
    """容忍轮转失败的 TimedRotatingFileHandler，旧进程锁文件时不崩溃。"""

    def handleError(self, record):
        """吞掉轮转 PermissionError，避免旧进程锁文件导致整个启动失败。"""
        import sys
        exc_type, exc_value, _ = sys.exc_info()
        if exc_type is PermissionError:
            return  # 静默跳过，日志本轮写入控制台即可
        super().handleError(record)


# ========== 全局配置 ==========
from utils.config import get_app_root
LOG_DIR = os.path.join(get_app_root(), "data", "inspection_logs")
os.makedirs(LOG_DIR, exist_ok=True)  # 确保日志目录存在

# 日志格式（text 模式）：时间 | 级别 | 模块名 | 消息内容
LOG_FORMAT = logging.Formatter(
    "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

# 密钥形值识别（脱敏 filter 用）：键名含关键字且值长度 >= 8 才视为敏感
_SECRET_KEY_PATTERN = re.compile(r"(PASSWORD|PASSWD|API_KEY|TOKEN|SECRET)", re.IGNORECASE)
_MIN_SECRET_LENGTH = 8


class JsonFormatter(logging.Formatter):
    """JSON Lines formatter：结构化字段 + request_id/trace_id 注入（第 5 周）。"""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%d %H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        request_id = get_request_id()
        if request_id:
            payload["request_id"] = request_id
        trace_id = get_trace_id()
        if trace_id:
            payload["trace_id"] = trace_id
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def collect_secret_values(environ: Mapping[str, str] | None = None) -> List[str]:
    """收集环境变量中疑似密钥的值（键名含 PASSWORD/API_KEY/TOKEN/SECRET 且长度 >= 8）。"""
    env = os.environ if environ is None else environ
    values: List[str] = []
    for key, value in env.items():
        if not value or len(value) < _MIN_SECRET_LENGTH:
            continue
        if _SECRET_KEY_PATTERN.search(key):
            values.append(value)
    return values


class RedactionFilter(logging.Filter):
    """把已知密钥值从日志消息中替换为 ***（密钥隔离，第 5 周）。"""

    def __init__(self, secrets: Iterable[str]) -> None:
        super().__init__()
        self._secrets = [s for s in secrets if len(s) >= _MIN_SECRET_LENGTH]

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._secrets:
            return True
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - 异常记录不因脱敏逻辑反过来打崩日志
            return True
        redacted = message
        for secret in self._secrets:
            redacted = redacted.replace(secret, "***")
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def _resolve_log_format() -> str:
    """日志格式开关：OA_LOG_FORMAT 环境变量 > config.yaml logging.format > text。"""
    env_value = os.environ.get("OA_LOG_FORMAT", "").strip().lower()
    if env_value in ("text", "json"):
        return env_value
    try:
        from utils.config import config
        configured = str(config.get("logging.format", "text")).strip().lower()
    except Exception:  # noqa: BLE001 - 配置不可用时退回默认
        configured = "text"
    return configured if configured in ("text", "json") else "text"


def get_logger(name: str = "OAOpsAgent") -> logging.Logger:
    """
    获取一个配置好的logger实例。

    每个模块调用此函数时会获得独立的logger，
    日志同时输出到控制台和文件，文件按天自动轮转。
    第 5 周起：支持 JSON 格式（OA_LOG_FORMAT / logging.format），
    并统一附加密钥脱敏 filter。

    Args:
        name: logger名称，建议传 __name__ 以便区分来源模块

    Returns:
        配置好的 logging.Logger 实例
    """
    logger = logging.getLogger(name)

    # 避免重复添加handler（多次调用时检查）
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    formatter: logging.Formatter = JsonFormatter() if _resolve_log_format() == "json" else LOG_FORMAT
    redaction = RedactionFilter(collect_secret_values())

    # ---- 控制台handler ----
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(redaction)
    logger.addHandler(console_handler)

    # ---- 文件handler（按天轮转，保留7天）----
    log_file = os.path.join(LOG_DIR, "oa_ops.log")
    file_handler = RobustTimedRotatingFileHandler(
        filename=log_file,
        when="midnight",
        interval=1,
        backupCount=7,
        encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    file_handler.addFilter(redaction)
    logger.addHandler(file_handler)

    return logger
