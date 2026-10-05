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
        "failure_list_csv": write_failure_list(out_dir),
    }
    return written


def write_failure_list(out_dir: Path) -> Path:
    """把需要人工处理的页面导成一张单表。

    与逐页质量表的区别：这里只保留 warn / fail 页，并给出建议动作，
    供测试与交付环节直接引用（对应交付清单里的“失败清单固化”）。
    """
    out_dir = Path(out_dir)
    rows = []
    for report_path in sorted(out_dir.glob("*/quality_report.json")):
        report = json.loads(report_path.read_text(encoding="utf-8"))
        engine = (report.get("engine") or {}).get("name", "")
        for page_no in sorted(report.get("warned_pages", []) + report.get("failed_pages", [])):
            reasons = report.get("reasons", {}).get(str(page_no), [])
            hard = [r for r in reasons if not str(r).startswith("info:")]
            if not hard:
                continue
            kinds = "；".join(sorted({str(r).split("=")[0].split(":")[0] for r in hard}))
            rows.append({
                "doc_id": report.get("doc_id", ""),
                "page": page_no,
                "status": report.get("page_status", {}).get(str(page_no), ""),
                "reasons": kinds,
                "detail": "；".join(str(r) for r in hard)[:300],
                "engine": engine,
                "source_path": report.get("source_path", ""),
                "suggested_action": _suggestion(kinds),
            })
    path = out_dir / "failure_list.csv"
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=[
            "doc_id", "page", "status", "reasons", "detail",
            "engine", "source_path", "suggested_action"], extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


def _suggestion(kinds: str) -> str:
    table = {
        "ocr_used": "该页文字来自 OCR，引用其数值前请人工核对原图",
        "engine_disagreement": "两套引擎内容不一致，人工比对页面图确认",
        "engine_agreement_low": "两套引擎差异较大，需重新解析或人工抄录",
        "table_empty_cells": "表格结构可疑，建议人工确认表头与合并单元格",
        "table_column_inconsistent": "表内列数不一致，人工确认跨页或合并结构",
        "no_text_layer": "无文本层，确认是否扫描件并评估 OCR 结果",
        "image_only_page": "整页图表，若正文在图中需 OCR 或人工录入",
        "garbled_ratio": "出现乱码，需更换引擎或人工核对",
        "bbox_missing": "坐标缺失，属契约级问题，需排查引擎输出",
        "bbox_out_of_page": "坐标严重越界，需排查页面旋转或坐标系",
    }
    actions = [table[k] for k in table if k in kinds]
    return "；".join(actions) or "人工复核该页解析结果"


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
