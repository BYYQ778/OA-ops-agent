"""可选重排序组件（BGE CrossEncoder；懒加载，缺失/失败时直通降级）。

quality 模式启用（config: knowledge_base.retrieval.rerank），模型单独下载
（默认 BAAI/bge-reranker-v2-m3），不进入基础安装体积；lite/CI/离线环境
未安装 sentence-transformers 或未下载模型时，本组件原样返回候选（不报错）。

测试注入点: CrossEncoderReranker(encoder=...) 可传入 fake encoder（实现
predict(pairs) -> 分数序列），完全离线可测。
"""

from typing import Any, List, Optional, Sequence

from utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_RERANK_MODEL = "BAAI/bge-reranker-v2-m3"


class CrossEncoderReranker:
    """CrossEncoder 重排序：按 (query, text) 相关性分数重排候选项。"""

    def __init__(self, model_name: str = DEFAULT_RERANK_MODEL, encoder: Any = None):
        self.model_name = model_name
        self._encoder = encoder
        self._load_failed = False

    def _get_encoder(self):
        if self._encoder is not None:
            return self._encoder
        if self._load_failed:
            return None
        try:
            from sentence_transformers import CrossEncoder

            self._encoder = CrossEncoder(self.model_name, max_length=512)
            logger.info(f"重排序模型已加载: {self.model_name}")
        except Exception as e:  # 未安装/未下载/加载失败 → 直通模式（仅记录一次）
            self._load_failed = True
            logger.warning(f"重排序模型不可用，检索将直通（不重排）: {e}")
            return None
        return self._encoder

    def __call__(self, query: str, hits: Sequence[Any], top_k: int = 5) -> List[Any]:
        candidates = list(hits)
        if not candidates:
            return candidates
        encoder = self._get_encoder()
        if encoder is None:
            return candidates[:top_k] if top_k else candidates
        try:
            scores = list(encoder.predict([(query, h.text) for h in candidates]))
            ranked = [hit for _, hit in sorted(zip(scores, candidates), key=lambda pair: -pair[0])]
            return ranked[:top_k] if top_k else ranked
        except Exception as e:
            logger.warning(f"重排序执行失败，保持原序: {e}")
            return candidates[:top_k] if top_k else candidates


def build_reranker(enabled: bool, model_name: Optional[str] = None):
    """按配置构建重排序器；未启用时返回 None（HybridRetriever 跳过重排）。"""
    if not enabled:
        return None
    return CrossEncoderReranker(model_name=model_name or DEFAULT_RERANK_MODEL)
