# -*- coding: utf-8 -*-
"""D 展示层：大模型纠错助手（L4）。

定位是核查结果的「证据翻译器」，不是新的裁判：
- 判定结论（status / error_type / suggested_value）由 C 的确定性规则产出；
- 助手只解释与汇总，回答中的数值只能用 finding 内的已核实数据；
- 未配置模型（YJCHECK_* 环境变量）时降级为规则解释卡，功能不缺失；
- 请求留痕 assistant_traces.jsonl，不记录密钥。

模型端点兼容 OpenAI Chat Completions（DeepSeek 可直接用 https://api.deepseek.com/v1）。
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from exports import ERROR_TYPE_LABELS, STATUS_LABELS
from yjcheck.model import ModelConfig
from yjcheck.model_runtime import BudgetedChatClient

# ---------------------------------------------------------------- 规则解释卡（离线降级）

RULE_CARDS: dict[str, str] = {
    "C.VALUE.001": "同条件数值核对：公司、期间、口径、币种一致时，研报数值与可靠来源在显示精度内不一致。",
    "C.UNIT.001": "单位倍数：研报沿用了来源的原数、但标注的金额/数量单位改变了倍数；或研报与来源量纲不同，不能仅凭数字判断。",
    "C.UNIT.002": "百分比与百分点含义不同，不能互换。",
    "C.PERIOD.001": "期间核对：文档冠名年度与可靠来源不一致。",
    "C.CONTEXT.001": "口径/期间错位：研报数值与另一期间或口径的数值吻合，与声明条件下的来源数值不一致。",
    "C.CONTEXT.002": "疑似错引（待确认）：数值匹配另一期间/口径，但缺少声明条件下的来源，不能确认错引。",
    "C.BASIS.001": "调整前后口径：可靠来源的调整前后数值完全相同，且与研报显示精度一致。",
    "C.MATCH.001": "无对应来源：未找到同一期间、指标和口径的可靠来源，不能把其他期间数值当作正确值。",
    "C.MATCH.002": "来源冲突：同一指标/期间/口径存在互相矛盾的来源，程序不选择有利候选。",
    "C.CURRENCY.001": "币种不一致：缺少明确汇率与换算日期，不能直接比较。",
    "C.EVIDENCE.001": "证据准入：待核查事实缺少可靠证据、明确口径或有效来源定位。",
    "C.CITATION.001": "引用核对：所标引用与已核实数值的来源位置不一致。",
    "C.YOY.001": "同比复算需要两期可靠金额；上年基数为零或负数时转人工。",
    "C.PE.001": "市盈率复算需要同一时点、同口径且为正的股价与每股收益。",
    "C.DERIVED.001": "派生指标复算：缺少可核验输入或输入存在冲突，无法复算。",
    "INPUT_IDENTITY_OR_COMPLETENESS": "输入质量：文件身份或完整性未通过，全部结论转人工复核。",
    "NO_CLAIMS": "覆盖范围：未提取到支持范围内的核查项，需检查输入或补充抽取规则。",
}

_SYSTEM = (
    "你是研报纠错助手的解释员。用户提供的核查结果与证据是已核实数据，你的职责是忠实解释，"
    "并遵守：1) 不得改变系统判定状态与错误类型；2) 不得编造核查结果中不存在的数值、页码或依据，"
    "建议值只能引用 finding 中的 suggested_value 或 calculation；3) 不得生成投资建议或超越证据的推断；"
    "4) 回答要引用证据位置（如「财报第 3 页」）；5) 不确定时明确回答「需人工复核」。用中文回答。"
)

_NUM_TOKEN = re.compile(r"\d[\d,，]*(?:\.\d+)?(?:亿元|万元|千元|百万元|万亿|亿|万|千|百|元|美元|港元|欧元|股|%|％|倍|点)?")
_SKIP_NUM = re.compile(r"^(?:19|20)\d{2}$|^\d{4}-\d{2}-\d{2}$")
_UNIT_SUFFIX = re.compile(r"(亿元|万元|千元|百万元|万亿|亿|万|千|百|元|美元|港元|欧元|股|%|％|倍|点)$")


class TraceLog:
    """助手请求留痕（JSONL），不含密钥。"""

    def __init__(self, run_dir: str | Path):
        self.path = Path(run_dir) / "assistant_traces.jsonl"

    def add(self, entry: dict[str, Any]) -> None:
        entry = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), **entry}
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _validate_base_url(config: ModelConfig) -> str:
    parsed = urlparse(config.base_url)
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        raise ValueError("模型地址必须为不含凭据的 HTTP(S) URL")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("远程模型端点必须使用 HTTPS")
    return config.base_url.rstrip("/") + "/chat/completions"


def _chat(config: ModelConfig, system: str, user: str, timeout: float = 60) -> str:
    return _chat_result(config, system, user, timeout)["content"]


def _chat_result(config: ModelConfig, system: str, user: str, timeout: float = 60) -> dict:
    from dataclasses import replace
    client = BudgetedChatClient(replace(config, timeout=timeout), opener=urllib.request.urlopen)
    return client([{"role": "system", "content": system}, {"role": "user", "content": user}], purpose="assistant-explanation-v2")


# ---------------------------------------------------------------- 定位与数值白名单

def _location_lines(facts: list[dict]) -> list[str]:
    lines = []
    for fact in facts:
        for evidence in fact.get("evidence", []):
            file_name = Path(str(evidence.get("file", ""))).name or evidence.get("doc_id", "")
            if evidence.get("page") is not None:
                lines.append(f"{file_name} 第{evidence['page']}页")
            elif evidence.get("paragraph") is not None:
                lines.append(f"{file_name} 第{evidence['paragraph']}段")
    return list(dict.fromkeys(lines))


def _allowed_numbers(finding: dict) -> set[str]:
    """回答中允许出现的数值集合：声明值、建议值、复算值、证据页码与期间。"""
    allowed: set[str] = set()

    def add(value: Any) -> None:
        if value is None:
            return
        allowed.add(str(value).replace(",", "").replace("，", ""))

    claim = finding.get("claim", {})
    add(claim.get("value"))
    add(claim.get("period"))
    add(finding.get("suggested_value"))
    # 显示精度（如 1.50 亿 → 0.01）是复算说明的一部分，模型复述不算编造。
    for value in (claim.get("value"), finding.get("suggested_value")):
        text = str(value or "")
        if re.fullmatch(r"-?\d+\.\d+", text):
            decimals = len(text.split(".", 1)[1])
            add("0." + "0" * (decimals - 1) + "1")
    calculation = finding.get("calculation", {})
    calc_claim = calculation.get("claim", {})
    add(calc_claim.get("value"))
    add(calc_claim.get("normalized_value"))
    for source in calculation.get("sources", []):
        add(source.get("value"))
        add(source.get("normalized_value"))
    for key in ("expected_in_claim_unit", "unit_scale_ratio"):
        add(calculation.get(key))
    for fact in [claim, *finding.get("evidence", [])]:
        add(fact.get("value"))
        add(fact.get("period"))
        for evidence in fact.get("evidence", []):
            add(evidence.get("page"))
    return {item for item in allowed if item}


def _sanitize_numbers(answer: str, allowed: set[str]) -> list[str]:
    """检查回答中出现的金融数值。页码、序号、规则号、日期小片段等非金额数字不告警。"""
    allowed_norm = {str(item).lstrip("+-") for item in allowed}
    offenders = []
    for match in _NUM_TOKEN.finditer(answer):
        token = match.group()
        number = _UNIT_SUFFIX.sub("", token).replace(",", "")
        if not number or _SKIP_NUM.fullmatch(number):
            continue
        has_unit = _UNIT_SUFFIX.search(token) is not None
        digits = re.sub(r"\D", "", number)
        if not has_unit and len(digits) < 4:
            continue
        if number.lstrip("+-") not in allowed_norm:
            offenders.append(token)
    return list(dict.fromkeys(offenders))


# ---------------------------------------------------------------- 两条问答路径

def rule_explanation(finding: dict) -> str:
    """离线规则解释卡：规则 → 结论 → 建议 → 证据位置。"""
    rule_id = finding.get("rule_id", "")
    claim = finding.get("claim", {})
    status = STATUS_LABELS.get(finding.get("status", ""), finding.get("status", ""))
    lines = [
        f"**{status} · {ERROR_TYPE_LABELS.get(finding.get('error_type', ''), finding.get('error_type', '')) or '—'}**"
        f"　规则 `{rule_id}`",
        "",
        RULE_CARDS.get(rule_id, "系统核查规则（详见计算过程）。"),
    ]
    if finding.get("message"):
        lines += ["", f"结论：{finding['message']}"]
    value = f"{claim.get('value', '')}{claim.get('unit', '')}".strip()
    lines += ["", f"- 研报声明：{claim.get('text', '')}（{claim.get('company', '')} "
              f"{claim.get('metric', '')} {value} {claim.get('period', '')}）"]
    if finding.get("suggestion"):
        suggest = finding["suggestion"]
        if finding.get("suggested_value") is not None:
            suggest += f"（建议值 {finding['suggested_value']}）"
        lines += [f"- 修改建议：{suggest}"]
    locations = _location_lines(finding.get("evidence", []))
    lines += ["", "- 依据位置：" + ("；".join(locations) if locations else "无定位证据")]
    lines += ["", "以上为离线规则解释卡；满足证据条件时请由人工复核确认。"]
    return "\n".join(lines)


def _ask(model_config: ModelConfig | None, system: str, user: str,
         trace: TraceLog | None, kind: str) -> dict[str, Any]:
    if model_config is None:
        return {"mode": "offline", "answer": "", "warnings": []}
    response = _chat_result(model_config, system, user)
    answer = response["content"]
    if trace:
        trace.add({"kind": kind, "model": model_config.model,
                   "question_chars": len(user), "answer": answer, "runtime": response["trace"]})
    return {"mode": "model", "answer": answer, "warnings": []}


def ask_finding_question(result: dict, finding: dict, question: str,
                         config: ModelConfig | None, trace: TraceLog | None) -> dict[str, Any]:
    """单条追问：以该 finding 的已核实数据为上下文。"""
    detection = config is not None
    if not detection:
        explanation = rule_explanation(finding)
        return {"mode": "offline", "answer": explanation, "warnings": []}
    claim = finding.get("claim", {})
    value = f"{claim.get('value', '')}{claim.get('unit', '')}".strip()
    evidence_lines = []
    for fact in finding.get("evidence", []):
        locations = "；".join(_location_lines([fact]))
        evidence_lines.append(
            f"- 依据：{fact.get('metric', '')} {fact.get('value', '')}{fact.get('unit', '')} "
            f"{fact.get('period', '')}（{fact.get('basis', '')}/{fact.get('scope', '')}）"
            + (f"，位置 {locations}" if locations else ""))
    context = {
        "系统判定": f"{STATUS_LABELS.get(finding.get('status', ''), '')}"
                    f" · {ERROR_TYPE_LABELS.get(finding.get('error_type', ''), '')}",
        "规则": f"{finding.get('rule_id', '')}：{RULE_CARDS.get(finding.get('rule_id', ''), '')}",
        "结论说明": finding.get("message", ""),
        "研报声明": {"公司": claim.get("company", ""), "指标": claim.get("metric", ""),
                     "数值": value, "期间": claim.get("period", ""),
                     "口径": f"{claim.get('basis', '')}/{claim.get('scope', '')}",
                     "原文": claim.get("text", "")},
        "已核实依据": evidence_lines,
        "计算过程": finding.get("calculation", {}),
        "修改建议": finding.get("suggestion", ""),
        "建议值": finding.get("suggested_value"),
        "用户问题": question,
    }
    user = json.dumps(context, ensure_ascii=False, indent=1)[:12000]
    answered = _ask(config, _SYSTEM, user, trace, "finding_question")
    allowed = _allowed_numbers(finding)
    offenders = _sanitize_numbers(answered["answer"], allowed)
    if offenders:
        answered["warnings"] = [
            "回答中出现未出现在核查结果中的数值（%s），已按未验证信息提示，请以核查结果与人工复核为准。" % "、".join(offenders)]
    return answered


def _tokenize(text: str) -> list[str]:
    text = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", " ", text).lower()
    tokens = re.findall(r"[a-z0-9]+", text)
    for index in range(len(text) - 1):
        if "\u4e00" <= text[index] <= "\u9fff" and "\u4e00" <= text[index + 1] <= "\u9fff":
            tokens.append(text[index:index + 2])
    return tokens


def _retrieve(result: dict, query: str, top: int = 6) -> list[dict]:
    docs = []
    for finding in result.get("findings", []):
        claim = finding.get("claim", {})
        locations = "；".join(_location_lines(finding.get("evidence", [])))
        docs.append({
            "type": "finding",
            "text": " ".join([str(claim.get(key, "")) for key in
                              ("company", "metric", "value", "unit", "period", "basis", "scope")]
                             + [claim.get("text", ""), finding.get("message", ""),
                                finding.get("suggestion", "")]),
            "payload": finding, "locations": locations,
        })
    query_tokens = set(_tokenize(query))
    scored = []
    for doc in docs:
        tokens = _tokenize(doc["text"])
        if not tokens:
            continue
        overlap = len([t for t in tokens if t in query_tokens])
        score = overlap / (len(query_tokens or [""]) * 0.5 + 1)
        if overlap:
            scored.append((score, doc))
    scored.sort(key=lambda item: item[0], reverse=True)
    if not scored:
        # 无关键词重叠时按严重程度兜底：确认错误 → 待确认 → 无问题。
        order = {"confirmed_error": 0, "needs_review": 1, "no_issue": 2}
        ranked = sorted(docs, key=lambda d: order.get(d["payload"].get("status", ""), 3))
        return ranked[:top]
    return [doc for _, doc in scored[:top]]


def ask_report_question(result: dict, question: str,
                        config: ModelConfig | None, trace: TraceLog | None) -> dict[str, Any]:
    """报告问答：先检索最相关的发现作为上下文，再作答。命中列表一并返回。"""
    hits = _retrieve(result, question)
    summary = result.get("summary", {})
    finding = {"claim": {}, "suggested_value": None, "calculation": {}}
    allowed = set(str(summary.get(key, 0)) for key in
                  ("confirmed_error", "needs_review", "no_issue", "claims", "source_facts"))
    for hit in hits:
        allowed |= _allowed_numbers(hit["payload"])
    if config is None:
        lines = [f"**离线摘要（未配置模型）**：已确认错误 {summary.get('confirmed_error', 0)}，"
                 f"待人工确认 {summary.get('needs_review', 0)}，未发现问题 {summary.get('no_issue', 0)}。",
                 "", "相关发现："]
        for hit in hits:
            payload = hit["payload"]
            claim = payload.get("claim", {})
            lines.append(f"- {STATUS_LABELS.get(payload.get('status', ''), '')}"
                         f"｜{claim.get('company', '')} {claim.get('metric', '')} "
                         f"{claim.get('value', '')}{claim.get('unit', '')}"
                         f"　{payload.get('suggestion', '')[:60]}")
        return {"mode": "offline", "answer": "\n".join(lines), "warnings": [], "hits": hits}
    hit_lines = []
    for hit in hits:
        payload = hit["payload"]
        claim = payload.get("claim", {})
        hit_lines.append(
            f"- [{STATUS_LABELS.get(payload.get('status', ''), '')}] "
            f"{claim.get('company', '')} {claim.get('metric', '')} "
            f"{claim.get('value', '')}{claim.get('unit', '')} {claim.get('period', '')}："
            f"{payload.get('message', '')[:80]}　建议：{payload.get('suggestion', '')[:60]}"
            + (f"　{hit['locations']}" if hit.get("locations") else ""))
    context = (
        f"本次核查摘要：已确认错误 {summary.get('confirmed_error', 0)}，"
        f"待人工确认 {summary.get('needs_review', 0)}，未发现问题 {summary.get('no_issue', 0)}，"
        f"提取声明 {summary.get('claims', 0)}。\n"
        f"与问题最相关的发现：\n" + "\n".join(hit_lines) + "\n"
        f"用户问题：{question}"
    )
    answered = _ask(config, _SYSTEM, context, trace, "report_question")
    offenders = _sanitize_numbers(answered["answer"], allowed)
    if offenders:
        answered["warnings"] = [
            "回答中出现未出现在核查结果中的数值（%s），请以核查结果与人工复核为准。" % "、".join(offenders)]
    answered["hits"] = hits
    return answered
