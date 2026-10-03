"""导出核查结果的 Ask/Proceed 预测；金标准须独立人工标注。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def export_decisions(result: dict) -> list[dict]:
    predictions = []
    for index, finding in enumerate(result["findings"]):
        if "decision" not in finding or "evidence_request" not in finding:
            raise ValueError("此结果缺少取证字段，请使用更新后的核查流程重新生成。")
        predictions.append({
            "item_id": f"{result['run_id']}:{index}:{finding['id']}",
            "operation": "evidence_request",
            "decision": finding["decision"],
            "evidence_request": finding["evidence_request"],
            # 原文偏移是块内偏移，不能冒充 FinED 的全文偏移。
            "findings": [],
        })
    return predictions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    result = json.loads(Path(args.result).read_text(encoding="utf-8"))
    predictions = export_decisions(result)
    dest = Path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(predictions, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已导出 {len(predictions)} 条取证决策；金标准须独立人工标注。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
