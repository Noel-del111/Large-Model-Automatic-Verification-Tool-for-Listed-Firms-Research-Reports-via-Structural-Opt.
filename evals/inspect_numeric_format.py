# -*- coding: utf-8 -*-
"""统计数值缺失/格式错误金标的可规则化形态覆盖。"""
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "data/v2/dataset/gold.dev.jsonl"
INPUTS = ROOT / "data/v2/dataset/inputs.dev.jsonl"
RESEARCH = {"个股研报", "行业研报"}

ORD = re.compile(r"第\s*[位名次]")
PERC = re.compile(r"(?<![0-9之])[%％](?=[，。；、\s]|$)")
UNIT = re.compile(r"(?<![0-9.])\s(?:亿元|万元|千万元|元|吨|桶|只|辆|倍)(?=[，。；、\s]|$)")
CODE = re.compile(r"[\u4e00-\u9fff]{2,8}[（(]\d{3,5}[）)]")


def main() -> int:
    gold = {}
    for l in GOLD.read_text(encoding="utf-8").splitlines():
        g = json.loads(l)
        gold[g["document_id"]] = g["errors"]
    scenes = {r["doc_id"]: r["scene"] for r in
              (json.loads(l) for l in INPUTS.read_text(encoding="utf-8").splitlines())}

    stats = {k: [] for k in ("序数空槽", "百分号空槽", "单位空槽", "证券代码")}
    total = Counter()
    for did, errs in gold.items():
        if scenes.get(did) not in RESEARCH:
            continue
        for e in errs:
            if not e.get("scorable", True):
                continue
            t = e.get("type")
            for s in e.get("spans") or []:
                txt = s.get("text", "")
                if t == "数值缺失":
                    total["数值缺失"] += 1
                    if ORD.search(txt): stats["序数空槽"].append(txt[:40])
                    elif PERC.search(txt): stats["百分号空槽"].append(txt[:40])
                    elif UNIT.search(txt): stats["单位空槽"].append(txt[:40])
                elif t == "格式错误":
                    total["格式错误"] += 1
                    if CODE.search(txt): stats["证券代码"].append(txt[:40])
    print("数值缺失 金标:", total["数值缺失"], "| 序数", len(stats["序数空槽"]),
          "| 百分号", len(stats["百分号空槽"]), "| 单位", len(stats["单位空槽"]))
    print("格式错误 金标:", total["格式错误"], "| 证券代码", len(stats["证券代码"]))
    for k in ("序数空槽", "百分号空槽", "单位空槽", "证券代码"):
        print(f"--- {k} ---")
        for x in stats[k][:10]:
            print("  ", x)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())