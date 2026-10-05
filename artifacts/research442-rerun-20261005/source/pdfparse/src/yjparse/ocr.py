"""OCR 支持：处理扫描件与位图图表里的文字。

使用 RapidOCR（ONNX，CPU 可跑，离线），只在需要时触发：
  - 整页没有文本层（扫描件）；
  - 页面存在大面积位图，可能包含图表文字。
识别结果按页面坐标映射回 PDF 坐标系，作为普通文本块进入同一份契约。
"""

from __future__ import annotations

import logging
import os
from threading import RLock
from dataclasses import dataclass
from typing import Any, List, Optional, Sequence, Tuple

from .contract import BBox, Block

_OCR_ENGINE = None
_OCR_LOCK = RLock()


class OcrUnavailable(RuntimeError):
    """OCR 依赖缺失。"""


@dataclass
class OcrLine:
    text: str
    bbox: BBox
    score: float


def ocr_available() -> bool:
    try:
        import rapidocr  # noqa: F401

        return True
    except Exception:
        return False


def ocr_version() -> str:
    try:
        import importlib.metadata as md

        return md.version("rapidocr")
    except Exception:
        return ""


class OcrRunner:
    """懒加载的 OCR 执行器，模型只在首次调用时初始化。"""

    def __init__(self, min_score: float = 0.5):
        self.min_score = float(min_score)
        self._engine: Any = None

    def _ensure(self):
        global _OCR_ENGINE
        if self._engine is None:
            try:
                from rapidocr import RapidOCR
            except Exception as exc:  # pragma: no cover - 依赖缺失
                raise OcrUnavailable(
                    "未安装 OCR 依赖，请执行：pip install rapidocr onnxruntime"
                ) from exc
            # RapidOCR 默认把加载信息打到 stdout，会污染命令行输出，这里压到只报错误
            logging.getLogger("RapidOCR").setLevel(logging.ERROR)
            # Reuse model sessions across documents and limit nested ONNX
            # thread pools; default all-core pools can overwhelm local runs.
            with _OCR_LOCK:
                if _OCR_ENGINE is None:
                    _OCR_ENGINE = RapidOCR(params={
                        "Global.log_level": "error",
                        "EngineConfig.onnxruntime.intra_op_num_threads": min(4, os.cpu_count() or 1),
                        "EngineConfig.onnxruntime.inter_op_num_threads": 1,
                    })
                self._engine = _OCR_ENGINE
        return self._engine

    def recognize_image(self, image, origin: Tuple[float, float] = (0.0, 0.0),
                        scale: float = 1.0) -> List[OcrLine]:
        """识别一张已渲染的页面图像。

        image 为 RGB 图像；origin/scale 把图像像素坐标换算回 PDF 坐标，
        即 pdf_x = origin_x + px / scale。
        """
        import numpy as np

        engine = self._ensure()
        with _OCR_LOCK:
            result = engine(np.asarray(image))
        boxes = getattr(result, "boxes", None)
        texts = getattr(result, "txts", None) or ()
        scores = getattr(result, "scores", None) or ()
        lines: List[OcrLine] = []
        for index, text in enumerate(texts):
            if boxes is None or index >= len(boxes):
                continue
            score = float(scores[index]) if index < len(scores) else 0.0
            if score < self.min_score or not str(text).strip():
                continue
            points = np.asarray(boxes[index], dtype=float).reshape(-1, 2)
            xs = [origin[0] + p[0] / scale for p in points]
            ys = [origin[1] + p[1] / scale for p in points]
            lines.append(OcrLine(text=str(text).strip(),
                                 bbox=BBox(min(xs), min(ys), max(xs), max(ys)),
                                 score=score))
        lines.sort(key=lambda line: (round(line.bbox.y0 / 4.0), line.bbox.x0))
        return lines


def render_page_image(pdf_path, page_no: int, dpi: int = 200,
                      region: Optional[Sequence[float]] = None):
    """渲染整页或页面局部，返回（PIL 图像, 原点, 缩放比）。"""
    try:
        import pymupdf as fitz  # type: ignore
    except Exception:  # pragma: no cover
        import fitz  # type: ignore
    from PIL import Image

    with fitz.open(str(pdf_path)) as doc:
        page = doc[page_no - 1]
        scale = dpi / 72.0
        clip = None
        origin = (0.0, 0.0)
        if region:
            clip = fitz.Rect(*region)
            origin = (float(region[0]), float(region[1]))
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False)
        image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    return image, origin, scale


def lines_to_blocks(lines: Sequence[OcrLine], page_no: int, start_order: int = 5000,
                    source: str = "ocr:rapidocr", page_w: float = 0.0,
                    page_h: float = 0.0) -> List[Block]:
    """Preserve OCR line boundaries and only group vertically aligned lines."""
    blocks: List[Block] = []
    current: List[OcrLine] = []

    def flush() -> None:
        if not current:
            return
        text = "\n".join(line.text for line in current)
        bbox = BBox(min(l.bbox.x0 for l in current), min(l.bbox.y0 for l in current),
                    max(l.bbox.x1 for l in current), max(l.bbox.y1 for l in current))
        score = sum(l.score for l in current) / len(current)
        blocks.append(Block(
            block_id=f"p{page_no}_ocr{len(blocks)}",
            type="text",
            bbox=bbox,
            order=start_order + len(blocks),
            text=text,
            level="ocr",
            source_note=source,
            confidence=round(score, 4),
        ))
        current.clear()

    for line in sorted(lines, key=lambda item: (item.bbox.y0, item.bbox.x0)):
        if current:
            gap = line.bbox.y0 - current[-1].bbox.y1
            height = max(current[-1].bbox.height, 1.0)
            previous = current[-1].bbox
            overlap = min(previous.x1, line.bbox.x1) - max(previous.x0, line.bbox.x0)
            aligned = overlap / max(min(previous.width, line.bbox.width), 1.0) >= 0.5
            if gap < -height * 0.2 or gap > height * 0.8 or not aligned:
                flush()
        current.append(line)
    flush()
    if page_w and page_h:
        for block in blocks:
            block.bbox = BBox(max(0.0, min(block.bbox.x0, page_w)),
                              max(0.0, min(block.bbox.y0, page_h)),
                              max(0.0, min(block.bbox.x1, page_w)),
                              max(0.0, min(block.bbox.y1, page_h)))
    return blocks
