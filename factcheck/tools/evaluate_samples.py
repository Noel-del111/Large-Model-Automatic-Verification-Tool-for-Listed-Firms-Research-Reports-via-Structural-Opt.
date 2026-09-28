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
_ERROR_TYPES = {"正确": "", "数值": "number", "数值错误": "number", "口径错误": "basis", "单位错误": "unit", "期间错误": "period", "引用错误": "citation"}


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
            for row in ET.fromstring(archive.read(target)).findall("s:sheetData/s:row", _NS):
                values = {}
                for cell in row.findall("s:c", _NS):
                    col = re.sub(r"\d", "", cell.attrib["r"])
                    value = cell.find("s:v", _NS)
                    text = value.text if value is not None else "".join(cell.find("s:is", _NS).itertext()) if cell.find("s:is", _NS) is not None else ""
                    values[col] = shared[int(text)] if cell.attrib.get("t") == "s" and text else text
                if not values.get("D") or values.get("C", "").strip() not in _ERROR_TYPES:
                    continue
                no = int(row.attrib["r"])
                output.append({"workbook": str(path.resolve()), "sheet": sheet.attrib["name"], "row": no,
                               "range": f"B{no}:G{no}", "raw_cells": values,
                               "location": values.get("B", ""), "error_type": values.get("C", "").strip(),
                               "text": values.get("D", ""), "suggestion": values.get("E", ""),
                               "source": values.get("F", ""), "reason": values.get("G", "")})
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
    if unit == "元" and metric in {"eps_basic", "price"}:
        unit = "元/股"
    basis_match = re.search(r"调整前|重述前|调整后|重述后|影响(?:金额)?", text)
    basis = ("before" if basis_match.group().endswith("前") else "after" if basis_match.group().endswith("后") else "change") if basis_match else None
    expected_page = re.search(r"[Pp]\s*(\d+)|第\s*(\d+)\s*页", row["source"])
    return {"metric": metric, "value": amount, "unit": unit, "basis": basis,
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
        if expected["metric"] and claim["metric"] != expected["metric"]:
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
                comparison["error_type_match"] = finding["error_type"] == _ERROR_TYPES[row["error_type"]]
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
