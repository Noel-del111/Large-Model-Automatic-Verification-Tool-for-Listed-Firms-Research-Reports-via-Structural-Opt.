"""FinED-Bench 体检与评分工具。

FinED-Bench（arXiv:2608.12342）给每个错误标注了 ``start_idx`` 与 ``error_span``，
但实测发现偏移并不可靠：长文档集里 60% 的 span 与 content 对不上。
本工具做三件事：

1. 体检：校验偏移可用性、重叠、重复、可重新锚定的比例；
2. 重新锚定：把对不上的 span 用精确匹配 + 模糊匹配找回正确位置；
3. 评分：对任意检测器输出计算 span 级 precision / recall / F1、类型准确率，
   并支持"只看定位不看类型"的宽松口径，便于和论文的检测口径对齐。

用法：
    py -3 evals/fined_bench_eval.py --data <FinED-Bench-main 目录> --out evals/reports
    py -3 evals/fined_bench_eval.py --selftest
"""

from __future__ import annotations

import argparse
import difflib
import json
import pathlib
import re
import sys
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

REPORT_KEYS = ("documents", "errors", "offset_ok", "offset_bad", "reanchored",
               "unresolved", "overlap", "duplicate_groups", "table_docs",
               "number_unit_docs", "by_type", "by_scene")


def normalize(text: str) -> str:
    """比较与再锚定用的归一化：压缩空白、统一全角空格。"""
    return re.sub(r"\s+", "", text.replace("\u3000", " "))


def locate(content: str, span: str, start: int) -> Optional[Tuple[int, int]]:
    """返回 span 在 content 中的真实区间。

    三级策略：按标注偏移直接命中 → 精确子串搜索 → 在偏移附近模糊匹配。
    """
    if content[start:start + len(span)] == span:
        return start, start + len(span)
    found = content.find(span)
    if found >= 0:
        return found, found + len(span)
    flat_content = normalize(content)
    flat_span = normalize(span)
    if not flat_span:
        return None
    # 归一化后仍在，说明只是空白差异：按归一化位置映射回原串
    if flat_span in flat_content:
        index = flat_content.index(flat_span)
        mapping = [i for i, ch in enumerate(content) if not ch.isspace()]
        begin = mapping[index]
        end = mapping[min(index + len(flat_span) - 1, len(mapping) - 1)]
        return begin, end + 1
    window = content[max(0, start - 200): start + len(span) + 200]
    matcher = difflib.SequenceMatcher(None, window, span)
    match = matcher.find_longest_match(0, len(window), 0, len(span))
    if match.size >= max(8, int(len(span) * 0.6)):
        begin = max(0, start - 200) + match.a
        return begin, begin + match.size
    return None


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
            report["by_type"][error["error_type"]] += 1
            starts = error.get("start_idx") or []
            spans = error.get("error_span") or []
            duplicates[(tuple(starts), tuple(spans), error["error_type"])] += 1
            for start, span in zip(starts, spans):
                if content[start:start + len(span)] == span:
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


def score_detection(predictions: Iterable[Dict[str, Any]],
                    golds: Iterable[Dict[str, Any]],
                    strict_type: bool = True) -> Dict[str, Any]:
    """span 级评分。

    prediction: {"doc_id": str, "start": int, "end": int, "type": str}
    gold:       {"doc_id": str, "start": int, "end": int, "type": str}

    命中规则：同一文档内区间有重叠即算命中；strict_type=True 时还要求类型一致。
    """
    preds_by_doc: Dict[str, List[Dict[str, Any]]] = {}
    for item in predictions:
        preds_by_doc.setdefault(item["doc_id"], []).append(item)
    gold_by_doc: Dict[str, List[Dict[str, Any]]] = {}
    for item in golds:
        gold_by_doc.setdefault(item["doc_id"], []).append(item)

    true_positive = false_positive = false_negative = 0
    type_hit = type_total = 0
    for doc_id, gold_items in gold_by_doc.items():
        remaining = list(gold_items)
        for pred in preds_by_doc.get(doc_id, []):
            match = None
            for gold in remaining:
                if pred["start"] < gold["end"] and gold["start"] < pred["end"]:
                    if strict_type and pred.get("type") != gold.get("type"):
                        continue
                    match = gold
                    break
            if match is None:
                false_positive += 1
                continue
            true_positive += 1
            remaining.remove(match)
            type_total += 1
            type_hit += int(pred.get("type") == match.get("type"))
        false_negative += len(remaining)
    for doc_id, items in preds_by_doc.items():
        if doc_id not in gold_by_doc:
            false_positive += len(items)
    precision = true_positive / max(true_positive + false_positive, 1)
    recall = true_positive / max(true_positive + false_negative, 1)
    f1 = 2 * precision * recall / max(precision + recall, 1e-9)
    return {
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "type_accuracy": round(type_hit / type_total, 4) if type_total else None,
        "strict_type": strict_type,
    }


def gold_spans(rows: Sequence[Dict[str, Any]], doc_id_key: str = "title") -> List[Dict[str, Any]]:
    """把标注转成统一的金标准 span（自动重新锚定）。"""
    golds: List[Dict[str, Any]] = []
    for index, row in enumerate(rows):
        doc_id = f"{index}:{row.get(doc_id_key, '')[:40]}"
        for error in row["errors"]:
            for start, span in zip(error.get("start_idx") or [], error.get("error_span") or []):
                located = locate(row["content"], span, start)
                if located is None:
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
