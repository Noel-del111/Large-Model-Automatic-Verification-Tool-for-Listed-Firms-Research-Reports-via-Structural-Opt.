"""Re-run the local development corpus, then compare with the untouched E sheets.

Answer workbooks are opened only after run_check has produced its result. They
never enter extraction or checking. Raw rows, duplicate rows and disagreements
remain separate, so this report is not a claimed blind-test accuracy score.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import re
import sys
from xml.etree import ElementTree as ET
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "factcheck/src"), str(ROOT / "pdfparse/src")]

from yjcheck.claim_extract import METRICS, NUMBER_RE
from yjcheck.pipeline import run_check

_NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_ERROR_TYPES = {"正确": "", "数值": "number", "数值错误": "number", "计算错误/逻辑错误": "number",
                "逻辑错误": "number", "口径错误": ("basis", "scope"), "单位错误": "unit",
                "期间错误": "period", "引用错误": "citation",
                # FinED-Bench 十五类错误类型名称（答案表可复用其标注口径）
                "计算错误": "number", "数值单位错误": "unit", "时间矛盾": "time_conflict",
                "数值不一致错误": "numeric_inconsistency", "术语误用": "term_misuse",
                "语义逻辑矛盾": "semantic_contradiction", "数值缺失": "numeric_missing",
                "冗余语句": "redundant_statement", "时间信息非法": "invalid_time",
                "金融要素缺失": "financial_element_missing", "属性值缺失": "attribute_missing",
                "属性值缺失错误": "attribute_missing", "格式错误": "format_error",
                "法规引用错误": "citation"}
# 答案表各版本的表头列名（检测到表头行时按列名自动对齐，其余版本回退行列号约定）
_ANSWER_HEADERS = {"location": ("位置", "行列"), "error_type": ("错误类型",),
                   "text": ("研报原文", "原文"), "suggestion": ("建议修改", "建议"),
                   "source": ("来源页码", "来源"), "reason": ("依据", "原因")}


def _detect_columns(row_cells: dict[str, str]) -> dict[str, str]:
    """有表头行时按列名对齐；旧版无表头时维持 B/C/D/E/F/G 的行列号约定。"""
    by_text = {str(text).strip(): col for col, text in row_cells.items()}
    mapping: dict[str, str] = {}
    matched = 0
    for key, names in _ANSWER_HEADERS.items():
        for name in names:
            if name in by_text:
                mapping[key] = by_text[name]
                matched += 1
                break
    if matched >= 3:
        return mapping
    return {"location": "B", "error_type": "C", "text": "D",
            "suggestion": "E", "source": "F", "reason": "G"}


def read_answer_rows(path: Path) -> list[dict]:
    """Read original XML values, preserving workbook/sheet/cell coordinates."""
    output = []
    with ZipFile(path) as archive:
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared = ["".join(si.itertext()) for si in ET.fromstring(archive.read("xl/sharedStrings.xml")).findall("s:si", _NS)]
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {r.attrib["Id"]: r.attrib["Target"] for r in relationships}
        for sheet in workbook.findall("s:sheets/s:sheet", _NS):
            rid = sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
            target = targets[rid]
            target = target.lstrip("/") if target.startswith("/") else "xl/" + target
            sheet_rows = ET.fromstring(archive.read(target)).findall("s:sheetData/s:row", _NS)
            columns: dict[str, str] | None = None
            for row in sheet_rows:
                values = {}
                for cell in row.findall("s:c", _NS):
                    col = re.sub(r"\d", "", cell.attrib["r"])
                    value = cell.find("s:v", _NS)
                    text = value.text if value is not None else "".join(cell.find("s:is", _NS).itertext()) if cell.find("s:is", _NS) is not None else ""
                    values[col] = shared[int(text)] if cell.attrib.get("t") == "s" and text else text
                if columns is None:
                    columns = _detect_columns(values)
                text = values.get(columns["text"], "").strip()
                error_type = values.get(columns["error_type"], "").strip()
                if not text or error_type not in _ERROR_TYPES:
                    continue
                no = int(row.attrib["r"])
                output.append({"workbook": str(path.resolve()), "sheet": sheet.attrib["name"], "row": no,
                               "range": f"{columns['text']}{no}:{columns['reason']}{no}", "raw_cells": values,
                               "location": values.get(columns["location"], ""), "error_type": error_type,
                               "text": text, "suggestion": values.get(columns["suggestion"], ""),
                               "source": values.get(columns["source"], ""), "reason": values.get(columns["reason"], "")})
    return output


def _compact(text: str) -> str:
    return re.sub(r"[\s,，]", "", str(text)).replace("−", "-").replace("－", "-")


def _description(row: dict) -> dict:
    text = _compact(row["text"])
    metric = next((METRICS[label] for label in sorted(METRICS, key=len, reverse=True) if label in text), None)
    if metric is None and "归母所有者权益" in text:
        metric = "equity_parent"
    if re.search(r"20\d{2}年度披露", text):
        metric = "publication_year"
        amount = re.search(r"20\d{2}", text).group()
        unit = "年"
    else:
        match = NUMBER_RE.search(text)
        amount, unit = (match["value"], match["unit"]) if match else (None, None)
    # 同比/环比行：研报写的是百分比变化，与系统 *_yoy/_qoq 复算口径对齐。
    if metric and re.search(r"同比|环比", text) and amount is not None and unit not in {"%", "％"}:
        pct = next((m for m in NUMBER_RE.finditer(text) if m["unit"] in {"%", "％", "百分比"}), None)
        if pct is not None:
            sign = "-" if re.search(r"下降|减少|下滑", text) else ""
            amount, unit = sign + pct["value"], pct["unit"]
            metric = metric + ("_yoy" if "同比" in text else "_qoq")
    if unit == "元" and metric in {"eps_basic", "price"}:
        unit = "元/股"
    basis_match = re.search(r"调整前|重述前|调整后|重述后|影响(?:金额)?", text)
    basis = ("before" if basis_match.group().endswith("前") else "after" if basis_match.group().endswith("后") else "change") if basis_match else None
    # 归一到核查侧口径：营业总收入/营业收入同属 revenue；“母公司 xx”为母公司报表口径。
    if metric == "revenue_total":
        metric = "revenue"
    scope = None
    if text.startswith("母公司") and metric not in {"net_profit_parent", "net_profit_parent_excl", "equity_parent"}:
        scope = "parent"
    expected_page = re.search(r"[Pp]\s*(\d+)|第\s*(\d+)\s*页", row["source"])
    return {"metric": metric, "value": amount, "unit": unit, "basis": basis, "scope": scope,
            "source_page": int(expected_page[1] or expected_page[2]) if expected_page else None}


def _value(value: str) -> Decimal | None:
    text = _compact(value)
    if text.startswith(("(", "（")):
        text = "-" + text[1:-1]
    try:
        return Decimal(text)
    except Exception:
        return None


def _match(row: dict, findings: list[dict]) -> list[dict]:
    expected = _description(row)
    candidates = []
    for finding in findings:
        claim = finding["claim"]
        claim_metric = "revenue" if claim.get("metric") == "revenue_total" else claim.get("metric")
        if expected["metric"] and claim_metric != expected["metric"]:
            continue
        if expected.get("scope") and claim.get("scope") != expected["scope"]:
            continue
        if expected["value"] is None or _value(claim["value"]) != _value(expected["value"]):
            continue
        if expected["unit"] != claim["unit"]:
            continue
        if expected["basis"] and expected["basis"] != claim["basis"]:
            continue
        candidates.append(finding)
    return candidates


def evaluate(samples: Path, out: Path) -> dict:
    cases = []
    for folder in sorted(samples.iterdir()):
        reports, sources, sheets = list(folder.glob("*.docx")), list(folder.glob("*.pdf")), list(folder.glob("*.xlsx"))
        if len(reports) != 1 or not sources or len(sheets) != 1:
            continue
        # Hard boundary: only report and source PDFs reach the checker.
        result, result_dir = run_check(reports[0], sources, out / "runs")
        gold_rows = read_answer_rows(sheets[0])
        findings = result["findings"]
        comparisons = []
        matched_ids: set[str] = set()
        first_row_for_id: dict[str, int] = {}
        for row in gold_rows:
            expected = _description(row)
            candidates = _match(row, findings)
            comparison = {"original": row, "parsed_expectation": expected,
                          "match_status": "unique" if len(candidates) == 1 else "missing" if not candidates else "ambiguous",
                          "candidate_ids": [f["claim"]["fact_id"] for f in candidates],
                          "semantic_match": None, "error_type_match": None, "source_page_match": None,
                          "duplicate_of_row": None, "issues": []}
            if len(candidates) == 1:
                finding = candidates[0]
                claim_id = finding["claim"]["fact_id"]
                matched_ids.add(claim_id)
                if claim_id in first_row_for_id:
                    comparison["duplicate_of_row"] = first_row_for_id[claim_id]
                    comparison["issues"].append("duplicate_expected_claim")
                else:
                    first_row_for_id[claim_id] = row["row"]
                expected_status = "no_issue" if row["error_type"] == "正确" else "confirmed_error"
                comparison["semantic_match"] = finding["status"] == expected_status
                expected_type = _ERROR_TYPES[row["error_type"]]
                comparison["error_type_match"] = (finding["error_type"] == expected_type
                                                  if isinstance(expected_type, str)
                                                  else finding["error_type"] in expected_type)
                primary_pages = sorted({location["page"] for fact in finding["evidence"]
                                        for location in fact.get("attributes", {}).get("value_locations", [])
                                        if location.get("page") is not None})
                comparison["source_page_match"] = expected["source_page"] in primary_pages if expected["source_page"] is not None else None
                comparison["prediction"] = {k: finding[k] for k in ("status", "error_type", "suggested_value", "suggestion", "rule_id", "message")}
                comparison["prediction"]["claim"] = finding["claim"]
                comparison["prediction"]["primary_source_pages"] = primary_pages
                comparison["prediction"]["source_facts"] = finding["evidence"]
                if comparison["error_type_match"] is False:
                    comparison["issues"].append("classification_disagreement_requires_review")
                if comparison["source_page_match"] is False:
                    comparison["issues"].append("expected_page_disagrees_with_original_pdf")
            comparisons.append(comparison)
        extra = [f for f in findings if f["claim"]["fact_id"] not in matched_ids]
        cases.append({"case": folder.name, "report": str(reports[0].resolve()), "sources": [str(p.resolve()) for p in sources],
                      "result_dir": str(result_dir.resolve()), "checker_summary": result["summary"],
                      "input_issues": result["input_issues"], "rows": comparisons,
                      "additional_unannotated_predictions": extra})
    rows = [r for case in cases for r in case["rows"]]
    unique = [r for r in rows if r["duplicate_of_row"] is None]
    errors = [r for r in rows if r["original"]["error_type"] != "正确"]
    def score(name, group):
        return {"matched": sum(r[name] is True for r in group), "disagreed": sum(r[name] is False for r in group),
                "unverified": sum(r[name] is None for r in group), "total": len(group)}
    summary = {"cases": len(cases), "original_rows": len(rows), "unique_expected_claims": len(unique),
               "duplicates": len(rows)-len(unique), "declared_error_rows": len(errors),
               "semantic_original_rows": score("semantic_match", rows), "semantic_unique_claims": score("semantic_match", unique),
               "error_type_original_rows": score("error_type_match", rows), "error_type_error_rows": score("error_type_match", errors),
               "source_page_original_rows": score("source_page_match", rows),
               "additional_unannotated_predictions": sum(len(c["additional_unannotated_predictions"]) for c in cases),
               "checker_statuses": dict(Counter(f["status"] for c in cases for f in [r["prediction"] for r in c["rows"] if "prediction" in r])),
               "all_original_dimensions_agree": bool(rows) and all(r["semantic_match"] is True and r["error_type_match"] is True and r["source_page_match"] is True and r["duplicate_of_row"] is None for r in rows)}
    output = {"created_at": datetime.now(timezone.utc).isoformat(), "evaluation_kind": "development_regression_not_blind_test",
              "methodology": ["run_check only receives report and source PDF paths; workbooks are read afterwards",
                              "match original rows by metric, displayed value, unit and explicit adjustment basis",
                              "semantic status, error type, source PDF page and duplicates are scored separately",
                              "unmatched, ambiguous and unannotated predictions are not treated as passing",
                              "original expected rows remain unchanged; classification differences need adjudication"],
              "summary": summary, "cases": cases}
    out.mkdir(parents=True, exist_ok=True)
    (out / "evaluation.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, default=ROOT.parent / "E测试样本最新版")
    parser.add_argument("--out", type=Path, default=ROOT / "data/c-dev-eval")
    args = parser.parse_args()
    if not args.samples.is_dir():
        parser.error(f"样本目录不存在：{args.samples}")
    result = evaluate(args.samples, args.out)
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    print(str((args.out / "evaluation.json").resolve()))
    # Successful generation is not agreement. Automated acceptance must inspect
    # the explicit dimensions instead of interpreting process exit 0 as 100%.
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
