"""BM25 词法检索模块：中文优先，纯 Python 实现（除 jieba 外零重依赖）。

设计:
- tokenize: jieba 分词（缺失时自动降级 regex bigram 分词），latin 统一小写
- BM25Index: 由 (id, text) 序列构建；search 返回 [(id, score)] 按分数降序，
  平分时按 id 升序保证确定性
- IDF 采用 Lucene 变形 log(1 + (N - df + 0.5) / (df + 0.5))，恒为非负，
  语料很小时也不会出现负分文档

调用关系: utils/retrieval.py 的 HybridRetriever 消费本模块；纯离线，无网络。
"""

import logging
import math
import re
from typing import Dict, Iterable, List, Tuple

try:  # jieba 为可选依赖：缺失时降级，不影响导入与检索可用性
    import jieba

    jieba.setLogLevel(logging.WARNING)
except ImportError:  # pragma: no cover - 环境缺 jieba 时才走到
    jieba = None

# 英文/数字/错误码/IP/版本号等（保留内部 . - _ : / 便于匹配 ORA-01555、10.0.0.8:8080）
_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\-:/]*")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")


def fallback_tokenize(text: str) -> List[str]:
    """降级分词：英文/数字串（小写）+ 中文 bigram。供无 jieba 环境与单测使用。"""
    tokens: List[str] = []
    if not text:
        return tokens
    for match in _WORD_RE.finditer(text):
        tokens.append(match.group(0).lower())
    for match in _CJK_RE.finditer(text):
        seg = match.group(0)
        if len(seg) == 1:
            tokens.append(seg)
        else:
            tokens.extend(seg[i : i + 2] for i in range(len(seg) - 1))
    return tokens


def tokenize(text: str) -> List[str]:
    """分词入口：jieba 可用用 jieba，否则降级；过滤纯空白/纯标点 token，latin 小写。"""
    if not text or not text.strip():
        return []
    if jieba is None:
        return fallback_tokenize(text)
    tokens: List[str] = []
    for tok in jieba.lcut(text):
        tok = tok.strip()
        if not tok:
            continue
        if any(ch.isalnum() or "\u4e00" <= ch <= "\u9fff" for ch in tok):
            tokens.append(tok.lower())
    return tokens


class BM25Index:
    """不可变 BM25 索引。

    documents: (doc_id, text) 序列；doc_id 应唯一，若重复则最后一次写入生效
    （覆盖语义，不产生重复计分）。
    """

    def __init__(self, documents: Iterable[Tuple[str, str]], k1: float = 1.5, b: float = 0.75):
        self.k1 = float(k1)
        self.b = float(b)

        order: List[str] = []
        pool: Dict[str, List[str]] = {}
        for doc_id, text in documents:
            if doc_id not in pool:
                order.append(doc_id)
            pool[doc_id] = tokenize(text or "")

        self._ids: List[str] = order
        self._tokenized: List[List[str]] = [pool[i] for i in order]
        n = len(self._ids)
        self._avgdl = (sum(len(t) for t in self._tokenized) / n) if n else 0.0

        df: Dict[str, int] = {}
        for toks in self._tokenized:
            for tok in set(toks):
                df[tok] = df.get(tok, 0) + 1
        self._idf: Dict[str, float] = {
            tok: math.log(1 + (n - d + 0.5) / (d + 0.5)) for tok, d in df.items()
        }

        self._tfs: List[Dict[str, int]] = []
        for toks in self._tokenized:
            tf: Dict[str, int] = {}
            for tok in toks:
                tf[tok] = tf.get(tok, 0) + 1
            self._tfs.append(tf)

    @property
    def size(self) -> int:
        return len(self._ids)

    def search(self, query: str, k: int = 10) -> List[Tuple[str, float]]:
        """检索：返回 [(doc_id, score)]，按 score 降序、doc_id 升序；无命中返回 []。"""
        if k <= 0 or not self._ids:
            return []
        query_tokens = tokenize(query or "")
        if not query_tokens:
            return []

        seen = set()
        query_terms: List[str] = []
        for tok in query_tokens:
            if tok in self._idf and tok not in seen:
                seen.add(tok)
                query_terms.append(tok)
        if not query_terms:
            return []

        scored: List[Tuple[str, float]] = []
        for i, doc_id in enumerate(self._ids):
            dl = len(self._tokenized[i])
            score = 0.0
            for tok in query_terms:
                tf = self._tfs[i].get(tok, 0)
                if not tf:
                    continue
                denom = tf + self.k1 * (1 - self.b + self.b * dl / self._avgdl)
                score += self._idf[tok] * (tf * (self.k1 + 1)) / denom
            if score > 0:
                scored.append((doc_id, score))

        scored.sort(key=lambda item: (-item[1], item[0]))
        return scored[:k]
