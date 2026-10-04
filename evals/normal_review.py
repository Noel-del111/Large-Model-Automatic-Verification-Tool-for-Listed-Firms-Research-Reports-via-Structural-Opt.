"""Normal-text sign-off workflow and false-positive rate tooling; no model calls.

交接文档中「正常文本签核与误报率」尚未完成：16 条正常候选全部待审核。本工具把
人工签核做成可追溯、可复用、可评测的三步流程（对既有预测离线运行）：

1. queue  从正常文档 + 已有 detect_text 预测生成待复核队列 review_queue.json
          （只含 needs_review/未定位候选；confirmed_error 不进人工队列）；
2. signoff 消费人工决策 JSONL（confirm/dismiss、复核人、备注、计时），幂等合并；
3. report 按文档与错误类型统计 误报率 = 驳回数 / 已决策数，未决策与被驳回未计时
          的条目单列，未计时不得记为 0 秒。

usage:
    python evals/normal_review.py queue --normal data/v2/normals.json --predictions data/v2/predictions/hybrid --out data/v2/normal-review
    python evals/normal_review.py signoff --queue data/v2/normal-review/review_queue.json --decisions decisions.jsonl --out data/v2/normal-review
    python evals/normal_review.py report --queue data/v2/normal-review/signed_queue.json --out data/v2/normal-review
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "evals")]

from analyze_predictions import load_prediction_docs  # noqa: E402  (same-package helper)


def candidate_id(document_id: str, error: dict) -> str:
    payload = [document_id, error.get("error_type", ""), error.get("spans", []), str(error.get("reason", ""))]
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:24]


def build_queue(normal_docs: list[dict], pred_docs: dict[str, dict], *, include_confirmed: bool = False) -> list[dict]:
    """Build the human review queue.

    Daily review queues may omit auto-confirmed findings, but normal-text
    false-positive auditing must cover confirmed_error output too, so pass
    ``include_confirmed=True`` for that audit. Confirmed findings are potential
    false positives just like any other hint.
    """
    known = {doc.get("document_id") for doc in normal_docs if isinstance(doc.get("document_id"), str)}
    queue = []
    for doc_id in sorted(set(known) & set(pred_docs)):
        for error in pred_docs[doc_id]["errors"]:
            if error.get("status") == "confirmed_error" and not include_confirmed:
                continue
            queue.append({"candidate_id": candidate_id(doc_id, error),
                          "document_id": doc_id, "error_type": error.get("error_type", ""),
                          "spans": error.get("spans", []), "reason": error.get("reason", ""),
                          "review_priority": error.get("review_priority", "unspecified"),
                          "validation": error.get("validation", ""),
                          "decision": None, "reviewer": "", "note": "",
                          "duration_seconds": None, "timing_method": "not_measured"})
    return queue


def merge_signoffs(queue: list[dict], decisions: list[dict]) -> list[dict]:
    by_id = {item["candidate_id"]: item for item in queue}
    seen: set[str] = {item["candidate_id"] for item in queue
                      if item.get("decision") in {"confirmed", "dismissed"}}
    for decision in decisions:
        if not isinstance(decision, dict):
            raise ValueError(f"决策必须是对象：{str(decision)[:120]}")
        cid = decision.get("candidate_id")
        verdict = decision.get("verdict")
        if not isinstance(cid, str) or cid not in by_id:
            raise ValueError(f"决策引用了不存在的候选：{str(decision)[:120]}")
        if verdict not in {"confirmed", "dismissed"}:
            raise ValueError(f"非法结论 {verdict!r}，只允许 confirmed/dismissed")
        if cid in seen:
            raise ValueError(f"同一候选重复决策：{cid}")
        seen.add(cid)
        duration = decision.get("duration_seconds")
        if duration is not None and (not isinstance(duration, (int, float)) or duration < 0):
            raise ValueError(f"复核耗时必须是非负数：{decision}")
        entry = by_id[cid]
        entry["decision"] = verdict
        entry["reviewer"] = str(decision.get("reviewer", "")).strip()
        entry["note"] = str(decision.get("note", "")).strip()
        entry["duration_seconds"] = duration
        entry["timing_method"] = "explicit_signoff_wall_clock" if duration is not None else "not_measured"
    return queue


def false_positive_report(queue: list[dict]) -> dict:
    """统计"已审核提示驳回比例"(review dismissal rate)，不是正确内容误报率。

    正确内容误报率需要正确文本样本（断言/片段/文档级负类）与人工签核，
    本函数只衡量"人工已审核提示中被驳回的比例"，绝不用于 README 的 FPR 验收。
    没有已审核条目时 rate 为 null 且 status=not_measured。
    """
    decided = [item for item in queue if item["decision"] in {"confirmed", "dismissed"}]
    dismissed = [item for item in decided if item["decision"] == "dismissed"]
    untimed_dismissed = [item for item in dismissed if item["timing_method"] == "not_measured"]
    by_doc: dict[str, dict] = {}
    by_type: dict[str, dict] = {}
    for item in decided:
        for bucket in (by_doc.setdefault(item["document_id"], {"decided": 0, "dismissed": 0}),
                       by_type.setdefault(item["error_type"], {"decided": 0, "dismissed": 0})):
            bucket["decided"] += 1
            if item["decision"] == "dismissed":
                bucket["dismissed"] += 1

    def with_rate(bucket):
        rate = bucket["dismissed"] / bucket["decided"] if bucket["decided"] else None
        return {**bucket, "review_dismissal_rate": round(rate, 4) if rate is not None else None}

    if not decided:
        return {"queue_total": len(queue), "decided": 0, "dismissed": 0,
                "review_dismissal_rate": None, "status": "not_measured",
                "reason": "没有已审核条目，无法计算驳回比例；未审核项不算已通过",
                "by_document": {}, "by_type": {},
                "undecided": len(queue),
                "untimed_dismissed_items": [], "timing_note": "未计时条目单独统计。"}
    return {"queue_total": len(queue), "decided": len(decided), "dismissed": len(dismissed),
            "review_dismissal_rate": round(len(dismissed) / len(decided), 4), "status": "measured",
            "by_document": {key: with_rate(value) for key, value in sorted(by_doc.items())},
            "by_type": {key: with_rate(value) for key, value in sorted(by_type.items())},
            "undecided": len(queue) - len(decided),
            "untimed_dismissed_items": [item["candidate_id"] for item in untimed_dismissed],
            "timing_note": "未计时条目单独统计；review_dismissal_rate 只按已审核提示计算，是驳回比例，不是正确内容误报率(FPR)。"}


def _load_normal_docs(path: str) -> list[dict]:
    target = Path(path)
    if not target.is_file():
        raise SystemExit(f"正常文档清单不存在：{target}")
    data = json.loads(target.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else [data]


def _load_decisions(path: str) -> list[dict]:
    if not Path(path).is_file():
        raise SystemExit(f"决策文件不存在：{path}")
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="正常文本核验与误报率评测；不发起模型调用")
    sub = parser.add_subparsers(dest="command", required=True)
    queue_parser = sub.add_parser("queue")
    queue_parser.add_argument("--normal", required=True, help="正常文档清单 JSON（document_id/content）")
    queue_parser.add_argument("--predictions", required=True, help="检测报告目录或 JSON 文件")
    queue_parser.add_argument("--include-confirmed", action="store_true",
                              help="正常文本误报审计须覆盖 confirmed_error 输出")
    queue_parser.add_argument("--out", required=True, help="输出目录")
    signoff_parser = sub.add_parser("signoff")
    signoff_parser.add_argument("--queue", required=True, help="review_queue.json 路径")
    signoff_parser.add_argument("--decisions", required=True, help="人工决策 JSONL")
    signoff_parser.add_argument("--out", required=True, help="输出目录")
    report_parser = sub.add_parser("report")
    report_parser.add_argument("--queue", required=True, help="已合并决策的队列 JSON 路径")
    report_parser.add_argument("--out", required=True, help="输出目录")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.command == "queue":
        normal_docs = _load_normal_docs(args.normal)
        if not any(isinstance(doc.get("document_id"), str) for doc in normal_docs):
            raise SystemExit("正常文档清单缺少 document_id")
        queue = build_queue(normal_docs, load_prediction_docs(args.predictions),
                            include_confirmed=getattr(args, "include_confirmed", False))
        (out_dir / "review_queue.json").write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"queued_candidates": len(queue)}, ensure_ascii=False))
    elif args.command == "signoff":
        queue = json.loads(Path(args.queue).read_text(encoding="utf-8"))
        merged = merge_signoffs(queue, _load_decisions(args.decisions))
        (out_dir / "signed_queue.json").write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"decided": sum(item["decision"] is not None for item in merged)}, ensure_ascii=False))
    elif args.command == "report":
        queue = json.loads(Path(args.queue).read_text(encoding="utf-8"))
        report = false_positive_report(queue)
        (out_dir / "fp_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({
            "decided": report["decided"], "dismissed": report["dismissed"],
            "review_dismissal_rate": report["review_dismissal_rate"],
            "status": report["status"],
            "undecided": report["undecided"],
            "untimed_dismissed": len(report["untimed_dismissed_items"]),
        }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())