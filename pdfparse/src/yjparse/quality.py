"""解析质量度量与失败判定。

三层机制中的前两层在这里实现：
  1. 确定性指标：文字密度、乱码率、坐标合法性、表格结构。
  2. 交叉校验：与对照引擎逐页比对，同时给出顺序敏感与顺序不敏感的一致度。
第三层（视觉模型抽检）留出接口，由上层按 vlm_sample_ratio 抽样调用。

两套一致度的分工：
  engine_agreement_bag  顺序不敏感，用来判断内容有没有漏抽或抽错，参与状态判定；
  engine_agreement      顺序敏感，两者内容一致但顺序差异大，说明阅读顺序可能有问题。
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Sequence, Tuple

from .contract import Page, PageQuality

CID_PATTERN = re.compile(r"\(cid:\d+\)")
WORD_PATTERN = re.compile(r"[0-9A-Za-z]+")


def normalize_text(text: str) -> str:
    """归一化：去掉控制字符、压缩空白，便于跨引擎比较。"""
    if not text:
        return ""
    cleaned = "".join(
        ch for ch in text
        if ch in "\n\t " or unicodedata.category(ch) not in {"Cc", "Cf"}
    )
    cleaned = cleaned.replace("\u00a0", " ").replace("\u3000", " ")
    return re.sub(r"\s+", " ", cleaned).strip()


def garbled_ratio(text: str) -> float:
    """统计替换字符、CID 残留与私用区字符占比。"""
    if not text:
        return 0.0
    bad = len(CID_PATTERN.findall(text))
    for ch in text:
        code = ord(ch)
        if ch == "\ufffd" or 0xE000 <= code <= 0xF8FF or 0xF0000 <= code <= 0xFFFFD:
            bad += 1
    return bad / max(len(text), 1)


def sequence_agreement(a: str, b: str) -> Optional[float]:
    """顺序敏感的文本一致度。任一侧为空时返回 None。"""
    na, nb = normalize_text(a), normalize_text(b)
    if not na and not nb:
        return None
    if not na or not nb:
        return 0.0
    return round(SequenceMatcher(None, na, nb).ratio(), 4)


def _tokens(text: str) -> List[str]:
    """中文按单字、英文数字按单词切分，作为顺序不敏感比较的粒度。

    先去掉全部空白再切分：不同引擎对同一串数字可能给出 2021 或 2 0 2 1，
    保留空白会产生大量伪差异，把内容一致的两页判成不一致。
    """
    normalized = re.sub(r"\s+", "", normalize_text(text))
    tokens = [ch for ch in normalized if "\u4e00" <= ch <= "\u9fff"]
    tokens.extend(WORD_PATTERN.findall(normalized))
    tokens.extend(
        ch for ch in normalized
        if not ch.isspace() and not ("\u4e00" <= ch <= "\u9fff") and not ch.isalnum()
    )
    return tokens


def _multiset_jaccard(ca: Counter, cb: Counter) -> float:
    if not ca and not cb:
        return 1.0
    if not ca or not cb:
        return 0.0
    return round(sum((ca & cb).values()) / sum((ca | cb).values()), 4)


def bag_agreement(a: str, b: str) -> Optional[float]:
    """顺序不敏感的内容一致度：字符多重集合的 Jaccard 系数。

    用字符而不是词元，是因为两套引擎对同一段内容的空格处理不同
    （例如 “2024 2025” 与 “20242025”、“2 0 2 4” 与 “2024”），
    词元口径会把这些当成大量差异，把内容一致的两页判成不一致。
    顺序差异由 engine_agreement（序列口径）单独反映，两者分工明确。
    """
    ca = Counter(re.sub(r"\s", "", normalize_text(a)))
    cb = Counter(re.sub(r"\s", "", normalize_text(b)))
    if not ca and not cb:
        return None
    return round(_multiset_jaccard(ca, cb), 4)


def token_agreement(a: str, b: str) -> Optional[float]:
    """词元口径的一致度，仅作诊断指标，不参与状态判定。"""
    ta, tb = _tokens(a), _tokens(b)
    if not ta and not tb:
        return None
    return _multiset_jaccard(Counter(ta), Counter(tb))


def page_text(page: Page) -> str:
    parts: List[str] = []
    for block in sorted(page.blocks, key=lambda b: b.order):
        if block.type == "table" and block.cells:
            parts.append(" ".join(c.text for c in block.cells if c.text))
        elif block.text:
            parts.append(block.text)
    return "\n".join(parts)


def image_area_ratio(page: Page) -> float:
    """图片块覆盖的页面面积比例，用于区分整页图表与空白页。"""
    page_area = max(page.page_size[0] * page.page_size[1], 1.0)
    covered = sum(b.bbox.area for b in page.blocks if b.type == "image" and b.bbox)
    return round(min(covered / page_area, 1.0), 4)


def table_stats(page: Page) -> Tuple[bool, float, int]:
    """返回（同一张表内列数是否不一致, 空单元格比例, 单元格总数）。"""
    inconsistent = False
    total, empty = 0, 0
    for block in page.blocks:
        if block.type != "table" or not block.cells:
            continue
        rows: Dict[int, set] = {}
        for cell in block.cells:
            rows.setdefault(cell.row, set()).add(cell.col)
            total += 1
            if not cell.text.strip():
                empty += 1
        if len({len(cols) for cols in rows.values()}) > 1:
            inconsistent = True
    ratio = round(empty / total, 4) if total else 0.0
    return inconsistent, ratio, total


def compute_page_quality(page: Page, reference_text: Optional[str] = None,
                         tables_filtered: int = 0) -> PageQuality:
    text = page_text(page)
    area_k = (page.page_size[0] * page.page_size[1]) / 1000.0
    chars = len(re.sub(r"\s", "", text))
    inconsistent, empty_ratio, cell_total = table_stats(page)
    quality = PageQuality(
        char_count=chars,
        block_count=len(page.blocks),
        table_count=sum(1 for b in page.blocks if b.type == "table"),
        image_count=sum(1 for b in page.blocks if b.type == "image"),
        image_area_ratio=image_area_ratio(page),
        text_coverage=round(chars / area_k, 3) if area_k else 0.0,
        garbled_ratio=round(garbled_ratio(text), 5),
        table_col_inconsistent=inconsistent,
        table_empty_cell_ratio=empty_ratio,
        table_cell_count=cell_total,
        tables_filtered=tables_filtered,
        sentence_count=sum(len(b.sentences) for b in page.blocks),
        heading_count=sum(1 for b in page.blocks if b.type == "heading"),
    )
    if reference_text is not None:
        quality.engine_agreement = sequence_agreement(text, reference_text)
        quality.engine_agreement_bag = bag_agreement(text, reference_text)
        quality.engine_agreement_token = token_agreement(text, reference_text)
        quality.reference_char_count = len(re.sub(r"\s", "", normalize_text(reference_text)))
    return quality


def _raise(status: str, target: str) -> str:
    order = {"ok": 0, "warn": 1, "fail": 2}
    return status if order[status] >= order[target] else target


def decide_status(quality: PageQuality, violations: Sequence[str], thresholds: Dict,
                  doc_context: Optional[Dict] = None) -> Tuple[str, List[str]]:
    """按阈值给出页面状态与原因。

    violations 为契约层面的硬问题（坐标缺失、坐标严重越界等），一律判失败。
    doc_context 提供文档级信息，用于区分扫描件、整页图表与空白页。
    """
    doc_context = doc_context or {}
    reasons: List[str] = list(violations)
    status = "fail" if violations else "ok"

    if quality.garbled_ratio > thresholds.get("garbled_ratio_fail", 0.01):
        status = "fail"
        reasons.append(f"garbled_ratio={quality.garbled_ratio}")

    min_chars = thresholds.get("min_text_layer_chars_per_page", 30)
    if quality.char_count == 0:
        image_ratio = thresholds.get("image_only_page_min_area_ratio", 0.3)
        if quality.image_count and quality.image_area_ratio >= image_ratio:
            status = _raise(status, "warn")
            reasons.append(
                "image_only_page:image_area="
                f"{quality.image_area_ratio}（整页图表，若正文在图中需走 OCR）"
            )
        elif doc_context.get("has_text_layer", True):
            status = "fail"
            reasons.append(f"no_text_layer:empty_page:chars={quality.char_count}")
        else:
            status = "fail"
            reasons.append(f"no_text_layer:scanned_pdf:chars={quality.char_count}")
    elif quality.char_count < min_chars:
        # 有少量文字：章节分隔页、版权页、单行注释页等，属于正常版面而非解析失败，
        # 只作为信息记录，避免这类页面把整篇文档拖成“需人工复核”
        reasons.append(f"info:sparse_page:chars={quality.char_count}")
    else:
        # 文字密度只作记录，不单独升级为警告：
        # 研报里图表页、标注页、分隔页天然字少，真正的抽取失败由双引擎比对来抓。
        coverage_warn = thresholds.get("text_coverage_warn", 0.8)
        if quality.text_coverage < coverage_warn:
            blocks = max(quality.block_count, 1)
            avg_chars = quality.char_count / blocks
            if quality.image_area_ratio >= thresholds.get("chart_page_image_ratio", 0.3):
                label = "chart_page"
            elif (quality.block_count >= thresholds.get("chart_label_min_blocks", 12)
                  and avg_chars <= thresholds.get("chart_label_max_avg_chars", 22)):
                label = "chart_labels"
            elif quality.char_count < 120:
                label = "divider_page"
            else:
                label = "text_sparse"
            reasons.append(
                f"info:low_text_coverage={quality.text_coverage}"
                f"(type={label},blocks={quality.block_count},avg_chars={round(avg_chars, 1)})"
            )

    bag = quality.engine_agreement_bag
    if bag is not None:
        reference_short = (
            quality.reference_char_count is not None
            and quality.char_count >= min_chars
            and quality.reference_char_count < quality.char_count * 0.5
        )
        if reference_short and bag < thresholds.get("engine_agreement_warn", 0.85):
            # 对照引擎自身抽到的内容明显更少，属于对照侧能力限制，不据此判失败
            reasons.append(
                f"info:reference_engine_incomplete:ref_chars={quality.reference_char_count}"
                f"/ours={quality.char_count},bag={bag}"
            )
        elif bag < thresholds.get("engine_agreement_fail", 0.6):
            status = "fail"
            reasons.append(f"engine_agreement_low={bag}")
        elif bag < thresholds.get("engine_agreement_warn", 0.85):
            status = _raise(status, "warn")
            reasons.append(f"engine_disagreement={bag}")
        seq = quality.engine_agreement
        if seq is not None and seq < thresholds.get("sequence_agreement_warn", 0.75):
            if thresholds.get("reading_order_divergence_is_warning", False):
                status = _raise(status, "warn")
                reasons.append(f"reading_order_divergence={seq}")
            else:
                reasons.append(f"info:reading_order_divergence={seq}")

    if quality.table_col_inconsistent:
        status = _raise(status, "warn")
        reasons.append("table_column_inconsistent")
    min_cells = thresholds.get("table_empty_cell_min_cells", 8)
    if (quality.table_empty_cell_ratio > thresholds.get("table_empty_cell_warn", 0.75)
            and quality.table_cell_count >= min_cells):
        # 单元格太少的小表（合并单元格常见）不判为结构可疑，避免噪声
        status = _raise(status, "warn")
        reasons.append(
            f"table_empty_cells={quality.table_empty_cell_ratio}"
            f"（{quality.table_cell_count} 个单元格）"
        )

    return status, reasons


def apply_page_quality(page: Page, reference_text: Optional[str], violations: Sequence[str],
                       thresholds: Dict, doc_context: Optional[Dict] = None,
                       tables_filtered: int = 0,
                       soft_notes: Sequence[str] = ()) -> Page:
    quality = compute_page_quality(page, reference_text, tables_filtered=tables_filtered)
    status, reasons = decide_status(quality, violations, thresholds, doc_context)
    # 以 info: 开头的只是记录，不改变状态；其余软问题（如 OCR 兜底）需要人工留意
    if any(not str(note).startswith("info:") for note in soft_notes):
        status = _raise(status, "warn")
    reasons.extend(soft_notes)
    page.quality = quality
    page.status = status
    page.notes = reasons
    return page
