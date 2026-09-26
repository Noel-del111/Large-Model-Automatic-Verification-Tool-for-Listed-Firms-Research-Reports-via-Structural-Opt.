"""生成合成测试 PDF：一份带文本层与表格的研报样例，一份模拟扫描件。"""

from __future__ import annotations

from pathlib import Path

import pymupdf

PAGE_W, PAGE_H = 595.0, 842.0


def _line(page, x, y, text, size=10.5, font="china-s"):
    page.insert_text((x, y), text, fontsize=size, fontname=font)


def _table(page, x0, y0, col_w, row_h, rows):
    for r_idx, row in enumerate(rows):
        for c_idx, value in enumerate(row):
            rect = pymupdf.Rect(
                x0 + c_idx * col_w,
                y0 + r_idx * row_h,
                x0 + (c_idx + 1) * col_w,
                y0 + (r_idx + 1) * row_h,
            )
            page.draw_rect(rect, color=(0.2, 0.2, 0.2), width=0.6)
            page.insert_text((rect.x0 + 4, rect.y0 + 13), value,
                             fontsize=8.5, fontname="china-s")


def make_report_pdf(path: Path) -> Path:
    """两页样例：正文、页眉页脚、财务表格、来源标注。"""
    doc = pymupdf.open()

    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    _line(page, 72, 30, "示例证券研究报告 2025 年半年度", size=8)          # 页眉
    _line(page, 72, 96, "公司 2025 年上半年营业收入同比增长 18.6%", size=15)
    _line(page, 72, 130, "我们维持买入评级，目标价 42.0 元，对应 2026 年 28 倍市盈率。", size=10.5)
    _line(page, 72, 152, "报告发布日期：2025-08-28    分析师：示例", size=9)
    _line(page, 72, 190, "表 1 主要财务指标", size=10)
    _table(page, 72, 200, 110, 22, [
        ["指标", "2024A", "2025E", "2026E"],
        ["营业收入（百万元）", "8,420", "9,986", "11,800"],
        ["净利润（百万元）", "1,120", "1,360", "1,690"],
        ["毛利率（%）", "31.2", "32.0", "32.8"],
    ])
    _line(page, 72, 312, "资料来源：公司半年报，分析师测算", size=8.5)
    _line(page, 72, 350, "风险提示：下游需求不及预期、原材料价格波动、行业竞争加剧。", size=10.5)
    _line(page, 72, 810, "第 1 页", size=8)                                  # 页脚

    page2 = doc.new_page(width=PAGE_W, height=PAGE_H)
    _line(page2, 72, 30, "示例证券研究报告 2025 年半年度", size=8)
    _line(page2, 72, 96, "二、盈利预测与估值", size=13)
    _line(page2, 72, 128, "我们预计公司 2025 至 2027 年归母净利润复合增速约 22%。", size=10.5)
    _line(page2, 72, 152, "估值方面，当前股价对应 2025 年 24 倍市盈率，低于可比公司均值 29 倍。", size=10.5)
    _line(page2, 72, 810, "第 2 页", size=8)

    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    doc.close()
    return path


def make_scanned_pdf(path: Path) -> Path:
    """模拟扫描件：页面只有图形没有文本层，用于验证失败识别。"""
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    page.draw_rect(pymupdf.Rect(60, 60, 535, 300), color=None, fill=(0.85, 0.85, 0.85))
    page.draw_line(pymupdf.Point(60, 380), pymupdf.Point(535, 380), width=1)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    doc.close()
    return path


def make_all(target_dir: Path):
    target_dir = Path(target_dir)
    return {
        "report": make_report_pdf(target_dir / "样例研报_2025H1.pdf"),
        "scanned": make_scanned_pdf(target_dir / "扫描件样例.pdf"),
    }
