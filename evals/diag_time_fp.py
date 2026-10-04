# -*- coding: utf-8 -*-
"""诊断时间信息非法 FP：span 粒度匹配 vs 真误报。"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "evals"), str(ROOT / "factcheck/src")]
from fined_bench_eval import _valid_span
from yjcheck.text_review import _verified_rules

GOLD = ROOT / "data/v2/dataset/gold.dev.jsonl"
INPUTS = ROOT / "data/v2/dataset/inputs.dev.jsonl"


def main() -> int:
    gold = {}
    for l in GOLD.read_text(encoding="utf-8").splitlines():
        g = json.loads(l)
        gold[g["document_id"]] = g["errors"]
    rows = [json.loads(l) for l in INPUTS.read_text(encoding="utf-8").splitlines()]
    by_id = {r["doc_id"]: r for r in rows}

    for suffix in ("000322", "000407"):
        did = next(d for d in by_id if d.endswith(suffix))
        content = by_id[did]["content"]
        preds = [e for e in _verified_rules(content) if e["error_type"] == "时间信息非法"]
        golds = [e for e in gold[did] if e.get("scorable", True) and e.get("type") == "时间信息非法"]
        print(f"=== {suffix} | preds={len(preds)} golds={len(golds)}")
        for e in golds:
            print("  GOLD:", [(s["start"], s["end"], s["text"][:40]) for s in e.get("spans") or [] if _valid_span(s)])
        for e in preds:
            print("  PRED:", e["detector_id"], [(s["start"], s["end"], s["text"][:40]) for s in e["spans"]])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())