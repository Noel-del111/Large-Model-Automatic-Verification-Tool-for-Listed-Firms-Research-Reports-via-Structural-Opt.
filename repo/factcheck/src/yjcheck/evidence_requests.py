"""将最终核查状态转为可执行的取证清单，不改变三类判定。"""
from __future__ import annotations

from typing import Any
from pathlib import Path


def attach_evidence_requests(finding: dict[str, Any], documents: list[dict]) -> dict:
    """Add Ask/Proceed fields after input-quality overrides have been applied.

    ``ask`` includes both missing evidence and human interpretation requests.
    It is not an additional error status. Unknown fields remain empty/unknown.
    """
    finding = dict(finding)
    finding["decision"] = "ask" if finding["status"] == "needs_review" else "proceed"
    finding["evidence_request"] = []
    if finding["decision"] == "proceed":
        return finding

    claim = finding["claim"]
    rule = finding["rule_id"]
    calculation = finding.get("calculation", {})
    requests = finding["evidence_request"]

    def add(role: str, field: str, reason: str, kind: str = "provide_source",
            period: str | None = None, file: str = "") -> None:
        item = {"doc_role": role, "field": field,
                "period": claim.get("period", "") if period is None else period,
                "company": claim.get("company", ""), "basis": claim.get("basis", "unknown"),
                "scope": claim.get("scope", "unknown"), "file": file,
                "request_type": kind, "reason": reason}
        if item not in requests:
            requests.append(item)

    metric = claim.get("metric", "")
    if rule == "INPUT_IDENTITY_OR_COMPLETENESS":
        for doc in documents:
            issues = [issue for issue in doc.get("issues", []) if issue.startswith("document:")]
            if issues:
                add(doc["role"], "document_integrity", "核对原文件身份、页数与公司后重新解析：" + "；".join(issues),
                    "repair_input", file=doc.get("path", ""))
    elif rule == "NO_CLAIMS":
        add("report", "supported_claims", "提供含可识别指标、数值、单位与期间的研报段落或表格。", "repair_input")
    elif rule.startswith("C.INTRINSIC."):
        add("report", metric, "人工核对研报原句及上下文：" + finding["message"], "clarify_context")
    elif rule == "C.CURRENCY.001":
        add("source", "exchange_rate", "提供两种币种的换算汇率、汇率日期和适用口径。")
    elif calculation.get("required_inputs"):
        for item in calculation["required_inputs"]:
            add("source", item["metric"], "提供同公司、同口径且有原文定位的复算输入。", period=item["period"])
    elif rule in {"C.YOY.001", "C.PE.001"}:
        add("source", metric, "人工确认计算定义和适用条件：" + finding["message"], "clarify_context")
    elif rule == "C.MATCH.002":
        add("source", metric, "提供权威原文或更正公告，说明冲突来源中适用的期间与口径。", "resolve_conflict")
    elif rule == "C.EVIDENCE.001" and calculation.get("blocking_reasons"):
        dimensions = {"公司": "company", "指标": "metric", "期间": "period",
                      "调整前后": "basis", "合并/母公司": "scope", "币种": "currency", "单位": "unit"}
        for reason in calculation["blocking_reasons"]:
            field = next((value for key, value in dimensions.items() if key in reason), metric)
            add("report", field, "补充研报原文及有效定位，确认：" + reason, "clarify_context")
    elif calculation.get("rejected_sources"):
        add("source", metric, "重新解析并核实对应来源的数值行、表头、单位与页码坐标。", "repair_input")
    else:
        add("source", metric, "补充该公司、期间和口径下的财报/公告数值行及表头，保留页码与坐标。")

    if not requests:
        add("report", metric, "核对原始材料后重新运行：" + finding["message"], "repair_input")
    return finding


def request_text(finding: dict) -> str:
    """Plain text representation for backend CSV and Markdown exports."""
    roles = {"report": "研报", "source": "财报/公告"}
    return "；".join(
        f"{roles.get(r['doc_role'], r['doc_role'])} / {r['field']} / {r['period'] or '期间待确认'}：{r['reason']}"
        + (f"（文件：{Path(r['file']).name}）" if r.get("file") else "")
        for r in finding.get("evidence_request", []))
