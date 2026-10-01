# -*- coding: utf-8 -*-
"""D 展示层：导出。复核状态并入 CSV / Markdown / JSON，PDF 采用内置 CJK 字体排版。

本模块不依赖 yjcheck，只消费 check_result 的 dict 与 ReviewStore 的记录，
便于独立测试；PDF 导出依赖 PyMuPDF，缺失时返回 None 由界面降级提示。
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

STATUS_LABELS = {"confirmed_error": "已确认错误", "needs_review": "待人工确认", "no_issue": "未发现问题"}
ERROR_TYPE_LABELS = {
    "number": "数值", "unit": "单位", "period": "期间", "basis": "调整前后口径",
    "scope": "归属范围", "citation": "引用", "input_quality": "输入质量", "coverage": "覆盖范围",
    # 研报自身一致性检查（学习自 FinED-Bench）
    "numeric_inconsistency": "数值不一致", "time_conflict": "时间矛盾", "unit_term_mismatch": "单位-术语不匹配",
    # FinED-Bench 十五类预留（评测/人工标注归类用）
    "calc_error": "计算错误", "numeric_missing": "数值缺失", "redundant_statement": "冗余语句",
    "invalid_time": "时间信息非法", "term_misuse": "术语误用", "semantic_contradiction": "语义逻辑矛盾",
    "financial_element_missing": "金融要素缺失", "attribute_missing": "属性值缺失", "format_error": "格式错误",
    "": "—",
}
REVIEW_LABELS = {"unreviewed": "未复核", "confirmed": "确认", "dismissed": "驳回（误报）", "contested": "存疑"}

_HEADER = ["状态", "错误类型", "公司", "指标", "研报原文", "声明值", "期间", "建议值", "建议", "依据位置", "规则", "复核状态", "复核人", "复核备注"]


def _guard(value: str) -> str:
    """防止 Excel 把研报原文当作公式执行（与 C 的 findings.csv 同口径）。"""
    text = str(value)
    return "'" + text if text.startswith(("=", "+", "-", "@")) else text


def _location_text(facts: list[dict]) -> str:
    locations = []
    for fact in facts:
        for evidence in fact.get("evidence", []):
            file_name = Path(str(evidence.get("file", ""))).name or evidence.get("doc_id", "")
            if evidence.get("page") is not None:
                locations.append(f"{file_name} 第{evidence['page']}页")
            elif evidence.get("paragraph") is not None:
                locations.append(f"{file_name} 第{evidence['paragraph']}段")
            else:
                locations.append(file_name)
    return "；".join(dict.fromkeys(locations))


def _claim_cell(claim: dict) -> str:
    value = claim.get("value", "")
    unit = claim.get("unit", "")
    return f"{value}{unit}".strip()


def findings_rows(result: dict, reviews: dict[str, dict]) -> list[list[str]]:
    rows = []
    for finding in result.get("findings", []):
        claim = finding.get("claim", {})
        review = reviews.get(finding.get("id", ""), {})
        rows.append([
            STATUS_LABELS.get(finding.get("status", ""), finding.get("status", "")),
            ERROR_TYPE_LABELS.get(finding.get("error_type", ""), finding.get("error_type", "")),
            claim.get("company", ""),
            claim.get("metric", ""),
            claim.get("text", ""),
            _claim_cell(claim),
            claim.get("period", ""),
            finding.get("suggested_value") or "",
            finding.get("suggestion", ""),
            _location_text(finding.get("evidence", [])),
            finding.get("rule_id", ""),
            REVIEW_LABELS.get(review.get("status", "unreviewed"), "未复核"),
            review.get("reviewer", ""),
            review.get("note", ""),
        ])
    return rows


def findings_csv_bytes(result: dict, reviews: dict[str, dict]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(_HEADER)
    for row in findings_rows(result, reviews):
        writer.writerow([_guard(cell) for cell in row])
    return buffer.getvalue().encode("utf-8-sig")


def report_md(result: dict, reviews: dict[str, dict]) -> str:
    summary = result.get("summary", {})
    lines = [
        "# 研报核查结果（含人工复核）",
        "",
        "仅覆盖已提取的受支持事实，不表示已审查文章全部论断。",
        "",
        f"已确认错误 {summary.get('confirmed_error', 0)}；待人工确认 {summary.get('needs_review', 0)}；"
        f"未发现问题 {summary.get('no_issue', 0)}。",
        "",
        "| " + " | ".join(_HEADER) + " |",
        "|" + "---|" * len(_HEADER),
    ]
    for row in findings_rows(result, reviews):
        lines.append("| " + " | ".join(_guard(str(cell)).replace("\n", " ") for cell in row) + " |")
    reviewed = sum(1 for entry in reviews.values() if entry.get("status") != "unreviewed")
    lines += ["", f"复核进度：{reviewed}/{len(reviews)}（复核状态由 D 维护，写入 review.json，不改写核查产物）"]
    if result.get("input_issues"):
        lines += ["", "## 输入质量问题", ""]
        for item in result["input_issues"]:
            lines.append(f"- {Path(str(item.get('file',''))).name}: {'; '.join(item.get('issues', []))}")
    return "\n".join(lines) + "\n"


def full_json_bytes(result: dict, reviews: dict[str, dict]) -> bytes:
    merged = dict(result)
    merged["review"] = reviews
    return json.dumps(merged, ensure_ascii=False, indent=2).encode("utf-8")


def _pdf_lines(result: dict, reviews: dict[str, dict]) -> list[str]:
    lines = ["研报核查结果（含人工复核）", ""]
    summary = result.get("summary", {})
    lines.append(f"已确认错误 {summary.get('confirmed_error', 0)}　待人工确认 {summary.get('needs_review', 0)}　"
                 f"未发现问题 {summary.get('no_issue', 0)}")
    lines.append("")
    for row in findings_rows(result, reviews):
        lines.append(f"【{row[0]}】{row[2]} {row[3]}　{row[4][:80]}")
        if row[7]:
            lines.append(f"  建议值：{row[7]}　规则：{row[10]}")
        lines.append(f"  依据：{row[9][:100]}　复核：{row[11]}")
        lines.append("")
    lines.append(f"复核进度：{sum(1 for e in reviews.values() if e.get('status') != 'unreviewed')}/{len(reviews)}")
    lines.append("声明：仅覆盖已提取的受支持事实，不表示已审查文章全部论断。")
    return lines


def report_pdf_bytes(result: dict, reviews: dict[str, dict]) -> bytes | None:
    """用 PyMuPDF 内置 CJK 字体排版导出 PDF；环境缺 fitz 或字体注册失败时返回 None。"""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return None
    try:
        cjk = fitz.Font("cjk")
        document = fitz.open()

        def new_pdf_page():
            page = document.new_page(width=595, height=842)
            # 内置 CJK 字体按页注册缓冲区，供后续 insert_textbox 引用。
            page.insert_font(fontname="cjk0", fontbuffer=cjk.buffer)
            return page

        page = new_pdf_page()
        y = 50.0
        for line in _pdf_lines(result, reviews):
            if y > 790:
                page = new_pdf_page()
                y = 50.0
            text = line or " "
            page.insert_textbox(fitz.Rect(50, y, 545, y + 40), text,
                                fontsize=10, fontname="cjk0")
            y += 16 if line else 10
        buffer = io.BytesIO()
        document.save(buffer)
        document.close()
        return buffer.getvalue()
    except Exception:
        return None