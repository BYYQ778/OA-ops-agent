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

## 用户提问时，会同时提供当前知识库的状态信息，请据此判断能否回答问题。
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

        # ---- 构建RAG专用的Agent ----
        self._setup_agent()

        logger.info("知识库Agent初始化完成")

    def _setup_agent(self):
        """构建LangChain Agent，注册知识库工具"""
        kb = self  # 闭包引用，避免 self 被 @tool 识别为工具参数

        @tool
        def search_knowledge_base(query: str) -> str:
            """
            从运维知识库中检索与用户问题最相关的文档内容。
            此工具会返回知识库中最匹配的文档片段。
            """
            return kb._retrieve_context(query)

        @tool
        def get_kb_stats(dummy: str = "") -> str:
            """
            获取当前知识库的统计信息，包括文档总数、分块总数等。
            """
            return kb.get_stats()

        self.agent = create_agent(
            model=self.llm,
            tools=[search_knowledge_base, get_kb_stats],
            system_prompt=RAG_SYSTEM_PROMPT,
        )

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

            summary = (
                f"文档导入成功！\n"
                f"  文件名: {file_name}\n"
                f"  文档长度: {len(raw_text)} 字符\n"
                f"  分块数量: {len(chunks)} 块\n"
                f"  分块大小: {CHUNK_SIZE} 字/块 (重叠 {CHUNK_OVERLAP} 字)\n"
                f"  存储位置: {CHROMA_DB_DIR}"
            )
            return summary

        except Exception as e:
            logger.error(f"文档导入失败: {e}")
            return f"[错误] 文档导入失败: {str(e)}"

    def query(self, question: str) -> str:
        """
        基于RAG回答运维知识问题。

        Args:
            question: 用户的运维问题

        Returns:
            基于知识库的回答
        """
        if not question.strip():
            return "[提示] 请输入您想咨询的运维问题。"

        # 先获取知识库概况
        kb_info = self.get_stats()

        logger.info(f"RAG问答: {question[:50]}...")

        try:
            result = self.agent.invoke({
                "messages": [
                    {"role": "user", "content": f"{question}\n\n当前知识库状态: {kb_info}"}
                ]
            })
            messages = result.get("messages", [])
            return messages[-1].content if messages else "问答处理异常，请重试"
        except Exception as e:
            # LLM不可用时，降级为仅检索（不生成回答）
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

            return f"文档 '{doc_name}' 已从知识库删除（共移除 {len(ids_to_delete)} 个分块）。"

        except Exception as e:
            return f"[错误] 删除文档失败: {str(e)}"

    def get_stats(self) -> str:
        """
        获取知识库统计信息。

        Returns:
            统计数据字符串
        """
        try:
            all_data = self.vector_store.get()
            if not all_data["ids"]:
                return "知识库为空，尚未导入任何文档。"

            total_chunks = len(all_data["ids"])
            total_chars = sum(
                m.get("chunk_size", 0) for m in all_data["metadatas"]
            )
            unique_docs = len(set(
                m.get("source", "") for m in all_data["metadatas"]
            ))

            return (
                f"知识库概况:\n"
                f"  文档数量: {unique_docs} 份\n"
                f"  分块总数: {total_chunks} 块\n"
                f"  总字数: {total_chars} 字\n"
                f"  存储路径: {CHROMA_DB_DIR}"
            )

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
