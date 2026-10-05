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
REQUEST_TYPE_LABELS = {"provide_source": "补充来源材料", "repair_input": "修复或重新核对输入",
                       "clarify_context": "人工澄清上下文/口径", "resolve_conflict": "裁定来源冲突"}
ROLE_LABELS = {"report": "研报", "source": "财报/公告"}
_REQUEST_FIELDS = {"doc_role", "field", "period", "company", "basis", "scope", "file", "request_type", "reason"}

_HEADER = ["状态", "错误类型", "公司", "指标", "研报原文", "声明值", "期间", "建议值", "建议", "依据位置", "规则",
           "补证状态", "补证/澄清条目数", "补充证据清单", "复核状态", "复核人", "复核备注"]


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


def evidence_request_contract(result: dict) -> dict:
    """Interpret R09 without mutating or silently completing legacy results."""
    version = result.get("schema_version")
    findings = result.get("findings", [])
    summary = result.get("summary", {})
    if not isinstance(findings, list) or not isinstance(summary, dict):
        return {"status": "invalid", "label": "补证字段不符合契约：发现或汇总结构错误",
                "finding_count": None, "item_count": None, "item_count_source": "invalid"}
    if any(not isinstance(finding, dict) for finding in findings):
        return {"status": "invalid", "label": "补证字段不符合契约：finding 必须为对象",
                "finding_count": None, "item_count": None, "item_count_source": "invalid"}
    present = [("decision" in finding, "evidence_request" in finding) for finding in findings]
    if version == "text-review/1.0":
        return {"status": "not_applicable", "label": "独立文本检测不适用配对补证契约",
                "finding_count": None, "item_count": None, "item_count_source": "not_applicable"}
    has_summary_fields = "evidence_requests" in summary or "evidence_request_items" in summary
    if version == "1.0.0" and not any(a or b for a, b in present) and not has_summary_fields:
        return {"status": "legacy_missing", "label": "旧版结果未提供补证信息",
                "finding_count": None, "item_count": None, "item_count_source": "not_provided"}
    problems = []
    if version == "1.1.0" and ("evidence_requests" not in summary or "evidence_request_items" not in summary):
        problems.append("1.1.0 汇总缺少补证计数")
    for key in ("evidence_requests", "evidence_request_items"):
        if key in summary and (type(summary[key]) is not int or summary[key] < 0):
            problems.append(f"{key} 必须为非负整数")
    ask_count = item_count = 0
    for index, finding in enumerate(findings, 1):
        has_decision, has_requests = present[index - 1]
        if has_decision != has_requests or not has_decision:
            problems.append(f"第 {index} 条 finding 补证字段不完整")
            continue
        decision, requests = finding.get("decision"), finding.get("evidence_request")
        if not isinstance(decision, str) or decision not in {"ask", "proceed"} or not isinstance(requests, list):
            problems.append(f"第 {index} 条 finding 补证字段类型错误")
            continue
        for request_index, request in enumerate(requests, 1):
            prefix = f"第 {index} 条 finding 的第 {request_index} 条请求"
            if not isinstance(request, dict) or not _REQUEST_FIELDS.issubset(request):
                problems.append(prefix + "缺少必需字段")
                continue
            if (not isinstance(request.get("doc_role"), str) or request["doc_role"] not in ROLE_LABELS
                    or not isinstance(request.get("request_type"), str)
                    or request["request_type"] not in REQUEST_TYPE_LABELS
                    or not isinstance(request.get("field"), str) or not request["field"]
                    or not isinstance(request.get("reason"), str) or not request["reason"]
                    or not isinstance(request.get("basis"), (str, type(None)))
                    or request.get("basis") not in {"before", "after", "change", "reported", "unknown", None}
                    or not isinstance(request.get("scope"), (str, type(None)))
                    or request.get("scope") not in {"consolidated", "parent", "unknown", None}
                    or any(value is not None and not isinstance(value, str)
                           for value in (request.get("period"), request.get("company"), request.get("file")))):
                problems.append(prefix + "字段类型或枚举错误")
        status = finding.get("status")
        valid_relation = ((status == "needs_review" and decision == "ask" and bool(requests)) or
                          (status in {"confirmed_error", "no_issue"} and decision == "proceed" and not requests))
        if not valid_relation:
            problems.append(f"第 {index} 条 finding 状态与补证提示不一致")
        if decision == "ask":
            ask_count += 1
        item_count += len(requests)
    if "evidence_requests" in summary and summary["evidence_requests"] != ask_count:
        problems.append("待补证 finding 汇总与完整列表不一致")
    if "evidence_request_items" in summary and summary["evidence_request_items"] != item_count:
        problems.append("补证条目汇总与完整列表不一致")
    if problems:
        return {"status": "invalid", "label": "补证字段不符合契约：" + "；".join(problems),
                "finding_count": None, "item_count": None, "item_count_source": "invalid"}
    source = "provided" if "evidence_request_items" in summary else "derived_from_complete_lists"
    return {"status": "available", "label": "本次核查补证信息可用",
            "finding_count": summary.get("evidence_requests", ask_count),
            "item_count": summary.get("evidence_request_items", item_count), "item_count_source": source}


