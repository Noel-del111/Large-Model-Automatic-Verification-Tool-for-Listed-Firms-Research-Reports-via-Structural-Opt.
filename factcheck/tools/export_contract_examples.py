"""Generate synthetic R09 integration fixtures without reading files or calling a model.

Only JSON fixtures are committed. --artifacts-out optionally writes the complete
C JSON/CSV/Markdown/manifest bundle for each fixture to a new run directory.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "factcheck/src"), str(ROOT / "pdfparse/src")]

from yjcheck.models import Block, Document
from yjcheck.pipeline import check_documents, write_result


def _document(role: str, texts: list[str]) -> Document:
    # These identify in-memory synthetic text, not a PDF/DOCX on disk.
    return Document(
        doc_id=f"fixture-{role}", sha256=sha256("\n".join(texts).encode("utf-8")).hexdigest(),
        run_id=f"fixture-{role}-parse", path=f"synthetic-{role}.docx", role=role,
        company="联调示例公司", period="2024FY",
        blocks=[Block(f"{role}-{index}", text, paragraph=index)
                for index, text in enumerate(texts, start=1)],
        metadata={"synthetic": True, "original_file_exists": False},
    )


def build_examples() -> dict[str, dict]:
    source = _document("source", ["2024年度合并利润表", "单位：万元",
                                  "项目 2024年度 2023年度", "营业收入 100 90"])
    cases = {
        "01_no_issue": ("2024年营业收入100万元。", [source]),
        "02_confirmed_error": ("2024年营业收入200万元。", [source]),
        "03_needs_review": ("2024年货币资金300万元。", [source]),
        "04_ask_two_periods": ("2024年营业收入同比增长20%。", []),
    }
    examples = {name: check_documents(_document("report", [text]), sources)
                for name, (text, sources) in cases.items()}

    # Reproduce both known historical contract shapes from the same content.
    legacy = deepcopy(examples["04_ask_two_periods"])
    legacy["schema_version"] = "1.0.0"
    legacy["summary"].pop("evidence_request_items")
    examples["06_legacy_request_count_only"] = deepcopy(legacy)
    legacy["summary"].pop("evidence_requests")
    for finding in legacy["findings"]:
        finding.pop("decision")
        finding.pop("evidence_request")
    examples["05_legacy_without_requests"] = legacy

    for name, result in examples.items():
        result["run_id"] = f"fixture-r09-{name}"
        # Pin fixture metadata for reviewable diffs; no performance measurement.
        result["created_at"] = "2026-10-05T00:00:00+08:00"
        result["summary"].pop("stage_seconds", None)
        result["fixture"] = {
            "synthetic": True, "purpose": "R09 interface integration, not an accuracy evaluation",
            "original_files_exist": False, "created_at_is_fixed_fixture_metadata": True,
            "generation": "check_documents with in-memory Document objects; no model calls",
            "legacy_shape_conversion": name.startswith(("05_", "06_")),
        }
    return dict(sorted(examples.items()))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "factcheck/examples/r09")
    parser.add_argument("--artifacts-out", type=Path,
                        help="Optional C export root; existing fixture run directories are not overwritten")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    examples = build_examples()
    for name, result in examples.items():
        (args.out / f"{name}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if args.artifacts_out:
            write_result(result, args.artifacts_out)
    print(f"Generated {len(examples)} synthetic fixtures in {args.out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
