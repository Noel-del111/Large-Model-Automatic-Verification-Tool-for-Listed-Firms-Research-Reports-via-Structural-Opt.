# -*- coding: utf-8 -*-
"""离线验证时间信息非法规则(calendar+月份>12+日>31)对 dev 研报的 TP/FP/FN。"""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "evals"), str(ROOT / "factcheck/src")]
from fined_bench_eval import _contains_error, _maximum_matching, _valid_span
from yjcheck.text_review import _verified_rules, _deduplicate

INPUTS = ROOT / "data/v2/dataset/inputs.dev.jsonl"
GOLD = ROOT / "data/v2/dataset/gold.dev.jsonl"
RESEARCH = {"个股研报", "行业研报"}


def pr(tp, fp, fn):
    p = tp / max(tp + fp, 1)
    r = tp / max(tp + fn, 1)
    return round(p * 100, 1), round(r * 100, 1), round(2 * p * r / max(p + r, 1e-9) * 100, 1)


def main() -> int:
    gold = {}
    for l in GOLD.read_text(encoding="utf-8").splitlines():
        g = json.loads(l)
        gold[g["document_id"]] = g["errors"]
    rows = [json.loads(l) for l in INPUTS.read_text(encoding="utf-8").splitlines()]
    research = [r for r in rows if r.get("scene") in RESEARCH]

    tp = fp = fn = 0
    fp_samples = []
    by_detector = Counter()
    for row in research:
        did, content = row["doc_id"], row["content"]
        preds = _deduplicate([e for e in _verified_rules(content) if e["error_type"] == "时间信息非法"], did)
        golds = [{"error_type": e.get("type", ""), "spans": [s for s in e.get("spans") or [] if _valid_span(s)]}
                 for e in gold.get(did, []) if e.get("scorable", True) and e.get("type") == "时间信息非法"]
        pairs = _maximum_matching(preds, golds, lambda a, b: _contains_error(a, b))
        tp += len(pairs)
        fp += len(preds) - len(pairs)
        fn += len(golds) - len(pairs)
        for p in preds:
            if all(p is not pp for pp, _ in pairs):
                fp_samples.append((did[-6:], p["spans"][0]["text"][:40]))
                by_detector[p["detector_id"]] += 1
        for pp, _ in pairs:
            by_detector[pp["detector_id"]] += 1
    p, r, f1 = pr(tp, fp, fn)
    gold_n = sum(1 for row in research for e in gold.get(row["doc_id"], [])
                 if e.get("scorable", True) and e.get("type") == "时间信息非法")
    print(f"时间信息非法: 金标{gold_n}  TP={tp} FP={fp} FN={fn}  P={p}% R={r}% F1={f1}%")
    print(f"按 detector: {dict(by_detector)}")
    for did, text in fp_samples[:8]:
        print(f"  FP [{did}] {text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())