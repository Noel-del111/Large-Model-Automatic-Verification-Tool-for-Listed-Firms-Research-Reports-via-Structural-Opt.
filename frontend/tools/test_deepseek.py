# -*- coding: utf-8 -*-
"""DeepSeek 实测四段式：连通性 → C 大模型候选抽取 → 助手单条追问 → 报告问答。

只读环境变量（YJCHECK_BASE_URL / YJCHECK_MODEL / YJCHECK_API_KEY），
密钥不写入任何文件；留痕 assistant_traces.jsonl 不含密钥。
"""
from __future__ import annotations

import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent          # frontend/tools
FRONTEND = BASE.parent
ROOT = FRONTEND.parent
sys.path.insert(0, str(FRONTEND))
sys.path.insert(0, str(ROOT / "factcheck" / "src"))
sys.path.insert(0, str(ROOT / "pdfparse" / "src"))

from yjcheck.model import ModelConfig  # noqa: E402
from yjcheck.pipeline import run_check  # noqa: E402

from assistant import (TraceLog, _SYSTEM, _chat,  # noqa: E402
                       ask_finding_question, ask_report_question)

DEMO = FRONTEND / "data" / "demo"
CHECK_OUT = FRONTEND / "data" / "check"


def main() -> int:
    config = ModelConfig.from_env()
    if not config.base_url or not config.model or not config.api_key:
        print("缺少配置：请设置 YJCHECK_BASE_URL / YJCHECK_MODEL / YJCHECK_API_KEY")
        return 2
    print(f"端点 {config.base_url}　模型 {config.model}　密钥已配置(长度 {len(config.api_key)})")

    # 1) 连通性
    try:
        pong = _chat(config, "你只能回答：ok", "ping", timeout=120)
        print("连通性 OK ->", pong[:80].replace("\n", " "))
    except Exception as exc:
        print("连通性失败：%s %s" % (type(exc).__name__, str(exc)[:200]))
        return 1

    # 2) C 大模型候选抽取（真实 DeepSeek，规则已识别项仍走确定性判定）
    result, out_dir = run_check(str(DEMO / "示例研报_2025H1.pdf"),
                                [str(DEMO / "示例财报_2025H1.pdf")],
                                str(CHECK_OUT), model_config=config)
    traces = result["model_traces"]
    print("C 候选抽取 traces=%d status=%s accepted=%d" % (
        len(traces), [t["status"] for t in traces], sum(t.get("accepted", 0) for t in traces)))
    print("summary:", result["summary"])
    confirmed = next((f for f in result["findings"] if f["status"] == "confirmed_error"), None)
    if confirmed is None:
        print("警告：样本未复现已确认错误，跳过助手问答")
        return 1

    trace_log = TraceLog(out_dir)

    # 3) 助手单条追问
    answer = ask_finding_question(
        result, confirmed,
        "为什么判定为数值错误？依据在财报第几页？应该改成多少？", config, trace_log)
    print("\n[单条追问] mode=%s 警告=%d" % (answer["mode"], len(answer["warnings"])))
    for warning in answer["warnings"]:
        print("  警告:", warning[:120])
    print("回答：%s" % answer["answer"][:600].replace("\n", " "))

    # 4) 报告问答
    report_answer = ask_report_question(
        result, "这份报告有哪些问题？哪些该优先处理？", config, trace_log)
    print("\n[报告问答] mode=%s 命中=%d 警告=%d" % (
        report_answer["mode"], len(report_answer.get("hits", [])), len(report_answer.get("warnings", []))))
    for warning in report_answer.get("warnings", []):
        print("  警告:", warning[:120])
    print("回答：%s" % report_answer["answer"][:600].replace("\n", " "))

    # 5) 留痕去密钥与新候选语义标注
    trace_text = trace_log.path.read_text(encoding="utf-8")
    assert config.api_key not in trace_text, "留痕不得包含密钥"
    model_extra = [f for f in result["findings"]
                   if (f.get("claim") or {}).get("attributes", {}).get("extraction") == "model"]
    print("\n模型新增候选（语义转人工）:", len(model_extra))
    print("留痕 OK: %s（%d 行）" % (trace_log.path, len(trace_text.splitlines())))
    print("DEEPSEEK ALL OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())