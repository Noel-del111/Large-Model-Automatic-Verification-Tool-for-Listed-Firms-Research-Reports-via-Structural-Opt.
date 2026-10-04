"""Offline FP attribution for the frozen 442-report batch; no model calls.

本脚本未读取原始 442 批次的逐篇模型预测产物，而是用两种离线
来源做结构归因：
1. dataset_prepare.py 按固定种子重建的三期金标（本机 FinED-Bench 原始数据，读取不修改）；
2. 《研报442篇测评指标汇总》PDF 的分类型 P/R/F1（全部提示口径，模型直接检测）。

方法：G_t 为第 t 类可评分金标条数（442 篇研报全口径），由 PDF 的 R_t 反推
TP_t ≈ R_t*G_t，再由 P_t 反推 FP_t = TP_t*(1-P_t)/P_t。PDF 只给两位小数，
反推存在舍入误差，不保证得到唯一整数计数；用整体 TP=1181/FP=610/FN=566 交叉校验。

usage: python evals/fp_attribution_offline.py
输入: data/v2/dataset/{inputs,gold}.{dev,eval_oct05,holdout_oct07}.jsonl
输出: data/v2/fp-attribution/research442_fp_breakdown.json
"""
from __future__ import annotations

import json
import hashlib
import math
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "data/v2/dataset"
OUT = ROOT / "data/v2/fp-attribution"
SPLITS = ("dev", "eval_oct05", "holdout_oct07")
RESEARCH_SCENES = {"个股研报", "行业研报"}

# PDF 第六节：模型直接检测，全部提示口径（P/R 为百分数）
MODEL_DIRECT_PR = {
    "数值单位错误": (88.51, 94.67),
    "数值缺失": (79.19, 90.21),
    "属性值缺失错误": (81.40, 83.33),
    "时间信息非法": (66.38, 91.57),
    "时间矛盾": (74.70, 45.26),
    "语义逻辑矛盾": (67.74, 73.43),
    "数值不一致错误": (56.35, 72.45),
    "计算错误": (53.17, 72.83),
    "格式错误": (71.74, 51.56),
    "冗余语句": (38.70, 56.11),
    "术语误用": (52.55, 43.90),
    "金融要素缺失": (77.78, 5.04),
    "法规引用错误": (0.0, 0.0),
    "模糊语言": (0.0, 0.0),
    "不一致条款": None,  # 原报告未提供，必须保留缺失状态，不能补造 P/R
}


def estimate_type(kind: str, gold: int, total_gold: int, pr) -> dict:
    """保留全部类型；零召回/零精确率不能用于反推出 FP 数量。"""
    if type(gold) is not int or type(total_gold) is not int or not 0 <= gold <= total_gold:
        raise ValueError("金标数量必须为非负整数，且分类数量不能超过总数")
    if pr is not None and (len(pr) != 2 or any(
        not isinstance(v, (int, float)) or isinstance(v, bool)
        or not math.isfinite(v) or not 0 <= v <= 100 for v in pr
    )):
        raise ValueError("P/R 必须为 0 到 100 的有限百分数")
    row = {"error_type": kind, "gold": gold,
           "gold_share_pct": round(gold / total_gold * 100, 2) if total_gold else None,
           "tp": None, "fp": None, "fn": None,
           "pdf_p": pr[0] if pr is not None else None,
           "pdf_r": pr[1] if pr is not None else None,
           "fp_density": None, "fp_share_of_610": None}
    if pr is None:
        row.update(status="missing_source_metrics", reason="PDF 未提供该类指标，需原批次逐篇预测或分类型计数。")
        if gold == 0:
            row.update(tp=0, fn=0)
        return row
    p, r = pr
    tp = round(r / 100 * gold)
    row.update(tp=tp, fn=gold - tp)
    if gold == 0 or p <= 0:
        row.update(status="fp_not_identifiable", reason="零金标或零精确率不能确定预测数量；FP 待原始产物核实。")
        return row
    fp = round(tp * (1 - p / 100) / (p / 100))
    row.update(fp=fp, fp_density=round(fp / gold, 3),
               fp_share_of_610=round(fp / 610 * 100, 2),
               status="estimated_from_rounded_metrics", reason="由报告舍入后的 P/R 反推，并非逐条预测复算。")
    return row


