#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
构建"金融文本一致性 / 纠错"外部评测数据集  —— v2（修正版）

v1 输出在 2026-10-08 被外部评审报出 4 个必须修复的数据缺陷，另发现 2 个会夸大
结果的问题。本版逐条修复：

  [F1] 标签方向相反：统一视图里 FinanceBench 用 0=有错，FinVerBench 用 1=有错。
       本版以 has_error(bool) 为权威字段，label 固定 1=有错 / 0=无错，
       并逐条断言 has_error / label / label_text 三者一致。
  [F2] FinVerBench 纠错对有 120 条 corrupted_text == corrected_text（上游把错误注入在
       模型不可见的结构化字段）。这批对隔离，并给对应检测样本打 ambiguous_visible_text。
  [F3] FinanceBench 数值扰动误改非金额数值（年份 2022、日期 January 28,2023、
       财年标签 FY22、机型编号 737）。改用严格匹配器并断言 0 条误改。
  [F4] ConvFinQA 混装 train/valid/test，且 id 每划分各自从 0 编号（12,594 行仅 8,891
       个唯一 id）。本版按划分分文件落盘，sample_id 全局唯一。
  [E1] 两份 FOMC 内容逐字相同（同一 parquet 哈希），只保留一份计分副本。
  [E2] sample_id 泄漏答案（__clean / _wrong / _right / 含 error_type）。改为中性 id，
       原始 id 只留在 gold/audit。

