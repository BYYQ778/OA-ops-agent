"""混合检索核心模块测试（完全离线）。

覆盖：查询规范化、RRF 融合、去重与文档限流、证据阈值门、
HybridRetriever 全链路（含降级、缓存失效、重排序注入、异常回退）。
"""

from utils.retrieval import (
    HybridRetriever,
    RetrievalConfig,
    RetrievalHit,
    apply_evidence_gate,
    dedupe_hits,
    normalize_query,
    reciprocal_rank_fusion,
)

# ========== 查询规范化 ==========

def test_normalize_collapses_whitespace() -> None:
    assert normalize_query("  如何  排查   502  ") == "如何 排查 502"
    assert normalize_query("a\tb\nc") == "a b c"


def test_normalize_strips_leading_bullets() -> None:
    assert normalize_query("- 如何排查502") == "如何排查502"
    assert normalize_query("• OOM 怎么处理") == "OOM 怎么处理"


def test_normalize_fullwidth_to_halfwidth() -> None:
    assert normalize_query("（ＯＡ）系统") == "(OA)系统"


def test_normalize_removes_zero_width() -> None:
    assert normalize_query("如\u200b何") == "如何"


def test_normalize_empty() -> None:
    assert normalize_query("") == ""
    assert normalize_query("  \u200b ") == ""


# ========== RRF 融合 ==========

def test_rrf_merges_and_reorders() -> None:
    fused = reciprocal_rank_fusion([["a", "b"], ["b", "c"]], k=60)
    order = sorted(fused, key=lambda i: (-fused[i], i))
    assert order == ["b", "a", "c"]
    assert abs(fused["a"] - 1 / 61) < 1e-9
    assert abs(fused["b"] - (1 / 62 + 1 / 61)) < 1e-9
    assert abs(fused["c"] - 1 / 62) < 1e-9


def test_rrf_single_ranking_preserves_order() -> None:
    fused = reciprocal_rank_fusion([["x", "y", "z"]], k=60)
    order = sorted(fused, key=lambda i: (-fused[i], i))
    assert order == ["x", "y", "z"]


def test_rrf_empty() -> None:
    assert reciprocal_rank_fusion([], k=60) == {}
    assert reciprocal_rank_fusion([[], []], k=60) == {}


def test_rrf_k_parameter_effect() -> None:
    fused_small_k = reciprocal_rank_fusion([["a"], ["b"]], k=1)
    fused_large_k = reciprocal_rank_fusion([["a"], ["b"]], k=1000)
    # k 越小头部权重越大；两者分数都应 > 0 且随 k 增大而减小
    assert fused_small_k["a"] > fused_large_k["a"]
    assert fused_large_k["a"] > 0


# ========== 去重与文档限流 ==========

def _hit(hid: str, text: str, source: str | None = None) -> RetrievalHit:
    meta = {"source": source} if source else {}
    return RetrievalHit(id=hid, text=text, metadata=meta)


def test_dedupe_removes_normalized_duplicates_keeps_first() -> None:
    hits = [
        _hit("1", "磁盘 空间不足 处理"),
        _hit("2", "磁盘  空间不足  处理 "),
        _hit("3", "完全不同的内容"),
    ]
    out = dedupe_hits(hits, max_per_document=None)
    assert [h.id for h in out] == ["1", "3"]


def test_dedupe_per_document_cap() -> None:
    hits = [
        _hit("1", "a", source="doc.pdf"),
        _hit("2", "b", source="doc.pdf"),
        _hit("3", "c", source="doc.pdf"),
        _hit("4", "d", source="other.pdf"),
    ]
    out = dedupe_hits(hits, max_per_document=2)
    assert [h.id for h in out] == ["1", "2", "4"]


def test_dedupe_no_cap_when_none() -> None:
    hits = [_hit(str(i), f"t{i}", source="doc.pdf") for i in range(5)]
    assert len(dedupe_hits(hits, max_per_document=None)) == 5


