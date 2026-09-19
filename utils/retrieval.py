"""混合检索核心：查询规范化 → BM25 + Dense → RRF 融合 → 阈值判定 → 去重限流
→（可选）重排序。

设计原则:
- 纯 Python、零重依赖：不 import chromadb/torch；dense 检索与语料均由调用方
  注入（依赖注入），因此可在 CI core 环境与离线单测中完整运行
- 逐级降级：dense 失败 → 仅 BM25；BM25 不可得 → 仅 dense；两者都弱 → 明确拒答
- 与上层解耦：返回结构化 RetrievalResult（含分数、来源、调试信息），供
  knowledge_agent 引用渲染与第 3 周评测直接消费
"""

import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from utils.bm25 import BM25Index

DEFAULT_REFUSE_MESSAGE = "抱歉，知识库中未找到相关信息，请补充相关文档后重试。"

# 查询开头的列表符号等噪声字符（规范化时剥离）
_LEADING_NOISE = "-•*#>·◦▪\u2022\u25cf\u25aa "

# 类型别名（依赖注入的三个口子）
DenseSearchFn = Callable[[str, int], Sequence[Tuple[str, str, Mapping[str, Any], float]]]
CorpusFn = Callable[[], Tuple[Any, Sequence[Tuple[str, str, Mapping[str, Any]]]]]
RerankerFn = Callable[[str, List["RetrievalHit"], int], List["RetrievalHit"]]


def normalize_query(query: str) -> str:
    """查询规范化：NFKC（全角→半角）、去零宽字符、压缩空白、剥离开头列表符号。"""
    if not query:
        return ""
    q = unicodedata.normalize("NFKC", query)
    q = q.replace("\u200b", "").replace("\ufeff", "")
    q = " ".join(q.split())
    q = q.lstrip(_LEADING_NOISE)
    return q.strip()


def reciprocal_rank_fusion(rankings: Sequence[Sequence[str]], k: int = 60) -> Dict[str, float]:
    """RRF 融合：score(id) = Σ 1 / (k + rank)，rank 从 1 起。输入为按名次排序的 id 列表。"""
    fused: Dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            fused[doc_id] = fused.get(doc_id, 0.0) + 1.0 / (k + rank)
    return fused


def _normalized_text(text: str) -> str:
    return " ".join((text or "").split()).lower()


@dataclass
class RetrievalHit:
    """统一检索命中项。dense_similarity 为 [0,1] 余弦相似度（适配器负责换算）。"""

    id: str
    text: str
    metadata: Mapping[str, Any] = field(default_factory=dict)
    fused_score: float = 0.0
    dense_similarity: Optional[float] = None
    bm25_score: Optional[float] = None


@dataclass
class RetrievalResult:
    """检索结果：hits 为最终候选（拒答时仍保留证据供调试），refused 表示证据不足。"""

    hits: List[RetrievalHit] = field(default_factory=list)
    refused: bool = False
    refuse_message: Optional[str] = None
    query: str = ""
    debug: Dict[str, Any] = field(default_factory=dict)


def dedupe_hits(
    hits: Sequence[RetrievalHit], max_per_document: Optional[int] = 2
) -> List[RetrievalHit]:
    """去重（归一化文本相同者保留首个）+ 按来源文档限流（max_per_document，None 不限）。"""
    out: List[RetrievalHit] = []
    seen_texts: set = set()
    per_doc: Dict[str, int] = {}
    for hit in hits:
        key = _normalized_text(hit.text)
        if key and key in seen_texts:
            continue
        src = hit.metadata.get("source")
        if src is not None and max_per_document is not None:
            src_key = str(src)
            if per_doc.get(src_key, 0) >= max_per_document:
                continue
            per_doc[src_key] = per_doc.get(src_key, 0) + 1
        if key:
            seen_texts.add(key)
        out.append(hit)
    return out


def apply_evidence_gate(
    dense_top: Optional[float],
    bm25_top: Optional[float],
    min_dense_similarity: float,
    min_bm25_score: float,
) -> bool:
    """证据阈值门：任一可用通道达到其阈值即视为证据充分；两通道都不可用则拒答。

    规则: dense 通道看余弦相似度（≥ min_dense_similarity），BM25 通道看原始分
    （≥ min_bm25_score，第 3 周评测集校准）；只有一个通道有数据时由该通道裁决。
    """
    checks: List[bool] = []
    if dense_top is not None:
        checks.append(dense_top >= min_dense_similarity)
    if bm25_top is not None:
        checks.append(bm25_top >= min_bm25_score)
    return any(checks)