def finding_request_view(result: dict, finding: dict) -> dict:
    contract = evidence_request_contract(result)
    if contract["status"] != "available":
        raw_items = finding.get("evidence_request") if isinstance(finding, dict) else None
        items = [item for item in raw_items if isinstance(item, dict)] if isinstance(raw_items, list) else []
        return {"status": contract["status"], "label": contract["label"], "items": items}
    decision = finding.get("decision")
    items = finding.get("evidence_request", [])
    return {"status": "ask" if decision == "ask" else "proceed",
            "label": "需要补充证据或人工澄清" if decision == "ask" else "本次核查未提出补证请求",
            "items": items}


def _display(value) -> str:
    return "待确认/未指定" if value in (None, "", "unknown") else str(value)


def format_evidence_requests(items: list[dict]) -> str:
    lines = []
    for index, item in enumerate(items, 1):
        role = item.get("doc_role")
        request_type = item.get("request_type")
        fields = [("材料", ROLE_LABELS.get(role, _display(role)) if isinstance(role, str) else _display(role)),
                  ("字段", _display(item.get("field"))), ("期间", _display(item.get("period"))),
                  ("公司", _display(item.get("company"))), ("调整口径", _display(item.get("basis"))),
                  ("合并范围", _display(item.get("scope"))), ("文件线索", _display(item.get("file"))),
                  ("请求类型", REQUEST_TYPE_LABELS.get(request_type, _display(request_type))
                   if isinstance(request_type, str) else _display(request_type)),
                  ("原因", _display(item.get("reason")))]
        lines.append(f"{index}. " + "；".join(f"{key}={value}" for key, value in fields))
    return "\n".join(lines)


