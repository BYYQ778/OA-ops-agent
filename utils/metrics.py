"""最小指标注册表：线程安全计数器 + Prometheus 文本输出（第 4 周）。

第 5 周将扩展 OpenTelemetry / 完整 Prometheus 指标；本模块先满足
GET /metrics 的最小可用形态（诊断次数、拒答数等业务计数）。
"""

from __future__ import annotations

import threading
from typing import Dict, List


class MetricsRegistry:
    """进程内计数器（线程安全）；指标名为 snake_case。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: Dict[str, int] = {}

    def inc(self, name: str, value: int = 1) -> None:
        """计数 +value（默认 1）。"""
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + int(value)

    def get(self, name: str) -> int:
        """读取当前计数（不存在为 0）。"""
        with self._lock:
            return self._counters.get(name, 0)

    def reset(self) -> None:
        """清空所有计数（测试与运维排查用）。"""
        with self._lock:
            self._counters.clear()

    def snapshot(self) -> Dict[str, int]:
        """当前全部计数快照。"""
        with self._lock:
            return dict(self._counters)

    def render(self) -> str:
        """Prometheus 文本格式（# TYPE counter）。"""
        lines: List[str] = []
        for name, value in sorted(self.snapshot().items()):
            lines.append(f"# TYPE {name} counter")
            lines.append(f"{name} {value}")
        return "\n".join(lines) + ("\n" if lines else "")


#: 进程级单例
metrics = MetricsRegistry()
