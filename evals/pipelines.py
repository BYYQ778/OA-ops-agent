"""评测管线适配（第 3 周 RAG 评测）。

- LegacyPipeline：重建旧版行为 —— 固定 500/50 切块 + Chroma 纯稠密 similarity_search
  top-5（无阈值、无引用、无拒答），与 master（81dd4d4）代码路径逐点对齐；
- HybridPipeline：复用 utils/retrieval.HybridRetriever（语义切块 + BM25 + Dense + RRF
  + 阈值门），配置取自 config.yaml，可注入覆盖值（阈值校准用）。

设计要点:
- chromadb / sentence-transformers / langchain-* 全部惰性导入（CI core 环境可安全
  import 本模块）；离线单测通过工厂注入 fake 存储，不下载模型、不联网；
- 两条管线读取同一份语料原始文本（解析层不参与对比，避免不公平差异）。
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from utils.retrieval import HybridRetriever, RetrievalConfig

EMBEDDING_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"
LEGACY_COLLECTION = "eval_legacy"
HYBRID_COLLECTION = "eval_hybrid"
LEGACY_CHUNK_SIZE = 500
LEGACY_CHUNK_OVERLAP = 50

# (collection_name, persist_dir) -> 存储对象（需要 add_documents / similarity_search* / get）
StoreFactory = Callable[[str, str], Any]

_EMBEDDINGS_CACHE: Any = None


def distance_to_similarity(distance: Any) -> float:
    """Chroma 默认 L2 距离（归一化向量）→ 余弦相似度：cos = 1 - d²/2。

    与生产代码 KnowledgeBaseAgent._distance_to_similarity 完全一致（镜像实现，
    此处保持可离线单测）。
    """
    try:
        d = float(distance)
    except (TypeError, ValueError):
        return 0.0
    return max(-1.0, min(1.0, 1.0 - (d * d) / 2.0))


def canonical_chunk_id(meta: Mapping[str, Any], fallback_index: int = 0) -> str:
    """两通道统一的块 ID：chunk_uid 优先，旧数据回退 source#chunk_index。"""
    uid = meta.get("chunk_uid")
    if uid:
        return str(uid)
    source = meta.get("source")
    if source and meta.get("chunk_index") is not None:
        return f"{source}#{meta.get('chunk_index')}"
    return f"chunk:{fallback_index}"


def md5_of_file(path: Path) -> str:
    """文件 MD5（与生产 _file_hash 一致）。"""
    hasher = hashlib.md5()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(8192), b""):
            hasher.update(block)
    return hasher.hexdigest()


def load_retrieval_config(
    overrides: Optional[Mapping[str, Any]] = None,
) -> RetrievalConfig:
    """读取 config.yaml 的 knowledge_base.retrieval；`overrides` 浅覆盖（含 thresholds 子段）。"""
    data: Dict[str, Any] = {}
    try:
        from utils.config import config as app_config

        data = dict(app_config.get("knowledge_base.retrieval", {}) or {})
    except Exception:
        data = {}
    if overrides:
        for key, value in overrides.items():
            if key == "thresholds" and isinstance(value, Mapping):
                merged = dict(data.get("thresholds") or {})
                merged.update(value)
                data["thresholds"] = merged
            else:
                data[key] = value
    return RetrievalConfig.from_mapping(data)


def _get_embeddings() -> Any:
    """进程内共享的本地嵌入模型（离线加载；缺失时明确报错）。"""
    global _EMBEDDINGS_CACHE
    if _EMBEDDINGS_CACHE is None:
        from langchain_huggingface import HuggingFaceEmbeddings

        _EMBEDDINGS_CACHE = HuggingFaceEmbeddings(
            model_name=EMBEDDING_MODEL_NAME,
            model_kwargs={"device": "cpu", "local_files_only": True},
            encode_kwargs={"normalize_embeddings": True},
        )
    return _EMBEDDINGS_CACHE


def _default_store_factory(collection_name: str, persist_dir: str) -> Any:
    from langchain_chroma import Chroma

    return Chroma(
        collection_name=collection_name,
        embedding_function=_get_embeddings(),
        persist_directory=persist_dir,
    )


def _make_document(text: str, metadata: Mapping[str, Any]) -> Any:
    from langchain_core.documents import Document

    return Document(page_content=text, metadata=dict(metadata))


def read_corpus_texts(corpus_dir: Path) -> List[Tuple[Path, str]]:
    """读取语料目录下全部 *.md（UTF-8 原文；两版共用同一输入）。"""
    return [(path, path.read_text(encoding="utf-8")) for path in sorted(Path(corpus_dir).glob("*.md"))]


class LegacyPipeline:
    """旧版基线：fixed 切块 + 纯稠密 top-k（无阈值/无引用/无拒答）。"""

    name = "legacy"

    def __init__(self, index_dir: Path, *, store_factory: Optional[StoreFactory] = None) -> None:
        self.index_dir = Path(index_dir)
        self._store_factory = store_factory
        self.store: Any = None
        self.chunk_count = 0

    def _make_store(self) -> Any:
        self.index_dir.mkdir(parents=True, exist_ok=True)
        factory = self._store_factory or _default_store_factory
        return factory(LEGACY_COLLECTION, str(self.index_dir))

    def build_index(self, corpus_dir: Path) -> int:
        """建索引：逐文档 fixed 切块（500/50）后写入向量库；返回块数。"""
        from utils.doc_parser import split_text

        shutil.rmtree(self.index_dir, ignore_errors=True)  # 重建即清空，避免重复写入
        store = self._make_store()
        documents: List[Any] = []
        for path, raw in read_corpus_texts(corpus_dir):
            pieces = split_text(raw, chunk_size=LEGACY_CHUNK_SIZE, overlap=LEGACY_CHUNK_OVERLAP)
            for index, piece in enumerate(pieces, start=1):
                documents.append(
                    _make_document(
                        piece,
                        {
                            "source": path.name,
                            "file_path": str(path),
                            "chunk_index": index,
                            "total_chunks": len(pieces),
                            "chunk_size": len(piece),
                        },
                    )
                )
        if documents:
            store.add_documents(documents)
        self.store = store
        self.chunk_count = len(documents)
        return self.chunk_count

    def retrieve(self, query: str, k: int = 5) -> Tuple[List[Tuple[str, str]], bool]:
        """纯稠密检索 top-k；旧版无拒答能力（恒 False）。"""
        docs = self.store.similarity_search(query, k=k)
        hits = [(str(d.metadata.get("source", "未知")), d.page_content) for d in docs]
        return hits, False


