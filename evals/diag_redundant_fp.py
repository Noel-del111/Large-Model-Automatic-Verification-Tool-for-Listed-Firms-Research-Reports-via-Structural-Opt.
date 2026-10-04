# -*- coding: utf-8 -*-
"""诊断冗余规则 FP 的具体形态：区分"真冗余但匹配失败"与"假冗余(顶真等)"。"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "evals"), str(ROOT / "factcheck/src")]
from fined_bench_eval import _contains_error, _valid_span
from yjcheck.text_review import _redundant_duplicate_rules

GOLD = ROOT / "data/v2/dataset/gold.dev.jsonl"
INPUTS = ROOT / "data/v2/dataset/inputs.dev.jsonl"


def main() -> int:
    gold = {}
    for line in GOLD.read_text(encoding="utf-8").splitlines():
        g = json.loads(line)
        gold[g["document_id"]] = g["errors"]
    rows = [json.loads(l) for l in INPUTS.read_text(encoding="utf-8").splitlines()]
    by_id = {r["doc_id"]: r for r in rows}

    for suffix in ("000369", "000315", "000320"):
        did = next(d for d in by_id if d.endswith(suffix))
        content = by_id[did]["content"]
        preds = _redundant_duplicate_rules(content)
        golds = [e for e in gold[did] if e.get("scorable", True) and e.get("type") == "冗余语句"]
        print(f"=== {suffix} | preds={len(preds)} golds={len(golds)}")
        for e in golds:
            print("  GOLD:", [s["text"][:50] for s in e.get("spans") or []])
        for e in preds:
            print("  PRED:", e["spans"][0]["text"][:60], "|", e["detector_id"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())