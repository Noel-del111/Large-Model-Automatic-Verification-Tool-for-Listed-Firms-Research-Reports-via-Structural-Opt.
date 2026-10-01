"""本地 Mock 模型服务：在没有密钥或没有网络时彩排整条链路。

提供 OpenAI 兼容的三个接口：
  GET  /v1/models             列出模型
  POST /v1/embeddings         返回固定维度的确定性向量
  POST /v1/chat/completions   按规则返回质检结论（可指定命中哪一类问题）

用法：
    py -3 tools/mock_model_server.py --port 8899
    py -3 tools/mock_model_server.py --port 8899 --verdict mismatch   # 全部判为不一致

配合命令：
    run.cmd model-check --base-url http://127.0.0.1:8899/v1 \
        --embedding-model mock-embed --chat-model mock-chat --vision-model mock-vl
    run.cmd parse --input data/real --out data/out_demo --vlm always \
        --vlm-base-url http://127.0.0.1:8899/v1 --vlm-model mock-vl --vlm-max-pages 3

注意：它只用于彩排与演示，不做真实推理，不要用于任何效果评估。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    verdict_mode = "match"
    embedding_dim = 1024

    def log_message(self, fmt, *args):
        print("  [mock] " + (fmt % args))

    def _send(self, payload, code=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _embedding(self, text: str):
        seed = int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16)
        rng = random.Random(seed)
        return [round(rng.uniform(-1, 1), 6) for _ in range(type(self).embedding_dim)]

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            self._send({"object": "list", "data": [
                {"id": "mock-chat", "object": "model"},
                {"id": "mock-vl", "object": "model"},
                {"id": "mock-embed", "object": "model"},
            ]})
        else:
            self._send({"error": {"message": "not found"}}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        path = self.path.rstrip("/")
        if path.endswith("/embeddings"):
            inputs = payload.get("input") or []
            if isinstance(inputs, str):
                inputs = [inputs]
            self._send({"object": "list", "data": [
                {"object": "embedding", "index": i, "embedding": self._embedding(str(text))}
                for i, text in enumerate(inputs)
            ], "model": payload.get("model", "mock-embed")})
            return
        if path.endswith("/chat/completions"):
            mode = type(self).verdict_mode
            if mode == "mismatch":
                verdict = {"match": False, "confidence": 0.86, "issues": [
                    {"type": "missing_text", "detail": "示例：疑似漏掉一段风险提示"}]}
                content = json.dumps(verdict, ensure_ascii=False)
            elif mode == "noise":
                content = '模型回答：```json\n{"match": true, "issues": [], "confidence": 0.71}\n```'
            else:
                content = json.dumps(
                    {"match": True, "issues": [], "confidence": 0.9}, ensure_ascii=False)
            self._send({"choices": [{"index": 0, "message": {"role": "assistant",
                                                             "content": content}}],
                        "model": payload.get("model", "mock-chat")})
            return
        self._send({"error": {"message": "not found"}}, 404)


def main() -> int:
    parser = argparse.ArgumentParser(description="本地 Mock 模型服务（OpenAI 兼容）")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8899)
    parser.add_argument("--verdict", default="match", choices=["match", "mismatch", "noise"],
                        help="质检结论的返回模式")
    parser.add_argument("--embedding-dim", type=int, default=1024)
    args = parser.parse_args()

    Handler.verdict_mode = args.verdict
    Handler.embedding_dim = args.embedding_dim
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Mock 模型服务已启动：http://{args.host}:{args.port}/v1  结论模式={args.verdict}")
    print("按 Ctrl+C 停止。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
