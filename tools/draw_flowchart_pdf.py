# -*- coding: utf-8 -*-
"""生成《研报纠错助手 · 数据流程图》PDF（用 pymupdf 手绘，无第三方绘图库依赖）。"""
import math
import fitz

OUT = r"c:\Users\xiaoliu\Desktop\project\研报纠错助手_数据流程图.pdf"
FONT = "china-s"
PAGE_W, PAGE_H = 1400, 700

# 颜色
C_BORDER = (0.20, 0.24, 0.32)
C_TEXT = (0.10, 0.10, 0.12)
C_ARROW = (0.30, 0.35, 0.45)
C_INPUT = (0.94, 0.96, 0.98)    # 输入/数据 浅蓝灰
C_PROCESS = (1.0, 1.0, 1.0)     # 处理 白
C_OUTPUT = (0.88, 1.0, 0.93)    # 输出 浅绿
C_LANE = (0.97, 0.97, 0.99)     # 泳道底 浅灰

FONT_TITLE = 20
FONT_LANE = 13
FONT_NODE = 9


def draw_box(page, x, y, w, h, lines, fill=C_PROCESS, fontsize=FONT_NODE, border=C_BORDER):
    page.draw_rect(fitz.Rect(x, y, x + w, y + h), color=border, fill=fill, width=1.2)
    line_h = fontsize * 1.55
    total_h = len(lines) * line_h
    start_y = y + (h - total_h) / 2 + line_h * 0.32
    for i, line in enumerate(lines):
        tw = fitz.get_text_length(line, fontname=FONT, fontsize=fontsize)
        tx = x + (w - tw) / 2
        page.insert_text(fitz.Point(tx, start_y + i * line_h), line,
                         fontsize=fontsize, fontname=FONT, color=C_TEXT)


def arrow(page, p1, p2, color=C_ARROW):
    page.draw_line(p1, p2, color=color, width=1.4)
    ang = math.atan2(p2.y - p1.y, p2.x - p1.x)
    size = 7
    a1 = fitz.Point(p2.x - size * math.cos(ang - 0.45), p2.y - size * math.sin(ang - 0.45))
    a2 = fitz.Point(p2.x - size * math.cos(ang + 0.45), p2.y - size * math.sin(ang + 0.45))
    shape = page.new_shape()
    shape.draw_line(p2, a1)
    shape.draw_line(p2, a2)
    shape.finish(color=color, width=1.4)
    shape.commit()


def lane(page, title, y, nodes, node_w=148, node_h=120, top_gap=34):
    """画一个泳道：标题 + 一行横向节点（节点之间箭头相连）。"""
    page.draw_rect(fitz.Rect(40, y - 8, PAGE_W - 40, y + node_h + 46), color=C_LANE, fill=C_LANE, width=0)
    page.insert_text(fitz.Point(52, y + 6), title, fontsize=FONT_LANE, fontname=FONT, color=(0.05, 0.25, 0.35))
    total_w = len(nodes) * node_w + (len(nodes) - 1) * 30
    x0 = (PAGE_W - total_w) / 2
    node_y = y + top_gap
    centers = []
    for i, node in enumerate(nodes):
        x = x0 + i * (node_w + 30)
        fill = C_INPUT if i == 0 else C_OUTPUT if i == len(nodes) - 1 else C_PROCESS
        draw_box(page, x, node_y, node_w, node_h, node["lines"], fill=fill)
        centers.append((x + node_w / 2, node_y + node_h / 2))
    for i in range(len(centers) - 1):
        p1 = fitz.Point(centers[i][0] + node_w / 2, centers[i][1])
        p2 = fitz.Point(centers[i + 1][0] - node_w / 2, centers[i + 1][1])
        arrow(page, p1, p2)
    return node_y + node_h + 46


def main():
    doc = fitz.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)

    # 标题
    title = "研报纠错助手 · 数据流程图（第二版）"
    tw = fitz.get_text_length(title, fontname=FONT, fontsize=FONT_TITLE)
    page.insert_text(fitz.Point((PAGE_W - tw) / 2, 46), title, fontsize=FONT_TITLE, fontname=FONT, color=C_TEXT)
    sub = "两条检测主线（配对核查 / 独立文本检测）+ 一条评测主线（FinED-Bench 442 篇研报）"
    tw = fitz.get_text_length(sub, fontname=FONT, fontsize=10)
    page.insert_text(fitz.Point((PAGE_W - tw) / 2, 70), sub, fontsize=10, fontname=FONT, color=(0.35, 0.38, 0.45))

    y = 96
    y = lane(page, "① 配对核查流程（研报 × 财报）", y, [
        {"lines": ["研报PDF/DOCX", "+ 财报/公告PDF"]},
        {"lines": ["pdfparse(B)", "多引擎解析", "→ Document"]},
        {"lines": ["claim_extract", "研报声明", "source_extract", "财报事实"]},
        {"lines": ["rules.check_facts", "+ intrinsic", "→ 三态findings"]},
        {"lines": ["evidence_requests", "补证请求(ask/proceed)"]},
        {"lines": ["text_review", "独立文本候选"]},
        {"lines": ["write_result", "check_result/csv/md"]},
        {"lines": ["frontend展示", "人工复核", "review.json"]},
    ], node_w=140, node_h=128)

    y = lane(page, "② 独立文本检测流程（单份文本）", y, [
        {"lines": ["TXT/Markdown", "/原文"]},
        {"lines": ["detect_text", "+ few-shot示例"]},
        {"lines": ["模型调用", "BudgetedChatClient", "→ 候选"]},
        {"lines": ["_anchor锚点", "+ 8类确定性规则", "+ cross_period"]},
        {"lines": ["_verified_candidate", "确认 + _deduplicate"]},
        {"lines": ["text-review/1.0", "errors/coverage/traces", "rejected_candidates"]},
    ], node_w=160, node_h=128)

    y = lane(page, "③ 评测流程（FinED-Bench 442 篇研报）", y, [
        {"lines": ["FinED-Bench", "原始数据997篇"]},
        {"lines": ["dataset_prepare", "→ inputs/gold", "manifest(dev/eval/holdout)"]},
        {"lines": ["run_v2 run", "→ predictions", "三组detector"]},
        {"lines": ["run_v2 score", "fined_bench_eval", "评分(TP/FP/FN)"]},
        {"lines": ["报告生成", "aggregate/REPORT"]},
    ], node_w=180, node_h=128)

    # 底部图例
    legend_y = y + 10
    page.insert_text(fitz.Point(60, legend_y + 14), "图例：", fontsize=11, fontname=FONT, color=C_TEXT)
    draw_box(page, 110, legend_y, 18, 18, [], fill=C_INPUT)
    page.insert_text(fitz.Point(134, legend_y + 14), "输入/数据", fontsize=10, fontname=FONT, color=C_TEXT)
    draw_box(page, 230, legend_y, 18, 18, [], fill=C_PROCESS)
    page.insert_text(fitz.Point(254, legend_y + 14), "处理模块", fontsize=10, fontname=FONT, color=C_TEXT)
    draw_box(page, 350, legend_y, 18, 18, [], fill=C_OUTPUT)
    page.insert_text(fitz.Point(374, legend_y + 14), "输出/结果", fontsize=10, fontname=FONT, color=C_TEXT)
    page.insert_text(fitz.Point(500, legend_y + 14),
                     "三态 findings：confirmed_error / needs_review / no_issue", fontsize=10, fontname=FONT, color=(0.4, 0.42, 0.48))

    doc.save(OUT)
    print("saved:", OUT, "pages:", doc.page_count)


if __name__ == "__main__":
    main()