def format_citations(hits: Sequence[RetrievalHit]) -> str:
    """把命中渲染为带精确引用的上下文（LLM Prompt 与降级展示共用）。

    格式: [参考资料N] 《source》 · 第X页 · §章节 · chunk_uid（缺项自动跳过）
    """
    parts: List[str] = []
    for i, hit in enumerate(hits, 1):
        meta = hit.metadata or {}
        source = meta.get("source", "未知")
        loc: List[str] = []
        page = meta.get("page")
        if isinstance(page, int) and page > 0:
            loc.append(f"第{page}页")
        section = meta.get("section")
        if section:
            loc.append(f"§{section}")
        uid = meta.get("chunk_uid")
        if uid:
            loc.append(str(uid))
        elif meta.get("chunk_index") is not None:
            loc.append(f"块#{meta.get('chunk_index')}")
        suffix = (" · " + " · ".join(loc)) if loc else ""
        parts.append(f"[参考资料{i}] 《{source}》{suffix}\n{hit.text}")
    return "\n\n".join(parts)


@dataclass
class RetrievalConfig:
    """检索配置（config.yaml → knowledge_base.retrieval 段）。"""

    mode: str = "lite"
    hybrid_enabled: bool = True
    dense_top_k: int = 10
    bm25_top_k: int = 10
    rrf_k: int = 60
    final_top_k: int = 5
    max_per_document: Optional[int] = 2
    rerank: bool = False
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    min_dense_similarity: float = 0.35
    min_bm25_score: float = 0.30
    refuse_message: str = DEFAULT_REFUSE_MESSAGE

    @classmethod
    def from_mapping(cls, data: Optional[Mapping[str, Any]]) -> "RetrievalConfig":
        """从嵌套 dict（含 thresholds 子段）构建；未知键忽略，缺省回退默认值。"""
        data = dict(data or {})
        thresholds = dict(data.pop("thresholds", None) or {})
        cfg = cls()
        for key, value in data.items():
            if value is not None and hasattr(cfg, key):
                setattr(cfg, key, value)
        if thresholds.get("min_dense_similarity") is not None:
            cfg.min_dense_similarity = float(thresholds["min_dense_similarity"])
        if thresholds.get("min_bm25_score") is not None:
            cfg.min_bm25_score = float(thresholds["min_bm25_score"])
        if thresholds.get("refuse_message"):
            cfg.refuse_message = str(thresholds["refuse_message"])
        return cfg


