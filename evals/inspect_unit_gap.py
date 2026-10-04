# -*- coding: utf-8 -*-
"""统计数值缺失金标中"单位空槽"各形态的覆盖，指导单位空槽规则设计。"""
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "data/v2/dataset/gold.dev.jsonl"
INPUTS = ROOT / "data/v2/dataset/inputs.dev.jsonl"
RESEARCH = {"个股研报", "行业研报"}

UNITS = r"亿元|万元|千万元|元|吨|桶|只|辆|人次|倍|名|位|户|家"
# 各形态正则
SHAPES = {
    "约+单位": re.compile(rf"约\s*(?:{UNITS})(?=[，。；、\s]|$)"),
    "空格+单位": re.compile(rf"(?<![0-9.])\s(?:{UNITS})(?=[，。；、\s]|$)"),
    "达/至+单位": re.compile(rf"(?:达|达到|增至|降至|升至)\s*(?:{UNITS})(?=[，。；、\s]|$)"),
    "名词+单位(营收元/金吨)": re.compile(rf"(?:营收|产量|销量|库存|销售额|规模|金额|市值|收入|利润)[元吨只辆]"),
    "减少/增长+单位": re.compile(rf"(?:减少|增长|下降|上升|下滑|回落|增加|提升)\s*(?:{UNITS})(?=[，。；、\s]|$)"),
}


def main() -> int:
    gold = {}
    for l in GOLD.read_text(encoding="utf-8").splitlines():
        g = json.loads(l)
        gold[g["document_id"]] = g["errors"]
    scenes = {r["doc_id"]: r["scene"] for r in
              (json.loads(l) for l in INPUTS.read_text(encoding="utf-8").splitlines())}

    samples = {k: [] for k in SHAPES}
    matched = Counter()
    total = 0
    for did, errs in gold.items():
        if scenes.get(did) not in RESEARCH:
            continue
        for e in errs:
            if not e.get("scorable", True) or e.get("type") != "数值缺失":
                continue
            total += 1
            texts = [s.get("text", "") for s in e.get("spans") or []]
            hit = False
            for name, pattern in SHAPES.items():
                for txt in texts:
                    if pattern.search(txt):
                        matched[name] += 1
                        samples[name].append(txt[:50])
                        hit = True
                        break
    print(f"数值缺失金标(研报) 总: {total}")
    for name in SHAPES:
        print(f"  {name}: {matched[name]} 条")
        for x in samples[name][:6]:
            print(f"      {x}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())