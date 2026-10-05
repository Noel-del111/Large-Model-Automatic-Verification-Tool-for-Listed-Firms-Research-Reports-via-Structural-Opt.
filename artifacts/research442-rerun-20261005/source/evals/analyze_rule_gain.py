# -*- coding: utf-8 -*-
"""规则净增益分析：hybrid 相对 model_direct 的完整一对一净增，并按 detector 分解。

反例修复（原 analyze_rule_gain.py:35–53）：不再对每条新增 finding 独立匹配全部
金标——那会在「原模型已命中同一条金标、hybrid 又添两条重复规则提示」时误报两个
新增 TP。改为：

1. 对 hybrid 与 model_direct 分别做完整一对一最大匹配，净增 = 两者差值；
2. 规则贡献按每条 finding 在完整一对一匹配中的实际角色归属，不重复计数。

用法：
    python evals/analyze_rule_gain.py --gold data/v2/dataset/gold.eval_oct05.jsonl \
        --direct data/v2/.../predictions/model_direct \
        --hybrid data/v2/.../predictions/hybrid
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "evals"), str(ROOT / "factcheck/src")]

from fined_bench_eval import _contains_error, _maximum_matching  # noqa: E402

# 规则层 finding 的 detector_id 前缀（模型路以外、由确定性规则/legacy 产出）。
RULE_PREFIXES = ("verified.", "legacy.", "intrinsic.", "rules.", "cross_period", "redundant")


def _records_from(path: str) -> list[dict]:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(f"输入不存在：{target}")
    if target.is_dir():
        records = []
        for file in sorted(target.glob("*.json")):
            records.append(json.loads(file.read_text(encoding="utf-8")))
        if not records:
            raise ValueError(f"目录 {target} 下没有 .json 文件")
        return records
    text = target.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
        return data if isinstance(data, list) else [data]
    except ValueError:
        return [json.loads(line) for line in text.splitlines() if line.strip()]


def _canonical_error(record: dict) -> dict:
    error = {"error_type": record.get("error_type", record.get("type", ""))}
    # 原样保留 span，不静默丢弃非法 span。
    error["spans"] = list(record.get("spans", []) or [])
    for key in ("scorable", "reason", "status", "detector_id", "validation"):
        if key in record:
            error[key] = record[key]
    return error


def _load_docs(path: str) -> dict[str, dict]:
    docs: dict[str, dict] = {}
    for record in _records_from(path):
        doc_id = record.get("document_id", record.get("doc_id"))
        if not isinstance(doc_id, str) or not doc_id:
            raise ValueError(f"记录缺少 document_id：{str(record)[:120]}")
        if doc_id in docs:
            raise ValueError(f"document_id 重复：{doc_id}")
        docs[doc_id] = {"document_id": doc_id,
                        "errors": [_canonical_error(e) for e in record.get("errors", [])]}
    return docs


def _match(preds: list[dict], golds: list[dict]):
    """完整一对一最大匹配，返回 (pairs, unmatched_preds, unmatched_golds)。"""
    pairs = _maximum_matching(preds, golds, lambda p, g: _contains_error(p, g)
                              and p["error_type"] == g["error_type"])
    paired_preds = {id(p) for p, _ in pairs}
    paired_golds = {id(g) for _, g in pairs}
    return pairs, [p for p in preds if id(p) not in paired_preds], \
        [g for g in golds if id(g) not in paired_golds]


def _score(pred_docs: dict[str, dict], gold_docs: dict[str, dict]) -> tuple[int, int, int]:
    tp = fp = fn = 0
    for doc_id in sorted(set(pred_docs) | set(gold_docs)):
        preds = pred_docs.get(doc_id, {}).get("errors", [])
        golds = [g for g in gold_docs.get(doc_id, {}).get("errors", []) if g.get("scorable", True)]
        pairs, unmatched_preds, unmatched_golds = _match(preds, golds)
        tp += len(pairs)
        fp += len(unmatched_preds)
        fn += len(unmatched_golds)
    return tp, fp, fn


def _detector_breakdown(pred_docs: dict[str, dict], gold_docs: dict[str, dict]) -> dict[str, dict]:
    """按 detector_id 统计 finding 在完整一对一匹配中的 TP/FP，不重复计数。"""
    agg: dict[str, dict] = {}
    for doc_id in sorted(set(pred_docs) | set(gold_docs)):
        preds = pred_docs.get(doc_id, {}).get("errors", [])
        golds = [g for g in gold_docs.get(doc_id, {}).get("errors", []) if g.get("scorable", True)]
        pairs, unmatched_preds, _ = _match(preds, golds)
        tp_ids = {id(p) for p, _ in pairs}
        for pred in preds:
            detector = pred.get("detector_id", "unknown")
            bucket = agg.setdefault(detector, {"tp": 0, "fp": 0})
            if id(pred) in tp_ids:
                bucket["tp"] += 1
            else:
                bucket["fp"] += 1
    return agg


def main() -> int:
    parser = argparse.ArgumentParser(description="规则净增益：完整一对一比较（不发起模型调用）")
    parser.add_argument("--gold", required=True, help="gold.{split}.jsonl")
    parser.add_argument("--direct", required=True, help="model_direct 预测目录或文件")
    parser.add_argument("--hybrid", required=True, help="hybrid 预测目录或文件")
    args = parser.parse_args()

    gold_docs = _load_docs(args.gold)
    direct_docs = _load_docs(args.direct)
    hybrid_docs = _load_docs(args.hybrid)

    d_tp, d_fp, d_fn = _score(direct_docs, gold_docs)
    h_tp, h_fp, h_fn = _score(hybrid_docs, gold_docs)

    result = {
        "model_direct": {"tp": d_tp, "fp": d_fp, "fn": d_fn},
        "hybrid": {"tp": h_tp, "fp": h_fp, "fn": h_fn},
        "net_gain": {"tp": h_tp - d_tp, "fp": h_fp - d_fp, "fn": h_fn - d_fn},
        "rule_detector_breakdown": _detector_breakdown(hybrid_docs, gold_docs),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