def test_dedupe_missing_source_not_capped() -> None:
    hits = [_hit("1", "a"), _hit("2", "b"), _hit("3", "c")]
    assert len(dedupe_hits(hits, max_per_document=1)) == 3


# ========== 证据阈值门 ==========

def test_gate_strong_dense_passes() -> None:
    assert apply_evidence_gate(0.5, None, 0.35, 0.3) is True


def test_gate_strong_bm25_passes_even_if_dense_weak() -> None:
    assert apply_evidence_gate(0.1, 2.0, 0.35, 0.3) is True


def test_gate_both_weak_refuses() -> None:
    assert apply_evidence_gate(0.1, 0.05, 0.35, 0.3) is False


def test_gate_no_channels_refuses() -> None:
    assert apply_evidence_gate(None, None, 0.35, 0.3) is False


def test_gate_boundary_inclusive() -> None:
    assert apply_evidence_gate(0.35, None, 0.35, 0.3) is True
    assert apply_evidence_gate(None, 0.3, 0.35, 0.3) is True


def test_gate_joint_both_moderate_passes() -> None:
    assert apply_evidence_gate(0.6, 8.0, 0.9, 11.0, 0.6, 8.0) is True


def test_gate_joint_requires_both_channels() -> None:
    assert apply_evidence_gate(0.62, 7.9, 0.9, 11.0, 0.6, 8.0) is False
    assert apply_evidence_gate(0.59, 8.1, 0.9, 11.0, 0.6, 8.0) is False


def test_gate_joint_inactive_by_default() -> None:
    assert apply_evidence_gate(0.7, 9.0, 0.9, 11.0) is False


def test_gate_single_strong_still_passes_with_joint_configured() -> None:
    assert apply_evidence_gate(0.95, 1.0, 0.9, 11.0, 0.6, 8.0) is True
    assert apply_evidence_gate(0.1, 12.0, 0.9, 11.0, 0.6, 8.0) is True


def test_config_reads_joint_thresholds() -> None:
    config = RetrievalConfig.from_mapping(
        {
            "thresholds": {
                "min_dense_similarity": 0.9,
                "min_bm25_score": 11.0,
                "joint_dense_similarity": 0.6,
                "joint_bm25_score": 8.0,
            }
        }
    )
    assert config.joint_dense_similarity == 0.6
    assert config.joint_bm25_score == 8.0


def test_retrieve_passes_joint_thresholds(monkeypatch) -> None:
    import importlib

    captured = {}

    def fake_gate(dense_top, bm25_top, min_dense, min_bm25, joint_dense=None, joint_bm25=None):
        captured.update(
            min_dense=min_dense, min_bm25=min_bm25, joint_dense=joint_dense, joint_bm25=joint_bm25
        )
        return True

    module = importlib.import_module("utils.retrieval")
    monkeypatch.setattr(module, "apply_evidence_gate", fake_gate)
    config = RetrievalConfig(
        min_dense_similarity=0.9,
        min_bm25_score=11.0,
        joint_dense_similarity=0.6,
        joint_bm25_score=8.0,
    )
    retriever = HybridRetriever(
        dense_search=lambda query, k: [("a", "text", {"source": "a.md"}, 0.7)],
        corpus_fn=lambda: (1, [("a", "text", {"source": "a.md"})]),
        config=config,
    )
    retriever.retrieve("查询")
    assert captured == {
        "min_dense": 0.9,
        "min_bm25": 11.0,
        "joint_dense": 0.6,
        "joint_bm25": 8.0,
    }


# ========== RetrievalConfig ==========

def test_config_from_mapping_with_defaults() -> None:
    cfg = RetrievalConfig.from_mapping({
        "dense_top_k": 7,
        "thresholds": {"min_dense_similarity": 0.5},
    })
    assert cfg.dense_top_k == 7
    assert cfg.min_dense_similarity == 0.5
    # 未提供项回退默认
    assert cfg.rrf_k == 60
    assert cfg.max_per_document == 2
    assert cfg.refuse_message


# ========== HybridRetriever 全链路 ==========