class HybridRetriever:
    """混合检索器。三个依赖注入点:
    - dense_search(query, k) -> [(id, text, metadata, similarity)]
    - corpus_fn() -> (version, [(id, text, metadata)])   # version 变化触发 BM25 重建
    - reranker(query, hits, top_k) -> hits               # 可选
    """

    def __init__(
        self,
        dense_search: Optional[DenseSearchFn] = None,
        corpus_fn: Optional[CorpusFn] = None,
        reranker: Optional[RerankerFn] = None,
        config: Optional[RetrievalConfig] = None,
    ):
        self.dense_search = dense_search
        self.corpus_fn = corpus_fn
        self.reranker = reranker
        self.config = config or RetrievalConfig()
        self._bm25: Optional[BM25Index] = None
        self._corpus_version: Any = None
        self._corpus_map: Dict[str, Tuple[str, Mapping[str, Any]]] = {}

    def invalidate(self) -> None:
        """知识库变更（导入/删除/清空）后由调用方触发，强制下次重建 BM25。"""
        self._bm25 = None
        self._corpus_version = None
        self._corpus_map = {}

    def _ensure_bm25(self, debug: Dict[str, Any]) -> Optional[BM25Index]:
        if not self.config.hybrid_enabled or self.corpus_fn is None:
            return None
        try:
            version, entries = self.corpus_fn()
        except Exception as exc:  # 语料不可得 → 跳过 BM25 通道
            debug["corpus_error"] = str(exc)
            return None
        debug["corpus_version"] = version
        if self._bm25 is not None and version == self._corpus_version:
            return self._bm25
        docs = [(str(eid), text or "") for eid, text, _ in entries]
        self._bm25 = BM25Index(docs)
        self._corpus_version = version
        self._corpus_map = {str(eid): (text or "", dict(meta or {})) for eid, text, meta in entries}
        debug["bm25_corpus_size"] = len(docs)
        return self._bm25

    def retrieve(self, query: str, top_k: Optional[int] = None) -> RetrievalResult:
        cfg = self.config
        debug: Dict[str, Any] = {}

        q = normalize_query(query)
        if not q:
            return RetrievalResult(hits=[], refused=False, query=q, debug={"empty_query": True})

        # ---- 通道 1：dense ----
        dense_hits: List[RetrievalHit] = []
        if self.dense_search is not None:
            try:
                for eid, text, meta, sim in self.dense_search(q, cfg.dense_top_k):
                    dense_hits.append(
                        RetrievalHit(
                            id=str(eid),
                            text=text or "",
                            metadata=dict(meta or {}),
                            dense_similarity=float(sim),
                        )
                    )
            except Exception as exc:
                debug["dense_error"] = str(exc)
        debug["dense_count"] = len(dense_hits)

        # ---- 通道 2：BM25 ----
        bm25_hits: List[RetrievalHit] = []
        bm25_index = self._ensure_bm25(debug)
        if bm25_index is not None and bm25_index.size > 0:
            for eid, score in bm25_index.search(q, cfg.bm25_top_k):
                text, meta = self._corpus_map.get(str(eid), ("", {}))
                bm25_hits.append(
                    RetrievalHit(id=str(eid), text=text, metadata=dict(meta), bm25_score=float(score))
                )
        debug["bm25_count"] = len(bm25_hits)

        # ---- RRF 融合（同 id 合并，保留两通道分数）----
        rankings: List[List[str]] = []
        if dense_hits:
            rankings.append([h.id for h in dense_hits])
        if bm25_hits:
            rankings.append([h.id for h in bm25_hits])
        fused = reciprocal_rank_fusion(rankings, cfg.rrf_k) if rankings else {}

        merged: Dict[str, RetrievalHit] = {}
        for hit in dense_hits:
            merged[hit.id] = hit
        for hit in bm25_hits:
            if hit.id in merged:
                merged[hit.id].bm25_score = hit.bm25_score
                if not merged[hit.id].text:
                    merged[hit.id].text = hit.text
                if not merged[hit.id].metadata:
                    merged[hit.id].metadata = hit.metadata
            else:
                merged[hit.id] = hit
        ordered = sorted(merged.values(), key=lambda h: (-fused.get(h.id, 0.0), h.id))
        for hit in ordered:
            hit.fused_score = fused.get(hit.id, 0.0)

        # ---- 证据阈值门 ----
        dense_top = max(
            (h.dense_similarity for h in dense_hits if h.dense_similarity is not None),
            default=None,
        )
        bm25_top = max(
            (h.bm25_score for h in bm25_hits if h.bm25_score is not None), default=None
        )
        debug["dense_top_similarity"] = dense_top
        debug["bm25_top_score"] = bm25_top
        debug["fused_count"] = len(ordered)

        sufficient = bool(ordered) and apply_evidence_gate(
            dense_top, bm25_top, cfg.min_dense_similarity, cfg.min_bm25_score
        )
        if not sufficient:
            return RetrievalResult(
                hits=dedupe_hits(ordered, cfg.max_per_document),
                refused=True,
                refuse_message=cfg.refuse_message,
                query=q,
                debug=debug,
            )

        # ---- 去重限流 →（可选）重排序 → top_k ----
        deduped = dedupe_hits(ordered, cfg.max_per_document)
        limit = int(top_k) if top_k is not None else cfg.final_top_k
        if self.reranker is not None:
            try:
                deduped = list(self.reranker(q, deduped, limit))
            except Exception as exc:
                debug["rerank_error"] = str(exc)
        return RetrievalResult(
            hits=deduped[:limit], refused=False, refuse_message=None, query=q, debug=debug
        )
