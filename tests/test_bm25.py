"""BM25 词法检索模块测试（完全离线，不依赖外部服务）。

覆盖：分词（jieba 与降级路径）、BM25 排序行为、边界情况（空语料/无命中/
平分决胜）、分数非负（Lucene 型 IDF 修正）。
"""

from utils.bm25 import BM25Index, fallback_tokenize, tokenize

# ========== 分词 ==========

def test_fallback_tokenize_mixed_chinese_english() -> None:
    toks = fallback_tokenize("MySQL 连接失败 192.168.1.100 ORA-01555")
    assert "mysql" in toks
    assert "192.168.1.100" in toks
    # 中文按 bigram 切分
    assert "连接" in toks
    assert "失败" in toks


def test_fallback_tokenize_drops_punctuation_only_tokens() -> None:
    assert fallback_tokenize("。。 —— ！？ ,, 。 ") == []


def test_fallback_tokenize_cjk_bigrams() -> None:
    toks = fallback_tokenize("磁盘空间不足")
    assert "磁盘" in toks
    assert "空间" in toks
    assert "不足" in toks


def test_tokenize_lowercases_latin_keeps_cjk() -> None:
    toks = tokenize("NGINX 磁盘空间")
    assert "nginx" in toks
    # jieba/failed 路径都应产出非空、无空白 token
    assert toks
    assert all(t.strip() == t and t for t in toks)


def test_tokenize_empty_input() -> None:
    assert tokenize("") == []
    assert tokenize("   \n\t ") == []


# ========== BM25 检索 ==========

DOCS = [
    ("d1", "磁盘空间不足导致服务异常，请及时清理磁盘空间。"),
    ("d2", "502 Bad Gateway 上游服务不可用，请检查 nginx 配置。"),
    ("d3", "MySQL 数据库连接失败，检查网络与端口配置。"),
]


def test_search_finds_matching_document() -> None:
    idx = BM25Index(DOCS)
    hits = idx.search("磁盘空间", k=3)
    assert hits
    assert hits[0][0] == "d1"


def test_search_ranks_higher_term_frequency_first() -> None:
    idx = BM25Index([
        ("a", "nginx 502 错误 nginx nginx 排查"),
        ("b", "nginx 502 错误 排查 方法 总结 文档"),
    ])
    hits = idx.search("nginx", k=2)
    assert [h[0] for h in hits][0] == "a"


def test_search_rare_term_beats_common_term() -> None:
    idx = BM25Index([
        ("common", "服务 服务 服务 检查 检查 检查"),
        ("rare", "服务 检查 磁盘爆满排查手册"),
    ])
    hits = idx.search("磁盘爆满", k=2)
    assert hits[0][0] == "rare"


def test_search_returns_empty_for_unmatched_query() -> None:
    idx = BM25Index(DOCS)
    assert idx.search("区块链智能合约灰度发布", k=3) == []


def test_search_empty_index() -> None:
    assert BM25Index([]).search("磁盘", k=3) == []


def test_search_empty_query() -> None:
    idx = BM25Index(DOCS)
    assert idx.search("", k=3) == []
    assert idx.search("   。 ", k=3) == []


def test_scores_non_negative_and_sorted() -> None:
    idx = BM25Index(DOCS)
    hits = idx.search("磁盘 服务 检查 网络", k=5)
    scores = [s for _, s in hits]
    assert scores == sorted(scores, reverse=True)
    assert all(s >= 0 for s in scores)


def test_term_present_in_all_docs_still_positive() -> None:
    # Lucene 型 IDF：df == N 时不产生负分
    idx = BM25Index([("a", "故障 故障 排查"), ("b", "故障 处理 流程")])
    hits = idx.search("故障", k=2)
    assert len(hits) == 2
    assert all(s > 0 for _, s in hits)


def test_tie_break_is_deterministic_by_doc_id() -> None:
    idx = BM25Index([("b", "磁盘 空间"), ("a", "磁盘 空间")])
    hits = idx.search("磁盘", k=2)
    assert [h[0] for h in hits] == ["a", "b"]


def test_k_limits_results() -> None:
    idx = BM25Index(DOCS)
    assert len(idx.search("配置", k=1)) <= 1
    assert len(idx.search("磁盘 服务 检查 网络 配置", k=2)) <= 2


def test_size_property() -> None:
    assert BM25Index(DOCS).size == 3
    assert BM25Index([]).size == 0


def test_same_id_duplicates_use_last_text() -> None:
    # 契约：documents 的 id 应唯一；若重复，后写入的文本生效且不产生双份计分
    idx = BM25Index([("x", "旧文本"), ("x", "新文本 磁盘")])
    hits = idx.search("磁盘", k=5)
    assert [h[0] for h in hits] == ["x"]
