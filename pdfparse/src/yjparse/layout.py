"""版面辅助：阅读顺序与页眉页脚识别。

真实研报常见两栏排版、跨栏标题和整页图表，纯按坐标 (y, x) 排序会把两栏内容交错。
这里用 XY-cut 递归投影切割：先在页面上找横向整宽的空白把页面切成上下段，
再在段内找纵向空白切成左右栏，逐层递归，得到接近人眼阅读的顺序。
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from .contract import Block


def is_furniture(block: Block, page_h: float, margin: float = 40.0) -> bool:
    """页眉页脚判定：位于上下边距带内的文本块。"""
    if block.type != "text" or block.bbox is None:
        return False
    return block.bbox.y1 <= margin or block.bbox.y0 >= page_h - margin


def _ykey(block: Block):
    bbox = block.bbox
    if bbox is None:
        return (0.0, 0.0)
    return (round(bbox.y0 / 4.0), bbox.x0)


def _center_x(block: Block) -> float:
    bbox = block.bbox
    return (bbox.x0 + bbox.x1) / 2 if bbox else 0.0


def _is_full_width(block: Block, page_w: float, ratio: float = 0.55) -> bool:
    return bool(block.bbox) and block.bbox.width >= page_w * ratio


def sort_band(items: Sequence[Block], page_w: float) -> List[Block]:
    """对一个横向带内的块排序：能分出两栏就左栏优先，否则按坐标顺序。"""
    items = list(items)
    if len(items) < 4:
        return sorted(items, key=_ykey)
    mid = page_w / 2.0
    narrow = [b for b in items if not _is_full_width(b, page_w)]
    left = [b for b in narrow if _center_x(b) < mid]
    right = [b for b in narrow if _center_x(b) >= mid]
    if len(left) >= 2 and len(right) >= 2:
        left_max = max(b.bbox.x1 for b in left if b.bbox)
        right_min = min(b.bbox.x0 for b in right if b.bbox)
        if left_max <= right_min + 8.0:  # 两栏在水平方向不重叠，认定为双栏
            return sorted(left, key=_ykey) + sorted(right, key=_ykey)
    return sorted(items, key=_ykey)


def column_count(blocks: Sequence[Block], page_w: float) -> int:
    """估计页面栏数，用于质量报告与调试。"""
    narrow = [b for b in blocks if b.bbox and not _is_full_width(b, page_w)]
    if len(narrow) < 4:
        return 1
    mid = page_w / 2.0
    left = [b for b in narrow if _center_x(b) < mid]
    right = [b for b in narrow if _center_x(b) >= mid]
    if len(left) >= 2 and len(right) >= 2:
        left_max = max(b.bbox.x1 for b in left if b.bbox)
        right_min = min(b.bbox.x0 for b in right if b.bbox)
        if left_max <= right_min + 8.0:
            return 2
    return 1


def _gaps(intervals: Sequence[Tuple[float, float]], min_gap: float
          ) -> List[Tuple[float, float, float]]:
    """在区间集合上找空白段，返回 (宽度, 起点, 终点)，用于投影切割。"""
    if not intervals:
        return []
    events: List[Tuple[float, int]] = []
    for start, end in intervals:
        if end <= start:
            continue
        events.append((start, 1))
        events.append((end, -1))
    events.sort()
    gaps: List[Tuple[float, float, float]] = []
    coverage = 0
    prev: Optional[float] = None
    index = 0
    while index < len(events):
        position = events[index][0]
        if prev is not None and coverage == 0 and position - prev >= min_gap:
            gaps.append((position - prev, prev, position))
        while index < len(events) and events[index][0] == position:
            coverage += events[index][1]
            index += 1
        prev = position
    return gaps


def order_blocks_xycut(blocks: Sequence[Block], page_w: float, page_h: float,
                       min_gap: float = 8.0, max_depth: int = 12) -> List[Block]:
    """XY-cut 阅读顺序：递归按整宽/整高空白切分，再在各区域内排序。"""
    items = [b for b in blocks if b.bbox is not None and b.bbox.area > 0]
    missing = [b for b in blocks if b.bbox is None or b.bbox.area <= 0]

    def cut(region: List[Block], depth: int) -> List[Block]:
        if len(region) <= 1 or depth >= max_depth:
            return sorted(region, key=_ykey)
        h_gaps = _gaps([(b.bbox.y0, b.bbox.y1) for b in region], min_gap)
        v_gaps = _gaps([(b.bbox.x0, b.bbox.x1) for b in region], min_gap)
        h_best = max(h_gaps, default=None)
        v_best = max(v_gaps, default=None)
        # 归一化后比较两个方向的空白宽度，优先切更明显的那一刀
        h_score = (h_best[0] / page_h) if h_best else 0.0
        v_score = (v_best[0] / page_w) if v_best else 0.0
        if max(h_score, v_score) <= 0:
            return sorted(region, key=_ykey)
        if h_score >= v_score:
            split = (h_best[1] + h_best[2]) / 2.0
            top = [b for b in region if b.bbox.y1 <= split]
            bottom = [b for b in region if b.bbox.y1 > split]
        else:
            split = (v_best[1] + v_best[2]) / 2.0
            top = [b for b in region if b.bbox.x1 <= split]
            bottom = [b for b in region if b.bbox.x1 > split]
        if not top or not bottom:
            return sorted(region, key=_ykey)
        return cut(top, depth + 1) + cut(bottom, depth + 1)

    ordered = cut(items, 0)
    ordered.extend(missing)
    for index, block in enumerate(ordered):
        block.order = index
    return ordered


def column_switch_count(blocks: Sequence[Block], page_w: float) -> int:
    """统计相邻块之间跨栏跳转次数。

    正确的分栏阅读顺序每段只应跳转一次，按坐标硬排会每行跳一次，
    因此这个数字可以直接衡量阅读顺序的质量。
    """
    mid = page_w / 2.0
    sides = []
    for block in sorted([b for b in blocks if b.bbox], key=lambda b: b.order):
        if _is_full_width(block, page_w):
            sides.append("F")
        else:
            sides.append("L" if _center_x(block) < mid else "R")
    seq = [s for s in sides if s != "F"]
    return sum(1 for a, b in zip(seq, seq[1:]) if a != b)


def order_blocks(blocks: Sequence[Block], page_w: float, page_h: Optional[float] = None,
                 mode: str = "xycut") -> List[Block]:
    """对外统一入口：默认 XY-cut，mode=band 时退回原来的分带方案。"""
    if page_h is None:
        page_h = max((b.bbox.y1 for b in blocks if b.bbox), default=0.0)
    if str(mode).lower() == "band":
        return _order_blocks_band(blocks, page_w)
    return order_blocks_xycut(blocks, page_w, page_h)


def _order_blocks_band(blocks: Sequence[Block], page_w: float) -> List[Block]:
    """分带排序（保留为对照实现，便于 A/B 比较）。"""
    items = [b for b in blocks if b.bbox is not None]
    missing = [b for b in blocks if b.bbox is None]
    ordered: List[Block] = []
    band: List[Block] = []
    for block in sorted(items, key=_ykey):
        if _is_full_width(block, page_w):
            if band:
                ordered.extend(sort_band(band, page_w))
                band = []
            ordered.append(block)
        else:
            band.append(block)
    if band:
        ordered.extend(sort_band(band, page_w))
    ordered.extend(missing)
    for index, block in enumerate(ordered):
        block.order = index
    return ordered