def _dense_from(specs):
    """构造 fake dense_search：specs = [(id, text, meta, sim)]，按给定顺序返回。"""

    def _search(query: str, k: int):
        return list(specs)[:k]

    return _search


CORPUS = [
    ("c1", "磁盘空间不足请清理", {"source": "ops.pdf"}),
    ("c2", "nginx 502 排查手册", {"source": "web.pdf"}),
    ("c3", "MySQL 连接失败处理", {"source": "db.pdf"}),
]


def _corpus_fn(version=1, corpus=None):
    state = {"version": version, "corpus": corpus or CORPUS, "calls": 0}

    def _fn():
        state["calls"] += 1
        return state["version"], state["corpus"]

    return _fn, state


def test_retriever_fuses_dense_and_bm25() -> None:
    corpus_fn, _ = _corpus_fn()
    dense = _dense_from([
        ("c2", "nginx 502 排查手册", {"source": "web.pdf"}, 0.80),
        ("c1", "磁盘空间不足请清理", {"source": "ops.pdf"}, 0.60),
    ])
    r = HybridRetriever(dense_search=dense, corpus_fn=corpus_fn, config=RetrievalConfig(final_top_k=5))
    result = r.retrieve("磁盘空间不足")
    assert not result.refused
    # c1 在 dense 排 2、bm25 排 1；c2 仅 dense 排 1 → c1 融合分更高
    assert [h.id for h in result.hits][0] == "c1"
    c1 = next(h for h in result.hits if h.id == "c1")
    assert c1.dense_similarity == 0.60
    assert c1.bm25_score is not None and c1.bm25_score > 0
    assert c1.text == "磁盘空间不足请清理"
    assert c1.metadata["source"] == "ops.pdf"


def test_retriever_refuses_when_evidence_weak() -> None:
    corpus_fn, _ = _corpus_fn(corpus=[("x1", "无关内容", {"source": "x.pdf"})])
    dense = _dense_from([("x1", "无关内容", {"source": "x.pdf"}, 0.10)])
    cfg = RetrievalConfig(refuse_message="证据不足，请补充文档。")
    r = HybridRetriever(dense_search=dense, corpus_fn=corpus_fn, config=cfg)
    result = r.retrieve("磁盘空间不足")
    assert result.refused is True
    assert result.refuse_message == "证据不足，请补充文档。"


def test_retriever_empty_result_refuses() -> None:
    corpus_fn, _ = _corpus_fn(corpus=[])
    r = HybridRetriever(dense_search=_dense_from([]), corpus_fn=corpus_fn, config=RetrievalConfig())
    result = r.retrieve("任何问题")
    assert result.refused is True
    assert result.hits == []


def test_retriever_empty_query_short_circuits() -> None:
    corpus_fn, _ = _corpus_fn()
    r = HybridRetriever(dense_search=_dense_from([]), corpus_fn=corpus_fn, config=RetrievalConfig())
    result = r.retrieve("   ")
    assert result.refused is False
    assert result.hits == []
    assert result.debug.get("empty_query") is True


def test_retriever_rebuilds_bm25_on_version_change() -> None:
    corpus_fn, state = _corpus_fn()
    dense = _dense_from([])  # 仅 BM25 通道
    r = HybridRetriever(dense_search=dense, corpus_fn=corpus_fn, config=RetrievalConfig())
    first = r.retrieve("磁盘空间")
    assert any(h.id == "c1" for h in first.hits)
    # 语料变化 + 版本号递增 → 应重建索引
    state["corpus"] = [("n1", "磁盘空间问题已解决", {"source": "new.pdf"})]
    state["version"] = 2
    second = r.retrieve("磁盘空间")
    assert [h.id for h in second.hits] == ["n1"]
    assert state["calls"] >= 2


def test_retriever_dense_only_when_hybrid_disabled() -> None:
    corpus_fn, _ = _corpus_fn()
    dense = _dense_from([("c2", "nginx 502 排查手册", {"source": "web.pdf"}, 0.9)])
    r = HybridRetriever(
        dense_search=dense, corpus_fn=corpus_fn, config=RetrievalConfig(hybrid_enabled=False)
    )
    result = r.retrieve("nginx")
    assert [h.id for h in result.hits] == ["c2"]


