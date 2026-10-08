#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
对 v2 产物做独立复检（不依赖构建脚本的内部状态，只读磁盘上的数据）。

逐条验证 2026-10-08 评审提出的 4 个问题 + 2 个放大结果的问题是否真的修好了：
  1 标签方向统一且自洽
  2 FinVerBench 不再有"错误文本==正确文本"的纠错对
  3 FinanceBench 数值扰动不再误改年份/日期/财号/编号
  4 ConvFinQA 按划分分文件且 sample_id 全局唯一
  E1 重复子集只保留一份
  E2 sample_id 不再泄漏答案
  另加：inputs 不含 gold 字段、划分不跨组、类别不均衡基线

运行：python3 scripts/verify_datasets.py   （全部通过退出码 0，否则 1）
"""
import glob
import gzip
import json
import os
import re
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
SPLITS = os.path.join(ROOT, "splits")

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print("  [{}] {} {}".format("PASS" if ok else "FAIL", name, detail))
    return ok


def op(path):
    return gzip.open(path, "rt", encoding="utf-8") if path.endswith(".gz") else open(path, encoding="utf-8")


def resolve(path):
    return path + ".gz" if (not os.path.exists(path) and os.path.exists(path + ".gz")) else path


def load(path):
    with op(resolve(path)) as f:
        return [json.loads(l) for l in f if l.strip()]


MONTH_RE = re.compile(
    r"(January|February|March|April|May|June|July|August|September|October|November|December"
    r"|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec)")
LEAK_TOKENS = ("__clean", "_wrong", "_right", "_correct", "corrupted", "__AE_", "__CL_",
               "__YOY_", "__MR_", "inconsistent", "consistent")


def monetary_like(tok):
    return (tok.startswith("$") or tok.endswith("%") or "." in tok
            or bool(re.search(r"\d,\d{3}", tok)))


def task_gold(task):
    rows = []
    for sp in ("dev", "test"):
        rows += load(os.path.join(DATA, task, "gold.{}.jsonl".format(sp)))
    return rows


def task_inputs(task):
    rows = []
    for sp in ("dev", "test"):
        rows += load(os.path.join(DATA, task, "inputs.{}.jsonl".format(sp)))
    return rows


def main():
    print("== 1. 标签方向 ==")
    det_g = task_gold("finverbench_detection")
    bad = [r for r in det_g
           if int(r["label"]) != (1 if r["has_error"] else 0)
           or r["label_text"] != ("inconsistent" if r["has_error"] else "consistent")]
    check("FinVerBench 检测 gold: label/has_error/label_text 一致", not bad, "n={} 违例={}".format(len(det_g), len(bad)))

    cor_g = task_gold("finverbench_correction")
    check("FinVerBench 纠错 gold: has_error 恒为 True", all(r["has_error"] for r in cor_g), "n={}".format(len(cor_g)))

    cv_g = task_gold("financebench_claim_verification")
    bad = [r for r in cv_g
           if int(r["label"]) != (1 if r["has_error"] else 0)
           or r["label_text"] != ("inconsistent" if r["has_error"] else "consistent")]
    check("FinanceBench 断言核验: label 方向与 FinVerBench 一致", not bad,
          "n={} 违例={} 有错={}".format(len(cv_g), len(bad), sum(1 for r in cv_g if r["has_error"])))

    print("\n== 2. FinVerBench 无效纠错对 ==")
    cor_i = {r["sample_id"]: r for r in task_inputs("finverbench_correction")}
    same = [r for r in cor_g if r["corrected_text"] == cor_i[r["sample_id"]]["corrupted_text"]]
    check("不再存在 corrupted_text == corrected_text 的纠错对", not same,
          "n_pairs={} 相同={}".format(len(cor_g), len(same)))
    q = load(os.path.join(ROOT, "audit", "quarantine_candidates.jsonl"))
    nq = sum(1 for r in q if r["reason"] == "finverbench_correction_identical_text")
    check("被隔离的相同对已留痕", nq == 120, "quarantine 记录={}".format(nq))
    amb = sum(1 for r in det_g if r["ambiguous_visible_text"])
    check("对应检测样本已打模糊标记", amb == 162, "ambiguous={} (42 clean + 120 注错)".format(amb))

    print("\n== 3. FinanceBench 数值扰动 ==")
    fb_g = task_gold("financebench_correction")
    bad = []
    for r in fb_g:
        frm = r["edit"]["from"]
        if not monetary_like(frm):
            bad.append((r["sample_id"], frm, "not_monetary"))
        elif re.fullmatch(r"(19|20)\d{2}", frm.strip("$%").rstrip(",")):
            bad.append((r["sample_id"], frm, "year"))
    check("所有扰动对象都是金额/比例（无裸整数）", not bad, "n_pairs={} 违例={}".format(len(fb_g), len(bad)))
    years = [r for r in fb_g if re.search(r"(19|20)\d{2},", json.dumps(r["edit"], ensure_ascii=False))]
    check("不再出现 2022, -> 2,184 这类年份误改", not years, "违例={}".format(len(years)))
    nbad = sum(1 for r in q if r["reason"] == "financebench_nonmonetary_edit_v1")
    check("v1 误改项已留痕", nbad == 9, "记录={} 条（3 年份 + 1 机型 + 1 财号 + 4 日期）".format(nbad))
    dirs = {r["perturbation_direction"] for r in fb_g}
    check("扰动方向已在数据里写明", dirs == {"upward_only"}, "方向={}".format(sorted(dirs)))

    print("\n== 4. ConvFinQA 划分与 ID ==")
    files = sorted(glob.glob(os.path.join(DATA, "finben", "flare-convfinqa.*.jsonl*")))
    check("ConvFinQA 按划分分文件", len(files) == 3, "files={}".format([os.path.basename(f) for f in files]))
    allrows = []
    for f in files:
        allrows += load(f)
    sids = [r["sample_id"] for r in allrows]
    check("sample_id 全局唯一", len(set(sids)) == len(sids), "n={} unique={}".format(len(sids), len(set(sids))))
    check("sample_id 含官方划分", all(r["sample_id"].split("/")[2] == r["split"] for r in allrows),
          "split 分布={}".format(dict(Counter(r["split"] for r in allrows))))

    print("\n== E1. 重复子集 ==")
    fomc = glob.glob(os.path.join(DATA, "finben", "*fomc*.jsonl*"))
    check("FOMC 只保留一份计分副本", len(fomc) == 1, "files={}".format([os.path.basename(f) for f in fomc]))
    ndup = sum(1 for r in q if r["reason"] == "finben_duplicate_subset")
    check("被丢弃的重复子集已留痕", ndup == 496, "qurantine={}".format(ndup))

    print("\n== E2. ID 泄漏 ==")
    leak = []
    for f in glob.glob(os.path.join(DATA, "**", "*.jsonl*"), recursive=True):
        if "/index.jsonl" in f:
            continue
        for r in load(f):
            sid = r.get("sample_id", "")
            if any(t in sid for t in LEAK_TOKENS):
                leak.append((f, sid))
                break
    check("data/ 下 sample_id 不含答案泄漏标记", not leak, "违例文件={}".format(leak[:3]))

    print("\n== 附加. inputs 不含 gold 字段 ==")
    forbidden = {"has_error", "label", "label_text", "corrected_text", "correct_statement",
                 "gold_output", "error_type", "error_location", "modified_value",
                 "original_value", "error_description", "gold_answer", "answer"}
    viol = []
    for f in sorted(glob.glob(os.path.join(DATA, "*", "inputs.*.jsonl*"))):
        keys = set()
        for r in load(f):
            keys |= set(r.keys())
            break
        inter = keys & forbidden
        if inter:
            viol.append((os.path.basename(f), sorted(inter)))
    check("data/<task>/inputs.*.jsonl 不含标签/答案字段", not viol, "违例={}".format(viol))

    print("\n== 附加. 划分不跨组 ==")
    dev = load(os.path.join(SPLITS, "dev.jsonl"))
    test = load(os.path.join(SPLITS, "test.jsonl"))
    gd = {r["group_id"] for r in dev}
    gt = {r["group_id"] for r in test}
    check("dev / test 的文档组零重叠", not (gd & gt), "dev组={} test组={} 交={}".format(len(gd), len(gt), len(gd & gt)))
    check("split 索引样本数自洽", len(dev) + len(test) == len(load(os.path.join(DATA, "index.jsonl"))),
          "dev={} test={}".format(len(dev), len(test)))
    bad = [r for r in load(os.path.join(DATA, "index.jsonl"))
           if r["has_error"] is not None and r["label_text"] != ("inconsistent" if r["has_error"] else "consistent")]
    check("index 里 label_text 与 has_error 一致", not bad, "违例={}".format(len(bad)))

    print("\n== 附加. 类别不均衡（必须报告召回/精确率/FPR）==")
    n_err = sum(1 for r in det_g if r["has_error"])
    n = len(det_g)
    print("  FinVerBench 检测: 有错={} 干净={} → 全部答'有错'的准确率={:.2f}%（虚高基线）".format(
        n_err, n - n_err, 100.0 * n_err / n))
    print("  → 评测必须同时报告 error_recall / error_precision / false_positive_rate / 各错误类型召回")

    fails = [r for r in RESULTS if not r[1]]
    print("\n== 结论 ==")
    print("  共 {} 项检查，通过 {}，失败 {}".format(len(RESULTS), len(RESULTS) - len(fails), len(fails)))
    for name, ok, detail in fails:
        print("  FAIL:", name, detail)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
