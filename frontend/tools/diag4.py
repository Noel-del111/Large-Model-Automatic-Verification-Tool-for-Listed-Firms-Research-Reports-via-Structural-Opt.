# -*- coding: utf-8 -*-
"""打印样本2 各 case 的 check_result findings（从 evaluation.json 定位）。"""
import glob
import json
import sys
from pathlib import Path

EVAL = Path(r"c:\Users\xiaoliu\Desktop\project2\frontend\data\eval2\evaluation.json")
ev = json.loads(EVAL.read_text(encoding="utf-8"))
for case in ev["cases"]:
    print("#####", case["case"], "| checker:", case["checker_summary"])
    result_files = glob.glob(str(Path(case["result_dir"]) / "**" / "check_result.json"), recursive=True)
    if not result_files:
        print("  (no check_result.json)")
        continue
    result = json.loads(Path(result_files[0]).read_text(encoding="utf-8"))
    for f in result["findings"]:
        c = f["claim"]
        print("  [%s/%s/%s] %s %s%s %s %s/%s | %s" % (
            f["status"], f["error_type"], f["rule_id"], c["metric"],
            c.get("value"), c.get("unit"), c.get("period"), c.get("basis"),
            c.get("scope"), f["message"][:40]))