# -*- coding: utf-8 -*-
"""FinED-Bench 公开测评集 · 研报子集类型分布对照工具。

学习公开基准（fined_bench/eval_data.json）中与本项目任务域一致的"行业研报/个股研报"
场景的错误类型分布，输出到 repo/data/fined_research_profile.json（与 Markdown 概览），
供评测报告引用作为类型覆盖的公开对照。仅做统计，幂等覆盖，不进入核查链路。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EVAL_DATA = ROOT / "测评集" / "测评集" / "FinED-Bench-main" / "fined_bench" / "eval_data.json"
RESEARCH_SCENES = {"行业研报", "个股研报"}
OUT_JSON = ROOT / "repo" / "data" / "fined_research_profile.json"
OUT_MD = ROOT / "repo" / "data" / "fined_research_profile.md"


def profile(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    scenes = Counter(item.get("scene", "?") for item in data)
    research = [item for item in data if item.get("scene") in RESEARCH_SCENES]
    types = Counter()
    samples: dict[str, list[str]] = {}
    for item in research:
        for error in item.get("errors", []):
            label = error.get("error_type", "?")
            types[label] += 1
            span = error.get("error_span")
            text = span[0] if isinstance(span, list) and span else str(span or "")
            flat = "".join(text.split())[:60]
            if len(samples.get(label, [])) < 2 and flat and flat not in samples.get(label, []):
                samples.setdefault(label, []).append(flat)
    return {
        "source": str(path.resolve()),
        "benchmark": "FinED-Bench (Are Large Language Models Reliable Reviewers? A Benchmark for Error Detection in Financial Documents)",
        "total_docs": len(data),
        "scene_counts": dict(sorted(scenes.items(), key=lambda kv: -kv[1])),
        "research_docs": len(research),
        "research_error_type_counts": dict(sorted(types.items(), key=lambda kv: -kv[1])),
        "samples": samples,
        "note": "仅统计行业研报/个股研报场景；作为类型覆盖对照，替代不了自建研报-财报配对盲测集",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--eval-data", type=Path, default=DEFAULT_EVAL_DATA,
                        help=f"FinED-Bench eval_data.json（默认 {DEFAULT_EVAL_DATA}）")
    args = parser.parse_args()
    if not args.eval_data.is_file():
        parser.error(f"eval_data.json 不存在：{args.eval_data}")
    result = profile(args.eval_data)
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# FinED-Bench 研报子集 · 错误类型分布对照", "",
             f"- 来源：{Path(result['source']).name}；全集 {result['total_docs']} 篇，场景分布："
             f"{result['scene_counts']}", "",
             f"- 研报相关子集（行业研报+个股研报）：{result['research_docs']} 篇", "",
             "| 错误类型 | 数量 | 示例 |",
             "| --- | --- | --- |"]
    for label, count in result["research_error_type_counts"].items():
        example = result["samples"].get(label, [""])[0]
        lines.append(f"| {label} | {count} | `{example}` |")
    lines += ["", result["note"], ""]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"research_docs": result["research_docs"],
                      "error_types": len(result["research_error_type_counts"])}, ensure_ascii=False))
    print(str(OUT_JSON.resolve()))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())