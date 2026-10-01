"""MinerU 输出适配器（可选主引擎）。

接入方式刻意设计成“先离线跑 MinerU，再读取它的结构化输出”，原因有两点：
  1. MinerU 的模型体量大、版本迭代快，把它的运行方式固化进本模块会带来额外耦合；
  2. 比赛要求封闭环境可复现，离线产物比现场下载模型更可控。

支持读取 MinerU 的 content_list 结构（每项含 page_idx、type、bbox、text 等字段）。
状态说明：当前机器未安装 MinerU，本适配器尚未做端到端验证。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..contract import BBox, Block, Cell, Page
from .base import BaseEngine, EngineCapabilities, EngineUnavailable, register

VERIFIED = False

TYPE_MAP = {
    "text": "text",
    "title": "heading",
    "table": "table",
    "image": "image",
    "figure": "image",
    "equation": "formula",
    "formula": "formula",
    "caption": "caption",
    "table_caption": "caption",
    "image_caption": "caption",
}

_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")


def html_table_to_cells(html: str) -> List[Cell]:
    """把 MinerU 输出的 HTML 表格转成不带坐标的单元格矩阵。"""
    cells: List[Cell] = []
    if not html:
        return cells
    for r_idx, row_html in enumerate(_ROW_RE.findall(html)):
        for c_idx, cell_html in enumerate(_CELL_RE.findall(row_html)):
            text = _TAG_RE.sub("", cell_html)
            text = text.replace("&nbsp;", " ").replace("&amp;", "&").strip()
            cells.append(Cell(row=r_idx, col=c_idx, text=text))
    return cells


@register
class MinerUOutputEngine(BaseEngine):
    name = "mineru"
    install_hint = (
        "本适配器读取 MinerU 的离线输出。请先按官方文档在离线环境运行 MinerU，"
        "再用 --params output=<content_list.json 路径> 指定产物。"
    )

    def version(self) -> str:
        return str(self.params.get("mineru_version", "unknown"))

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            coordinates=True,
            tables=True,
            reading_order=True,
            scanned_ocr=True,
            offline=True,
            notes=[
                "读取 MinerU 离线产物，不联网",
                "表格单元格来自 HTML 结构，无单元格坐标",
                f"本机未验证（VERIFIED={VERIFIED}）",
            ],
        )

    @classmethod
    def available(cls) -> bool:
        return True  # 仅依赖标准库，是否可用取决于是否提供了输出文件

    def _load(self, source: Path) -> List[Dict[str, Any]]:
        target = source
        if source.is_dir():
            candidates = sorted(source.glob("*content_list*.json"))
            if not candidates:
                raise EngineUnavailable(f"目录中未找到 content_list 产物：{source}")
            target = candidates[0]
        with open(target, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        if isinstance(payload, dict):
            payload = payload.get("content_list") or payload.get("items") or []
        return list(payload)

    def parse(self, path: Path, doc_id: str, ledger=None) -> List[Page]:
        output = self.params.get("output") or self.params.get("mineru_output")
        if not output:
            raise EngineUnavailable(self.install_hint)
        items = self._load(Path(output))
        buckets: Dict[int, List[Block]] = {}
        extents: Dict[int, Tuple[float, float]] = {}
        for item in items:
            page_no = int(item.get("page_idx", 0)) + 1
            bbox_raw = item.get("bbox") or item.get("poly")
            bbox = None
            if bbox_raw and len(bbox_raw) >= 4:
                xs = [float(bbox_raw[i]) for i in range(0, len(bbox_raw), 2)]
                ys = [float(bbox_raw[i]) for i in range(1, len(bbox_raw), 2)]
                bbox = BBox(min(xs), min(ys), max(xs), max(ys))
            kind = TYPE_MAP.get(str(item.get("type", "text")).lower(), "text")
            text = item.get("text") or item.get("content") or ""
            block = Block(
                block_id=f"p{page_no}_{kind}{len(buckets.get(page_no, []))}",
                type=kind,
                bbox=bbox,
                order=len(buckets.get(page_no, [])),
                text=str(text).strip(),
                caption=str(item.get("table_caption") or item.get("img_caption") or "").strip()
                if isinstance(item.get("table_caption") or item.get("img_caption"), str)
                else "",
            )
            if kind == "table":
                block.cells = html_table_to_cells(item.get("table_body") or "")
            buckets.setdefault(page_no, []).append(block)
            if bbox:
                cur = extents.get(page_no, (0.0, 0.0))
                extents[page_no] = (max(cur[0], bbox.x1), max(cur[1], bbox.y1))

        pages: List[Page] = []
        for page_no in sorted(buckets):
            blocks = sorted(buckets[page_no],
                            key=lambda b: (round((b.bbox.y0 if b.bbox else 0) / 4.0),
                                           b.bbox.x0 if b.bbox else 0))
            for order, block in enumerate(blocks):
                block.order = order
            size = (extents.get(page_no, (0.0, 0.0))[0], extents.get(page_no, (0.0, 0.0))[1])
            notes = ["adapter_unverified_in_this_env"]
            if size[0] <= 0 or size[1] <= 0:
                notes.append("page_size_inferred_from_bbox")
            pages.append(Page(page=page_no, page_size=size, blocks=blocks, notes=notes))
            if ledger is not None:
                ledger.compute(op="engine_page_extract", page=page_no,
                               inputs={"engine": self.name, "output": str(output)},
                               result={"blocks": len(blocks)}, decision="extracted")
        return pages
