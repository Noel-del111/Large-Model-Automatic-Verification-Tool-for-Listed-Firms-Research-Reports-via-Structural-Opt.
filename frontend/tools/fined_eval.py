# -*- coding: utf-8 -*-
"""FinED-Bench 公开基准对照评测：把测评集文档文本喂给核查层的研报自身一致性检查，
与标准答案（error_type + error_span）逐项对照，输出精确率/召回率等指标。

口径（务必随结果一起引用）：
- 本程序只跑"研报自身一致性检查"（intrinsic：列举不一致/时间矛盾/单位-术语护栏）加
  声明提取（claim_extract 供单位护栏使用）。Factcheck 的数值比对规则需要配对的财报
  来源文件，FinED-Bench 只有单篇文档文本，因此跨文件核对不在本评测范围内。
- 命中 = finding 的原文与标准答案 error_span 文本重叠（互为子串，忽略空白）且
  错误类型对齐（numeric_inconsistency↔数值不一致错误；
  time_conflict↔时间矛盾/时间信息非法；unit_term_mismatch↔数值单位错误）。
  其余系统报错计 FP；未被任何 finding 覆盖的标准错误计 FN；每条标准错误只计一次。
- 证据定位/建议完整性依赖 PDF 页码与财报证据，在纯文本基准上不适用；
  适用指标为判错精确率、错误召回率、F1、误报率与逐类型明细。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "repo" / "factcheck" / "src")]
from yjcheck.claim_extract import extract_claims
from yjcheck.intrinsic import check_intrinsic_consistency
from yjcheck.models import Block, Document

DEFAULT_EVAL_DATA = ROOT / "测评集" / "测评集" / "FinED-Bench-main" / "fined_bench" / "eval_data.json"
OUT_JSON = ROOT / "repo" / "data" / "fined_eval_results.json"

_TYPE_ALIGN = {
    "numeric_inconsistency": {"数值不一致错误"},
    "time_conflict": {"时间矛盾", "时间信息非法"},
    "unit_term_mismatch": {"数值单位错误"},
}


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def _overlap(left: str, right: str) -> bool:
    a, b = _norm(left), _norm(right)
    return bool(a) and bool(b) and (a in b or b in a)


def analyze(path: Path, limit: int, scenes: list[str] | None) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if scenes:
        wanted = set(scenes)
        data = [x for x in data if x.get("scene") in wanted]
    items = data[:limit] if limit else data
    scenes_stat = Counter(item.get("scene", "?") for item in items)
    gold_types = Counter()
    per_doc = []
    t0 = time.monotonic()
    for item in items:
        content = item.get("content", "")
        lines = [line for line in content.split("\n") if line.strip()]
        blocks = [Block(f"b{i}", line, page=1, paragraph=i + 1) for i, line in enumerate(lines)]
        doc = Document("fined", "a" * 64, "run", "fined.txt", "report", "", "unknown", blocks)
        claims = extract_claims(doc)
        findings = check_intrinsic_consistency(doc, claims)
        gold = []
        for error in item.get("errors", []):
            span = error.get("error_span")
            text = span[0] if isinstance(span, list) and span else str(span or "")
            gold.append({"type": error.get("error_type", "?"), "text": text})
            gold_types[error.get("error_type", "?")] += 1
        per_doc.append({"title": item.get("title", ""), "scene": item.get("scene", ""),
                        "claims": len(claims),
                        "claim_texts": [c.text for c in claims],
                        "findings": [{"rule": f.rule_id, "type": f.error_type, "text": f.claim.text}
                                     for f in findings], "gold": gold})
    runtime = time.monotonic() - t0
    return {"scenes": dict(sorted(scenes_stat.items(), key=lambda kv: -kv[1])),
            "gold_type_counts": dict(sorted(gold_types.items(), key=lambda kv: -kv[1])),
            "runtime_seconds": round(runtime, 2),
            "failures": 0,
            "per_doc": per_doc}


def score(results: dict) -> dict:
    tp, fp = [], []
    matched_gold: set[tuple[int, int]] = set()
    for d, doc in enumerate(results["per_doc"]):
        for finding in doc["findings"]:
            aligned = _TYPE_ALIGN.get(finding["type"], set())
            hit = None
            for g, gold in enumerate(doc["gold"]):
                if gold["type"] in aligned and _overlap(finding["text"], gold["text"]) \
                        and (d, g) not in matched_gold:
                    hit = (d, g)
                    break
            if hit is None:
                fp.append((finding, doc))
            else:
                matched_gold.add(hit)
                tp.append((finding, doc, doc["gold"][hit[1]]))
    fn = sum(1 for d, doc in enumerate(results["per_doc"])
             for g in range(len(doc["gold"])) if (d, g) not in matched_gold)
    gold_total = sum(len(doc["gold"]) for doc in results["per_doc"])
    reported = len(tp) + len(fp)

    def pct(top: int, bottom: int) -> float | None:
        return round(100.0 * top / bottom, 1) if bottom else None

    by_gold_type = {}
    for label, count in results["gold_type_counts"].items():
        found, base = 0, 0
        for d, doc in enumerate(results["per_doc"]):
            for g, gold in enumerate(doc["gold"]):
                if gold["type"] != label:
                    continue
                base += 1
                if (d, g) in matched_gold:
                    found += 1
        by_gold_type[label] = {"gold": base, "recalled": found,
                               "recall_pct": pct(found, base)}

    by_rule_counter = Counter()
    for doc in results["per_doc"]:
        for finding in doc["findings"]:
            by_rule_counter[finding["rule"]] += 1
    by_rule_hit = Counter()
    for finding, doc, gold in tp:
        by_rule_hit[finding["rule"]] += 1
    by_rule = {rule: {"reported": by_rule_counter[rule], "hit": by_rule_hit.get(rule, 0),
                      "precision_pct": pct(by_rule_hit.get(rule, 0), by_rule_counter[rule])}
               for rule in by_rule_counter}
    docs = len(results["per_doc"])

    # 补充工程指标（对齐 README"补充工程指标"八维度，可在纯文本基准上直接计算的部分）
    covered, claim_total = 0, 0
    for doc in results["per_doc"]:
        claim_total += doc["claims"]
        for gold in doc["gold"]:
            if any(_overlap(gold["text"], text) for text in doc["claim_texts"]):
                covered += 1
    engineering = {
        # 价格：离线规则引擎，模型调用 0 次
        "price_model_calls": 0,
        "price_note": "纯离线规则引擎运行，无付费模型调用，外部 API 成本为 0",
        # 准确度：需人工复核定论后联合统计，本基准无复核记录，仅可给已判定部分（=精确率）
        "accuracy_note": "已出确定结论并核对的部分即判错精确率；整体准确度需人工复核记录(review.json)后联合统计",
        # 核查覆盖率：金标错误所在句被声明抽取覆盖的比例
        "coverage_gold_span_sentences_pct": pct(covered, gold_total),
        # 不确定性处理：过度转人工率需人工复核反标，本基准无此记录；给转人工规模
        "over_review_note": "过度转人工率需人工复核后统计；以下为转人工规模与占比",
        "needs_review_total": reported,
        "needs_review_per_doc": round(reported / docs, 2) if docs else None,
        "needs_review_share_pct": 100.0 if reported else None,
        "confirmed_error_total": 0,
        # 证据支持度：本基准无已确认错误，不适用
        "evidence_support_note": "本运行 0 条已确认错误(全部转人工)，证据支持度不适用",
        # 人工复核效率：篇均转人工条数、转人工即全部报错
        "review_burden_per_doc": round(reported / docs, 2) if docs else None,
        # 可靠性与运行速度
        "runtime_seconds": results["runtime_seconds"],
        "avg_ms_per_doc": round(results["runtime_seconds"] * 1000 / docs, 1) if docs else None,
        "failures": 0,
        "success_rate_pct": 100.0 if docs else None,
        # 抽取指标：抽取多少 / 确定判断多少 / 留给人工多少
        "claims_total": claim_total,
        "claims_per_doc": round(claim_total / docs, 1) if docs else None,
        "determined_total": 0,
        "left_to_human_total": reported,
    }

    return {
        "docs_evaluated": docs,
        "gold_errors": gold_total, "reported": reported,
        "tp": len(tp), "fp": len(fp), "fn": fn,
        "precision_pct": pct(len(tp), reported),
        "recall_pct": pct(len(tp), gold_total),
        "f1_pct": None if not (len(tp) + len(fp) and gold_total) else
        round(2 * len(tp) * 100.0 / (2 * len(tp) + len(fp) + fn), 1),
        "false_positive_rate_pct": pct(len(fp), reported),
        "by_gold_type": by_gold_type,
        "by_rule": {rule: {"reported": v["reported"], "hit": v["hit"],
                           "precision_pct": v["precision_pct"]}
                    for rule, v in by_rule.items()},
        "engineering": engineering,
        "scope_note": "仅研报自身一致性检查（intrinsic）+声明提取；跨文件数值比对需配对的财报来源，在单文档基准上不适用",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--eval-data", type=Path, default=DEFAULT_EVAL_DATA)
    parser.add_argument("--limit", type=int, default=0, help="评测前 N 篇（0=全部，默认）")
    parser.add_argument("--scenes", type=str, default=None,
                        help="逗号分隔的场景过滤，如 行业研报,个股研报（默认不过滤）")
    args = parser.parse_args()
    if not args.eval_data.is_file():
        parser.error(f"eval_data.json 不存在：{args.eval_data}")
    scenes = [s.strip() for s in args.scenes.split(",")] if args.scenes else None
    results = analyze(args.eval_data, args.limit, scenes)
    metrics = score(results)
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    payload = {"source": str(args.eval_data.resolve()), "limit": args.limit,
               "scenes": results["scenes"], "gold_type_counts": results["gold_type_counts"],
               "metrics": metrics}
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(str(OUT_JSON.resolve()))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())