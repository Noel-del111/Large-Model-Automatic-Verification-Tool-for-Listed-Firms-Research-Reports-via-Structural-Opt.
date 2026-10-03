"""pdfplumber 引擎：MIT 许可的轻量对照引擎。

按行聚类成块，块粒度比版面引擎粗，适合作为交叉校验的对照组，
或在需要规避 AGPL 依赖时替换 PyMuPDF 作为主引擎。
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple

from ..contract import BBox, Block, Cell, Page
from ..layout import column_count, is_furniture, order_blocks
from .base import BaseEngine, EngineCapabilities, EngineUnavailable, register

try:
    import pdfplumber  # type: ignore
except Exception as exc:  # pragma: no cover
    pdfplumber = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


def _version() -> str:
    if pdfplumber is None:
        return ""
    try:
        import importlib.metadata as md

        return md.version("pdfplumber")
    except Exception:
        return getattr(pdfplumber, "__version__", "")


@register
class PdfPlumberEngine(BaseEngine):
    name = "pdfplumber"
    install_hint = "安装依赖：pip install pdfplumber"

    def version(self) -> str:
        return _version()

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            coordinates=True,
            tables=True,
            reading_order=False,
            scanned_ocr=False,
            notes=["按行聚类成块，块粒度较粗", "表格为规则线框检测，复杂版面需换引擎"],
        )

    @classmethod
    def available(cls) -> bool:
        return pdfplumber is not None

    def _line_blocks(self, page) -> List[Block]:
        words = page.extract_words(use_text_flow=False, keep_blank_chars=False) or []
        lines: Dict[int, List[dict]] = {}
        for word in words:
            key = int(round(float(word["top"]) / 3.0))
            lines.setdefault(key, []).append(word)
        blocks: List[Block] = []
        for idx, key in enumerate(sorted(lines)):
            group = sorted(lines[key], key=lambda w: w["x0"])
            text = " ".join(w["text"] for w in group)
            bbox = BBox(
                min(float(w["x0"]) for w in group),
                min(float(w["top"]) for w in group),
                max(float(w["x1"]) for w in group),
                max(float(w["bottom"]) for w in group),
            )
            blocks.append(Block(
                block_id=f"line{idx}",
                type="text",
                bbox=bbox,
                order=idx,
                text=text,
            ))
        return blocks

    def _table_blocks(self, page) -> List[Block]:
        blocks: List[Block] = []
        try:
            finder = page.find_tables()
        except Exception:
            return blocks
        for idx, table in enumerate(finder or []):
            bbox = BBox.from_any(table.bbox)
            try:
                matrix = table.extract()
            except Exception:
                matrix = []
            cells: List[Cell] = []
            table_rows = list(getattr(table, "rows", []) or [])
            for r_idx, row in enumerate(matrix or []):
                for c_idx, value in enumerate(row or []):
                    cell_bbox = None
                    row_cells = table_rows[r_idx].cells if r_idx < len(table_rows) else []
                    if c_idx < len(row_cells) and row_cells[c_idx]:
                        cell_bbox = BBox.from_any(row_cells[c_idx])
                    cells.append(Cell(row=r_idx, col=c_idx, text=(value or "").strip(), bbox=cell_bbox))
            blocks.append(Block(
                block_id=f"table{idx}",
                type="table",
                bbox=bbox,
                order=1000 + idx,
                cells=cells,
            ))
        return blocks

    def parse(self, path: Path, doc_id: str, ledger=None) -> List[Page]:
        if pdfplumber is None:  # pragma: no cover
            raise EngineUnavailable(self.install_hint)
        margin = float(self.params.get("furniture_margin_pt", 40.0))
        pages: List[Page] = []
        with pdfplumber.open(str(path)) as pdf:
            for idx, page in enumerate(pdf.pages, start=1):
                blocks = self._line_blocks(page)
                for block in blocks:
                    if is_furniture(block, float(page.height), margin):
                        block.level = "furniture"
                tables = self._table_blocks(page)
                merged = order_blocks(blocks + tables, float(page.width), float(page.height))
                pages.append(Page(
                    page=idx,
                    page_size=(float(page.width), float(page.height)),
                    blocks=merged,
                    engine_stats={
                        "columns": column_count(merged, float(page.width)),
                        "rotation": getattr(page, "rotation", 0) or 0,
                        "image_area": sum(max(0.0, min(float(page.width), float(image.get("x1", 0))) - max(0.0, float(image.get("x0", 0))))
                                          * max(0.0, min(float(page.height), float(image.get("bottom", 0))) - max(0.0, float(image.get("top", 0))))
                                          for image in page.images),
                    },
                ))
                if ledger is not None:
                    ledger.compute(
                        op="engine_page_extract",
                        page=idx,
                        inputs={"engine": self.name, "path": str(path)},
                        result={"blocks": len(merged), "tables": len(tables)},
                        decision="extracted",
                    )
        return pages

    def page_texts(self, path: Path) -> Dict[int, str]:
        if pdfplumber is None:  # pragma: no cover
            raise EngineUnavailable(self.install_hint)
        texts: Dict[int, str] = {}
        with pdfplumber.open(str(path)) as pdf:
            for idx, page in enumerate(pdf.pages, start=1):
                texts[idx] = page.extract_text() or ""
        return texts
