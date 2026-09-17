"""Import helpers for modules that depend on optional RAG extras.

CI installs only the core dependency set (``uv sync --locked --dev``) and the
local uv environment skips the heavy RAG/OCR extras as well. Tests that
exercise such modules first install minimal placeholder modules for the
missing third-party names, then replace every real class with a fake inside
the test itself.
"""

import importlib
import sys
import types

# 可选依赖模块名 → 本需要占位的顶层类名
_OPTIONAL_STUBS: dict[str, tuple] = {
    "langchain_huggingface": ("HuggingFaceEmbeddings",),
    "langchain_chroma": ("Chroma",),
    "sentence_transformers": ("SentenceTransformer",),
}


class OptionalDependencyStub:
    """Placeholder for a missing optional dependency.

    Instantiating it fails loudly, so a test that forgets to install a fake
    cannot silently pass with a mock.
    """

    def __init__(self, *args, **kwargs) -> None:
        raise RuntimeError("可选依赖未安装：测试必须先把该类替换为 fake 再实例化（见 tests/_optional_deps.py）")


def ensure_optional_stubs() -> None:
    """Register placeholder modules for optional dependencies that are absent."""
    for name, attributes in _OPTIONAL_STUBS.items():
        try:
            importlib.import_module(name)
            continue
        except ModuleNotFoundError:
            pass
        module = types.ModuleType(name)
        module.__doc__ = f"placeholder installed by tests/_optional_deps.py ({name} is not installed)"
        for attribute in attributes:
            setattr(module, attribute, OptionalDependencyStub)
        sys.modules[name] = module


def import_knowledge_agent():
    """Import ``agents.knowledge_agent``, stubbing absent optional RAG deps."""
    ensure_optional_stubs()
    return importlib.import_module("agents.knowledge_agent")
