#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
对接主线评测的适配层。

主线约定（见 `evals/datasets.md`）：样本以 `doc_id` 为键、`content` 为正文；
答案单独存在 `gold.*.jsonl`，推理只读 `inputs.*.jsonl`。
本目录的 `data/<task>/inputs.<split>.jsonl` 与 `gold.<split>.jsonl` 已沿用同一命名，
但字段名不同，因此需要这一层薄适配，**不需要改动主线代码**。

用法：
    from adapters import to_project_inputs, to_project_gold, CONTENT_FIELD
    ins  = to_project_inputs("finverbench_detection", "test")   # [{"doc_id":..., "content":...}]
    gold = to_project_gold("finverbench_detection", "test")     # [{"doc_id":..., "has_error":...}]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scorers import CONSISTENCY_TASKS, load_task  # noqa: E402

# 每个任务里"哪一段是正文"（对应主线的 content）
CONTENT_FIELD = {
    "finverbench_detection": "context",
    "finverbench_correction": "corrupted_text",
    "financebench_qa": "context_full_page",
    "financebench_claim_verification": "evidence_text",
    "financebench_correction": "wrong_statement",
}

# gold 中允许进入主线的字段（其余留在本目录的 gold 文件里）
GOLD_KEEP = {
    "finverbench_detection": ["has_error", "label", "label_text", "error_category",
                              "error_type", "error_location", "company", "period",
                              "group_id", "ambiguous_visible_text"],
    "finverbench_correction": ["has_error", "label", "label_text", "error_category",
                               "error_type", "error_location", "company", "period", "group_id"],
    "financebench_qa": ["answer", "justification", "doc_name", "group_id"],
    "financebench_claim_verification": ["has_error", "label", "label_text", "correct_statement",
                                        "error_category", "group_id"],
    "financebench_correction": ["has_error", "label", "label_text", "correct_statement",
                                "edit", "perturbation_direction", "group_id"],
}


def _is_finben(task):
    return task.startswith("finben:")


def to_project_inputs(task, split="test"):
    """转成主线的输入记录：{"doc_id": ..., "content": ..., "meta": {...}}。"""
    out = []
    for r in load_task(task, split=split):
        i = r["input"]
        if _is_finben(task):
            content = str(i.get("input") or i.get("instruction") or "")
            meta = {"instruction": i.get("instruction"), "subset": r["gold"]["subset"]}
        else:
            field = CONTENT_FIELD[task]
            content = str(i.get(field) or "")
            meta = {k: v for k, v in i.items() if k != field}
        out.append({"doc_id": r["sample_id"], "content": content, "meta": meta})
    return out


def to_project_gold(task, split="test"):
    """转成主线的 gold 记录：{"doc_id": ..., <允许的标签字段>}。"""
    out = []
    for r in load_task(task, split=split):
        if _is_finben(task):
            g = {"doc_id": r["sample_id"], "gold_output": r["gold"]["gold_output"],
                 "subset": r["gold"]["subset"], "orig_task": r["gold"]["orig_task"]}
        else:
            g = {"doc_id": r["sample_id"]}
            for k in GOLD_KEEP[task]:
                if k in r["gold"]:
                    g[k] = r["gold"][k]
        out.append(g)
    return out


if __name__ == "__main__":
    for task in sorted(CONSISTENCY_TASKS):
        ins = to_project_inputs(task, "test")
        g = to_project_gold(task, "test")
        print("{:<38s} test: inputs={:>5d} gold={:>5d}  content示例={!r}".format(
            task, len(ins), len(g), ins[0]["content"][:40] if ins else ""))
    for sub in ("flare-fnxl", "flare-fomc"):
        ins = to_project_inputs("finben:" + sub, "test")
        print("{:<38s} test: inputs={:>5d}".format("finben:" + sub, len(ins)))
