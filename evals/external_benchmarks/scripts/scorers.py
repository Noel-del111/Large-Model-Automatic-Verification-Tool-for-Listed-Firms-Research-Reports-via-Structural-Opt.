#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
各任务的输入装配与评分器。

设计原则（对应评审第 4、5 条）：
  * load_task() 返回的每条记录都把 {input, gold} 分开；模型只能看到 record["input"]。
  * 每个任务用各自的输入/答案结构和指标，不合成"整体纠错准确率"。
  * 检测类任务必须同时报告 recall / precision / FPR / 各错误类型召回，
    因为 FinVerBench 有 97.8% 的"有错"占比，只报准确率会虚高。

用法：
    from scorers import load_task, evaluate, baseline
    recs = load_task("finverbench_detection", split="test")
    preds = [True] * len(recs)
    print(evaluate("finverbench_detection", recs, preds))
"""
import glob
import gzip
import json
import os
import re
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
SPLITS = os.path.join(ROOT, "splits")

# task -> (inputs 文件, gold 文件, 指标族)
# task -> 指标族（输入/答案文件为 data/<task>/inputs.<split>.jsonl 与 gold.<split>.jsonl）
CONSISTENCY_TASKS = {
    "finverbench_detection": "detection",
    "finverbench_correction": "correction",
    "financebench_qa": "qa",
    "financebench_claim_verification": "detection",
    "financebench_correction": "numcorr",
}
FINBEN_METRIC = {
    "named_entity_recognition": "token_f1",
    "relation_extraction": "token_f1",
    "xbrl_numeric_tagging": "token_f1",
    "central_bank_stance_classification": "stance",
    "table_text_numeric_reasoning": "num_em",
    "conversational_numeric_reasoning": "num_em",
}


def _resolve(path):
    return path + ".gz" if (not os.path.exists(path) and os.path.exists(path + ".gz")) else path


def _open(path):
    path = _resolve(path)
    return gzip.open(path, "rt", encoding="utf-8") if path.endswith(".gz") else open(path, encoding="utf-8")


def load_jsonl(path):
    with _open(path) as f:
        return [json.loads(l) for l in f if l.strip()]


def load_index():
    return {r["sample_id"]: r for r in load_jsonl(os.path.join(DATA, "index.jsonl"))}


def load_task(task, split=None):
    """返回 [{sample_id, source, task, input:{...}, gold:{...}, split}]；split=None 返回全部。"""
    if task in CONSISTENCY_TASKS:
        metric = CONSISTENCY_TASKS[task]
        out = []
        for sp in ("dev", "test"):
            if split and sp != split:
                continue
            ins = {r["sample_id"]: r for r in
                   load_jsonl(os.path.join(DATA, task, "inputs.{}.jsonl".format(sp)))}
            golds = load_jsonl(os.path.join(DATA, task, "gold.{}.jsonl".format(sp)))
            for g in golds:
                i = ins[g["sample_id"]]
                prompt = {k: v for k, v in i.items() if k != "sample_id"}
                out.append({"sample_id": g["sample_id"], "source": i["source"], "task": task,
                            "metric": metric, "split": sp, "input": prompt, "gold": g})
        return out

    # FinBen 子集：task 形如 "finben:<subset>"
    files = sorted(glob.glob(os.path.join(DATA, "finben", "*.jsonl*")))
    idx = load_index()
    out = []
    for f in files:
        for r in load_jsonl(f):
            if task != "finben:" + r["subset"]:
                continue
            sp = idx[r["sample_id"]]["split"]
            if split and sp != split:
                continue
            prompt = {k: v for k, v in r.items()
                      if k in ("instruction", "input", "input_extra", "dialogue_id", "turn")}
            out.append({"sample_id": r["sample_id"], "source": "FinBen", "task": task,
                        "metric": FINBEN_METRIC[r["task"]], "split": sp,
                        "input": prompt,
                        "gold": {"gold_output": r.get("gold_output"), "subset": r["subset"],
                                 "orig_task": r["task"], "split_official": r["split"]}})
    return out


def list_finben_subsets():
    return sorted({r["subset"] for f in glob.glob(os.path.join(DATA, "finben", "*.jsonl*"))
                   for r in load_jsonl(f)})


# ---------------------------------------------------------------------------
# 归一化与基础指标
# ---------------------------------------------------------------------------
def norm_text(s):
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def to_number(s):
    m = re.search(r"-?\d+(?:,\d{3})*(?:\.\d+)?", str(s or "").replace("$", ""))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def prf(tp, fp, fn):
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f = 2 * p * r / (p + r) if (p + r) else 0.0
    return p, r, f


def token_f1(gold, pred):
    g, p = str(gold or "").split(), str(pred or "").split()
    if not g and not p:
        return 1.0, 1.0, 1.0
    gc, pc = Counter(g), Counter(p)
    tp = sum((gc & pc).values())
    fp = sum(pc.values()) - tp
    fn = sum(gc.values()) - tp
    return prf(tp, fp, fn)


# ---------------------------------------------------------------------------
# 各任务评分
# ---------------------------------------------------------------------------
def score_detection(recs, preds, exclude_ambiguous=True):
    """preds: 每个样本一个 bool（是否判定为有错）。"""
    used = [(r, bool(p)) for r, p in zip(recs, preds)
            if not (exclude_ambiguous and r["gold"].get("ambiguous_visible_text"))]
    tp = fp = fn = tn = 0
    per_type = defaultdict(lambda: [0, 0])       # type -> [命中, 该类型总数]
    for r, p in used:
        he = bool(r["gold"]["has_error"])
        # 错误类型：FinVerBench 用 error_type，FinanceBench 只有 error_category
        etype = r["gold"].get("error_type") or r["gold"].get("error_category") or "?"
        if he and p:
            tp += 1
            per_type[etype][0] += 1
        elif he and not p:
            fn += 1
        elif not he and p:
            fp += 1
        else:
            tn += 1
        if he:
            per_type[etype][1] += 1
    n = tp + fp + fn + tn
    acc = (tp + tn) / n if n else 0.0
    p, r_, f = prf(tp, fp, fn)
    # 虚高基线：一律预测多数类（取 gold 分布，与预测无关）
    n_err, n_clean = tp + fn, fp + tn
    majority = max(n_err, n_clean) / n if n else 0.0
    return {
        "n_scored": n,
        "n_excluded_ambiguous": len(recs) - n,
        "accuracy": round(acc, 4),
        "error_recall": round(r_, 4),
        "error_precision": round(p, 4),
        "error_f1": round(f, 4),
        "false_positive_rate": round(fp / (fp + tn), 4) if (fp + tn) else None,
        "majority_baseline_accuracy": round(majority, 4),
        "per_error_type_recall": {k: round(v[0] / v[1], 4) for k, v in sorted(per_type.items()) if v[1]},
        "counts": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
    }


def score_correction(recs, preds):
    """FinVerBench 纠错：preds 为模型输出的"修正后报表文本"。"""
    em = mv = ov = 0
    for r, p in zip(recs, preds):
        g = r["gold"]
        if norm_text(p) == norm_text(g["corrected_text"]):
            em += 1
        pn = norm_text(p)
        mv_s = str(int(g["modified_value"])) if isinstance(g["modified_value"], float) and g["modified_value"].is_integer() else str(g["modified_value"])
        ov_s = str(int(g["original_value"])) if isinstance(g["original_value"], float) and g["original_value"].is_integer() else str(g["original_value"])
        if mv_s not in pn.replace(",", "") and mv_s not in pn:
            mv += 1
        if ov_s and (ov_s in pn.replace(",", "") or ov_s in pn):
            ov += 1
    n = len(recs) or 1
    return {"n": len(recs), "exact_match": round(em / n, 4),
            "modified_value_removed_rate": round(mv / n, 4),
            "original_value_present_rate": round(ov / n, 4)}


def score_numcorr(recs, preds):
    """FinanceBench 数值纠错：preds 为模型给出的"正确断言"。"""
    em = num_ok = 0
    for r, p in zip(recs, preds):
        g = r["gold"]
        if norm_text(p) == norm_text(g["correct_statement"]):
            em += 1
        a, b = to_number(p), to_number(g["correct_statement"])
        if a is not None and b is not None and abs(a - b) < 1e-6:
            num_ok += 1
    n = len(recs) or 1
    return {"n": len(recs), "exact_match": round(em / n, 4), "numeric_match": round(num_ok / n, 4)}


def score_qa(recs, preds):
    em = num_ok = 0
    for r, p in zip(recs, preds):
        g = r["gold"]["answer"]
        if norm_text(p) == norm_text(g):
            em += 1
        a, b = to_number(p), to_number(g)
        if a is not None and b is not None and abs(a - b) < 1e-6:
            num_ok += 1
    n = len(recs) or 1
    return {"n": len(recs), "exact_match": round(em / n, 4), "numeric_match": round(num_ok / n, 4)}


def score_token_f1(recs, preds):
    ps, rs, fs = [], [], []
    for r, p in zip(recs, preds):
        a, b, c = token_f1(r["gold"]["gold_output"], p)
        ps.append(a); rs.append(b); fs.append(c)
    n = len(recs) or 1
    return {"n": len(recs), "token_precision": round(sum(ps) / n, 4),
            "token_recall": round(sum(rs) / n, 4), "token_f1": round(sum(fs) / n, 4)}


def score_stance(recs, preds):
    ok = sum(1 for r, p in zip(recs, preds)
             if norm_text(p) == norm_text(r["gold"]["gold_output"]))
    # 宏平均 F1
    labels = sorted({norm_text(r["gold"]["gold_output"]) for r in recs})
    f1s = []
    for lb in labels:
        tp = sum(1 for r, p in zip(recs, preds)
                 if norm_text(p) == lb and norm_text(r["gold"]["gold_output"]) == lb)
        fp = sum(1 for r, p in zip(recs, preds)
                 if norm_text(p) == lb and norm_text(r["gold"]["gold_output"]) != lb)
        fn = sum(1 for r, p in zip(recs, preds)
                 if norm_text(p) != lb and norm_text(r["gold"]["gold_output"]) == lb)
        f1s.append(prf(tp, fp, fn)[2])
    n = len(recs) or 1
    return {"n": len(recs), "accuracy": round(ok / n, 4),
            "macro_f1": round(sum(f1s) / len(f1s), 4) if f1s else None,
            "label_distribution": dict(Counter(norm_text(r["gold"]["gold_output"]) for r in recs))}


def score_num_em(recs, preds):
    ok = 0
    for r, p in zip(recs, preds):
        a, b = to_number(p), to_number(r["gold"]["gold_output"])
        if a is not None and b is not None and abs(a - b) < 1e-6:
            ok += 1
    n = len(recs) or 1
    return {"n": len(recs), "numeric_em": round(ok / n, 4)}


DISPATCH = {
    "detection": score_detection,
    "correction": score_correction,
    "numcorr": score_numcorr,
    "qa": score_qa,
    "token_f1": score_token_f1,
    "stance": score_stance,
    "num_em": score_num_em,
}


def evaluate(task, recs, preds):
    metric = recs[0]["metric"] if recs else None
    if metric is None:
        raise ValueError("空记录")
    return {"task": task, "metric": metric, "result": DISPATCH[metric](recs, preds)}


def to_prompt(rec):
    """交给模型的唯一入口：只返回可见输入。"""
    return rec["input"]


if __name__ == "__main__":
    print("可用一致性任务:", sorted(CONSISTENCY_TASKS))
    print("FinBen 子集:", list_finben_subsets())
