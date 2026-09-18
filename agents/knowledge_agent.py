"""
知识库问答Agent模块（RAG + Chroma向量库）
---------------------------------------
功能：
1. 上传PDF/Word/TXT运维文档 → 自动解析 → 文本清洗 → 分块
2. 文本块向量化存入Chroma向量数据库
3. 基于检索增强生成(RAG)回答运维知识问题
4. 严格限制只基于知识库作答，无法回答时明确告知用户

RAG流程：
用户提问 → 向量检索Top-K相关文档块 → 拼接上下文 → LLM生成回答

设计思路：
- 使用sentence-transformers生成文档向量（本地运行，无需API）
- Chroma作为向量存储后端（持久化到本地磁盘）
- RAG Prompt强制LLM仅基于上下文回答，减少幻觉
- 支持知识库文档的增删查管理
"""

import os
import hashlib
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Tuple

from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_openai import ChatOpenAI
from langchain_core.documents import Document
from pydantic import BaseModel, ValidationError

from utils.doc_parser import parse_document, parse_pdf_pages, split_text
from utils.chunking import Chunk, split_semantic, split_semantic_pages
from utils.retrieval import (
    DEFAULT_REFUSE_MESSAGE,
    HybridRetriever,
    RetrievalConfig,
    RetrievalResult,
    format_citations,
)
from utils.structured import parse_structured
from utils.config import get_app_root
from utils.logger import get_logger
from utils.prompt_safety import UNTRUSTED_DATA_GUARD, wrap_untrusted
from utils.database import db

logger = get_logger(__name__)

# HuggingFace连接优化：如果无法直连则使用国内镜像
if "HF_ENDPOINT" not in os.environ:
    os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

# ========== 路径配置 ==========
BASE_DIR = get_app_root()
CHROMA_DB_DIR = os.path.join(BASE_DIR, "data", "chroma_db")
os.makedirs(CHROMA_DB_DIR, exist_ok=True)

# ========== 嵌入模型配置 ==========
# 使用多语言MiniLM模型，中英文均支持，体积约118MB
# 首次运行会自动下载到本地缓存目录
# 如需切换模型，修改此变量即可（也支持接入OpenAI兼容的嵌入API）
EMBEDDING_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"

# ========== RAG 系统提示词 ==========
# 核心约束：只能基于提供的知识库片段回答，禁止使用训练数据中的外部知识

RAG_SYSTEM_PROMPT = """你是一名OA运维知识库助手，你的职责是基于企业OA运维知识库文档回答用户问题。

## 严格规则
1. **只能**根据下方【参考资料】中的内容回答问题
2. 如果【参考资料】中没有相关信息，必须回答："抱歉，知识库中未找到相关信息，请补充相关文档后重试。"
3. **禁止**使用你的训练数据或外部知识回答问题
4. 回答时**必须**引用具体的完整文档文件名，用《》括起来（如"根据《MySQL8.0数据库安装参考手册Windows.pdf》"）（如"根据《xxx文档》..."）
5. 如果参考资料中有操作步骤，请按序号列出并标注注意事项

## 回答格式
- 先给出直接答案
- 然后列出依据（引用的文档片段）
- 如果涉及操作，给出具体步骤和命令
""" + UNTRUSTED_DATA_GUARD


class RouterDecision(BaseModel):
    """Agentic 路由决策（Pydantic 结构化输出，替代 NEXT_ACTION 字符串解析）。"""

    action: Literal["search_kb", "search_kg", "explore_graph", "answer"]
    query: str = ""
    reasoning: str = ""


# ========== 文档分块配置 ==========
CHUNK_SIZE = 500        # 每块最多500字
CHUNK_OVERLAP = 50      # 相邻块重叠50字，保持上下文连贯


