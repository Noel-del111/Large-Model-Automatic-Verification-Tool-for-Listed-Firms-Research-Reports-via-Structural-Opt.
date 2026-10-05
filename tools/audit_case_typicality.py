"""Audit diagnostic coverage, not statistical representativeness, without inference.

The frozen scorer is imported only by an isolated worker. Outputs contain counts,
identities and mechanical labels, never full reports or inferred business causes.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys


CORE = ("invalid_anchor", "same_type_partial_overlap", "other_type_full_containment",
        "one_to_one_competition", "no_anchored_overlap")
DEFINITIONS = {
    "invalid_anchor": "FP的最终提示明确标记invalid_anchor；不是所有FP，也不推断OCR失败。",
    "same_type_partial_overlap": "存在同类型预测/gold空间重叠，但该预测不能严格完整包含该gold全部span。",
    "other_type_full_containment": "存在不同类型预测/gold，预测严格完整包含gold；不证明reason识别了同一个异常。",
    "one_to_one_competition": "存在同类型完整包含的兼容边，但该边另一端已被确定性一对一匹配分配给其他实例。",
    "no_anchored_overlap": "FN与所有最终有效提示均无空间重叠；FP须自身有有效span且与所有可评gold均无重叠。",
    "other_type_partial_overlap": "不同类型仅部分空间重叠，不满足完整包含；只作几何关系描述。",
    "fn_raw_candidates_empty": "FN所在整篇report.raw_candidates为空；需另看执行状态，不等于已证实的生成根因。",
    "fn_raw_present_no_exact_quote_overlap": "整篇raw候选非空，但其所有引文在原文的精确出现位置均不与该FN重叠；改写引文可能造成零命中。",
    "fn_raw_quote_overlap_without_final_overlap": "FN无最终提示重叠，但至少一个raw引文的某个精确出现位置重叠；不说明其reason识别该错误。",
    "fn_no_raw_candidate_of_gold_type": "整篇raw候选中没有gold类型；可能与分类差异重叠，不称该类型没被检查。",
    "invalid_anchor_exact_quote_absent": "invalid_anchor FP至少有一个原始引文在原文精确零命中；不做空白或标点模糊恢复。",
    "invalid_anchor_exact_quote_ambiguous": "invalid_anchor FP至少有一个原始引文有多个精确出现位置；标签可与零命中并存。",
}
LIMITATIONS = [
    "目的性诊断样本；类别配额、锚点优先和尾部补样改变入选概率，不能估计总体根因频率或总体业务准确率。",
    "机械标签允许重叠，同一业务问题也可能同时产生一个FP和一个FN；各标签计数不能相加当作独立根因。",
    "覆盖计数只统计每份case的焦点实例，不将其related_predictions/related_golds附带讨论算作另一个已选实例。因此焦点FN切片为0不表示既有FP分析从未涉及相似链路。",
    "原文包含、引文重叠和提示中列出某类型不等于模型实际检查或理解了该异常；需逐case人工签核。",
    "本批是文本研报，不能评价PDF/OCR、跨文件检索、无错误真阴性或竞赛端到端能力；全量最长仍不足8000字。",
    "全量指research442-rerun-20261005的hybrid冻结批次，不与截图旧批或当前源码重跑成绩混合。",
    "本地manifest提供文件一致性检查，不是独立签名的来源保证；审计不改冻结输入、评分或人工结论。",
]


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def rows(path):
    return [json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines() if x.strip()]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def identity(row):
    return row["document_id"], row["result"], row["all_hints_index"], row["gold_index"]


def kind(obj):
    return obj.get("error_type", obj.get("type"))


def anchored(obj):
    return [s for s in obj.get("spans", []) if type(s.get("start")) is int
            and type(s.get("end")) is int and 0 <= s["start"] < s["end"]
            and isinstance(s.get("text"), str) and len(s["text"]) == s["end"] - s["start"]]


def overlap(a, b):
    return any(x["start"] < y["end"] and y["start"] < x["end"]
               for x in anchored(a) for y in anchored(b))


def contains(a, b):
    """Frozen full-span semantics: a union of short spans is insufficient."""
    ps, gs = anchored(a), anchored(b)
    if not ps or not gs or len(ps) != len(a.get("spans", [])) or len(gs) != len(b.get("spans", [])):
        return False
    return all(any(p["start"] <= g["start"] and p["end"] >= g["end"]
                   and p["text"][g["start"] - p["start"]:g["end"] - p["start"]] == g["text"]
                   for p in ps) for g in gs)


def occurrences(text, quote):
    if not isinstance(quote, str) or not quote:
        return []
    start, found = -1, []
    while (start := text.find(quote, start + 1)) >= 0:
        found.append({"start": start, "end": start + len(quote), "text": quote})
    return found


def mechanical_labels(case, doc):
    """Relational observations only; labels intentionally overlap."""
    is_fn = case["result"] == "FN"
    pairs = ([(p, case["gold"], i, case["gold_index"]) for i, p in enumerate(doc["hints"])]
             if is_fn else [(case["prediction"], g, case["all_hints_index"], j)
                            for j, g in enumerate(doc["golds"]) if g.get("scorable", True)])
    labels = set()
    for p, g, pi, gi in pairs:
        same, full, touches = kind(p) == kind(g), contains(p, g), overlap(p, g)
        if same and touches and not full:
            labels.add("same_type_partial_overlap")
        if not same and full:
            labels.add("other_type_full_containment")
        if not same and touches and not full:
            labels.add("other_type_partial_overlap")
        if same and full and any((a == pi and b != gi) if is_fn else (b == gi and a != pi)
                                 for a, b in doc["matched_pairs"]):
            labels.add("one_to_one_competition")
    no_overlap = not any(overlap(p, g) for p, g, _, _ in pairs)
    if no_overlap and (is_fn or anchored(case["prediction"])):
        labels.add("no_anchored_overlap")
    if not is_fn and case["prediction"].get("invalid_anchor"):
        labels.add("invalid_anchor")
        counts = [len(occurrences(doc["content"], s.get("text")))
                  for s in case["prediction"].get("original_spans", []) or []]
        if any(n == 0 for n in counts):
            labels.add("invalid_anchor_exact_quote_absent")
        if any(n > 1 for n in counts):
            labels.add("invalid_anchor_exact_quote_ambiguous")
    raw = doc["raw_candidates"]
    if is_fn:
        if not raw:
            labels.add("fn_raw_candidates_empty")
        elif not any(overlap({"spans": occurrences(doc["content"], s.get("text"))}, case["gold"])
                     for r in raw for s in r["candidate"].get("spans", [])):
            labels.add("fn_raw_present_no_exact_quote_overlap")
        elif no_overlap:
            labels.add("fn_raw_quote_overlap_without_final_overlap")
        if not any(kind(r["candidate"]) == kind(case["gold"]) for r in raw):
            labels.add("fn_no_raw_candidate_of_gold_type")
    return sorted(labels)


def length_bucket(n):
    return "<2000" if n < 2000 else "2000–3999" if n < 4000 else "4000–7999" if n < 8000 else ">=8000"


def f1_bucket(n):
    return "0" if n == 0 else "(0,0.4]" if n <= .4 else "(0.4,0.6]" if n <= .6 else "(0.6,0.8]" if n <= .8 else "(0.8,1]"


def concentration(records):
    counts = Counter(r["document_id"] for r in records)
    n = len(records)
    top = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
    return {"instances": n, "unique_documents": len(counts),
            "max_instances_per_document": max(counts.values(), default=0),
            "top5_instance_share": round(sum(x[1] for x in top[:5]) / n, 6) if n else 0,
            "hhi": round(sum((v / n) ** 2 for v in counts.values()), 6) if n else 0,
            "top_documents": [{"document_id": did, "instances": count} for did, count in top[:5]]}


def selected_cohorts(selection, canonical):
    indexed = {identity(c): c for c in canonical}
    if len(indexed) != len(canonical):
        raise ValueError("Duplicate canonical identities")
    groups = {"baseline": [], "supplemental": [], "controls": []}
    seen, ids = set(), set()
    for row in selection["cases"]:
        key = identity(row)
        if key in seen or row["case_id"] in ids:
            raise ValueError("Duplicate selected identity or case_id")
        seen.add(key)
        ids.add(row["case_id"])
        if key not in indexed:
            raise ValueError("Selection identity not in frozen hybrid cases")
        canonical_row = indexed[key]
        if row["error_type"] != kind(canonical_row["gold"] if row["result"] == "FN" else canonical_row["prediction"]):
            raise ValueError("Selection error_type differs from frozen case")
        group = {"diagnostic": "baseline", "supplemental": "supplemental", "control": "controls"}.get(row["cohort"])
        if group is None or ((group == "controls") != (row["result"] == "TP")):
            raise ValueError("Cohort/result mismatch")
        groups[group].append({**canonical_row, "case_id": row["case_id"]})
    groups["expanded"] = groups["baseline"] + groups["supplemental"]
    return groups


def coverage_goals(records, docs):
    """Coverage obligations, not quota/probability sampling targets."""
    goals = set()
    for c in records:
        d = docs[c["document_id"]]
        goals.update("mechanical:" + x for x in c["mechanical_labels"])
        if d["f1"] == 0:
            goals.add("zero_f1_document:" + c["document_id"])
        if d["length_chars"] >= 4000:
            goals.add("length_scene:" + length_bucket(d["length_chars"]) + ":" + d["scene"])
        goals.add("scene:" + d["scene"])
    return goals


def recommend(full, baseline, supplemental, docs, limit=8):
    """Validate curated supplement first, then greedily cover any remaining gaps."""
    missing = coverage_goals(full, docs) - coverage_goals(baseline, docs)
    chosen, used = [], {identity(c) for c in baseline}
    for case in supplemental:
        if len(chosen) >= limit:
            break
        covered = missing & coverage_goals([case], docs)
        if covered:
            chosen.append({"identity": list(identity(case)), "case_id": case["case_id"],
                           "error_type": kind(case["gold"] if case["result"] == "FN" else case["prediction"]),
                           "covers": sorted(covered), "applied": True})
            missing -= covered
        used.add(identity(case))
    while missing and len(chosen) < limit:
        eligible = [(c, missing & coverage_goals([c], docs)) for c in full if identity(c) not in used]
        eligible = [(c, g) for c, g in eligible if g]
        if not eligible:
            break
        c, hit = min(eligible, key=lambda x: (-len(x[1]), docs[x[0]["document_id"]]["f1"],
                                             x[0]["result"] != "FN", json.dumps(identity(x[0]))))
        chosen.append({"identity": list(identity(c)), "error_type": kind(c["gold"] if c["result"] == "FN" else c["prediction"]),
                       "covers": sorted(hit), "applied": False})
        used.add(identity(c))
        missing -= hit
    return chosen, sorted(missing)


def summarize(canonical, docs, selection, supported_types):
    groups = selected_cohorts(selection, canonical)
    full = [c for c in canonical if c["result"] in {"FP", "FN"}]
    populations = {"full": full, "baseline": groups["baseline"], "expanded": groups["expanded"]}
    for records in populations.values():
        for c in records:
            c["mechanical_labels"] = mechanical_labels(c, docs[c["document_id"]])
    # Supplemental objects are the same references included in expanded.
    counts = {name: len(rs) for name, rs in populations.items()}
    strata = []
    for error_type in sorted(supported_types):
        for result in ("FP", "FN"):
            row = {"error_type": error_type, "result": result}
            for name, rs in populations.items():
                n = sum(c["result"] == result and kind(c["gold"] if result == "FN" else c["prediction"]) == error_type for c in rs)
                row[name] = n
                row[name + "_share"] = n / len(rs) if rs else 0
            row["baseline_minus_full_percentage_points"] = 100 * (row["baseline_share"] - row["full_share"])
            row["expanded_minus_full_percentage_points"] = 100 * (row["expanded_share"] - row["full_share"])
            strata.append(row)
    distributions = {}
    for dimension, key in (("scene", lambda d: d["scene"]), ("length", lambda d: length_bucket(d["length_chars"])),
                           ("document_f1", lambda d: f1_bucket(d["f1"]))):
        bins = sorted({key(d) for d in docs.values()})
        if dimension == "length":
            bins = ["<2000", "2000–3999", "4000–7999", ">=8000"]
        dist = []
        for bucket in bins:
            row = {"bucket": bucket, "all_documents": sum(key(d) == bucket for d in docs.values())}
            for name, rs in populations.items():
                subset = [c for c in rs if key(docs[c["document_id"]]) == bucket]
                row[name + "_failures"] = len(subset)
                row[name + "_failure_share"] = len(subset) / len(rs) if rs else 0
                row[name + "_documents"] = len({c["document_id"] for c in subset})
            dist.append(row)
        distributions[dimension] = dist
    mechanisms = []
    for label, definition in DEFINITIONS.items():
        applicable = {"FN"} if label.startswith("fn_") else {"FP"} if label.startswith("invalid_anchor") else {"FP", "FN"}
        row = {"label": label, "definition": definition, "core": label in CORE, "eligible_results": sorted(applicable)}
        for name, rs in populations.items():
            hits = [c for c in rs if label in c["mechanical_labels"]]
            row[name] = len(hits)
            row[name + "_eligible"] = sum(c["result"] in applicable for c in rs)
            row[name + "_by_result"] = dict(Counter(c["result"] for c in hits))
        mechanisms.append(row)
    goals = {name: coverage_goals(rs, docs) for name, rs in populations.items()}
    recs, remaining = recommend(full, groups["baseline"], groups["supplemental"], docs)
    zero = [{"document_id": did, "length_chars": d["length_chars"], "scene": d["scene"],
             "counts": d["counts"], "raw_candidates": len(d["raw_candidates"]),
             "execution_complete": d["execution_complete"],
             **{name + "_case_ids": [c["case_id"] for c in rs if c["document_id"] == did]
                for name, rs in populations.items() if name != "full"}}
            for did, d in sorted(docs.items()) if d["f1"] == 0]
    return {"schema_version": 1, "scope": "hybrid frozen error instances; diagnostic coverage, not statistical representativeness",
            "population": {"documents": len(docs), "failures": counts, "results": {n: dict(Counter(c["result"] for c in rs)) for n, rs in populations.items()},
                           "tp_controls": len(groups["controls"]), "supplemental_failures": len(groups["supplemental"]),
                           "all_selected_documents_including_controls": len({c["document_id"] for c in selection["cases"]})},
            "category_result_distribution": strata,
            "category_result_total_variation": {n: .5 * sum(abs(r[n + "_share"] - r["full_share"]) for r in strata) for n in ("baseline", "expanded")},
            "document_concentration": {n: concentration(rs) for n, rs in populations.items()},
            "distributions": distributions, "mechanical_labels": mechanisms, "zero_f1_documents": zero,
            "coverage_gaps": {n: sorted(goals["full"] - goals[n]) for n in ("baseline", "expanded")},
            "supplement_recommendations": recs, "remaining_after_recommendations": remaining,
            "compact_case_labels": [{"identity": list(identity(c)), "case_id": c.get("case_id"), "mechanical_labels": c["mechanical_labels"]} for c in groups["expanded"]],
            "limitations": LIMITATIONS, "new_api_calls": 0}


def verify_manifest(base, manifest):
    for entry in read(manifest):
        file = (base / entry["path"]).resolve()
        if not file.is_relative_to(base.resolve()) or sha(file) != entry["sha256"]:
            raise ValueError("Frozen manifest mismatch: " + entry["path"])


def load_frozen(package):
    verify_manifest(package, package / "manifest.json")
    verify_manifest(package / "verification", package / "verification/manifest.json")
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(package / "source/evals"), str(package / "source/factcheck/src")]
    from run_v2 import validate_predictions
    from yjcheck.review_hints import all_review_hints
    from fined_bench_eval import _contains_error, _maximum_matching
    inputs = {r["doc_id"]: r for r in rows(package / "inputs/inputs.research442.jsonl")}
    golds = {r["document_id"]: r for r in rows(package / "inputs/gold.research442.jsonl")}
    cases = [c for c in rows(package / "verification/cases.jsonl") if c["arm"] == "hybrid"]
    by_doc = defaultdict(list)
    for c in cases:
        by_doc[c["document_id"]].append(c)
    docs, actual = {}, set()
    for file in sorted((package / "run/predictions/hybrid").glob("*.json")):
        report = read(file)
        did = report["document_id"]
        inp, gs = inputs[did], golds[did]["errors"]
        hints = all_review_hints(validate_predictions(deepcopy(report), inp["content"]))["errors"]
        for p in hints:
            for g in gs:
                if contains(p, g) != _contains_error(p, g):
                    raise ValueError("Local containment differs from frozen scorer")
        pairs = _maximum_matching(hints, [g for g in gs if g.get("scorable", True)],
                                  lambda p, g: _contains_error(p, g) and kind(p) == kind(g))
        pi, gi = {id(p): i for i, p in enumerate(hints)}, {id(g): j for j, g in enumerate(gs)}
        match = [(pi[id(p)], gi[id(g)]) for p, g in pairs]
        mp, mg = {i for i, _ in match}, {j for _, j in match}
        outcomes = [("TP", i, j) for i, j in match] + [("FP", i, None) for i in range(len(hints)) if i not in mp] + [("FN", None, j) for j, g in enumerate(gs) if g.get("scorable", True) and j not in mg]
        for label, i, j in outcomes:
            actual.add((did, label, i, j))
        for c in by_doc[did]:
            if c["prediction"] is not None and c["prediction"] != hints[c["all_hints_index"]]:
                raise ValueError("Canonical prediction differs from frozen report")
            if c["gold"] is not None and c["gold"] != gs[c["gold_index"]]:
                raise ValueError("Canonical gold differs from original array")
        count = Counter(label for label, _, _ in outcomes)
        denominator = 2 * count["TP"] + count["FP"] + count["FN"]
        docs[did] = {"content": inp["content"], "length_chars": len(inp["content"]), "scene": inp["scene"],
                     "hints": hints, "golds": gs, "matched_pairs": match, "raw_candidates": report["raw_candidates"],
                     "f1": 2 * count["TP"] / denominator if denominator else 0,
                     "counts": dict(count), "execution_complete": report["coverage"]["execution_complete"]}
    if set(docs) != set(inputs) or actual != {identity(c) for c in cases}:
        raise ValueError("Frozen replay identities or document coverage differ")
    supported = report["coverage"]["supported_error_types"]
    return cases, docs, supported


def markdown(data):
    pop = data["population"]
    baseline_n, expanded_n = pop["failures"]["baseline"], pop["failures"]["expanded"]
    lines = ["# 逐 case 样本典型性审计", "", "典型性在此指诊断现象与困难切片覆盖，不指统计代表性。所有机械标签都不是全量根因裁定。", "",
             f"冻结批次：research442-rerun-20261005 / hybrid，{pop['documents']}篇，{pop['failures']['full']}个FP/FN。原始失败样本{pop['failures']['baseline']}个，补样后{pop['failures']['expanded']}个；{pop['tp_controls']}个TP对照不进入失败样本分母。", "",
             "## 类别 × FP/FN", "", f"|类别|结果|全量|原{baseline_n}|补样后{expanded_n}|原样本占比−全量占比(pp)|", "|---|---|---:|---:|---:|---:|"]
    for r in data["category_result_distribution"]:
        lines.append(f"|{r['error_type']}|{r['result']}|{r['full']}|{r['baseline']}|{r['expanded']}|{r['baseline_minus_full_percentage_points']:.2f}|")
    lines += ["", "类别×结果总变差距离（0表示分布相同，不是置信度）：" + ", ".join(f"{k}={v:.4f}" for k, v in data["category_result_total_variation"].items()), "",
              "## 文档集中度", "", "|范围|实例|文档|单篇最多实例|前5篇占比|HHI|", "|---|---:|---:|---:|---:|---:|"]
    for name, r in data["document_concentration"].items():
        lines.append(f"|{name}|{r['instances']}|{r['unique_documents']}|{r['max_instances_per_document']}|{r['top5_instance_share']:.2%}|{r['hhi']:.4f}|")
    lines += ["", "HHI和前5篇占比会随样本量变化，仅用于描述文档集中程度，不是统计代表性检验。"]
    for name, dist in data["distributions"].items():
        lines += ["", "## " + name + " 切片", "", "失败数/文档数分别计数；全量文档列含没有失败的文档。", "", f"|切片|全量文档|全量失败/失败文档|原{baseline_n}失败/文档|补样{expanded_n}失败/文档|", "|---|---:|---:|---:|---:|"]
        for r in dist:
            lines.append(f"|{r['bucket']}|{r['all_documents']}|{r['full_failures']}/{r['full_documents']}|{r['baseline_failures']}/{r['baseline_documents']}|{r['expanded_failures']}/{r['expanded_documents']}|")
    lines += ["", "## 可重叠机械标签", "", "每格为命中数/适用分母；FN专属标签分母是FN，invalid_anchor标签分母是FP，其余分母是全部失败实例。", "", f"|标签|全量|原{baseline_n}|补样{expanded_n}|", "|---|---:|---:|---:|"]
    for r in data["mechanical_labels"]:
        lines.append("|" + r["label"] + "|" + "|".join(f"{r[n]}/{r[n + '_eligible']}" for n in ("full", "baseline", "expanded")) + "|")
    lines += ["", *[f"- **{k}**：{v}" for k, v in DEFINITIONS.items()], "", "## 零分文档逐篇覆盖", "", "|文档|长度|场景|raw候选|原样本|补样后|", "|---|---:|---|---:|---|---|"]
    for r in data["zero_f1_documents"]:
        lines.append(f"|{r['document_id'].split(':')[-1]}|{r['length_chars']}|{r['scene']}|{r['raw_candidates']}|{','.join(r['baseline_case_ids']) or '未覆盖'}|{','.join(r['expanded_case_ids']) or '未覆盖'}|")
    lines += ["", "## 缺口及最小补样", "", "原样本未覆盖的机械标签或困难切片：", "", *["- " + x for x in data["coverage_gaps"]["baseline"]], ""]
    for r in data["supplement_recommendations"]:
        lines.append(f"- {r.get('case_id', '待追加')} / `{json.dumps(r['identity'], ensure_ascii=False)}`：{'已补' if r['applied'] else '建议补'}；覆盖 {', '.join(r['covers'])}。")
    lines += ["", "补样后未覆盖目标：" + ("；".join(data["coverage_gaps"]["expanded"]) or "无。已覆盖定义的机械现象、全部零分文档以及有失败的4000字以上各场景；这不证明所有业务根因齐备。"), "", "## 解释边界", "", *["- " + x for x in data["limitations"]], "", "新模型/API调用：0。人工结论不由本工具生成。", ""]
    return "\n".join(lines)


def worker(package, pack):
    package, pack = package.resolve(), pack.resolve()
    if pack.is_relative_to(package):
        raise ValueError("Audit output must be outside frozen package")
    cases, docs, supported = load_frozen(package)
    data = summarize(cases, docs, read(pack / "selection.json"), supported)
    data["provenance"] = {"package_manifest_sha256": sha(package / "manifest.json"),
                          "verification_manifest_sha256": sha(package / "verification/manifest.json"),
                          "selection_sha256": sha(pack / "selection.json"), "frozen_case_replay_exact": True}
    (pack / "typicality.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    (pack / "typicality.md").write_text(markdown(data), encoding="utf-8", newline="\n")
    print(json.dumps({"failures": data["population"]["failures"], "gaps": data["coverage_gaps"]}, ensure_ascii=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", required=True, type=Path)
    parser.add_argument("--pack", required=True, type=Path)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        worker(args.package, args.pack)
    else:
        proc = subprocess.run([sys.executable, "-X", "utf8", "-I", "-B", str(Path(__file__).resolve()), "--worker",
                               "--package", str(args.package.resolve()), "--pack", str(args.pack.resolve())],
                              capture_output=True, encoding="utf-8")
        if proc.returncode:
            raise ValueError(proc.stderr.strip() or "Typicality audit failed")
        print(proc.stdout.strip())


if __name__ == "__main__":
    main()
