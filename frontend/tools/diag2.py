# -*- coding: utf-8 -*-
"""样本2深度诊断：来源事实、文档问题、finding 判定全景。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE.parent))
sys.path.insert(0, str(BASE.parent.parent / "factcheck" / "src"))
sys.path.insert(0, str(BASE.parent.parent / "pdfparse" / "src"))

from yjcheck.pipeline import run_check  # noqa: E402

SAMPLES = {
    "中瓷电子": (r"C:\Users\xiaoliu\Desktop\project2\样本2\中瓷电子\中瓷电子模拟研报.docx",
                 [r"C:\Users\xiaoliu\Desktop\project2\样本2\中瓷电子\中瓷电子：半年报财务报表.pdf"]),
    "燕塘乳业": (r"C:\Users\xiaoliu\Desktop\project2\样本2\燕塘乳业\燕塘乳业模拟研报.docx",
                 [r"C:\Users\xiaoliu\Desktop\project2\样本2\燕塘乳业\燕塘乳业：半年报财务报表.pdf"]),
    "菲达环保": (r"C:\Users\xiaoliu\Desktop\project2\样本2\菲达环保\菲达环保模拟研报.docx",
                 [r"C:\Users\xiaoliu\Desktop\project2\样本2\菲达环保\菲达环保：立信会计师事务所（特殊普通合伙）关于浙江菲达环保科技股份有限公司对以前年度报告披露的财务报表数据由于同一控制下企业合并进行追溯调整的专项报告.pdf"]),
    "龙源电力": (r"C:\Users\xiaoliu\Desktop\project2\样本2\龙源电力\龙源电力模拟研报.docx",
                 [r"C:\Users\xiaoliu\Desktop\project2\样本2\龙源电力\龙源电力：关于龙源电力2025年度对以前年度报告披露的财务报表数据由于同一控制追溯调整的专项报告.pdf"]),
}


def main() -> int:
    for name, (report, sources) in SAMPLES.items():
        print("=" * 20, name)
        result, out = run_check(report, sources, "frontend/data/diag2")
        print("summary:", result["summary"])
        for item in result["input_issues"]:
            print("issues[%s]:" % Path(item["file"]).name, item["issues"][:8])
        print("公司绑定:", [(d["role"], d.get("company"), d.get("path", "").split("\\")[-1][:20])
                           for d in result["documents"]])
        print("-- source_facts (前 25 条) --")
        for f in result["source_facts"][:25]:
            print(f"  {f['metric']:<18} {f['value']:>14} {f['unit']:<4} {f['period']:<10} "
                  f"basis={f['basis']:<8} scope={f['scope']:<11} warn={f['warnings']}")
        print("-- findings --")
        for f in result["findings"]:
            c = f["claim"]
            print(f"  [{f['status']:<14}] {f['error_type']:<6} {c['metric']:<14} "
                  f"{c.get('value')}{c.get('unit')} {c.get('period')} {c.get('basis')}/{c.get('scope')} "
                  f"| {f['message'][:70]}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())