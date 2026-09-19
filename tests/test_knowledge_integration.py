"""knowledge_agent 混合检索接入的离线集成测试。

通过 __new__ 绕过重量级 __init__（不加载嵌入模型、不连 Chroma），注入
fake vector_store / fake retriever，验证:
- 引用格式 v2（文档/页码/章节/Chunk ID）
- 证据不足拒答短路（均不调用 LLM）
- 语义切块导入（section/page/uid 元数据落库 + 语料版本递增）
- 重复导入去重、删除后 BM25 缓存失效
"""

from _optional_deps import import_knowledge_agent
from _pdf_factory import build_minimal_pdf

from utils.retrieval import RetrievalHit, RetrievalResult, format_citations

ka = import_knowledge_agent()

REFUSE = "抱歉，知识库中未找到相关信息，请补充相关文档后重试。"


class _FakeVectorStore:
    def __init__(self, existing=None):
        self.added = []
        self.deleted = []
        self._existing = existing if existing is not None else {
            "ids": [], "documents": [], "metadatas": []
        }

    def get(self, where=None):
        return self._existing

    def add_documents(self, docs):
        self.added.extend(docs)

    def delete(self, ids=None):
        self.deleted.append(list(ids or []))


class _FakeRetriever:
    def __init__(self, result=None):
        self.result = result
        self.invalidated = 0

    def retrieve(self, query, top_k=None):
        return self.result

    def invalidate(self):
        self.invalidated += 1


def _refused(msg=REFUSE):
    return RetrievalResult(hits=[], refused=True, refuse_message=msg, query="q")


def _make_agent(vector_store=None, retriever=None):
    agent = ka.KnowledgeBaseAgent.__new__(ka.KnowledgeBaseAgent)
    agent.vector_store = vector_store or _FakeVectorStore()
    agent.kg_builder = None
    agent.kg_store = None
    agent.retriever = retriever
    agent._corpus_version = 0
    agent._chunking_strategy = lambda: "semantic"
    return agent


# ========== 引用格式 v2 ==========

def test_format_citations_with_page_section_uid() -> None:
    hits = [
        RetrievalHit(
            id="u1",
            text="增量备份步骤：先停应用，再复制数据目录。",
            metadata={"source": "ops.pdf", "page": 3, "section": "3.1 增量备份", "chunk_uid": "abc:12"},
        ),
        RetrievalHit(id="u2", text="无元数据命中", metadata={}),
    ]
    out = format_citations(hits)
    assert "[参考资料1] 《ops.pdf》 · 第3页 · §3.1 增量备份 · abc:12" in out
    assert "增量备份步骤" in out
    assert "[参考资料2] 《未知》" in out


def test_format_citations_falls_back_to_chunk_index() -> None:
    hits = [RetrievalHit(id="x", text="正文", metadata={"source": "a.pdf", "chunk_index": 5})]
    out = format_citations(hits)
    assert "《a.pdf》 · 块#5" in out


def test_retrieve_context_uses_hybrid_citations() -> None:
    hit = RetrievalHit(
        id="u1", text="磁盘清理流程",
        metadata={"source": "ops.pdf", "page": 2, "chunk_uid": "h:1"},
    )
    agent = _make_agent(retriever=_FakeRetriever(RetrievalResult(hits=[hit], refused=False)))
    ctx = agent._retrieve_context("磁盘满了")
    assert "《ops.pdf》" in ctx and "第2页" in ctx and "磁盘清理流程" in ctx


def test_retrieve_context_refused_returns_message() -> None:
    agent = _make_agent(retriever=_FakeRetriever(_refused("请补充文档。")))
    assert agent._retrieve_context("与知识库无关的问题") == "请补充文档。"


# ========== 拒答短路（不应触达 LLM/图） ==========

def test_query_agentic_refuses_without_llm() -> None:
    agent = _make_agent(retriever=_FakeRetriever(_refused()))
    assert agent._query_agentic("与知识库无关的问题") == REFUSE


def test_query_legacy_refuses_without_llm() -> None:
    agent = _make_agent(retriever=_FakeRetriever(_refused()))
    assert agent._query_legacy("与知识库无关的问题") == REFUSE


