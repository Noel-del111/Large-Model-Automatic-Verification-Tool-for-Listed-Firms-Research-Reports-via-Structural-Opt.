# -*- coding: utf-8 -*-
"""端到端冒烟：用合成样本跑 C 核查流水线，并验证 D 的模块（复核/导出/高亮）。"""
from __future__ import annotations

import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent  # frontend/tools
FRONTEND = BASE.parent
ROOT = FRONTEND.parent
sys.path.insert(0, str(FRONTEND))
sys.path.insert(0, str(ROOT / "factcheck" / "src"))
sys.path.insert(0, str(ROOT / "pdfparse" / "src"))

from yjcheck.pipeline import run_check, verify_artifacts  # noqa: E402

from exports import findings_csv_bytes, full_json_bytes, report_md, report_pdf_bytes  # noqa: E402
from highlight import CLAIM_COLOR, render_location_image  # noqa: E402
from review_store import ReviewStore  # noqa: E402

DEMO = FRONTEND / "data" / "demo"
CHECK_OUT = FRONTEND / "data" / "check"


def main() -> int:
    report = DEMO / "示例研报_2025H1.pdf"
    source = DEMO / "示例财报_2025H1.pdf"
    result, out_dir = run_check(str(report), [str(source)], str(CHECK_OUT))
    summary = result["summary"]
    print("SUMMARY:", summary)
    assert summary["confirmed_error"] >= 1, "期望至少一条已确认错误"
    assert summary["no_issue"] >= 1, "期望至少一条未发现问题"
    for finding in result["findings"]:
        claim = finding["claim"]
        print(f"- {finding['status']:<16} {finding['error_type']:<6} "
              f"{claim.get('metric')} {claim.get('value')}{claim.get('unit')} {claim.get('period')}"
              f" -> {finding.get('suggested_value')}")

    # D：复核状态机
    store = ReviewStore(out_dir)
    store.set(result["findings"][0]["id"], "confirmed", "核对无误，采纳", "冒烟测试员")
    stats = store.stats()
    print("REVIEW STATS:", stats)
    assert stats["confirmed"] == 1

    # D：导出
    reviews = store.load()
    csv_data = findings_csv_bytes(result, reviews)
    assert csv_data.startswith(b"\xef\xbb\xbf"), "CSV 应为带 BOM 的 UTF-8"
    md = report_md(result, reviews)
    assert "已确认错误" in md
    json_data = full_json_bytes(result, reviews)
    assert b'"review"' in json_data
    pdf = report_pdf_bytes(result, reviews)
    print("PDF bytes:", len(pdf) if pdf else "None(不可用)")
    assert pdf and pdf[:4] == b"%PDF", "PDF 导出应为有效 PDF"

    # D：证据高亮
    first_confirmed = next(f for f in result["findings"] if f["status"] == "confirmed_error")
    sites = [(str(e["file"]), e["page"], e["bbox"])
             for e in first_confirmed["claim"]["evidence"] if e.get("page")]
    print("CLAIM SITES:", sites)
    assert sites, "确认错误的声明应带页码定位"
    png = render_location_image(sites[0][0], sites[0][1], [(sites[0][2], CLAIM_COLOR)])
    assert png and png[:4] == b"\x89PNG", "高亮图应为 PNG"

    # D：产物完整性校验不受 review.json 影响
    assert verify_artifacts(str(out_dir)), "复核文件不应破坏 C 产物校验"
    print("ALL OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())