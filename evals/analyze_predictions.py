"""Offline candidate/auto-confirm analysis for stage comparisons; no model calls.

Scores already-produced predictions against locally prepared answers so接手者 can
answer the open V2 questions without new paid runs:

- 按 15 类错误分别统计 P/R/F1（预测与答案同类型、原句包含、一对一最大匹配）；
- 列出组合流程中自动确认（confirmed_error）的 TP/FP 失配清单，供逐条排查已知 1 FP；
- 组合 vs 直接模型逐文档 TP/FP/FN 得失，找出净增 4 TP / 9 FP 来自哪些文档与类型；
- 待复核队列按类型与 review_priority 分级统计（人工负担视图）。

Inputs (local, never committed):
- answers: gold.{split}.jsonl produced by dataset_prepare.py, or any JSON/JSONL of
  records {"document_id", "errors": [{"error_type"/"type", "spans": [{"start","end","text"}], "scorable"}]}
- direct/combined: detect_text reports, either a directory of per-doc JSON files
  (run_v2 ``predictions/<detector>/``) or a single JSON file containing a list.

usage:
    python evals/analyze_predictions.py --answers data/v2/gold.eval_oct05.jsonl ^
        --direct data/v2/predictions/model_direct --combined data/v2/predictions/hybrid ^
        --out data/v2/analysis
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "evals"), str(ROOT / "factcheck/src")]

from fined_bench_eval import _contains_error, _maximum_matching, _valid_span
from yjcheck.review_hints import all_review_hints

FINED_TYPES = ("时间信息非法", "冗余语句", "格式错误", "数值缺失", "属性值缺失错误",
               "术语误用", "法规引用错误", "模糊语言", "数值单位错误", "金融要素缺失",
               "语义逻辑矛盾", "时间矛盾", "数值不一致错误", "计算错误", "不一致条款")
FOCUS_TYPES = ("金融要素缺失", "术语误用", "冗余语句")


def _records_from(path: str) -> list[dict]:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(f"输入文件或目录不存在：{target}")
    if target.is_dir():
        records = []
        for file in sorted(target.glob("*.json")):
            records.append(json.loads(file.read_text(encoding="utf-8")))
        if not records:
            raise ValueError(f"目录 {target} 下没有任何 .json 结果文件")
        return records
    text = target.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
        return data if isinstance(data, list) else [data]
    except ValueError:
        return [json.loads(line) for line in text.splitlines() if line.strip()]


def _canonical_error(record: dict) -> dict:
    error = {"error_type": record.get("error_type", record.get("type", ""))}
    spans = []
    for span in record.get("spans", []) or []:
        if (isinstance(span, dict) and _valid_span(span) and isinstance(span.get("text"), str)
                and len(span["text"]) == span["end"] - span["start"]):
            spans.append({"start": span["start"], "end": span["end"], "text": span["text"]})
    error["spans"] = spans
    for key in ("scorable", "reason", "status", "review_priority", "detector_id", "validation"):
        if key in record:
            error[key] = record[key]
    return error


def load_gold_docs(path: str) -> dict[str, dict]:
    docs: dict[str, dict] = {}
    for record in _records_from(path):
        doc_id = record.get("document_id")
        if not isinstance(doc_id, str) or not doc_id:
            raise ValueError(f"答案记录缺少 document_id：{str(record)[:120]}")
        if not isinstance(record.get("errors"), list):
            raise ValueError(f"答案记录 {doc_id} 的 errors 必须是列表")
        if doc_id in docs:
            raise ValueError(f"答案记录重复：{doc_id}")
        docs[doc_id] = {"document_id": doc_id, "errors": [_canonical_error(e) for e in record["errors"]]}
    return docs


def load_prediction_docs(path: str, *, use_all_hints: bool = False) -> dict[str, dict]:
    """Load prediction reports.

    ``use_all_hints=True`` applies the unified ``all_review_hints`` transform so
    rejected candidates in ``rejected_candidates`` (hybrid) are represented as
    unmatched review hints, symmetric with direct-model anchor-rejected errors.
    """
    docs: dict[str, dict] = {}
    for report in _records_from(path):
        if use_all_hints:
            report = all_review_hints(report)
        doc_id = report.get("document_id")
        if not isinstance(doc_id, str) or not doc_id:
            raise ValueError("预测报告缺少 document_id")
        if not isinstance(report.get("errors"), list):
            raise ValueError(f"预测报告 {doc_id} 的 errors 必须是列表")
        if doc_id in docs:
            raise ValueError(f"预测报告重复：{doc_id}")
        docs[doc_id] = {"document_id": doc_id, "errors": [_canonical_error(e) for e in report["errors"]]}
    return docs


def _rates(tp: int, fp: int, fn: int) -> dict:
    precision = tp / max(tp + fp, 1)
    recall = tp / max(tp + fn, 1)
    return {"tp": tp, "fp": fp, "fn": fn,
            "precision": round(precision, 4), "recall": round(recall, 4),
            "f1": round(2 * precision * recall / max(precision + recall, 1e-9), 4)}


def match_errors(pred_errors: list[dict], gold_errors: list[dict]) -> tuple[list[tuple], list[dict], list[dict]]:
    """Typed, all-spans-contained, maximum one-to-one matching per document."""
    pairs = _maximum_matching(pred_errors, gold_errors,
                              lambda p, g: _contains_error(p, g) and p["error_type"] == g["error_type"])
    paired_preds = {id(p) for p, _ in pairs}
    paired_golds = {id(g) for _, g in pairs}
    return pairs, [p for p in pred_errors if id(p) not in paired_preds], \
        [g for g in gold_errors if id(g) not in paired_golds]


def score_docs(pred_docs: dict[str, dict], gold_docs: dict[str, dict],
               type_filter: str | None = None) -> dict:
    tp = fp = fn = 0
    per_doc = []
    for doc_id in sorted(set(gold_docs) | set(pred_docs)):
        preds = [e for e in pred_docs.get(doc_id, {}).get("errors", [])
                 if type_filter is None or e["error_type"] == type_filter]
        golds = [g for g in gold_docs.get(doc_id, {}).get("errors", [])
                 if g.get("scorable", True) and (type_filter is None or g["error_type"] == type_filter)]
        if not preds and not golds:
            continue
        pairs, unmatched_preds, unmatched_golds = match_errors(preds, golds)
        row = {"document_id": doc_id, **_rates(len(pairs), len(unmatched_preds), len(unmatched_golds)),
               "n_pred": len(preds), "n_gold": len(golds)}
        per_doc.append(row)
        tp += len(pairs)
        fp += len(unmatched_preds)
        fn += len(unmatched_golds)
    missing = [doc_id for doc_id in sorted(set(gold_docs) - set(pred_docs))
               if type_filter is None or any(g["error_type"] == type_filter for g in gold_docs[doc_id]["errors"])]
    return {**_rates(tp, fp, fn), "per_doc": per_doc, "missing_prediction_documents": missing}


def auto_confirm_audit(pred_docs: dict[str, dict], gold_docs: dict[str, dict]) -> dict:
    matched_entries, mismatch_entries = [], []
    per_type_tp = {t: 0 for t in FINED_TYPES}
    per_type_fp = {t: 0 for t in FINED_TYPES}
    for doc_id in sorted(set(pred_docs) | set(gold_docs)):
        preds = pred_docs.get(doc_id, {}).get("errors", [])
        golds = [g for g in gold_docs.get(doc_id, {}).get("errors", []) if g.get("scorable", True)]
        auto = [e for e in preds if e.get("status") == "confirmed_error"]
        pairs, unmatched_preds, _ = match_errors(auto, golds)
        for pred, gold in pairs:
            matched_entries.append({"document_id": doc_id, "error_type": pred["error_type"],
                                    "text": pred["spans"][0]["text"] if pred.get("spans") else "",
                                    "gold_error_type": gold["error_type"],
                                    "gold_text": gold["spans"][0]["text"] if gold.get("spans") else ""})
            per_type_tp[pred["error_type"]] = per_type_tp.get(pred["error_type"], 0) + 1
        for pred in unmatched_preds:
            mismatch_entries.append({"document_id": doc_id, "error_type": pred["error_type"],
                                     "text": pred["spans"][0]["text"] if pred.get("spans") else "",
                                     "detector_id": pred.get("detector_id", ""),
                                     "validation": pred.get("validation", ""),
                                     "reason": pred.get("reason", "")})
            per_type_fp[pred["error_type"]] = per_type_fp.get(pred["error_type"], 0) + 1
    return {"confirmed_total": len(matched_entries) + len(mismatch_entries),
            "confirmed_tp": len(matched_entries), "confirmed_fp": len(mismatch_entries),
            "mismatches": mismatch_entries, "matched": matched_entries,
            "per_type": {t: {"tp": per_type_tp.get(t, 0), "fp": per_type_fp.get(t, 0)} for t in FINED_TYPES}}


def review_burden(pred_docs: dict[str, dict]) -> dict:
    by_type: dict[str, int] = {}
    by_priority: dict[str, int] = {"high": 0, "low": 0, "confirmed": 0, "unspecified": 0}
    for doc in pred_docs.values():
        for error in doc["errors"]:
            if error.get("status") != "needs_review":
                continue
            by_type[error["error_type"]] = by_type.get(error["error_type"], 0) + 1
            priority = error.get("review_priority")
            by_priority[priority if priority in by_priority else "unspecified"] += 1
    by_priority.pop("confirmed", None)
    return {"pending_total": sum(by_type.values()), "by_type": by_type, "by_priority": by_priority}


def _overlap(left: dict, right: dict) -> bool:
    return any(a["start"] < b["end"] and b["start"] < a["end"]
               for a in left.get("spans", []) for b in right.get("spans", []))


def focus_miss_audit(pred_docs: dict[str, dict], gold_docs: dict[str, dict]) -> dict:
    """Expose every unmatched priority-type annotation without relabeling it.

    Buckets describe span/type relationships only. They are routing signals for
    human error analysis, not semantic verdicts or permission to alter gold.
    """
    cases = []
    for document_id, gold_doc in gold_docs.items():
        predictions = pred_docs.get(document_id, {}).get("errors", [])
        targets = [error for error in gold_doc.get("errors", []) if error.get("scorable", True)]
        _, _, misses = match_errors(predictions, targets)
        for gold in misses:
            if gold["error_type"] not in FOCUS_TYPES:
                continue
            containing = [p for p in predictions if _contains_error(p, gold)]
            same_type_containing = [p for p in containing if p["error_type"] == gold["error_type"]]
            overlapping = [p for p in predictions if _overlap(p, gold)]
            same_type_overlap = [p for p in overlapping if p["error_type"] == gold["error_type"]]
            category = ("同类型一对一匹配竞争" if same_type_containing else
                        "定位包含但类型不同" if containing else
                        "同类型定位不完整" if same_type_overlap else
                        "存在其他重叠提示" if overlapping else "无重叠提示")
            related = same_type_containing or containing or same_type_overlap or overlapping
            cases.append({"document_id": document_id, "error_type": gold["error_type"],
                          "category": category, "gold_spans": gold.get("spans", []),
                          "related_predictions": [{"error_type": p["error_type"],
                                                   "spans": p.get("spans", []),
                                                   "detector_id": p.get("detector_id", "")}
                                                  for p in related]})
    by_type = {kind: {"total": 0, "by_category": {}} for kind in FOCUS_TYPES}
    for case in cases:
        row = by_type[case["error_type"]]
        row["total"] += 1
        row["by_category"][case["category"]] = row["by_category"].get(case["category"], 0) + 1
    return {"types": list(FOCUS_TYPES), "total": len(cases), "by_type": by_type,
            "cases": cases,
            "scope_note": "关系桶不是人工语义裁决；不自动修改标注。"}


def analyze(gold_docs: dict[str, dict], direct: dict[str, dict] | None,
            combined: dict[str, dict] | None) -> dict:
    result: dict[str, Any] = {}
    for name, preds in (("direct", direct), ("combined", combined)):
        if preds is None:
            continue
        result[name] = {"overall": score_docs(preds, gold_docs),
                        "per_type": {t: score_docs(preds, gold_docs, type_filter=t) for t in FINED_TYPES},
                        "review_burden": review_burden(preds),
                        "focus_misses": focus_miss_audit(preds, gold_docs)}
    if combined is not None:
        result["combined"]["auto_confirm"] = auto_confirm_audit(combined, gold_docs)
    if direct is not None and combined is not None:
        direct_per_doc = {row["document_id"]: row for row in result["direct"]["overall"]["per_doc"]}
        combined_per_doc = {row["document_id"]: row for row in result["combined"]["overall"]["per_doc"]}
        delta_rows = []
        for doc_id in sorted(set(direct_per_doc) | set(combined_per_doc)):
            a, b = direct_per_doc.get(doc_id, {}), combined_per_doc.get(doc_id, {})
            delta_rows.append({"document_id": doc_id,
                               "direct_tp": a.get("tp", 0), "direct_fp": a.get("fp", 0),
                               "direct_fn": a.get("fn", 0),
                               "combined_tp": b.get("tp", 0), "combined_fp": b.get("fp", 0),
                               "combined_fn": b.get("fn", 0),
                               "tp_delta": b.get("tp", 0) - a.get("tp", 0),
                               "fp_delta": b.get("fp", 0) - a.get("fp", 0),
                               "fn_delta": b.get("fn", 0) - a.get("fn", 0)})
        result["delta"] = {"per_doc": delta_rows,
                           "total_tp_delta": sum(row["tp_delta"] for row in delta_rows),
                           "total_fp_delta": sum(row["fp_delta"] for row in delta_rows),
                           "total_fn_delta": sum(row["fn_delta"] for row in delta_rows)}
    return result


def _csv(rows: list[dict], path: Path) -> None:
    if not rows:
        return
    import csv
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def emit(result: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    for name in ("direct", "combined"):
        if name not in result:
            continue
        per_type_rows = []
        for kind, score in result[name]["per_type"].items():
            if score["per_doc"]:
                per_type_rows.append({"error_type": kind, **{k: v for k, v in score.items() if k != "per_doc"}})
        _csv(per_type_rows, out_dir / f"{name}_per_type.csv")
        burden = result[name]["review_burden"]
        burden_rows = [{"error_type": kind, "pending": count} for kind, count in
                       sorted(burden["by_type"].items(), key=lambda item: -item[1])]
        burden_rows.append({"error_type": "总计", "pending": burden["pending_total"]})
        _csv(burden_rows, out_dir / f"{name}_pending_by_type.csv")
    if "combined" in result and "auto_confirm" in result["combined"]:
        _csv(result["combined"]["auto_confirm"]["mismatches"], out_dir / "auto_confirm_mismatches.csv")
        _csv(result["combined"]["auto_confirm"]["matched"], out_dir / "auto_confirm_matched.csv")
    if "delta" in result:
        _csv(result["delta"]["per_doc"], out_dir / "delta_per_doc.csv")


def main() -> int:
    parser = argparse.ArgumentParser(description="离线候选/自动确认得失分析；不发起模型调用")
    parser.add_argument("--answers", required=True, help="gold.{split}.jsonl 或等价答案文件")
    parser.add_argument("--direct", help="直接模型预测：目录或 JSON 列表文件")
    parser.add_argument("--combined", help="组合流程预测：目录或 JSON 列表文件")
    parser.add_argument("--out", required=True, help="输出目录")
    args = parser.parse_args()
    if not args.direct and not args.combined:
        parser.error("至少提供 --direct 或 --combined 之一")
    gold_docs = load_gold_docs(args.answers)
    if not gold_docs:
        raise SystemExit("答案文件没有可评分的记录")
    direct = load_prediction_docs(args.direct, use_all_hints=True) if args.direct else None
    combined = load_prediction_docs(args.combined, use_all_hints=True) if args.combined else None
    result = analyze(gold_docs, direct, combined)
    emit(result, Path(args.out))
    top = result.get("combined") or result.get("direct")
    print(json.dumps({"overall": top["overall"], "auto_confirm_tp_fp": result.get("combined", {})
                      .get("auto_confirm", {}).get("confirmed_fp")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
