"""Prepare normal-text candidates for human review, then import signed-off negatives.

The public SFT originals are candidates, not verified ground truth. They are kept
in the separate negative_review track, never in FinED's blind evaluation splits.
An operator must review the full candidate and fill the approval fields before
approve-import can produce an empty-error gold record. This tool never approves.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re

TRACK = "negative_review"
RESEARCH_TITLE = re.compile(r"研报|行业|点评|周观点|月报")
HARD_NEGATIVES = (
    ("unit_conversion", "单位换算", "某公司2025年营业收入为1亿元，即10,000万元。", "1亿元与10,000万元相等。"),
    ("different_periods", "不同期间", "甲公司2024年营业收入为100万元，2025年营业收入为120万元。", "两笔数据所属年度不同，不能据此判为数值冲突。"),
    ("different_calibers", "不同利润口径", "甲公司2025年净利润为30万元，其中归属于母公司股东的净利润为25万元，少数股东损益为5万元。", "净利润与归母净利润口径不同，25+5=30。"),
    ("different_entities", "不同主体", "2025年，甲公司营业收入为100万元，乙公司营业收入为120万元。", "公司主体不同，不能据此判为数值冲突。"),
    ("percent_and_points", "百分比与百分点", "甲公司毛利率由2024年的20%上升至2025年的25%，提高了5个百分点，相对增幅为25%。", "25%-20%=5个百分点；(25%-20%)/20%=25%。"),
    ("consistent_table", "表格与正文一致", "甲公司2025年各业务营业收入如下，单位为万元。\n|业务|营业收入|\n|---|---:|\n|业务甲|40|\n|业务乙|60|\n|合计|100|\n甲公司2025年营业收入合计100万元。", "40+60=100，表格总计与正文相符。"),
)


def sha256(value: str | bytes) -> str:
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def read_jsonl(path: str | Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def jsonl(records) -> str:
    return "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records)


def _candidate(identity, title, content, provenance, note):
    return {"doc_id": identity, "title": title, "scene": "研报正常片段候选",
            "source_track": TRACK, "content": content, "content_sha256": sha256(content),
            "provenance": provenance, "review_note": note,
            "human_review_status": "pending", "reviewer": None, "approved_at": None,
            "review_comments": "", "eligible_for_blind_evaluation": False}


def prepare_review_pack(source: str | Path, output: str | Path) -> dict:
    source, output = Path(source), Path(output)
    if output.exists():
        raise FileExistsError("待审包已存在；为保留人工意见，请使用新输出文件")
    raw = source.read_bytes()
    rows = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(rows, list):
        raise ValueError("SFT source must be a JSON array")
    source_hash, candidates, seen = sha256(raw), [], set()
    for index, row in enumerate(rows, 1):
        title, content = row.get("name", ""), row.get("origin_text", "")
        if not isinstance(title, str) or not RESEARCH_TITLE.search(title):
            continue
        if not isinstance(content, str) or not content.strip() or sha256(content) in seen:
            continue
        seen.add(sha256(content))
        candidates.append(_candidate(
            f"negative:sft:{source_hash[:16]}:{index:06d}", title, content,
            {"kind": "public_sft_original", "file": source.name, "source_sha256": source_hash,
             "source_row": index, "selection": "research_title_keywords", "source_field": "origin_text",
             "overlap_with_public_benchmark": "possible_same_source_not_a_blind_set"},
            "公开SFT原文仅为候选。请对照完整研报与来源核实事实、单位、期间及上下文；缺上下文或证据时不要批准。"))
    for code, title, content, note in HARD_NEGATIVES:
        candidates.append(_candidate(f"negative:synthetic:{code}:{sha256(content)[:16]}", title, content,
                                     {"kind": "synthetic_control", "scenario": code,
                                      "overlap_with_public_benchmark": "not_a_real_world_document"},
                                     note + " 合成控制样本，仍需人工签核；不得代替真实场景的误报率。"))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(jsonl(candidates), encoding="utf-8", newline="\n")
    return {"path": str(output.resolve()), "candidates": len(candidates),
            "by_origin": dict(Counter(r["provenance"]["kind"] for r in candidates)),
            "human_review_status": "pending", "approved": 0, "source_track": TRACK,
            "source_sha256": source_hash}


def import_approved(review_file: str | Path, output_dir: str | Path) -> dict:
    """Fail closed for any unsigned row. Export a separately selected signed file."""
    records = read_jsonl(review_file)
    if not records:
        raise ValueError("No approved records supplied")
    identities, inputs, golds, approvals = set(), [], [], []
    for index, record in enumerate(records, 1):
        identity, content = record.get("doc_id"), record.get("content")
        if not isinstance(identity, str) or not identity or identity in identities:
            raise ValueError(f"row {index}: missing or duplicate doc_id")
        identities.add(identity)
        if record.get("source_track") != TRACK or record.get("eligible_for_blind_evaluation") is not False:
            raise ValueError(f"{identity}: must remain in the separate negative_review track")
        if not isinstance(content, str) or not content.strip() or sha256(content) != record.get("content_sha256"):
            raise ValueError(f"{identity}: content hash mismatch; review the changed text before import")
        reviewer, approved_at = record.get("reviewer"), record.get("approved_at")
        if record.get("human_review_status") != "approved" or not isinstance(reviewer, str) or not reviewer.strip():
            raise ValueError(f"{identity}: explicit human approval and nonempty reviewer required")
        try:
            approved_time = datetime.fromisoformat(approved_at.replace("Z", "+00:00"))
            if approved_time.tzinfo is None:
                raise ValueError("timezone required")
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError(f"{identity}: approved_at must be an ISO timestamp with timezone") from exc
        inputs.append({"doc_id": identity, "title": record.get("title", ""), "scene": record.get("scene", ""),
                       "content": content, "content_sha256": sha256(content), "source_track": TRACK,
                       "split": TRACK, "eligible_for_blind_evaluation": False,
                       "source_kind": record.get("provenance", {}).get("kind", "unknown")})
        golds.append({"document_id": identity, "errors": []})
        approvals.append({key: record.get(key) for key in ("doc_id", "content_sha256", "human_review_status",
                                                          "reviewer", "approved_at", "review_comments", "provenance")})
    output = Path(output_dir)
    if output.exists() and any(output.iterdir()):
        raise FileExistsError("Approval output directory must be empty; do not overwrite a prior signed dataset")
    output.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for name, data in (("inputs.negative_review.jsonl", inputs), ("gold.negative_review.jsonl", golds),
                       ("approvals.jsonl", approvals)):
        payload = jsonl(data)
        (output / name).write_text(payload, encoding="utf-8", newline="\n")
        hashes[name] = sha256(payload)
    manifest = {"source_track": TRACK, "documents": len(inputs), "review_file_sha256": sha256(Path(review_file).read_bytes()),
                "files_sha256": hashes, "eligible_for_blind_evaluation": False,
                "limits": "Public SFT and synthetic controls must be reported separately from the FinED blind benchmark."}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare", help="create candidates, all pending human review")
    prepare.add_argument("--source", required=True, help="sft_data_wo_errors.json")
    prepare.add_argument("--out", default="data/v2/negative_review.jsonl")
    approve = sub.add_parser("approve-import", help="import only an entirely human-approved review file")
    approve.add_argument("--review-file", required=True)
    approve.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        result = prepare_review_pack(args.source, args.out) if args.command == "prepare" else import_approved(args.review_file, args.out)
    except (ValueError, OSError) as exc:
        parser.exit(2, str(exc) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
