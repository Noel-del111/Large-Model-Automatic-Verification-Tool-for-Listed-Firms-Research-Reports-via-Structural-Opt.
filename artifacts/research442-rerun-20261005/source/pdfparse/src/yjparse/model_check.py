"""模型端点体检：提交前确认 Embedding / 对话 / 视觉三类端点可用。

现场换环境最容易出问题的不是代码而是模型端点，这里把三件事一次查清：
  1. /models 能否列出模型（部分服务不实现，属于可选项）；
  2. /embeddings 能否返回向量与维度（OpenViking 与检索要用）；
  3. /chat/completions 能否对话，以及能否看图（第三层质检要用）。
"""

from __future__ import annotations

import base64
import io
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ProbeResult:
    name: str
    ok: bool
    detail: str = ""
    duration_s: float = 0.0


def _post(url: str, payload: Dict[str, Any], api_key: str = "",
          timeout: float = 30.0) -> Dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                     headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _get(url: str, api_key: str = "", timeout: float = 15.0) -> Dict[str, Any]:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _tiny_png() -> str:
    from PIL import Image

    image = Image.new("RGB", (64, 64), (255, 255, 255))
    image.putpixel((32, 32), (0, 0, 0))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def probe(base_url: str, api_key: str = "", chat_model: str = "",
          embedding_model: str = "", vision_model: str = "",
          timeout: float = 30.0) -> List[ProbeResult]:
    base = base_url.rstrip("/")
    results: List[ProbeResult] = []

    started = time.time()
    try:
        data = _get(f"{base}/models", api_key, timeout)
        names = [item.get("id", "") for item in data.get("data", [])][:6]
        results.append(ProbeResult("模型列表", True, ", ".join(names) or "端点未返回模型名",
                                   round(time.time() - started, 2)))
    except Exception as exc:
        results.append(ProbeResult("模型列表", False, str(exc)[:160],
                                   round(time.time() - started, 2)))

    if embedding_model:
        started = time.time()
        try:
            data = _post(f"{base}/embeddings",
                         {"model": embedding_model, "input": ["测试文本"]}, api_key, timeout)
            vector = (data.get("data") or [{}])[0].get("embedding") or []
            results.append(ProbeResult("Embedding", bool(vector),
                                       f"{embedding_model} 维度 {len(vector)}",
                                       round(time.time() - started, 2)))
        except Exception as exc:
            results.append(ProbeResult("Embedding", False, str(exc)[:160],
                                       round(time.time() - started, 2)))

    if chat_model:
        started = time.time()
        try:
            data = _post(f"{base}/chat/completions",
                         {"model": chat_model, "max_tokens": 16, "temperature": 0.0,
                          "messages": [{"role": "user", "content": "回答两个字：正常"}]},
                         api_key, timeout)
            content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")
            results.append(ProbeResult("对话", bool(content),
                                       f"{chat_model} 返回 {str(content)[:20]}",
                                       round(time.time() - started, 2)))
        except Exception as exc:
            results.append(ProbeResult("对话", False, str(exc)[:160],
                                       round(time.time() - started, 2)))

    if vision_model:
        started = time.time()
        try:
            data = _post(f"{base}/chat/completions", {
                "model": vision_model, "max_tokens": 32, "temperature": 0.0,
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": "只回答一个词：看到图片了吗"},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/png;base64,{_tiny_png()}"}},
                ]}],
            }, api_key, timeout)
            content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")
            results.append(ProbeResult("视觉", bool(content),
                                       f"{vision_model} 返回 {str(content)[:20]}",
                                       round(time.time() - started, 2)))
        except Exception as exc:
            results.append(ProbeResult("视觉", False, str(exc)[:160],
                                       round(time.time() - started, 2)))
    return results


def render(results: List[ProbeResult]) -> str:
    lines = ["模型端点体检", ""]
    for item in results:
        flag = "通过" if item.ok else "失败"
        lines.append(f"  {item.name:<12}{flag:<6}{item.duration_s:>6.2f}s  {item.detail}")
    failed = [r for r in results if not r.ok]
    lines.append("")
    lines.append(f"共 {len(results)} 项，通过 {len(results) - len(failed)} 项，失败 {len(failed)} 项")
    if failed:
        lines.append("失败项请检查：端点地址是否正确、模型名是否已开通、密钥是否有效、网络是否可达")
    return "\n".join(lines)
