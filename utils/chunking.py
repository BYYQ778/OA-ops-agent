"""语义切块：按标题/段落/页面切分，替代固定字符切块（纯 Python、离线可测）。

设计:
- 标题识别: Markdown（#）、中文章节（第二章/第三节）、编号标题（3.1 xxx），
  短行 + 不以句末标点结尾 + 非列表项/非 IP 行，防误判
- 段落优先: 空行分段，段落在 max_size 内聚合成块；超长段落按句界硬切
  （切不动时按 max_size 精确切），硬切相邻块保留 overlap 个字符的精确重叠
- 页级: split_semantic_pages 逐页处理，块永不跨页（保证引用页码精确）；
  章节归属跨页延续
- 小段合并: min_size 以下且与相邻块同页同章节、合并后不超 max_size 时合并
- 旧 utils/doc_parser.split_text 保持不变（兼容与回退）；
  本模块输出 Chunk（text/index/section/page/uid），uid = f"{doc_id}:{index}"
"""

import re
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

_MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
_CN_HEADING = re.compile(r"^\s*第\s*[一二三四五六七八九十百千零两0-9]+\s*[章节篇部分]\s*[、.：:]?\s*.{0,80}$")
_NUM_HEADING = re.compile(r"^\s*\d{1,2}(?:\.\d{1,2}){0,2}\s*[、.]?\s+\S.{0,78}$")

_BOUNDARY_CHARS = "。！？；!?;\n"
_SENTENCE_END = "。！？；!?;，,、"


@dataclass
class Chunk:
    """语义块：index 从 1 起；section 为最近标题（可为空）；page 仅页级模式有值。"""

    text: str
    index: int = 0
    section: str = ""
    page: Optional[int] = None
    uid: str = ""


def _validate(max_size: int, overlap: int, min_size: int) -> None:
    if max_size <= 0:
        raise ValueError("max_size 必须大于 0")
    if overlap < 0 or overlap >= max_size:
        raise ValueError("overlap 必须满足 0 <= overlap < max_size")
    if min_size < 0:
        raise ValueError("min_size 不能为负数")


def _is_heading(line: str) -> Optional[str]:
    """判断一行是否为标题，返回标题文本；否则 None。"""
    stripped = line.strip()
    if not stripped or len(stripped) > 80:
        return None
    if stripped[0] in "-•*·◦▪># \t" and not stripped.startswith("#"):
        return None
    if stripped[-1] in _SENTENCE_END:  # 以句末标点结尾的句子不是标题
        return None
    md = _MD_HEADING.match(stripped)
    if md:
        return md.group(1).strip()
    if _CN_HEADING.match(stripped):
        return stripped
    if _NUM_HEADING.match(stripped):
        return stripped
    return None


def _hard_split(paragraph: str, max_size: int, overlap: int) -> List[str]:
    """超长段落硬切：优先句界，切不动按 max_size 精确切；相邻块保留精确 overlap。"""
    pieces: List[str] = []
    start = 0
    n = len(paragraph)
    while start < n:
        end = min(start + max_size, n)
        if end < n:
            floor = start + int(max_size * 0.6)
            for i in range(end - 1, floor - 1, -1):
                if paragraph[i] in _BOUNDARY_CHARS:
                    end = i + 1
                    break
        piece = paragraph[start:end]
        if piece.strip():
            pieces.append(piece)
        if end >= n:
            break
        start = max(start + 1, end - overlap)
    return pieces


def _process_text(
    text: str, section: str, max_size: int, overlap: int, page: Optional[int]
) -> Tuple[List[Chunk], str]:
    """处理一段（页）文本：返回 (chunks, 结束时章节)。"""
    chunks: List[Chunk] = []
    blocks = re.split(r"\n\s*\n+", text)
    group: List[str] = []
    group_len = 0

    def flush() -> None:
        nonlocal group, group_len
        if group:
            joined = "\n\n".join(group)
            chunks.append(Chunk(text=joined, section=section, page=page))
            group = []
            group_len = 0

    for block in blocks:
        para = block.strip()
        if not para:
            continue
        # 单行块可能是标题
        if "\n" not in para:
            heading = _is_heading(para)
            if heading is not None:
                flush()
                section = heading
                continue
        if len(para) > max_size:
            flush()
            for piece in _hard_split(para, max_size, overlap):
                chunks.append(Chunk(text=piece, section=section, page=page))
            continue
        extra = 2 if group else 0
        if group_len + extra + len(para) > max_size:
            flush()
        group.append(para)
        group_len = len("\n\n".join(group))
    flush()
    return chunks, section


def _merge_small(chunks: List[Chunk], max_size: int, min_size: int) -> List[Chunk]:
    """同页同章节的小块向下一块合并（合并后不超过 max_size）。"""
    merged: List[Chunk] = []
    for chunk in chunks:
        if (
            merged
            and len(merged[-1].text) < min_size
            and merged[-1].section == chunk.section
            and merged[-1].page == chunk.page
            and len(merged[-1].text) + 2 + len(chunk.text) <= max_size
        ):
            merged[-1] = Chunk(
                text=merged[-1].text + "\n\n" + chunk.text,
                section=merged[-1].section,
                page=merged[-1].page,
            )
        else:
            merged.append(Chunk(text=chunk.text, section=chunk.section, page=chunk.page))
    return merged


def _finalize(chunks: List[Chunk], doc_id: str) -> List[Chunk]:
    for i, chunk in enumerate(chunks, start=1):
        chunk.index = i
        chunk.uid = f"{doc_id}:{i}" if doc_id else f"chunk:{i}"
    return chunks


def split_semantic(
    text: str,
    *,
    doc_id: str = "",
    max_size: int = 500,
    overlap: int = 50,
    min_size: int = 120,
) -> List[Chunk]:
    """把纯文本切成语义块（标题/段落优先，超长段落句界硬切）。"""
    _validate(max_size, overlap, min_size)
    if not text or not text.strip():
        return []
    chunks, _ = _process_text(text, "", max_size, overlap, page=None)
    if min_size:
        chunks = _merge_small(chunks, max_size, min_size)
    return _finalize(chunks, doc_id)


def split_semantic_pages(
    pages: Sequence[Tuple[int, str]],
    *,
    doc_id: str = "",
    max_size: int = 500,
    overlap: int = 50,
    min_size: int = 120,
) -> List[Chunk]:
    """按页切块（PDF 首选）：块永不跨页，page 记录页码，章节跨页延续。"""
    _validate(max_size, overlap, min_size)
    chunks: List[Chunk] = []
    section = ""
    for page_no, page_text in pages:
        if not page_text or not page_text.strip():
            continue
        page_chunks, section = _process_text(page_text, section, max_size, overlap, page=page_no)
        chunks.extend(page_chunks)
    if min_size:
        chunks = _merge_small(chunks, max_size, min_size)
    return _finalize(chunks, doc_id)
