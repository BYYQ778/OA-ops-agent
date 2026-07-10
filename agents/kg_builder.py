"""
知识图谱构建编排模块
-------------------
将 entity_extractor 和 kg_store 连接起来：
  文档文本 → EntityExtractor → 实体列表 → KGStore.add_entities → 持久化

并提供增量更新（添加/删除文档）和批量构建能力。
"""

import logging
from typing import List, Optional

from langchain_openai import ChatOpenAI
from langchain_core.documents import Document

from utils.kg_store import KGStore
from utils.doc_parser import split_text
from agents.entity_extractor import EntityExtractor

logger = logging.getLogger("kg_builder")

# 构建图谱时的分块大小（通常与知识库分块一致）
BUILD_CHUNK_SIZE = 800
BUILD_CHUNK_OVERLAP = 80


class KnowledgeGraphBuilder:
    """
    知识图谱构建编排器。

    使用方式:
        llm = ChatOpenAI(...)
        store = KGStore("data/knowledge_graph")
        builder = KnowledgeGraphBuilder(llm, store, provider="ollama")
        builder.add_document(text="OA使用Nginx...", source="运维手册.pdf")
        store.save()
    """

    def __init__(
        self,
        llm: ChatOpenAI,
        kg_store: KGStore,
        provider: str = "ollama",
        chunk_size: int = BUILD_CHUNK_SIZE,
        chunk_overlap: int = BUILD_CHUNK_OVERLAP,
    ):
        self.llm = llm
        self.store = kg_store
        self.provider = provider
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.extractor = EntityExtractor(llm, provider=provider)

    def add_document(self, text: str, source: str) -> int:
        """
        从文档文本构建图谱节点和边。

        Args:
            text: 文档纯文本内容
            source: 文档名称（用于溯源和删除）

        Returns:
            新增的实体数量
        """
        if not text or not text.strip():
            logger.info(f"文档 [{source}] 内容为空，跳过 KG 构建")
            return 0

        # 分块处理
        chunks = split_text(text, chunk_size=self.chunk_size, overlap=self.chunk_overlap)
        logger.info(f"KG 构建 [{source}]: {len(chunks)} 个文本块")

        total_entities = 0
        for i, chunk in enumerate(chunks):
            if not chunk.strip():
                continue
            try:
                entities = self.extractor.extract(chunk)
                if entities:
                    self.store.add_entities(entities, source_doc=source, chunk_id=i)
                    total_entities += len(entities)
            except Exception as e:
                logger.warning(f"实体提取失败 (chunk {i}): {e}")
                continue

        logger.info(f"KG 构建完成 [{source}]: 提取 {total_entities} 个实体")
        return total_entities

    def remove_document(self, source: str):
        """移除文档相关的图谱节点和边。"""
        self.store.remove_document(source)

    def build_from_documents(
        self, documents: List[Document], save: bool = True
    ) -> int:
        """
        从 LangChain Document 列表批量构建图谱。

        Args:
            documents: LangChain Document 对象列表
            save: 构建完成后是否立即持久化

        Returns:
            总实体数
        """
        total = 0
        for doc in documents:
            source = doc.metadata.get("source", "unknown")
            text = doc.page_content
            total += self.add_document(text, source)

        if save:
            self.store.save()

        return total

    def rebuild_from_scratch(
        self, documents: List[Document], save: bool = True
    ) -> int:
        """
        清空并重建整个图谱（用于全量重建场景）。
        """
        self.store.clear()
        return self.build_from_documents(documents, save=save)

    def get_stats(self) -> dict:
        """获取图谱统计。"""
        return self.store.get_stats()
