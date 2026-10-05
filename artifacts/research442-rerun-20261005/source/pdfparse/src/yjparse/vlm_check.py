"""第三层质检：用视觉模型抽检页面，核对解析文本与页面图像是否一致。

前两层（确定性指标、双引擎交叉校验）只能发现“两套引擎不一致”或“明显异常”，
无法发现两套引擎同时抽错的情况；这一层直接看图，能抓到漏段、数字错位、图表标注混入正文。

只依赖标准库发 HTTP 请求，兼容任何 OpenAI 兼容端点（云端服务或本机 Ollama / vLLM）。
"""

from __future__ import annotations

import base64
import io
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

PROMPT = (
    "你是研报解析质检员。下面是一页研报的页面图像，以及我们自动解析出的文本。\n"
    "请判断解析文本是否忠实反映了页面内容，重点检查三件事：\n"
    "1. 是否漏掉了页面上的正文段落；\n"
    "2. 表格中的关键数字、单位、年份是否与图像一致；\n"
    "3. 是否把图表内部的坐标轴、图例、数据标签误当成了正文。\n"
    "只输出 JSON，不要任何解释文字，格式为：\n"
    '{"match": true 或 false, "issues": [{"type": "missing_text|wrong_number|'
    'table_mismatch|chart_label_noise|other", "detail": "具体说明"}], "confidence": 0 到 1 的小数}'
)


class VlmUnavailable(RuntimeError):
    """未配置可用的视觉模型端点。"""


@dataclass
class VlmConfig:
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    timeout: float = 90.0
    max_pages: int = 20
    sample_ratio: float = 0.1
    min_confidence: float = 0.5
    max_image_side: int = 1400
    extra_headers: Dict[str, str] = field(default_factory=dict)

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.model)

    @classmethod
    def from_env(cls, environ: Optional[Dict[str, str]] = None) -> "VlmConfig":
        import os

        env = environ if environ is not None else os.environ
        return cls(
            base_url=env.get("YJPARSE_VLM_BASE_URL", "").rstrip("/"),
            api_key=env.get("YJPARSE_VLM_API_KEY", ""),
            model=env.get("YJPARSE_VLM_MODEL", ""),
            timeout=float(env.get("YJPARSE_VLM_TIMEOUT", "90")),
        )


@dataclass
class VlmVerdict:
    status: str                 # match / mismatch / error
    confidence: float = 0.0
    issues: List[Dict[str, str]] = field(default_factory=list)
    raw: str = ""
    duration_s: float = 0.0


def encode_page_image(pdf_path, page_no: int, max_side: int = 1400,
                      dpi: int = 140) -> str:
    """把一页渲染成 PNG 的 base64，长边超过 max_side 时等比缩小以控制成本。"""
    from PIL import Image

    from .ocr import render_page_image

    image, _origin, _scale = render_page_image(pdf_path, page_no, dpi=dpi)
    if max(image.size) > max_side:
        ratio = max_side / max(image.size)
        image = image.resize((max(1, int(image.width * ratio)),
                              max(1, int(image.height * ratio))))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def build_payload(config: VlmConfig, image_b64: str, parsed_text: str) -> Dict[str, Any]:
    text = parsed_text[:6000]
    return {
        "model": config.model,
        "temperature": 0.0,
        "max_tokens": 800,
        "messages": [
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": f"解析文本如下：\n{text}"},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
            ]},
        ],
    }


def parse_verdict(content: str, min_confidence: float = 0.5) -> VlmVerdict:
    """从模型输出里取出 JSON；容错处理代码块包裹与多余文字。"""
    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return VlmVerdict(status="error", raw=content[:400])
    try:
        payload = json.loads(match.group(0))
    except Exception:
        return VlmVerdict(status="error", raw=content[:400])
    confidence = float(payload.get("confidence", 0.0) or 0.0)
    issues = payload.get("issues") or []
    if not isinstance(issues, list):
        issues = []
    is_match = bool(payload.get("match", True))
    if is_match or confidence < min_confidence:
        status = "match"
    else:
        status = "mismatch"
    return VlmVerdict(status=status, confidence=round(confidence, 3),
                      issues=[{"type": str(i.get("type", "other")),
                               "detail": str(i.get("detail", ""))[:200]}
                              for i in issues if isinstance(i, dict)],
                      raw=content[:400])


def call_vlm(config: VlmConfig, payload: Dict[str, Any]) -> str:
    """调用 OpenAI 兼容的 chat/completions 端点，返回模型文本。"""
    if not config.configured:
        raise VlmUnavailable("未配置视觉模型端点，请设置 YJPARSE_VLM_BASE_URL / MODEL / API_KEY")
    url = config.base_url
    if not url.endswith("/chat/completions"):
        url = f"{url}/chat/completions"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json", **config.extra_headers}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=config.timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    choices = data.get("choices") or []
    if not choices:
        raise VlmUnavailable(f"端点未返回结果：{str(data)[:200]}")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, list):   # 部分服务返回分段内容
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return str(content or "")


def check_page(pdf_path, page_no: int, parsed_text: str, config: VlmConfig) -> VlmVerdict:
    started = time.time()
    try:
        image_b64 = encode_page_image(pdf_path, page_no, max_side=config.max_image_side)
        content = call_vlm(config, build_payload(config, image_b64, parsed_text))
    except VlmUnavailable:
        raise
    except Exception as exc:  # 网络或服务异常：记为 error，不影响主流程
        return VlmVerdict(status="error", raw=str(exc)[:300],
                          duration_s=round(time.time() - started, 3))
    verdict = parse_verdict(content, min_confidence=config.min_confidence)
    verdict.duration_s = round(time.time() - started, 3)
    return verdict


def select_pages(pages: Sequence[Any], mode: str, ratio: float,
                 max_pages: int) -> List[int]:
    """确定抽检页面，规则固定以保证可复现。

    auto：优先抽问题页（warn / fail），再按固定步长补齐抽样比例；
    always：按固定步长覆盖全篇，并额外纳入问题页。
    """
    mode = (mode or "off").lower()
    if mode == "off" or not pages:
        return []
    ordered = sorted(pages, key=lambda p: p.page)
    suspicious = [p.page for p in ordered if p.status in {"warn", "fail"}]
    if mode == "auto":
        selected = suspicious
    else:
        step = max(int(round(1.0 / ratio)), 1) if ratio > 0 else 1
        selected = [p.page for index, p in enumerate(ordered) if index % step == 0]
        selected.extend(suspicious)
    unique: List[int] = []
    for page_no in selected:
        if page_no not in unique:
            unique.append(page_no)
    return unique[: max_pages] if max_pages > 0 else unique
