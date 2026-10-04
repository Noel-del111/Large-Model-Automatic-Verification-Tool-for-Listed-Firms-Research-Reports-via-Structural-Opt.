# -*- coding: utf-8 -*-
"""从 dev 研报(非 pilot 24)挖掘"总-分结构"句子，作为冗余语句误报的研报语境负例。

arm_new2 的负例 #6 是招标投标法条文("招标文件/资格预审文件"反复)，与研报冗余形态
差异大，导致模型对真冗余(字面重复)过度宽容。本脚本找研报里的"总-分/递进"结构句——
这类句子被模型误报为冗余(FP #3 形态)，但金标无错，适合示范"结构性重复≠冗余"。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "evals")]
from fined_bench_eval import _valid_span

PILOT = ROOT / "data/v2/dataset/inputs.pilot24.jsonl"
GOLD = ROOT / "data/v2/dataset/gold.dev.jsonl"
INPUTS = ROOT / "data/v2/dataset/inputs.dev.jsonl"

# "总-分"结构的触发模式：先给总数/维度词，再展开列举，模型易误判为冗余
TOTAL_SPLIT = [
    re.compile(r"从[^。；]{0,30}等\s*[一二三四五六七八九十\d]+\s*(?:个)?方面"),
    re.compile(r"主要[包括包][^。；]{0,40}等"),
    re.compile(r"分为[^。；]{0,40}等"),
    re.compile(r"涵盖[^。；]{0,40}等"),
    re.compile(r"包括[^。；]{0,50}等"),
]


def has_total_split(text: str) -> bool:
    return any(p.search(text) for p in TOTAL_SPLIT)


def main() -> int:
    pilot_ids = {json.loads(line)["doc_id"] for line in PILOT.read_text(encoding="utf-8").splitlines()}
    gold = {}
    for line in GOLD.read_text(encoding="utf-8").splitlines():
        g = json.loads(line)
        gold[g["document_id"]] = g["errors"]

    hits = []
    for line in INPUTS.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("scene") not in {"个股研报", "行业研报"}:
            continue
        if row["doc_id"] in pilot_ids:
            continue
        content = row["content"]
        golds = gold.get(row["doc_id"], [])
        # 收集该篇全部金标错误 span（所有类型），用于排除"句内有任何错误"的句子
        all_error_spans = []
        for e in golds:
            if not e.get("scorable", True):
                continue
            for s in e.get("spans") or []:
                if isinstance(s, dict) and isinstance(s.get("start"), int) and isinstance(s.get("end"), int):
                    all_error_spans.append((s["start"], s["end"], s.get("text", "")))
        for m in re.finditer(r"[^。\n]+[。\n]", content):
            sent = m.group(0).strip()
            if not (15 <= len(sent) <= 220):
                continue
            if not has_total_split(sent):
                continue
            # 该句不得与任何金标错误 span 有字符重叠（保证是"完全无错"的句子）
            start, end = m.start(), m.end()
            if any(s0 < end and start < s1 for s0, s1, _ in all_error_spans):
                continue
            hits.append({"doc_id": row["doc_id"], "scene": row["scene"], "sent": sent})
    print(f"候选数: {len(hits)}")
    for h in hits[:15]:
        print(f"[{h['scene']}] {h['doc_id'][-6:]} | {h['sent'][:120]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())