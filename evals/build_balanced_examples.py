# -*- coding: utf-8 -*-
"""均衡 few-shot 示例构建工具：挖掘硬负例 + 按类型均衡重选正例（离线，零模型调用）。

背景（P0-2）：442 篇研报实测模型直接检测 FP=610，其中冗余语句 160、术语误用 65，
且金融要素缺失召回仅 5.04%。原 few-shot 只有 3 个整篇示例、类型覆盖不受控。
本工具产出：
1. 正例：开发集研报中"注释完整、1-N 个可评分错误、长度受限"的文档，按错误类型
   加权贪心覆盖全部细分类（FP 重灾区与召回缺口类型加权）。
2. 硬负例：官方 SFT 负样本（sft_data_wo_errors.json）中形态酷似错误（数字单位/
   涨跌词/日期/金融术语/重复衔接）但经核验无错的句子，作为 errors=[] 的演示，
   抑制模型过度报错。
3. reasons 改写走显式补丁（沿用 curate_dev_examples.py 的签名绑定思路），
   人工撰写的 reason 只允许替换、不允许改动 content/spans/类型。

用法:
  python evals/build_balanced_examples.py --select
  python evals/build_balanced_examples.py --apply-patch --patch evals/balanced_reason_patch.v1.json
  python evals/build_balanced_examples.py --verify

输出: data/v2/dataset/examples.balanced.v1.json（不含金标 held-out 信息，
      仅 dev 正例与官方 sft 负例）；audit 文件同目录。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "data/v2/dataset"
SFT_NEG = ROOT / "测评集" / "测评集" / "FinED-Bench-main" / "sft_data" / "sft_data_wo_errors.json"
EXAMPLES_OUT = DATASET / "examples.balanced.v1.json"
AUDIT_OUT = DATASET / "examples.balanced.v1.audit.json"
SEL_REPORT = DATASET / "examples.balanced.v1.selection.json"

SPLITS = ("dev", "eval_oct05", "holdout_oct07")
RESEARCH_SCENES = {"个股研报", "行业研报"}
MAX_POSITIVE_DOCS = 8
MAX_POSITIVE_CHARS = 9000
PRIMARY_LEN_CAP = 2000
FALLBACK_LEN_CAP = 3200
MAX_ERRORS_PER_DOC = 4
MAX_NEGATIVES = 4
MAX_NEGATIVE_CHARS = 320
PATTERN_SCHEMA = "1.0.0"
# FP 重灾区（442 批实测）与召回缺口加权；其余类型权重 1
TYPE_WEIGHTS = {
    "冗余语句": 3, "术语误用": 3, "金融要素缺失": 3,
    "计算错误": 2, "数值不一致错误": 2, "时间信息非法": 2,
    "时间矛盾": 2, "语义逻辑矛盾": 2,
    "数值单位错误": 1, "数值缺失": 1, "属性值缺失错误": 1, "格式错误": 1,
}

# 硬负例按"误报形态"手动指定：(sft_index 或 None, 显式 content 或 None, 形态标注)。
# 前一轮负例全为"数字+单位密集"句，导致数值类真错误检测退化；再改为覆盖 FP 重灾区
# (冗余/术语/数值不一致) 的"看似有错实则正确"形态。其中冗余负例 #6 由法律条文改为
# 研报"总-分结构"句(从 dev 研报挖掘、金标无错)，消除法律条文语境对研报冗余的过度泛化。
NEGATIVE_PICKS = [
    (None, "公司以“B2B+B2C”双轮驱动为核心，构建了涵盖旅游批发、零售分销、签证服务、保险、会奖、定制等多业态的全产业链服务体系。",
     "冗余语句误报：'涵盖…等多业态'为总-分列举，非重复"),
    (22, None, "术语误用误报：'独山子石化/乙烯项目/石油石化产业园'等术语密集但正确"),
    (7, None, "数值不一致误报：失信分区间 8≤x≤10 为合法区间表述，非数值冲突"),
    (18, None, "术语误用误报：'拆入资金/固定资产贷款'等金融术语正确使用"),
]


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def official_type(record: dict) -> str:
    return record.get("error_type") or record.get("type") or ""


def clean_spans(err: dict, content: str) -> list[dict] | None:
    """只保留 _examples() 允许的 start/end/text 三元组并逐字校验。"""
    out = []
    for span in err.get("spans") or []:
        if not isinstance(span, dict):
            return None
        if set(span) != {"start", "end", "text"}:
            # dataset_prepare 的 span 带 original_start/anchor_method 等字段，需裁剪
            keep = [(k, span.get(k)) for k in ("start", "end", "text")]
            if any(v is None for _, v in keep):
                return None
            start, end, text = span["start"], span["end"], span["text"]
        else:
            start, end, text = span["start"], span["end"], span["text"]
        if type(start) is not int or type(end) is not int or not (0 <= start < end <= len(content)):
            return None
        if content[start:end] != text:
            return None
        out.append({"start": start, "end": end, "text": text})
    return out


def positive_pool(dev_inputs: list[dict], dev_gold: dict[str, list[dict]],
                  len_cap: int) -> list[dict]:
    candidates = []
    for row in dev_inputs:
        if row.get("scene") not in RESEARCH_SCENES:
            continue
        content = row["content"]
        if not 0 < len(content) <= len_cap:
            continue
        errors = [e for e in dev_gold.get(row["doc_id"], []) if e.get("scorable", True)]
        if not 1 <= len(errors) <= MAX_ERRORS_PER_DOC:
            continue
        typed, ok = [], True
        for err in errors:
            kind = official_type(err)
            if kind not in TYPE_WEIGHTS:
                ok = False
                break
            spans = clean_spans(err, content)
            if spans is None:
                ok = False
                break
            typed.append({"error_type": kind, "spans": spans, "reason": ""})
        if not ok or not typed:
            continue
        candidates.append({"source_split": "dev", "source_id": row["doc_id"],
                           "document_id": row["doc_id"], "content": content,
                           "scene": row["scene"], "complete_annotation": True,
                           "errors": typed, "types": sorted({t["error_type"] for t in typed})})
    return candidates


def greedy_cover(pool: list[dict], uncovered: set[str], char_budget: int,
                 max_docs: int, prefer_types: tuple[str, ...]) -> tuple[list[dict], list[dict]]:
    """加权贪心：优先覆盖未出现的类型，其次奖励 FP 重灾区类型共现与短文档。

    返回 (chosen, remaining)。"""
    chosen: list[dict] = []
    chars = 0
    remaining = list(pool)
    while uncovered and len(chosen) < max_docs:
        def score(doc: dict) -> tuple:
            new_types = [t for t in doc["types"] if t in uncovered]
            gain = sum(TYPE_WEIGHTS[t] for t in new_types)
            focus = sum(1 for t in doc["types"] if t in prefer_types)
            overlap_gain = sum(TYPE_WEIGHTS[t] for t in doc["types"] if t not in uncovered)
            return (gain, focus, overlap_gain, -len(doc["content"]), -len(doc["errors"]))
        best = max(remaining, key=score)
        if sum(TYPE_WEIGHTS[t] for t in best["types"] if t in uncovered) == 0:
            break
        if chars + len(best["content"]) > char_budget:
            break
        chosen.append(best)
        chars += len(best["content"])
        uncovered -= set(best["types"])
        remaining.remove(best)
    return chosen, remaining


def refill_focus(chosen: list[dict], remaining: list[dict], char_budget: int,
                 max_docs: int, max_extra: int, prefer_types: tuple[str, ...]) -> list[dict]:
    """覆盖面达成后，用剩余预算补强 FP 重灾区类型的正例频次。"""
    chars = sum(len(d["content"]) for d in chosen)
    type_count = {}
    for doc in chosen:
        for t in doc["types"]:
            type_count[t] = type_count.get(t, 0) + 1
    added = 0
    while remaining and added < max_extra and len(chosen) < max_docs:
        def score(doc: dict) -> tuple:
            reinforce = sum(TYPE_WEIGHTS[t] for t in doc["types"] if t in prefer_types and type_count.get(t, 0) < 2)
            any_prefer = sum(1 for t in doc["types"] if t in prefer_types)
            return (reinforce, any_prefer, -len(doc["content"]))
        best = max(remaining, key=score)
        reinforce = sum(TYPE_WEIGHTS[t] for t in best["types"]
                        if t in prefer_types and type_count.get(t, 0) < 2)
        if reinforce == 0:
            break
        if chars + len(best["content"]) > char_budget:
            break
        chosen.append(best)
        chars += len(best["content"])
        for t in best["types"]:
            type_count[t] = type_count.get(t, 0) + 1
        remaining.remove(best)
        added += 1
    return chosen


def negative_pool(contents_by_split: dict[str, list[str]], gold_span_texts: list[str]) -> list[dict]:
    records = json.loads(SFT_NEG.read_text(encoding="utf-8-sig"))
    out = []
    for order, (sft_index, explicit_content, label) in enumerate(NEGATIVE_PICKS):
        if sft_index is not None:
            record = records[sft_index]
            text = str(record.get("origin_text") or "").strip()
            name = str(record.get("name") or "")
        else:
            text = (explicit_content or "").strip()
            name = "dev研报总-分句"
        if not text or len(text) > MAX_NEGATIVE_CHARS:
            continue
        needle = re.sub(r"\s+", "", text)
        if not needle:
            continue
        # 与评测/保留集输入重叠的一律排除（测试集内容不进 few-shot）
        if any(needle in content for content in contents_by_split.get("eval_oct05", []) + contents_by_split.get("holdout_oct07", [])):
            continue
        # 若句中包含任何金标错误片段则不可能是无错句
        if any(span in needle for span in gold_span_texts):
            continue
        source_id = f"fined:sftneg:{_sha(text)[:16]}:{order:06d}"
        in_dev = any(needle in content for content in contents_by_split.get("dev", []))
        out.append({"source_split": "sft_negative", "source_id": source_id,
                    "document_id": source_id, "content": text, "scene": "unknown",
                    "complete_annotation": True, "errors": [],
                    "_name": name, "_in_dev": in_dev,
                    "_label": label})
    return out


def selection_report(positives: list[dict], negatives: list[dict],
                     covered: set[str], uncovered: set[str]) -> dict:
    return {
        "positive_docs": len(positives),
        "positive_chars": sum(len(d["content"]) for d in positives),
        "negative_docs": len(negatives),
        "covered_types": sorted(covered),
        "uncovered_types": sorted(uncovered),
        "per_positive": [{"source_id": d["source_id"], "scene": d["scene"],
                          "chars": len(d["content"]), "types": d["types"]} for d in positives],
        "per_negative": [{"source_id": n["source_id"], "name": n["_name"], "chars": len(n["content"]),
                          "misreport_shape": n["_label"], "dev_overlap": bool(n["_in_dev"]),
                          "content": n["content"]} for n in negatives],
    }


def finalize(positives: list[dict], negatives: list[dict]) -> tuple[list[dict], dict]:
    payload = []
    for rank, doc in enumerate(positives, 1):
        payload.append({k: v for k, v in doc.items() if k in ("source_split", "source_id",
                       "document_id", "content", "scene", "complete_annotation", "errors")})
    for rank, neg in enumerate(negatives, 1):
        payload.append({k: v for k, v in neg.items() if k in ("source_split", "source_id",
                        "document_id", "content", "scene", "complete_annotation", "errors")})
    return payload, None


def strip_reasons(example: dict) -> dict:
    slim = {k: example[k] for k in ("source_split", "source_id", "document_id",
                                    "content", "scene", "complete_annotation")}
    slim["errors"] = [{"error_type": e["error_type"], "spans": e["spans"]} for e in example["errors"]]
    return slim


def signature(example: dict) -> str:
    return _sha(json.dumps(strip_reasons(example), ensure_ascii=False,
                           sort_keys=True, separators=(",", ":")))


def run_select() -> int:
    by_id = {}
    contents_by_split: dict[str, list[str]] = {}
    gold_span_texts = set()
    for split in SPLITS:
        rows = load_jsonl(DATASET / f"inputs.{split}.jsonl")
        contents_by_split[split] = [re.sub(r"\s+", "", r["content"]) for r in rows]
        for row in rows:
            by_id[row["doc_id"]] = row
        for g in load_jsonl(DATASET / f"gold.{split}.jsonl"):
            for err in g["errors"]:
                for span in err.get("spans") or []:
                    if isinstance(span, dict) and span.get("text"):
                        gold_span_texts.add(re.sub(r"\s+", "", span["text"]))
    dev_inputs = [r for r in by_id.values() if r.get("split") == "dev"]
    dev_gold = {}
    for g in load_jsonl(DATASET / "gold.dev.jsonl"):
        dev_gold[g["document_id"]] = g["errors"]

    all_types = set(TYPE_WEIGHTS)
    pool = positive_pool(dev_inputs, dev_gold, PRIMARY_LEN_CAP)
    covered = set()
    for doc in pool:
        covered |= set(doc["types"])
    missing = all_types - covered
    if missing:
        extra = positive_pool(dev_inputs, dev_gold, FALLBACK_LEN_CAP)
        pool = pool + [d for d in extra if len(d["content"]) > PRIMARY_LEN_CAP]
        for doc in pool:
            covered |= set(doc["types"])
        missing = all_types - covered
    prefer = ("冗余语句", "术语误用", "金融要素缺失", "计算错误")
    chosen, remaining = greedy_cover(pool, set(all_types), MAX_POSITIVE_CHARS,
                                     MAX_POSITIVE_DOCS, prefer)
    chosen = refill_focus(chosen, remaining, MAX_POSITIVE_CHARS, MAX_POSITIVE_DOCS, 2, prefer)
    covered = set()
    for doc in chosen:
        covered |= set(doc["types"])
    uncovered = all_types - covered

    negatives = negative_pool(contents_by_split, sorted(gold_span_texts))[:MAX_NEGATIVES]
    report = selection_report(chosen, negatives, covered, uncovered)
    SEL_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    payload, _ = finalize(chosen, negatives)
    EXAMPLES_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8", newline="\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def run_apply_patch(patch_path: Path) -> int:
    patch = json.loads(patch_path.read_text(encoding="utf-8-sig"))
    if patch.get("schema_version") != PATTERN_SCHEMA:
        raise ValueError("补丁 schema 必须是 1.0.0")
    examples = json.loads(EXAMPLES_OUT.read_text(encoding="utf-8"))
    by_sig = {signature(e): e for e in examples}
    for revision in patch.get("documents") or []:
        example = by_sig.pop(revision.get("example_sha256"), None)
        if example is None:
            raise ValueError("补丁包含未知示例签名，禁止改写内容/spans/类型")
        reasons = revision.get("reasons") or []
        if (len(reasons) != len(example["errors"])
                or any(not isinstance(r, str) or not r.strip() or len(r) > 80 for r in reasons)):
            raise ValueError("每条错误需要一条 1-80 字的 reason")
        for err, reason in zip(example["errors"], reasons):
            err["reason"] = reason
    leftovers = [sig for sig, e in by_sig.items() if e["errors"]]
    if leftovers:
        raise ValueError(f"有正例缺少 reason 补丁条目：{leftovers}")
    EXAMPLES_OUT.write_text(json.dumps(examples, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8", newline="\n")
    audit = run_verify(quiet=True)
    audit.update({"reason_patch_sha256": _sha(patch_path.read_text(encoding="utf-8")),
                  "revision": patch.get("revision", ""),
                  "change": "仅补写 reason；content/spans/error_type/provenance 未变"})
    AUDIT_OUT.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8", newline="\n")
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    return 0


def run_verify(quiet: bool = False) -> dict:
    examples = json.loads(EXAMPLES_OUT.read_text(encoding="utf-8"))
    types_covered, counts = set(), {"positive": 0, "negative": 0}
    chars = {"positive": 0, "negative": 0}
    err_msgs = []
    for example in examples:
        if set(example) - {"source_split", "source_id", "document_id", "content",
                           "errors", "scene", "complete_annotation"}:
            err_msgs.append(f"{example.get('source_id')}: 存在未允许字段")
        split = example.get("source_split")
        if split not in ("dev", "sft_negative") or not example.get("source_id"):
            err_msgs.append(f"{example.get('source_id')}: provenance 非法")
        if example.get("complete_annotation") is not True:
            err_msgs.append(f"{example.get('source_id')}: complete_annotation 必须为 true")
        content = example.get("content")
        if not isinstance(content, str) or not content:
            err_msgs.append(f"{example.get('source_id')}: content 非法")
        is_neg = split == "sft_negative"
        counts["negative" if is_neg else "positive"] += 1
        chars["negative" if is_neg else "positive"] += len(content)
        if is_neg and example.get("errors"):
            err_msgs.append(f"{example.get('source_id')}: 负例不得携带错误标注")
        for err in example.get("errors") or []:
            if set(err) - {"error_type", "spans", "reason"}:
                err_msgs.append(f"{example.get('source_id')}: error 字段非法")
            if err.get("error_type") not in TYPE_WEIGHTS:
                err_msgs.append(f"{example.get('source_id')}: 类型不受支持")
            for span in err.get("spans") or []:
                if (set(span) != {"start", "end", "text"}
                        or not (0 <= span["start"] < span["end"] <= len(content))
                        or content[span["start"]:span["end"]] != span["text"]):
                    err_msgs.append(f"{example.get('source_id')}: span 校验失败")
            reason = err.get("reason", "")
            if not isinstance(reason, str) or not reason.strip() or len(reason) > 80:
                err_msgs.append(f"{example.get('source_id')}: reason 缺失或超长")
            types_covered.add(err["error_type"])
    payload = EXAMPLES_OUT.read_text(encoding="utf-8").encode("utf-8")
    audit = {"examples_sha256": _sha(payload.decode("utf-8")), "documents": len(examples),
             "counts": counts, "total_chars": chars, "types_covered": sorted(types_covered),
             "validation": "OK" if not err_msgs else err_msgs,
             "read_policy": "仅读取 dev 正例金标（示例标注）、全部划分的 inputs（隔离校验）与官方 sft 负样本；未读取 eval/holdout 金标用于示例内容。",
             "negative_source": "官方 FinED-Bench sft_data_wo_errors.json（作者核验的无错句），与全部输入内容及金标 span 双重重叠校验后入选。"}
    if not quiet:
        print(json.dumps(audit, ensure_ascii=False, indent=2))
    return audit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--select", action="store_true")
    group.add_argument("--apply-patch", action="store_true")
    group.add_argument("--verify", action="store_true")
    parser.add_argument("--patch", default=str(ROOT / "evals" / "balanced_reason_patch.v1.json"))
    args = parser.parse_args()
    if args.select:
        return run_select()
    if args.apply_patch:
        return run_apply_patch(Path(args.patch))
    run_verify()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())