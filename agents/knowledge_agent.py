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
from typing import List, Dict, Optional, Tuple

from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_openai import ChatOpenAI
from langchain_core.documents import Document

from utils.doc_parser import parse_document, split_text
from utils.logger import get_logger

logger = get_logger(__name__)

# HuggingFace连接优化：如果无法直连则使用国内镜像
if "HF_ENDPOINT" not in os.environ:
    os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

# ========== 路径配置 ==========
BASE_DIR = os.path.dirname(os.path.dirname(__file__))
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
4. 回答时引用具体的文档来源（如"根据《xxx文档》..."）
5. 如果参考资料中有操作步骤，请按序号列出并标注注意事项

## 回答格式
- 先给出直接答案
- 然后列出依据（引用的文档片段）
- 如果涉及操作，给出具体步骤和命令
"""

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

## 输出格式
每次你的回复必须以以下格式开头：
NEXT_ACTION: search_kb | search_kg | explore_graph | answer

如果是 answer，之后的内容就是给用户的最终回答。
如果是其他 action，之后的内容是传给该工具的查询关键词（纯文本，不含引号）。"""

    def _setup_agentic_rag(self):
        """构建 LangGraph ReAct Agent（支持 Ollama 和 DeepSeek）。"""
        try:
            from langgraph.graph import StateGraph, END
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
                resp = kb.llm.invoke([
                    {"role": "system", "content": kb.AGENTIC_RAG_PROMPT},
                    {"role": "user", "content": router_msg},
                ])
                content = resp.content if hasattr(resp, "content") else str(resp)

                # 解析 NEXT_ACTION
                import re as _re
                action_match = _re.search(r"NEXT_ACTION:\s*(\w+)", content, _re.IGNORECASE)
                if action_match:
                    action = action_match.group(1).strip().lower()
                    if action not in ("search_kb", "search_kg", "explore_graph", "answer"):
                        action = "search_kb"
                else:
                    # 如果 LLM 没有遵循格式，检查是否看起来像一个答案
                    if len(content) > 100 and "NEXT_ACTION" not in content.upper():
                        action = "answer"
                    else:
                        action = "search_kb"

                if action in ("search_kg", "explore_graph") and not has_kg:
                    action = "search_kb"

            except Exception as e:
                logger.warning(f"Router LLM 调用失败: {e}")
                action = "search_kb"

            # 提取工具参数（NEXT_ACTION 行之后的内容）
            tool_input = content
            if action_match:
                tool_input = content[action_match.end():].strip()
                if not tool_input:
                    tool_input = state["question"]

            return {
                "messages": state.get("messages", []) + [{"role": "assistant", "content": content}],
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
        def answer_node(state: dict) -> dict:
            kb_ctx = state.get("kb_context", "")
            kg_ctx = state.get("kg_context", "")

            answer_prompt = f"""基于以下检索到的信息回答用户问题。

## 知识库检索结果
{kb_ctx if kb_ctx else '（无结果）'}

## 知识图谱信息
{kg_ctx if kg_ctx else '（无图谱数据）'}

## 用户问题
{state['question']}

请给出完整、准确的回答。引用来源。如果信息不足，请明确说明。"""

            try:
                resp = kb.llm.invoke([
                    {"role": "system", "content": "你是一个 OA 运维知识库助手。严格基于提供的检索信息回答，禁止编造。"},
                    {"role": "user", "content": answer_prompt},
                ])
                answer = resp.content if hasattr(resp, "content") else str(resp)
            except Exception as e:
                logger.warning(f"Answer LLM 调用失败: {e}")
                answer = f"[检索模式] 以下是与您问题相关的内容（LLM 暂不可用）：\n\n{kb_ctx}\n\n{kg_ctx}"

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

    def _get_agentic_config(self, key: str, default):
        """读取 agentic_rag 配置项。"""
        try:
            from utils.config import config as app_config
            return app_config.get(f"knowledge_base.agentic_rag.{key}", default)
        except Exception:
            return default

    # ========== 核心功能 ==========

    def _retrieve_context(self, query: str, top_k: int = 5) -> str:
        """
        从向量库检索与查询最相关的文档块。

        Args:
            query: 查询文本
            top_k: 返回最相关的前K个结果

        Returns:
            拼接后的文档上下文，包含来源信息
        """
        try:
            docs = self.vector_store.similarity_search(query, k=top_k)

            if not docs:
                return "知识库为空，未检索到任何相关内容。"

            context_parts = []
            for i, doc in enumerate(docs, 1):
                source = doc.metadata.get("source", "未知")
                chunk_idx = doc.metadata.get("chunk_index", "?")
                context_parts.append(
                    f"[参考资料{i}] 来源: {source} (片段{chunk_idx})\n{doc.page_content}"
                )

            return "\n\n".join(context_parts)

        except Exception as e:
            logger.error(f"检索失败: {e}")
            return f"检索出错: {str(e)}"

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

            # 第2步：文本分块
            chunks = split_text(raw_text, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP)
            logger.info(f"  文档分块完成: {len(chunks)} 块")

            # 第3步：构建LangChain Document对象列表
            documents = []
            for i, chunk in enumerate(chunks):
                doc = Document(
                    page_content=chunk,
                    metadata={
                        "source": file_name,
                        "file_path": file_path,
                        "chunk_index": i + 1,
                        "total_chunks": len(chunks),
                        "file_hash": file_hash,
                        "import_time": datetime.now().isoformat(),
                        "chunk_size": len(chunk),
                    }
                )
                documents.append(doc)

            # 第4步：向量化并存入Chroma
            self.vector_store.add_documents(documents)
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
                f"  分块数量: {len(chunks)} 块 (向量库)\n"
                + (f"  实体提取: {kg_entity_count} 个实体 (知识图谱)\n" if kg_entity_count else "")
                + f"  分块大小: {CHUNK_SIZE} 字/块 (重叠 {CHUNK_OVERLAP} 字)\n"
                f"  存储位置: {CHROMA_DB_DIR}"
            )
            return summary

        except Exception as e:
            logger.error(f"文档导入失败: {e}")
            return f"[错误] 文档导入失败: {str(e)}"

    # ========== 对话 Chatbot ==========

    def chat(self, message: str, conversation_id: str = "default") -> str:
        """
        多轮对话问答（带记忆）。

        Args:
            message: 用户当前消息
            conversation_id: 对话 ID（不同 ID 独立记忆）

        Returns:
            基于知识库 + 对话历史的回答
        """
        if not message.strip():
            return "[提示] 请输入您想咨询的运维问题。"

        # 获取或创建对话历史
        if conversation_id not in self.conversations:
            self.conversations[conversation_id] = []
        history = self.conversations[conversation_id]

        logger.info(f"对话 [{conversation_id}] 第 {len(history)//2 + 1} 轮: {message[:50]}...")

        # 构建对话上下文
        memory_turns = self._get_agentic_config("chat_max_turns", 10)
        chat_context = self._format_chat_history(history, max_turns=memory_turns)

        # ---- Agentic RAG 路径 ----
        if self.agentic_graph is not None:
            answer = self._query_agentic(message, chat_history=chat_context)
        else:
            answer = self._query_legacy(message, chat_history=chat_context)

        # 保存到历史
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": answer})

        # 限制历史长度（最多保留 30 轮 = 60 条消息）
        max_messages = self._get_agentic_config("chat_max_history", 30) * 2
        if len(history) > max_messages:
            self.conversations[conversation_id] = history[-max_messages:]

        return answer

    def clear_conversation(self, conversation_id: str = "default"):
        """清除指定对话的历史记录。"""
        self.conversations.pop(conversation_id, None)

    def get_conversation(self, conversation_id: str = "default") -> List[Dict]:
        """获取对话历史。"""
        return self.conversations.get(conversation_id, [])

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
        try:
            # 将对话历史注入到初始消息
            initial_messages = []
            if chat_history:
                initial_messages.append({
                    "role": "user",
                    "content": f"{chat_history}\n\n## 当前问题\n{question}"
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
        prompt = question
        if chat_history:
            prompt = f"{chat_history}\n\n## 当前问题\n{question}"

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
