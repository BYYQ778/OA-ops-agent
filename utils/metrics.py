"""指标注册表（第 4 周最小计数器 → 第 5 周完整 Prometheus 原语）。

- 计数器 / 仪表 / 直方图，线程安全；文本格式遵循 Prometheus exposition
  （text/plain; version=0.0.4）；
- 零第三方依赖（兼容绿色版冻结产物与离线 CI）；
- 第 4 周 API（inc/get/reset/snapshot/render）保持兼容，渲染行只增不减。
"""

from __future__ import annotations

import re
import threading
import time
from typing import Dict, Iterable, List, Mapping, Optional, Tuple

#: 直方图默认桶（秒；对齐 Prometheus 常用默认值）
DEFAULT_BUCKETS: Tuple[float, ...] = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

#: 高基数路径段折叠（数字 / 长 hex / INC-* 编号）
_ID_SEGMENT = re.compile(r"^(?:[0-9]+|[0-9a-fA-F]{8,}|INC-[A-Za-z0-9-]+)$")

#: 保留原样统计的非 API 页面（其余非 /api 路径折叠为 /<other>，防扫描器制造基数）
_KNOWN_PAGES = frozenset({"/", "/login"})

LabelKey = Tuple[Tuple[str, str], ...]


def _escape_label_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _labels_key(labels: Optional[Mapping[str, str]]) -> LabelKey:
    if not labels:
        return ()
    return tuple(sorted((str(key), str(value)) for key, value in labels.items()))


def _render_labels(key: LabelKey) -> str:
    if not key:
        return ""
    inner = ",".join(f'{name}="{_escape_label_value(value)}"' for name, value in key)
    return "{" + inner + "}"


def _format_number(value: float) -> str:
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return repr(value)


def normalize_route(path: str) -> str:
    """把路径中的高基数段折叠为 {id}（防指标标签基数爆炸）。"""
    if not path:
        return "/"
    if path in _KNOWN_PAGES:
        return path
    if not path.startswith("/api"):
        return "/<other>"
    return "/".join("{id}" if _ID_SEGMENT.match(segment) else segment for segment in path.split("/"))


class _HistogramState:
    """直方图状态：非累计桶计数 + 总数/总和（渲染时转累计）。"""

    __slots__ = ("buckets", "bucket_counts", "count", "total")

    def __init__(self, buckets: Tuple[float, ...]) -> None:
        self.buckets = buckets
        self.bucket_counts = [0] * len(buckets)
        self.count = 0
        self.total = 0.0

    def observe(self, value: float) -> None:
        self.count += 1
        self.total += value
        for index, bound in enumerate(self.buckets):
            if value <= bound:
                self.bucket_counts[index] += 1
                return
        # 超过最大桶：只进 count/sum（+Inf 桶在渲染用 count）


