"""评测管线测试（完全离线）：工厂注入 fake 存储，不下载模型、不联网。

覆盖：距离→相似度换算、块 ID 规则、md5、旧版管线建库/检索、
新版管线建库/检索（含拒答路径）、配置覆盖加载、双管线组装。
"""

from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

import pytest

from evals.pipelines import (
    HybridPipeline,
    LegacyPipeline,
    build_pipelines,
    canonical_chunk_id,
    distance_to_similarity,
    load_retrieval_config,
    md5_of_file,
)
from utils.chunking import split_semantic
from utils.retrieval import RetrievalConfig

StoreFactory = Callable[[str, str], Any]


class FakeStore:
    """最小存储替身：插入顺序即检索顺序，距离 0（相似度 1.0）。"""

    def __init__(self) -> None:
        self.docs: List[Any] = []

    def add_documents(self, documents: List[Any]) -> None:
        self.docs.extend(documents)

    def similarity_search(self, query: str, k: int = 5) -> List[Any]:
        return list(self.docs)[:k]

    def similarity_search_with_score(self, query: str, k: int = 10) -> List[Tuple[Any, float]]:
        return [(doc, 0.0) for doc in list(self.docs)[:k]]

    def get(self) -> Dict[str, List[Any]]:
        return {
            "ids": [f"id{i}" for i in range(len(self.docs))],
            "documents": [doc.page_content for doc in self.docs],
            "metadatas": [dict(doc.metadata) for doc in self.docs],
        }


def _factory_for(stores: Dict[str, FakeStore]) -> StoreFactory:
    def factory(collection_name: str, persist_dir: str) -> FakeStore:
        return stores.setdefault(collection_name, FakeStore())

    return factory


def _write_corpus(tmp_path: Path) -> Path:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "排查手册.md").write_text(
        "# 故障排查\n\n第一步：检查服务进程是否存在，使用 systemctl status oa-portal 查看。\n"
        "\n## 日志检查\n\n第二步：查看 /opt/tomcat/logs/catalina.out 的最后 200 行。",
        encoding="utf-8",
    )
    (corpus / "备份手册.md").write_text(
        "# 备份说明\n\n每日凌晨 2 点执行全量备份，保留 7 天，备份文件位于 /data/backup。",
        encoding="utf-8",
    )
    return corpus


# ---------- 纯函数 ----------

def test_distance_to_similarity() -> None:
    assert distance_to_similarity(0.0) == pytest.approx(1.0)
    assert distance_to_similarity(1.0) == pytest.approx(0.5)
    assert distance_to_similarity(2 ** 0.5) == pytest.approx(0.0, abs=1e-9)
    assert distance_to_similarity(3.0) == pytest.approx(-1.0)
    assert distance_to_similarity("bad") == 0.0


def test_canonical_chunk_id_rules() -> None:
    assert canonical_chunk_id({"chunk_uid": "abc:1"}) == "abc:1"
    assert canonical_chunk_id({"source": "手册.md", "chunk_index": 3}) == "手册.md#3"
    assert canonical_chunk_id({}, fallback_index=5) == "chunk:5"


def test_md5_of_file_matches_hashlib(tmp_path: Path) -> None:
    import hashlib

    path = tmp_path / "a.md"
    path.write_text("内容", encoding="utf-8")
    assert md5_of_file(path) == hashlib.md5(path.read_bytes()).hexdigest()


def test_load_retrieval_config_threshold_overrides() -> None:
    config = load_retrieval_config({"thresholds": {"min_dense_similarity": 0.9}})
    assert config.min_dense_similarity == pytest.approx(0.9)
    assert config.final_top_k >= 1


# ---------- 旧版管线 ----------

