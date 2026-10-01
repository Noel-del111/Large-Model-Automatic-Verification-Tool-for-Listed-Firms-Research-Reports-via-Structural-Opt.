# -*- coding: utf-8 -*-
"""本地 Mock Chat Completions 服务：彩排助手链路，无需真实模型密钥。

用法：python mock_chat_server.py --port 8898
合法请求为 OpenAI 兼容的 POST {base}/v1/chat/completions。
按请求内容分流：
- 用户消息含 '"blocks"'  → 事实抽取协议，返回 {"facts":[]}
- 用户消息含「编造」   → 故意编造数值 999999 亿元（验证护栏）
- 用户消息含「系统判定」 → 单条追问的解释（不含硬编码数值）
- 其他                 → 报告问答（不含硬编码数值）
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8", errors="replace")
        try:
            payload = json.loads(body)
            user_content = str(payload["messages"][-1]["content"])
        except (ValueError, KeyError, IndexError):
            self._reply({"error": "bad request"})
            return
        if '"blocks"' in user_content:
            content = json.dumps({"facts": []}, ensure_ascii=False)
        elif "编造" in user_content:
            content = "我建议把数值改成 999999 亿元，应为正确值。"
        elif "系统判定" in user_content:
            content = ("该发现是系统基于已核实证据给出的确定结论：研报数值与同一期间、"
                       "口径的财报数值不一致。修改建议以核查结果中的建议值为准，"
                       "请结合「证据对照」视图核验出处，最终以人工复核为准。")
        else:
            content = ("本次核查共给出已确认错误与待人工确认项，涉及数值与期间口径问题。"
                       "建议优先处理已确认错误，逐条在证据对照视图中核对出处后再确认修改。")
        self._reply({
            "choices": [{"message": {"role": "assistant", "content": content},
                         "index": 0, "finish_reason": "stop"}],
        })

    def _reply(self, data: dict) -> None:
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, fmt, *args):  # 静默默认访问日志
        pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8898)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"mock chat server on http://127.0.0.1:{args.port}/v1", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()