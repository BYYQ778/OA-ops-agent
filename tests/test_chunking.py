"""语义切块模块测试（完全离线）。

覆盖：标题识别（Markdown/中文章节/编号）、段落分组与大小上限、
超长段落硬切（含 overlap 精确性）、页级切块（不跨页）、小段合并、
uid 稳定性、非法参数、常见误判防护（列表项/IP 行不当标题）。
"""

import pytest

from utils.chunking import Chunk, split_semantic, split_semantic_pages

# ========== 标题识别 ==========

def test_markdown_headings_tracked() -> None:
    text = "# 安装指南\n\n这是安装的正文内容，足够长一些来通过最小长度限制的检查。\n\n## 子节标题\n\n子节的正文内容也写得稍微长一点点，以便不被合并。"
    chunks = split_semantic(text, min_size=0)
    assert chunks[0].section == "安装指南"
    assert chunks[-1].section == "子节标题"


def test_chinese_chapter_heading() -> None:
    text = "第二章 备份策略\n\n备份策略的正文内容，写长一些用于测试是否划入第二章的章节。"
    chunks = split_semantic(text, min_size=0)
    assert chunks[0].section == "第二章 备份策略"


def test_numbered_heading() -> None:
    text = "3.1 增量备份\n\n增量备份的正文内容，写长一些用于验证编号标题的识别是否正确。"
    chunks = split_semantic(text, min_size=0)
    assert chunks[0].section == "3.1 增量备份"


def test_list_item_is_not_heading() -> None:
    text = "# 章节一\n\n- 项目一\n\n其后的正文内容，列表项不应被当作标题处理，章节保持不变。"
    chunks = split_semantic(text, min_size=0)
    assert all(c.section == "章节一" for c in chunks)


def test_ip_line_is_not_heading() -> None:
    text = "# 网络配置\n\n10.0.0.8 是内网数据库地址，该行不应被识别为编号标题。"
    chunks = split_semantic(text, min_size=0)
    assert all(c.section == "网络配置" for c in chunks)


def test_no_headings_section_empty() -> None:
    chunks = split_semantic("普通正文，没有任何标题结构。再补充一些内容凑长度。", min_size=0)
    assert all(c.section == "" for c in chunks)


# ========== 段落分组与大小上限 ==========

def test_paragraphs_grouped_up_to_max_size() -> None:
    para = "字" * 200
    text = "\n\n".join([para, para, para, para])  # 4 段，每段 200
    chunks = split_semantic(text, max_size=500, min_size=0)
    # 200+200=400 可合并；三段 600 超限 → 至少 2 块
    assert len(chunks) >= 2
    assert all(len(c.text) <= 500 for c in chunks)
    # 段落不被拆散：每块都应是完整段落的组合
    for c in chunks:
        assert set(c.text.replace("\n", "")) == {"字"}


def test_all_chunks_within_max_size_mixed_doc() -> None:
    doc = (
        "# 总则\n\n" + "总则段落。" * 60 + "\n\n"
        "## 细项\n\n" + "字" * 700 + "\n\n" + "细节补充。" * 40
    )
    chunks = split_semantic(doc, max_size=500, overlap=50, min_size=0)
    assert chunks
    assert all(len(c.text) <= 500 for c in chunks)


def test_long_paragraph_hard_split_with_exact_overlap() -> None:
    # 无标点无空白的长段落，保证切点在 max_size 处精确可预测
    para = "字" * 1200
    chunks = split_semantic(para, max_size=500, overlap=50, min_size=0)
    assert len(chunks) >= 3
    assert all(len(c.text) <= 500 for c in chunks)
    # overlap 语义：下一块以「上一块末尾 overlap 个字符」开头（精确子串）
    assert chunks[1].text.startswith(chunks[0].text[-50:])
    assert chunks[2].text.startswith(chunks[1].text[-50:])


def test_hard_split_prefers_sentence_boundary() -> None:
    para = ("第一句。" * 80)  # 320 字
    chunks = split_semantic(para, max_size=100, overlap=10, min_size=0)
    assert all(len(c.text) <= 100 for c in chunks)
    # 大多数块应以句号结尾（自然边界切分优先）
    period_ended = sum(1 for c in chunks if c.text.rstrip().endswith("。"))
    assert period_ended >= len(chunks) - 1


