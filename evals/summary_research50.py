# -*- coding: utf-8 -*-
"""汇总 arm-research50 的三组 detector、三个 scope 指标。"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
S = json.loads((ROOT / "data/v2/hybrid-eval/arm-research50/score.json").read_text(encoding="utf-8"))
D = S["detectors"]


def show(v):
    p = v["precision"] * 100
    r = v["recall"] * 100
    f1 = v["f1"] * 100
    ta = v.get("type_accuracy")
    ta = (ta * 100 if ta is not None else 0)
    return f"TP={v['true_positive']} FP={v['false_positive']} FN={v['false_negative']} | P={p:.2f}% R={r:.2f}% F1={f1:.2f}% | 类型准确率={ta:.2f}%"


for det in ("model_direct", "hybrid"):
    d = D[det]
    print(f"== {det} ==")
    for scope in ("candidate_detection", "all_review_hints_detection", "verified_detection"):
        if scope in d:
            print(f"  [{scope}] {show(d[scope])}")

# hybrid 相对 model_direct 的净增益（候选口径）
md = D["model_direct"]["candidate_detection"]
hy = D["hybrid"]["candidate_detection"]
print("\n== hybrid 相对 model_direct 净增益（候选口径）==")
print(f"  TP {md['true_positive']}→{hy['true_positive']} (+{hy['true_positive']-md['true_positive']})")
print(f"  FP {md['false_positive']}→{hy['false_positive']} (+{hy['false_positive']-md['false_positive']})")
print(f"  FN {md['false_negative']}→{hy['false_negative']} ({hy['false_negative']-md['false_negative']})")
print(f"  P {(hy['precision']-md['precision'])*100:+.2f}pp  R {(hy['recall']-md['recall'])*100:+.2f}pp  F1 {(hy['f1']-md['f1'])*100:+.2f}pp")
