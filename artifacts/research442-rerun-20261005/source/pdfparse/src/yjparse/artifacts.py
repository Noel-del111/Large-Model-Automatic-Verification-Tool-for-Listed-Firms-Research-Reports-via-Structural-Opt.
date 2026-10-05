"""产物落盘与复现校验。"""

from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from .contract import ParseResult
from .utils import ensure_dir, sha256_file, write_json

CSV_COLUMNS = [
    "doc_id", "page", "status", "char_count", "block_count", "table_count",
    "text_coverage", "garbled_ratio", "engine_agreement",
    "table_col_inconsistent", "table_empty_cell_ratio", "reasons",
]


def write_artifacts(result: ParseResult, out_dir: Path, ledger=None) -> Dict[str, Path]:
    current_dir = ensure_dir(Path(out_dir) / result.doc.doc_id)
    doc_dir = current_dir / "runs" / result.run_id
    doc_dir.mkdir(parents=True, exist_ok=False)
    written: Dict[str, Path] = {}

    written["parse_result"] = write_json(doc_dir / "parse_result.json", result.to_dict())

    pages_path = doc_dir / "pages.jsonl"
    with open(pages_path, "w", encoding="utf-8") as fh:
        for page in result.to_dict()["pages"]:
            fh.write(json.dumps({"doc_id": result.doc.doc_id, "run_id": result.run_id,
                                 **page}, ensure_ascii=False) + "\n")
    written["pages"] = pages_path

    blocks_path = doc_dir / "blocks.jsonl"
    rows = 0
    with open(blocks_path, "w", encoding="utf-8") as fh:
        for page in result.to_dict()["pages"]:
            for block in page["blocks"]:
                fh.write(json.dumps({
                    "doc_id": result.doc.doc_id,
                    "run_id": result.run_id,
                    "page": page["page"],
                    "page_size": page["page_size"],
                    "page_status": page["status"],
                    **block,
                }, ensure_ascii=False) + "\n")
                rows += 1
    written["blocks"] = blocks_path

    written["quality_report"] = write_json(doc_dir / "quality_report.json",
                                           _quality_report_dict(result))
    written["quality_table"] = _write_quality_csv(result, doc_dir / "quality_table.csv")

    # Stable top-level paths remain the current-version interface for CLI/UI.
    # Ledgers only hash immutable run paths, so a reparse cannot invalidate history.
    for path in written.values():
        shutil.copy2(path, current_dir / path.name)
    write_json(current_dir / "latest.json", {"run_id": result.run_id,
               "parse_result": str(written["parse_result"]), "sha256": result.doc.sha256})

    if ledger is not None:
        for kind, path in written.items():
            ledger.artifact(path, sha256=sha256_file(path), kind=kind)
        ledger.artifact(blocks_path, rows=rows, sha256=sha256_file(blocks_path), kind="blocks_rows")
    return written


def _quality_report_dict(result: ParseResult) -> Dict[str, Any]:
    report = result.quality_report
    return {
        "doc_id": result.doc.doc_id,
        "run_id": result.run_id,
        "source_path": result.doc.source_path,
        "source_sha256": result.doc.sha256,
        "engine": {"name": result.engine.name, "version": result.engine.version,
                   "strategy": result.engine.strategy},
        "reference_engine": result.reference_engine,
        "status": report.status,
        "page_status": report.page_status,
        "failed_pages": report.failed_pages,
        "warned_pages": report.warned_pages,
        "reasons": report.reasons,
        "violations": report.violations,
        "summary": report.summary,
    }


def _write_quality_csv(result: ParseResult, path: Path) -> Path:
    ensure_dir(path.parent)
    pages = result.to_dict()["pages"]
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for page in pages:
            quality = page["quality"]
            writer.writerow({
                "doc_id": result.doc.doc_id,
                "page": page["page"],
                "status": page["status"],
                "char_count": quality.get("char_count"),
                "block_count": quality.get("block_count"),
                "table_count": quality.get("table_count"),
                "text_coverage": quality.get("text_coverage"),
                "garbled_ratio": quality.get("garbled_ratio"),
                "engine_agreement": quality.get("engine_agreement"),
                "table_col_inconsistent": quality.get("table_col_inconsistent"),
                "table_empty_cell_ratio": quality.get("table_empty_cell_ratio"),
                "reasons": ";".join(page.get("notes") or []),
            })
    return path


def verify_run(out_dir: Path, doc_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """按运行清单里的哈希逐项复核产物，返回每个文件的校验结果。"""
    out_dir = Path(out_dir)
    checks: List[Dict[str, Any]] = []
    manifest_paths = sorted(out_dir.glob("logs/*/manifest.json"))
    if not manifest_paths:
        raise FileNotFoundError(f"未找到运行清单：{out_dir / 'logs' / '<run_id>' / 'manifest.json'}")
    for manifest_path in manifest_paths:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for entry in manifest.get("artifacts", []):
            path = Path(entry["artifact"])
            if doc_id and doc_id not in str(path):
                continue
            exists = path.exists()
            actual = sha256_file(path) if exists else ""
            checks.append({
                "run_id": manifest.get("run_id"),
                "artifact": str(path),
                "exists": exists,
                "expected": entry.get("sha256", ""),
                "actual": actual,
                "match": bool(exists and actual == entry.get("sha256", "")),
            })
    return checks
