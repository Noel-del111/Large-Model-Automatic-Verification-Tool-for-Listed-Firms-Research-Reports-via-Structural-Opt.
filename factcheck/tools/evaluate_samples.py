"""Re-run the local development corpus, then compare with the untouched E sheets.

Answer workbooks are opened only after run_check has produced its result. They
never enter extraction or checking. Raw rows, duplicate rows and disagreements
remain separate, so this report is not a claimed blind-test accuracy score.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import sys
from xml.etree import ElementTree as ET
from zipfile import ZipFile
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "factcheck/src"), str(ROOT / "pdfparse/src")]

from yjcheck.claim_extract import METRICS, NUMBER_RE
from yjcheck.model import ModelConfig
from yjcheck.model_runtime import RuntimeSettings
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


def _file_info(path: Path) -> dict:
    return {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _source_hashes() -> dict:
    paths = [Path(__file__).resolve()]
    for directory in (ROOT / "factcheck/src/yjcheck", ROOT / "pdfparse/src/yjparse"):
        paths.extend(directory.rglob("*.py"))
    return {path.relative_to(ROOT).as_posix(): _file_info(path)["sha256"] for path in sorted(set(paths))}


def _model_snapshot(config: ModelConfig, runtime: RuntimeSettings) -> dict:
    """Use an explicit allowlist; never serialize the credential or endpoint."""
    return {"model": config.model, "endpoint_sha256": hashlib.sha256(config.base_url.encode("utf-8")).hexdigest(),
            "timeout_seconds": config.timeout, "review_text": config.review_text,
            "thinking": config.thinking, "reasoning_effort": getattr(config, "reasoning_effort", ""),
            "temperature": 0, "input_cny_per_mtok": runtime.input_cny_per_mtok,
            "output_cny_per_mtok": runtime.output_cny_per_mtok, "price_source": runtime.price_source,
            "price_date": runtime.price_date, "context_tokens": runtime.context_tokens,
            "max_output_tokens": runtime.max_output_tokens, "budget_cny": runtime.budget_cny,
            "max_retries": runtime.max_retries, "ledger": str(runtime.ledger.resolve()),
            "few_shot_examples": 0, "prompt_protocol": "facts-v1 extraction plus hybrid text review without examples"}


_TRACE_FIELDS = frozenset({"call_id", "model", "purpose", "attempt", "created_at", "request_sha256",
                           "input_token_estimate", "estimate_method", "max_output_tokens", "price_source", "price_date",
                           "input_cny_per_mtok", "output_cny_per_mtok", "cost_basis", "thinking", "reasoning_effort",
                           "maximum_cny", "cost_cny", "status", "local_endpoint", "input_tokens", "output_tokens",
                           "usage_source", "duration_seconds", "error_code", "finish_reason", "response_model",
                           "system_fingerprint", "reservation_exceeded", "response_format"})


def _runtime_calls(result: dict) -> dict:
    calls = {}

    def walk(value):
        if isinstance(value, dict):
            if value.get("call_id"):
                calls[value["call_id"]] = {key: item for key, item in value.items() if key in _TRACE_FIELDS}
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    # Include retry traces nested in either model extraction or text detection.
    walk(result.get("model_traces", []))
    walk(result.get("text_review", {}).get("traces", []))
    return calls


def _usage_summary(calls: dict) -> dict:
    traces = list(calls.values())
    cost = sum((Decimal(str(trace.get("cost_cny", trace.get("maximum_cny", 0)))) for trace in traces), Decimal(0))
    return {"calls": len(traces), "input_tokens": sum(trace.get("input_tokens", 0) for trace in traces),
            "output_tokens": sum(trace.get("output_tokens", 0) for trace in traces),
            "accounted_cny": float(cost), "cost_basis": "configured_rate_upper_bound",
            "unknown_usage_calls": sum(trace.get("usage_source") != "provider" for trace in traces),
            "failed_calls": sum(trace.get("status") != "ok" for trace in traces),
            "duration_seconds": round(sum(trace.get("duration_seconds", 0) for trace in traces), 6),
            "trace_deduplication": "unique call_id across extraction, text review and retry history",
            "traces": sorted(traces, key=lambda trace: trace["call_id"])}


def _separate_text_review(result: dict) -> dict:
    review = result.get("text_review", {})
    fields = ("schema_version", "document_id", "scene", "detector", "errors", "coverage", "raw_candidates",
              "rejected_candidates", "source_locations")
    return {**{key: review[key] for key in fields if key in review},
            "evaluation_scope": "Separate unscored text candidates; paired E workbook rows are not text-error gold.",
            "gold_available": False, "detection_metrics": None}


def evaluate(samples: Path, out: Path, *, model: bool = False) -> dict:
    model_config = None
    model_settings = None
    if model:
        model_config = replace(ModelConfig.from_env(), review_text=True)
        if not model_config.base_url or not model_config.model:
            raise ValueError("开启模型需配置 YJCHECK_BASE_URL 和 YJCHECK_MODEL")
        local = urlparse(model_config.base_url).hostname in {"localhost", "127.0.0.1", "::1"}
        model_settings = _model_snapshot(model_config, RuntimeSettings.from_env(local=local))
    run_config = {"mode": "model" if model else "offline", "model_settings": model_settings,
                  "source_code_sha256": _source_hashes(), "resume_supported": False,
                  "answer_isolation": "Each workbook is hashed and read only after its paired inference completes."}
    out.mkdir(parents=True, exist_ok=True)
    (out / "run_config.json").write_text(json.dumps(run_config, ensure_ascii=False, indent=2), encoding="utf-8")
    cases = []
    all_calls = {}
    for folder in sorted(samples.iterdir()):
        reports, sources, sheets = sorted(folder.glob("*.docx")), sorted(folder.glob("*.pdf")), sorted(folder.glob("*.xlsx"))
        if len(reports) != 1 or not sources or len(sheets) != 1:
            continue
        input_hashes = {"report": _file_info(reports[0]), "sources": [_file_info(path) for path in sources]}
        # Hard boundary: only report and source PDFs reach the checker.
        result, result_dir = run_check(reports[0], sources, out / "runs", model_config=model_config)
        gold_rows = read_answer_rows(sheets[0])
        answer_hash = _file_info(sheets[0])
        calls = _runtime_calls(result)
        all_calls.update(calls)
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
                      "input_hashes": input_hashes, "answer_workbook_hash_after_inference": answer_hash,
                      "runtime": result.get("runtime", {}), "unique_model_usage": _usage_summary(calls),
                      "text_review": _separate_text_review(result),
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
    text_errors = [error for case in cases for error in case["text_review"].get("errors", [])]
    summary["unique_model_usage"] = _usage_summary(all_calls)
    summary["text_review"] = {"candidates": len(text_errors),
                              "status_counts": dict(Counter(error.get("status", "needs_review") for error in text_errors)),
                              "raw_model_candidates": sum(len(case["text_review"].get("raw_candidates", [])) for case in cases),
                              "rejected_model_candidates": sum(len(case["text_review"].get("rejected_candidates", [])) for case in cases),
                              "execution_complete_cases": sum(bool(case["text_review"].get("coverage", {}).get("execution_complete")) for case in cases),
                              "gold_available": False, "detection_metrics": None,
                              "scope": "Descriptive candidate/review burden only; not included in paired workbook accuracy."}
    output = {"created_at": datetime.now(timezone.utc).isoformat(), "evaluation_kind": "development_regression_not_blind_test",
              "methodology": ["run_check only receives report and source PDF paths; workbooks are read afterwards",
                              "match original rows by metric, displayed value, unit and explicit adjustment basis",
                              "semantic status, error type, source PDF page and duplicates are scored separately",
                              "unmatched, ambiguous and unannotated predictions are not treated as passing",
                              "original expected rows remain unchanged; classification differences need adjudication",
                              "text review candidates remain separate and unscored; workbook labels do not provide exhaustive text-error gold",
                              "model usage includes unique charged call IDs and retries, with conservative reservation costs when provider usage is unavailable"],
              "run_config": run_config,
              "summary": summary, "cases": cases}
    out.mkdir(parents=True, exist_ok=True)
    (out / "evaluation.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, default=ROOT.parent / "E测试样本最新版")
    parser.add_argument("--out", type=Path, default=ROOT / "data/c-dev-eval")
    parser.add_argument("--model", action="store_true", help="Use configured model extraction and text review; consumes the shared API budget")
    args = parser.parse_args()
    if not args.samples.is_dir():
        parser.error(f"样本目录不存在：{args.samples}")
    result = evaluate(args.samples, args.out, model=args.model)
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    print(str((args.out / "evaluation.json").resolve()))
    # Successful generation is not agreement. Automated acceptance must inspect
    # the explicit dimensions instead of interpreting process exit 0 as 100%.
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
