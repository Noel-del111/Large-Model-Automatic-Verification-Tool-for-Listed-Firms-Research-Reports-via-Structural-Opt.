# -*- coding: utf-8 -*-
"""助手层冒烟：在线问答（本地 Mock 端点）、数值护栏、离线降级、留痕去密钥。"""
from __future__ import annotations

import os
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import mock_chat_server  # 同目录

BASE = Path(__file__).resolve().parent          # frontend/tools
FRONTEND = BASE.parent
ROOT = FRONTEND.parent
sys.path.insert(0, str(FRONTEND))
sys.path.insert(0, str(ROOT / "factcheck" / "src"))
sys.path.insert(0, str(ROOT / "pdfparse" / "src"))

PORT = 8899
os.environ["YJCHECK_BASE_URL"] = f"http://127.0.0.1:{PORT}/v1"
os.environ["YJCHECK_MODEL"] = "mock-chat"
os.environ["YJCHECK_API_KEY"] = "sk-test-secret"

from yjcheck.model import ModelConfig  # noqa: E402
from yjcheck.pipeline import run_check  # noqa: E402

from assistant import (TraceLog, ask_finding_question,  # noqa: E402
                       ask_report_question, rule_explanation)

DEMO = FRONTEND / "data" / "demo"
CHECK_OUT = FRONTEND / "data" / "check"


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", PORT), mock_chat_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    report = str(DEMO / "示例研报_2025H1.pdf")
    source = str(DEMO / "示例财报_2025H1.pdf")
    result, out_dir = run_check(report, [source], str(CHECK_OUT))
    confirmed = next(f for f in result["findings"] if f["status"] == "confirmed_error")

    # 1) C 的模型候选抽取链路（mock 返回空 facts，验证协议与留痕）
    model_result, _ = run_check(report, [source], str(CHECK_OUT),
                                model_config=ModelConfig.from_env())
    traces = model_result["model_traces"]
    assert traces and all(t["status"] == "ok" for t in traces), "模型抽取链路应返回 ok 留痕"
    print("MODEL EXTRACTION TRACES:", len(traces))

    config = ModelConfig.from_env()
    trace_log = TraceLog(out_dir)

    # 2) 单条追问（在线）
    answer = ask_finding_question(result, confirmed, "为什么判定为数值错误？依据在财报第几页？", config, trace_log)
    assert answer["mode"] == "model" and answer["answer"], "在线追问应返回模型解释"
    assert not answer["warnings"], "正常回答不应触发数值护栏"
    print("FINDING ANSWER:", answer["answer"][:80])

    # 3) 数值护栏：模型编造 999999 亿元 → 附加警告
    forged = ask_finding_question(result, confirmed, "编造数字", config, trace_log)
    assert forged["warnings"] and "999999" in forged["warnings"][0], "护栏应标出未验证数值"
    print("GUARD WARNING:", forged["warnings"][0][:80])

    # 4) 离线降级：规则解释卡（不调用模型）
    offline = ask_finding_question(result, confirmed, "为什么？", None, None)
    assert offline["mode"] == "offline" and "建议" in offline["answer"]
    print("OFFLINE CARD:", offline["answer"][:80])

    # 5) 报告问答（在线 + 命中）与离线摘要
    report_answer = ask_report_question(result, "这份报告有哪些问题？", config, trace_log)
    assert report_answer["mode"] == "model" and report_answer["hits"], "报告问答应带检索命中"
    print("REPORT HITS:", len(report_answer["hits"]), "ANSWER:", report_answer["answer"][:80])
    offline_report = ask_report_question(result, "有哪些问题", None, None)
    assert "已确认错误" in offline_report["answer"]

    # 6) 留痕去密钥
    trace_text = trace_log.path.read_text(encoding="utf-8")
    assert "sk-test-secret" not in trace_text and "Authorization" not in trace_text
    assert trace_text.count("finding_question") >= 2 and "report_question" in trace_text
    print("TRACE OK, lines:", len(trace_log.path.read_text(encoding="utf-8").splitlines()))

    server.shutdown()
    print("ALL OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())