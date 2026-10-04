# -*- coding: utf-8 -*-
"""冻结旧规则(d71f8c7 归档)在评测集(eval_oct05)前 50 篇研报上的完整指标。"""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "evals"), str(ROOT / "factcheck/src")]
from baseline import frozen_legacy_detect
from fined_bench_eval import score_paper_detection

INPUTS = ROOT / "data/v2/dataset/inputs.eval_oct05.jsonl"
GOLD = ROOT / "data/v2/dataset/gold.eval_oct05.jsonl"
ARCHIVE = ROOT / "data/v2/baseline/source-d71f8c7.zip"
RESEARCH = {"个股研报", "行业研报"}
N = 50


def main() -> int:
    all_rows = [json.loads(l) for l in INPUTS.read_text(encoding="utf-8").splitlines()]
    gold_by_id = {g["document_id"]: g for g in
                  (json.loads(l) for l in GOLD.read_text(encoding="utf-8").splitlines())}

    research_rows = [r for r in all_rows if r.get("scene") in RESEARCH][:N]
    predictions, golds = [], []
    for row in research_rows:
        did = row["doc_id"]
        report = frozen_legacy_detect(row["content"], document_id=did,
                                      scene=row.get("scene", ""), archive=ARCHIVE)
        predictions.append(report)
        if did in gold_by_id:
            golds.append(gold_by_id[did])

    r = score_paper_detection(predictions, golds)
    p, rec, f1 = r["precision"], r["recall"], r["f1"]
    ta = r["type_accuracy"]
    tp, fp, fn = r["true_positive"], r["false_positive"], r["false_negative"]
    scenes = Counter(row.get("scene") for row in research_rows)

    print(f"冻结旧规则(d71f8c7) 在评测集前 50 篇研报上的指标：")
    print(f"  场景构成        : {dict(scenes)}")
    print(f"  精确率 P        = {p*100:.2f}%")
    print(f"  召回率 R        = {rec*100:.2f}%")
    print(f"  F1              = {f1*100:.2f}%")
    print(f"  类型准确率       = {(ta*100 if ta is not None else 0):.2f}%")
    print(f"  TP              = {tp}")
    print(f"  FP              = {fp}")
    print(f"  FN              = {fn}")
    print(f"  误报率(1−P)      = {(1-p)*100:.2f}%")
    print(f"  定位命中数       = {r['localized_matches']}")
    print(f"  排除金标错误     = {r['excluded_gold_errors']}")
    print(f"  缺失预测文档     = {len(r['missing_prediction_documents'])}")

    # 检测出的错误类型分布（TP 的具体类型）
    from collections import Counter as C
    types = C()
    for report in predictions:
        for e in report.get("errors", []):
            types[e["error_type"]] += 1
    print(f"  报出错误类型分布 : {dict(types)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())