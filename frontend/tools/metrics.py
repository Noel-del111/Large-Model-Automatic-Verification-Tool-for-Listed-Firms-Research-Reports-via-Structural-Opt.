# -*- coding: utf-8 -*-
"""P3 评测层：把 evaluate_samples 的逐项对照换算成竞赛五指标。

口径（原 rates 精确率仅基于已标注可对照子集；另给全部确认项的可识别上下界）：
- 判错精确率 = 正确识别的错误（系统报错且答案标注错误）÷ 与答案可对照的系统报错数
- 错误召回率 = 正确识别的错误 ÷ 答案标注的错误行数（去重后）
- 正确内容误报率 = 答案标注"正确"却被系统报错的行数 ÷ 答案标注"正确"的行数
- 证据定位准确率 = 已确认错误中来源页码定位正确 ÷ 可判定页码的已确认错误
- 建议完整性   = 已确认错误中同时含建议文本、建议值与依据的比例（逐条检查）
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

TARGETS = {
    "precision": {"label": "已标注子集判错精确率", "sign": "≥", "target": 0.90},
    "recall": {"label": "错误召回率", "sign": "≥", "target": 0.80},
    "false_positive_rate": {"label": "正确内容误报率", "sign": "≤", "target": 0.10},
    "evidence_accuracy": {"label": "证据定位准确率", "sign": "≥", "target": 0.90},
    "suggestion_completeness": {"label": "建议完整性", "sign": "=", "target": 1.0},
}

# 答案表错误类型中文名 → 归一代码（与 evaluate_samples._ERROR_TYPES 口径一致；
# metrics 模块独立运行，不依赖 tools 层）。FinED-Bench 十五类名称可直接复用。
_TYPE_NORMALIZE = {
    "正确": "correct",
    "数值": "number", "数值错误": "number", "计算错误": "number",
    "计算错误/逻辑错误": "number", "逻辑错误": "number",
    "口径错误": "basis_scope", "单位错误": "unit", "数值单位错误": "unit",
    "期间错误": "period", "时间矛盾": "time_conflict",
    "引用错误": "citation", "法规引用错误": "citation",
    "数值不一致错误": "numeric_inconsistency",
    "术语误用": "term_misuse", "语义逻辑矛盾": "semantic_contradiction",
    "数值缺失": "numeric_missing", "冗余语句": "redundant_statement",
    "时间信息非法": "invalid_time", "金融要素缺失": "financial_element_missing",
    "属性值缺失": "attribute_missing", "属性值缺失错误": "attribute_missing",
    "格式错误": "format_error",
    "模糊表达": "ambiguous_expression", "模糊语言": "ambiguous_expression",
    "不一致条款": "clause_conflict", "条款冲突": "clause_conflict",
    "basis": "basis_scope", "scope": "basis_scope", "unit_term_mismatch": "unit",
    "calc_error": "number",
}

_TYPE_LABELS = {
    "correct": "正确", "number": "数值/计算", "basis_scope": "口径", "unit": "单位",
    "period": "期间", "time_conflict": "时间矛盾", "citation": "引用",
    "numeric_inconsistency": "数值不一致", "term_misuse": "术语误用",
    "semantic_contradiction": "语义逻辑矛盾", "numeric_missing": "数值缺失",
    "redundant_statement": "冗余语句", "invalid_time": "时间信息非法",
    "financial_element_missing": "金融要素缺失", "attribute_missing": "属性值缺失",
    "format_error": "格式错误",
    "ambiguous_expression": "模糊语言", "clause_conflict": "不一致条款",
}


def _normalized_type(value: str) -> str:
    text = str(value).strip()
    return _TYPE_NORMALIZE.get(text, text)


def _matched_prediction(row: dict) -> dict:
    # An ambiguous/missing match cannot acquire a TP just because an upstream
    # diagnostic happened to retain one candidate in a prediction field.
    if row.get("match_status") in {"missing", "ambiguous"}:
        return {}
    prediction = row.get("prediction")
    return prediction if isinstance(prediction, dict) else {}


def compute_metrics(evaluation: dict) -> dict:
    rows, extras = [], []
    for case in evaluation.get("cases", []):
        rows.extend(case.get("rows", []))
        extras.extend(case.get("additional_unannotated_predictions", []))

    def unique(seq):
        return [r for r in seq if r.get("duplicate_of_row") is None]

    uniq = unique(rows)
    err_rows = [r for r in uniq if _normalized_type(r["original"].get("error_type", "")) != "correct"]
    ok_rows = [r for r in uniq if _normalized_type(r["original"].get("error_type", "")) == "correct"]

    tp, fn_miss, fp = [], [], []
    for row in err_rows:
        prediction = _matched_prediction(row)
        (tp if prediction.get("status") == "confirmed_error" else fn_miss).append(row)
    fp = [r for r in ok_rows if _matched_prediction(r).get("status") == "confirmed_error"]

    unverified = [{"case": case.get("case", ""), "status": f.get("status", ""),
                   "error_type": f.get("error_type", ""),
                   "text": (f.get("claim") or {}).get("text", "")}
                  for case in evaluation.get("cases", [])
                  for f in case.get("additional_unannotated_predictions", [])]
    for row in uniq:
        if row.get("match_status") == "ambiguous":
            unverified.append({"case": "", "status": (row.get("prediction") or {}).get("status", ""),
                               "error_type": "", "text": row["original"].get("text", ""),
                               "reason": "答案行匹配到多个候选"})

    evidence_rated = [r for r in tp if r.get("source_page_match") is not None]
    evidence_ok = [r for r in evidence_rated if r["source_page_match"] is True]
    complete = [r for r in tp if _suggestion_complete(r.get("prediction") or {})]

    def ratio(top: int, bottom: int) -> float | None:
        return round(top / bottom, 4) if bottom else None

    def type_of(row: dict) -> str:
        return _normalized_type(row["original"].get("error_type", "")) or "other"

    by_type: dict[str, dict] = {}
    for row in err_rows:
        key = type_of(row)
        stats = by_type.setdefault(key, {"label": _TYPE_LABELS.get(key, key), "tp": 0, "fn": 0, "fp": 0})
        if _matched_prediction(row).get("status") == "confirmed_error":
            stats["tp"] += 1
        else:
            stats["fn"] += 1
    for row in ok_rows:
        key = type_of(row)
        stats = by_type.setdefault(key, {"label": _TYPE_LABELS.get(key, key), "tp": 0, "fn": 0, "fp": 0})
        if _matched_prediction(row).get("status") == "confirmed_error":
            stats["fp"] += 1
    by_type = dict(sorted(by_type.items(), key=lambda kv: (kv[1]["tp"] + kv[1]["fn"] + kv[1]["fp"]), reverse=True))

    matched = [r for r in uniq if _matched_prediction(r)]
    labeled_statuses = Counter(_matched_prediction(r).get("status", "unknown") for r in matched)
    extra_statuses = Counter(f.get("status", "unknown") for f in extras)
    all_statuses = labeled_statuses + extra_statuses
    automatic = labeled_statuses["confirmed_error"] + labeled_statuses["no_issue"]
    review_count = all_statuses["needs_review"]
    correct_review = sum(_matched_prediction(r).get("status") == "needs_review" for r in ok_rows)
    type_rated = [r for r in err_rows if _matched_prediction(r)]
    # Type accuracy is independent of status detection. A wrong category on a
    # correctly detected error is still wrong; abstentions and blank types are
    # not dropped from the denominator when the underlying claim was matched.
    def type_matches(row: dict) -> bool:
        explicit = row.get("error_type_match")
        if type(explicit) is bool:
            return explicit
        actual = _normalized_type(_matched_prediction(row).get("error_type", ""))
        expected = type_of(row)
        return bool(actual and expected) and actual == expected
    type_ok = sum(type_matches(row) for row in type_rated)
    unknown_confirmed = extra_statuses["confirmed_error"]
    all_confirmed = len(tp) + len(fp) + unknown_confirmed
    precision_bounds = {
        "lower": ratio(len(tp), all_confirmed),
        "upper": ratio(len(tp) + unknown_confirmed, all_confirmed),
        "known_true_positive": len(tp), "known_false_positive": len(fp),
        "unknown_confirmed": unknown_confirmed, "denominator": all_confirmed,
        "scope": "all_confirmed_predictions_in_supplied_evaluation",
        "assumption": "未标注额外确认项分别全部错误或全部正确；不将待复核候选计为确认项。",
    }
    rates = {
        "precision": ratio(len(tp), len(tp) + len(fp)),
        "recall": ratio(len(tp), len(err_rows)),
        "false_positive_rate": ratio(len(fp), len(ok_rows)),
        "evidence_accuracy": ratio(len(evidence_ok), len(evidence_rated)),
        "suggestion_completeness": ratio(len(complete), len(tp)),
        "extraction_coverage": ratio(len(matched), len(uniq)),
        "automatic_coverage": ratio(automatic, len(uniq)),
        "review_burden": ratio(review_count, sum(all_statuses.values())),
        "correct_content_review_rate": ratio(correct_review, len(ok_rows)),
        "type_accuracy": ratio(type_ok, len(type_rated)),
        "type_recall": ratio(type_ok, len(err_rows)),
    }
    return {
        "counts": {
            "declared_error_rows_unique": len(err_rows),
            "declared_correct_rows_unique": len(ok_rows),
            "true_positive": len(tp),
            "false_negative": len(fn_miss),
            "false_positive": len(fp),
            "unverified_errors": len(unverified),
            "evidence_rated": len(evidence_rated),
            "evidence_ok": len(evidence_ok),
            "suggestion_complete": len(complete),
            "expected_claims_unique": len(uniq),
            "matched_expected_claims": len(matched),
            "unmatched_expected_claims": len(uniq) - len(matched),
            "missing_prediction_rows": sum(not _matched_prediction(r) and r.get("match_status") != "ambiguous" for r in uniq),
            "ambiguous_prediction_rows": sum(r.get("match_status") == "ambiguous" for r in uniq),
            "automatic_expected_claims": automatic,
            "needs_review_expected_claims": labeled_statuses["needs_review"],
            "needs_review_all_predictions": review_count,
            "correct_content_needs_review": correct_review,
            "predictions_total": sum(all_statuses.values()),
            "additional_unannotated_predictions": len(extras),
            "type_rated": len(type_rated), "type_correct": type_ok,
        },
        "rates": rates,
        "rate_scopes": {
            "precision": "已标注且唯一匹配的确认判错子集，不能解释为所有输出的精确率。",
            "recall": "全部去重答案错误行；缺失、歧义、待复核及未报错均为漏检。",
            "extraction_coverage": "唯一匹配答案声明数/全部去重答案声明数；是抽取匹配覆盖代理，无法区分漏抽与匹配失败。",
            "automatic_coverage": "已标注声明中 confirmed_error 或 no_issue 数/全部去重答案声明数；另报准确率。",
            "review_burden": "所有已匹配及额外输出中的 needs_review 数/全部输出数；不是人工时间。",
            "correct_content_review_rate": "标注正确却待复核的声明数/全部标注正确声明数。",
            "type_accuracy": "已唯一匹配的答案错误行中类别正确比例；不以类别一致筛选匹配，也不以已确认状态筛选分母。",
            "type_recall": "类别正确的答案错误行数/全部答案错误行数；缺失预测计入分母。",
            "evidence_accuracy": "可比较的来源页码定位准确率；页码命中不证明语义证据支持。",
            "suggestion_completeness": "建议字段结构完整性；不证明建议或建议值正确。",
        },
        "precision_bounds": precision_bounds,
        "prediction_status_counts": {"annotated_matched": dict(labeled_statuses),
                                     "additional_unannotated": dict(extra_statuses), "all": dict(all_statuses)},
        "unmeasured": {
            "semantic_evidence_support": {"value": None, "reason": "没有独立语义证据审核记录；不能由页码一致推导。"},
            "human_review_seconds": {"value": None, "reason": "本评估输入未关联人工复核计时记录；不以候选数量代替耗时。"},
            "pure_manual_seconds": {"value": None, "reason": "未提供同材料纯人工对照计时。"},
        },
        "targets": {
            key: {"label": item["label"], "sign": item["sign"], "target": item["target"],
                  "measured": rates[key],
                  "met": _met(item["sign"], rates[key], item["target"])}
            for key, item in TARGETS.items()
        },
        "by_type": by_type,
        "unverified_errors": unverified,
        "missing_cases": _missing_cases(evaluation),
    }


def _met(sign: str, measured: float | None, target: float) -> bool | None:
    if measured is None:
        return None
    if sign == "≥":
        return measured >= target
    if sign == "≤":
        return measured <= target
    return measured == target


def _suggestion_complete(prediction: dict) -> bool:
    return bool(prediction.get("suggestion")) \
        and prediction.get("suggested_value") is not None \
        and bool(prediction.get("source_facts"))


def _missing_cases(evaluation: dict) -> list[str]:
    known = set(evaluation.get("expected_cases", ["佛燃能源", "兰石重装", "大地海洋", "群兴玩具"]))
    aliases = {"佛然能源": "佛燃能源"}
    seen = {aliases.get(case.get("case", ""), case.get("case", "")) for case in evaluation.get("cases", [])}
    return sorted(known - seen)


def report_markdown(evaluation: dict, metrics: dict, samples_label: str) -> str:
    counts, rates, targets = metrics["counts"], metrics["rates"], metrics["targets"]
    bounds = metrics["precision_bounds"]
    def percent(value):
        return f"{value:.1%}" if value is not None else "无法计算"
    lines = [
        "# 研报纠错助手 · 评测报告（开发样本回归）",
        "",
        f"- 生成时间：{datetime.now(timezone.utc).isoformat()}",
        f"- 样本目录：{samples_label}",
        f"- 评测性质：{evaluation.get('evaluation_kind', '')}——不是独立盲测，不宣称业务精确率/召回率达到比赛目标",
        "- 精确率仅覆盖已标注且唯一匹配的子集；未标注额外确认项会使总体精确率不可直接识别。",
        f"- 答案行（去重后）：错误 {counts['declared_error_rows_unique']} 条、正确 {counts['declared_correct_rows_unique']} 条；"
        f"缺失对照的样本：{('、'.join(metrics['missing_cases']) or '无')}",
        "",
        "## 一、五指标对照",
        "",
        "| 指标 | 目标 | 实测 | 是否达标 |",
        "| --- | --- | --- | --- |",
    ]
    for key, item in targets.items():
        measured = f"{item['measured']:.1%}" if item["measured"] is not None else "无法计算"
        met = {True: "达标", False: "未达标", None: "—"}[item["met"]]
        lines.append(f"| {item['label']} | {item['sign']} {item['target']:.0%} | {measured} | {met} |")
    lines += [
        "",
        "## 二、逐维度明细",
        "",
        f"- 正确识别的错误（TP）：{counts['true_positive']}；漏报（FN）：{counts['false_negative']}；"
        f"误报（FP）：{counts['false_positive']}",
        f"- 证据定位：可判定 {counts['evidence_rated']} 条、定位正确 {counts['evidence_ok']} 条",
        f"- 建议完整性：已确认错误中 {counts['suggestion_complete']}/{counts['true_positive']} 条含建议文本、建议值与依据",
        f"- 无答案对照的额外输出：{counts['additional_unannotated_predictions']} 条；状态分布："
        f"{json.dumps(metrics['prediction_status_counts']['additional_unannotated'], ensure_ascii=False)}",
        f"- 全部确认项精确率可识别范围：{percent(bounds['lower'])}—{percent(bounds['upper'])}；"
        f"共 {bounds['denominator']} 条确认项，其中 {bounds['unknown_confirmed']} 条未标注。",
        f"- 答案声明 {counts['expected_claims_unique']} 条 → 匹配 {counts['matched_expected_claims']} 条 "
        f"→ 自动判断 {counts['automatic_expected_claims']} 条 / 待复核 {counts['needs_review_expected_claims']} 条；"
        f"未匹配 {counts['unmatched_expected_claims']} 条（其中歧义 {counts['ambiguous_prediction_rows']} 条）。",
        f"- 抽取匹配覆盖代理：{percent(rates['extraction_coverage'])}；自动判断覆盖：{percent(rates['automatic_coverage'])}",
        f"- 全部输出待复核：{counts['needs_review_all_predictions']}/{counts['predictions_total']} "
        f"（{percent(rates['review_burden'])}）；正常内容待复核率：{percent(rates['correct_content_review_rate'])}",
        f"- 独立类型准确率：{counts['type_correct']}/{counts['type_rated']}（{percent(rates['type_accuracy'])}）；"
        f"含缺失预测的类型召回：{percent(rates['type_recall'])}",
        "- 页码定位不等于语义证据支持；语义证据支持度、人工复核耗时及纯人工对照耗时均未测量。",
        "",
        "## 三、逐错误类型明细（答案行归类）",
        "",
        "| 错误类型 | TP | FN | FP | 合计 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for key, stats in metrics["by_type"].items():
        total = stats["tp"] + stats["fn"] + stats["fp"]
        dash = lambda value: str(value) if value else "—"
        lines.append(f"| {stats['label']} | {dash(stats['tp'])} | {dash(stats['fn'])} | {dash(stats['fp'])} | {total} |")
    lines += [
        "",
        "## 四、方法口径",
        "",
    ]
    for item in evaluation.get("methodology", []):
        lines.append(f"- {item}")
    for key, description in metrics["rate_scopes"].items():
        lines.append(f"- {key}：{description}")
    lines.append(f"- 总体精确率上下界假设：{bounds['assumption']}")
    if metrics["unverified_errors"]:
        lines += ["", "## 五、待人工对照的额外输出清单", ""]
        for entry in metrics["unverified_errors"][:30]:
            why = entry.get("reason") or "未出现在答案表中"
            lines.append(f"- [{entry['status']}] {str(entry['text'])[:60]}（{why}）")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluation", type=Path, required=True, help="evaluation.json 路径")
    parser.add_argument("--samples-label", default="(未标注)")
    args = parser.parse_args()
    evaluation = json.loads(args.evaluation.read_text(encoding="utf-8"))
    metrics = compute_metrics(evaluation)
    out_dir = args.evaluation.parent
    (out_dir / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    report = report_markdown(evaluation, metrics, args.samples_label)
    (out_dir / "metrics_report.md").write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