def test_stream_agentic_refuses_without_llm() -> None:
    agent = _make_agent(retriever=_FakeRetriever(_refused()))
    assert list(agent._stream_agentic("与知识库无关的问题")) == [REFUSE]


# ========== 导入：语义切块元数据 ==========

def test_import_txt_records_section_and_uid(tmp_path) -> None:
    txt = tmp_path / "ops.txt"
    txt.write_text(
        "# 第三章 备份策略\n\n" + "备份策略说明内容。" * 30 + "\n\n"
        "## 3.1 增量备份\n\n" + "增量备份操作步骤详情。" * 30,
        encoding="utf-8",
    )
    vs = _FakeVectorStore()
    agent = _make_agent(vector_store=vs)
    result = agent.import_document(str(txt))
    assert "文档导入成功" in result
    assert "semantic 策略" in result
    assert vs.added, "should add documents to the vector store"
    metas = [doc.metadata for doc in vs.added]
    assert all(m.get("chunk_uid") for m in metas)
    assert [m["chunk_index"] for m in metas] == list(range(1, len(metas) + 1))
    sections = {m.get("section") for m in metas}
    assert "第三章 备份策略" in sections
    assert "3.1 增量备份" in sections
    assert "page" not in metas[0]  # 非 PDF：无页元数据
    assert agent._corpus_version == 1  # 语料版本递增（BM25 失效）


def test_import_pdf_records_page_numbers(tmp_path) -> None:
    pdf = tmp_path / "guide.pdf"
    pdf.write_bytes(build_minimal_pdf([
        "Disk full troubleshooting steps for OA server",
        "Nginx 502 upstream check notes for gateway",
    ]))
    vs = _FakeVectorStore()
    agent = _make_agent(vector_store=vs)
    result = agent.import_document(str(pdf))
    assert "文档导入成功" in result
    pages = [doc.metadata.get("page") for doc in vs.added]
    assert pages == [1, 2]
    joined = " ".join(doc.page_content for doc in vs.added)
    assert "Disk full" in joined and "Nginx 502" in joined


def test_import_duplicate_hash_returns_hint(tmp_path) -> None:
    txt = tmp_path / "dup.txt"
    txt.write_text("重复导入测试内容。", encoding="utf-8")
    vs = _FakeVectorStore(existing={
        "ids": ["x"], "documents": ["old"], "metadatas": [{}],
    })
    agent = _make_agent(vector_store=vs)
    result = agent.import_document(str(txt))
    assert "已导入过" in result
    assert not vs.added


# ========== 变更后语料版本 / 缓存失效 ==========

def test_delete_document_bumps_version_and_invalidates() -> None:
    vs = _FakeVectorStore(existing={
        "ids": ["a", "b"],
        "documents": ["x", "y"],
        "metadatas": [{"source": "d.pdf"}, {"source": "other.pdf"}],
    })
    retriever = _FakeRetriever()
    agent = _make_agent(vector_store=vs, retriever=retriever)
    result = agent.delete_document("d.pdf")
    assert "已从知识库删除" in result
    assert vs.deleted == [["a"]]
    assert agent._corpus_version == 1
    assert retriever.invalidated == 1


def test_clear_knowledge_base_bumps_version() -> None:
    vs = _FakeVectorStore(existing={
        "ids": ["a", "b"], "documents": ["x", "y"], "metadatas": [{}, {}],
    })
    agent = _make_agent(vector_store=vs)
    result = agent.clear_knowledge_base()
    assert "已清空" in result
    assert agent._corpus_version == 1


# ========== 适配器辅助 ==========

def test_canonical_chunk_id_precedence() -> None:
    f = ka.KnowledgeBaseAgent._canonical_chunk_id
    assert f({"chunk_uid": "u:9"}) == "u:9"
    assert f({"source": "a.pdf", "chunk_index": 3}) == "a.pdf#3"
    assert f({}, fallback_index=7) == "chunk:7"


def test_distance_to_similarity_conversion() -> None:
    f = ka.KnowledgeBaseAgent._distance_to_similarity
    assert abs(f(0.0) - 1.0) < 1e-9
    assert abs(f(2 ** 0.5) - 0.0) < 1e-9  # d²/2 = 1 → cos 0
    assert f(10.0) == -1.0  # 裁剪下界
    assert f("bad-value") == 0.0