class HybridPipeline:
    """新版：语义切块 + HybridRetriever（BM25 + Dense + RRF + 阈值门）。"""

    name = "hybrid"

    def __init__(
        self,
        index_dir: Path,
        *,
        store_factory: Optional[StoreFactory] = None,
        retrieval_config: Optional[RetrievalConfig] = None,
        reranker: Optional[Any] = None,
    ) -> None:
        self.index_dir = Path(index_dir)
        self._store_factory = store_factory
        self.retrieval_config = retrieval_config or load_retrieval_config()
        self.reranker = reranker
        self.store: Any = None
        self.retriever: Optional[HybridRetriever] = None
        self.chunk_count = 0
        self._version = 0

    def _make_store(self) -> Any:
        self.index_dir.mkdir(parents=True, exist_ok=True)
        factory = self._store_factory or _default_store_factory
        return factory(HYBRID_COLLECTION, str(self.index_dir))

    def build_index(self, corpus_dir: Path) -> int:
        """建索引：逐文档语义切块（含 section/page/chunk_uid 元数据）后写入向量库。"""
        from utils.chunking import split_semantic

        shutil.rmtree(self.index_dir, ignore_errors=True)  # 重建即清空，避免重复写入
        store = self._make_store()
        documents: List[Any] = []
        for path, raw in read_corpus_texts(corpus_dir):
            file_hash = md5_of_file(path)
            chunks = split_semantic(raw, doc_id=file_hash, max_size=500, overlap=50)
            for chunk in chunks:
                metadata: Dict[str, Any] = {
                    "source": path.name,
                    "file_path": str(path),
                    "chunk_index": chunk.index,
                    "total_chunks": len(chunks),
                    "file_hash": file_hash,
                    "chunk_size": len(chunk.text),
                    "chunk_uid": chunk.uid,
                }
                if chunk.section:
                    metadata["section"] = chunk.section
                if chunk.page is not None:
                    metadata["page"] = int(chunk.page)
                documents.append(_make_document(chunk.text, metadata))
        if documents:
            store.add_documents(documents)
        self.store = store
        self.chunk_count = len(documents)
        self._version += 1
        self.retriever = HybridRetriever(
            dense_search=self._dense_search,
            corpus_fn=self._corpus_snapshot,
            reranker=self.reranker,
            config=self.retrieval_config,
        )
        return self.chunk_count

    # ---- HybridRetriever 依赖注入 ----

    def _dense_search(self, query: str, k: int) -> List[Tuple[str, str, Mapping[str, Any], float]]:
        pairs = self.store.similarity_search_with_score(query, k=k)
        hits: List[Tuple[str, str, Mapping[str, Any], float]] = []
        for i, (doc, distance) in enumerate(pairs):
            meta = dict(doc.metadata or {})
            hits.append(
                (
                    canonical_chunk_id(meta, fallback_index=i),
                    doc.page_content,
                    meta,
                    distance_to_similarity(distance),
                )
            )
        return hits

    def _corpus_snapshot(self) -> Tuple[int, List[Tuple[str, str, Mapping[str, Any]]]]:
        data = self.store.get()
        ids = data.get("ids") or []
        docs = data.get("documents") or []
        metas = data.get("metadatas") or []
        entries: List[Tuple[str, str, Mapping[str, Any]]] = []
        for i, _chroma_id in enumerate(ids):
            text = docs[i] if i < len(docs) else ""
            meta = dict(metas[i] or {}) if i < len(metas) else {}
            entries.append((canonical_chunk_id(meta, fallback_index=i), text or "", meta))
        return self._version, entries

    def retrieve(self, query: str, k: Optional[int] = None) -> Tuple[List[Tuple[str, str]], bool]:
        """混合检索；拒答时返回 ([], True)（被拒条目在指标中计为未命中）。"""
        assert self.retriever is not None, "build_index() 必须先执行"
        result = self.retriever.retrieve(query, top_k=k)
        if result.refused:
            return [], True
        hits = [(str(h.metadata.get("source", "未知")), h.text) for h in result.hits]
        return hits, False


def build_pipelines(
    index_dir: Path,
    *,
    store_factory: Optional[StoreFactory] = None,
    retrieval_config: Optional[RetrievalConfig] = None,
) -> Tuple[LegacyPipeline, HybridPipeline]:
    """一次性构建两条管线（共享索引根目录，各自子目录）。"""
    legacy = LegacyPipeline(Path(index_dir) / "legacy", store_factory=store_factory)
    hybrid = HybridPipeline(
        Path(index_dir) / "hybrid",
        store_factory=store_factory,
        retrieval_config=retrieval_config,
    )
    return legacy, hybrid