def test_legacy_build_and_retrieve(tmp_path: Path) -> None:
    corpus = _write_corpus(tmp_path)
    stores: Dict[str, FakeStore] = {}
    pipeline = LegacyPipeline(tmp_path / "idx", store_factory=_factory_for(stores))

    chunk_count = pipeline.build_index(corpus)
    assert chunk_count > 0
    assert chunk_count == len(stores["eval_legacy"].docs)

    metas = [dict(doc.metadata) for doc in stores["eval_legacy"].docs]
    assert {m["source"] for m in metas} == {"排查手册.md", "备份手册.md"}
    assert all(m["chunk_index"] >= 1 for m in metas)
    assert all(m["chunk_size"] == len(doc.page_content) for m, doc in zip(metas, stores["eval_legacy"].docs))
    assert "chunk_uid" not in metas[0]

    hits, refused = pipeline.retrieve("磁盘满怎么处理", k=2)
    assert refused is False
    assert len(hits) == 2
    assert hits[0][0] in {"排查手册.md", "备份手册.md"}


# ---------- 新版管线 ----------

def test_hybrid_build_and_retrieve(tmp_path: Path) -> None:
    corpus = _write_corpus(tmp_path)
    stores: Dict[str, FakeStore] = {}
    config = RetrievalConfig(min_dense_similarity=0.0, min_bm25_score=0.0)
    pipeline = HybridPipeline(
        tmp_path / "idx", store_factory=_factory_for(stores), retrieval_config=config
    )

    chunk_count = pipeline.build_index(corpus)
    assert chunk_count > 0
    stored = stores["eval_hybrid"].docs
    assert all(dict(doc.metadata).get("chunk_uid") for doc in stored)

    # 与直接切块结果一致（uid 级对齐）
    raw = (corpus / "排查手册.md").read_text(encoding="utf-8")
    expected = split_semantic(raw, doc_id=md5_of_file(corpus / "排查手册.md"), max_size=500, overlap=50)
    stored_uids = [m["chunk_uid"] for m in (dict(doc.metadata) for doc in stored) if m["source"] == "排查手册.md"]
    assert stored_uids == [chunk.uid for chunk in expected]

    hits, refused = pipeline.retrieve("catalina.out 在哪里查看")
    assert refused is False
    assert hits and hits[0][0] in {"排查手册.md", "备份手册.md"}


def test_hybrid_refusal_gate(tmp_path: Path) -> None:
    corpus = _write_corpus(tmp_path)
    stores: Dict[str, FakeStore] = {}
    strict = RetrievalConfig(min_dense_similarity=2.0, min_bm25_score=999.0)
    pipeline = HybridPipeline(
        tmp_path / "idx", store_factory=_factory_for(stores), retrieval_config=strict
    )
    pipeline.build_index(corpus)

    hits, refused = pipeline.retrieve("完全无关的问题")
    assert refused is True
    assert hits == []


def test_build_pipelines_wires_distinct_dirs(tmp_path: Path) -> None:
    stores: Dict[str, FakeStore] = {}
    legacy, hybrid = build_pipelines(
        tmp_path / "idx", store_factory=_factory_for(stores), retrieval_config=RetrievalConfig()
    )
    assert isinstance(legacy, LegacyPipeline)
    assert isinstance(hybrid, HybridPipeline)
    assert legacy.index_dir != hybrid.index_dir


def test_rebuild_clears_index_dir(tmp_path: Path) -> None:
    """重复 build_index 必须先清空目录（防止向量库重复写入）。"""
    corpus = _write_corpus(tmp_path)
    stores: Dict[str, FakeStore] = {}
    pipeline = LegacyPipeline(tmp_path / "idx", store_factory=_factory_for(stores))
    pipeline.build_index(corpus)
    marker = tmp_path / "idx" / "marker.txt"
    marker.write_text("x", encoding="utf-8")
    pipeline.build_index(corpus)
    assert not marker.exists()


def test_hybrid_custom_collection_name(tmp_path: Path) -> None:
    """可指定集合名（LLM 子集需要对接生产 Agent 的 oa_knowledge_base）。"""
    corpus = _write_corpus(tmp_path)
    stores: Dict[str, FakeStore] = {}
    pipeline = HybridPipeline(
        tmp_path / "idx",
        store_factory=_factory_for(stores),
        retrieval_config=RetrievalConfig(),
        collection_name="oa_knowledge_base",
    )
    pipeline.build_index(corpus)
    assert "oa_knowledge_base" in stores
    assert "eval_hybrid" not in stores