# ========== 结构化路由决策 ==========

class _FakeStructured:
    def __init__(self, result):
        self._result = result

    def invoke(self, messages):
        return self._result


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeRouterLLM:
    def __init__(self, structured=None, text="", structured_raises=False):
        self.structured = structured
        self.text = text
        self.structured_raises = structured_raises
        self.structured_calls = 0
        self.text_calls = 0

    def with_structured_output(self, schema):
        self.structured_calls += 1
        if self.structured_raises:
            raise RuntimeError("tools not supported by this endpoint")
        return _FakeStructured(self.structured)

    def invoke(self, messages):
        self.text_calls += 1
        return _FakeMessage(self.text)


def _router_agent(llm):
    agent = _make_agent()
    agent.llm = llm
    agent._structured_ok = None
    return agent


def test_router_decide_uses_structured_output() -> None:
    llm = _FakeRouterLLM(structured=ka.RouterDecision(action="answer"))
    agent = _router_agent(llm)
    assert agent._router_decide("问题", has_kg=True) == ("answer", "")
    assert llm.structured_calls == 1
    assert llm.text_calls == 0


def test_router_decide_text_json_fallback() -> None:
    llm = _FakeRouterLLM(
        structured_raises=True,
        text='{"action": "search_kb", "query": "磁盘空间不足", "reasoning": "先检索"}',
    )
    agent = _router_agent(llm)
    assert agent._router_decide("磁盘满了", has_kg=True) == ("search_kb", "磁盘空间不足")
    assert agent._structured_ok is False
    # 第二次不再尝试结构化输出（避免每步双调用）
    agent._router_decide("再问一次", has_kg=True)
    assert llm.structured_calls == 1


def test_router_decide_invalid_text_defaults_to_search_kb() -> None:
    llm = _FakeRouterLLM(structured_raises=True, text="一段没有 JSON 的自由文本")
    agent = _router_agent(llm)
    assert agent._router_decide("问题", has_kg=True) == ("search_kb", "")


def test_router_decide_kg_disabled_coerces_action() -> None:
    llm = _FakeRouterLLM(
        structured=ka.RouterDecision(action="search_kg", query="某实体")
    )
    agent = _router_agent(llm)
    assert agent._router_decide("问题", has_kg=False) == ("search_kb", "某实体")


# ========== 重排序接线 ==========

def test_build_reranker_respects_config() -> None:
    from utils.retrieval import RetrievalConfig

    agent = _make_agent()
    agent.retrieval_config = RetrievalConfig(rerank=False)
    assert agent._build_reranker() is None
    agent.retrieval_config = RetrievalConfig(rerank=True, rerank_model="model-x")
    built = agent._build_reranker()
    assert built is not None
    assert callable(built)


# ========== 结构化检索（/api/v1/rag/query 数据面） ==========


def test_retrieve_returns_structured_hits() -> None:
    hits = [
        RetrievalHit(
            id="c1",
            text="磁盘空间不足处置：清理过期日志",
            metadata={"source": "磁盘指南", "chunk_uid": "u1"},
            dense_similarity=0.82,
        ),
    ]
    agent = _make_agent(retriever=_FakeRetriever(RetrievalResult(hits=hits, refused=False, query="磁盘满")))
    outcome = agent.retrieve("磁盘满", top_k=3)
    assert outcome["available"] is True
    assert outcome["refused"] is False
    assert outcome["hits"][0]["source"] == "磁盘指南"
    assert abs(outcome["hits"][0]["score"] - 0.82) < 1e-9
    assert outcome["citations"] == ["《磁盘指南》"]
    assert "磁盘空间不足处置" in outcome["context"]


def test_retrieve_refused_and_unavailable() -> None:
    agent = _make_agent(retriever=_FakeRetriever(_refused()))
    outcome = agent.retrieve("xx")
    assert outcome["available"] is True
    assert outcome["refused"] is True
    assert outcome["refuse_message"]
    assert outcome["citations"] == []
    assert outcome["context"] == ""

    agent2 = _make_agent(retriever=None)
    outcome2 = agent2.retrieve("xx")
    assert outcome2["available"] is False
    assert outcome2["hits"] == []
    assert outcome2["citations"] == []