class MetricsRegistry:
    """进程内指标注册表（线程安全）；指标名为 snake_case。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: Dict[str, int] = {}
        self._labeled_counters: Dict[str, Dict[LabelKey, float]] = {}
        self._gauges: Dict[Tuple[str, LabelKey], float] = {}
        self._histograms: Dict[str, Dict[LabelKey, _HistogramState]] = {}
        self._types: Dict[str, str] = {}
        self._helps: Dict[str, str] = {}

    # ---------- 第 4 周 API（兼容） ----------

    def inc(self, name: str, value: int = 1) -> None:
        """计数 +value（默认 1）。"""
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + int(value)
            self._types.setdefault(name, "counter")

    def get(self, name: str) -> int:
        """读取当前计数（不存在为 0）。"""
        with self._lock:
            return self._counters.get(name, 0)

    # ---------- 第 5 周：带标签计数器 ----------

    def inc_labeled(self, name: str, labels: Optional[Mapping[str, str]] = None, value: float = 1) -> None:
        """带标签计数（标签键排序归一，空标签等价于无标签）。"""
        key = _labels_key(labels)
        with self._lock:
            family = self._labeled_counters.setdefault(name, {})
            family[key] = family.get(key, 0.0) + float(value)
            self._types.setdefault(name, "counter")

    def get_labeled(self, name: str, labels: Optional[Mapping[str, str]] = None) -> float:
        key = _labels_key(labels)
        with self._lock:
            return self._labeled_counters.get(name, {}).get(key, 0.0)

    # ---------- 第 5 周：仪表 ----------

    def set_gauge(self, name: str, value: float, labels: Optional[Mapping[str, str]] = None) -> None:
        key = _labels_key(labels)
        with self._lock:
            self._gauges[(name, key)] = float(value)
            self._types.setdefault(name, "gauge")

    def get_gauge(self, name: str, labels: Optional[Mapping[str, str]] = None) -> Optional[float]:
        with self._lock:
            return self._gauges.get((name, _labels_key(labels)))

    # ---------- 第 5 周：直方图 ----------

    def observe(
        self,
        name: str,
        value: float,
        labels: Optional[Mapping[str, str]] = None,
        buckets: Iterable[float] = DEFAULT_BUCKETS,
    ) -> None:
        key = _labels_key(labels)
        with self._lock:
            family = self._histograms.setdefault(name, {})
            state = family.get(key)
            if state is None:
                state = _HistogramState(tuple(sorted(float(bound) for bound in buckets)))
                family[key] = state
            self._types.setdefault(name, "histogram")
            state.observe(float(value))

    def get_histogram(self, name: str, labels: Optional[Mapping[str, str]] = None) -> Optional[Tuple[int, float, List[int]]]:
        """返回 (count, sum, 非累计桶计数列表)；不存在返回 None。"""
        with self._lock:
            state = self._histograms.get(name, {}).get(_labels_key(labels))
            if state is None:
                return None
            return state.count, state.total, list(state.bucket_counts)

    # ---------- 通用 ----------

    def set_help(self, name: str, text: str) -> None:
        with self._lock:
            self._helps[name] = text

    def reset(self) -> None:
        """清空所有指标（测试与运维排查用）。"""
        with self._lock:
            self._counters.clear()
            self._labeled_counters.clear()
            self._gauges.clear()
            self._histograms.clear()
            self._types.clear()
            self._helps.clear()

    def snapshot(self) -> Dict[str, int]:
        """第 4 周语义：仅普通计数器快照（兼容旧调用）。"""
        with self._lock:
            return dict(self._counters)

    def render(self) -> str:
        """Prometheus 文本格式；第 4 周普通计数器行保持原格式不变。"""
        with self._lock:
            counters = dict(self._counters)
            labeled = {name: dict(family) for name, family in self._labeled_counters.items()}
            gauges = dict(self._gauges)
            histograms = {name: dict(family) for name, family in self._histograms.items()}
            types = dict(self._types)
            helps = dict(self._helps)

        lines: List[str] = []

        # 1) 旧普通计数器（格式与第 4 周完全一致）
        for name, value in sorted(counters.items()):
            lines.append(f"# TYPE {name} counter")
            lines.append(f"{name} {value}")

        # 2) 带标签计数器
        for name in sorted(labeled):
            lines.append(f"# TYPE {name} {types.get(name, 'counter')}")
            if name in helps:
                lines.append(f"# HELP {name} {helps[name]}")
            for key, value in sorted(labeled[name].items()):
                lines.append(f"{name}{_render_labels(key)} {_format_number(value)}")

        # 3) 仪表（同一指标名合并 TYPE 行）
        gauge_families: Dict[str, List[Tuple[LabelKey, float]]] = {}
        for (name, key), value in gauges.items():
            gauge_families.setdefault(name, []).append((key, value))
        for name in sorted(gauge_families):
            lines.append(f"# TYPE {name} gauge")
            if name in helps:
                lines.append(f"# HELP {name} {helps[name]}")
            for key, value in sorted(gauge_families[name]):
                lines.append(f"{name}{_render_labels(key)} {_format_number(value)}")

        # 4) 直方图（累计桶 + _sum/_count）
        for name in sorted(histograms):
            lines.append(f"# TYPE {name} histogram")
            if name in helps:
                lines.append(f"# HELP {name} {helps[name]}")
            for key, state in sorted(histograms[name].items()):
                cumulative = 0
                for bound, bucket_count in zip(state.buckets, state.bucket_counts):
                    cumulative += bucket_count
                    bucket_key = key + (("le", str(bound)),)
                    lines.append(f"{name}_bucket{_render_labels(bucket_key)} {cumulative}")
                infinite_key = key + (("le", "+Inf"),)
                lines.append(f"{name}_bucket{_render_labels(infinite_key)} {state.count}")
                lines.append(f"{name}_sum{_render_labels(key)} {_format_number(state.total)}")
                lines.append(f"{name}_count{_render_labels(key)} {state.count}")

        return "\n".join(lines) + ("\n" if lines else "")


def init_default_metrics(version: str) -> None:
    """设置进程级默认仪表与 HELP（创建应用时调用；重复调用幂等）。"""
    metrics.set_gauge("oa_build_info", 1.0, {"version": version})
    metrics.set_gauge("oa_process_start_time_seconds", time.time())
    metrics.set_help("oa_build_info", "Build info with version label")
    metrics.set_help("oa_http_requests_total", "HTTP requests by method, normalized route and status")
    metrics.set_help("oa_http_request_duration_seconds", "HTTP request duration by method and normalized route")


#: 进程级单例
metrics = MetricsRegistry()
