#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小批联调（smoke test）：用"最笨的基线"把每个任务的 输入→预测→评分 通路跑通一遍。

目的不是刷分，而是确认：
  1 数据能被正确装载，input 与 gold 确实分开；
  2 每个任务的评分器能被调用、返回完整指标，不会因字段缺失报错；
  3 把"虚高基线"显式打出来（例如 FinVerBench 全答'有错'就有 97.8% 准确率）。

跑通后再把真实模型的输出按同样格式喂进 evaluate() 即可。

运行：python3 scripts/smoke_test.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scorers import (CONSISTENCY_TASKS, evaluate, list_finben_subsets,  # noqa: E402
                    load_jsonl, load_task)


def show(title, payload):
    print("\n" + "=" * 72)
    print(title)
    print("-" * 72)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def check_no_leak(rec):
    """确保喂给模型的 prompt 里没有标签/答案字段。"""
    forbidden = {"has_error", "label", "label_text", "corrected_text", "correct_statement",
                 "gold_output", "answer", "error_type", "error_location", "modified_value",
                 "original_value", "error_description", "edit"}
    return sorted(set(rec["input"].keys()) & forbidden)


def main():
    total_recs = 0

    for task in sorted(CONSISTENCY_TASKS):
        recs = load_task(task, split="dev")[:40]
        if not recs:
            print("\n[skip] {} 无 dev 样本".format(task))
            continue
        total_recs += len(recs)
        leaks = [r["sample_id"] for r in recs if check_no_leak(r)]
        assert not leaks, "prompt 泄漏: {}".format(leaks[:2])

        # 最笨基线
        if task in ("finverbench_detection", "financebench_claim_verification"):
            preds = [True] * len(recs)                       # 全判"有错"
        elif task == "finverbench_correction":
            preds = [r["input"]["corrupted_text"] for r in recs]   # 原样返回（不修）
        elif task == "financebench_correction":
            preds = [r["input"]["wrong_statement"] for r in recs]  # 原样返回
        else:
            preds = ["" for _ in recs]
        out = evaluate(task, recs, preds)
        out["n_loaded_dev"] = len(load_task(task, split="dev"))
        out["prompt_keys"] = sorted(recs[0]["input"].keys())
        out["gold_keys"] = sorted(recs[0]["gold"].keys())
        show("[基线] {}  (dev 前 {} 条)".format(task, len(recs)), out)

    for subset in list_finben_subsets():
        task = "finben:" + subset
        recs = load_task(task, split="dev")[:40]
        if not recs:
            recs = load_task(task, split="test")[:40]
            where = "test(官方)"
        else:
            where = "dev"
        if not recs:
            continue
        total_recs += len(recs)
        metric = recs[0]["metric"]
        if metric == "stance":
            from collections import Counter
            majority = Counter(r["gold"]["gold_output"] for r in load_task(task)).most_common(1)[0][0]
            preds = [majority] * len(recs)
        else:
            preds = ["" for _ in recs]
        out = evaluate(task, recs, preds)
        out["n_scored_from"] = where
        show("[基线] {}  ({} 前 {} 条)".format(task, where, len(recs)), out)

    print("\n" + "=" * 72)
    print("联调通过：{} 条样本完成 输入→预测→评分 全链路，无字段缺失、无提示词泄漏。".format(total_recs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