class KnowledgeBaseAgent:
    """
    知识库RAG Agent。

    核心能力：
    - 文档导入：解析PDF/Word/TXT，分块，向量化存入Chroma
    - 知识检索：根据用户问题从向量库检索最相关的Top-K文档块
    - 智能问答：基于检索结果让LLM生成准确回答

    使用方式：
        kb = KnowledgeBaseAgent(llm_api_key="...", llm_base_url="...")
        kb.import_document("path/to/doc.pdf")         # 导入文档
        answer = kb.query("OA审批流程是什么？")        # 提问
        kb.list_documents()                            # 查看已导入文档
        kb.delete_document("doc_name")                 # 删除文档
    """

    def __init__(
        self,
        llm_api_key: str = "your-api-key-here",
        llm_base_url: str = "https://api.deepseek.com/v1",
        llm_model: str = "deepseek-chat",
        embedding_model: str = EMBEDDING_MODEL_NAME,
    ):
        """
        初始化知识库Agent。

        Args:
            llm_api_key: 大模型API密钥
            llm_base_url: API地址
            llm_model: 模型名称
            embedding_model: 本地嵌入模型名称（sentence-transformers模型）
        """
        self.llm_api_key = llm_api_key
        self.llm_base_url = llm_base_url
        self.llm_model = llm_model

        # ---- 初始化嵌入模型（本地运行）----
        logger.info(f"正在加载嵌入模型: {embedding_model}...")
        self.embeddings = None
        # 优先尝试纯本地加载（跳过远程校验，避免超时卡死）
        for attempt in [
            {"local_files_only": True},
            {"local_files_only": False},
        ]:
            try:
                logger.info(f"  尝试加载 (local_files_only={attempt['local_files_only']})...")
                self.embeddings = HuggingFaceEmbeddings(
                    model_name=embedding_model,
                    model_kwargs={"device": "cpu", "local_files_only": attempt["local_files_only"]},
                    encode_kwargs={"normalize_embeddings": True},
                )
                logger.info("嵌入模型加载成功")
                break
            except Exception as e:
                logger.warning(f"  加载失败: {e}")
                if attempt["local_files_only"]:
                    logger.info("  本地文件不存在，尝试在线下载...")
                else:
                    logger.error(f"  在线下载也失败，知识库功能不可用")
                    raise

        # ---- 初始化Chroma向量库（持久化）----
        self.vector_store = Chroma(
            collection_name="oa_knowledge_base",
            embedding_function=self.embeddings,
            persist_directory=CHROMA_DB_DIR,
        )
        logger.info(f"Chroma向量库已连接，存储路径: {CHROMA_DB_DIR}")

        # ---- 混合检索（RAG 2.0：BM25 + Dense + RRF）----
        self._corpus_version = 0
        self._structured_ok = None  # 路由结构化输出可用性（首次失败后禁用）
        self.retrieval_config = self._load_retrieval_config()
        self.retriever = HybridRetriever(
            dense_search=self._dense_search,
            corpus_fn=self._corpus_snapshot,
            reranker=self._build_reranker(),
            config=self.retrieval_config,
        )

        # ---- 初始化LLM（用于RAG生成回答）----
        self.llm = ChatOpenAI(
            api_key=llm_api_key,
            base_url=llm_base_url,
            model=llm_model,
            temperature=0.1,    # 极低温度确保回答严格依据上下文
            max_tokens=2048,
        )

        # ---- 初始化知识图谱（可选，config 控制）----
        self.kg_store = None
        self.kg_builder = None
        self._init_kg()

        # ---- 对话记忆（chatbot 多轮对话）----
        self.conversations: Dict[str, List[Dict]] = {}

        # ---- 构建 Agent（Agentic RAG 或简单 RAG）----
        self.agentic_graph = None
        self._setup_agent()

        logger.info("知识库Agent初始化完成")

    def _init_kg(self):
        """根据配置初始化知识图谱（Phase 2）。"""
        try:
            from utils.config import config as app_config
            kg_enabled = app_config.get("knowledge_base.kg.enabled", False)
        except Exception:
            kg_enabled = False

        if not kg_enabled:
            logger.info("知识图谱未启用（config.knowledge_base.kg.enabled=false）")
            return

        try:
            from utils.kg_store import KGStore
            from agents.kg_builder import KnowledgeGraphBuilder
            from utils.config import config as app_config

            storage_dir = app_config.get("knowledge_base.kg.storage_dir", "data/knowledge_graph")
            if storage_dir and not os.path.isabs(storage_dir):
                storage_dir = os.path.join(BASE_DIR, storage_dir)
            provider = app_config.get("llm.provider", "ollama")

            self.kg_store = KGStore(storage_dir)
            self.kg_builder = KnowledgeGraphBuilder(
                llm=self.llm,
                kg_store=self.kg_store,
                provider=provider,
            )
            logger.info("知识图谱已初始化" + (f" ({self.kg_store.get_stats()['total_nodes']} 节点)" if self.kg_store.graph.number_of_nodes() > 0 else ""))
        except Exception as e:
            logger.warning(f"知识图谱初始化失败（KG 功能不可用）: {e}")
            self.kg_store = None
            self.kg_builder = None

    def _setup_agent(self):
        """构建 Agent。根据 config 选择 Agentic RAG 或简单 RAG。"""
        try:
            from utils.config import config as app_config
            use_agentic = app_config.get("knowledge_base.agentic_rag.enabled", False)
        except Exception:
            use_agentic = False

        if use_agentic:
            self._setup_agentic_rag()
        else:
            self._setup_legacy_agent()

    def _setup_legacy_agent(self):
        """构建简单 LangChain Agent（v2.x 兼容模式）。"""
        kb = self

        @tool
        def search_knowledge_base(query: str) -> str:
            """从运维知识库中检索与用户问题最相关的文档内容。"""
            return kb._retrieve_context(query)

        self.agent = create_agent(
            model=self.llm,
            tools=[search_knowledge_base],
            system_prompt=RAG_SYSTEM_PROMPT,
        )
        logger.info("简单 RAG Agent 已就绪")

    # ========== Agentic RAG — LangGraph ReAct ==========

    AGENTIC_RAG_PROMPT = """你是一个 OA 运维知识库智能助手，可以多步检索来回答问题。

## 可用工具
1. search_kb — 向量语义搜索，从知识库找相关文档片段
2. search_kg — 搜索知识图谱，查找相关实体（技术/概念/组织等）
3. explore_graph — 探索图谱中某个实体的邻居，发现关联概念

## 决策流程
每次收到用户问题后，按以下步骤操作：
- 第一步：使用 search_kb 做宽泛的语义检索
- 第二步：使用 search_kg 查找问题中涉及的关键实体
- 第三步：如发现有价值的实体，用 explore_graph 探索其关联
- 第四步：综合所有信息，给出最终答案

## 回答规则
- **只能**基于检索到的内容回答，禁止使用外部知识
- 如检索结果不足以回答，明确告知用户
- 引用具体的文档来源
- 按步骤列出操作建议

## 输出格式（JSON 结构化输出）
每次必须仅输出一个 JSON 对象，不要 markdown 围栏、不要任何多余文字：
{"action": "search_kb | search_kg | explore_graph | answer", "query": "传给工具的查询词", "reasoning": "一句话决策理由"}

- action=answer 时 query 留空（最终回答由后续节点生成）
- 只能基于检索到的内容作答，禁止使用外部知识
- 如检索结果不足以回答，选择 answer 并在最终回答中明确说明信息不足""" + UNTRUSTED_DATA_GUARD

    def _setup_agentic_rag(self):
        """构建 LangGraph ReAct Agent（支持 Ollama 和 DeepSeek）。"""
        try:
            from langgraph.graph import StateGraph, END
            from langgraph.types import StreamWriter
            from typing import TypedDict, Annotated, List as ListType
        except ImportError:
            logger.warning("langgraph 未安装，回退到简单 RAG")
            self._setup_legacy_agent()
            return

        # 检查 KG 是否就绪
        has_kg = self.kg_store is not None and self.kg_store.graph.number_of_nodes() > 0
        if not has_kg:
            logger.info("知识图谱为空或未启用，Agentic RAG 仅使用向量检索")

        kb = self

        # ---- State ----
        class AgenticState(TypedDict):
            messages: list
            question: str
            kb_context: str
            kg_context: str
            reasoning_steps: int
            next_action: str
            final_answer: str

        # ---- Router Node ----
        def router_node(state: dict) -> dict:
            step = state.get("reasoning_steps", 0) + 1
            max_steps = self._get_agentic_config("max_reasoning_steps", 5)

            if step > max_steps:
                return {"next_action": "answer", "reasoning_steps": step}

            # 构建 router prompt
            tool_list = "search_kb, answer"
            if has_kg:
                tool_list = "search_kb, search_kg, explore_graph, answer"

            context_summary = ""
            if state.get("kb_context"):
                context_summary += f"[向量检索结果] {state['kb_context'][:500]}\n"
            if state.get("kg_context"):
                context_summary += f"[图谱检索结果] {state['kg_context'][:500]}\n"

            router_msg = f"""问题: {state['question']}

当前步骤: {step}/{max_steps}
已获取的信息:
{context_summary if context_summary else '（暂无，这是第一步）'}

可用工具: {tool_list}

基于当前信息，决定下一步。如果已有足够信息回答用户，选择 answer。"""

            try:
                action, tool_input = kb._router_decide(router_msg, has_kg)
            except Exception as e:
                logger.warning(f"Router 决策失败: {e}")
                action, tool_input = "search_kb", ""

            if not tool_input:
                tool_input = state["question"]

            return {
                "messages": state.get("messages", []) + [
                    {"role": "assistant", "content": f"[router] action={action}"}
                ],
                "next_action": action,
                "reasoning_steps": step,
                "_tool_input": tool_input,
            }

        # ---- search_kb Node ----
        def search_kb_node(state: dict) -> dict:
            query = state.get("_tool_input", state["question"])
            ctx = kb._retrieve_context(query)
            existing = state.get("kb_context", "")
            return {"kb_context": (existing + "\n\n" + ctx).strip(), "_tool_input": ""}

        # ---- search_kg Node ----
        def search_kg_node(state: dict) -> dict:
            if not has_kg:
                return {}
            query = state.get("_tool_input", state["question"])
            ctx = kb.kg_store.to_context_string(query)
            existing = state.get("kg_context", "")
            return {"kg_context": (existing + "\n\n" + ctx).strip(), "_tool_input": ""}

        # ---- explore_graph Node ----
        def explore_graph_node(state: dict) -> dict:
            if not has_kg:
                return {}
            query = state.get("_tool_input", state["question"])
            # 查找第一个匹配的实体，提取其子图
            nodes = kb.kg_store.search_nodes(query, limit=3)
            if not nodes:
                return {"kg_context": state.get("kg_context", "") + f"\n[图谱探索] 未找到与 '{query}' 相关的实体", "_tool_input": ""}

            parts = []
            for node in nodes[:2]:
                sub = kb.kg_store.extract_subgraph(node["id"], depth=2)
                related_names = [n["name"] for n in sub.get("nodes", []) if n.get("name") != node["name"]]
                parts.append(f"[{node['type']}] {node['name']} → 关联: {', '.join(related_names[:10])}")

            existing = state.get("kg_context", "")
            return {"kg_context": (existing + "\n[图谱探索]\n" + "\n".join(parts)).strip(), "_tool_input": ""}

        # ---- Answer Node ----
        def answer_node(state: dict, writer: StreamWriter) -> dict:
            kb_ctx = state.get("kb_context", "")
            kg_ctx = state.get("kg_context", "")

            answer_prompt = f"""基于以下检索到的信息回答用户问题。

## 知识库检索结果
{wrap_untrusted(kb_ctx, '知识库检索结果')}

## 知识图谱信息
{wrap_untrusted(kg_ctx, '知识图谱信息')}

## 用户问题
{state['question']}

## 回答要求
1. 严格基于上方检索资料回答，禁止使用资料以外的知识；
2. 引用资料时必须使用《文档名》格式（如"根据《xxx手册.md》"），可同时标注 [参考资料N]；
3. 若检索资料与问题无关或不足以回答，必须仅回复："抱歉，知识库中未找到相关信息，请补充相关文档后重试。"，不得给出资料以外的具体建议。"""

            answer = ""
            try:
                # 流式生成：writer 把 token 推给 stream_mode="custom" 的消费者
                for chunk in kb.llm.stream([
                    {"role": "system", "content": "你是一个 OA 运维知识库助手。严格基于提供的检索信息回答，禁止编造；引用使用《文档名》格式，资料不足时按回答要求明确拒答。"},
                    {"role": "user", "content": answer_prompt},
                ]):
                    token = ""
                    content = getattr(chunk, "content", chunk)
                    if isinstance(content, list):
                        token = "".join(
                            b.get("text", "") if isinstance(b, dict) else str(b)
                            for b in content
                        )
                    else:
                        token = str(content or "")
                    if token:
                        answer += token
                        writer({"token": token})
            except Exception as e:
                logger.warning(f"Answer LLM 流式调用失败: {e}")
                answer = f"[检索模式] 以下是与您问题相关的内容（LLM 暂不可用）：\n\n{kb_ctx}\n\n{kg_ctx}"
                writer({"token": answer})

            return {
                "final_answer": answer,
                "messages": state.get("messages", []) + [{"role": "assistant", "content": answer}],
            }

        # ---- Build Graph ----
        builder = StateGraph(AgenticState)

        builder.add_node("router", router_node)
        builder.add_node("search_kb", search_kb_node)
        builder.add_node("search_kg", search_kg_node)
        builder.add_node("explore_graph", explore_graph_node)
        builder.add_node("answer", answer_node)

        builder.set_entry_point("router")

        # Conditional edges from router
        def route_after_router(state: dict) -> str:
            return state.get("next_action", "search_kb")

        builder.add_conditional_edges("router", route_after_router, {
            "search_kb": "search_kb",
            "search_kg": "search_kg",
            "explore_graph": "explore_graph",
            "answer": "answer",
        })

        # Tool nodes → back to router
        builder.add_edge("search_kb", "router")
        builder.add_edge("search_kg", "router")
        builder.add_edge("explore_graph", "router")

        # Answer → END
        builder.add_edge("answer", END)

        self.agentic_graph = builder.compile()
        logger.info(f"Agentic RAG (LangGraph ReAct) 已就绪" + (" (含 KG)" if has_kg else " (仅向量检索)"))

    def _router_invoke(self, messages: list):
        """路由 LLM 调用：结构化输出优先，失败回退文本 JSON 解析。

        Returns: (RouterDecision | None, raw_text)
        """
        if self._structured_ok is not False:
            try:
                out = self.llm.with_structured_output(RouterDecision).invoke(messages)
                self._structured_ok = True
                if isinstance(out, RouterDecision):
                    return out, ""
                if isinstance(out, dict):
                    try:
                        return RouterDecision.model_validate(out), ""
                    except ValidationError:
                        pass
            except Exception as e:
                self._structured_ok = False
                logger.info(f"路由结构化输出不可用，改用文本 JSON 解析: {e}")
        try:
            resp = self.llm.invoke(messages)
        except Exception as e:
            logger.warning(f"Router LLM 调用失败: {e}")
            return None, ""
        content = resp.content if hasattr(resp, "content") else str(resp)
        return parse_structured(content, RouterDecision), content

    def _router_decide(self, router_msg: str, has_kg: bool):
        """路由决策 → (action, tool_input)。解析失败安全默认 search_kb。"""
        messages = [
            {"role": "system", "content": self.AGENTIC_RAG_PROMPT},
            {"role": "user", "content": router_msg},
        ]
        decision, _content = self._router_invoke(messages)
        if decision is None:
            return "search_kb", ""
        action = decision.action
        if action in ("search_kg", "explore_graph") and not has_kg:
            action = "search_kb"
        return action, (decision.query or "").strip()

    def _get_agentic_config(self, key: str, default):
        """读取 agentic_rag 配置项。"""
        try:
            from utils.config import config as app_config
            return app_config.get(f"knowledge_base.agentic_rag.{key}", default)
        except Exception:
            return default

    # ========== 混合检索（RAG 2.0：BM25 + Dense + RRF） ==========

    def _load_retrieval_config(self) -> RetrievalConfig:
        """从 config.yaml 读取检索配置（knowledge_base.retrieval，缺省用默认值）。"""
        try:
            from utils.config import config as app_config
            data = app_config.get("knowledge_base.retrieval", {}) or {}
        except Exception:
            data = {}
        return RetrievalConfig.from_mapping(data)

    def _build_reranker(self):
        """按配置构建可选重排序组件（quality 模式；不可用时自动直通，不影响检索）。"""
        try:
            from utils.rerank import build_reranker
            return build_reranker(
                enabled=bool(self.retrieval_config.rerank),
                model_name=self.retrieval_config.rerank_model,
            )
        except Exception as e:
            logger.warning(f"重排序组件构建失败（忽略，检索直通）: {e}")
            return None

    def _chunking_strategy(self) -> str:
        """分块策略：semantic（RAG 2.0 默认，标题/段落/页）/ fixed（旧固定切块）。"""
        try:
            from utils.config import config as app_config
            return str(app_config.get("knowledge_base.chunking.strategy", "semantic")).lower()
        except Exception:
            return "semantic"

    def _retrieve(self, query: str, top_k: Optional[int] = None) -> Optional[RetrievalResult]:
        """执行混合检索；检索器异常时返回 None（调用方降级旧路径）。"""
        if self.retriever is None:
            return None
        try:
            return self.retriever.retrieve(query, top_k=top_k)
        except Exception as e:
            logger.warning(f"混合检索失败，降级旧检索: {e}")
            return None

    def _dense_search(self, query: str, k: int):
        """dense 通道适配器：Chroma 结果 → (id, text, metadata, 余弦相似度)。"""
        pairs = self.vector_store.similarity_search_with_score(query, k=k)
        hits = []
        for i, (doc, distance) in enumerate(pairs):
            meta = dict(doc.metadata or {})
            hits.append((
                self._canonical_chunk_id(meta, fallback_index=i),
                doc.page_content,
                meta,
                self._distance_to_similarity(distance),
            ))
        return hits

    def _corpus_snapshot(self):
        """BM25 语料快照：(version, [(id, text, metadata)])；version 变化触发索引重建。"""
        data = self.vector_store.get()
        ids = data.get("ids") or []
        docs = data.get("documents") or []
        metas = data.get("metadatas") or []
        entries = []
        for i, _chroma_id in enumerate(ids):
            text = docs[i] if i < len(docs) else ""
            meta = dict(metas[i] or {}) if i < len(metas) else {}
            entries.append((self._canonical_chunk_id(meta, fallback_index=i), text or "", meta))
        return self._corpus_version, entries

    @staticmethod
    def _canonical_chunk_id(meta: dict, fallback_index: int = 0) -> str:
        """两通道（dense/bm25）统一的块 ID：chunk_uid 优先，旧数据回退 source#chunk_index。"""
        uid = meta.get("chunk_uid")
        if uid:
            return str(uid)
        source = meta.get("source")
        if source and meta.get("chunk_index") is not None:
            return f"{source}#{meta.get('chunk_index')}"
        return f"chunk:{fallback_index}"

    @staticmethod
    def _distance_to_similarity(distance) -> float:
        """Chroma 默认 L2 距离（归一化向量）→ 余弦相似度：cos = 1 - d²/2。"""
        try:
            d = float(distance)
        except (TypeError, ValueError):
            return 0.0
        return max(-1.0, min(1.0, 1.0 - (d * d) / 2.0))

    def _bump_corpus(self) -> None:
        """知识库发生变更后调用：使 BM25 缓存失效，下次检索自动重建。"""
        self._corpus_version = getattr(self, "_corpus_version", 0) + 1
        if getattr(self, "retriever", None) is not None:
            self.retriever.invalidate()

    # ========== 核心功能 ==========

    def _retrieve_context(self, query: str, top_k: int = 5) -> str:
        """
        检索与查询最相关的文档块（RAG 2.0 混合检索；失败自动降级旧向量检索）。

        Returns:
            带精确引用（文档/页码/章节/Chunk ID）的拼接上下文；证据不足时返回拒答文案
        """
        result = self._retrieve(query, top_k=top_k)
        if result is not None:
            if result.refused:
                return result.refuse_message or DEFAULT_REFUSE_MESSAGE
            if result.hits:
                return format_citations(result.hits)
            return "知识库为空，未检索到任何相关内容。"

        # ---- 降级：旧 dense-only 路径 ----
        try:
            docs = self.vector_store.similarity_search(query, k=top_k)

            if not docs:
                return "知识库为空，未检索到任何相关内容。"

            context_parts = []
            for i, doc in enumerate(docs, 1):
                source = doc.metadata.get("source", "未知")
                chunk_idx = doc.metadata.get("chunk_index", "?")
                context_parts.append(
                    f"[参考资料{i}] 来源:《{source}》(片段{chunk_idx})\n{doc.page_content}"
                )

            return "\n\n".join(context_parts)

        except Exception as e:
            logger.error(f"检索失败: {e}")
            return f"检索出错: {str(e)}"

    def retrieve(self, question: str, top_k: int = 5) -> Dict[str, Any]:
        """结构化检索（不调用 LLM），供 /api/v1/rag/query 等程序化消费。

        Returns:
            {
              "available": 混合检索是否可用（旧向量库/异常时为 False）,
              "query": 规范化后的查询,
              "refused": 证据是否不足（分层证据门）,
              "refuse_message": 拒答文案,
              "hits": [{source, text, score, bm25_score, chunk_uid}],
              "citations": ["《文档名》", ...],
              "context": 带精确引用的拼接上下文,
            }
        """
        result = self._retrieve(question, top_k=top_k)
        if result is None:
            return {
                "available": False,
                "query": question,
                "refused": False,
                "refuse_message": "",
                "hits": [],
                "citations": [],
                "context": "",
            }

        hits = [
            {
                "source": str(hit.metadata.get("source") or "未知"),
                "text": hit.text,
                "score": hit.dense_similarity,
                "bm25_score": hit.bm25_score,
                "chunk_uid": str(hit.metadata.get("chunk_uid") or ""),
            }
            for hit in result.hits
        ]
        citations: List[str] = []
        for hit in result.hits:
            source = str(hit.metadata.get("source") or "未知")
            ref = source if source.startswith("《") else f"《{source}》"
            if ref not in citations:
                citations.append(ref)
        context = format_citations(result.hits) if (result.hits and not result.refused) else ""
        return {
            "available": True,
            "query": result.query,
            "refused": result.refused,
            "refuse_message": result.refuse_message or "",
            "hits": hits,
            "citations": citations,
            "context": context,
        }

    def import_document(self, file_path: str) -> str:
        """
        导入运维文档到知识库：解析 → 分块 → 向量化 → 存入Chroma。

        Args:
            file_path: 文档文件路径（支持 .pdf / .docx / .txt）

        Returns:
            导入结果摘要
        """
        if not os.path.exists(file_path):
            return f"[错误] 文件不存在: {file_path}"

        file_name = os.path.basename(file_path)

        # 检查是否已导入（通过文件hash去重）
        file_hash = self._file_hash(file_path)
        existing = self.vector_store.get(where={"file_hash": file_hash})
        if existing and existing["ids"]:
            return f"[提示] 文档 '{file_name}' 已导入过，无需重复导入。如需更新请先删除旧版。"

        logger.info(f"开始导入文档: {file_name}")

        try:
            # 第1步：解析文档为纯文本
            raw_text = parse_document(file_path)
            if not raw_text.strip():
                return f"[错误] 文档 '{file_name}' 解析后内容为空，请检查文件是否有效。"

            # 第2步：分块（RAG 2.0 默认语义切块：标题/段落/页；fixed 保留旧算法）
            strategy = self._chunking_strategy()
            if strategy == "fixed":
                chunks = [
                    Chunk(text=t, index=i + 1, uid=f"{file_hash}:{i + 1}")
                    for i, t in enumerate(
                        split_text(raw_text, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP)
                    )
                ]
            else:
                chunks = self._semantic_chunks(file_path, raw_text, file_hash)
            logger.info(f"  文档分块完成: {len(chunks)} 块（策略: {strategy}）")

            # 第3步：构建LangChain Document对象列表（含页/章节/uid 元数据）
            documents = []
            for c in chunks:
                metadata = {
                    "source": file_name,
                    "file_path": file_path,
                    "chunk_index": c.index,
                    "total_chunks": len(chunks),
                    "file_hash": file_hash,
                    "import_time": datetime.now().isoformat(),
                    "chunk_size": len(c.text),
                    "chunk_uid": c.uid,
                }
                if c.section:
                    metadata["section"] = c.section
                if c.page is not None:
                    metadata["page"] = int(c.page)
                documents.append(Document(page_content=c.text, metadata=metadata))

            # 第4步：向量化并存入Chroma
            self.vector_store.add_documents(documents)
            self._bump_corpus()  # 语料变化 → BM25 缓存失效
            logger.info(f"  向量化并存入Chroma完成")

            # 第5步：构建知识图谱（非阻塞，失败不影响向量库）
            kg_entity_count = 0
            if self.kg_builder is not None:
                try:
                    kg_entity_count = self.kg_builder.add_document(raw_text, file_name)
                    self.kg_store.save()
                    logger.info(f"  知识图谱构建完成: {kg_entity_count} 个实体")
                except Exception as e:
                    logger.warning(f"  知识图谱构建失败（向量库已正常导入）: {e}")

            summary = (
                f"文档导入成功！\n"
                f"  文件名: {file_name}\n"
                f"  文档长度: {len(raw_text)} 字符\n"
                f"  分块数量: {len(chunks)} 块 ({strategy} 策略, 向量库)\n"
                + (f"  实体提取: {kg_entity_count} 个实体 (知识图谱)\n" if kg_entity_count else "")
                + f"  分块大小: {CHUNK_SIZE} 字/块 (重叠 {CHUNK_OVERLAP} 字)\n"
                f"  存储位置: {CHROMA_DB_DIR}"
            )
            return summary

        except Exception as e:
            logger.error(f"文档导入失败: {e}")
            return f"[错误] 文档导入失败: {str(e)}"

    def _semantic_chunks(self, file_path: str, raw_text: str, file_hash: str) -> List[Chunk]:
        """语义切块：PDF 优先页级（引用页码精确），其余走标题/段落切块。"""
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".pdf":
            try:
                pages = parse_pdf_pages(file_path)
            except Exception as e:
                logger.warning(f"  页级解析失败，退回整文切块: {e}")
                pages = []
            if pages:
                return split_semantic_pages(
                    pages, doc_id=file_hash, max_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP
                )
        return split_semantic(
            raw_text, doc_id=file_hash, max_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP
        )

    # ========== 对话 Chatbot ==========

    def chat(self, message: str, conversation_id: str = "default", stream: bool = False):
        """
        多轮对话问答（带记忆，持久化到 SQLite）。

        Args:
            message: 用户当前消息
            conversation_id: 对话 ID（不同 ID 独立记忆）
            stream: 为 True 时返回生成器，逐 token 产出回答（SSE 流式用）

        Returns:
            stream=False 返回完整回答字符串；stream=True 返回生成器
        """
        if not message.strip():
            return "[提示] 请输入您想咨询的运维问题。"

        # 懒加载：内存没有则从数据库恢复该对话
        if conversation_id not in self.conversations:
            self.conversations[conversation_id] = self._load_conversation_from_db(conversation_id)
        history = self.conversations[conversation_id]

        # 新会话自动建号并生成标题（取首条用户消息）
        if not history:
            self._ensure_conversation_row(conversation_id, message)

        logger.info(f"对话 [{conversation_id}] 第 {len(history)//2 + 1} 轮: {message[:50]}...")

        # 构建对话上下文
        memory_turns = self._get_agentic_config("chat_max_turns", 10)
        chat_context = self._format_chat_history(history, max_turns=memory_turns)

        # 流式模式：返回生成器（由 SSE 端点逐 token 消费）
        if stream:
            return self._chat_stream(message, chat_context, conversation_id, history)

        # ---- Agentic RAG 路径 ----
        if self.agentic_graph is not None:
            answer = self._query_agentic(message, chat_history=chat_context)
        else:
            answer = self._query_legacy(message, chat_history=chat_context)

        # 保存到内存历史
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": answer})

        # 持久化到 SQLite（每条消息落库，重启不丢）
        try:
            db.save_message(conversation_id, "user", message)
            db.save_message(conversation_id, "assistant", answer)
        except Exception as e:
            logger.warning(f"对话历史持久化失败: {e}")

        # 限制内存历史长度（最多保留 30 轮 = 60 条消息；数据库保留全量）
        max_messages = self._get_agentic_config("chat_max_history", 30) * 2
        if len(history) > max_messages:
            self.conversations[conversation_id] = history[-max_messages:]

        return answer

    def _ensure_conversation_row(self, conversation_id: str, first_message: str):
        """确保 conversations 表存在该会话；新会话自动生成标题（首条用户消息截断）。"""
        title = (first_message.strip() or "新对话")[:20]
        try:
            db.create_conversation(title, conversation_id=conversation_id)
        except Exception:
            pass  # 已存在
        # 若会话还没有任何消息且标题仍是默认值，则用首条用户消息更新标题
        try:
            if not db.get_conversation_messages(conversation_id, limit=1):
                convs = db.list_conversations(limit=200)
                cur = next((c for c in convs if c["id"] == conversation_id), None)
                if cur and cur.get("title", "").strip() in ("", "新对话"):
                    db.rename_conversation(conversation_id, title)
        except Exception:
            pass

    def _load_conversation_from_db(self, conversation_id: str) -> List[Dict]:
        """从数据库恢复对话历史（按内存上限截取，供 LLM 上下文使用）。"""
        rows = db.get_conversation_messages(conversation_id)
        max_messages = self._get_agentic_config("chat_max_history", 30) * 2
        recent = rows[-max_messages:]
        return [{"role": r["role"], "content": r["content"]} for r in recent]

    def clear_conversation(self, conversation_id: str = "default"):
        """清除指定对话（内存 + 数据库）。"""
        self.conversations.pop(conversation_id, None)
        try:
            db.delete_conversation(conversation_id)
        except Exception as e:
            logger.warning(f"删除对话失败: {e}")

    def get_conversation(self, conversation_id: str = "default") -> List[Dict]:
        """获取对话历史（优先从数据库读全量，内存仅作缓存）。"""
        try:
            rows = db.get_conversation_messages(conversation_id)
            if rows:
                return [{"role": r["role"], "content": r["content"]} for r in rows]
        except Exception:
            pass
        return self.conversations.get(conversation_id, [])

    def create_conversation(self, title: str = "新对话") -> str:
        """新建对话，返回对话 ID。"""
        return db.create_conversation(title)

    def list_conversations(self, limit: int = 50) -> List[Dict]:
        """对话列表（按最近更新倒序）。"""
        return db.list_conversations(limit)

    def delete_conversation(self, conversation_id: str) -> bool:
        """删除指定对话（内存 + 数据库）。"""
        self.conversations.pop(conversation_id, None)
        return db.delete_conversation(conversation_id)

    def rename_conversation(self, conversation_id: str, title: str) -> bool:
        """重命名对话标题。"""
        return db.rename_conversation(conversation_id, title)

    def _format_chat_history(self, history: List[Dict], max_turns: int = 10) -> str:
        """将对话历史格式化为上下文字符串。"""
        if not history:
            return "（这是对话的第一轮）"

        recent = history[-max_turns * 2:]  # 每轮 = user + assistant
        lines = ["## 对话历史"]
        for i, msg in enumerate(recent):
            role = "用户" if msg["role"] == "user" else "助手"
            content = msg["content"][:500]  # 每条消息最多 500 字，避免撑爆上下文
            lines.append(f"{role}: {content}")
        return "\n".join(lines)

    # ========== 单次问答 ==========

    def query(self, question: str) -> str:
        """
        单次问答（无记忆），基于 RAG / Agentic RAG。

        Args:
            question: 用户的运维问题
        """
        if not question.strip():
            return "[提示] 请输入您想咨询的运维问题。"

        logger.info(f"问答: {question[:50]}...")

        if self.agentic_graph is not None:
            return self._query_agentic(question)

        return self._query_legacy(question)

    def _query_agentic(self, question: str, chat_history: str = "") -> str:
        """LangGraph ReAct Agent 多步推理问答。"""
        pre = self._retrieve(question)
        if pre is not None and pre.refused:
            return pre.refuse_message or DEFAULT_REFUSE_MESSAGE
        try:
            # 将对话历史注入到初始消息
            initial_messages = []
            if chat_history:
                initial_messages.append({
                    "role": "user",
                    "content": f"{wrap_untrusted(chat_history, '对话历史')}\n\n## 当前问题\n{question}"
                })
                # 把历史当上下文给 router，但把原始问题保留给检索
                effective_question = question
            else:
                effective_question = question

            result = self.agentic_graph.invoke({
                "messages": initial_messages,
                "question": effective_question,
                "kb_context": "",
                "kg_context": "",
                "reasoning_steps": 0,
                "next_action": "search_kb",
                "final_answer": "",
            })
            answer = result.get("final_answer", "")
            if answer:
                return answer
            # 回退：从 messages 中提取最后一条
            messages = result.get("messages", [])
            if messages:
                return messages[-1].get("content", "问答处理异常，请重试")
            return "问答处理异常，请重试"

        except Exception as e:
            logger.warning(f"Agentic RAG 异常，降级到检索模式: {e}")
            context = self._retrieve_context(question)
            if self.kg_store:
                try:
                    kg_ctx = self.kg_store.to_context_string(question)
                    context = context + "\n\n[知识图谱]\n" + kg_ctx
                except Exception:
                    pass
            return (
                f"[检索降级模式] 以下是与您问题相关的知识库内容：\n\n"
                f"{context}"
            )

    def _query_legacy(self, question: str, chat_history: str = "") -> str:
        """简单 RAG 问答（v2.x 兼容）。"""
        pre = self._retrieve(question)
        if pre is not None and pre.refused:
            return pre.refuse_message or DEFAULT_REFUSE_MESSAGE
        prompt = question
        if chat_history:
            prompt = f"{wrap_untrusted(chat_history, '对话历史')}\n\n## 当前问题\n{question}"

        try:
            result = self.agent.invoke({
                "messages": [
                    {"role": "user", "content": prompt}
                ]
            })
            messages = result.get("messages", [])
            return messages[-1].content if messages else "问答处理异常，请重试"
        except Exception as e:
            logger.warning(f"LLM调用异常，降级为检索模式: {e}")
            context = self._retrieve_context(question)
            return (
                f"[检索模式] 以下是与您问题相关的知识库内容（LLM暂不可用，请自行参考）：\n\n"
                f"{context}"
            )
    def _chat_stream(self, message: str, chat_context: str, conversation_id: str, history: List[Dict]):
        """生成器：流式产出回答，结束时持久化对话（用户消息先落库，生成失败也不丢）。"""
        # 用户消息先持久化
        try:
            db.save_message(conversation_id, "user", message)
        except Exception as e:
            logger.warning(f"对话历史持久化失败: {e}")
        history.append({"role": "user", "content": message})

        if self.agentic_graph is not None:
            gen = self._stream_agentic(message, chat_history=chat_context)
        else:
            gen = self._stream_legacy(message, chat_history=chat_context)

        answer = ""
        for chunk in gen:
            answer += chunk
            yield chunk

        # 助手回答持久化
        history.append({"role": "assistant", "content": answer})
        try:
            db.save_message(conversation_id, "assistant", answer)
        except Exception as e:
            logger.warning(f"对话历史持久化失败: {e}")

        # 限制内存历史长度（数据库保留全量）
        max_messages = self._get_agentic_config("chat_max_history", 30) * 2
        if len(history) > max_messages:
            self.conversations[conversation_id] = history[-max_messages:]

    def _stream_agentic(self, question: str, chat_history: str = ""):
        """生成器：流式产出 Agentic RAG 最终回答的 token（stream_mode='custom'）。"""
        pre = self._retrieve(question)
        if pre is not None and pre.refused:
            yield pre.refuse_message or DEFAULT_REFUSE_MESSAGE
            return
        try:
            initial_messages = []
            if chat_history:
                initial_messages.append({
                    "role": "user",
                    "content": f"{wrap_untrusted(chat_history, '对话历史')}\n\n## 当前问题\n{question}"
                })
            input_state = {
                "messages": initial_messages,
                "question": question,
                "kb_context": "",
                "kg_context": "",
                "reasoning_steps": 0,
                "next_action": "search_kb",
                "final_answer": "",
            }
            full = ""
            for event in self.agentic_graph.stream(input_state, stream_mode="custom"):
                token = (event or {}).get("token", "")
                if token:
                    full += token
                    yield token
            if not full:
                # 流式未产出（极端情况），退回一次性生成
                yield self._query_agentic(question, chat_history=chat_history)
        except Exception as e:
            logger.warning(f"Agentic RAG 流式异常，降级到检索模式: {e}")
            context = self._retrieve_context(question)
            if self.kg_store:
                try:
                    kg_ctx = self.kg_store.to_context_string(question)
                    context = context + "\n\n[知识图谱]\n" + kg_ctx
                except Exception:
                    pass
            yield f"[检索降级模式] 以下是与您问题相关的知识库内容：\n\n{context}"

    def _stream_legacy(self, question: str, chat_history: str = ""):
        """简单 RAG 问答（一次性产出，兼容非流式后端）。"""
        yield self._query_legacy(question, chat_history=chat_history)
    def list_documents(self) -> str:
        """
        列出知识库中所有已导入的文档清单。

        Returns:
            文档列表字符串
        """
        try:
            all_docs = self.vector_store.get()

            if not all_docs or not all_docs["ids"]:
                return "知识库当前为空，请先导入运维文档。"

            # 按文档来源分组统计
            doc_map: Dict[str, Dict] = {}
            for metadata in all_docs["metadatas"]:
                source = metadata.get("source", "未知")
                if source not in doc_map:
                    doc_map[source] = {
                        "chunks": 0,
                        "total_chars": 0,
                        "import_time": metadata.get("import_time", "未知"),
                    }
                doc_map[source]["chunks"] += 1
                doc_map[source]["total_chars"] += metadata.get("chunk_size", 0)

            lines = [f"知识库文档清单（共 {len(doc_map)} 份文档）：", "-" * 50]
            for i, (name, info) in enumerate(doc_map.items(), 1):
                lines.append(
                    f"{i}. {name}\n"
                    f"   分块数: {info['chunks']} | 总字数: {info['total_chars']} | "
                    f"导入时间: {info['import_time'][:19] if info['import_time'] != '未知' else '未知'}"
                )

            return "\n".join(lines)

        except Exception as e:
            return f"[错误] 获取文档列表失败: {str(e)}"

    def delete_document(self, doc_name: str) -> str:
        """
        从知识库中删除指定文档的所有分块。

        Args:
            doc_name: 文档文件名（如 '操作手册.pdf'）

        Returns:
            删除结果
        """
        try:
            # Chroma的delete需要先检索再删除
            all_data = self.vector_store.get()

            if not all_data["ids"]:
                return "知识库为空，没有可删除的文档。"

            # 找到属于该文档的所有记录ID
            ids_to_delete = []
            for i, metadata in enumerate(all_data["metadatas"]):
                if metadata.get("source") == doc_name:
                    ids_to_delete.append(all_data["ids"][i])

            if not ids_to_delete:
                return f"未找到文档 '{doc_name}'，请确认文件名是否正确。可用文档: {self._list_doc_names()}"

            # 删除找到的分块
            self.vector_store.delete(ids=ids_to_delete)
            self._bump_corpus()  # 语料变化 → BM25 缓存失效
            logger.info(f"已删除文档 '{doc_name}' 的 {len(ids_to_delete)} 个分块")

            # 同步清理知识图谱
            if self.kg_builder is not None:
                try:
                    self.kg_builder.remove_document(doc_name)
                    self.kg_store.save()
                except Exception as e:
                    logger.warning(f"KG 清理失败（不影响向量库删除）: {e}")

            return f"文档 '{doc_name}' 已从知识库删除（共移除 {len(ids_to_delete)} 个分块）。"

        except Exception as e:
            return f"[错误] 删除文档失败: {str(e)}"

    def get_document_text(self, doc_name: str) -> str:
        """
        获取指定文档的完整文本内容（所有分块拼接）。

        Args:
            doc_name: 文档文件名（如 '操作手册.pdf'）

        Returns:
            文档全文，或错误提示
        """
        try:
            all_data = self.vector_store.get()
            if not all_data["ids"]:
                return "[错误] 知识库为空。"

            # 找到该文档的所有分块，按 chunk_index 排序
            chunks = []
            for i, meta in enumerate(all_data["metadatas"]):
                if meta.get("source", "") == doc_name:
                    chunks.append((
                        meta.get("chunk_index", 0),
                        all_data["documents"][i] if all_data["documents"] else ""
                    ))

            if not chunks:
                return f"[错误] 未找到文档 '{doc_name}'。"

            # 按分块序号排序后拼接
            chunks.sort(key=lambda x: x[0])
            full_text = "\n\n".join(c[1] for c in chunks)
            return full_text

        except Exception as e:
            return f"[错误] 获取文档内容失败: {str(e)}"

    def get_stats(self) -> str:
        """
        获取知识库统计信息（含知识图谱）。

        Returns:
            统计数据字符串
        """
        try:
            all_data = self.vector_store.get()
            if not all_data["ids"]:
                base = "知识库为空，尚未导入任何文档。"
            else:
                total_chunks = len(all_data["ids"])
                total_chars = sum(
                    m.get("chunk_size", 0) for m in all_data["metadatas"]
                )
                unique_docs = len(set(
                    m.get("source", "") for m in all_data["metadatas"]
                ))
                base = (
                    f"知识库概况:\n"
                    f"  文档数量: {unique_docs} 份\n"
                    f"  分块总数: {total_chunks} 块\n"
                    f"  总字数: {total_chars} 字\n"
                    f"  存储路径: {CHROMA_DB_DIR}"
                )

            # 附加 KG 统计
            if self.kg_store is not None:
                try:
                    kg_stats = self.kg_store.get_stats()
                    base += (
                        f"\n\n知识图谱概况:\n"
                        f"  实体总数: {kg_stats['total_nodes']}\n"
                        f"  关联边数: {kg_stats['total_edges']}\n"
                        f"  实体类型: {kg_stats['nodes_by_type']}\n"
                        f"  存储路径: {kg_stats['storage_dir']}"
                    )
                except Exception:
                    pass

            return base

        except Exception as e:
            return f"统计获取失败: {str(e)}"

    def close(self):
        """释放向量库资源（进程退出前调用）"""
        try:
            if hasattr(self, "vector_store") and self.vector_store:
                self.vector_store._client.clear_system_cache()
        except Exception:
            pass
        logger.info("知识库资源已释放")

    def clear_knowledge_base(self) -> str:
        """
        清空整个知识库（危险操作，需确认）。

        Returns:
            操作结果
        """
        try:
            all_data = self.vector_store.get()
            if all_data["ids"]:
                count = len(all_data["ids"])
                self.vector_store.delete(ids=all_data["ids"])
                self._bump_corpus()  # 语料变化 → BM25 缓存失效
                logger.info(f"知识库已清空，共删除 {count} 条记录")

                # 同步清空知识图谱
                if self.kg_store is not None:
                    try:
                        self.kg_store.clear()
                    except Exception as e:
                        logger.warning(f"KG 清空失败: {e}")

                return f"知识库已清空，共删除 {count} 个文档分块。"
            return "知识库原本就是空的。"
        except Exception as e:
            return f"[错误] 清空知识库失败: {str(e)}"

    # ========== 辅助方法 ==========

    def _file_hash(self, file_path: str) -> str:
        """计算文件的MD5哈希值，用于去重"""
        hasher = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    def _list_doc_names(self) -> str:
        """列出知识库中的所有文档名称"""
        all_data = self.vector_store.get()
        if not all_data["ids"]:
            return "空"
        names = sorted(set(m.get("source", "未知") for m in all_data["metadatas"]))
        return ", ".join(names)