# ========== 页级切块 ==========

def test_pages_never_cross_boundaries() -> None:
    pages = [
        (1, "第一页的内容。" * 60),
        (2, "第二页的内容。" * 60),
    ]
    chunks = split_semantic_pages(pages, max_size=500, min_size=0)
    page1_texts = [c.text for c in chunks if c.page == 1]
    page2_texts = [c.text for c in chunks if c.page == 2]
    assert page1_texts and page2_texts
    assert all("第二页" not in t for t in page1_texts)
    assert all("第一页" not in t for t in page2_texts)


def test_page_section_carries_across_pages() -> None:
    pages = [
        (2, "第三章 数据库维护\n\n本章介绍数据库维护要点，内容足够长以形成独立块。"),
        (3, "继续上一章的正文内容，跨页时章节归属应保持为第三章数据库维护。"),
    ]
    chunks = split_semantic_pages(pages, min_size=0)
    assert all(c.section == "第三章 数据库维护" for c in chunks)
    assert {c.page for c in chunks} == {2, 3}


def test_page_chunks_record_page_numbers() -> None:
    pages = [(5, "第五页内容。" * 30), (6, "第六页内容。" * 30)]
    chunks = split_semantic_pages(pages, min_size=0)
    assert all(c.page in (5, 6) for c in chunks)
    assert any(c.page == 5 for c in chunks)


# ========== 小段合并 ==========

def test_min_size_merges_tiny_paragraphs() -> None:
    text = "短段一。\n\n短段二。\n\n短段三。"
    chunks = split_semantic(text, max_size=500, min_size=100)
    assert len(chunks) == 1
    assert "短段一。" in chunks[0].text and "短段三。" in chunks[0].text


def test_min_size_merges_hard_split_tail_with_next() -> None:
    text = "字" * 600 + "\n\n短尾。"
    chunks = split_semantic(text, max_size=500, overlap=50, min_size=200)
    assert len(chunks) == 2
    assert "字" in chunks[1].text and "短尾。" in chunks[1].text
    assert all(len(c.text) <= 500 for c in chunks)


def test_min_size_respects_max_ceiling() -> None:
    # 两段各 40 字，min=100 想合并，但这样合并后仍 ≤ max；三段 120+120+120
    # 长段落场景：不能因为 min_size 超过 max_size 上限
    text = "\n\n".join(["字" * 120] * 3)
    chunks = split_semantic(text, max_size=300, min_size=250)
    assert all(len(c.text) <= 300 for c in chunks)


def test_merge_not_across_sections() -> None:
    text = "# 甲\n\n短。\n\n# 乙\n\n短。"
    chunks = split_semantic(text, min_size=100)
    assert len(chunks) == 2
    assert chunks[0].section == "甲"
    assert chunks[1].section == "乙"


# ========== 元数据与边界 ==========

def test_indexing_and_uid_stability() -> None:
    text = "# 一\n\n内容甲。\n\n# 二\n\n内容乙。"
    first = split_semantic(text, doc_id="docA", min_size=0)
    second = split_semantic(text, doc_id="docA", min_size=0)
    assert [c.index for c in first] == list(range(1, len(first) + 1))
    assert [c.uid for c in first] == [c.uid for c in second]
    assert first[0].uid == "docA:1"
    assert all(c.uid for c in first)


def test_empty_inputs() -> None:
    assert split_semantic("") == []
    assert split_semantic("   \n\n  \n ") == []
    assert split_semantic_pages([]) == []
    assert split_semantic_pages([(1, "  "), (2, "")]) == []


def test_illegal_params() -> None:
    with pytest.raises(ValueError):
        split_semantic("内容", max_size=0)
    with pytest.raises(ValueError):
        split_semantic("内容", max_size=100, overlap=-1)
    with pytest.raises(ValueError):
        split_semantic("内容", max_size=100, overlap=100)
    with pytest.raises(ValueError):
        split_semantic("内容", max_size=100, min_size=-5)
