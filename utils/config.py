"""
配置管理模块
-----------
统一加载和访问系统配置，支持：
- YAML 配置文件（config.yaml）
- 环境变量覆盖（${VAR_NAME} 或 ${VAR_NAME:默认值}）
- .env 文件自动加载
- 点号路径访问（如 config.get("llm.api_key")）
"""

import os
import re
import yaml
from typing import Any, Optional

# 项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_dotenv():
    """自动加载项目根目录的 .env 文件（如果存在）"""
    env_file = os.path.join(PROJECT_ROOT, ".env")
    if not os.path.exists(env_file):
        return

    with open(env_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            # 跳过注释和空行
            if not line or line.startswith("#"):
                continue
            # 解析 KEY=VALUE
            if "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value


class Config:
    """
    配置管理器（单例模式）。

    加载顺序（后面覆盖前面）：
    1. config.yaml 默认值
    2. .env 文件
    3. 系统环境变量

    使用方式：
        from utils.config import config
        api_key = config.get("llm.api_key")
        port = config.get("server.port", 7860)
    """

    _instance = None
    _data: dict = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load()
        return cls._instance

    def _load(self):
        """加载配置文件"""
        # 1. 先加载 .env
        _load_dotenv()

        # 2. 读取 YAML 配置文件
        config_path = os.path.join(PROJECT_ROOT, "config.yaml")
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                raw = f.read()

            # 替换环境变量占位符 ${VAR_NAME} 或 ${VAR_NAME:默认值}
            raw = self._resolve_env_vars(raw)

            import yaml
            self._data = yaml.safe_load(raw) or {}
        else:
            self._data = {}

    def _resolve_env_vars(self, text: str) -> str:
        """
        替换文本中的环境变量占位符。
        支持格式：${VAR_NAME} 和 ${VAR_NAME:默认值}
        """
        def replacer(match):
            full = match.group(1)
            if ":" in full:
                var_name, default = full.split(":", 1)
            else:
                var_name, default = full, ""

            return os.environ.get(var_name.strip(), default)

        return re.sub(r"\$\{([^}]+)\}", replacer, text)

    def get(self, path: str, default: Any = None) -> Any:
        """
        通过点号路径获取配置值。

        Args:
            path: 配置路径，如 "llm.api_key" 对应 _data["llm"]["api_key"]
            default: 默认值

        Returns:
            配置值
        """
        keys = path.split(".")
        value = self._data
        for key in keys:
            if isinstance(value, dict):
                value = value.get(key)
            else:
                return default
            if value is None:
                return default
        return value

    def set(self, path: str, value: Any):
        """设置配置值（运行时，不持久化）"""
        keys = path.split(".")
        data = self._data
        for key in keys[:-1]:
            if key not in data:
                data[key] = {}
            data = data[key]
        data[keys[-1]] = value

    def all(self) -> dict:
        """返回全部配置数据"""
        return self._data.copy()

    def reload(self):
        """重新加载配置"""
        self._load()


# 全局单例
config = Config()
