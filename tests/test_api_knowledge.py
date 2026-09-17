"""Knowledge-base, chat and knowledge-graph endpoint tests.

The real KnowledgeBaseAgent needs optional RAG dependencies (and would load
embedding models), so the HTTP endpoints are exercised against a deterministic
fake that records calls. Conversation endpoints use the real SQLite database
inside the isolated OA_DATA_DIR. The (private) KB state machine and factory
are tested directly.
"""

import json
import os
from urllib.parse import quote

import pytest
from _optional_deps import import_knowledge_agent
from fastapi.testclient import TestClient

import ui.server as server
from ui.server import create_app
from utils.database import db

pytestmark = pytest.mark.allow_hosts(["127.0.0.1", "::1", "localhost"])


class FakeGraph:
    def __init__(self, nodes: int = 3) -> None:
        self._nodes = nodes

    def number_of_nodes(self) -> int:
        return self._nodes


class FakeKgStore:
    def __init__(self, nodes: int = 3) -> None:
        self.graph = FakeGraph(nodes)
        self.search_calls: list = []
        self.subgraph_calls: list = []

    def search_nodes(self, query, entity_type=None, limit=30):
        self.search_calls.append({"query": query, "entity_type": entity_type, "limit": limit})
        return [{"id": "n1", "name": query, "type": entity_type or "service"}]

    def get_stats(self):
        return {"nodes": self.graph.number_of_nodes(), "edges": 2}

    def extract_subgraph(self, node_id, depth=2):
        self.subgraph_calls.append({"node_id": node_id, "depth": depth})
        return {"nodes": [node_id], "edges": []}

    def find_shortest_path(self, source, target):
        if source == "missing":
            return {"error": "未找到路径"}
        return {"path": [source, target], "length": 1, "nodes": [source, target], "edges": []}


class FakeKnowledgeBase:
    def __init__(self, kg_store=None) -> None:
        self.calls: list = []
        self.kg_store = kg_store
        self.cleared_conversations: list = []

    def query(self, question):
        self.calls.append(("query", question))
        return f"回答: {question}"

    def import_document(self, path):
        self.calls.append(("import", os.path.basename(path)))
        return f"已导入 {os.path.basename(path)}"

    def list_documents(self):
        return ["运维手册.md", "故障案例.pdf"]

    def get_stats(self):
        return {"documents": 2, "chunks": 10}

    def get_document_text(self, name):
        return f"全文:{name}"

    def delete_document(self, name):
        return f"已删除:{name}"

    def clear_knowledge_base(self):
        return "知识库已清空"

    def chat(self, message, conversation_id="default", stream=False):
        if stream:
            if message == "boom":
                def failing():
                    yield "片段"
                    raise RuntimeError("流式中断")
                return failing()
            return iter(["第一段", "第二段"])
        return f"对话回答:{message}"

    def clear_conversation(self, conversation_id):
        self.cleared_conversations.append(conversation_id)


@pytest.fixture()
def client():
    with TestClient(create_app(enable_background_services=False)) as test_client:
        yield test_client


@pytest.fixture()
def fake_kb(monkeypatch):
    fake = FakeKnowledgeBase()
    monkeypatch.setattr(server, "get_kb_agent", lambda: fake)
    return fake


# ============ 知识库管理端点 ============


def test_kb_ready_endpoints_delegate_to_agent(client, fake_kb) -> None:
    assert client.post("/api/kb/ask", data={"question": "OA 502 怎么处理"}).json() == {
        "result": "回答: OA 502 怎么处理"
    }
    assert client.get("/api/kb/list").json() == {"result": ["运维手册.md", "故障案例.pdf"]}
    assert client.get("/api/kb/stats").json() == {"result": {"documents": 2, "chunks": 10}}
    assert client.get(f"/api/kb/document/{quote('运维手册.md')}").json() == {"result": "全文:运维手册.md"}
    assert client.post("/api/kb/delete", data={"doc_name": "运维手册.md"}).json() == {"result": "已删除:运维手册.md"}
    assert client.post("/api/kb/clear").json() == {"result": "知识库已清空"}
    assert fake_kb.calls == [("query", "OA 502 怎么处理")]