def findings_rows(result: dict, reviews: dict[str, dict]) -> list[list[str]]:
    rows = []
    for finding in result.get("findings", []):
        claim = finding.get("claim", {})
        review = reviews.get(finding.get("id", ""), {})
        request_view = finding_request_view(result, finding)
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
            request_view["label"],
            len(request_view["items"]) if request_view["status"] in {"ask", "proceed"} else "未确认",
            format_evidence_requests(request_view["items"]),
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
    contract = evidence_request_contract(result)
    count_text = contract["label"]
    if contract["status"] == "available":
        source = "（由已提供请求列表计算）" if contract["item_count_source"] == "derived_from_complete_lists" else ""
        count_text = f"本次核查原结果：待补证/待澄清发现 {contract['finding_count']} 项；补证/澄清请求 {contract['item_count']} 条{source}。"
    lines = [
        "# 研报核查结果（含人工复核）",
        "",
        "仅覆盖已提取的受支持事实，不表示已审查文章全部论断。",
        "",
        f"已确认错误 {summary.get('confirmed_error', 0)}；待人工确认 {summary.get('needs_review', 0)}；"
        f"未发现问题 {summary.get('no_issue', 0)}。",
        count_text,
        "",
        "| " + " | ".join(_HEADER) + " |",
        "|" + "---|" * len(_HEADER),
    ]
    for row in findings_rows(result, reviews):
        lines.append("| " + " | ".join(_guard(str(cell)).replace("|", "\\|").replace("\r", " ").replace("\n", "<br>") for cell in row) + " |")
    request_findings = [(finding, finding_request_view(result, finding)) for finding in result.get("findings", [])]
    request_findings = [(finding, view) for finding, view in request_findings if view["items"]]
    if request_findings:
        lines += ["", "## 补充证据 / 人工澄清详情", ""]
        for finding, view in request_findings:
            lines += [f"### {finding.get('id', '未编号')} · {view['label']}", ""]
            for line in format_evidence_requests(view["items"]).splitlines():
                lines.append("- " + line.replace("|", "\\|").replace("\r", " ").replace("\n", " "))
            lines.append("")
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
    contract = evidence_request_contract(result)
    if contract["status"] == "available":
        source = "（由完整请求列表计算）" if contract["item_count_source"] == "derived_from_complete_lists" else ""
        lines.append(f"本次核查原结果：待补证/待澄清发现 {contract['finding_count']} 项；补证/澄清请求 {contract['item_count']} 条{source}")
    else:
        lines.append(contract["label"])
    lines.append("")
    for finding, row in zip(result.get("findings", []), findings_rows(result, reviews)):
        lines.append(f"【{row[0]}】{row[2]} {row[3]}　{row[4]}")
        if row[7]:
            lines.append(f"  建议值：{row[7]}　规则：{row[10]}")
        lines.append(f"  依据：{row[9]}　复核：{row[14]}")
        request_view = finding_request_view(result, finding)
        lines.append(f"  补证状态：{request_view['label']}；条目数：{len(request_view['items']) if request_view['status'] in {'ask', 'proceed'} else '未确认'}")
        lines.extend("  " + line for line in format_evidence_requests(request_view["items"]).splitlines())
        lines.append("")
    lines.append(f"复核进度：{sum(1 for e in reviews.values() if e.get('status') != 'unreviewed')}/{len(reviews)}")
    lines.append("声明：仅覆盖已提取的受支持事实，不表示已审查文章全部论断。")
    return lines


def report_pdf_bytes(result: dict, reviews: dict[str, dict]) -> bytes | None:
    """生成完整、分页的 CJK PDF；请求内容不截断。"""
    wrapped = []
    for logical in _pdf_lines(result, reviews):
        physical = logical.splitlines() or [""]
        for line in physical:
            if not line:
                wrapped.append("")
            else:
                wrapped.extend(line[start:start + 46] for start in range(0, len(line), 46))
    try:
        import fitz  # PyMuPDF
    except ImportError:
        fitz = None
    if fitz is None:
        try:
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.ttfonts import TTFont
            from reportlab.pdfgen import canvas
            font_paths = (Path("C:/Windows/Fonts/msyh.ttc"), Path("C:/Windows/Fonts/simhei.ttf"),
                          Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"))
            font_path = next(path for path in font_paths if path.is_file())
            pdfmetrics.registerFont(TTFont("R09CJK", str(font_path)))
            buffer = io.BytesIO()
            page = canvas.Canvas(buffer, pagesize=(595, 842), invariant=1)
            page.setFont("R09CJK", 10)
            y = 792
            for line in wrapped:
                if y < 52:
                    page.showPage()
                    page.setFont("R09CJK", 10)
                    y = 792
                page.drawString(50, y, line or " ")
                y -= 16 if line else 10
            page.save()
            return buffer.getvalue()
        except (ImportError, OSError, StopIteration, ValueError):
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
        for line in wrapped:
            if y > 775:
                page = new_pdf_page()
                y = 50.0
            text = line or " "
            result_code = page.insert_textbox(fitz.Rect(50, y, 545, y + 15), text,
                                fontsize=10, fontname="cjk0")
            if result_code < 0:
                raise ValueError("PDF line did not fit")
            y += 16 if line else 10
        buffer = io.BytesIO()
        document.save(buffer)
        document.close()
        return buffer.getvalue()
    except Exception:
        return None
