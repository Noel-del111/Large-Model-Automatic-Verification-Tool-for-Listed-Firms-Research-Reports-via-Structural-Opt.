# -*- coding: utf-8 -*-
"""查看时间信息非法与格式错误金标剩余形态，确定可规则化的确定性模式。"""
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "data/v2/dataset/gold.dev.jsonl"
INPUTS = ROOT / "data/v2/dataset/inputs.dev.jsonl"
RESEARCH = {"个股研报", "行业研报"}

# 时间信息非法候选形态
TIME_SHAPES = {
    "月份>12(13月)": re.compile(r"20\d{2}年\s*(?:1[3-9]|[2-9]\d)月"),
    "日>31": re.compile(r"月\s*(?:3[2-9]|[4-9]\d)日"),
}
# 格式错误候选形态
FMT_SHAPES = {
    "千分位错(X,XX,XX)": re.compile(r"\d{1,3}(?:,\d{1,2}){2,}"),
    "列表编号混用(（1）…（二）)": re.compile(r"[（(][0-9一二三四五六七八九十]+[）)]"),
}


def main() -> int:
    gold = {}
    for l in GOLD.read_text(encoding="utf-8").splitlines():
        g = json.loads(l)
        gold[g["document_id"]] = g["errors"]
    scenes = {r["doc_id"]: r["scene"] for r in
              (json.loads(l) for l in INPUTS.read_text(encoding="utf-8").splitlines())}

    for target, shapes in (("时间信息非法", TIME_SHAPES), ("格式错误", FMT_SHAPES)):
        print(f"\n===== {target} =====")
        total = 0
        hits = Counter()
        samples = {k: [] for k in shapes}
        for did, errs in gold.items():
            if scenes.get(did) not in RESEARCH:
                continue
            for e in errs:
                if not e.get("scorable", True) or e.get("type") != target:
                    continue
                total += 1
                for s in e.get("spans") or []:
                    txt = s.get("text", "")
                    for name, pattern in shapes.items():
                        if pattern.search(txt):
                            hits[name] += 1
                            samples[name].append(txt[:55])
        print(f"  金标总数: {total}")
        for name in shapes:
            print(f"  {name}: {hits[name]} 条")
            for x in samples[name][:6]:
                print(f"      {x}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())