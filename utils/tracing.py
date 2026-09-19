"""OpenTelemetry 追踪薄封装（第 5 周）。

- **可选依赖**：未安装 opentelemetry-sdk 或未启用时，全部 span 退化为 no-op；
- 启用方式：config.yaml `observability.tracing.enabled` 或环境变量 `OA_TRACING=1`；
- exporter：console（本地调试，立即输出）/ otlp（HTTP；endpoint 与 headers 可配，
  可指向任何 OTLP 兼容端点——如 Langfuse 的 OTel 摄入端点）；
- span 生效期间把 trace_id 写入 utils.request_context（JSON 日志自动携带 trace_id 字段）；
- 安装（可选）：`pip install opentelemetry-sdk`
  （用 OTLP exporter 还需 `pip install opentelemetry-exporter-otlp-proto-http`）。
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator, Mapping, Optional

from utils.logger import get_logger
from utils.request_context import reset_trace_id, set_trace_id

_logger = get_logger("oa.tracing")

_SERVICE_NAME = "oa-ops-agent"
_SDK_HINT = "未安装 opentelemetry-sdk（可选依赖），追踪自动降级为 no-op；如需启用：pip install opentelemetry-sdk"

#: 全局 tracer（None = no-op）；测试可直接注入假实现
_tracer: Any = None
_configured = False


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def tracing_enabled() -> bool:
    """是否启用追踪：环境变量 OA_TRACING > config.yaml observability.tracing.enabled。"""
    if "OA_TRACING" in os.environ:
        return _env_flag("OA_TRACING")
    try:
        from utils.config import config
        return bool(config.get("observability.tracing.enabled", False))
    except Exception:  # noqa: BLE001 - 配置不可用按关闭处理
        return False


def _resolve_exporter() -> str:
    raw = os.environ.get("OA_TRACING_EXPORTER", "").strip().lower()
    if raw in ("console", "otlp"):
        return raw
    try:
        from utils.config import config
        configured = str(config.get("observability.tracing.exporter", "console")).strip().lower()
    except Exception:  # noqa: BLE001 - 配置不可用按 console 处理
        configured = "console"
    return configured if configured in ("console", "otlp") else "console"


def _build_exporter(mode: str) -> tuple[Any, str]:
    """构建 exporter；otlp 库缺失时退回 console（返回 (exporter, 实际模式)）。"""
    if mode != "otlp":
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter
        return ConsoleSpanExporter(), "console"
    endpoint = os.environ.get("OA_TRACING_OTLP_ENDPOINT", "").strip()
    if not endpoint:
        try:
            from utils.config import config
            endpoint = str(config.get("observability.tracing.otlp_endpoint", "") or "").strip()
        except Exception:  # noqa: BLE001
            endpoint = ""
    headers: dict[str, str] = {}
    for pair in os.environ.get("OA_TRACING_OTLP_HEADERS", "").split(","):
        if "=" in pair:
            key, _, value = pair.partition("=")
            headers[key.strip()] = value.strip()
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    except Exception:  # noqa: BLE001 - 缺 otlp 包时退回 console
        _logger.warning("未安装 OTLP exporter（opentelemetry-exporter-otlp-proto-http），退回 console exporter")
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter
        return ConsoleSpanExporter(), "console"
    return OTLPSpanExporter(endpoint=endpoint or None, headers=headers or None), "otlp"


def configure_tracing() -> bool:
    """初始化 OTel SDK；返回是否真正启用（幂等；失败自动降级为 no-op）。"""
    global _tracer, _configured
    if _configured:
        return _tracer is not None
    _configured = True
    if not tracing_enabled():
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
    except Exception:  # noqa: BLE001 - SDK 属可选依赖，缺失则 no-op
        _logger.info("追踪已开启，但 %s", _SDK_HINT)
        return False
    try:
        exporter, effective_mode = _build_exporter(_resolve_exporter())
        processor = SimpleSpanProcessor(exporter) if effective_mode == "console" else BatchSpanProcessor(exporter)
        provider = TracerProvider(resource=Resource.create({"service.name": _SERVICE_NAME}))
        provider.add_span_processor(processor)
        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer(_SERVICE_NAME)
        _logger.info("追踪已启用（exporter=%s）", effective_mode)
        return True
    except Exception as exc:  # noqa: BLE001 - 初始化失败不阻断主流程
        _logger.warning("追踪初始化失败，自动降级为 no-op：%s", exc)
        _tracer = None
        return False


@contextmanager
def span(name: str, attributes: Optional[Mapping[str, Any]] = None) -> Iterator[Any]:
    """创建 span（未启用时 no-op）；span 生效期间日志自动携带 trace_id。"""
    tracer = _tracer
    if tracer is None:
        yield None
        return
    with tracer.start_as_current_span(name) as active:
        if attributes:
            for key, value in attributes.items():
                active.set_attribute(key, value)
        trace_id = 0
        span_context = active.get_span_context()
        if span_context is not None:
            trace_id = int(getattr(span_context, "trace_id", 0) or 0)
        if not trace_id:
            yield active
            return
        token = set_trace_id(format(trace_id, "032x"))
        try:
            yield active
        finally:
            reset_trace_id(token)
