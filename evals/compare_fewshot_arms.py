# -*- coding: utf-8 -*-
"""对比 two few-shot pilot 臂的逐类型 P/R/F1（离线，零调用）。

usage: python evals/compare_fewshot_arms.py
读取 data/v2/fewshot-pilot/arm-{old,new}/predictions/model_direct 与 pilot24 金标，
输出逐类型对比，定位新 few-shot 的增益/损失来源。
"""
from __future__ import annotations

import json
import glob
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "evals")]
from fined_bench_eval import _contains_error, _maximum_matching, _valid_span

PILOT = ROOT / "data/v2/dataset/inputs.pilot24.jsonl"
GOLD = ROOT / "data/v2/dataset/gold.dev.jsonl"


def load_preds(path: str) -> dict:
    out = {}
    for f in sorted(glob.glob(str(ROOT / path / "*.json"))):
        r = json.loads((ROOT / f).read_text(encoding="utf-8")) if False else json.loads(open(f, encoding="utf-8").read())
        out[r["document_id"]] = r.get("errors", [])
    return out


def can(e: dict) -> dict:
    return {"error_type": e.get("error_type") or e.get("type", ""),
            "spans": [s for s in (e.get("spans") or []) if _valid_span(s)]}


def pr(tp, fp, fn):
    p = tp / max(tp + fp, 1)
    r = tp / max(tp + fn, 1)
    return round(p * 100, 1), round(r * 100, 1), round(2 * p * r / max(p + r, 1e-9) * 100, 1)


def main() -> int:
    gold = {}
    for line in GOLD.read_text(encoding="utf-8").splitlines():
        g = json.loads(line)
        gold[g["document_id"]] = g["errors"]
    pilot_ids = {json.loads(line)["doc_id"] for line in PILOT.read_text(encoding="utf-8").splitlines()}

    def per_type(preds):
        res = defaultdict(lambda: [0, 0, 0])
        for did in pilot_ids:
            p = [can(e) for e in preds.get(did, []) if e.get("spans")]
            g = [can(e) for e in gold.get(did, []) if e.get("scorable", True)]
            types = {x["error_type"] for x in p} | {x["error_type"] for x in g}
            for t in types:
                pp = [x for x in p if x["error_type"] == t]
                gg = [x for x in g if x["error_type"] == t]
                pairs = _maximum_matching(pp, gg, lambda a, b: _contains_error(a, b))
                res[t][0] += len(pairs)
                res[t][1] += len(pp) - len(pairs)
                res[t][2] += len(gg) - len(pairs)
        return res

    arms = {
        "old(3正例)": per_type(load_preds("data/v2/fewshot-pilot/arm-old/predictions/model_direct")),
        "new(数字负例)": per_type(load_preds("data/v2/fewshot-pilot/arm-new/predictions/model_direct")),
        "new2(形态负例)": per_type(load_preds("data/v2/fewshot-pilot/arm-new2/predictions/model_direct")),
        "new3(总-分负例)": per_type(load_preds("data/v2/fewshot-pilot/arm-new3/predictions/model_direct")),
    }
    all_types = sorted(set().union(*[set(a) for a in arms.values()]))
    print(f"{'错误类型':<12} {'old':>14} {'new':>14} {'new2':>14} {'new3':>14} {'ΔF1(new3-new2)':>14}")
    for t in all_types:
        vals = [pr(*a.get(t, [0, 0, 0])) for a in arms.values()]
        delta = vals[3][2] - vals[2][2]
        flag = "  <==" if abs(delta) >= 3 else ""
        line = f"{t:<12} "
        for v in vals:
            line += f"{v[0]:>3}/{v[1]:>3}/{v[2]:>3}   "
        line += f"{delta:+6.1f}{flag}"
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())