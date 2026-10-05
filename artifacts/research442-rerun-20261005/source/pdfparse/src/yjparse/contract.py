"""解析结果的数据契约。

设计要点：
1. 页级与块级都强制要求页码与坐标（bbox），缺失即判失败，保证结论可以回链到原文位置。
2. 结果中携带引擎名、版本、参数、模型哈希、运行编号，保证一次解析可复现、可追溯。
3. 坐标系约定：单位为点（1/72 英寸），原点在页面左上角，x 向右、y 向下，与 pdfplumber 口径一致。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

SCHEMA_VERSION = "1.1.0"

BLOCK_TYPES = {"text", "heading", "table", "image", "formula", "caption", "other"}
STATUSES = ("ok", "warn", "fail")


@dataclass
class BBox:
    """页面内的矩形区域，单位为点，原点在左上角。"""

    x0: float
    y0: float
    x1: float
    y1: float

    def to_list(self) -> List[float]:
        return [round(self.x0, 2), round(self.y0, 2), round(self.x1, 2), round(self.y1, 2)]

    @property
    def width(self) -> float:
        return max(0.0, self.x1 - self.x0)

    @property
    def height(self) -> float:
        return max(0.0, self.y1 - self.y0)

    @property
    def area(self) -> float:
        return self.width * self.height

    def is_degenerate(self, min_side: float = 0.5) -> bool:
        return self.width < min_side or self.height < min_side

    def out_of_page(self, page_w: float, page_h: float, tol: float = 2.0) -> bool:
        return (
            self.x0 < -tol
            or self.y0 < -tol
            or self.x1 > page_w + tol
            or self.y1 > page_h + tol
        )

    @classmethod
    def from_any(cls, value: Sequence[float]) -> "BBox":
        x0, y0, x1, y1 = (float(v) for v in value[:4])
        return cls(
            min(x0, x1),
            min(y0, y1),
            max(x0, x1),
            max(y0, y1),
        )


@dataclass
class Cell:
    """表格单元格。bbox 允许为空，但为空时质量报告会给出警告。"""

    row: int
    col: int
    text: str = ""
    bbox: Optional[BBox] = None
    row_span: int = 1
    col_span: int = 1


@dataclass
class Sentence:
    """句子及其在页面上的近似位置。

    核查结论需要引用到句级，这里给出句子文本、坐标与在块内的字符区间，
    坐标由所在行按字符比例切分得到，属于近似值，用于高亮定位足够。
    """

    text: str
    bbox: Optional[BBox] = None
    char_start: int = 0
    char_end: int = 0


@dataclass
class Block:
    block_id: str
    type: str = "text"
    bbox: Optional[BBox] = None
    order: int = 0
    text: str = ""
    level: str = "body"
    caption: str = ""
    source_note: str = ""
    confidence: Optional[float] = None
    cells: List[Cell] = field(default_factory=list)
    sentences: List[Sentence] = field(default_factory=list)

    def as_block_list(self) -> List[List[str]]:
        """把单元格整理成二维文本表，便于下游做结构化比对。"""
        if not self.cells:
            return []
        rows = max(c.row for c in self.cells) + 1
        cols = max(c.col for c in self.cells) + 1
        grid = [["" for _ in range(cols)] for _ in range(rows)]
        for cell in self.cells:
            if cell.row < rows and cell.col < cols:
                grid[cell.row][cell.col] = cell.text
        return grid


@dataclass
class PageQuality:
    char_count: int = 0
    block_count: int = 0
    table_count: int = 0
    image_count: int = 0
    image_area_ratio: float = 0.0
    text_coverage: float = 0.0          # 每千平方点的有效字符数，衡量版面文字密度
    garbled_ratio: float = 0.0
    engine_agreement: Optional[float] = None       # 顺序敏感的一致度，用于发现阅读顺序分歧
    engine_agreement_bag: Optional[float] = None   # 顺序不敏感的一致度，用于判定内容是否漏抽
    engine_agreement_token: Optional[float] = None  # 词元口径（诊断用，受空格影响）
    reference_char_count: Optional[int] = None     # 对照引擎在该页抽到的字符数
    table_col_inconsistent: bool = False
    table_empty_cell_ratio: float = 0.0
    table_cell_count: int = 0
    tables_filtered: int = 0
    sentence_count: int = 0
    heading_count: int = 0
    vlm_checked: bool = False
    vlm_verdict: str = ""            # match / mismatch / error
    vlm_confidence: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items()}


@dataclass
class Page:
    page: int
    page_size: Tuple[float, float]
    blocks: List[Block] = field(default_factory=list)
    status: str = "ok"
    quality: PageQuality = field(default_factory=PageQuality)
    notes: List[str] = field(default_factory=list)
    engine_stats: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DocumentMeta:
    doc_id: str
    source_path: str
    sha256: str
    total_pages: int
    bytes: int = 0
    title: str = ""


@dataclass
class EngineInfo:
    name: str
    version: str = ""
    strategy: str = ""
    model: str = ""
    model_sha256: str = ""
    params: Dict[str, Any] = field(default_factory=dict)
    started_at: str = ""
    duration_s: float = 0.0


@dataclass
class QualityReport:
    status: str = "ok"
    page_status: Dict[str, str] = field(default_factory=dict)
    failed_pages: List[int] = field(default_factory=list)
    warned_pages: List[int] = field(default_factory=list)
    reasons: Dict[str, List[str]] = field(default_factory=dict)
    summary: Dict[str, Any] = field(default_factory=dict)
    violations: List[str] = field(default_factory=list)

    @staticmethod
    def worst(statuses: Sequence[str]) -> str:
        order = {"ok": 0, "warn": 1, "fail": 2}
        return max(statuses, key=lambda s: order.get(s, 0)) if statuses else "ok"


@dataclass
class ParseResult:
    run_id: str
    doc: DocumentMeta
    engine: EngineInfo
    pages: List[Page]
    quality_report: QualityReport
    reference_engine: Optional[Dict[str, Any]] = None
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "doc": asdict(self.doc),
            "engine": asdict(self.engine),
            "reference_engine": self.reference_engine,
            "quality_report": {
                "status": self.quality_report.status,
                "page_status": self.quality_report.page_status,
                "failed_pages": self.quality_report.failed_pages,
                "warned_pages": self.quality_report.warned_pages,
                "reasons": self.quality_report.reasons,
                "summary": self.quality_report.summary,
                "violations": self.quality_report.violations,
            },
            "pages": [
                {
                    "page": p.page,
                    "page_size": [round(p.page_size[0], 2), round(p.page_size[1], 2)],
                    "status": p.status,
                    "quality": p.quality.to_dict(),
                    "notes": p.notes,
                    "engine_stats": p.engine_stats,
                    "blocks": [
                        {
                            "block_id": b.block_id,
                            "type": b.type,
                            "bbox": b.bbox.to_list() if b.bbox else None,
                            "order": b.order,
                            "text": b.text,
                            "level": b.level,
                            "caption": b.caption,
                            "source_note": b.source_note,
                            "confidence": b.confidence,
                            "cells": [
                                {
                                    "row": c.row,
                                    "col": c.col,
                                    "text": c.text,
                                    "bbox": c.bbox.to_list() if c.bbox else None,
                                    "row_span": c.row_span,
                                    "col_span": c.col_span,
                                }
                                for c in b.cells
                            ],
                            "sentences": [
                                {
                                    "text": s.text,
                                    "bbox": s.bbox.to_list() if s.bbox else None,
                                    "char_start": s.char_start,
                                    "char_end": s.char_end,
                                }
                                for s in b.sentences
                            ],
                        }
                        for b in p.blocks
                    ],
                }
                for p in self.pages
            ],
        }


def validate_bbox(bbox: Optional[BBox]) -> Optional[str]:
    if bbox is None:
        return "bbox_missing"
    if bbox.is_degenerate():
        return "bbox_degenerate"
    return None


def clamp_bbox(bbox: BBox, page_w: float, page_h: float) -> Tuple[BBox, bool, float]:
    """把越界框收回页面范围。

    返回（收拢后的框, 是否发生变化, 溢出比例）。溢出比例取超出页面最长边占页面尺寸的比例，
    用于判断这是排版溢出还是坐标系统错乱：小幅溢出应收拢并告警，大幅溢出仍应判失败。
    """
    clamped = BBox(
        max(0.0, min(bbox.x0, page_w)),
        max(0.0, min(bbox.y0, page_h)),
        max(0.0, min(bbox.x1, page_w)),
        max(0.0, min(bbox.y1, page_h)),
    )
    overflow = max(
        max(0.0, -bbox.x0), max(0.0, -bbox.y0),
        max(0.0, bbox.x1 - page_w), max(0.0, bbox.y1 - page_h),
    )
    ratio = overflow / max(page_w, page_h, 1.0)
    return clamped, clamped != bbox, ratio


def validate_result(result: ParseResult, tol: float = 2.0) -> List[str]:
    """返回契约层面的违规列表。任何一条都意味着该结果不可用于自动结论。"""
    violations: List[str] = []
    if not result.run_id:
        violations.append("run_id_missing")
    if not result.engine.name:
        violations.append("engine_name_missing")
    if result.doc.total_pages <= 0:
        violations.append("source_page_count_invalid")
    if len(result.pages) != result.doc.total_pages:
        violations.append(
            f"page_count_mismatch: parsed={len(result.pages)} pdf={result.doc.total_pages}"
        )
    seen_pages = set()
    for page in result.pages:
        if page.page in seen_pages:
            violations.append(f"p{page.page}: duplicate_page")
        seen_pages.add(page.page)
        if page.page < 1 or page.page > result.doc.total_pages:
            violations.append("page_number_invalid")
        w, h = page.page_size
        if w <= 0 or h <= 0:
            violations.append(f"p{page.page}: page_size_invalid")
        for block in page.blocks:
            problem = validate_bbox(block.bbox)
            if problem:
                violations.append(f"p{page.page}:{block.block_id}: {problem}")
            elif block.bbox.out_of_page(w, h, tol):
                violations.append(f"p{page.page}:{block.block_id}: bbox_out_of_page")
            if block.type not in BLOCK_TYPES:
                violations.append(f"p{page.page}:{block.block_id}: unknown_type={block.type}")
    for missing in sorted(set(range(1, result.doc.total_pages + 1)) - seen_pages):
        violations.append(f"p{missing}: missing_page")
    return violations
