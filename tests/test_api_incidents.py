"""第 4 周 /api/v1 路由离线测试：诊断/检索/评测/指标（fake KB 注入，不加载模型）。"""

import pytest
from fastapi.testclient import TestClient

from ui.server import create_app
from utils.metrics import metrics

pytestmark = pytest.mark.allow_hosts(["127.0.0.1", "::1", "localhost"])

DISK_LOG = (
    "2026-09-18 09:12:44 [FATAL] No space left on device - /data/logs/oa.log\n"
    "2026-09-18 09:13:02 [ERROR] write failed: No space left on device (/data/upload)"
)


class _FakeRetrieveHit:
    def __init__(self, text: str, meta: dict, dense: float) -> None:
        self.text = text
        self.metadata = meta
        self.dense_similarity = dense
        self.bm25_score = None
        self.fused_score = 0.0


class _FakeResult:
    def __init__(self, hits: list, refused: bool = False) -> None:
        self.hits = hits
        self.refused = refused
        self.refuse_message = None
        self.query = "q"


class _FakeHybrid:
    def __init__(self, hits: list | None = None, refused: bool = False) -> None:
        self.hits = hits or []
        self.refused = refused

    def retrieve(self, query: str, top_k: int | None = None) -> _FakeResult:
        return _FakeResult(self.hits, self.refused)


class _FakeLLM:
    def invoke(self, prompt: str) -> object:
        return type("R", (), {"content": "磁盘已满导致附件写入失败。"})()


class FakeKbAgent:
    """测试用 KB Agent：暴露 retriever 与 llm，retrieve() 返回结构化检索结果。"""

    def __init__(self, hits: list | None = None, refused: bool = False, llm: object | None = None,
                 rag_refused: bool = False) -> None:
        if hits is None:
            hits = [
                _FakeRetrieveHit(
                    "磁盘空间不足处置：清理过期日志并扩容",
                    {"source": "磁盘指南", "chunk_uid": "u1"},
                    0.9,
                )
            ]
        self.retriever = _FakeHybrid(hits, refused)
        self.llm = llm
        self.rag_refused = rag_refused
        self.questions: list = []

    def retrieve(self, question: str, top_k: int = 5) -> dict:
        self.questions.append((question, top_k))
        return {
            "available": True,
            "query": question,
            "refused": self.rag_refused,
            "refuse_message": "证据不足" if self.rag_refused else "",
            "hits": [{"source": "磁盘指南", "text": "磁盘处置", "score": 0.9, "bm25_score": None, "chunk_uid": "u1"}],
            "citations": ["《磁盘指南》"],
            "context": "[参考资料1] 《磁盘指南》\n磁盘处置",
        }


def _client(kb_agent: object | None = None) -> TestClient:
    app = create_app(enable_background_services=False, kb_agent_factory=lambda: kb_agent)
    return TestClient(app)


# ---------- 诊断端点 ----------


def test_analyze_with_kb_and_persistence() -> None:
    client = _client(FakeKbAgent())
    response = client.post(
        "/api/v1/incidents/analyze",
        json={"log_text": DISK_LOG, "service": "oa-app", "host": "10.20.1.11"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["saved"] is True
    report = body["report"]
    assert report["status"] == "ok"
    assert report["root_cause"]["cause_id"] == "disk_full"
    assert "《磁盘指南》" in report["citations"]
    assert report["read_only"] is True

    incident_id = report["incident_id"]
    detail = client.get(f"/api/v1/incidents/{incident_id}")
    assert detail.status_code == 200
    record = detail.json()
    assert record["status"] == "ok"
    assert record["root_cause"] == "disk_full"
    assert record["report"]["root_cause"]["cause_id"] == "disk_full"

    listed = client.get("/api/v1/incidents")
    assert incident_id in [row["id"] for row in listed.json()["items"]]


def test_analyze_without_kb_degrades() -> None:
    client = _client(None)
    response = client.post("/api/v1/incidents/analyze", json={"log_text": DISK_LOG})
    report = response.json()["report"]
    assert report["status"] == "ok"
    assert report["meta"]["degraded"] is True
    assert report["citations"] == []


def test_analyze_uncertain_flow_saved() -> None:
    client = _client(None)
    response = client.post(
        "/api/v1/incidents/analyze",
        json={"log_text": "2026-09-18 21:00:00 [WARN] upstream connect failed: Connection refused"},
    )
    report = response.json()["report"]
    assert report["status"] == "uncertain"
    assert report["root_cause"] is None
    detail = client.get(f"/api/v1/incidents/{report['incident_id']}")
    assert detail.json()["root_cause"] == ""


def test_analyze_include_llm_narrator() -> None:
    client = _client(FakeKbAgent(llm=_FakeLLM()))
    response = client.post(
        "/api/v1/incidents/analyze",
        json={"log_text": DISK_LOG, "include_llm": True},
    )
    meta = response.json()["report"]["meta"]
    assert meta["llm_used"] is True
    assert "磁盘已满" in meta["llm_narrative"]


def test_analyze_include_llm_without_llm_notes() -> None:
    client = _client(None)
    response = client.post(
        "/api/v1/incidents/analyze",
        json={"log_text": DISK_LOG, "include_llm": True},
    )
    meta = response.json()["report"]["meta"]
    assert "llm_note" in meta
    assert meta.get("llm_used") is not True


def test_get_incident_404() -> None:
    client = _client(None)
    assert client.get("/api/v1/incidents/INC-NOT-EXIST").status_code == 404


# ---------- 检索/评测/指标 ----------


def test_rag_query_without_kb() -> None:
    client = _client(None)
    body = client.post("/api/v1/rag/query", json={"question": "磁盘满了怎么办"}).json()
    assert body["available"] is False
    assert body["hits"] == []


def test_rag_query_with_kb_agent() -> None:
    agent = FakeKbAgent()
    client = _client(agent)
    body = client.post("/api/v1/rag/query", json={"question": "磁盘满了怎么办", "top_k": 3}).json()
    assert body["available"] is True
    assert body["hits"][0]["source"] == "磁盘指南"
    assert "《磁盘指南》" in body["citations"]
    assert agent.questions == [("磁盘满了怎么办", 3)]


def test_evals_latest_present_and_missing(tmp_path, monkeypatch) -> None:
    client = _client(None)
    body = client.get("/api/v1/evals/latest").json()
    assert body["available"] is True
    assert "meta" in body["rag"]
    assert body["incidents"]["top1"]["total"] == 30

    import ui.routers.incidents as incidents_router

    monkeypatch.setattr(incidents_router, "RESULTS_DIR", tmp_path)
    body2 = client.get("/api/v1/evals/latest").json()
    assert body2["available"] is False


def test_metrics_endpoint_counts() -> None:
    metrics.reset()
    client = _client(None)
    client.post("/api/v1/incidents/analyze", json={"log_text": DISK_LOG})
    client.post("/api/v1/rag/query", json={"question": "x"})
    text = client.get("/api/v1/metrics").text
    assert "oa_incidents_analyze_total 1" in text
    assert "oa_incidents_ok_total 1" in text
    assert "oa_rag_query_total 1" in text
    metrics.reset()


def test_rag_query_refused_metric() -> None:
    metrics.reset()
    agent = FakeKbAgent(rag_refused=True)
    client = _client(agent)
    body = client.post("/api/v1/rag/query", json={"question": "库里没有的问题"}).json()
    assert body["refused"] is True
    text = client.get("/api/v1/metrics").text
    assert "oa_rag_query_refused_total 1" in text
    metrics.reset()
