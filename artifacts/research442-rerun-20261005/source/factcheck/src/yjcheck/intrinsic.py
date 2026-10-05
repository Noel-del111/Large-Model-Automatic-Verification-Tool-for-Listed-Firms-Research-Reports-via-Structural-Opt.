"""研报自身一致性检查（intrinsic）：不依赖财报来源，借鉴 FinED-Bench 公开测评集的错误类型。

学习来源：FinED-Bench《金融文档错误检测基准》研报类样本，其高频错误类型包括
"数值不一致错误"（列举对象数与数值个数不匹配）、"时间矛盾"、"数值单位错误"
（术语与单位量级不匹配）。本项目此前仅做研报-财报跨文件核查，同类内部一致性
检查缺失。

设计约定：候选进入 needs_review，不能据此宣称零误报。文本一致性候选与
财务事实比较分别计数；评测须把候选命中、误报及新增人工负担纳入报告。
合成 Fact 保留错误类型、原文和位置，不虚构金额或自动更正值。
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
    # Introductory time/context clauses are not members of an object list.
    segment = re.sub(r"^(?:报告期(?:内|各期末|各期)?|本报告期内|其中|截至[^，,]+|当期)[，,]", "", segment)
    parts = [p for p in re.split(r"[、,，/／]", segment) if p.strip()]
    if len(parts) < 2:
        return 0
    # A prose comma alone also separates time/context from a single subject.
    # Accept it as a list separator only with explicit company/entity endings.
    if not re.search(r"[、/／]", segment) and not all(
            re.search(r"(?:公司|集团|银行|基金|证券|行业|业务|产品|项目|地区|国有行|农商行|城商行)$", p.strip())
            for p in parts):
        return 0
    for part in parts:
        part = part.strip()
        if (not (2 <= len(part) <= 15) or re.search(r"\d", part)
                or re.search(r"报告期|各期末|各期|上述|构成|主要由|包括|其中|占比|比例|截至|为主|实现|近年来|近年|上半年|下半年|本期|当期|上年|去年|今年|本季度|前三季度", part)):
            return 0
    return len(parts)


def _flat_value_count(tail: str) -> int | None:
    """Count a fully understood flat list, otherwise abstain.

    Never compare a prefix of a list containing ranges, per-item qualifiers,
    parentheses or unsupported compound units. A named subsequent comparison
    clause can close the first list; its numbers belong to a separate series.
    """
    if any(pattern.search(tail) for pattern in _RANGE_RES):
        return None
    numbers = list(NUMBER_RE.finditer(tail))
    if not numbers:
        return None
    prefix = tail[:numbers[0].start()]
    if not re.fullmatch(r"(?:(?:约为|上涨|下跌|下降|增长|减少|占比|同比|为|是|达|占|约))*", prefix):
        return None

    def next_clause(text):
        return bool(re.match(r"^[，,](?:同比|环比|较(?:上|去|前)|增速|增长率|降幅|变动|变化|对应|其中)", text))

    count, last = 1, numbers[0]
    for number in numbers[1:]:
        gap = tail[last.end():number.start()]
        if re.fullmatch(r"[、,，/／和及与]+", gap):
            count += 1
            last = number
        elif next_clause(tail[last.end():]):
            return count
        else:
            return None
    suffix = tail[last.end():]
    return count if re.fullmatch(r"[。！？；]?", suffix) or next_clause(suffix) else None


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
            # Removing a range would turn a valid three-item list into an
            # apparent two-item list. Keep values intact and abstain below.
            cleaned = _ORDINAL_RE.sub("", compact)
            if "分别" not in cleaned:
                continue
            if "为" not in cleaned and len(re.findall(r"分别(?:上涨|下跌|下降|增长|减少|为|是|达|占比?|约为?|同比)", cleaned)) == 0:
                continue
            marker = cleaned.index("分别")
            # Both “A、B分别为...” and “A、B分别上涨...” put their
            # compared objects before 分别. Restrict amounts to its tail so
            # earlier context values cannot inflate the enumeration count.
            objects = _count_objects(cleaned[:marker])
            if objects < 2 or objects >= 20:
                continue
            value_count = _flat_value_count(cleaned[marker+len("分别"):])
            if value_count is None:
                continue
            if value_count != objects:
                claim = _synthetic_fact("numeric_inconsistency", doc, block, raw,
                                        sentence.start(), sentence.end())
                findings.append(_finding(
                    "numeric_inconsistency", "C.INTRINSIC.001", claim,
                    f"研报中列举对象数与数值个数不一致：对象 {objects} 个、数值 {value_count} 个，需人工核对。"))
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


def check_fact_consistency(claims: list[Fact]) -> list[Finding]:
    """Compare reliable same-context prose/table facts without choosing a truth.

    Matching requires the same registered metric, company, period, scope,
    currency and restatement basis. Equivalent units and display rounding are
    respected. Conflicts retain both locations for subsequent verification.
    """
    from collections import defaultdict
    from .metric_catalog import CATALOG, normalize_metric
    from .rules import _equal, _issues, _unit

    groups = defaultdict(list)
    for claim in claims:
        metric = normalize_metric(claim.metric)
        if metric not in CATALOG or _issues(claim):
            continue
        if claim.attributes.get("extraction", "").startswith("model"):
            continue
        key = (claim.company, metric, claim.period, claim.scope, claim.basis, claim.currency)
        groups[key].append(claim)
    findings = []
    for candidates in groups.values():
        conflicts = []
        for index, left in enumerate(candidates):
            for right in candidates[index + 1:]:
                if left.fact_id == right.fact_id or _unit(left.unit)[0] != _unit(right.unit)[0]:
                    continue
                if not (_equal(left, right) or _equal(right, left)):
                    for fact in (left, right):
                        if fact not in conflicts:
                            conflicts.append(fact)
        if conflicts:
            finding = _finding("numeric_inconsistency", "C.INTRINSIC.004", conflicts[0],
                               "同一指标、期间和口径在正文或表格中出现不同数值，需结合来源核实。")
            finding.evidence = conflicts
            finding.calculation.update({"candidate_fact_ids": [fact.fact_id for fact in conflicts],
                                        "verification": "same_context_unit_normalized_conflict"})
            findings.append(finding)
    return findings


def check_intrinsic_consistency(doc: Document, claims: list[Fact]) -> list[Finding]:
    findings = check_enumerations(doc)
    findings += check_time_conflict(doc)
    findings += check_unit_term_mismatch(claims)
    findings += check_fact_consistency(claims)
    return findings
