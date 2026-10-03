# -*- coding: utf-8 -*-
"""单份财报解析诊断：页状态、notes、OCR 块数与文本样例。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "factcheck" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pdfparse" / "src"))

from yjparse.pipeline import PipelineConfig, parse_document  # noqa: E402


def main() -> int:
    path = Path(sys.argv[1])
    engine = sys.argv[2] if len(sys.argv) > 2 else "pdfplumber"
    result = parse_document(path, Path("frontend/data/parse_diag"), PipelineConfig(
        primary=engine, reference="none", ocr="auto"))
    data = result.to_dict()
    print("engine:", data["engine"]["name"], "pages:", len(data["pages"]),
          "status:", data["quality_report"]["status"])
    print("failed:", data["quality_report"]["failed_pages"],
          "warned:", data["quality_report"]["warned_pages"])
    for page in data["pages"][:6]:
        text = "".join(b.get("text", "") or "" for b in page["blocks"])
        ocr_blocks = [b for b in page["blocks"] if "ocr" in str(b.get("block_id", ""))]
        conf = [b.get("confidence") for b in page["blocks"] if b.get("confidence") is not None]
        print(f"p{page['page']} status={page['status']} chars={len(text)} "
              f"ocr_blocks={len(ocr_blocks)} conf={conf[:3]} notes={page.get('notes', [])}")
        if text.strip():
            print("  TEXT:", text[:120].replace("\n", " | "))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())