"""质量报告汇总：把逐篇产物合并成可交付的表格与结论。"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict, List

from .artifacts import CSV_COLUMNS
from .utils import write_json


def collect_quality_rows(out_dir: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for path in sorted(Path(out_dir).glob("*/quality_table.csv")):
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                row["_source"] = str(path)
                rows.append(row)
    return rows


def summarize(out_dir: Path) -> Dict[str, Any]:
    out_dir = Path(out_dir)
    rows = collect_quality_rows(out_dir)
    docs: Dict[str, Dict[str, Any]] = {}
    for report_path in sorted(out_dir.glob("*/quality_report.json")):
        report = json.loads(report_path.read_text(encoding="utf-8"))
        docs[report["doc_id"]] = {
            "status": report["status"],
            "pages": report["summary"].get("pages"),
            "failed_pages": report["failed_pages"],
            "warned_pages": report["warned_pages"],
            "engine": report.get("engine"),
            "reference_engine": report.get("reference_engine"),
            "violations": report.get("violations", []),
        }
    summary = {
        "documents": len(docs),
        "pages": len(rows),
        "status_counts": {
            "ok": sum(1 for r in rows if r["status"] == "ok"),
            "warn": sum(1 for r in rows if r["status"] == "warn"),
            "fail": sum(1 for r in rows if r["status"] == "fail"),
        },
        "review_queue": [
            {"doc_id": r["doc_id"], "page": int(r["page"]),
             "status": r["status"], "reasons": r["reasons"]}
            for r in rows if r["status"] == "fail"
        ],
        "documents_detail": docs,
    }
    return summary


def write_summary(out_dir: Path) -> Dict[str, Path]:
    out_dir = Path(out_dir)
    summary = summarize(out_dir)
    written = {
        "quality_summary_json": write_json(out_dir / "quality_summary.json", summary),
        "quality_summary_csv": _write_csv(out_dir / "quality_summary.csv",
                                          collect_quality_rows(out_dir)),
    }
    return written


def _write_csv(path: Path, rows: List[Dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = CSV_COLUMNS
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in columns})
    return path


def render_summary_table(summary: Dict[str, Any]) -> str:
    """终端可读的汇总表，便于现场演示与答辩。"""
    lines = [
        f"文档数：{summary['documents']}    页数：{summary['pages']}    "
        f"正常：{summary['status_counts']['ok']}    "
        f"警告：{summary['status_counts']['warn']}    "
        f"失败：{summary['status_counts']['fail']}",
    ]
    if summary["review_queue"]:
        lines.append("")
        lines.append("待复核页：")
        lines.append(f"  {'文档':<28}{'页':>5}  {'状态':<6} 原因")
        for item in summary["review_queue"][:20]:
            lines.append(
                f"  {item['doc_id'][:28]:<28}{item['page']:>5}  "
                f"{item['status']:<6} {item['reasons'][:60]}"
            )
        if len(summary["review_queue"]) > 20:
            lines.append(f"  ... 另有 {len(summary['review_queue']) - 20} 页")
    return "\n".join(lines)
