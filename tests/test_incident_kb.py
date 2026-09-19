"""incident_kb 单测：适配器换算、拒绝透传、真实 HybridRetriever 组合、历史读取。"""

from dataclasses import dataclass

from agents.incident_agent import analyze_incident
from agents.incident_kb import RetrievalKbAdapter, history_from_store
from utils.incident_models import EvidenceKind, ReportStatus
from utils.retrieval import HybridRetriever, RetrievalConfig


@dataclass
class _Hit:
    id: str
    text: str
    metadata: dict
    fused_score: float = 0.0
    dense_similarity: float | None = None
    bm25_score: float | None = None


@dataclass
class _Result:
    hits: list
    refused: bool = False


class FakeHybrid:
    def __init__(self, result: _Result) -> None:
        self.result = result
        self.calls: list = []

    def retrieve(self, query: str, top_k: int | None = None) -> _Result:
        self.calls.append((query, top_k))
        return self.result


def test_adapter_converts_hits() -> None:
    fake = FakeHybrid(
        _Result(
            hits=[
                _Hit(
                    "c1",
                    "磁盘空间不足处置：清理过期日志",
                    {"source": "磁盘指南", "chunk_uid": "u1"},
                    dense_similarity=0.88,
                )
            ]
        )
    )
    hits = RetrievalKbAdapter(fake).search("磁盘满", k=3)
    assert len(hits) == 1
    assert hits[0].doc == "磁盘指南"
    assert abs(hits[0].score - 0.88) < 1e-9
    assert hits[0].chunk_uid == "u1"
    assert fake.calls == [("磁盘满", 3)]


def test_adapter_refused_returns_empty() -> None:
    fake = FakeHybrid(_Result(hits=[], refused=True))
    assert RetrievalKbAdapter(fake).search("x") == []


def test_adapter_limits_k() -> None:
    hits = [_Hit(f"c{i}", f"文本 {i}", {"source": f"doc{i}"}, dense_similarity=0.9) for i in range(5)]
    adapter = RetrievalKbAdapter(FakeHybrid(_Result(hits=hits)))
    assert len(adapter.search("q", k=2)) == 2


def test_adapter_bm25_only_uses_medium_score() -> None:
    fake = FakeHybrid(_Result(hits=[_Hit("c1", "文本", {"source": "doc"}, bm25_score=12.0)]))
    hits = RetrievalKbAdapter(fake).search("q")
    assert hits[0].score == 0.6
    assert hits[0].doc == "doc"


def test_adapter_missing_source_falls_back_to_知识库() -> None:
    fake = FakeHybrid(_Result(hits=[_Hit("c1", "文本", {}, dense_similarity=0.7)]))
    hits = RetrievalKbAdapter(fake).search("q")
    assert hits[0].doc == "知识库"


def dense_stub(query: str, k: int) -> list:
    return [("c1", "磁盘空间不足处置：清理过期日志并扩容", {"source": "磁盘指南", "chunk_uid": "u1"}, 0.9)]


def test_adapter_over_real_hybrid_retriever_and_analyze() -> None:
    config = RetrievalConfig(hybrid_enabled=False, min_dense_similarity=0.5)
    retriever = HybridRetriever(dense_search=dense_stub, corpus_fn=None, config=config)
    adapter = RetrievalKbAdapter(retriever)

    hits = adapter.search("磁盘满了怎么办", k=3)
    assert hits and hits[0].doc == "磁盘指南"

    report = analyze_incident(
        log_text="2026-09-18 09:12:44 [FATAL] No space left on device - /data",
        retriever=adapter,
    )
    assert report.status == ReportStatus.ok
    assert "《磁盘指南》" in report.citations


def test_history_from_store_filters_and_maps() -> None:
    class FakeStore:
        def list_incidents(self, limit: int = 50) -> list:
            return [
                {"id": "INC-1", "service": "oa", "root_cause": "disk_full", "root_title": "服务器磁盘空间不足"},
                {"id": "INC-2", "service": "", "root_cause": "", "root_title": ""},
                {"id": "INC-3", "service": "db", "root_cause": "oom", "root_title": "OOM"},
            ]

    entries = history_from_store(FakeStore(), limit=10)
    assert [entry["incident_id"] for entry in entries] == ["INC-1", "INC-3"]
    assert entries[0]["root_cause"]["cause_id"] == "disk_full"
    assert entries[0]["service"] == "oa"


def test_history_from_store_excludes_current_and_survives_broken_store() -> None:
    class FakeStore:
        def list_incidents(self, limit: int = 50) -> list:
            return [{"id": "INC-X", "service": "", "root_cause": "disk_full", "root_title": "t"}]

    assert history_from_store(FakeStore(), exclude_id="INC-X") == []

    class BrokenStore:
        def list_incidents(self, limit: int = 50) -> list:
            raise RuntimeError("db down")

    assert history_from_store(BrokenStore()) == []


def test_history_same_cause_used_once_in_agent() -> None:
    history = [
        {"incident_id": "INC-OLD-1", "root_cause": {"cause_id": "disk_full"}},
        {"incident_id": "INC-OLD-2", "root_cause": {"cause_id": "disk_full"}},
    ]
    report = analyze_incident(log_text="2026-09-18 [FATAL] No space left on device", history=history)
    matches = [item for item in report.evidence if item.kind == EvidenceKind.history_match]
    assert len(matches) == 1
    assert report.meta["history_matched"] == 1
