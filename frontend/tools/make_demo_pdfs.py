# -*- coding: utf-8 -*-
"""生成一对合成研报/财报 PDF，用于核查台端到端试跑。

预期结果：
- 归母净利润 1.50 亿元 vs 财报 1.55 亿元 → 已确认错误（数值）
- 营业收入 12.34 亿元 与财报一致 → 未发现问题
- 存货 9.69 亿元 财报无对应行 → 待人工确认
"""
from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "demo"

REPORT_LINES = [
    "示例公司(600000) 2025年半年度点评报告",
    "公司2025年上半年归母净利润为1.50亿元。",
    "2025年上半年营业收入为12.34亿元。",
    "期末存货为9.69亿元。",
]

SOURCE_LINES = [
    "示例公司股份有限公司2025年半年度报告",
    "合并利润表",
    "项目 2025年半年度",
    "单位：亿元",
    "归母净利润 1.55",
    "营业收入 12.34",
]


def _make_pdf(path: Path, lines: list[str]) -> None:
    cjk = fitz.Font("cjk")
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    page.insert_font(fontname="cjk0", fontbuffer=cjk.buffer)
    y = 80.0
    for line in lines:
        page.insert_text((72.0, y), line, fontsize=11, fontname="cjk0")
        y += 28.0
    document.save(str(path))
    document.close()


def make_all() -> dict[str, Path]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report = OUT_DIR / "示例研报_2025H1.pdf"
    source = OUT_DIR / "示例财报_2025H1.pdf"
    _make_pdf(report, REPORT_LINES)
    _make_pdf(source, SOURCE_LINES)
    return {"report": report, "source": source}


if __name__ == "__main__":
    for name, path in make_all().items():
        print(f"{name}: {path}")