def load_dataset(dataset: Path):
    """核验本地 manifest 一致性；不能据此证明与原始预测批次相同。"""
    manifest = json.loads((dataset / "manifest.json").read_text(encoding="utf-8"))
    hashes = {}
    doc_meta: dict[str, dict] = {}
    gold_by_doc: dict[str, list[dict]] = {}
    for split in SPLITS:
        split_ids = {}
        for kind, id_key, target in (("inputs", "doc_id", doc_meta),
                                     ("gold", "document_id", gold_by_doc)):
            key = f"{kind}.{split}"
            raw = (dataset / f"{key}.jsonl").read_bytes()
            hashes[key] = hashlib.sha256(raw).hexdigest()
            if hashes[key] != manifest["files"][key]["sha256"]:
                raise ValueError(f"文件指纹不匹配: {key}")
            ids = set()
            for line in raw.decode("utf-8").splitlines():
                row = json.loads(line)
                doc_id = row[id_key]
                if not isinstance(doc_id, str) or not doc_id or doc_id in target:
                    raise ValueError(f"文档 ID 无效或重复: {key}: {doc_id}")
                ids.add(doc_id)
                if kind == "inputs":
                    target[doc_id] = {"scene": row["scene"], "split": split}
                else:
                    if not isinstance(row["errors"], list):
                        raise ValueError(f"errors 必须为列表: {doc_id}")
                    for err in row["errors"]:
                        if err.get("type") not in MODEL_DIRECT_PR or type(err.get("scorable")) is not bool:
                            raise ValueError(f"错误类型未知或缺少布尔 scorable: {doc_id}")
                    target[doc_id] = row["errors"]
            split_ids[kind] = ids
        if split_ids["inputs"] != split_ids["gold"]:
            raise ValueError(f"输入与金标文档集合不一致: {split}")
    errors = [err for rows in gold_by_doc.values() for err in rows]
    actual = {"documents": len(doc_meta), "errors": len(errors),
              "scorable_errors": sum(err["scorable"] for err in errors),
              "excluded_errors": sum(not err["scorable"] for err in errors)}
    for key, value in actual.items():
        if manifest[key] != value:
            raise ValueError(f"manifest 计数不匹配: {key}")
    return doc_meta, gold_by_doc, hashes


def validate_scope(per_split, total_gold, excluded):
    if dict(per_split) != {"dev": 265, "eval_oct05": 89, "holdout_oct07": 88} or total_gold != 1747 or excluded != 45:
        raise ValueError("数据不符合冻结 442 篇研报范围，禁止套用该批次 PDF 指标")


def main() -> int:
    doc_meta, gold_by_doc, hashes = load_dataset(DATASET)

    research_docs = [d for d, m in doc_meta.items() if m["scene"] in RESEARCH_SCENES]
    per_split = Counter(doc_meta[d]["split"] for d in research_docs)
    print("研报篇数按子集:", dict(per_split), "合计", len(research_docs))

    gold_by_type: Counter[str] = Counter()
    excluded_by_type: Counter[str] = Counter()
    excluded_in_research = 0
    for doc in research_docs:
        for err in gold_by_doc[doc]:
            t = err.get("type", "")
            if err.get("scorable", True):
                gold_by_type[t] += 1
            else:
                excluded_in_research += 1
                excluded_by_type[t] += 1
    total_gold = sum(gold_by_type.values())
    validate_scope(per_split, total_gold, excluded_in_research)
    print("研报可评分金标合计:", total_gold, "(PDF 记 1,747)；研报内不可评分标注:", excluded_in_research)
    print("金标类型数:", len(gold_by_type), "→", dict(sorted(gold_by_type.items())))

    rows = []
    tp_sum = fp_sum = fn_sum = 0
    for t, pr in MODEL_DIRECT_PR.items():
        g = gold_by_type.get(t, 0)
        row = estimate_type(t, g, total_gold, pr)
        tp_sum += row["tp"] or 0
        fp_sum += row["fp"] or 0
        fn_sum += row["fn"] or 0
        rows.append(row)
    rows.sort(key=lambda row: (row["fp"] is None, -(row["fp"] or 0)))
    empty_types = [t for t, pr in MODEL_DIRECT_PR.items() if pr is not None and gold_by_type.get(t, 0) == 0]
    report = {
        "schema_version": "1.1",
        "provenance": {"dataset_sha256": hashes, "local_manifest_verified": True,
                       "original_prediction_batch_identity": "not_verified",
                       "note": "本地文件与本地 manifest 一致不代表原始预测批次身份已核实。",
                       "metrics_source": "研报442篇测评指标汇总，第六节，模型直接检测，全部提示口径"},
        "complete": False,
        "missing_evidence": ["原始 442 篇逐篇预测及批次清单", "不一致条款分类指标", "零金标/零精确率类型的预测计数"],
        "scope_note": ("442 篇研报(开发265+评测89+保留88)重建金标 × PDF 分类型 P/R 反解；"
                       "PDF 两位小数引入舍入误差；未读取原始预测产物，无法做 span 级共因归因"),
        "validation": {"research_docs": len(research_docs), "per_split": dict(per_split),
                       "scorable_gold": total_gold, "excluded_in_research": excluded_in_research,
                       "excluded_by_type": dict(sorted(excluded_by_type.items())),
                       "derived_tp": tp_sum, "derived_fp": fp_sum, "derived_fn": fn_sum,
                       "derived_counts_note": "仅汇总非 null 估计值；缺失项不等于零，不是完整逐条复算。",
                       "identified_type_counts": {key: sum(row[key] is not None for row in rows)
                                                  for key in ("tp", "fp", "fn")},
                       "pdf_tp": 1181, "pdf_fp": 610, "pdf_fn": 566,
                       "tp_deviation": tp_sum - 1181, "fp_deviation": fp_sum - 610,
                       "fn_deviation": fn_sum - 566,
                       "gold_empty_types": empty_types,
                       "unattributed_fp_residual": 610 - fp_sum,
                       "residual_note": "总 FP 减可反推 FP 的差额；可能涉及缺失类型、零金标类型及舍入，不能归给特定类型。",
                       "incomplete_types": [row["error_type"] for row in rows if row["fp"] is None]},
        "per_type": rows,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "research442_fp_breakdown.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
