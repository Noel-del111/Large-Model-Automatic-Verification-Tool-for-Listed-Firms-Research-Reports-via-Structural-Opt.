"""全量运行 FinED 研报：保存真实输出，严格区分自动判错与待复核疑点。"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime
import hashlib
import html
import json
from pathlib import Path
import re
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "repo/factcheck/src"), str(ROOT / "repo/pdfparse/src")]
from yjcheck.adapters import bind_company
from yjcheck.claim_extract import extract_claims
from yjcheck.intrinsic import check_intrinsic_consistency
from yjcheck.error_types import ERROR_TYPES
from yjcheck.models import Block, Document
from yjcheck.pipeline import check_documents, write_result, verify_artifacts

SCENES = {"行业研报", "个股研报"}


def pct(n, d):
    return round(100 * n / d, 4) if d else None


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def make_document(content, input_path, case_id):
    """保留真实行起点；纯文本不伪造 PDF 页码或坐标。"""
    input_path.write_text(content, encoding="utf-8", newline="")
    blocks, offsets, cursor = [], {}, 0
    for line_no, line in enumerate(content.splitlines(keepends=True), 1):
        text = line.rstrip("\r\n")
        if text.strip():
            key = f"line_{line_no}"
            blocks.append(Block(key, text, paragraph=line_no))
            offsets[key] = cursor
        cursor += len(line)
    digest = sha(input_path)
    doc = Document(f"sha256:{digest}", digest, case_id, str(input_path.resolve()), "report", blocks=blocks)
    doc.metadata = {"format": "benchmark_text", "locator": "line + character offset; no PDF geometry"}
    return doc, offsets


def fact_spans(fact, offsets):
    ranges = []
    for e in fact.get("evidence", []):
        offset = offsets.get(e["block_id"])
        start, end = e.get("char_start"), e.get("char_end")
        if offset is not None and isinstance(start, int) and isinstance(end, int) and end > start:
            ranges.append([offset + start, offset + end])
    return ranges


def locate_gold(content, span, declared):
    if not span:
        return None, "empty"
    if isinstance(declared, int) and declared >= 0 and content[declared:declared + len(span)] == span:
        return [declared, declared + len(span)], "original"
    hits = [m.start() for m in re.finditer(re.escape(span), content)]
    if hits:
        start = min(hits, key=lambda i: abs(i - declared)) if isinstance(declared, int) else hits[0]
        return [start, start + len(span)], "exact_reanchored"
    mapping = [i for i, ch in enumerate(content) if not ch.isspace()]
    flat = "".join(content[i] for i in mapping)
    target = re.sub(r"\s+", "", span)
    hits = [m.start() for m in re.finditer(re.escape(target), flat)] if target else []
    if hits:
        start = min(hits, key=lambda i: abs(mapping[i] - declared)) if isinstance(declared, int) else hits[0]
        return [mapping[start], mapping[start + len(target) - 1] + 1], "whitespace_reanchored"
    return None, "unresolved"


def gold_groups(content, errors):
    groups = []
    for error in errors:
        spans, statuses = [], []
        starts = error.get("start_idx") or []
        for index, text in enumerate(error.get("error_span") or []):
            span, status = locate_gold(content, text, starts[index] if index < len(starts) else None)
            if span is not None:
                spans.append(span)
            statuses.append(status)
        groups.append({"type": error["error_type"], "spans": spans,
                       "alignment": statuses, "texts": error.get("error_span", [])})
    return groups


def overlap(a, b):
    return any(x[0] < y[1] and y[0] < x[1] for x in a for y in b)


def match(predictions, golds, strict=True):
    """最大二分匹配：一个候选最多命中一个标注，重复预测不会重复加分。"""
    edges = [[g for g, gold in enumerate(golds)
              if (not strict or pred["type"] == gold["type"]) and overlap(pred["spans"], gold["spans"])]
             for pred in predictions]
    owners = {}
    def visit(p, seen):
        for g in edges[p]:
            if g in seen:
                continue
            seen.add(g)
            if g not in owners or visit(owners[g], seen):
                owners[g] = p
                return True
        return False
    for p in range(len(predictions)):
        visit(p, set())
    return sorted((p, g) for g, p in owners.items())


def metric(label, n=None, d=None, target=None, sign=None, note=""):
    value = pct(n, d) if n is not None and d is not None else None
    met = None if value is None or target is None else (n / d >= target / 100 if sign == ">=" else n / d <= target / 100)
    return {"label": label, "numerator": n, "denominator": d, "percent": value,
            "target_percent": target, "sign": sign, "qualified": met, "note": note}


def score_cases(cases, key, strict=True):
    tp = fp = fn = gold_total = predicted = 0
    by_type = {}
    for case in cases:
        preds, golds = case.get(key, []), case["gold"]
        pairs = match(preds, golds, strict)
        matched = {g for _, g in pairs}
        tp += len(pairs); fp += len(preds) - len(pairs); fn += len(golds) - len(pairs)
        predicted += len(preds); gold_total += len(golds)
        case[key + "_matches" + ("_strict" if strict else "_loose")] = pairs
        for g, gold in enumerate(golds):
            value = by_type.setdefault(gold["type"], {"gold": 0, "hit": 0})
            value["gold"] += 1; value["hit"] += g in matched
    for value in by_type.values():
        value["recall_percent"] = pct(value["hit"], value["gold"])
    return {"tp": tp, "fp": fp, "fn": fn, "predicted": predicted, "gold": gold_total,
            "precision_percent": pct(tp, predicted), "recall_percent": pct(tp, gold_total),
            "f1_percent": pct(2 * tp, 2 * tp + fp + fn),
            "false_discovery_percent": pct(fp, predicted), "by_type": by_type}


def infer(content, case_id, out):
    start = time.perf_counter()
    doc, offsets = make_document(content, out / "inputs" / (case_id + ".txt"), case_id)
    # 两条运行路径只接收正文，不读取 errors、origin_text、output 等答案字段。
    candidate_doc = deepcopy(doc)
    claims = extract_claims(candidate_doc)
    candidates = check_intrinsic_consistency(candidate_doc, claims)
    candidate_rows = []
    for f in candidates:
        candidate_rows.append({"type": ERROR_TYPES[f.error_type].fined_name, "code": f.error_type,
                               "rule_id": f.rule_id, "status": f.status, "text": f.claim.text,
                               "spans": fact_spans(f.claim.to_dict(), offsets)})
    bind_company(doc, [])
    result = check_documents(doc, [], model_config=None)
    directory = write_result(result, out / "runs")
    verified = verify_artifacts(directory)
    confirmed = []
    for f in result["findings"]:
        if f["status"] == "confirmed_error":
            definition = ERROR_TYPES.get(f["error_type"])
            confirmed.append({"type": definition.fined_name if definition else f["error_type"],
                              "spans": fact_spans(f["claim"], offsets)})
    return {"summary": result["summary"], "candidates": candidate_rows, "confirmed": confirmed,
            "claim_spans": [fact_spans(c.to_dict(), offsets) for c in claims],
            "result_path": str(directory.relative_to(out) / "check_result.json"),
            "verified": verified, "elapsed_seconds": time.perf_counter() - start,
            "run_completed": True,
            "trace_count": len(result["model_traces"]),
            "finding_count": len(result["findings"]),
            "input_issue_count": sum(len(x["issues"]) for x in result["input_issues"]),
            "suggestion_complete": sum(bool(f.get("suggestion")) and bool(f["claim"].get("text"))
                and bool(f["claim"].get("evidence")) and bool(f.get("evidence"))
                for f in result["findings"] if f["status"] == "confirmed_error"),
            "suggestion_supported": sum(bool(f.get("evidence")) and bool(f.get("message"))
                for f in result["findings"] if f["status"] == "confirmed_error")}


def render_report(summary, out):
    def row(m):
        val = "无法计算" if m["percent"] is None else f"{m['percent']:.2f}%"
        counts = "" if m["numerator"] is None else f"（{m['numerator']}/{m['denominator']}）"
        target = "—" if m["target_percent"] is None else f"{m['sign']} {m['target_percent']:g}%"
        status = "合格" if m["qualified"] is True else "不合格" if m["qualified"] is False else "无合格线" if m["target_percent"] is None else "无法判定"
        return [m["label"], val + counts, target, status, m["note"]]
    lines = ["# 全量研究报告实测结果", "", f"运行目录：{out.name}", "",
             f"范围：{summary['docs']} 篇（行业研报 242、个股研报 200）；共 {summary['gold_errors']} 个标注错误。",
             "模式：当前离线规则，未配置模型；全量运行，无抽样，未调整检测规则。",
             "输入为基准正文文本，无配对财报/PDF。调用真实核查流水线并单列内部一致性候选检测；不能把待人工确认当成自动判错。", "",
             "## README 竞赛与工程指标", "", "| 指标 | 实测 | 合格线 | 结果 | 口径与限制 |", "|---|---|---|---|---|"]
    lines += ["|" + "|".join(row(m)) + "|" for m in summary["readme_metrics"]]
    lines += ["", "## 内部一致性疑点检测（全部是待人工确认）", "",
              "此表把明确指出错误类型的疑点作为检测候选，按原文字符区间重叠且类型完全相同一对一匹配。阈值仅作 README 目标对照，不代替自动判错验收。", "",
              "| 指标 | 实测 | 目标对照 | 结果 | 说明 |", "|---|---|---|---|---|"]
    lines += ["|" + "|".join(row(m)) + "|" for m in summary["candidate_metrics"]]
    lines += ["", "### 按错误类型统计召回率", "", "| 错误类型 | 命中/标注 | 召回率 |", "|---|---:|---:|"]
    lines += [f"|{k}|{v['hit']}/{v['gold']}|{v['recall_percent']:.2f}%|" for k, v in summary["candidate_score"]["by_type"].items()]
    lines += ["", "## 补充正确片段（微调样例，不混入测试集）", "",
              f"按文档名称与 442 篇研报精确对应，选取并去重 {summary['clean_supplement']['count']} 个 origin_text 正确片段。训练样例不能作为独立测试验收。",
              f"自动确认错误的片段比例：{summary['clean_supplement']['automatic_false_positive_percent']}%；疑点提示片段比例：{summary['clean_supplement']['candidate_false_positive_percent']}%。", "",
              "## 数量、耗时与成本", "",
              f"- 抽取声明：{summary['totals']['claims']}；来源事实：{summary['totals']['source_facts']}。",
              f"- 全部 findings：{summary['totals']['findings']}；确定结论：{summary['totals']['determined']}；待人工确认：{summary['totals']['needs_review']}。",
              f"- 内部一致性疑点：{summary['candidate_score']['predicted']}；严格匹配 TP/FP/FN：{summary['candidate_score']['tp']}/{summary['candidate_score']['fp']}/{summary['candidate_score']['fn']}。",
              f"- 442 篇总处理耗时：{summary['timing']['total_seconds']:.3f} 秒；平均 {summary['timing']['mean_seconds']:.4f} 秒/篇；P95 {summary['timing']['p95_seconds']:.4f} 秒。包含两条核查路径、落盘与哈希校验，不含不存在的 PDF 解析。",
              "- 外部模型调用 0 次，API 费用 0 元；本机电费和硬件成本未计量。数量、时间、费用不是百分比指标，保留实际单位。", "",
              "## 标注与可复现性", "",
              "- 所有错误标注均在预测完成后才进入评分；模型和规则没有读取标准答案。",
              "- 一个标注含多段原文时，命中任一段算命中一次；一个候选最多匹配一个标注。",
              "- 偏移失配使用完整原文精确或去空白匹配重新定位（重复文本选最靠近原偏移的位置），不使用局部模糊片段充当金标。无法定位的错误仍计入全量召回分母。",
              f"- 标注定位统计：{json.dumps(summary['gold_alignment'], ensure_ascii=False)}。",
              "- 未提供完整的正确断言集合、支持范围内全部断言标注、来源页码/坐标真值或人工复核决策，所以对应指标不能伪造为 0% 或 100%。",
              "- full_results.json 保存逐文档候选、金标、匹配与耗时；runs/ 保存 check_result.json、CSV、Markdown 和 manifest；source_manifest.json 保存数据与代码哈希。",
              "- 旧 frontend/tools/fined_eval.py 的 false_positive_rate_pct 使用 FP/(TP+FP)，实际是误报占告警比例；本报告不把它当作 README 的 FP/(FP+TN)。",
              "- 本项目规则已参考过该公开集，结果属于公开开发基准复测，不是未见数据的独立盲测。", ""]
    text = "\n".join(lines)
    (out / "REPORT.md").write_text(text, encoding="utf-8")
    # Standalone readable HTML; Markdown tables rendered without external packages.
    parts, in_table = [], False
    for line in lines:
        if line.startswith("|"):
            if not in_table:
                parts.append('<div class="table"><table>'); in_table = True
            if re.fullmatch(r"[|:\- ]+", line):
                continue
            parts.append("<tr>" + "".join("<td>" + html.escape(c) + "</td>" for c in line.strip("|").split("|")) + "</tr>")
        else:
            if in_table:
                parts.append("</table></div>"); in_table = False
            if line.startswith("#"):
                level = len(line) - len(line.lstrip("#"))
                parts.append(f"<h{level}>" + html.escape(line.lstrip("# ")) + f"</h{level}>")
            elif line:
                parts.append("<p>" + html.escape(line) + "</p>")
    if in_table:
        parts.append("</table></div>")
    (out / "REPORT.html").write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>全量研报实测</title><style>body{max-width:1250px;margin:40px auto;padding:0 24px;font:15px/1.8 system-ui,"Microsoft YaHei";color:#20324c;background:#f7f9fc}h1,h2{color:#183c73}h2{margin-top:36px}.table{overflow:auto;background:white;border:1px solid #dce4ee;border-radius:10px}table{border-collapse:collapse;width:100%;min-width:750px}td{padding:12px;border-bottom:1px solid #e0e6ef}tr:first-child{font-weight:700;background:#eaf0fb}p{margin:9px 0}</style><body>' + "\n".join(parts) + '</body></html>', encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=ROOT / "测评集/测评集/FinED-Bench-main")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve(); out.mkdir(parents=True, exist_ok=False); (out / "inputs").mkdir()
    path = args.data / "fined_bench/eval_data.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    selected = [(i, row) for i, row in enumerate(data) if row["scene"] in SCENES]
    code_paths = sorted((ROOT / "repo/factcheck/src/yjcheck").glob("*.py")) + [Path(__file__), ROOT / "repo/README.md"]
    code_hashes = {str(p.relative_to(ROOT)): sha(p) for p in code_paths}
    cases = []
    for n, (index, item) in enumerate(selected, 1):
        case_id = f"eval_{index:04d}"
        case = {"case_id": case_id, "dataset_index": index, "title": item["title"], "scene": item["scene"]}
        try:
            case.update(infer(item["content"], case_id, out))
        except Exception:
            case.update(run_completed=False, verified=False, failure=traceback.format_exc(), candidates=[], confirmed=[])
        # Only score after the checker returned.
        case["gold"] = gold_groups(item["content"], item["errors"])
        cases.append(case)
        if n % 50 == 0 or n == len(selected):
            print(f"Completed {n}/{len(selected)}; failures={sum(not c['run_completed'] for c in cases)}", flush=True)
    auto = score_cases(cases, "confirmed")
    candidate = score_cases(cases, "candidates")
    loose = score_cases(cases, "candidates", strict=False)
    return finish(args, out, selected, cases, auto, candidate, loose, code_hashes)


def finish(args, out, selected, cases, auto, candidate, loose, code_hashes):
    keys = ("claims", "source_facts", "confirmed_error", "needs_review", "no_issue")
    totals = {k: sum(c.get("summary", {}).get(k, 0) for c in cases) for k in keys}
    totals["findings"] = sum(c.get("finding_count", 0) for c in cases)
    totals["determined"] = totals["confirmed_error"] + totals["no_issue"]
    clean = []
    names = {row["title"] for _, row in selected}
    clean_path = args.data / "sft_data/sft_data_wo_errors.json"
    seen = set()
    for item in json.loads(clean_path.read_text(encoding="utf-8")):
        content = item["origin_text"]
        if item["name"] not in names or content in seen:
            continue
        seen.add(content)
        result = infer(content, f"clean_{len(clean):03d}", out)
        clean.append({"name": item["name"], **result})
    known = len(clean)
    clean_summary = {"count": known, "automatic_false_positive_percent": pct(sum(bool(c["confirmed"]) for c in clean), known),
                     "candidate_false_positive_percent": pct(sum(bool(c["candidates"]) for c in clean), known)}
    docs = len(cases); gold = auto["gold"]; completed = sum(c["run_completed"] for c in cases)
    m = [metric("判错精确率（自动确认）", auto["tp"], auto["predicted"], 90, ">=", "没有自动报错时分母为零，不记为100%"),
         metric("错误召回率（自动确认）", auto["tp"], gold, 80, ">=", "全量标注错误为分母；待人工确认不算自动命中"),
         metric("正确内容误报率", target=10, sign="<=", note="主测试集无完整正确断言标注；未标错内容不能直接当负样本"),
         metric("证据定位准确率", target=90, sign=">=", note="缺少原始PDF、来源财报与页码真值；字符匹配不能代替来源页码定位"),
         metric("建议完整性", sum(c.get("suggestion_complete", 0) for c in cases), totals["confirmed_error"], note="无自动确认错误；README未给百分比合格线"),
         metric("整体判定正确率", note="缺少完整负样本与人工复核定论，不能以自身状态充当正确答案"),
         metric("研报断言覆盖率", note="缺少支持范围内断言总数的人工标注"),
         metric("过度转人工率", note="缺少人工标注的本可自动判定项，不能把100%转人工直接当过度转人工率"),
         metric("建议带证据比例", sum(c.get("suggestion_supported", 0) for c in cases), totals["confirmed_error"], 100, ">=", "零确认错误，不记为100%"),
         metric("转人工占比", totals["needs_review"], totals["findings"], note="真实全流程findings；包含输入异常与覆盖提示"),
         metric("确定判断占比", totals["determined"], totals["findings"]),
         metric("程序运行失败率", docs - completed, docs, note="Python异常；不等同于财务结论正确或解析失败页比例"),
         metric("全量执行完成率", completed, docs),
         metric("产物哈希校验通过率", sum(c["verified"] for c in cases), completed),
         metric("有输入质量问题的研报比例", sum(bool(c.get("summary", {}).get("input_issues")) for c in cases), docs),
         metric("有抽取声明的研报比例", sum(bool(c.get("summary", {}).get("claims")) for c in cases), docs, note="按文档统计，不能代替断言覆盖率"),
         metric("解析失败页比例", note="输入只有文本，未运行PDF解析，无真实页面分母"),
         metric("复核定位具备页码与坐标比例", note="纯文本不提供页码坐标；不能捏造一页PDF计为通过")]
    cm = [metric("疑点检测精确率", candidate["tp"], candidate["predicted"], 90, ">=", "严格类型与区间匹配；仅疑点，不是自动确认"),
          metric("疑点检测召回率", candidate["tp"], gold, 80, ">="),
          metric("疑点检测F1", 2*candidate["tp"], 2*candidate["tp"]+candidate["fp"]+candidate["fn"]),
          metric("误报占告警比例", candidate["fp"], candidate["predicted"], note="FP/(TP+FP)，不是README正确内容误报率"),
          metric("只看定位的疑点精确率", loose["tp"], loose["predicted"], note="字符区间，不评价来源页码"),
          metric("只看定位的疑点召回率", loose["tp"], gold)]
    durations = sorted(c.get("elapsed_seconds", 0) for c in cases)
    alignment = Counter(status for c in cases for g in c["gold"] for status in g["alignment"])
    summary = {"docs": docs, "gold_errors": gold, "totals": totals, "automatic_score": auto,
               "candidate_score": candidate, "loose_score": loose,
               "readme_metrics": m, "candidate_metrics": cm, "clean_supplement": clean_summary,
               "gold_alignment": dict(alignment),
               "timing": {"total_seconds": sum(durations), "mean_seconds": sum(durations)/docs,
                          "p95_seconds": durations[min(docs-1, int(.95*docs))]},
               "model_calls": sum(c.get("trace_count", 0) for c in cases)}
    after = {p: sha(ROOT / p) for p in code_hashes}
    if after != code_hashes:
        raise RuntimeError("代码在评测中发生变化，不能合并为同一版本结果")
    data_paths = [args.data / "fined_bench/eval_data.json", args.data / "fined_bench/eval_data_hard.json", clean_path]
    manifest = {"code_sha256": code_hashes, "dataset_sha256": {str(p.resolve()): sha(p) for p in data_paths},
                "python": sys.version, "mode": "offline; model_config=None", "scene_filter": sorted(SCENES),
                "inference_has_gold_access": False, "created_at": datetime.now().astimezone().isoformat(),
                "code_unchanged_during_run": True}
    for name, value in [("metrics.json", summary), ("full_results.json", {"cases": cases, "clean_cases": clean}), ("source_manifest.json", manifest)]:
        (out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    render_report(summary, out)
    print(json.dumps({"output": str(out), "totals": totals, "candidate": candidate, "clean": clean_summary}, ensure_ascii=False, indent=2))
    return 0 if all(c["run_completed"] for c in cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
