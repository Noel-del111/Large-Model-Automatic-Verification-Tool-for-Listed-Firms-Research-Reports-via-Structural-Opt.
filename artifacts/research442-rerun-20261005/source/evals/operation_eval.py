# -*- coding: utf-8 -*-
"""按操作合同评测核查模块（借鉴 FinRiskAtlas 的两维评测）。

三个层面的指标：
1. 操作执行：每个操作各自的精确率/召回率/F1（复用 fined_bench_eval 的 span 级评分）；
2. 证据状态控制：Proceed / Ask 的均衡准确率 BAcc、证据请求对齐率 ERA、条件请求对齐率 CRA；
3. 模型选型：按单一综合分挑模型相对"按操作挑模型"的后悔值，用于回答
   "能不能只用一个总榜分数选模型"（论文结论：不能，个别操作最多损失 18 分）。

用法：
    py -3 evals/operation_eval.py --selftest
    py -3 evals/operation_eval.py --predictions preds.json --gold gold.json --out report.json
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Sequence

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from fined_bench_eval import score_detection  # noqa: E402

from contracts import OPERATIONS  # noqa: E402


def balanced_accuracy(pairs: Sequence[tuple]) -> Dict[str, Any]:
    """pairs: [(预测动作, 真实动作)]，取值 ask / proceed。"""
    stats = defaultdict(lambda: [0, 0])
    for predicted, actual in pairs:
        stats[actual][0] += int(predicted == actual)
        stats[actual][1] += 1
    recalls = {k: (v[0] / v[1] if v[1] else None) for k, v in stats.items()}
    values = [v for v in recalls.values() if v is not None]
    return {
        "recall_proceed": recalls.get("proceed"),
        "recall_ask": recalls.get("ask"),
        "balanced_accuracy": round(sum(values) / len(values), 4) if values else None,
        "counts": {k: v[1] for k, v in stats.items()},
    }


def _key(item: Dict[str, Any]) -> tuple:
    return (item.get("doc_role"), item.get("field"), item.get("period"))


def evidence_request_alignment(predicted: Iterable[Dict[str, Any]],
                               required: Iterable[Dict[str, Any]]) -> float:
    """证据请求对齐率：请求到的必需证据占全部必需证据的比例。"""
    required_set = {_key(item) for item in required}
    if not required_set:
        return 1.0
    predicted_set = {_key(item) for item in predicted}
    return round(len(required_set & predicted_set) / len(required_set), 4)


def evaluate(predictions: Sequence[Dict[str, Any]],
             golds: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    def unique_index(records, label):
        indexed = {}
        for item in records:
            item_id = item.get("item_id")
            if not isinstance(item_id, str) or not item_id:
                raise ValueError(f"{label}: missing item_id")
            if item_id in indexed:
                raise ValueError(f"{label}: duplicate item_id {item_id}")
            indexed[item_id] = item
        return indexed

    gold_by_id = unique_index(golds, "golds")
    pred_by_id = unique_index(predictions, "predictions")
    per_operation: Dict[str, Dict[str, Any]] = {
        item.key: {"items": 0, "detection": None, "ask_pairs": [],
                   "era": [], "cra": []} for item in OPERATIONS}
    missing = sorted(set(gold_by_id) - set(pred_by_id))
    unmatched = sorted(set(pred_by_id) - set(gold_by_id))
    for item_id, gold in sorted(gold_by_id.items()):
        pred = pred_by_id.get(item_id, {})
        operation = gold.get("operation") or "value_consistency"
        bucket = per_operation.setdefault(operation, {"items": 0, "detection": None,
                                                      "ask_pairs": [], "era": [], "cra": []})
        bucket["items"] += 1
        # Do not let another item's prediction match this item's missing output,
        # even when two operation items reference the same underlying document.
        def scoped(findings):
            return [dict(f, doc_id=f"{item_id}::{f['doc_id']}") for f in findings]
        bucket.setdefault("pred_findings", []).extend(scoped(pred.get("findings", [])))
        bucket.setdefault("gold_findings", []).extend(scoped(gold.get("findings", [])))
        predicted_action = pred.get("decision", "missing")
        actual_action = gold.get("recorded_action", "proceed")
        if actual_action not in {"ask", "proceed"}:
            raise ValueError(f"golds: invalid recorded_action for {item_id}")
        bucket["ask_pairs"].append((predicted_action, actual_action))
        era = (evidence_request_alignment(pred.get("evidence_request", []),
                                          gold.get("required_evidence", []))
               if item_id in pred_by_id else 0.0)
        bucket["era"].append(era)
        if actual_action == "ask":
            bucket["cra"].append(era)

    summary: Dict[str, Any] = {"operations": {}, "unmatched_predictions": len(unmatched),
                               "unmatched_prediction_ids": unmatched,
                               "unmatched_prediction_findings": sum(len(pred_by_id[k].get("findings", [])) for k in unmatched),
                               "missing_predictions": len(missing), "missing_prediction_ids": missing,
                               "gold_items": len(gold_by_id), "prediction_items": len(pred_by_id)}
    for key, bucket in per_operation.items():
        if not bucket["items"]:
            continue
        entry: Dict[str, Any] = {"items": bucket["items"]}
        if bucket.get("pred_findings") or bucket.get("gold_findings"):
            entry["detection"] = score_detection(bucket.get("pred_findings", []),
                                                 bucket.get("gold_findings", []),
                                                 strict_type=True)
        if bucket["ask_pairs"]:
            entry["decision"] = balanced_accuracy(bucket["ask_pairs"])
        if bucket["era"]:
            entry["era"] = round(sum(bucket["era"]) / len(bucket["era"]), 4)
        if bucket["cra"]:
            entry["cra"] = round(sum(bucket["cra"]) / len(bucket["cra"]), 4)
        summary["operations"][key] = entry
    return summary


def selection_regret(scores: Dict[str, Dict[str, float]],
                     shortlist_k: int = 1) -> Dict[str, Any]:
    """按综合分选模型相对按操作选模型的后悔值。

    scores: {模型: {操作: 分数}}，另需一个 "overall" 键表示综合分。
    """
    models = [m for m in scores if "overall" in scores[m]]
    operations = sorted({op for m in scores.values() for op in m if op != "overall"})
    ranked = sorted(models, key=lambda m: scores[m]["overall"], reverse=True)
    shortlist = ranked[:shortlist_k]
    result: Dict[str, Any] = {"shortlist": shortlist, "operations": {}}
    total = 0.0
    for operation in operations:
        best = max(scores[m][operation] for m in models)
        chosen = max(scores[m][operation] for m in shortlist)
        regret = round(best - chosen, 4)
        total += regret
        result["operations"][operation] = {"best": best, "selected": chosen, "regret": regret}
    result["mean_regret"] = round(total / max(len(operations), 1), 4)
    return result


def selftest() -> int:
    golds = [
        {"item_id": "d1", "operation": "value_consistency", "recorded_action": "proceed",
         "findings": [{"doc_id": "d1", "start": 10, "end": 20, "type": "数值不一致错误"}]},
        {"item_id": "d2", "operation": "caliber", "recorded_action": "ask",
         "findings": [], "required_evidence": [
             {"doc_role": "财报", "field": "扣非净利润", "period": "2025H1"}]},
    ]
    perfect = [
        {"item_id": "d1", "decision": "proceed",
         "findings": [{"doc_id": "d1", "start": 10, "end": 20, "type": "数值不一致错误"}]},
        {"item_id": "d2", "decision": "ask", "findings": [],
         "evidence_request": [{"doc_role": "财报", "field": "扣非净利润", "period": "2025H1"}]},
    ]
    report = evaluate(perfect, golds)
    assert report["operations"]["value_consistency"]["detection"]["f1"] == 1.0, report
    assert report["operations"]["caliber"]["decision"]["balanced_accuracy"] == 1.0, report
    assert report["operations"]["caliber"]["cra"] == 1.0, report

    wrong = [dict(perfect[0], decision="ask", evidence_request=[]),
             dict(perfect[1], decision="proceed", evidence_request=[])]
    bad = evaluate(wrong, golds)
    assert bad["operations"]["caliber"]["cra"] == 0.0, bad
    assert bad["operations"]["value_consistency"]["decision"]["balanced_accuracy"] < 1.0, bad

    scores = {
        "甲": {"overall": 90, "value_consistency": 70, "caliber": 95},
        "乙": {"overall": 85, "value_consistency": 95, "caliber": 60},
    }
    regret = selection_regret(scores, shortlist_k=1)
    assert regret["shortlist"] == ["甲"], regret
    assert regret["operations"]["value_consistency"]["regret"] == 25.0, regret
    print("自测通过：操作级评分、BAcc、ERA/CRA、选型后悔值均符合预期")
    print("\n" + __import__("contracts").describe()[:600])
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="按操作合同评测核查模块")
    parser.add_argument("--predictions")
    parser.add_argument("--gold")
    parser.add_argument("--out", default="evals/reports/operation_eval.json")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    if not (args.predictions and args.gold):
        parser.print_help()
        return 1
    predictions = json.loads(pathlib.Path(args.predictions).read_text(encoding="utf-8"))
    golds = json.loads(pathlib.Path(args.gold).read_text(encoding="utf-8"))
    report = evaluate(predictions, golds)
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for key, value in report["operations"].items():
        line = f"{key}: 样本 {value['items']}"
        if "detection" in value:
            line += f"，F1 {value['detection']['f1']}"
        if "decision" in value:
            line += f"，BAcc {value['decision']['balanced_accuracy']}"
        if "era" in value:
            line += f"，ERA {value['era']}"
        if "cra" in value:
            line += f"，CRA {value['cra']}"
        print(line)
    print(f"报告已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
