"""Report the selected paired E regression without mixing old runs or text gold."""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def selected_totals(evaluation):
    # The runs/ directory can contain several revisions. The evaluation selects
    # the four authoritative cases; scanning all result files double-counts them.
    totals = Counter()
    for case in evaluation["cases"]:
        summary = case["checker_summary"]
        totals.update({key: summary.get(key, 0) for key in (
            "claims", "source_facts", "automatic_claims", "review_claims", "confirmed_error",
            "needs_review", "no_issue", "model_candidates_aligned", "text_review_total_hints")})
    rows = [row for case in evaluation["cases"] for row in case["rows"] if not row.get("duplicate_of_row")]
    correct = [row for row in rows if row["original"]["error_type"] == "正确"]
    errors = [row for row in rows if row["original"]["error_type"] != "正确"]
    totals.update({"gold_unique_claims": len(rows), "gold_correct_claims": len(correct), "gold_error_claims": len(errors),
                   "gold_correct_claims_flagged_error": sum((row.get("prediction") or {}).get("status") == "confirmed_error" for row in correct),
                   "gold_error_claims_detected": sum((row.get("prediction") or {}).get("status") == "confirmed_error" for row in errors),
                   "gold_source_page_matches": sum(row.get("source_page_match") is True for row in rows),
                   "gold_error_source_page_matches": sum(row.get("source_page_match") is True for row in errors)})
    return dict(totals)


def render(evaluation, evaluation_path, out):
    totals = selected_totals(evaluation)
    summary = evaluation["summary"]
    semantic = summary["semantic_unique_claims"]
    kinds = summary["error_type_error_rows"]
    usage = summary["unique_model_usage"]
    relative = Path(os.path.relpath(Path(evaluation_path).resolve(), Path(out).resolve().parent)).as_posix()
    pct = 100 * totals["automatic_claims"] / totals["claims"] if totals["claims"] else 0
    lines = ["# 第二版业务配对回归报告", "",
             "生成时间（UTC）：" + datetime.now(timezone.utc).isoformat(timespec="seconds") + "。", "",
             "本报告只汇总 evaluation.json 指定的四组 E 样本，不扫描同目录中的旧回放结果。它们是已参与开发的研报—财报配对回归材料，不是独立盲测；与 FinED-Bench 公开文本检测分开报告。", "",
             "## 抽取、自动结论与复核负担", "",
             "| 材料 | 抽取声明 | 来源事实 | 自动判断声明 | 待复核声明 | 模型重复抽取对齐 | 额外文本待复核提示 |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for case in evaluation["cases"]:
        s = case["checker_summary"]
        lines.append("| " + str(case["case"]).replace("|", "\\|") + " | " + " | ".join(str(s.get(k, 0)) for k in
            ("claims", "source_facts", "automatic_claims", "review_claims", "model_candidates_aligned", "text_review_total_hints")) + " |")
    lines += ["", f"四组共抽取 {totals['claims']} 条声明、{totals['source_facts']} 条来源事实；其中 {totals['automatic_claims']} 条得到自动结论，{totals['review_claims']} 条待复核，已抽取声明内的自动判断比例为 {pct:.2f}%。这不是全文所有应核查事实的覆盖率，也不是自动结论的独立准确率。",
              f"共有 {totals['model_candidates_aligned']} 个模型重复抽取候选按同一原文数值位置对齐，保留其原始字段和未验证语义，不作为新增抽取能力。传统配对待复核 {totals['review_claims']} 条，另有文本检测提示 {totals['text_review_total_hints']} 条，两者分别计数。", "",
              "## 已标注声明的结果", "", "| 指标 | 结果 | 含义 |", "| --- | ---: | --- |",
              f"| 唯一声明状态匹配 | {semantic['matched']}/{semantic['total']} | 只评价答案表覆盖的声明 |",
              f"| 错误声明检测 | {totals['gold_error_claims_detected']}/{totals['gold_error_claims']} | 开发回归中的已标注错误 |",
              f"| 正确声明被报错 | {totals['gold_correct_claims_flagged_error']}/{totals['gold_correct_claims']} | 不能外推为正确研报全文误报率 |",
              f"| 错误类型匹配 | {kinds['matched']}/{kinds['total']} | 状态正确仍可能分类不同 |",
              f"| 唯一声明来源页匹配 | {totals['gold_source_page_matches']}/{totals['gold_unique_claims']} | 对照答案表页码的定位口径 |",
              f"| 错误声明来源页匹配 | {totals['gold_error_source_page_matches']}/{totals['gold_error_claims']} | 页码一致不等于独立核验过语义证据 |", "",
              f"另有 {summary['additional_unannotated_predictions']} 条配对输出未被答案表标注覆盖，不能默认视为正确。原表 {summary['original_rows']} 行含 {summary['duplicates']} 行重复，唯一声明分母为 {summary['unique_expected_claims']}。文本补充提示没有穷尽标注，因此不计算其 P/R/F1。", "",
              "## 输入质量与费用", ""]
    for case in evaluation["cases"]:
        if case["input_issues"]:
            lines.append(f"- {case['case']}仍有解析质量提示：" + "；".join(", ".join(i["issues"]) for i in case["input_issues"]) + "。有已匹配声明不表示该文档输入整体完整无缺。")
        reasons = case["checker_summary"].get("needs_review_by_rule", {})
        if reasons:
            lines.append(f"- {case['case']}配对待复核原因：" + "、".join(f"{k} × {v}" for k, v in reasons.items()) + "。")
    lines += ["", f"原始真实模型共 {usage['calls']} 个唯一调用，输入 {usage['input_tokens']:,} token、输出 {usage['output_tokens']:,} token；按当时保守费率记录 {usage['accounted_cny']:.6f} 元，原调用累计耗时 {usage['duration_seconds']:.3f} 秒。此成本已包含在共享总账，不能再与总账相加。回放墙钟不代表新部署时延。"]
    replay_path = Path(evaluation_path).parent / "replay_audit.json"
    if replay_path.exists():
        replay = read(replay_path)
        lines += [f"该次回放新增 API 调用 {replay['new_api_calls']} 次，复用 {replay['replayed_unique_calls']} 个原调用。财务抽取使用保存的响应字符串；文本检测使用保存的原始候选对象重新序列化，未声称服务器响应空白字节完全相同。"]
    lines += ["", "证据语义支持度、过度转人工比例、真人复核效率仍缺独立标注或真人计时，保留为未测量。16 条正常文本候选尚待人工签核，不能用此处 10 条正确的财务声明替代正常研报文本评测。", "",
              f"可追溯来源：[配对评测与逐行对照](<{relative}>)。"]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluation", type=Path, default=ROOT / "data/v2/paired-model-low-alignment-replay/evaluation.json")
    parser.add_argument("--out", type=Path, default=ROOT / "docs/V2_PAIRED_RESULTS.md")
    args = parser.parse_args(argv)
    text = render(read(args.evaluation), args.evaluation, args.out)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(args.out.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
