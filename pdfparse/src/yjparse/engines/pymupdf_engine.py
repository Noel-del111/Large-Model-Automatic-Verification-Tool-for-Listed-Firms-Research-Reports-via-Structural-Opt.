"""PyMuPDF 引擎：核心依赖，可在无网络、无 GPU 环境下运行。

提供细粒度文本坐标，并在版本支持时用 find_tables() 抽取表格与单元格边框。
阅读顺序采用坐标排序近似，属于基线水平；需要严谨阅读顺序时应换用版面引擎作为主引擎。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from ..contract import BBox, Block, Cell, Page
from ..layout import column_count, column_switch_count, is_furniture, order_blocks
from ..textstruct import (
    attach_captions_and_sources,
    body_font_size,
    build_sentences,
    classify_text_block,
)
from .base import BaseEngine, EngineCapabilities, EngineUnavailable, register

try:  # PyMuPDF 1.24 起推荐 import pymupdf
    import pymupdf as fitz  # type: ignore
except Exception:  # pragma: no cover
    try:
        import fitz  # type: ignore
    except Exception as exc:  # pragma: no cover
        fitz = None
        _IMPORT_ERROR = exc
    else:
        _IMPORT_ERROR = None
else:
    _IMPORT_ERROR = None


def _version() -> str:
    if fitz is None:
        return ""
    try:
        import importlib.metadata as md

        return md.version("PyMuPDF")
    except Exception:
        try:
            return ".".join(str(p) for p in fitz.version[0:3])  # type: ignore[attr-defined]
        except Exception:
            return ""


def _rect_to_bbox(rect: Sequence[float]) -> BBox:
    return BBox.from_any(rect)


def _rotate_bbox(rect: Sequence[float], matrix) -> BBox:
    """把未旋转坐标系下的框映射到显示坐标系。

    带 /Rotate 的页面（研报里的横向图表页很常见）文本提取返回的是未旋转坐标，
    不换算会整体错位，进而被误判成坐标越界。
    """
    if matrix is None:
        return BBox.from_any(rect)
    box = fitz.Rect(rect) * matrix  # type: ignore[union-attr]
    return BBox.from_any([box.x0, box.y0, box.x1, box.y1])


def _overlap_ratio(inner: BBox, outer: BBox) -> float:
    """inner 落在 outer 内的面积占 inner 的比例，用于剔除表格内的重复文本块。"""
    if inner.area <= 0:
        return 0.0
    x0 = max(inner.x0, outer.x0)
    y0 = max(inner.y0, outer.y0)
    x1 = min(inner.x1, outer.x1)
    y1 = min(inner.y1, outer.y1)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return ((x1 - x0) * (y1 - y0)) / inner.area


@register
class PyMuPDFEngine(BaseEngine):
    name = "pymupdf"
    install_hint = "安装依赖：pip install PyMuPDF"

    def version(self) -> str:
        return _version()

    def capabilities(self) -> EngineCapabilities:
        has_tables = fitz is not None and hasattr(fitz.Page, "find_tables")
        return EngineCapabilities(
            coordinates=True,
            tables=has_tables,
            reading_order=False,
            scanned_ocr=False,
            notes=[
                "文本与表格坐标为原生精度",
                "阅读顺序为坐标近似排序",
                "无文本层的扫描件需换用 OCR 引擎",
            ],
        )

    @classmethod
    def available(cls) -> bool:
        return fitz is not None

    # ---- 内部方法 ----
    def _text_blocks(self, page, margin: float) -> List[Block]:
        """把 PyMuPDF 的文本块转成契约块：判定标题层级、切句并保留句级坐标。"""
        raw = page.get_text("dict")
        matrix = page.rotation_matrix if page.rotation else None
        paragraphs = []
        images: List[Block] = []
        sizes: List[float] = []
        for item in raw.get("blocks", []):
            if item.get("type") == 0:
                lines = []
                for line in item.get("lines", []):
                    chunks, line_sizes, bold = [], [], False
                    for span in line.get("spans", []):
                        value = span.get("text", "")
                        if not value:
                            continue
                        chunks.append(value)
                        line_sizes.append(float(span.get("size", 0) or 0))
                        if int(span.get("flags", 0)) & 16:
                            bold = True
                    line_text = "".join(chunks).strip()
                    if not line_text:
                        continue
                    lines.append((line_text, _rotate_bbox(line["bbox"], matrix),
                                  max(line_sizes) if line_sizes else 0.0, bold))
                if lines:
                    sizes.extend(size for _, _, size, _ in lines if size > 0)
                    paragraphs.append(lines)
            else:
                images.append(Block(
                    block_id=f"p{page.number + 1}_i{len(images)}",
                    type="image",
                    bbox=_rotate_bbox(item["bbox"], matrix),
                    order=0,
                ))

        base_size = body_font_size(sizes)
        blocks: List[Block] = []
        for index, lines in enumerate(paragraphs):
            text = "\n".join(line[0] for line in lines)
            bbox = BBox(min(line[1].x0 for line in lines), min(line[1].y0 for line in lines),
                        max(line[1].x1 for line in lines), max(line[1].y1 for line in lines))
            max_size = max((line[2] for line in lines), default=0.0)
            bold = any(line[3] for line in lines)
            kind, level = classify_text_block(text, max_size, base_size, bold)
            blocks.append(Block(
                block_id=f"p{page.number + 1}_t{index}",
                type=kind,
                bbox=bbox,
                order=0,
                text=text,
                level=level,
                sentences=build_sentences([(line[0], line[1]) for line in lines],
                                          line_separator="\n"),
            ))
        blocks.extend(images)
        return blocks

    def _collect_tables(self, page, strategy: str, min_cells: int):
        """按指定策略检测表格，返回 (bbox, cells, 行数, 列数, 非空单元格数)。"""
        out = []
        finder = getattr(page, "find_tables", None)
        if finder is None:
            return out
        try:
            found = finder(strategy=strategy)
        except Exception:
            return out
        rot_matrix = page.rotation_matrix if page.rotation else None
        for table in getattr(found, "tables", []) or []:
            bbox = _rotate_bbox(table.bbox, rot_matrix)
            try:
                grid = table.extract()
            except Exception:
                grid = []
            cells: List[Cell] = []
            rows = getattr(table, "rows", []) or []
            for r_idx, row in enumerate(rows):
                row_cells = getattr(row, "cells", []) or []
                for c_idx, cell_bbox in enumerate(row_cells):
                    text = ""
                    if r_idx < len(grid) and c_idx < len(grid[r_idx]):
                        text = (grid[r_idx][c_idx] or "").strip()
                    cells.append(Cell(
                        row=r_idx,
                        col=c_idx,
                        text=text,
                        bbox=_rotate_bbox(cell_bbox, rot_matrix) if cell_bbox else None,
                    ))
            if not cells:
                for r_idx, line in enumerate(grid or []):
                    for c_idx, value in enumerate(line or []):
                        cells.append(Cell(row=r_idx, col=c_idx, text=(value or "").strip()))
            n_rows = max((c.row for c in cells), default=-1) + 1
            n_cols = max((c.col for c in cells), default=-1) + 1
            non_empty = sum(1 for c in cells if c.text.strip())
            out.append({"bbox": bbox, "cells": cells, "rows": n_rows,
                        "cols": n_cols, "non_empty": non_empty, "strategy": strategy})
        return out

    def _table_blocks(self, page, start_order: int, min_cells: int, min_fill: float = 0.2
                      ) -> Tuple[List[Block], List[BBox], Dict[str, int]]:
        """双策略表格检测。

        lines 策略对有线框的表格最准；text 策略能识别券商研报里最常见的无框财务预测表。
        默认只用 lines：text 策略在图表密集的页面上会把图表区并成巨型网格，
        破坏段落结构；需要抽取无框财务预测表时把 table_strategy 设为 hybrid 或 text。
        """
        line_tables = self._collect_tables(page, "lines", min_cells)
        text_tables = self._collect_tables(page, "text", min_cells)
        stats = {"tables_filtered": 0, "tables_from_lines": 0, "tables_from_text": 0,
                 "text_tables_skipped": 0}

        def usable(item, min_rows):
            return (item["rows"] >= min_rows and item["cols"] >= 2
                    and len(item["cells"]) >= min_cells and item["non_empty"] >= 4
                    and item["non_empty"] / max(len(item["cells"]), 1) >= min_fill)

        def looks_like_financial_table(item):
            """无框财务表判据：行数多、列数适中、格子短且以数字为主。"""
            values = [c.text.strip() for c in item["cells"] if c.text.strip()]
            if not values:
                return False
            numeric = sum(1 for v in values if any(ch.isdigit() for ch in v)) / len(values)
            avg_len = sum(len(v) for v in values) / len(values)
            fill = item["non_empty"] / max(len(item["cells"]), 1)
            return (item["rows"] >= 6 and 3 <= item["cols"] <= 12
                    and item["non_empty"] >= 24 and fill >= 0.45
                    and numeric >= 0.35 and avg_len <= 16.0)

        strategy = str(self.params.get("table_strategy", "lines")).lower()
        chosen = [t for t in line_tables if usable(t, 2)]
        stats["tables_from_lines"] = len(chosen)

        for item in text_tables:
            if not usable(item, 3) or not looks_like_financial_table(item):
                continue
            covered = any(_overlap_ratio(item["bbox"], kept["bbox"]) > 0.5
                          for kept in chosen if kept["bbox"])
            if covered:
                continue
            if strategy in {"hybrid", "text"}:
                chosen.append(item)
                stats["tables_from_text"] += 1
            else:
                # 默认策略下仅记录信号：本页存在疑似无框表格，需要时用 hybrid 重新解析
                stats["text_tables_skipped"] += 1

        stats["tables_filtered"] = max(
            len(line_tables) + len(text_tables) - len(chosen), 0)

        tables: List[Block] = []
        boxes: List[BBox] = []
        for idx, item in enumerate(chosen):
            boxes.append(item["bbox"])
            tables.append(Block(
                block_id=f"p{page.number + 1}_tb{idx}",
                type="table",
                bbox=item["bbox"],
                order=start_order + idx,
                cells=item["cells"],
                source_note=f"detect:{item['strategy']}",
            ))
        return tables, boxes, stats

    def parse(self, path: Path, doc_id: str, ledger=None) -> List[Page]:
        if fitz is None:  # pragma: no cover
            raise EngineUnavailable(self.install_hint)
        margin = float(self.params.get("furniture_margin_pt", 40.0))
        min_cells = int(self.params.get("table_min_cells", 4))
        # 图表线条也会被线框检测当成表格：实测有 45×35 的网格只有 31 个非空单元格，
        # 用填充率兜住这类伪表格；真实财务表的填充率通常在 0.3 以上。
        min_fill = float(self.params.get("table_min_fill_ratio", 0.2))
        pages: List[Page] = []
        with fitz.open(str(path)) as doc:
            for page in doc:
                text_blocks = self._text_blocks(page, margin)
                page_h = page.rect.height
                for block in text_blocks:
                    if is_furniture(block, page_h, margin):
                        block.level = "furniture"
                table_blocks, table_boxes, table_stats = self._table_blocks(
                    page, start_order=1000, min_cells=min_cells, min_fill=min_fill)

                kept: List[Block] = [
                    b for b in text_blocks
                    if not any(_overlap_ratio(b.bbox, tb) > 0.8 for tb in table_boxes if b.bbox)
                ]
                merged = kept + table_blocks
                captions_attached = attach_captions_and_sources(merged)
                # 默认用分带方案：评估显示三种排序在真实研报上的差异很小（见 tools/eval_reading_order.py），
                # 分带方案只在明确的双栏页面上改变顺序，风险最低；需要更激进的效果可切 xycut。
                merged = order_blocks(merged, page.rect.width, page.rect.height,
                                      mode=str(self.params.get("reading_order", "band")))
                columns = column_count(merged, page.rect.width)
                switches = column_switch_count(merged, page.rect.width)

                pages.append(Page(
                    page=page.number + 1,
                    page_size=(page.rect.width, page.rect.height),
                    blocks=merged,
                    engine_stats={
                        "tables_filtered": table_stats.get("tables_filtered", 0),
                        "tables_from_lines": table_stats.get("tables_from_lines", 0),
                        "tables_from_text": table_stats.get("tables_from_text", 0),
                        "text_tables_skipped": table_stats.get("text_tables_skipped", 0),
                        "columns": columns,
                        "column_switches": switches,
                        "rotation": page.rotation,
                        "captions_attached": captions_attached,
                        "dropped_inside_table": len(text_blocks) - len(kept),
                    },
                ))
                if ledger is not None:
                    ledger.compute(
                        op="engine_page_extract",
                        page=page.number + 1,
                        inputs={"engine": self.name, "path": str(path),
                                "rotation": page.rotation},
                        result={
                            "blocks": len(merged),
                            "tables": len(table_blocks),
                            "tables_filtered": table_stats.get("tables_filtered", 0),
                            "tables_from_text": table_stats.get("tables_from_text", 0),
                            "columns": columns,
                            "dropped_inside_table": len(text_blocks) - len(kept),
                        },
                        decision="extracted",
                    )
        return pages

    def page_texts(self, path: Path) -> Dict[int, str]:
        if fitz is None:  # pragma: no cover
            raise EngineUnavailable(self.install_hint)
        texts: Dict[int, str] = {}
        with fitz.open(str(path)) as doc:
            for page in doc:
                texts[page.number + 1] = page.get_text("text")
        return texts
