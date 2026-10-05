"""Docling 适配器（可选主引擎）。

Docling 的文档模型为每个元素提供 provenance（page_no + bbox + charspan），
坐标质量与阅读顺序都优于坐标排序基线，但是依赖 torch，首次运行需要下载模型。

状态说明：本适配器按 Docling 2.x 的公开接口编写，当前机器未安装该引擎，
因此尚未做端到端验证；团队确定选用后需按 REPRODUCE.md 的清单复核一次。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from ..contract import BBox, Block, Cell, Page
from .base import BaseEngine, EngineCapabilities, EngineUnavailable, register

VERIFIED = False

LABEL_MAP = {
    "section_header": "heading",
    "title": "heading",
    "table": "table",
    "picture": "image",
    "formula": "formula",
    "caption": "caption",
    "code": "other",
}


def _label_of(item: Any) -> str:
    label = getattr(item, "label", None)
    text = str(getattr(label, "value", label) or "").lower()
    return LABEL_MAP.get(text, "text")


def _cell_bbox(cell: Any, page_height: float) -> Optional[BBox]:
    bbox = getattr(cell, "bbox", None)
    if bbox is None:
        return None
    origin = str(getattr(bbox, "coord_origin", "")).upper()
    if "BOTTOM" in origin:
        return BBox(float(bbox.l), page_height - float(bbox.t),
                    float(bbox.r), page_height - float(bbox.b))
    return BBox(float(bbox.l), float(bbox.t), float(bbox.r), float(bbox.b))


@register
class DoclingEngine(BaseEngine):
    name = "docling"
    install_hint = "安装依赖：pip install docling（含 torch，首次运行需下载模型，请提前离线化）"

    def version(self) -> str:
        try:
            import importlib.metadata as md

            return md.version("docling")
        except Exception:
            return ""

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            coordinates=True,
            tables=True,
            reading_order=True,
            scanned_ocr=True,
            notes=[
                "每个元素带 provenance（页码 + 边界框 + 字符跨度）",
                "含阅读顺序与章节树",
                f"本机未验证（VERIFIED={VERIFIED}）",
            ],
        )

    @classmethod
    def available(cls) -> bool:
        try:
            import docling  # noqa: F401

            return True
        except Exception:
            return False

    def parse(self, path: Path, doc_id: str, ledger=None) -> List[Page]:
        if not self.available():  # pragma: no cover
            raise EngineUnavailable(self.install_hint)
        from docling.document_converter import DocumentConverter

        converter = DocumentConverter()
        result = converter.convert(str(path))
        document = result.document

        sizes: Dict[int, tuple] = {}
        for page_no, page_item in (getattr(document, "pages", {}) or {}).items():
            size = getattr(page_item, "size", None)
            if size is not None:
                sizes[int(page_no)] = (float(size.width), float(size.height))

        buckets: Dict[int, List[Block]] = {}
        for item, _level in document.iterate_items():
            kind = _label_of(item)
            for prov in getattr(item, "prov", []) or []:
                page_no = int(getattr(prov, "page_no", 0) or 0)
                if page_no <= 0:
                    continue
                page_h = sizes.get(page_no, (0.0, 0.0))[1]
                bbox = _cell_bbox(prov, page_h)
                block = Block(
                    block_id=f"p{page_no}_{kind}{len(buckets.get(page_no, []))}",
                    type=kind,
                    bbox=bbox,
                    order=len(buckets.get(page_no, [])),
                    text=(getattr(item, "text", "") or "").strip(),
                    caption=(getattr(item, "caption_text", "") or "").strip()
                    if hasattr(item, "caption_text") else "",
                )
                if kind == "table":
                    data = getattr(item, "data", None)
                    for cell in getattr(data, "table_cells", []) or []:
                        r = getattr(cell, "start_row_offset_idx",
                                    getattr(cell, "row_offset", 0))
                        c = getattr(cell, "start_col_offset_idx",
                                    getattr(cell, "col_offset", 0))
                        block.cells.append(Cell(
                            row=int(r or 0),
                            col=int(c or 0),
                            text=(getattr(cell, "text", "") or "").strip(),
                            bbox=_cell_bbox(cell, page_h),
                        ))
                buckets.setdefault(page_no, []).append(block)

        pages: List[Page] = []
        for page_no in sorted(buckets):
            blocks = sorted(buckets[page_no],
                            key=lambda b: (round((b.bbox.y0 if b.bbox else 0) / 4.0),
                                           b.bbox.x0 if b.bbox else 0))
            for order, block in enumerate(blocks):
                block.order = order
            pages.append(Page(
                page=page_no,
                page_size=sizes.get(page_no, (0.0, 0.0)),
                blocks=blocks,
                notes=[] if VERIFIED else ["adapter_unverified_in_this_env"],
            ))
            if ledger is not None:
                ledger.compute(op="engine_page_extract", page=page_no,
                               inputs={"engine": self.name},
                               result={"blocks": len(blocks)}, decision="extracted")
        return pages
