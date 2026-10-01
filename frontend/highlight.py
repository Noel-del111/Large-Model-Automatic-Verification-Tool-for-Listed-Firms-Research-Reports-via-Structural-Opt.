# -*- coding: utf-8 -*-
"""D 展示层：把 finding 的定位（文件、页码、bbox）渲染成带高亮框的页面图。

坐标约定沿用 B：左上角原点、单位为点；渲染缩放 = dpi / 72。
bbox 缺失或无效时返回 None，由调用方退化为「页码 + 原文摘录」展示。
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Iterable

import fitz  # PyMuPDF
from PIL import Image, ImageDraw

# 研报声明位置用红色，财报依据位置用蓝色（与解析模块预览图的口径区分开）。
CLAIM_COLOR = (214, 69, 56)
SOURCE_COLOR = (37, 99, 235)

_BOX_KEYS = ("bbox",)


def _scale_box(box: list[float], page_rect, pix_width: int, pix_height: int) -> tuple[float, float, float, float]:
    sx = pix_width / float(page_rect.width)
    sy = pix_height / float(page_rect.height)
    x0 = max(0.0, float(box[0]) * sx)
    y0 = max(0.0, float(box[1]) * sy)
    x1 = min(float(pix_width), float(box[2]) * sx)
    y1 = min(float(pix_height), float(box[3]) * sy)
    return x0, y0, x1, y1


def render_location_image(pdf_path: str | Path, page_no: int,
                          boxes: Iterable[tuple[list[float], tuple[int, int, int]]],
                          dpi: int = 110) -> bytes | None:
    """渲染原 PDF 的指定页，并画出高亮框。

    :param boxes: [(bbox, color), ...]，bbox 为点单位四元组 [x0, y0, x1, y1]
    :return: PNG 字节；文件不可读或页码越界时返回 None
    """
    path = Path(pdf_path)
    if not path.is_file() or page_no is None or page_no < 1:
        return None
    try:
        document = fitz.open(str(path))
        page = document.load_page(int(page_no) - 1)
    except Exception:
        return None
    pix = page.get_pixmap(dpi=dpi)
    image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    draw = ImageDraw.Draw(image)
    drawn = 0
    for box, color in boxes:
        if box is None or len(box) != 4:
            continue
        try:
            scaled = _scale_box(list(box), page.rect, pix.width, pix.height)
        except (TypeError, ValueError):
            continue
        if scaled[2] - scaled[0] < 3 or scaled[3] - scaled[1] < 3:
            continue
        # 双线描边，浅色页面上更醒目。
        draw.rectangle(scaled, outline=(255, 255, 255), width=5)
        draw.rectangle(scaled, outline=color, width=2)
        drawn += 1
    document.close()
    if drawn == 0:
        return None
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()