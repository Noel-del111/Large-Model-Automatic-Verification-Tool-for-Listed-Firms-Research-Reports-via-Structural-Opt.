"""文本结构后处理：标题识别、句级切分、图表标题与来源归并。

下游核查需要引用到“第几页第几句”，所以段落块要能给出句子级文本与位置；
图表标题与资料来源则要挂到对应的表格或图片上，才能形成完整证据链。
"""

from __future__ import annotations

import re
from statistics import median
from typing import Iterable, List, Optional, Sequence, Tuple

from .contract import BBox, Block, Sentence

CAPTION_PATTERN = re.compile(r"^\s*(图|表|图表|附注|Exhibit|Figure|Table)\s*[0-9一二三四五六七八九十]+")
SOURCE_PATTERN = re.compile(r"^\s*(资料来源|数据来源|信息来源|来源|注)\s*[:：]")
SENTENCE_PATTERN = re.compile(r"[^。！？；!?;\n]+[。！？；!?;]?")
HEADING_MAX_CHARS = 60


def body_font_size(sizes: Sequence[float]) -> float:
    """用中位数近似正文字号，避免个别大标题拉高基准。"""
    values = [s for s in sizes if s and s > 0]
    return float(median(values)) if values else 0.0


def classify_text_block(text: str, max_size: float, base_size: float,
                        bold: bool = False) -> Tuple[str, str]:
    """按字号相对大小判定标题层级；过长的文本一律按正文处理。"""
    stripped = text.strip()
    if not stripped:
        return "text", "body"
    # 标题的形态特征：单行、短、不以句读结尾、不含并列分号
    if ("\n" in stripped or stripped[-1] in "。，；、：,;:"
            or any(ch in stripped for ch in "。，；")):
        return "text", "body"
    ratio = (max_size / base_size) if base_size else 1.0
    if len(stripped) <= HEADING_MAX_CHARS:
        if ratio >= 1.6:
            return "heading", "h1"
        if ratio >= 1.25:
            return "heading", "h2"
        if ratio >= 1.15 or (bold and ratio >= 1.08):
            return "heading", "h3"
    return "text", "body"


def _trim_bbox(bbox: BBox, line_len: int, start: int, end: int) -> BBox:
    """按字符比例把行框横向切出一段，用于近似句子位置。"""
    if line_len <= 0:
        return bbox
    total = max(bbox.width, 0.1)
    x0 = bbox.x0 + total * (start / line_len)
    x1 = bbox.x0 + total * (end / line_len)
    return BBox(min(x0, x1), bbox.y0, max(x0, x1), bbox.y1)


def build_sentences(lines: Sequence[Tuple[str, BBox]],
                    max_sentences: int = 400,
                    line_separator: Optional[str] = None) -> List[Sentence]:
    """把若干行文本拼成段落并切句，返回句子文本、字符区间与近似坐标。"""
    if not lines:
        return []
    parts: List[str] = []
    ranges: List[Tuple[int, int, str, BBox]] = []
    pos = 0
    for index, (text, bbox) in enumerate(lines):
        # 中文换行不补空格，英文换行补空格，否则中文句子会被拆出多余空白
        if index:
            separator = line_separator if line_separator is not None else (
                " " if _needs_space(parts[-1][-1:], text[:1]) else "")
            parts.append(separator)
            pos += len(separator)
        start = pos
        parts.append(text)
        pos += len(text)
        ranges.append((start, pos, text, bbox))
    full = "".join(parts)

    sentences: List[Sentence] = []
    # Explicit separators make offsets address the exact Block.text, including
    # line breaks. Default normalization is retained for standalone callers.
    pattern = re.compile(r"[^。！？；!?;]+[。！？；!?;]?") if line_separator is not None else SENTENCE_PATTERN
    for match in pattern.finditer(full):
        raw = match.group(0)
        stripped = raw.strip()
        if not stripped:
            continue
        lead = len(raw) - len(raw.lstrip())
        start = match.start() + lead
        end = start + len(stripped)
        boxes: List[BBox] = []
        for line_start, line_end, line_text, line_bbox in ranges:
            overlap_start = max(start, line_start)
            overlap_end = min(end, line_end)
            if overlap_start >= overlap_end:
                continue
            boxes.append(_trim_bbox(line_bbox, len(line_text),
                                    overlap_start - line_start, overlap_end - line_start))
        bbox = None
        if boxes:
            bbox = BBox(min(b.x0 for b in boxes), min(b.y0 for b in boxes),
                        max(b.x1 for b in boxes), max(b.y1 for b in boxes))
        sentences.append(Sentence(text=stripped, bbox=bbox, char_start=start, char_end=end))
        if len(sentences) >= max_sentences:
            break
    return sentences


def _needs_space(previous: str, following: str) -> bool:
    if not previous or not following:
        return False
    return (previous.isascii() and following.isascii()
            and previous.isalnum() and following.isalnum())


def is_caption(text: str) -> bool:
    return bool(CAPTION_PATTERN.match(text or ""))


def is_source_note(text: str) -> bool:
    return bool(SOURCE_PATTERN.match(text or ""))


def attach_captions_and_sources(blocks: Iterable[Block],
                                max_distance_pt: float = 160.0) -> int:
    """把图表标题与资料来源挂到最近的表格或图片上，返回归并的数量。

    研报里的标题在表格上方、来源在表格下方，按垂直距离就近挂接即可覆盖绝大多数情况。
    """
    ordered = [b for b in blocks if b.bbox is not None]
    ordered.sort(key=lambda b: (round(b.bbox.y0 / 4.0), b.bbox.x0))
    attached = 0
    for block in ordered:
        if block.type not in {"text", "heading"} or not block.text:
            continue
        caption = is_caption(block.text)
        source = is_source_note(block.text)
        if not (caption or source):
            continue
        candidates = []
        for target in ordered:
            if target.type not in {"table", "image"} or target.bbox is None:
                continue
            # 标题在表格上方、来源在表格下方，按对应方向计算间距
            distance = (target.bbox.y0 - block.bbox.y1) if caption else (block.bbox.y0 - target.bbox.y1)
            overlap = min(target.bbox.x1, block.bbox.x1) - max(target.bbox.x0, block.bbox.x0)
            overlap_ratio = max(0.0, overlap) / max(min(target.bbox.width, block.bbox.width), 1.0)
            if -8.0 <= distance <= max_distance_pt and overlap_ratio >= 0.5:
                candidates.append((abs(distance), -overlap_ratio, target))
        candidates.sort(key=lambda item: item[:2])
        if not candidates:
            continue
        if len(candidates) > 1 and candidates[0][:2] == candidates[1][:2]:
            continue  # Equidistant spanning text cannot safely be assigned to a column.
        best = candidates[0][2]
        if caption:
            if best.caption and best.caption != block.text.strip():
                continue
            best.caption = block.text.strip()
            block.type = "caption"
        else:
            # 同一来源在块内可能重复出现，按行去重后再挂接
            existing = list(dict.fromkeys(line.strip() for line in best.source_note.splitlines() if line.strip()))
            seen = set(existing)
            unique = [line.strip() for line in block.text.splitlines()
                      if line.strip() and line.strip() not in seen]
            if unique:
                merged = "\n".join(existing + unique)
                best.source_note = merged[:300]
            block.type = "caption"
        attached += 1
    return attached