def test_kb_endpoints_report_loading_state(client, monkeypatch) -> None:
    monkeypatch.setattr(server, "get_kb_agent", lambda: None)
    monkeypatch.setattr(server, "_kb_state", {"state": "loading", "error": None})

    body = client.post("/api/kb/ask", data={"question": "q"}).json()

    assert body["state"] == "loading"
    assert "初始化" in body["result"]


def test_kb_endpoints_report_unavailable_state(client, monkeypatch) -> None:
    monkeypatch.setattr(server, "get_kb_agent", lambda: None)
    monkeypatch.setattr(server, "_kb_state", {"state": "unavailable", "error": "缺少 API Key"})

    assert client.get("/api/kb/list").json() == {
        "state": "unavailable",
        "result": "知识库引擎不可用：缺少 API Key",
    }
    assert client.get("/api/kb/stats").json()["state"] == "unavailable"
    assert client.post("/api/kb/clear").json()["state"] == "unavailable"


def test_kb_import_uploads_parses_and_cleans_up(client, fake_kb, tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    body = client.post("/api/kb/import", files={"file": ("运维手册.md", b"# hello", "text/markdown")}).json()

    assert body == {"result": "已导入 运维手册.md"}
    assert fake_kb.calls == [("import", "运维手册.md")]
    uploads = tmp_path / "data" / "uploads"
    assert uploads.is_dir()
    assert list(uploads.iterdir()) == []  # 上传的临时文件已被清理


def test_kb_import_sanitizes_traversal_filename(client, fake_kb, tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    body = client.post("/api/kb/import", files={"file": ("../../evil.txt", b"x", "text/plain")}).json()

    assert body == {"result": "已导入 evil.txt"}


# ============ 对话端点 ============


def test_chat_endpoint_returns_answer(client, fake_kb) -> None:
    body = client.post("/api/kb/chat", data={"message": "你好", "conversation_id": "c1"}).json()

    assert body == {"answer": "对话回答:你好", "conversation_id": "c1"}


def test_chat_history_reads_real_database(client) -> None:
    conversation_id = db.create_conversation("排障会话")
    db.save_message(conversation_id, "user", "OA 打不开")
    db.save_message(conversation_id, "assistant", "先看 nginx 状态")

    body = client.get("/api/kb/chat/history", params={"conversation_id": conversation_id}).json()

    assert body["messages"] == [
        {"role": "user", "content": "OA 打不开"},
        {"role": "assistant", "content": "先看 nginx 状态"},
    ]


def test_conversation_crud_endpoints(client) -> None:
    created = client.post("/api/kb/conversation").json()
    assert created["ok"] is True
    assert created["title"] == "新对话"
    conversation_id = created["conversation_id"]

    listed = client.get("/api/kb/conversations").json()["conversations"]
    assert any(item["id"] == conversation_id for item in listed)

    renamed = client.post(
        "/api/kb/conversation/rename",
        data={"conversation_id": conversation_id, "title": "数据库巡检"},
    ).json()
    assert renamed["ok"] is True

    renamed_list = client.get("/api/kb/conversations").json()["conversations"]
    assert any(item["id"] == conversation_id and item["title"] == "数据库巡检" for item in renamed_list)

    deleted = client.post("/api/kb/conversation/delete", data={"conversation_id": conversation_id}).json()
    assert deleted == {"ok": True, "conversation_id": conversation_id}

    deleted_again = client.post("/api/kb/conversation/delete", data={"conversation_id": conversation_id}).json()
    assert deleted_again["ok"] is False


def test_chat_clear_endpoint(client, fake_kb) -> None:
    body = client.post("/api/kb/chat/clear", data={"conversation_id": "abc"}).json()

    assert body == {"ok": True, "conversation_id": "abc"}
    assert fake_kb.cleared_conversations == ["abc"]


def test_chat_clear_without_agent(client, monkeypatch) -> None:
    monkeypatch.setattr(server, "get_kb_agent", lambda: None)

    assert client.post("/api/kb/chat/clear").json() == {"ok": False}


def _sse_payloads(text: str) -> list:
    return [json.loads(line[len("data: "):]) for line in text.splitlines() if line.startswith("data: ")]


def test_chat_stream_emits_deltas_then_done(client, fake_kb) -> None:
    response = client.post("/api/kb/chat/stream", data={"message": "你好", "conversation_id": "c1"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert _sse_payloads(response.text) == [
        {"delta": "第一段"},
        {"delta": "第二段"},
        {"done": True, "conversation_id": "c1"},
    ]


def test_chat_stream_reports_generator_errors(client, fake_kb) -> None:
    response = client.post("/api/kb/chat/stream", data={"message": "boom"})

    payloads = _sse_payloads(response.text)
    assert payloads[0] == {"delta": "片段"}
    assert payloads[-1] == {"error": "流式中断"}


def test_chat_stream_without_agent_reports_state(client, monkeypatch) -> None:
    monkeypatch.setattr(server, "get_kb_agent", lambda: None)
    monkeypatch.setattr(server, "_kb_state", {"state": "loading", "error": None})

    response = client.post("/api/kb/chat/stream", data={"message": "hi"})

    payload = _sse_payloads(response.text)[0]
    assert payload["state"] == "loading"
    assert "初始化" in payload["error"]


# ============ 批量问答端点 ============


def test_batch_ask_returns_answers_in_order(client, fake_kb) -> None:
    body = client.post("/api/kb/batch-ask", data={"questions": "问题1\n\n问题2\n问题3"}).json()

    assert body["total"] == 3
    assert [item["question"] for item in body["results"]] == ["问题1", "问题2", "问题3"]
    assert [item["answer"] for item in body["results"]] == ["回答: 问题1", "回答: 问题2", "回答: 问题3"]


def test_batch_ask_rejects_empty_question_list(client, fake_kb) -> None:
    assert client.post("/api/kb/batch-ask", data={"questions": "  \n "}).json() == {
        "results": [],
        "error": "未提供有效问题",
    }


def test_batch_ask_truncates_to_configured_maximum(client, fake_kb, monkeypatch) -> None:
    real_get = server.app_config.get

    def limited_get(path, default=None):
        if path == "knowledge_base.agentic_rag.batch_max_questions":
            return 2
        if path == "knowledge_base.agentic_rag.parallel_workers":
            return 2
        return real_get(path, default)

    monkeypatch.setattr(server.app_config, "get", limited_get)

    body = client.post("/api/kb/batch-ask", data={"questions": "a\nb\nc\nd"}).json()

    assert body["total"] == 2


def test_batch_ask_marks_failed_questions(client, monkeypatch) -> None:
    class FlakyKnowledgeBase(FakeKnowledgeBase):
        def query(self, question):
            if question == "坏问题":
                raise RuntimeError("检索失败")
            return super().query(question)

    monkeypatch.setattr(server, "get_kb_agent", lambda: FlakyKnowledgeBase())

    body = client.post("/api/kb/batch-ask", data={"questions": "好问题\n坏问题"}).json()

    answers = {item["question"]: item["answer"] for item in body["results"]}
    assert answers["好问题"] == "回答: 好问题"
    assert answers["坏问题"].startswith("[错误]")


def test_batch_ask_without_agent(client, monkeypatch) -> None:
    monkeypatch.setattr(server, "get_kb_agent", lambda: None)
    monkeypatch.setattr(server, "_kb_state", {"state": "unavailable", "error": "无 Key"})

    body = client.post("/api/kb/batch-ask", data={"questions": "q"}).json()

    assert body["state"] == "unavailable"
    assert body["results"] == []
    assert "不可用" in body["error"]


# ============ 知识图谱端点 ============


def test_kg_endpoints_delegate_to_store(client, monkeypatch) -> None:
    store = FakeKgStore()
    monkeypatch.setattr(server, "get_kb_agent", lambda: FakeKnowledgeBase(kg_store=store))

    search = client.post("/api/kg/search", data={"query": "Nginx", "entity_type": "service"}).json()
    assert search["total"] == 1
    assert search["results"] == [{"id": "n1", "name": "Nginx", "type": "service"}]
    assert search["stats"] == {"nodes": 3, "edges": 2}
    assert store.search_calls == [{"query": "Nginx", "entity_type": "service", "limit": 30}]

    client.post("/api/kg/search", data={"query": "Nginx", "entity_type": "all"})
    assert store.search_calls[-1]["entity_type"] is None  # "all" 表示不过滤

    explore = client.post("/api/kg/explore", data={"node_id": "nginx-1", "depth": 9}).json()
    assert explore["subgraph"]["nodes"] == ["nginx-1"]
    assert store.subgraph_calls == [{"node_id": "nginx-1", "depth": 5}]  # depth 钳制到 5

    path = client.post("/api/kg/path", data={"source": "a", "target": "b"}).json()
    assert path == {"path": ["a", "b"], "length": 1, "nodes": ["a", "b"], "edges": []}

    stats = client.get("/api/kg/stats").json()
    assert stats == {"nodes": 3, "edges": 2, "available": True}

    missing = client.post("/api/kg/path", data={"source": "missing", "target": "b"}).json()
    assert missing == {"error": "未找到路径", "path": [], "length": -1}


def test_kg_endpoints_report_503_when_unavailable(client, monkeypatch) -> None:
    monkeypatch.setattr(server, "get_kb_agent", lambda: None)
    monkeypatch.setattr(server, "_kb_state", {"state": "loading", "error": None})

    for path, payload in [
        ("/api/kg/search", {"query": "x"}),
        ("/api/kg/explore", {"node_id": "x"}),
        ("/api/kg/path", {"source": "a", "target": "b"}),
    ]:
        assert client.post(path, data=payload).status_code == 503

    stats = client.get("/api/kg/stats").json()
    assert stats["available"] is False
    assert "不可用" in stats["message"]


def test_kg_unavailable_when_graph_empty_or_store_missing(client, monkeypatch) -> None:
    monkeypatch.setattr(server, "get_kb_agent", lambda: FakeKnowledgeBase(kg_store=FakeKgStore(nodes=0)))
    assert client.post("/api/kg/search", data={"query": "x"}).status_code == 503

    monkeypatch.setattr(server, "get_kb_agent", lambda: FakeKnowledgeBase(kg_store=None))
    assert client.post("/api/kg/path", data={"source": "a", "target": "b"}).status_code == 503


# ============ KB 状态机与工厂 ============


def test_get_kb_agent_returns_cached_instance(monkeypatch) -> None:
    cached = FakeKnowledgeBase()
    monkeypatch.setattr(server, "_kb_agent", cached)

    assert server.get_kb_agent() is cached


def test_get_kb_agent_is_sticky_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(server, "_kb_agent", None)
    monkeypatch.setattr(server, "_kb_init_generation", None)
    monkeypatch.setattr(server, "_kb_state", {"state": "unavailable", "error": "以前的失败"})

    assert server.get_kb_agent() is None


def test_get_kb_agent_returns_none_while_another_thread_initializes(monkeypatch) -> None:
    monkeypatch.setattr(server, "_kb_agent", None)
    monkeypatch.setattr(server, "_kb_init_generation", 0)
    monkeypatch.setattr(server, "_kb_state", {"state": "loading", "error": None})

    assert server.get_kb_agent() is None


def test_get_kb_agent_builds_and_marks_ready(monkeypatch) -> None:
    built = FakeKnowledgeBase()
    monkeypatch.setattr(server, "_kb_agent", None)
    monkeypatch.setattr(server, "_kb_init_generation", None)
    monkeypatch.setattr(server, "_kb_state", {"state": "loading", "error": None})
    monkeypatch.setattr(server, "_build_kb_agent", lambda: (built, None))

    assert server.get_kb_agent() is built
    assert server._kb_agent is built
    assert server._kb_state["state"] == "ready"
    assert server._kb_init_generation is None


def test_get_kb_agent_marks_unavailable_on_build_failure(monkeypatch) -> None:
    monkeypatch.setattr(server, "_kb_agent", None)
    monkeypatch.setattr(server, "_kb_init_generation", None)
    monkeypatch.setattr(server, "_kb_state", {"state": "loading", "error": None})
    monkeypatch.setattr(server, "_build_kb_agent", lambda: (None, "未配置 LLM API Key"))

    assert server.get_kb_agent() is None
    assert server._kb_state == {"state": "unavailable", "error": "未配置 LLM API Key"}


def test_get_kb_agent_marks_unavailable_on_build_exception(monkeypatch) -> None:
    def boom():
        raise RuntimeError("模型加载失败")

    monkeypatch.setattr(server, "_kb_agent", None)
    monkeypatch.setattr(server, "_kb_init_generation", None)
    monkeypatch.setattr(server, "_kb_state", {"state": "loading", "error": None})
    monkeypatch.setattr(server, "_build_kb_agent", boom)

    assert server.get_kb_agent() is None
    assert server._kb_state["state"] == "unavailable"
    assert "模型加载失败" in server._kb_state["error"]


def test_build_kb_agent_reports_missing_cloud_key(monkeypatch) -> None:
    import_knowledge_agent()  # 缺 RAG 依赖时安装占位模块，函数内的 import 才能成功

    real_get = server.app_config.get

    def cloud_get(path, default=None):
        if path == "llm.provider":
            return "deepseek"
        if path == "llm.api_key":
            return ""
        return real_get(path, default)

    monkeypatch.setattr(server.app_config, "get", cloud_get)

    kb, error = server._build_kb_agent()

    assert kb is None
    assert error is not None
    assert "OA_LLM_API_KEY" in error


def test_build_kb_agent_ollama_branch_uses_local_defaults(monkeypatch) -> None:
    module = import_knowledge_agent()
    created: dict = {}

    class FakeKnowledgeBaseAgent:
        def __init__(self, llm_api_key, llm_base_url, llm_model):
            created.update({"key": llm_api_key, "url": llm_base_url, "model": llm_model})

    monkeypatch.setattr(module, "KnowledgeBaseAgent", FakeKnowledgeBaseAgent)

    real_get = server.app_config.get

    def ollama_get(path, default=None):
        values = {
            "llm.provider": "ollama",
            "llm.ollama.base_url": "http://127.0.0.1:11434/v1",
            "llm.ollama.model": "qwen3:8b",
        }
        return values.get(path, real_get(path, default))

    monkeypatch.setattr(server.app_config, "get", ollama_get)

    kb, error = server._build_kb_agent()

    assert error is None
    assert isinstance(kb, FakeKnowledgeBaseAgent)
    assert created == {"key": "ollama", "url": "http://127.0.0.1:11434/v1", "model": "qwen3:8b"}


def test_build_kb_agent_cloud_branch_uses_configured_values(monkeypatch) -> None:
    module = import_knowledge_agent()
    created: dict = {}

    class FakeKnowledgeBaseAgent:
        def __init__(self, llm_api_key, llm_base_url, llm_model):
            created.update({"key": llm_api_key, "url": llm_base_url, "model": llm_model})

    monkeypatch.setattr(module, "KnowledgeBaseAgent", FakeKnowledgeBaseAgent)

    real_get = server.app_config.get

    def cloud_get(path, default=None):
        values = {
            "llm.provider": "deepseek",
            "llm.api_key": "sk-live",
            "llm.base_url": "https://api.deepseek.com/v1",
            "llm.model": "deepseek-chat",
        }
        return values.get(path, real_get(path, default))

    monkeypatch.setattr(server.app_config, "get", cloud_get)

    kb, error = server._build_kb_agent()

    assert error is None
    assert isinstance(kb, FakeKnowledgeBaseAgent)
    assert created == {"key": "sk-live", "url": "https://api.deepseek.com/v1", "model": "deepseek-chat"}
