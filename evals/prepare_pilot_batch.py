# -*- coding: utf-8 -*-
"""Pilot 小批选择：为 few-shot 对照从 dev 研报中挑选 24 篇短文档（离线，零调用）。

目标分布对齐 442 篇研报测评：只取个股/行业研报；长度 ≤3200 字（控制单次调用成本）；
按 FP 重灾区/召回缺口类型加权（冗余语句/术语误用/金融要素缺失 权重3），
贪心最大化加权金标条数，保证细分类有足够样本观察 P/R 变化。

usage: python evals/prepare_pilot_batch.py [--size 24] [--out data/v2/dataset/inputs.pilot24.jsonl]
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "data/v2/dataset"
MAX_LEN = 3200
TYPE_WEIGHTS = {
    "冗余语句": 3, "术语误用": 3, "金融要素缺失": 3,
    "计算错误": 2, "数值不一致错误": 2, "时间信息非法": 2,
    "时间矛盾": 2, "语义逻辑矛盾": 2,
    "数值单位错误": 1, "数值缺失": 1, "属性值缺失错误": 1, "格式错误": 1,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size", type=int, default=24)
    parser.add_argument("--out", default=str(DATASET / "inputs.pilot24.jsonl"))
    args = parser.parse_args()

    inputs = [json.loads(line) for line in
              (DATASET / "inputs.dev.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    gold = {}
    for line in (DATASET / "gold.dev.jsonl").read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        gold[record["document_id"]] = record["errors"]

    candidates = []
    for row in inputs:
        if row.get("scene") not in {"个股研报", "行业研报"}:
            continue
        if not 0 < len(row["content"]) <= MAX_LEN:
            continue
        typed = Counter()
        for err in gold.get(row["doc_id"], []):
            if err.get("scorable", True) and err.get("type") in TYPE_WEIGHTS:
                typed[err["type"]] += 1
        candidates.append({"row": row, "typed": typed})

    chosen = []
    while len(chosen) < args.size and candidates:
        def score(item: dict) -> int:
            return sum(TYPE_WEIGHTS[t] * n for t, n in item["typed"].items())
        best = max(candidates, key=score)
        candidates.remove(best)
        chosen.append(best)

    payload = "".join(json.dumps(item["row"], ensure_ascii=False, sort_keys=True) + "\n"
                      for item in chosen)
    Path(args.out).write_text(payload, encoding="utf-8", newline="\n")
    total = Counter()
    for item in chosen:
        total.update(item["typed"])
    report = {"documents": len(chosen), "scenes": Counter(item["row"]["scene"] for item in chosen),
              "total_chars": sum(len(item["row"]["content"]) for item in chosen),
              "scorable_gold_by_type": dict(sorted(total.items())),
              "scorable_gold_total": sum(total.values()),
              "max_doc_chars": max(len(item["row"]["content"]) for item in chosen)}
    Path(args.out + ".report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                               encoding="utf-8", newline="\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())