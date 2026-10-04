# -*- coding: utf-8 -*-
"""提取 arm_new2 中被误报为'冗余语句'的 FP 候选原文（离线，零调用）。

用于定位负例 #6（法律条文）过度泛化的具体形态，指导替换负例的选择。
"""
from __future__ import annotations

import json
import glob
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "evals")]
from fined_bench_eval import _contains_error, _maximum_matching, _valid_span

PILOT = ROOT / "data/v2/dataset/inputs.pilot24.jsonl"
GOLD = ROOT / "data/v2/dataset/gold.dev.jsonl"
PREDS = ROOT / "data/v2/fewshot-pilot/arm-new2/predictions/model_direct"


def main() -> int:
    gold = {}
    for line in GOLD.read_text(encoding="utf-8").splitlines():
        g = json.loads(line)
        gold[g["document_id"]] = g["errors"]
    pilot_ids = {json.loads(line)["doc_id"] for line in PILOT.read_text(encoding="utf-8").splitlines()}
    contents = {json.loads(line)["doc_id"]: json.loads(line)["content"]
                for line in (ROOT / "data/v2/dataset/inputs.dev.jsonl").read_text(encoding="utf-8").splitlines()}

    def can(e):
        return {"error_type": e.get("error_type") or e.get("type", ""),
                "spans": [s for s in (e.get("spans") or []) if _valid_span(s)]}

    print("=== arm_new2 中 error_type=冗余语句 的候选（含 TP/FP 判定）===")
    for f in sorted(glob.glob(str(PREDS / "*.json"))):
        r = json.loads(open(f, encoding="utf-8").read())
        did = r["document_id"]
        if did not in pilot_ids:
            continue
        preds = [can(e) for e in r.get("errors", []) if e.get("spans")]
        golds = [can(e) for e in gold.get(did, []) if e.get("scorable", True)]
        for p in preds:
            if p["error_type"] != "冗余语句":
                continue
            pp = [p]
            gg = [g for g in golds if g["error_type"] == "冗余语句"]
            pairs = _maximum_matching(pp, gg, lambda a, b: _contains_error(a, b))
            is_tp = len(pairs) > 0
            text = p["spans"][0]["text"] if p["spans"] else ""
            reason = next((e.get("reason", "") for e in r.get("errors", [])
                           if (e.get("spans") and e["spans"][0].get("text") == text)), "")
            print(f"[{'TP' if is_tp else 'FP'}] {did[-6:]} | {text[:90]} | reason={reason[:40]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())