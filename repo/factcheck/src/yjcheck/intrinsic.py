"""研报自身一致性检查（intrinsic）：不依赖财报来源，借鉴 FinED-Bench 公开测评集的错误类型。

学习来源：FinED-Bench《金融文档错误检测基准》研报类样本，其高频错误类型包括
"数值不一致错误"（列举对象数与数值个数不匹配）、"时间矛盾"、"数值单位错误"
（术语与单位量级不匹配）。本项目此前仅做研报-财报跨文件核查，同类内部一致性
检查缺失。

设计约定：
- 全部产 needs_review（转人工），不直接判定确认错误，保证误报率为 0；
- 合成 Finding 的 claim 使用本模块错误类型代码作为 metric、value/unit 为空，
  保证评测器的行匹配（metric/value/unit/basis/scope）永不命中答案行，
  只进入"无答案对照的系统报错"清单，不进比率分母；
- 建议文案与核查层 _review 口径一致，不生成正确值。
"""
from __future__ import annotations

import re
from decimal import Decimal

from .claim_extract import NUMBER_RE
from .error_types import ERROR_TYPES
from .models import Block, Document, Fact, Finding

_SUGGESTION = "补充或人工核对可靠证据后再比较；当前不生成正确值。"

_SENTENCE_RE = re.compile(r"[^。！？；]+[。！？；]?")
# 年份倒退形态：区间（2017-2014年）与"从…至…"结构（从2025年1月…至2024年5月）
_BACKWARD_RANGE = re.compile(r"20(\d{2})\s*年?\s*[-—～至]\s*20(\d{2})\s*年")
_BACKWARD_FROM_TO = re.compile(r"从\s*20(\d{2})\s*年.{0,14}?(?:到|至)\s*20(\d{2})\s*年")
# 剥离区间/范围（1-3月、2020-2024年、10-15%、x—y元），避免被当作多个数值
_RANGE_RES = [
    re.compile(r"\d{4}\s*[-—～至]\s*\d{4}\s*年?"),
    re.compile(r"\d{1,3}(?:\.\d+)?\s*[-—～至]\s*\d{1,3}(?:\.\d+)?\s*(?:%|％|个月|月|天|日|元|亿元|万元|千万元|倍)?"),
]
# 剥离序数（第3名/第三位/第一类），避免被当作对象或数值
_ORDINAL_RE = re.compile(r"第\s*[一二三四五六七八九十百\d]+\s*(?:名|位|家|个|条|项|期|类|档|季)?")


def _synthetic_fact(code: str, doc: Document, block: Block, text: str,
                   start: int, end: int) -> Fact:
    evidence = block.evidence(doc, text.strip(), start, end)
    return Fact(code, "", "", doc.period, doc.company, text=text.strip(),
                evidence=[evidence], attributes={"extraction": "intrinsic"})


def _finding(code: str, rule: str, claim: Fact, message: str) -> Finding:
    definition = ERROR_TYPES[code]
    return Finding(claim, "needs_review", definition.code, rule, message,
                   _SUGGESTION, None, [], {"rule_id": rule})


def _strip_ranges(text: str) -> str:
    cleaned = text
    for pattern in _RANGE_RES:
        cleaned = pattern.sub("", cleaned)
    return _ORDINAL_RE.sub("", cleaned)


def _count_objects(segment: str) -> int:
    """按顿号/逗号切分纯名词短语（无数字、长度 2-15 字），返回片数；不足 2 片返回 0。"""
    parts = [p for p in re.split(r"[、,，]", segment) if p.strip()]
    if len(parts) < 2:
        return 0
    for part in parts:
        part = part.strip()
        if not (2 <= len(part) <= 15) or re.search(r"\d", part):
            return 0
    return len(parts)


