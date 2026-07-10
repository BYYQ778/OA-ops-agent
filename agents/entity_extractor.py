"""
实体提取模块 — LLM 驱动的命名实体识别
--------------------------------------
从运维文档中提取结构化实体，支持两种 LLM 后端：

1. DeepSeek / OpenAI 兼容 API → Function-calling + JSON Schema 结构化输出
2. Ollama 本地模型 → Prompt 工程 + JSON 解析 + 正则回退

实体类型: TECHNOLOGY / ORGANIZATION / PERSON / LOCATION / CONCEPT

使用方式:
    extractor = EntityExtractor(llm, config)
    entities = extractor.extract(text)
"""

import json
import re
import logging
from typing import List, Dict, Optional

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

logger = logging.getLogger("entity_extractor")

# 支持的实体类型
ENTITY_TYPES = ["TECHNOLOGY", "ORGANIZATION", "PERSON", "LOCATION", "CONCEPT"]

# 每块最大字符数（避免超出 LLM 上下文）
MAX_CHUNK_SIZE = 1200

# ---- 实体提取 Prompt ----

ENTITY_EXTRACTION_SYSTEM = """你是一个命名实体识别专家。从给定的运维文档文本中提取所有重要的命名实体。

## 实体类型定义
- TECHNOLOGY: 技术/软件/硬件/协议（如 Nginx、MySQL、Docker、HTTPS、Redis、Tomcat）
- ORGANIZATION: 组织/公司/部门（如 信息中心、运维部、OA厂商）
- PERSON: 人员姓名（如 张三、管理员）
- LOCATION: 地理位置/网络位置（如 北京机房、192.168.1.0/24、/opt/oa/）
- CONCEPT: 抽象概念/术语/方法（如 负载均衡、主从复制、灰度发布、故障转移）

## 提取规则
1. 只提取在文本中明确出现的实体，不要推测
2. 每个实体提供简要描述（引用原文片段）
3. 如果文本中没有某类实体，不返回该类
4. 忽略过于通用无意义的词（如"系统"、"服务器"、"文件"等，除非有明确限定）

## 输出格式
严格输出 JSON 数组，不要输出任何其他文字：
```json
[
  {"entity_type": "TECHNOLOGY", "entity_name": "Nginx", "description": "反向代理服务器，用于OA系统入口"},
  {"entity_type": "CONCEPT", "entity_name": "负载均衡", "description": "将流量分发到多台后端服务器"}
]
```"""


