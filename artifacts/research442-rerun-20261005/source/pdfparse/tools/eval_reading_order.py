"""阅读顺序评估：对比三种排序方案，供选型与回归使用。

三种排序：
  naive  按坐标 (y, x) 排序
  band   分带 + 带内分栏（当前默认）
  xycut  递归投影切割

两个参照物：
  stream     PDF 内容流原始写入顺序（模板化文档通常就是逻辑顺序）
  reference  对照引擎 pdfplumber 的文本顺序
另有“同栏内向上回跳次数”，正确顺序不应在同一栏内向上回跳。

用法：
    py -3 tools/eval_reading_order.py data/real/*.pdf
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from yjparse.engines.pdfplumber_engine import PdfPlumberEngine  # noqa: E402
from yjparse.engines.pymupdf_engine import PyMuPDFEngine  # noqa: E402
from yjparse.layout import _center_x, _is_full_width, order_blocks  # noqa: E402
from yjparse.quality import sequence_agreement  # noqa: E402

MODES = ("naive", "band", "xycut")


def order_with(blocks, mode: str, width: float, height: float):
    if mode == "naive":
        ordered = sorted(blocks, key=lambda b: (round(b.bbox.y0 / 4.0), b.bbox.x0))
        for index, block in enumerate(ordered):
            block.order = index
        return ordered
    return order_blocks([b for b in blocks], width, height, mode=mode)


def page_text_in_order(blocks) -> str:
    parts = []
    for block in sorted(blocks, key=lambda b: b.order):
        if block.type == "table" and block.cells:
            parts.append(" ".join(c.text for c in block.cells if c.text))
        elif block.text:
            parts.append(block.text)
    return "\n".join(parts)


def backward_jumps(blocks, page_w: float, tolerance: float = 6.0) -> int:
    mid = page_w / 2.0
    sequence = []
    for block in sorted([b for b in blocks if b.bbox], key=lambda b: b.order):
        if _is_full_width(block, page_w):
            continue
        side = "L" if _center_x(block) < mid else "R"
        sequence.append((side, block.bbox.y0, block.bbox.y1))
    jumps = 0
    for (s1, y0a, y1a), (s2, y0b, _y1b) in zip(sequence, sequence[1:]):
        if s1 == s2 and y0b < y1a - tolerance and y0b < y0a:
            jumps += 1
    return jumps


def evaluate(pdf: pathlib.Path, use_reference: bool = True) -> None:
    import pymupdf

    pages = PyMuPDFEngine().parse(pdf, doc_id=pdf.stem)
    with pymupdf.open(str(pdf)) as doc:
        stream = {index + 1: page.get_text("text") for index, page in enumerate(doc)}
    reference = PdfPlumberEngine().page_texts(pdf) if use_reference else {}

    scores = {mode: {"stream": [], "reference": []} for mode in MODES}
    jumps = {mode: 0 for mode in MODES}
    used = 0
    for page in pages:
        blocks = [b for b in page.blocks if b.bbox]
        if len(blocks) < 4:
            continue
        width, height = page.page_size
        used += 1
        for mode in MODES:
            ordered = order_with(blocks, mode, width, height)
            text = page_text_in_order(ordered)
            jumps[mode] += backward_jumps(ordered, width)
            if len(stream.get(page.page, "")) >= 80:
                value = sequence_agreement(text, stream[page.page])
                if value is not None:
                    scores[mode]["stream"].append(value)
            if reference.get(page.page):
                value = sequence_agreement(text, reference[page.page])
                if value is not None:
                    scores[mode]["reference"].append(value)

    def mean(values):
        return sum(values) / len(values) if values else 0.0

    print(f"\n=== {pdf.name}（参与评估 {used} 页）")
    print(f"{'方案':<8}{'对内容流':>10}{'对对照引擎':>12}{'同栏向上回跳':>14}")
    for mode in MODES:
        print(f"{mode:<8}{mean(scores[mode]['stream']):>10.4f}"
              f"{mean(scores[mode]['reference']):>12.4f}{jumps[mode]:>14}")


def main() -> int:
    targets = [pathlib.Path(arg) for arg in sys.argv[1:]]
    if not targets:
        print(__doc__)
        return 1
    for pdf in targets:
        evaluate(pdf)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
