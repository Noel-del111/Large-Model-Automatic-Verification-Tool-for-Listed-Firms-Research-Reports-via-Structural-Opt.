# -*- coding: utf-8 -*-
"""查看 dev 研报里低 R 类型(格式错误/金融要素缺失/数值缺失/属性值缺失)的金标样本形态。"""
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "data/v2/dataset/gold.dev.jsonl"
INPUTS = ROOT / "data/v2/dataset/inputs.dev.jsonl"
RESEARCH = {"个股研报", "行业研报"}


def main() -> int:
    gold = {}
    for line in GOLD.read_text(encoding="utf-8").splitlines():
        g = json.loads(line)
        gold[g["document_id"]] = g["errors"]
    rows = [json.loads(l) for l in INPUTS.read_text(encoding="utf-8").splitlines()]
    scenes = {r["doc_id"]: r["scene"] for r in rows}

    for target_type in ("格式错误", "金融要素缺失", "属性值缺失错误", "数值缺失"):
        samples = []
        for did, errs in gold.items():
            if scenes.get(did) not in RESEARCH:
                continue
            for e in errs:
                if e.get("scorable", True) and e.get("type") == target_type:
                    for s in e.get("spans") or []:
                        if s.get("text"):
                            samples.append(s["text"])
        print(f"\n=== {target_type} (金标 {len(samples)} 条) 样本前 15 条 ===")
        for s in samples[:15]:
            print(f"  {repr(s[:90])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())