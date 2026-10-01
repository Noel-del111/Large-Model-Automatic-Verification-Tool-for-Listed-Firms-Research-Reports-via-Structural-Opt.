"""把解析结果的坐标画回页面图像。

用途有两个：一是人工核对解析质量（哪里漏了、哪里框错了），
二是作为现场演示的素材，直接证明每个结论都能回到原文的具体位置。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .utils import ensure_dir

try:  # PyMuPDF 1.24 起推荐 import pymupdf
    import pymupdf as _fitz  # type: ignore
except Exception:  # pragma: no cover
    try:
        import fitz as _fitz  # type: ignore
    except Exception:
        _fitz = None

BLOCK_COLORS: Dict[str, tuple] = {
    "text": (214, 48, 49),
    "heading": (155, 40, 176),
    "table": (39, 96, 207),
    "image": (120, 120, 120),
    "caption": (0, 148, 96),
    "formula": (176, 96, 0),
    "other": (96, 96, 96),
}
FURNITURE_COLOR = (240, 150, 0)


def render_preview(parse_result_path: Path, out_dir: Optional[Path] = None,
                   pages: Optional[Iterable[int]] = None, dpi: int = 110,
                   draw_ids: bool = False,
                   only_blocks: Optional[Iterable[str]] = None) -> List[Path]:
    if _fitz is None:  # pragma: no cover
        raise RuntimeError("预览需要 PyMuPDF，请先安装依赖：pip install PyMuPDF")
    from PIL import Image, ImageDraw

    parse_result_path = Path(parse_result_path)
    payload = json.loads(parse_result_path.read_text(encoding="utf-8"))
    pdf_path = Path(payload["doc"]["source_path"])
    if not pdf_path.exists():
        raise FileNotFoundError(f"原始 PDF 不在本地，无法生成预览：{pdf_path}")
    wanted = set(int(p) for p in pages) if pages else None
    block_filter = {str(b) for b in only_blocks} if only_blocks else None
    target_dir = ensure_dir(out_dir or (parse_result_path.parent / "preview"))
    written: List[Path] = []

    with _fitz.open(str(pdf_path)) as doc:
        for page in payload["pages"]:
            page_no = int(page["page"])
            if wanted and page_no not in wanted:
                continue
            source = doc[page_no - 1]
            pix = source.get_pixmap(dpi=dpi)
            image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            draw = ImageDraw.Draw(image)
            width, height = page["page_size"]
            scale_x = pix.width / max(width, 1.0)
            scale_y = pix.height / max(height, 1.0)

            for block in page["blocks"]:
                if block_filter is not None and block["block_id"] not in block_filter:
                    continue
                bbox = block.get("bbox")
                if not bbox:
                    continue
                color = (FURNITURE_COLOR if block.get("level") == "furniture"
                         else BLOCK_COLORS.get(block["type"], (96, 96, 96)))
                rect = [bbox[0] * scale_x, bbox[1] * scale_y,
                        bbox[2] * scale_x, bbox[3] * scale_y]
                draw.rectangle(rect, outline=color,
                               width=2 if block["type"] in {"table", "image"} else 1)
                if block["type"] == "table" and block.get("cells"):
                    for cell in block["cells"]:
                        if cell.get("bbox"):
                            cb = cell["bbox"]
                            draw.rectangle(
                                [cb[0] * scale_x, cb[1] * scale_y,
                                 cb[2] * scale_x, cb[3] * scale_y],
                                outline=(120, 170, 240))
                if draw_ids:
                    draw.text((rect[0] + 2, rect[1] + 1), block["block_id"], fill=color)

            header = (f"page {page_no}  status={page['status']}  "
                      f"blocks={len(page['blocks'])}  chars={page['quality']['char_count']}")
            draw.rectangle([0, 0, 8 * len(header), 16], fill=(255, 255, 255))
            draw.text((4, 3), header, fill=(0, 0, 0))

            path = target_dir / f"page-{page_no:03d}.png"
            image.save(path)
            written.append(path)
    return written


def render_for_doc(out_dir: Path, doc_id: Optional[str] = None,
                   pages: Optional[Iterable[int]] = None, dpi: int = 110,
                   draw_ids: bool = False,
                   only_blocks: Optional[Iterable[str]] = None) -> List[Path]:
    out_dir = Path(out_dir)
    written: List[Path] = []
    for result_path in sorted(out_dir.glob("*/parse_result.json")):
        if doc_id and result_path.parent.name != doc_id:
            continue
        written.extend(render_preview(result_path, pages=pages, dpi=dpi, draw_ids=draw_ids,
                                      only_blocks=only_blocks))
    return written