class EntityExtractor:
    """
    LLM 驱动的实体提取器。

    使用方式:
        llm = ChatOpenAI(...)
        extractor = EntityExtractor(llm, provider="ollama")
        entities = extractor.extract("OA系统使用Nginx作为反向代理...")
    """

    def __init__(self, llm: ChatOpenAI, provider: str = "ollama"):
        self.llm = llm
        self.provider = provider

    def extract(self, text: str) -> List[Dict]:
        """
        从文本中提取实体。

        Args:
            text: 待分析的文本

        Returns:
            [{entity_type, entity_name, description, source_span}, ...]
        """
        if not text or not text.strip():
            return []

        # 文本分块处理
        chunks = self._chunk_text(text)

        all_entities = []
        for chunk in chunks:
            try:
                chunk_entities = self._extract_from_chunk(chunk)
                all_entities.extend(chunk_entities)
            except Exception as e:
                logger.warning(f"实体提取失败（chunk）: {e}")
                continue

        # 去重
        return self._deduplicate(all_entities)

    def _chunk_text(self, text: str) -> List[str]:
        """将长文本切分为 LLM 可处理的块。"""
        if len(text) <= MAX_CHUNK_SIZE:
            return [text]

        chunks = []
        start = 0
        while start < len(text):
            end = start + MAX_CHUNK_SIZE
            chunk = text[start:end]

            # 在句号处截断
            if end < len(text):
                last_period = chunk.rfind("。")
                last_newline = chunk.rfind("\n")
                cut = max(last_period, last_newline)
                if cut > MAX_CHUNK_SIZE * 0.5:
                    chunk = chunk[:cut + 1]

            chunks.append(chunk.strip())
            start += len(chunk) - 50  # 50 字符重叠

        return chunks

    def _extract_from_chunk(self, text: str) -> List[Dict]:
        """从单个文本块提取实体。"""
        if self.provider == "ollama":
            return self._extract_with_prompt(text)
        else:
            return self._extract_with_tools(text)

    # ---- 策略 1: Function-calling (DeepSeek) ----

    def _extract_with_tools(self, text: str) -> List[Dict]:
        """使用 function-calling 结构化输出。"""
        from langchain_core.utils.function_calling import convert_to_openai_tool

        schema = {
            "name": "extract_entities",
            "description": "从运维文档中提取命名实体",
            "parameters": {
                "type": "object",
                "properties": {
                    "entities": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "entity_type": {
                                    "type": "string",
                                    "enum": ENTITY_TYPES,
                                },
                                "entity_name": {"type": "string"},
                                "description": {"type": "string"},
                            },
                            "required": ["entity_type", "entity_name", "description"],
                        },
                    }
                },
                "required": ["entities"],
            },
        }

        llm_with_tools = self.llm.bind_tools([schema], tool_choice={"type": "function", "function": {"name": "extract_entities"}})
        response = llm_with_tools.invoke([
            SystemMessage(content=ENTITY_EXTRACTION_SYSTEM),
            HumanMessage(content=f"从以下文本中提取实体：\n\n{text}"),
        ])

        # 解析 tool_calls
        if hasattr(response, "tool_calls") and response.tool_calls:
            for tc in response.tool_calls:
                if tc.get("name") == "extract_entities":
                    args = tc.get("args", {})
                    return args.get("entities", [])

        # 回退到 prompt 方式
        logger.info("Function-calling 未返回结果，回退到 prompt 方式")
        return self._extract_with_prompt(text)

    # ---- 策略 2: Prompt + JSON 解析 (Ollama) ----

    def _extract_with_prompt(self, text: str) -> List[Dict]:
        """使用 prompt 工程 + JSON 解析提取实体。"""
        response = self.llm.invoke([
            SystemMessage(content=ENTITY_EXTRACTION_SYSTEM),
            HumanMessage(content=f"从以下文本中提取实体：\n\n{text}"),
        ])

        content = response.content if hasattr(response, "content") else str(response)
        return self._parse_json_response(content)

    def _parse_json_response(self, text: str) -> List[Dict]:
        """从 LLM 响应中解析 JSON 实体列表。多层回退策略。"""
        # 策略 A: 直接 JSON 解析
        try:
            data = json.loads(text.strip())
            if isinstance(data, list):
                return self._validate_entities(data)
            if isinstance(data, dict) and "entities" in data:
                return self._validate_entities(data["entities"])
        except json.JSONDecodeError:
            pass

        # 策略 B: 提取 JSON 数组
        patterns = [
            r"```json\s*([\s\S]*?)\s*```",   # ```json ... ```
            r"```\s*([\s\S]*?)\s*```",        # ``` ... ```
            r"\[\s*\{[\s\S]*?\}\s*\]",         # 裸数组
        ]
        for pattern in patterns:
            matches = re.findall(pattern, text)
            for match in matches:
                try:
                    data = json.loads(match)
                    if isinstance(data, list):
                        return self._validate_entities(data)
                except json.JSONDecodeError:
                    continue

        # 策略 C: 逐行正则提取
        logger.warning("JSON 解析失败，尝试正则提取")
        return self._regex_extract(text)

    def _regex_extract(self, text: str) -> List[Dict]:
        """正则回退：从非结构化文本中提取实体提及。"""
        entities = []
        # 匹配 "TECHNOLOGY: Nginx" 或 "- TECHNOLOGY: Nginx" 等模式
        for etype in ENTITY_TYPES:
            pattern = rf"{etype}\s*[:：]\s*(.+?)(?:\n|$|，|,)"
            for match in re.finditer(pattern, text):
                name = match.group(1).strip()
                if name and len(name) < 60:
                    entities.append({
                        "entity_type": etype,
                        "entity_name": name,
                        "description": "",
                    })

        return entities[:20]  # 限制数量

    def _validate_entities(self, entities: List[Dict]) -> List[Dict]:
        """验证和清洗实体列表。"""
        valid = []
        for ent in entities:
            if not isinstance(ent, dict):
                continue
            etype = ent.get("entity_type", "").upper()
            name = ent.get("entity_name", "").strip()
            if etype in ENTITY_TYPES and name and len(name) < 100:
                valid.append({
                    "entity_type": etype,
                    "entity_name": name,
                    "description": ent.get("description", ""),
                })
        return valid

    def _deduplicate(self, entities: List[Dict]) -> List[Dict]:
        """去重：按 (类型, 归一化名称) 合并。"""
        seen = {}
        for ent in entities:
            key = (ent["entity_type"], ent["entity_name"].lower().strip())
            if key not in seen:
                seen[key] = ent
            elif ent.get("description") and not seen[key].get("description"):
                seen[key]["description"] = ent["description"]
        return list(seen.values())
