"""
日志工具模块
-----------
提供统一的日志记录功能：
- 控制台输出（INFO级别）
- 文件持久化（按天轮转，保留最近7天）
- 支持获取专属logger实例，避免各模块日志混乱
"""

import logging
import os
from logging.handlers import TimedRotatingFileHandler

# ========== 全局配置 ==========
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "inspection_logs")
os.makedirs(LOG_DIR, exist_ok=True)  # 确保日志目录存在

# 日志格式：时间 | 级别 | 模块名 | 消息内容
LOG_FORMAT = logging.Formatter(
    "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)


def get_logger(name: str = "OAOpsAgent") -> logging.Logger:
    """
    获取一个配置好的logger实例。

    每个模块调用此函数时会获得独立的logger，
    日志同时输出到控制台和文件，文件按天自动轮转。

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

    # ---- 控制台handler ----
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(LOG_FORMAT)
    logger.addHandler(console_handler)

    # ---- 文件handler（按天轮转，保留7天）----
    log_file = os.path.join(LOG_DIR, "oa_ops.log")
    file_handler = TimedRotatingFileHandler(
        filename=log_file,
        when="midnight",
        interval=1,
        backupCount=7,
        encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(LOG_FORMAT)
    logger.addHandler(file_handler)

    return logger
