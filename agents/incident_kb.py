"""根因诊断的知识库 / 历史故障接入层。

- RetrievalKbAdapter：把 utils.retrieval.HybridRetriever（或兼容对象）适配为
  incident_agent 的 KbRetriever 口子——复用混合检索 + 分层证据门；
  refused=True 时返回空列表（宁缺毋滥，不给诊断硬塞证据）。
- history_from_store：从诊断记录库（Database.list_incidents）读取历史根因，
  产出 analyze_incident(history=...) 所需的结构（鸭子类型，便于测试与解耦）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping

from agents.incident_agent import KB_TOP_K, KbHit

#: 历史故障读取条数上限
HISTORY_LIMIT = 20


class RetrievalKbAdapter:
    """HybridRetriever → KbRetriever 适配器。

    换算规则:
    - 命中→KbHit: doc = metadata["source"]（缺省「知识库」）；
      score = dense_similarity（缺省时给 0.6 的中等强度，代表仅词面命中）；
      chunk_uid 透传（metadata 缺失时为空）。
    """

    def __init__(self, retriever: Any, top_k: int = KB_TOP_K) -> None:
        self._retriever = retriever
        self._top_k = max(1, int(top_k))

    def search(self, query: str, k: int = KB_TOP_K) -> List[KbHit]:
        limit = max(1, min(int(k), self._top_k))
        result = self._retriever.retrieve(query, top_k=limit)
        if getattr(result, "refused", False):
            return []
        hits: List[KbHit] = []
        for hit in list(getattr(result, "hits", None) or [])[:limit]:
            meta: Mapping[str, Any] = getattr(hit, "metadata", None) or {}
            hits.append(
                KbHit(
                    doc=str(meta.get("source") or "知识库"),
                    text=str(getattr(hit, "text", "") or ""),
                    score=_hit_score(hit),
                    chunk_uid=str(meta.get("chunk_uid") or ""),
                )
            )
        return hits


def _hit_score(hit: Any) -> float:
    dense = getattr(hit, "dense_similarity", None)
    if isinstance(dense, (int, float)):
        return float(dense)
    # 仅 BM25 通道命中：无余弦分，给中等强度（词面命中，语义未验证）
    return 0.6


def history_from_store(store: Any, limit: int = HISTORY_LIMIT, exclude_id: str = "") -> List[Dict[str, Any]]:
    """从诊断记录库读取历史根因（仅有确定根因的记录）。

    Args:
        store: 具备 ``list_incidents(limit)`` 的对象（utils.database.Database）
        limit: 读取条数上限
        exclude_id: 排除的记录编号（避免拿当前这条当历史）

    Returns:
        [{"incident_id", "service", "root_cause": {"cause_id", "title"}}, ...]
    """
    try:
        rows = list(store.list_incidents(limit=limit) or [])
    except Exception:  # noqa: BLE001 - 历史不可用属可选增强，静默降级
        return []
    out: List[Dict[str, Any]] = []
    for row in rows:
        cause_id = str(row.get("root_cause") or "")
        if not cause_id:
            continue
        incident_id = str(row.get("id") or "")
        if exclude_id and incident_id == exclude_id:
            continue
        out.append(
            {
                "incident_id": incident_id,
                "service": str(row.get("service") or ""),
                "root_cause": {"cause_id": cause_id, "title": str(row.get("root_title") or "")},
            }
        )
    return out
