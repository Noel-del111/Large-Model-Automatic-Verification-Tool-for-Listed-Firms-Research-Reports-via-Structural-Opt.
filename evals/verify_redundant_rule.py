# -*- coding: utf-8 -*-
"""离线验证冗余重复检测规则对 dev 研报的 TP/FP/FN（零模型调用）。

对比两种"规则"口径的冗余语句 P/R/F1：
1. 基线：仅当前 intrinsic 规则（check_intrinsic_consistency，不含冗余检测）→ 冗余语句 R≈0
2. 新增：_redundant_duplicate_rules 的字面重复检测

金标匹配沿用 fined_bench_eval 的错误实例级、类型一致、一对一最大匹配。
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "evals"), str(ROOT / "factcheck/src")]
from fined_bench_eval import _contains_error, _maximum_matching, _valid_span
from yjcheck.text_review import _redundant_duplicate_rules

INPUTS = ROOT / "data/v2/dataset/inputs.dev.jsonl"
GOLD = ROOT / "data/v2/dataset/gold.dev.jsonl"
RESEARCH_SCENES = {"个股研报", "行业研报"}


def pr(tp, fp, fn):
    p = tp / max(tp + fp, 1)
    r = tp / max(tp + fn, 1)
    return round(p * 100, 1), round(r * 100, 1), round(2 * p * r / max(p + r, 1e-9) * 100, 1)


def main() -> int:
    gold = {}
    for line in GOLD.read_text(encoding="utf-8").splitlines():
        g = json.loads(line)
        gold[g["document_id"]] = g["errors"]

    tp = fp = fn = 0
    per_scene = Counter()
    fp_samples = []
    for line in INPUTS.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("scene") not in RESEARCH_SCENES:
            continue
        did, content = row["doc_id"], row["content"]
        preds = [e for e in _redundant_duplicate_rules(content)]
        golds = [{"error_type": e.get("type", ""), "spans": [s for s in e.get("spans") or [] if _valid_span(s)]}
                 for e in gold.get(did, []) if e.get("scorable", True) and e.get("type") == "冗余语句"]
        pairs = _maximum_matching(preds, golds, lambda a, b: _contains_error(a, b))
        tp += len(pairs)
        fp += len(preds) - len(pairs)
        fn += len(golds) - len(pairs)
        if len(preds) - len(pairs) > 0:
            for p in preds:
                if all(p is not pp for pp, _ in pairs):
                    fp_samples.append((did[-6:], p["spans"][0]["text"][:70]))

    p, r, f1 = pr(tp, fp, fn)
    print(f"冗余语句规则检测(dev 研报 {sum(1 for l in INPUTS.read_text(encoding='utf-8').splitlines() if json.loads(l).get('scene') in RESEARCH_SCENES)} 篇):")
    print(f"  TP={tp} FP={fp} FN={fn}  P={p}% R={r}% F1={f1}%")
    print(f"\n基线(旧 intrinsic,无冗余规则): 冗余语句 R≈0(完全未覆盖)")
    print(f"\nFP 样例(前 10, 供排查误报形态):")
    for did, text in fp_samples[:10]:
        print(f"  [{did}] {text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())