# -*- coding: utf-8 -*-
"""对比 model_direct vs hybrid 的规则净增益来源（按规则类型统计新增 TP/FP）。"""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "evals"), str(ROOT / "factcheck/src")]
from fined_bench_eval import _contains_error, _maximum_matching, _valid_span

BASE = ROOT / "data/v2/hybrid-eval/arm-research50/predictions"
GOLD = ROOT / "data/v2/dataset/gold.eval_oct05.jsonl"


def load(detector):
    out = {}
    for f in sorted((BASE / detector).glob("*.json")):
        r = json.loads(f.read_text(encoding="utf-8"))
        out[r["document_id"]] = r.get("errors", [])
    return out


def main() -> int:
    gold = {}
    for l in GOLD.read_text(encoding="utf-8").splitlines():
        g = json.loads(l)
        gold[g["document_id"]] = g["errors"]

    direct = load("model_direct")
    hybrid = load("hybrid")

    by_detector_tp = Counter()
    by_detector_fp = Counter()
    for did in sorted(direct):
        d_ids = {e.get("id"): e for e in direct[did]}
        h_ids = {e.get("id"): e for e in hybrid[did]}
        # hybrid 相对 direct 新增的 finding（规则贡献）
        new_hybrid = [e for eid, e in h_ids.items() if eid not in d_ids]
        golds = [{"error_type": e.get("type", ""), "spans": [s for s in e.get("spans") or [] if _valid_span(s)]}
                 for e in gold.get(did, []) if e.get("scorable", True)]
        for e in new_hybrid:
            det = e.get("detector_id", "")
            # 只统计规则贡献（非 hybrid.model）
            if det.startswith("hybrid.model") or det.startswith("model_direct"):
                continue
            p = {"error_type": e["error_type"], "spans": [s for s in e.get("spans", []) if _valid_span(s)]}
            gg = [g for g in golds if g["error_type"] == e["error_type"]]
            pairs = _maximum_matching([p], gg, lambda a, b: _contains_error(a, b))
            if pairs:
                by_detector_tp[det] += 1
            else:
                by_detector_fp[det] += 1

    print("规则贡献的新增 TP（按 detector）：")
    for det, n in by_detector_tp.most_common():
        print(f"  {det}: {n}")
    print("规则贡献的新增 FP（按 detector）：")
    for det, n in by_detector_fp.most_common():
        print(f"  {det}: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())