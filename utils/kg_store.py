"""
知识图谱存储模块 — NetworkX + JSONL 持久化
------------------------------------------
提供知识图谱的构建、存储、查询和图探索操作。

存储格式:
  data/knowledge_graph/
    nodes.jsonl  — 每行一个节点 JSON: {id, type, name, description, sources, frequency}
    edges.jsonl  — 每行一条边 JSON:   {source_id, target_id, relation, weight, sources}

设计选型:
  - NetworkX: BFS/DFS/shortest_path/subgraph API 直接映射到图探索需求
  - JSONL:   人类可读、可追加、不依赖外部数据库
  - 节点 ID: md5(type + name) 跨重建稳定
"""

import os
import json
import hashlib
import logging
from typing import List, Dict, Optional, Any
from datetime import datetime

import networkx as nx

logger = logging.getLogger("kg_store")


def _node_id(entity_type: str, name: str) -> str:
    """生成稳定的节点 ID。"""
    raw = f"{entity_type}:{name}".lower().strip()
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


class KGStore:
    """
    知识图谱存储管理器。

    使用方式:
        store = KGStore("data/knowledge_graph")
        store.add_entities(entities, source_doc="运维手册.pdf")
        results = store.search_nodes("MySQL", entity_type="TECHNOLOGY")
        sub = store.extract_subgraph(node_id, depth=2)
        store.save()
    """

    def __init__(self, storage_dir: str = "data/knowledge_graph"):
        self.storage_dir = storage_dir
        os.makedirs(storage_dir, exist_ok=True)
        self.nodes_file = os.path.join(storage_dir, "nodes.jsonl")
        self.edges_file = os.path.join(storage_dir, "edges.jsonl")
        self.graph = nx.DiGraph()
        self._load()

    # ========== 持久化 ==========

    def _load(self):
        """从 JSONL 加载图谱。"""
        if not os.path.exists(self.nodes_file):
            logger.info(f"KG 存储目录为空，将创建新图谱: {self.storage_dir}")
            return

        count_nodes = 0
        if os.path.exists(self.nodes_file):
            with open(self.nodes_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        node = json.loads(line)
                        self.graph.add_node(
                            node["id"],
                            type=node.get("type", "CONCEPT"),
                            name=node.get("name", ""),
                            description=node.get("description", ""),
                            sources=node.get("sources", []),
                            frequency=node.get("frequency", 1),
                        )
                        count_nodes += 1
                    except json.JSONDecodeError:
                        continue

        count_edges = 0
        if os.path.exists(self.edges_file):
            with open(self.edges_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        edge = json.loads(line)
                        if self.graph.has_node(edge["source_id"]) and self.graph.has_node(edge["target_id"]):
                            self.graph.add_edge(
                                edge["source_id"],
                                edge["target_id"],
                                relation=edge.get("relation", "co_occurs"),
                                weight=edge.get("weight", 1),
                                sources=edge.get("sources", []),
                            )
                            count_edges += 1
                    except json.JSONDecodeError:
                        continue

        logger.info(f"KG 已加载: {count_nodes} 节点, {count_edges} 边")

    def save(self):
        """保存图谱到 JSONL（原子写入）。"""
        # 写入节点
        tmp_nodes = self.nodes_file + ".tmp"
        with open(tmp_nodes, "w", encoding="utf-8") as f:
            for node_id, attrs in self.graph.nodes(data=True):
                record = {
                    "id": node_id,
                    "type": attrs.get("type", "CONCEPT"),
                    "name": attrs.get("name", ""),
                    "description": attrs.get("description", ""),
                    "sources": attrs.get("sources", []),
                    "frequency": attrs.get("frequency", 1),
                    "created_at": datetime.now().isoformat(),
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        os.replace(tmp_nodes, self.nodes_file)

        # 写入边
        tmp_edges = self.edges_file + ".tmp"
        with open(tmp_edges, "w", encoding="utf-8") as f:
            for u, v, attrs in self.graph.edges(data=True):
                record = {
                    "source_id": u,
                    "target_id": v,
                    "relation": attrs.get("relation", "co_occurs"),
                    "weight": attrs.get("weight", 1),
                    "sources": attrs.get("sources", []),
                    "created_at": datetime.now().isoformat(),
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        os.replace(tmp_edges, self.edges_file)

        logger.info(f"KG 已保存: {self.graph.number_of_nodes()} 节点, {self.graph.number_of_edges()} 边")

    # ========== 修改操作 ==========

    def add_entities(
        self,
        entities: List[Dict],
        source_doc: str = "",
        chunk_id: int = 0,
    ):
        """
        批量添加实体到图谱。

        Args:
            entities: [{entity_type, entity_name, description, source_span}, ...]
            source_doc: 来源文档名
            chunk_id: 来源分块编号（同块实体之间建边）
        """
        # 添加节点
        node_ids = []
        for ent in entities:
            etype = ent.get("entity_type", "CONCEPT")
            name = ent.get("entity_name", "").strip()
            if not name:
                continue
            nid = _node_id(etype, name)
            node_ids.append(nid)

            if self.graph.has_node(nid):
                # 更新已有节点
                self.graph.nodes[nid]["frequency"] = self.graph.nodes[nid].get("frequency", 1) + 1
                sources = self.graph.nodes[nid].get("sources", [])
                if source_doc and source_doc not in sources:
                    sources.append(source_doc)
                self.graph.nodes[nid]["sources"] = sources
                # 合并描述
                new_desc = ent.get("description", "")
                if new_desc and new_desc not in self.graph.nodes[nid].get("description", ""):
                    existing = self.graph.nodes[nid]["description"]
                    self.graph.nodes[nid]["description"] = f"{existing}; {new_desc}" if existing else new_desc
            else:
                self.graph.add_node(
                    nid,
                    type=etype,
                    name=name,
                    description=ent.get("description", ""),
                    sources=[source_doc] if source_doc else [],
                    frequency=1,
                )

        # 同块内实体之间建边（共现关系）
        for i in range(len(node_ids)):
            for j in range(i + 1, len(node_ids)):
                u, v = node_ids[i], node_ids[j]
                if self.graph.has_edge(u, v):
                    self.graph[u][v]["weight"] = self.graph[u][v].get("weight", 1) + 1
                else:
                    self.graph.add_edge(
                        u, v,
                        relation="co_occurs",
                        weight=1,
                        sources=[source_doc] if source_doc else [],
                    )

    def remove_document(self, source_doc: str):
        """
        移除某文档相关的节点和边。
        仅移除来源唯一为该文档的节点；减少边的 sources。
        """
        nodes_to_remove = []
        for nid, attrs in list(self.graph.nodes(data=True)):
            sources = attrs.get("sources", [])
            if source_doc in sources:
                sources.remove(source_doc)
            if not sources:
                nodes_to_remove.append(nid)
            else:
                self.graph.nodes[nid]["sources"] = sources

        self.graph.remove_nodes_from(nodes_to_remove)

        # 清理引用已删除节点的边
        edges_to_remove = []
        for u, v in self.graph.edges:
            if u in nodes_to_remove or v in nodes_to_remove:
                edges_to_remove.append((u, v))
        self.graph.remove_edges_from(edges_to_remove)

        logger.info(f"已移除文档 [{source_doc}]: {len(nodes_to_remove)} 节点")

    def clear(self):
        """清空图谱。"""
        self.graph.clear()
        if os.path.exists(self.nodes_file):
            os.remove(self.nodes_file)
        if os.path.exists(self.edges_file):
            os.remove(self.edges_file)
        logger.info("KG 已清空")

    # ========== 查询操作 ==========

    def search_nodes(
        self, query: str, entity_type: str = None, limit: int = 20
    ) -> List[Dict]:
        """
        按名称或描述搜索节点。

        Args:
            query: 搜索关键词
            entity_type: 过滤实体类型，None=所有类型
            limit: 返回结果上限

        Returns:
            [{id, type, name, description, sources, frequency}, ...]
        """
        q = query.lower().strip()
        results = []
        for nid, attrs in self.graph.nodes(data=True):
            if entity_type and attrs.get("type") != entity_type:
                continue
            name = attrs.get("name", "").lower()
            desc = attrs.get("description", "").lower()
            if q in name or q in desc:
                results.append({
                    "id": nid,
                    "type": attrs.get("type", "CONCEPT"),
                    "name": attrs.get("name", ""),
                    "description": attrs.get("description", ""),
                    "sources": attrs.get("sources", []),
                    "frequency": attrs.get("frequency", 1),
                })
            if len(results) >= limit:
                break
        return results

    def get_node(self, node_id: str) -> Optional[Dict]:
        """获取单个节点详情。"""
        if not self.graph.has_node(node_id):
            return None
        attrs = self.graph.nodes[node_id]
        return {
            "id": node_id,
            "type": attrs.get("type", "CONCEPT"),
            "name": attrs.get("name", ""),
            "description": attrs.get("description", ""),
            "sources": attrs.get("sources", []),
            "frequency": attrs.get("frequency", 1),
        }

    def get_neighbors(self, node_id: str) -> List[Dict]:
        """获取节点的邻居列表。"""
        if not self.graph.has_node(node_id):
            return []
        neighbors = []
        for neighbor in self.graph.neighbors(node_id):
            edge = self.graph[node_id][neighbor]
            node = self.get_node(neighbor)
            if node:
                node["relation"] = edge.get("relation", "co_occurs")
                node["weight"] = edge.get("weight", 1)
                neighbors.append(node)
        # 按权重降序
        neighbors.sort(key=lambda x: x.get("weight", 0), reverse=True)
        return neighbors

    def extract_subgraph(self, node_id: str, depth: int = 2) -> Dict:
        """
        提取以指定节点为中心的子图。

        Args:
            node_id: 中心节点 ID
            depth: BFS 深度

        Returns:
            {nodes: [...], edges: [...]}
        """
        if not self.graph.has_node(node_id):
            return {"nodes": [], "edges": []}

        # BFS 收集节点
        visited = set()
        frontier = {node_id}
        for _ in range(depth + 1):
            next_frontier = set()
            for n in frontier:
                if n not in visited:
                    visited.add(n)
                    next_frontier.update(self.graph.neighbors(n))
            frontier = next_frontier - visited

        # 收集边
        sub_nodes = []
        sub_edges = []
        for n in visited:
            sub_nodes.append(self.get_node(n))
        for u, v, attrs in self.graph.edges(data=True):
            if u in visited and v in visited:
                sub_edges.append({
                    "source_id": u,
                    "target_id": v,
                    "relation": attrs.get("relation", "co_occurs"),
                    "weight": attrs.get("weight", 1),
                })

        return {"nodes": sub_nodes, "edges": sub_edges}

    def find_shortest_path(self, source_query: str, target_query: str) -> Dict:
        """
        查找两个实体之间的最短路径。

        Args:
            source_query: 起始实体名称（模糊匹配）
            target_query: 目标实体名称（模糊匹配）

        Returns:
            {path: [node_names], length: N, nodes: [...], edges: [...]}
        """
        # 模糊匹配转节点 ID
        src_results = self.search_nodes(source_query, limit=1)
        tgt_results = self.search_nodes(target_query, limit=1)

        if not src_results or not tgt_results:
            return {"path": [], "length": 0, "error": "未找到匹配的实体"}

        src_id = src_results[0]["id"]
        tgt_id = tgt_results[0]["id"]

        try:
            path_ids = nx.shortest_path(self.graph, source=src_id, target=tgt_id)
            path_nodes = []
            for nid in path_ids:
                node = self.get_node(nid)
                if node:
                    path_nodes.append(node)

            path_edges = []
            for i in range(len(path_ids) - 1):
                u, v = path_ids[i], path_ids[i + 1]
                if self.graph.has_edge(u, v):
                    edge = self.graph[u][v]
                    path_edges.append({
                        "source_id": u,
                        "target_id": v,
                        "relation": edge.get("relation", "co_occurs"),
                        "weight": edge.get("weight", 1),
                    })

            return {
                "path": [n["name"] for n in path_nodes],
                "length": len(path_nodes) - 1,
                "nodes": path_nodes,
                "edges": path_edges,
            }
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return {"path": [], "length": -1, "error": f"'{source_query}' 与 '{target_query}' 之间无路径"}

    def get_stats(self) -> Dict:
        """获取图谱统计信息。"""
        nodes_by_type = {}
        for _, attrs in self.graph.nodes(data=True):
            t = attrs.get("type", "CONCEPT")
            nodes_by_type[t] = nodes_by_type.get(t, 0) + 1

        return {
            "total_nodes": self.graph.number_of_nodes(),
            "total_edges": self.graph.number_of_edges(),
            "nodes_by_type": nodes_by_type,
            "storage_dir": self.storage_dir,
        }

    def to_context_string(self, query: str = "", limit: int = 10) -> str:
        """
        将图谱中相关节点渲染为 LLM 可读的上下文字符串。
        用于 Agentic RAG 中为 router 提供图谱信息。
        """
        if self.graph.number_of_nodes() == 0:
            return "知识图谱当前为空。"

        if query:
            nodes = self.search_nodes(query, limit=limit)
        else:
            # 返回频率最高的节点
            sorted_nodes = sorted(
                self.graph.nodes(data=True),
                key=lambda x: x[1].get("frequency", 0),
                reverse=True,
            )
            nodes = []
            for nid, attrs in sorted_nodes[:limit]:
                nodes.append({
                    "id": nid,
                    "type": attrs.get("type", "CONCEPT"),
                    "name": attrs.get("name", ""),
                    "description": attrs.get("description", ""),
                    "sources": attrs.get("sources", []),
                    "frequency": attrs.get("frequency", 1),
                })

        stats = self.get_stats()
        lines = [
            f"知识图谱概况: {stats['total_nodes']} 节点, {stats['total_edges']} 边",
            f"节点类型分布: {stats['nodes_by_type']}",
            "",
            "相关实体:",
        ]
        for node in nodes:
            lines.append(
                f"  [{node['type']}] {node['name']}"
                + (f" — {node['description'][:80]}" if node.get("description") else "")
                + f" (来源: {', '.join(node.get('sources', [])[:2])})"
            )

        return "\n".join(lines)
