# -*- coding: utf-8 -*-
"""离线验证新增确定性规则(冗余重复/空占位符/悬空标点)对 dev 研报的 TP/FP/FN。"""
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "evals"), str(ROOT / "factcheck/src")]
from fined_bench_eval import _contains_error, _maximum_matching, _valid_span
from yjcheck.text_review import (_redundant_duplicate_rules, _empty_placeholder_rules,
                                 _suspended_punctuation_rules, _numeric_gap_rules,
                                 _stock_code_gap_rules)

INPUTS = ROOT / "data/v2/dataset/inputs.dev.jsonl"
GOLD = ROOT / "data/v2/dataset/gold.dev.jsonl"
RESEARCH = {"个股研报", "行业研报"}

RULES = {
    "冗余语句": _redundant_duplicate_rules,
    "属性值缺失错误": _empty_placeholder_rules,
    "金融要素缺失": _suspended_punctuation_rules,
    "数值缺失": _numeric_gap_rules,
    "格式错误": _stock_code_gap_rules,
}


def pr(tp, fp, fn):
    p = tp / max(tp + fp, 1)
    r = tp / max(tp + fn, 1)
    return round(p * 100, 1), round(r * 100, 1), round(2 * p * r / max(p + r, 1e-9) * 100, 1)


def main() -> int:
    gold = {}
    for line in GOLD.read_text(encoding="utf-8").splitlines():
        g = json.loads(line)
        gold[g["document_id"]] = g["errors"]
    rows = [json.loads(l) for l in INPUTS.read_text(encoding="utf-8").splitlines()]
    research_rows = [r for r in rows if r.get("scene") in RESEARCH]

    total_tp = total_fp = total_fn = 0
    for kind, rule_fn in RULES.items():
        tp = fp = fn = 0
        fp_samples = []
        for row in research_rows:
            did, content = row["doc_id"], row["content"]
            preds = rule_fn(content)
            golds = [{"error_type": e.get("type", ""), "spans": [s for s in e.get("spans") or [] if _valid_span(s)]}
                     for e in gold.get(did, []) if e.get("scorable", True) and e.get("type") == kind]
            pairs = _maximum_matching(preds, golds, lambda a, b: _contains_error(a, b))
            tp += len(pairs)
            fp += len(preds) - len(pairs)
            fn += len(golds) - len(pairs)
            for p in preds:
                if all(p is not pp for pp, _ in pairs):
                    fp_samples.append((did[-6:], p["spans"][0]["text"][:60]))
        p, r, f1 = pr(tp, fp, fn)
        total_tp += tp; total_fp += fp; total_fn += fn
        gold_n = sum(1 for row in research_rows for e in gold.get(row["doc_id"], [])
                     if e.get("scorable", True) and e.get("type") == kind)
        print(f"{kind}: 金标{gold_n}  TP={tp} FP={fp} FN={fn}  P={p}% R={r}% F1={f1}%")
        for did, text in fp_samples[:5]:
            print(f"    FP [{did}] {text}")
    p, r, f1 = pr(total_tp, total_fp, total_fn)
    print(f"\n三规则合计: TP={total_tp} FP={total_fp} FN={total_fn}  P={p}% R={r}% F1={f1}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())