并按评审建议完成：
  * data/inputs/*（仅模型可见文本）与 data/gold/*（标签与答案）分文件；
  * 按原始文档分组后划分 dev/test，官方 test 保持评测专用；
  * audit/source_hashes.json 与 MANIFEST.json 冻结上游版本（sha256）。

运行：python3 scripts/build_datasets.py
依赖：pandas, pyarrow（仅 FinBen 的 parquet 解析需要）
"""
import glob
import gzip
import hashlib
import json
import os
import re
import shutil
from collections import Counter, OrderedDict, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "sources")
DATA = os.path.join(ROOT, "data")
AUDIT = os.path.join(ROOT, "audit")
SPLITS = os.path.join(ROOT, "splits")

LABEL_CONVENTION = "1=has_error(有错) / 0=no_error(无错)"
FACTOR = 0.08

# 上游版本锚点（2026-10-08 核对得到的 commit / revision）。
# 注意：文件 sha256（audit/source_hashes.json）才是真正的冻结依据；revision 便于溯源。
UPSTREAM_PINS = OrderedDict([
    ("FinVerBench", {
        "kind": "github", "repo": "SiluPanda/finverification-bench",
        "revision": "8aef2f48befdab5c57cc383a521711fe11c2df98", "revision_date": "2026-03-17",
        "url": "https://github.com/SiluPanda/finverification-bench"}),
    ("FinanceBench", {
        "kind": "github", "repo": "patronus-ai/financebench",
        "revision": "cc39aeb4afdf33909ee1412188bf89035950c2eb", "revision_date": "2024-12-03",
        "url": "https://github.com/patronus-ai/financebench"}),
    ("FinBen", {
        "kind": "huggingface", "org": "TheFinAI",
        "url": "https://huggingface.co/TheFinAI",
        "revisions": OrderedDict([
            ("finben-finer-ord", "1a235081039192371efe56c4bfd340edd25144ea"),
            ("flare-convfinqa", "a24fb040ac27d8045e4afcdbf3e126299cc731bb"),
            ("flare-fomc", "e1f823e0e71556d0a2c1206b71310e564cae8dd4"),
            ("finben-fomc", "e1f823e0e71556d0a2c1206b71310e564cae8dd4"),
            ("flare-finred", "af34b2c8c3cc4bae61eee23cfaf0f0783eb800b2"),
            ("flare-fnxl", "8bea408feb61295e0a31499e31d0291896d0d6ba"),
            ("flare-tatqa", "1cf60f0c2c2c7ef6153b1842a5aa79509da568ba"),
        ])}),
])


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(chunk), b""):
            h.update(b)
    return h.hexdigest()


def stable_hash(s, mod=5):
    return int(hashlib.sha1(str(s).encode("utf-8")).hexdigest(), 16) % mod


def jdump(path, rows, gzip_threshold_mb=1.5):
    payload = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows).encode("utf-8")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if len(payload) > gzip_threshold_mb * 1024 * 1024:
        path = path + ".gz"
        with gzip.open(path, "wb") as f:
            f.write(payload)
    else:
        with open(path, "wb") as f:
            f.write(payload)
    print("[write] {:<58s} {:>7d} rows  {:>8.1f} MB".format(
        os.path.relpath(path, ROOT), len(rows), os.path.getsize(path) / 1e6))
    return path


def jwrite_json(path, obj):
    """写标准 JSON（缩进 2），用于 manifest / summary 这类非逐行数据。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    print("[write] {:<58s} {:>7d} obj   {:>8.1f} MB".format(
        os.path.relpath(path, ROOT), 1, os.path.getsize(path) / 1e6))
    return path


def to_jsonable(v):
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, (list, tuple)):
        return [to_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {k: to_jsonable(x) for k, x in v.items()}
    try:
        return v.item()
    except Exception:
        pass
    try:
        return [to_jsonable(x) for x in list(v)]
    except Exception:
        return str(v)


QUARANTINE = []
STATS = {}


def quarantine(sample_id, source_file, reason, detail=None):
    QUARANTINE.append(OrderedDict([
        ("sample_id", sample_id),
        ("source_file", source_file),
        ("reason", reason),
        ("detail", detail),
    ]))


def check_label(rec):
    he = bool(rec["has_error"])
    assert int(rec["label"]) == (1 if he else 0), "label 与 has_error 不一致: %r" % (rec["sample_id"],)
    assert rec["label_text"] == ("inconsistent" if he else "consistent"), rec["sample_id"]
    return rec


# ---------------------------------------------------------------------------
# 1. FinVerBench
# ---------------------------------------------------------------------------
ERROR_CAT_CN = {
    "AE": "算术错误 (合计/小计不等于各项之和)",
    "CL": "跨表勾稽错误 (同一指标在不同报表间不一致)",
    "YOY": "同比连续性错误 (期初/期末与上期对不上)",
    "MR": "量级扰动 (数值被整体放大或缩小)",
    "multi": "多重错误 (同时注入多类错误)",
    "none": "无错误 (干净样本)",
}


def build_finverbench():
    path = os.path.join(SRC, "FinVerBench/data/benchmark/benchmark.json")
    if not os.path.exists(path):
        print("[skip] FinVerBench benchmark.json 不存在")
        return [], [], [], []
    instances = json.load(open(path, encoding="utf-8"))

    clean_by_company = {}
    for it in instances:
        if it.get("instance_id", "").endswith("__clean"):
            clean_by_company[it.get("company")] = it

    # [F2] 可见文本相同却标签不同的组
    text2labels = defaultdict(set)
    for it in instances:
        text2labels[it.get("formatted_statements")].add(
            bool((it.get("ground_truth") or {}).get("has_error")))
    ambiguous_texts = {t for t, ls in text2labels.items() if len(ls) > 1}

    det_in, det_gold, cor_in, cor_gold = [], [], [], []
    n_identical = 0
    for n, it in enumerate(sorted(instances, key=lambda x: x["instance_id"]), start=1):
        gt = it.get("ground_truth") or {}
        has_err = bool(gt.get("has_error"))
        group_id = "finverbench::{}|{}".format(it.get("company"), it.get("period"))
        ambiguous = it.get("formatted_statements") in ambiguous_texts
        sid = "fvb-det-%04d" % n

        det_in.append(OrderedDict([
            ("sample_id", sid),
            ("source", "FinVerBench"),
            ("task", "financial_statement_consistency_detection"),
            ("language", "en"),
            ("instruction", it.get("question")),
            ("context", it.get("formatted_statements")),
        ]))
        det_gold.append(check_label(OrderedDict([
            ("sample_id", sid),
            ("has_error", has_err),
            ("label", 1 if has_err else 0),
            ("label_text", "inconsistent" if has_err else "consistent"),
            ("error_category", gt.get("error_category") if has_err else "none"),
            ("error_category_cn", ERROR_CAT_CN.get(gt.get("error_category")) if has_err else ERROR_CAT_CN["none"]),
            ("error_type", gt.get("error_type") if has_err else None),
            ("error_location", gt.get("error_location") if has_err else None),
            ("error_magnitude_pct", gt.get("error_magnitude_pct") if has_err else None),
            ("original_value", gt.get("original_value") if has_err else None),
            ("modified_value", gt.get("modified_value") if has_err else None),
            ("error_description", gt.get("description") if has_err else None),
            ("difficulty", it.get("difficulty")),
            ("company", it.get("company")),
            ("period", it.get("period")),
            ("group_id", group_id),
            ("ambiguous_visible_text", ambiguous),
            ("src_instance_id", it["instance_id"]),
        ])))

        if not has_err:
            continue
        cl = clean_by_company.get(it.get("company"))
        if not (cl and cl.get("period") == it.get("period")):
            continue
        corrupted, corrected = it.get("formatted_statements"), cl.get("formatted_statements")
        if corrupted == corrected:
            n_identical += 1
            det_gold[-1]["ambiguous_visible_text"] = True
            quarantine(it["instance_id"], "FinVerBench/benchmark.json",
                       "finverbench_correction_identical_text",
                       {"error_type": gt.get("error_type"),
                        "note": "注入错误位于 formatted_statements 未呈现的结构化字段，上游已知局限"})
            continue
        csid = "fvb-corr-%04d" % (len(cor_in) + 1)
        cor_in.append(OrderedDict([
            ("sample_id", csid),
            ("source", "FinVerBench"),
            ("task", "financial_statement_correction"),
            ("language", "en"),
            ("instruction", "The financial statements below contain internal inconsistencies. "
                            "Identify the incorrect line item(s) and output the corrected statements."),
            ("corrupted_text", corrupted),
        ]))
        cor_gold.append(check_label(OrderedDict([
            ("sample_id", csid),
            ("has_error", True),
            ("label", 1),
            ("label_text", "inconsistent"),
            ("corrected_text", corrected),
            ("error_category", gt.get("error_category")),
            ("error_category_cn", ERROR_CAT_CN.get(gt.get("error_category"))),
            ("error_type", gt.get("error_type")),
            ("error_location", gt.get("error_location")),
            ("error_magnitude_pct", gt.get("error_magnitude_pct")),
            ("original_value", gt.get("original_value")),
            ("modified_value", gt.get("modified_value")),
            ("error_description", gt.get("description")),
            ("difficulty", it.get("difficulty")),
            ("company", it.get("company")),
            ("period", it.get("period")),
            ("group_id", group_id),
            ("src_instance_id", it["instance_id"]),
        ])))

    STATS["finverbench"] = {
        "detection_total": len(det_in),
        "detection_has_error": sum(1 for r in det_gold if r["has_error"]),
        "detection_clean": sum(1 for r in det_gold if not r["has_error"]),
        "detection_ambiguous_flagged": sum(1 for r in det_gold if r["ambiguous_visible_text"]),
        "correction_pairs_valid": len(cor_in),
        "correction_pairs_quarantined_identical": n_identical,
        "companies": len({r["company"] for r in det_gold}),
        "groups": len({r["group_id"] for r in det_gold}),
    }
    return det_in, det_gold, cor_in, cor_gold


# ---------------------------------------------------------------------------
# 2. FinanceBench
# ---------------------------------------------------------------------------
MONTH_RE = re.compile(
    r"(January|February|March|April|May|June|July|August|September|October|November|December"
    r"|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec)")
NUM_RE = re.compile(r"(?P<cur>\$)?(?P<num>-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)(?P<pct>%)?")
OLD_NUM_RE = re.compile(r"\$?-?\d[\d,]*(?:\.\d+)?%?")   # v1 的宽松正则，仅用于留痕


def old_pick(answer):
    for tok in OLD_NUM_RE.findall(answer or ""):
        if tok.startswith("$") or tok.endswith("%") or ("," in tok) or ("." in tok):
            return tok
    return None


def is_year(num):
    return bool(re.fullmatch(r"(?:19|20)\d{2}", num))


def token_is_monetary_like(tok):
    return (tok.startswith("$") or tok.endswith("%") or "." in tok
            or bool(re.search(r"\d,\d{3}", tok)))


def candidate_ok(text, m):
    cur, num, pct = m.group("cur"), m.group("num"), m.group("pct")
    start, end = m.start(), m.end()
    if (end < len(text) and text[end].isdigit()) or (start > 0 and text[start - 1].isdigit()):
        return False
    currency, ratio, decimal = cur is not None, pct is not None, "." in num
    grouped = bool(re.search(r"\d,\d{3}", num))
    if not (currency or ratio or decimal or grouped):
        return False
    if is_year(num) and not (currency or ratio or decimal):
        return False
    pre = text[max(0, start - 20):start]
    if MONTH_RE.search(pre):
        return False
    if re.search(r"(?i)\b(?:FY|fiscal)\s*$", pre):
        return False
    if re.match(r"\s*,?\s*(?:19|20)\d{2}\b", text[end:end + 8]):
        return False
    try:
        if float(num.replace(",", "")) == 0:
            return False
    except ValueError:
        return False
    return True


def pick_perturbable(answer):
    for m in NUM_RE.finditer(answer or ""):
        if candidate_ok(answer, m):
            return m.group(0)
    return None


def perturb_number(tok, factor):
    m = re.match(r"^(\$?)(-?\d[\d,]*(?:\.(\d+))?)(%?)$", tok)
    if not m:
        return None
    pre, num, dec, post = m.group(1), m.group(2), m.group(3), m.group(4)
    has_comma = "," in num
    try:
        val = float(num.replace(",", ""))
    except ValueError:
        return None
    new = val * (1 + factor)
    nd = len(dec) if dec else 0
    s = "{:,.{}f}".format(abs(new), nd) if has_comma else "{:.{}f}".format(abs(new), nd)
    if new < 0:
        s = "-" + s
    return pre + s + post


def build_financebench():
    qpath = os.path.join(SRC, "FinanceBench/data/financebench_open_source.jsonl")
    mpath = os.path.join(SRC, "FinanceBench/data/financebench_document_information.jsonl")
    if not os.path.exists(qpath):
        print("[skip] FinanceBench 数据不存在")
        return [], [], [], [], []
    questions = [json.loads(l) for l in open(qpath, encoding="utf-8") if l.strip()]
    meta = {}
    if os.path.exists(mpath):
        for l in open(mpath, encoding="utf-8"):
            if l.strip():
                d = json.loads(l)
                meta[d.get("doc_name")] = d

    qa_in, qa_gold, cv_in, cv_gold, cor_in, cor_gold = [], [], [], [], [], []
    n_v1, n_bad_v1 = 0, 0
    for i, q in enumerate(sorted(questions, key=lambda x: str(x.get("financebench_id"))), start=1):
        ev = q.get("evidence") or []
        ctx = "\n\n".join((e.get("evidence_text_full_page") or e.get("evidence_text") or "") for e in ev)
        m = meta.get(q.get("doc_name"), {})
        group_id = "financebench::{}".format(q.get("doc_name"))
        sid = "fb-qa-%04d" % i

        qa_in.append(OrderedDict([
            ("sample_id", sid),
            ("source", "FinanceBench"),
            ("task", "evidence_grounded_financial_qa"),
            ("language", "en"),
            ("instruction", q.get("question")),
            ("evidence_text", "\n\n".join((e.get("evidence_text") or "") for e in ev)),
            ("evidence_page_num", [e.get("evidence_page_num") for e in ev]),
            ("context_full_page", ctx),
        ]))
        qa_gold.append(OrderedDict([
            ("sample_id", sid),
            ("answer", q.get("answer")),
            ("justification", q.get("justification")),
            ("question_type", q.get("question_type")),
            ("question_reasoning", q.get("question_reasoning")),
            ("company", q.get("company")),
            ("doc_name", q.get("doc_name")),
            ("doc_type", m.get("doc_type")),
            ("doc_period", m.get("doc_period")),
            ("gics_sector", m.get("gics_sector")),
            ("group_id", group_id),
            ("financebench_id", q.get("financebench_id")),
        ]))

        ans = q.get("answer") or ""
        tok = pick_perturbable(ans)
        old_tok = old_pick(ans)
        # [F3] 留痕：v1 会把非金额数值当金额改
        if old_tok and old_tok != tok and not token_is_monetary_like(old_tok):
            n_bad_v1 += 1
            quarantine("financebench/" + str(q.get("financebench_id")),
                       "FinanceBench/financebench_open_source.jsonl",
                       "financebench_nonmonetary_edit_v1",
                       {"v1_edited_token": old_tok, "v2_token": tok,
                        "note": "年份/日期/财年标签/编号被当作金额千分位"})
        old_wrong = perturb_number(old_tok, FACTOR) if old_tok else None
        if old_wrong and old_wrong != old_tok:
            n_v1 += 1

        if not tok:
            continue
        wrong = perturb_number(tok, FACTOR)
        if not (wrong and wrong != tok):
            continue
        csid = "fb-corr-%04d" % (len(cor_in) + 1)
        cor_in.append(OrderedDict([
            ("sample_id", csid),
            ("source", "FinanceBench"),
            ("task", "numeric_claim_correction"),
            ("language", "en"),
            ("instruction", q.get("question")),
            ("wrong_statement", ans.replace(tok, wrong, 1)),
        ]))
        cor_gold.append(check_label(OrderedDict([
            ("sample_id", csid),
            ("has_error", True),
            ("label", 1),
            ("label_text", "inconsistent"),
            ("correct_statement", ans),
            ("edit", {"from": tok, "to": wrong}),
            ("perturbation_rule", "rule_based_factor=+0.08"),
            ("perturbation_direction", "upward_only"),
            ("company", q.get("company")),
            ("doc_name", q.get("doc_name")),
            ("group_id", group_id),
            ("financebench_id", q.get("financebench_id")),
        ])))

        # 断言式的对偶：同一断言配一正一反两个核验样本
        for tag, claim, has_err in (("A", wrong, True), ("B", tok, False)):
            vsid = "fb-cv-%04d%s" % (len(cv_in) // 2 + 1, tag)
            cv_in.append(OrderedDict([
                ("sample_id", vsid),
                ("source", "FinanceBench"),
                ("task", "claim_consistency_verification"),
                ("language", "en"),
                ("instruction", q.get("question")),
                ("claim", ans.replace(tok, claim, 1)),
                ("evidence_text", "\n\n".join((e.get("evidence_text") or "") for e in ev)),
            ]))
            cv_gold.append(check_label(OrderedDict([
                ("sample_id", vsid),
                ("has_error", has_err),
                ("label", 1 if has_err else 0),
                ("label_text", "inconsistent" if has_err else "consistent"),
                ("error_category", "MR" if has_err else "none"),
                ("correct_statement", ans),
                ("company", q.get("company")),
                ("doc_name", q.get("doc_name")),
                ("group_id", group_id),
                ("financebench_id", q.get("financebench_id")),
            ])))

    STATS["financebench"] = {
        "qa_total": len(qa_in),
        "correction_pairs_valid": len(cor_in),
        "correction_pairs_v1_would_produce": n_v1,
        "correction_pairs_v1_nonmonetary_bad": n_bad_v1,
        "claim_verification_samples": len(cv_in),
        "docs": len({r["doc_name"] for r in qa_gold}),
    }
    return qa_in, qa_gold, cv_in, cv_gold, cor_in, cor_gold


# ---------------------------------------------------------------------------
# 3. FinBen
# ---------------------------------------------------------------------------
FINBEN_TASKS = {
    "finben-finer-ord": ("named_entity_recognition", "信息抽取：金融文本命名实体识别 (PER/LOC/ORG)"),
    "finben-fomc": ("central_bank_stance_classification", "文本分析：央行(FOMC)立场分类 (与 flare-fomc 内容重复)"),
    "flare-fomc": ("central_bank_stance_classification", "文本分析：央行(FOMC)立场分类 (hawkish/dovish/neutral)"),
    "flare-finred": ("relation_extraction", "信息抽取：金融关系抽取"),
    "flare-fnxl": ("xbrl_numeric_tagging", "信息抽取：财报数字/XBRL 标签抽取"),
    "flare-convfinqa": ("conversational_numeric_reasoning", "数值推理：财报多轮数值问答"),
    "flare-tatqa": ("table_text_numeric_reasoning", "数值推理：表格+文本数值问答"),
}



def build_finben():
    base = os.path.join(SRC, "FinBen")
    if not os.path.isdir(base):
        print("[skip] FinBen 数据不存在")
        return []
    try:
        import pandas as pd
    except ImportError:
        print("[skip] 需要 pandas / pyarrow 才能解析 FinBen parquet")
        return []

    outdir = os.path.join(DATA, "finben")
    os.makedirs(outdir, exist_ok=True)

    files = sorted(glob.glob(os.path.join(base, "**", "*.parquet"), recursive=True))
    bucket, summary = defaultdict(list), []
    for f in files:
        ds = os.path.relpath(f, base).split(os.sep)[0]
        if ds not in FINBEN_TASKS:
            continue
        try:
            df = pd.read_parquet(f)
        except Exception as e:
            print("[warn] 解析失败 {}: {}".format(os.path.relpath(f, ROOT), e))
            continue
        task, task_cn = FINBEN_TASKS[ds]
        split = os.path.basename(f).split("-")[0]
        for rec in df.to_dict(orient="records"):
            rec = to_jsonable(rec)
            sid = "finben/{}/{}/{}".format(ds, split, rec.get("id"))   # [F4] 带 split，全局唯一
            row = OrderedDict([
                ("sample_id", sid),
                ("source", "FinBen"),
                ("subset", ds),
                ("split", split),
                ("task", task),
                ("task_cn", task_cn),
                ("language", "en"),
                ("instruction", rec.get("query")),
                ("input", rec.get("text") if rec.get("text") is not None else rec.get("query")),
                ("gold_output", rec.get("answer")),
                # dialogue_id 与 id 一样是"每个官方划分各自编号"，必须带上 split 才不会把
                # 不同对话误判成同一组
                ("group_id", ("finben::flare-convfinqa::{}::{}".format(split, rec.get("dialogue_id"))
                              if ds == "flare-convfinqa" else "finben::{}::{}".format(ds, split))),
            ])
            extra = {k: v for k, v in rec.items() if k not in {"id", "query", "answer", "text"}}
            if extra:
                row["input_extra"] = extra
            if ds == "flare-convfinqa":
                row["dialogue_id"] = rec.get("dialogue_id")
                row["turn"] = rec.get("turn")
            bucket[(ds, split)].append(row)

    # [E1] 两份 FOMC 的 parquet 字节不同、内容却逐字相同，所以按"内容签名"去重，
    # 而不是按文件哈希。重叠子集保留 flare-* 命名的那个作为唯一计分副本。
    def _pref(k):
        ds_, sp_ = k
        return (0 if ds_.startswith("flare-") else 1, ds_, sp_)

    sig2key, dropped = {}, []
    for key in sorted(bucket, key=_pref):
        rows = bucket[key]
        sig = hashlib.sha256(json.dumps(
            sorted((str(r["input"]), str(r["gold_output"])) for r in rows),
            ensure_ascii=False).encode("utf-8")).hexdigest()
        if sig in sig2key:
            dropped.append((key, sig2key[sig]))
        else:
            sig2key[sig] = key
    for key, keep in dropped:
        for r in bucket[key]:
            quarantine(r["sample_id"], "FinBen/{}/".format(key[0]), "finben_duplicate_subset",
                       {"duplicate_of": "{}/{}".format(keep[0], keep[1]), "n_duplicated_rows": len(bucket[key]),
                        "note": "与保留副本逐字相同，重复计入会夸大样本量，不参与计分"})
        del bucket[key]

    finben_index = []
    for (ds, split), rows in sorted(bucket.items(), key=lambda kv: _pref(kv[0])):
        rows.sort(key=lambda r: str(r["sample_id"]))
        if ds == "flare-convfinqa":
            jdump(os.path.join(outdir, "flare-convfinqa.{}.jsonl".format(split)), rows)
            summary.append((ds + "." + split, FINBEN_TASKS[ds][1], len(rows)))
        else:
            jdump(os.path.join(outdir, "{}.jsonl".format(ds)), rows)
            summary.append((ds, FINBEN_TASKS[ds][1], len(rows)))
        for r in rows:
            finben_index.append(OrderedDict([
                ("sample_id", r["sample_id"]), ("source", "FinBen"),
                ("task", r["task"]), ("group_id", r["group_id"]),
                ("has_error", None), ("label_text", None),
                ("error_category", None), ("ambiguous_visible_text", False),
                ("orig_split", r["split"]),
            ]))
    STATS["finben"] = {name: n for name, cn, n in summary}
    STATS["finben_duplicates_dropped"] = {"{}/{}".format(k[0], k[1]):
                                          "{}/{}".format(v[0], v[1]) for k, v in dropped}
    return summary, finben_index


# ---------------------------------------------------------------------------
# 划分
# ---------------------------------------------------------------------------
def assign_split(source, group_id, subset=None, orig_split=None):
    """官方 test 划分优先：只要该组含官方 test 样本，整组归 test，评测集专用；
    其余组按 group_id 稳定哈希分流 dev/test。group 内绝不跨划分。"""
    if source == "FinBen":
        if subset == "flare-convfinqa" and orig_split in ("train", "valid"):
            return "dev" if stable_hash(group_id) == 0 else "test"
        return "test"                    # 官方 test-only 子集：永久评测专用
    return "dev" if stable_hash(group_id) == 0 else "test"


def main():
    for d in (DATA, AUDIT, SPLITS):
        if os.path.isdir(d):
            shutil.rmtree(d)
        os.makedirs(d, exist_ok=True)

    print("==== 1/4 FinVerBench ====")
    fv_det_in, fv_det_gold, fv_cor_in, fv_cor_gold = build_finverbench()

    print("\n==== 2/4 FinanceBench ====")
    qa_in, qa_gold, cv_in, cv_gold, fb_cor_in, fb_cor_gold = build_financebench()

    print("\n==== 3/4 FinBen ====")
    finben_summary, finben_index = build_finben()

    print("\n==== 4/4 划分 / 落盘 / 审计 ====")
    # 每个任务：(inputs, gold, 指标族)；输出目录名与任务名一致，
    # 文件命名沿用主线的 inputs.<split>.jsonl / gold.<split>.jsonl 约定。
    TASK_BUNDLES = [
        ("finverbench_detection", fv_det_in, fv_det_gold),
        ("finverbench_correction", fv_cor_in, fv_cor_gold),
        ("financebench_qa", qa_in, qa_gold),
        ("financebench_claim_verification", cv_in, cv_gold),
        ("financebench_correction", fb_cor_in, fb_cor_gold),
    ]

    # 先集中所有样本的分组键，按 group 统一决定划分，保证同组不跨 dev/test
    group_orig = defaultdict(set)
    for task, ins, golds in TASK_BUNDLES:
        for g in golds:
            group_orig[g["group_id"]].add("n/a")
    for r in finben_index:
        group_orig[r["group_id"]].add(r["orig_split"])
    group_split = {g: ("test" if "test" in o else ("dev" if stable_hash(g) == 0 else "test"))
                   for g, o in group_orig.items()}

    index, counts = [], {}
    for task, ins, golds in TASK_BUNDLES:
        gold_by_id = {g["sample_id"]: g for g in golds}
        buckets = defaultdict(lambda: ([], []))
        for i in ins:
            g = gold_by_id[i["sample_id"]]
            sp = group_split[g["group_id"]]
            buckets[sp][0].append(i)
            buckets[sp][1].append(g)
            index.append(OrderedDict([
                ("sample_id", i["sample_id"]), ("source", i["source"]), ("task", task),
                ("group_id", g["group_id"]), ("has_error", g.get("has_error")),
                ("label_text", g.get("label_text")),
                ("error_category", g.get("error_category")),
                ("ambiguous_visible_text", bool(g.get("ambiguous_visible_text"))),
                ("orig_split", "n/a"), ("split", sp),
            ]))
        for sp in ("dev", "test"):
            sub_i, sub_g = buckets[sp]
            if not sub_i:
                continue
            outdir = os.path.join(DATA, task)
            jdump(os.path.join(outdir, "inputs.{}.jsonl".format(sp)), sub_i)
            jdump(os.path.join(outdir, "gold.{}.jsonl".format(sp)), sub_g)
            counts["{}:{}".format(task, sp)] = len(sub_i)

    for r in finben_index:
        r["split"] = group_split[r["group_id"]]
        index.append(r)
    jdump(os.path.join(DATA, "index.jsonl"), index)

    dev = [r for r in index if r["split"] == "dev"]
    test = [r for r in index if r["split"] == "test"]
    jdump(os.path.join(SPLITS, "dev.jsonl"), dev)
    jdump(os.path.join(SPLITS, "test.jsonl"), test)
    group_map, group_n = defaultdict(set), Counter()
    for r in index:
        group_map[r["group_id"]].add(r["split"])
        group_n[r["group_id"]] += 1
    jdump(os.path.join(SPLITS, "groups.jsonl"), [
        OrderedDict([("group_id", g), ("splits", sorted(s)), ("n_samples", group_n[g])])
        for g, s in sorted(group_map.items())])

    leak = {g: s for g, s in group_map.items() if len(s) > 1}
    assert not leak, "同一文档组跨划分: %r" % (list(leak)[:3],)

    # 来源冻结
    src_hashes = []
    for f in sorted(glob.glob(os.path.join(SRC, "**", "*"), recursive=True)):
        if os.path.isfile(f):
            src_hashes.append(OrderedDict([
                ("path", os.path.relpath(f, ROOT)),
                ("sha256", sha256_file(f)),
                ("bytes", os.path.getsize(f)),
            ]))
    jwrite_json(os.path.join(AUDIT, "source_hashes.json"), src_hashes)
    jdump(os.path.join(AUDIT, "quarantine_candidates.jsonl"), QUARANTINE)

    reason_counts = Counter(q["reason"] for q in QUARANTINE)
    STATS["task_split_counts"] = counts
    summary = OrderedDict([
        ("generated_by", "scripts/build_datasets.py (v2)"),
        ("label_convention", LABEL_CONVENTION),
        ("stats", STATS),
        ("index_total", len(index)),
        ("split_dev", len(dev)),
        ("split_test", len(test)),
        ("groups_total", len(group_map)),
        ("groups_crossing_splits", len(leak)),
        ("quarantine_total", len(QUARANTINE)),
        ("quarantine_by_reason", dict(reason_counts)),
    ])
    jwrite_json(os.path.join(AUDIT, "audit_summary.json"), summary)

    artifacts = []
    for pat in ("data/**/*.jsonl", "data/**/*.jsonl.gz", "splits/*.jsonl", "audit/*.jsonl"):
        for f in sorted(glob.glob(os.path.join(ROOT, pat), recursive=True)):
            op = gzip.open if f.endswith(".gz") else open
            with op(f, "rt", encoding="utf-8") as fh:
                n = sum(1 for _ in fh)
            artifacts.append(OrderedDict([
                ("path", os.path.relpath(f, ROOT)), ("rows", n),
                ("bytes", os.path.getsize(f)), ("sha256", sha256_file(f)),
            ]))
    manifest = OrderedDict([
        ("schema_version", 2),
        ("label_convention", LABEL_CONVENTION),
        ("upstream_pins", UPSTREAM_PINS),
        ("upstream_file_hashes", "audit/source_hashes.json"),
        ("artifacts", artifacts),
        ("stats", STATS),
        ("split_dev", len(dev)),
        ("split_test", len(test)),
        ("quarantine_by_reason", dict(reason_counts)),
        ("notes", [
            "所有样本以 has_error 为权威标签；label 固定 1=有错 / 0=无错。",
            "data/<task>/inputs.<split>.jsonl 仅供模型可见；gold.<split>.jsonl 含标签与答案，禁止进入提示词。",
            "划分按原始文档分组，同组不跨 dev/test；官方 test 划分只作评测，不做 dev 抽样。",
            "audit/quarantine_candidates.jsonl 记录每条被排除样本及原因。",
        ]),
    ])
    jwrite_json(os.path.join(ROOT, "MANIFEST.json"), manifest)

    print("\n==== 汇总 ====")
    for k, v in STATS.items():
        print(" ", k, v)
    print("  index:", len(index), " dev:", len(dev), " test:", len(test), " groups:", len(group_map))
    print("  quarantine:", len(QUARANTINE), dict(reason_counts))
    for name, cn, n in finben_summary:
        print("   - {:<30s} {:>6d}  ({})".format(name, n, cn))


if __name__ == "__main__":
    main()
