# -*- coding: utf-8 -*-
"""诊断：菲达/龙源目标事实全量 + 燕塘首页 OCR 文本。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "factcheck" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pdfparse" / "src"))

from yjcheck.adapters import load_document  # noqa: E402
from yjcheck.pipeline import run_check  # noqa: E402

S = Path(r"C:\Users\xiaoliu\Desktop\project2\样本2")
OUT = Path(r"C:\Users\xiaoliu\Desktop\project2\frontend\data\diag2")

TARGETS = {
    "菲达环保": (S / "菲达环保" / "菲达环保模拟研报.docx",
                 [S / "菲达环保" / "菲达环保：立信会计师事务所（特殊普通合伙）关于浙江菲达环保科技股份有限公司对以前年度报告披露的财务报表数据由于同一控制下企业合并进行追溯调整的专项报告.pdf"]),
    "龙源电力": (S / "龙源电力" / "龙源电力模拟研报.docx",
                 [S / "龙源电力" / "龙源电力：关于龙源电力2025年度对以前年度报告披露的财务报表数据由于同一控制追溯调整的专项报告.pdf"]),
}

KEEP = {"net_profit", "net_profit_parent", "net_profit_parent_excl", "revenue",
        "cash", "capital_reserve", "operating_cashflow", "inventory"}


def main() -> int:
    for name, (report, sources) in TARGETS.items():
        print("#####", name)
        result, _ = run_check(report, sources, OUT)
        for fact in result["source_facts"]:
            if fact["metric"] in KEEP:
                print("  %-22s %16s %-4s %-11s %-8s %-11s warn=%s" % (
                    fact["metric"], fact["value"], fact["unit"], fact["period"],
                    fact["basis"], fact["scope"], fact["warnings"]))
        print(" findings:")
        for f in result["findings"]:
            c = f["claim"]
            print("   [%s] %s %s %s%s %s | %s" % (
                f["status"], f["error_type"], c["metric"], c.get("value"),
                c.get("unit"), c.get("period"), f["message"][:60]))
    print("##### 燕塘 首页文本")
    doc = load_document(S / "燕塘乳业" / "燕塘乳业：半年报财务报表.pdf", "source", OUT / "w2")
    for block in doc.blocks:
        if block.page and block.page <= 2 and block.text.strip():
            print(" p%s:" % block.page, (" ".join(block.text.split()))[:150])
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())