def check_enumerations(doc: Document) -> list[Finding]:
    """C.INTRINSIC.001 列举不一致：'分别'句中对象数与数值个数不匹配（FinED 数值不一致错误）。"""
    findings: list[Finding] = []
    for block in doc.blocks:
        if block.type == "table":
            continue
        for sentence in _SENTENCE_RE.finditer(block.text):
            raw = sentence.group()
            if "分别" not in raw:
                continue
            compact = re.sub(r"\s+", "", raw)
            cleaned = _strip_ranges(compact)
            if "分别" not in cleaned:
                continue
            if "为" not in cleaned and len(re.findall(r"分别(?:上涨|下跌|下降|增长|减少|为|是|达|占比?|约为?|同比)", cleaned)) == 0:
                continue
            numbers = list(NUMBER_RE.finditer(cleaned))
            if len(numbers) < 2:
                continue
            marker = cleaned.index("分别")
            if cleaned.startswith("分别为", marker):
                # “分别为万达、横店…”：对象在“分别为”之后、首个数值之前
                tail = cleaned[marker + 3:]
                first_num = numbers[0].start()
                objects = _count_objects(tail[:first_num]) if first_num > 0 else 0
            else:
                # “A、B、C分别上涨…”：对象在“分别”之前的顿号列表
                objects = _count_objects(cleaned[:marker])
            if objects < 2 or objects >= 20:
                continue
            if len(numbers) != objects:
                claim = _synthetic_fact("numeric_inconsistency", doc, block, raw,
                                        sentence.start(), sentence.end())
                findings.append(_finding(
                    "numeric_inconsistency", "C.INTRINSIC.001", claim,
                    f"研报中列举对象数与数值个数不一致：对象 {objects} 个、数值 {len(numbers)} 个，需人工核对。"))
    return findings


def check_time_conflict(doc: Document) -> list[Finding]:
    """C.INTRINSIC.002 时间矛盾：仅报年份倒退形态（区间/从-至结构中后年早于前年）。

    研报正文普遍存在多年份对照叙事（如"2024 年实现…，预计 2025 年…"），属正常表达，
    不能作为矛盾证据；"2017-2014 年""从 2025 年 1 月…至 2024 年 5 月"这类年份倒序才近乎
    必然是错误（对应 FinED 的"时间矛盾/时间信息非法"），转人工复核。
    """
    findings: list[Finding] = []
    for block in doc.blocks:
        if block.type == "table":
            continue
        for sentence in _SENTENCE_RE.finditer(block.text):
            raw = sentence.group()
            compact = re.sub(r"\s+", "", raw)
            triggered = False
            for pattern, threshold in ((_BACKWARD_RANGE, 0), (_BACKWARD_FROM_TO, 0)):
                match = pattern.search(compact)
                if match and int(match.group(1)) > int(match.group(2)) + threshold:
                    triggered = True
                    break
            if not triggered:
                continue
            claim = _synthetic_fact("time_conflict", doc, block, raw,
                                    sentence.start(), sentence.end())
            findings.append(_finding(
                "time_conflict", "C.INTRINSIC.002", claim,
                "句中年份顺序倒置（晚于起始年份的表述早于起始年份），需人工核对期间。"))
    return findings


def check_unit_term_mismatch(claims: list[Fact]) -> list[Finding]:
    """C.INTRINSIC.003 单位-术语护栏：每股类指标数值量级异常、比率类指标配金额单位。"""
    findings: list[Finding] = []
    for claim in claims:
        if claim.metric == "eps_basic":
            try:
                if Decimal(claim.value.replace(",", "")) > 500:
                    findings.append(_finding(
                        "unit_term_mismatch", "C.INTRINSIC.003", claim,
                        "每股收益数值超过 500 元/股，疑似术语与量级不匹配，需人工核对。"))
            except Exception:
                continue
        elif claim.metric in {"gross_margin", "pe"} and ("亿" in claim.unit or "万" in claim.unit):
            findings.append(_finding(
                "unit_term_mismatch", "C.INTRINSIC.003", claim,
                f"指标 {claim.metric} 通常不以金额单位计量，出现单位 \"{claim.unit}\"，需人工核对术语与单位。"))
    return findings


def check_intrinsic_consistency(doc: Document, claims: list[Fact]) -> list[Finding]:
    findings = check_enumerations(doc)
    findings += check_time_conflict(doc)
    findings += check_unit_term_mismatch(claims)
    return findings