def test_retriever_bm25_only_when_no_dense() -> None:
    corpus_fn, _ = _corpus_fn()
    r = HybridRetriever(dense_search=None, corpus_fn=corpus_fn, config=RetrievalConfig())
    result = r.retrieve("MySQL 连接失败")
    assert result.hits and result.hits[0].id == "c3"


def test_retriever_dense_error_degrades_to_bm25() -> None:
    def broken_dense(query: str, k: int):
        raise RuntimeError("vector store down")

    corpus_fn, _ = _corpus_fn()
    r = HybridRetriever(dense_search=broken_dense, corpus_fn=corpus_fn, config=RetrievalConfig())
    result = r.retrieve("磁盘空间")
    assert any(h.id == "c1" for h in result.hits)
    assert result.debug.get("dense_error")


def test_retriever_applies_reranker_and_top_k() -> None:
    corpus_fn, _ = _corpus_fn()
    dense = _dense_from([
        ("c1", "磁盘空间不足请清理", {"source": "ops.pdf"}, 0.7),
        ("c2", "nginx 502 排查手册", {"source": "web.pdf"}, 0.6),
        ("c3", "MySQL 连接失败处理", {"source": "db.pdf"}, 0.5),
    ])

    def fake_reranker(query, hits, top_k):
        return list(reversed(hits))[:top_k]

    r = HybridRetriever(
        dense_search=dense, corpus_fn=corpus_fn, reranker=fake_reranker,
        config=RetrievalConfig(hybrid_enabled=False, final_top_k=2),
    )
    result = r.retrieve("任意问题")
    # dense 顺序 [c1,c2,c3] → 重排序反转后取 2 → [c3,c2]
    assert [h.id for h in result.hits] == ["c3", "c2"]


def test_retriever_per_document_cap_in_pipeline() -> None:
    corpus = [
        ("a1", "磁盘 处理一", {"source": "same.pdf"}),
        ("a2", "磁盘 处理二", {"source": "same.pdf"}),
        ("a3", "磁盘 处理三", {"source": "same.pdf"}),
    ]
    corpus_fn, _ = _corpus_fn(corpus=corpus)
    dense = _dense_from([
        ("a1", "磁盘 处理一", {"source": "same.pdf"}, 0.9),
        ("a2", "磁盘 处理二", {"source": "same.pdf"}, 0.8),
        ("a3", "磁盘 处理三", {"source": "same.pdf"}, 0.7),
    ])
    r = HybridRetriever(
        dense_search=dense, corpus_fn=corpus_fn,
        config=RetrievalConfig(hybrid_enabled=False, max_per_document=2, final_top_k=5),
    )
    result = r.retrieve("磁盘")
    assert [h.id for h in result.hits] == ["a1", "a2"]


def test_retriever_top_k_override() -> None:
    corpus_fn, _ = _corpus_fn()
    dense = _dense_from([
        ("c1", "磁盘空间不足请清理", {"source": "ops.pdf"}, 0.9),
        ("c2", "nginx 502 排查手册", {"source": "web.pdf"}, 0.8),
    ])
    r = HybridRetriever(
        dense_search=dense, corpus_fn=corpus_fn,
        config=RetrievalConfig(hybrid_enabled=False, final_top_k=5),
    )
    assert len(r.retrieve("磁盘 nginx", top_k=1).hits) == 1


def test_retriever_debug_reports_channel_counts() -> None:
    corpus_fn, _ = _corpus_fn()
    dense = _dense_from([("c2", "nginx 502 排查手册", {"source": "web.pdf"}, 0.9)])
    r = HybridRetriever(dense_search=dense, corpus_fn=corpus_fn, config=RetrievalConfig())
    debug = r.retrieve("nginx").debug
    assert debug["dense_count"] == 1
    assert "bm25_count" in debug
    assert "corpus_version" in debug