# ========== 快速测试入口 ==========

if __name__ == "__main__":
    print("=== 知识库Agent 本地功能测试 ===\n")

    # 注意：以下测试会下载嵌入模型（首次约118MB），需要网络连接

    # 测试1: 文本分块
    sample_text = """
OA系统运维手册

第一章 系统概述
OA系统是公司内部办公自动化系统，提供流程审批、文档管理、即时通讯等功能。

第二章 常见故障处理
2.1 OA页面无法访问
排查步骤：
1. 检查OA服务器网络连通性：ping oa-server
2. 检查Nginx服务状态：systemctl status nginx
3. 检查OA应用端口：netstat -tlnp | grep 8080
4. 查看应用日志：tail -200 /opt/oa/logs/catalina.out

2.2 审批流程卡死
审批流程卡死通常由以下原因导致：
1. 数据库锁等待
2. 外部接口调用超时
3. 流程引擎线程池耗尽
排查建议：先查数据库锁，再查应用日志中的超时信息。
"""
    print(f"原始文本长度: {len(sample_text)} 字")
    chunks = split_text(sample_text, chunk_size=300, overlap=30)
    print(f"分块结果: {len(chunks)} 块")

    # 测试2: 向量库（需要先下载模型）
    print("\n如需完整测试RAG功能，请配置LLM API密钥后运行:")
    print("  kb = KnowledgeBaseAgent(llm_api_key='your-key')")
    print("  kb.import_document('运维手册.pdf')")
    print("  answer = kb.query('OA页面无法访问怎么办？')")
    print("  print(answer)")
