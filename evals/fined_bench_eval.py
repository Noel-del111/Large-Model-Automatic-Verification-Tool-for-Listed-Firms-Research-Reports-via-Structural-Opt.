"""FinED-Bench 体检与评分工具。

FinED-Bench（arXiv:2608.12342）给每个错误标注了 ``start_idx`` 与 ``error_span``，
但实测发现偏移并不可靠：长文档集里 60% 的 span 与 content 对不上。
本工具做三件事：

1. 体检：校验偏移可用性、重叠、重复、可重新锚定的比例；
2. 重新锚定：只接受原偏移或唯一精确/空白归一匹配，歧义交给人工；
3. 评分：按错误实例计算原句包含且类型一致的 precision / recall / F1；
   span 重叠只作定位诊断，类型准确率独立匹配，均不是作者官方评分程序。

用法：
    py -3 evals/fined_bench_eval.py --data <FinED-Bench-main 目录> --out evals/reports
    py -3 evals/fined_bench_eval.py --selftest
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import warnings
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

REPORT_KEYS = ("documents", "errors", "offset_ok", "offset_bad", "reanchored",
               "unresolved", "overlap", "duplicate_groups", "table_docs",
               "number_unit_docs", "by_type", "by_scene", "invalid_span_arrays")


def normalize(text: str) -> str:
    """比较与再锚定用的归一化：压缩空白、统一全角空格。"""
    return re.sub(r"\s+", "", text.replace("\u3000", " "))


def _occurrences(content: str, span: str) -> list[int]:
    positions, cursor = [], 0
    while span:
        index = content.find(span, cursor)
        if index < 0:
            break
        positions.append(index)
        cursor = index + 1
    return positions


def anchor_span(content: str, span: str, start: int | None) -> Dict[str, Any]:
    """Return an auditable anchoring decision. Never choose an arbitrary occurrence."""
    if not isinstance(span, str) or not normalize(span):
        return {"accepted": False, "method": "empty_span", "candidates": []}
    if isinstance(start, int) and not isinstance(start, bool) and start >= 0 and content[start:start + len(span)] == span:
        return {"accepted": True, "method": "original_offset", "start": start,
                "end": start + len(span), "text": span}
    found = _occurrences(content, span)
    if len(found) == 1:
        begin = found[0]
        return {"accepted": True, "method": "unique_exact", "start": begin,
                "end": begin + len(span), "text": span}
    if found:
        return {"accepted": False, "method": "ambiguous_exact",
                "candidates": [{"start": index, "end": index + len(span)} for index in found]}
    flat_span = normalize(span)
    found = _occurrences(normalize(content), flat_span)
    mapping = [i for i, ch in enumerate(content) if not ch.isspace()]
    candidates = [{"start": mapping[index], "end": mapping[index + len(flat_span) - 1] + 1}
                  for index in found]
    if len(candidates) == 1:
        located = candidates[0]
        return {"accepted": True, "method": "unique_whitespace_normalized", **located,
                "text": content[located["start"]:located["end"]]}
    return {"accepted": False, "method": "ambiguous_normalized" if candidates else "unresolved",
            "candidates": candidates}


def locate(content: str, span: str, start: int) -> Optional[Tuple[int, int]]:
    """Compatibility helper; use anchor_span for audit information."""
    result = anchor_span(content, span, start)
    return (result["start"], result["end"]) if result["accepted"] else None


def check_dataset(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    report: Dict[str, Any] = {key: 0 for key in REPORT_KEYS}
    report["documents"] = len(rows)
    report["by_type"] = Counter()
    report["by_scene"] = Counter()
    for row in rows:
        content = row["content"]
        report["by_scene"][row["scene"]] += 1
        if re.search(r"\|\s*-{2,}", content):
            report["table_docs"] += 1
        if re.search(r"\d[\d,\.]*\s*(万元|亿元|元|%|％)", content):
            report["number_unit_docs"] += 1
        seen: List[Tuple[int, int]] = []
        duplicates: Counter = Counter()
        for error in row["errors"]:
            report["errors"] += 1
            if not isinstance(error, dict):
                report["invalid_span_arrays"] += 1
                continue
            report["by_type"][str(error.get("error_type", "missing_error_type"))] += 1
            starts = error.get("start_idx") or []
            spans = error.get("error_span") or []
            if not isinstance(starts, list) or not isinstance(spans, list) or len(starts) != len(spans) or not spans:
                report["invalid_span_arrays"] += 1
            starts = starts if isinstance(starts, list) else []
            spans = spans if isinstance(spans, list) else []
            duplicates[json.dumps([starts, spans, error.get("error_type")], sort_keys=True, ensure_ascii=False)] += 1
            for index, span in enumerate(spans):
                start = starts[index] if index < len(starts) else None
                if type(start) is int and start >= 0 and isinstance(span, str) and span and content[start:start + len(span)] == span:
                    report["offset_ok"] += 1
                    seen.append((start, start + len(span)))
                    continue
                report["offset_bad"] += 1
                located = locate(content, span, start)
                if located:
                    report["reanchored"] += 1
                    seen.append(located)
                else:
                    report["unresolved"] += 1
        seen.sort()
        for (_, end_a), (begin_b, _) in zip(seen, seen[1:]):
            if begin_b < end_a:
                report["overlap"] += 1
        report["duplicate_groups"] += sum(1 for value in duplicates.values() if value > 1)
    report["by_type"] = dict(report["by_type"].most_common())
    report["by_scene"] = dict(report["by_scene"].most_common())
    total = max(report["offset_ok"] + report["offset_bad"], 1)
    report["offset_ok_rate"] = round(report["offset_ok"] / total, 4)
    report["reanchor_rate"] = round(report["reanchored"] / max(report["offset_bad"], 1), 4)
    return report


def _canonical(item: dict) -> str:
    return json.dumps(item, sort_keys=True, ensure_ascii=False)


def _maximum_matching(predictions: list[dict], golds: list[dict], compatible) -> list[tuple[dict, dict]]:
    """Deterministic maximum-cardinality bipartite matching, independent of input order."""
    preds, targets = sorted(predictions, key=_canonical), sorted(golds, key=_canonical)
    edges = [[j for j, gold in enumerate(targets) if compatible(pred, gold)] for pred in preds]
    owner = {}

    def augment(index, visited):
        for target in edges[index]:
            if target in visited:
                continue
            visited.add(target)
            if target not in owner or augment(owner[target], visited):
                owner[target] = index
                return True
        return False

    for index in range(len(preds)):
        augment(index, set())
    return [(preds[owner[j]], targets[j]) for j in sorted(owner)]


def _rates(tp: int, fp: int, fn: int) -> dict:
    precision = tp / max(tp + fp, 1)
    recall = tp / max(tp + fn, 1)
    return {"true_positive": tp, "false_positive": fp, "false_negative": fn,
            "precision": round(precision, 4), "recall": round(recall, 4),
            "f1": round(2 * precision * recall / max(precision + recall, 1e-9), 4)}


def _valid_span(span: dict) -> bool:
    return (isinstance(span.get("start"), int) and not isinstance(span["start"], bool)
            and isinstance(span.get("end"), int) and not isinstance(span["end"], bool)
            and 0 <= span["start"] < span["end"])


def score_detection(predictions: Iterable[Dict[str, Any]], golds: Iterable[Dict[str, Any]],
                    strict_type: bool = True, *, match_mode: str = "legacy_overlap") -> Dict[str, Any]:
    """Legacy span diagnostic. The main error-level metric is score_paper_detection.

    Matching is now maximum one-to-one. Type accuracy is measured after independent
    localization matching, so a wrong type is visible even when strict_type is true.
    """
    if match_mode not in {"legacy_overlap", "exact"}:
        raise ValueError("match_mode must be legacy_overlap or exact")
    preds, targets = list(predictions), list(golds)

    def localized(pred, gold):
        if pred["doc_id"] != gold["doc_id"] or not _valid_span(pred) or not _valid_span(gold):
            return False
        if match_mode == "exact":
            return (pred["start"], pred["end"]) == (gold["start"], gold["end"])
        return pred["start"] < gold["end"] and gold["start"] < pred["end"]

    pairs = _maximum_matching(preds, targets, lambda p, g: localized(p, g) and
                              (not strict_type or p.get("type") == g.get("type")))
    localization = _maximum_matching(preds, targets, localized)
    result = _rates(len(pairs), len(preds) - len(pairs), len(targets) - len(pairs))
    result.update(type_accuracy=round(sum(p.get("type") == g.get("type") for p, g in localization) /
                                      len(localization), 4) if localization else None,
                  localized_matches=len(localization), strict_type=strict_type, match_mode=match_mode)
    return result


def _document_index(records: Iterable[dict], label: str) -> dict[str, dict]:
    indexed = {}
    for record in records:
        key = record.get("document_id", record.get("doc_id"))
        if not isinstance(key, str) or not key:
            raise ValueError(f"{label}: missing document_id")
        if key in indexed:
            raise ValueError(f"{label}: duplicate document_id {key}")
        if not isinstance(record.get("errors", []), list):
            raise ValueError(f"{label}: errors must be a list")
        indexed[key] = record
    return indexed


def _contains_error(pred: dict, gold: dict) -> bool:
    """Every gold span must be contained in an anchored predicted original passage."""
    spans, targets = pred.get("spans", []), gold.get("spans", [])
    if (not isinstance(spans, list) or not isinstance(targets, list) or not spans or not targets
            or any(not isinstance(s, dict) or not _valid_span(s) or not isinstance(s.get("text"), str)
                   or len(s["text"]) != s["end"] - s["start"] for s in spans + targets)):
        return False
    return all(any(p["start"] <= g["start"] and p["end"] >= g["end"]
                   and isinstance(p.get("text"), str) and isinstance(g.get("text"), str)
                   and p["text"][g["start"] - p["start"]:g["end"] - p["start"]] == g["text"]
                   for p in spans) for g in targets)


def score_paper_detection(predictions: Iterable[dict], golds: Iterable[dict]) -> Dict[str, Any]:
    """Error-level typed containment score with deterministic one-to-one matching.

    Each document contains errors, each error contains one or more original spans.
    All spans of one gold error must be covered to count one TP. This implementation
    is an explicit local paper-style protocol, not an assertion of official parity.
    Unscorable gold stays in the audit count; it is never silently converted to TP.
    """
    pred_docs, gold_docs = _document_index(predictions, "predictions"), _document_index(golds, "golds")
    tp = fp = fn = excluded = localized = type_hits = 0
    by_document = []
    for doc_id in sorted(set(pred_docs) | set(gold_docs)):
        preds = pred_docs.get(doc_id, {}).get("errors", [])
        all_golds = gold_docs.get(doc_id, {}).get("errors", [])
        targets = [g for g in all_golds if g.get("scorable", True)]
        excluded += len(all_golds) - len(targets)
        pairs = _maximum_matching(preds, targets, lambda p, g: _contains_error(p, g) and
                                  p.get("error_type", p.get("type")) == g.get("type", g.get("error_type")))
        locations = _maximum_matching(preds, targets, _contains_error)
        localized += len(locations)
        type_hits += sum(p.get("error_type", p.get("type")) == g.get("type", g.get("error_type"))
                         for p, g in locations)
        counts = _rates(len(pairs), len(preds) - len(pairs), len(targets) - len(pairs))
        tp += counts["true_positive"]
        fp += counts["false_positive"]
        fn += counts["false_negative"]
        by_document.append({"document_id": doc_id, **counts, "excluded_gold_errors": len(all_golds) - len(targets)})
    return {**_rates(tp, fp, fn), "type_accuracy": round(type_hits / localized, 4) if localized else None,
            "localized_matches": localized, "excluded_gold_errors": excluded,
            "missing_prediction_documents": sorted(set(gold_docs) - set(pred_docs)),
            "unknown_prediction_documents": sorted(set(pred_docs) - set(gold_docs)),
            "match_mode": "paper_style_typed_all_spans_contained_maximum_one_to_one",
            "by_document": by_document}


def gold_spans(rows: Sequence[Dict[str, Any]], doc_id_key: str = "title") -> List[Dict[str, Any]]:
    """把标注转成统一的金标准 span（自动重新锚定）。"""
    golds: List[Dict[str, Any]] = []
    for index, row in enumerate(rows):
        doc_id = f"{index}:{row.get(doc_id_key, '')[:40]}"
        for error in row["errors"]:
            for start, span in zip(error.get("start_idx") or [], error.get("error_span") or []):
                located = locate(row["content"], span, start)
                if located is None:
                    warnings.warn(f"Excluded unanchored legacy gold span in {doc_id}; use dataset_prepare for audit", stacklevel=2)
                    continue
                golds.append({"doc_id": doc_id, "start": located[0], "end": located[1],
                              "type": error["error_type"]})
    return golds


def selftest() -> int:
    rows = [{
        "scene": "个股研报",
        "title": "自测样例",
        "content": "公司2025年营业收入8,420万元，同比增长18.6%。",
        "errors": [
            {"start_idx": [2], "error_span": ["2025年营业收入8,420万元"], "error_type": "数值单位错误"},
            {"start_idx": [999], "error_span": ["同比增长18.6%"], "error_type": "数值不一致错误"},
        ],
    }]
    report = check_dataset(rows)
    assert report["errors"] == 2, report
    assert report["offset_ok"] == 1, report
    assert report["reanchored"] == 1, report
    golds = gold_spans(rows)
    assert len(golds) == 2, golds
    perfect = score_detection(golds, golds)
    assert perfect["f1"] == 1.0, perfect
    shifted = [dict(item, start=item["start"] + 50, end=item["end"] + 50) for item in golds[:1]]
    partial = score_detection(shifted, golds)
    assert partial["recall"] < 1.0, partial
    wrong_type = [dict(item, type="其它类型") for item in golds]
    strict = score_detection(wrong_type, golds, strict_type=True)
    loose = score_detection(wrong_type, golds, strict_type=False)
    assert strict["f1"] < loose["f1"], (strict, loose)
    print("自测通过：重新锚定、完美评分、位移降分、类型严格性均符合预期")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="FinED-Bench 体检与评分工具")
    parser.add_argument("--data", help="FinED-Bench-main 目录")
    parser.add_argument("--out", default="evals/reports", help="报告输出目录")
    parser.add_argument("--selftest", action="store_true", help="运行内置自测")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    if not args.data:
        parser.print_help()
        return 1
    root = pathlib.Path(args.data)
    out_dir = pathlib.Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary: Dict[str, Any] = {}
    for name in ("fined_bench/eval_data.json", "fined_bench/eval_data_hard.json"):
        path = root / name
        if not path.exists():
            continue
        rows = json.loads(path.read_text(encoding="utf-8"))
        report = check_dataset(rows)
        key = path.stem
        summary[key] = report
        if key == "eval_data":
            subset = [r for r in rows if r["scene"] in ("行业研报", "个股研报")]
            summary["eval_data_研报子集"] = check_dataset(subset)
        print(f"\n=== {name}")
        print(f"  文档 {report['documents']}，错误 {report['errors']}")
        print(f"  偏移可用 {report['offset_ok']}，对不上 {report['offset_bad']}"
              f"（可重新锚定 {report['reanchored']}，无法还原 {report['unresolved']}）")
        print(f"  重叠 {report['overlap']}，重复标注 {report['duplicate_groups']} 组，"
              f"含表格文档 {report['table_docs']}，含数值+单位文档 {report['number_unit_docs']}")
        print("  错误类型前五：" + "，".join(
            f"{k}({v})" for k, v in list(report["by_type"].items())[:5]))
    report_path = out_dir / "fined_bench_integrity.json"
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告已写入 {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
