"""Deterministic, conservative financial checks over evidence-backed facts.

``normalize`` returns base units (yuan, fraction, shares, or a dimensionless
number). ``check_facts`` returns one auditable result per claim and never edits
the submitted text. Extraction and company alias resolution belong upstream.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
import re
from typing import Any

from .models import Evidence, Fact, Finding


_UNITS = {
    "元": ("money", "1"), "千元": ("money", "1000"),
    "万元": ("money", "10000"), "百万元": ("money", "1000000"),
    "千万元": ("money", "10000000"), "亿元": ("money", "100000000"),
    "万亿元": ("money", "1000000000000"),
    "%": ("percent", "0.01"), "百分比": ("percent", "0.01"),
    "百分点": ("percentage_point", "0.01"), "个百分点": ("percentage_point", "0.01"),
    "基点": ("percentage_point", "0.0001"),
    "倍": ("multiple", "1"), "元/股": ("per_share", "1"),
    "元每股": ("per_share", "1"), "股": ("shares", "1"),
    "万股": ("shares", "10000"), "亿股": ("shares", "100000000"),
    "年": ("year", "1"), "1": ("number", "1"),
}
from .metric_catalog import METRICS as _METRIC_ALIASES, normalize_metric

_KNOWN_BASES = {"before", "after", "change", "reported"}
_KNOWN_SCOPES = {"consolidated", "parent"}
_UNKNOWN = {"", "unknown", "none", "null", "未知", "不明", "待定", "n/a"}


def _unit(unit: str) -> tuple[str, Decimal, str]:
    cleaned = str(unit).strip().replace(" ", "").replace("％", "%")
    cleaned = cleaned.replace("／", "/")
    for prefix in ("人民币", "RMB", "CNY"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):]
            break
    if cleaned not in _UNITS:
        raise ValueError(f"不支持或缺少单位：{unit!r}")
    family, scale = _UNITS[cleaned]
    return family, Decimal(scale), cleaned


def _number(value: str, unit: str) -> Decimal:
    raw = str(value).strip().replace("−", "-").replace("﹣", "-").replace("－", "-")
    raw = raw.replace("（", "(").replace("）", ")").replace("，", ",")
    raw = raw.replace("％", "%").replace("／", "/")
    _, _, clean_unit = _unit(unit)
    if raw.endswith(clean_unit) and clean_unit != "1":
        raw = raw[:-len(clean_unit)].strip()
    if raw.startswith("(") and raw.endswith(")"):
        raw = "-" + raw[1:-1].strip()
    if "," in raw:
        if not re.fullmatch(r"[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?", raw):
            raise ValueError(f"数字千位分隔无效：{value!r}")
        raw = raw.replace(",", "")
    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", raw):
        raise ValueError(f"无法无歧义读取数字：{value!r}")
    try:
        result = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"无效数字：{value!r}") from exc
    if not result.is_finite() or abs(result.adjusted()) > 1000:
        raise ValueError(f"数字超出支持范围：{value!r}")
    return result


def normalize(value: str, unit: str) -> Decimal:
    """Normalize a numeric string into base units, preserving decimal precision.

    Missing/ambiguous numbers and unsupported units raise ``ValueError``.
    Percent and percentage-point values share a numeric scale but their semantic
    unit kinds remain distinct in ``check_facts``.
    """
    number = _number(value, unit)
    with localcontext() as ctx:
        ctx.prec = max(50, len(number.as_tuple().digits) + 20)
        return number * _unit(unit)[1]


def _metric(fact: Fact) -> str:
    return normalize_metric(fact.metric)


def _period_valid(period: str, metric: str) -> bool:
    if metric == "publication_year":
        return period == "publication"
    if re.fullmatch(r"\d{4}(FY|H1|H2|9M|Q1|Q2|Q3|Q4)", period):
        return True
    try:
        return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", period)) and bool(date.fromisoformat(period))
    except ValueError:
        return False


def _evidence_issues(evidence: Evidence) -> list[str]:
    issues = []
    if evidence.quality != "ok":
        # Only informational warnings are non-blocking; OCR still needs review.
        info_only = evidence.quality == "warn" and bool(evidence.notes) and all(
            str(note).startswith("info:") for note in evidence.notes)
        if not info_only:
            issues.append(f"证据质量为 {evidence.quality or 'unknown'}")
    if not evidence.doc_id or not evidence.block_id:
        issues.append("缺少文档或区块标识")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", evidence.sha256 or ""):
        issues.append("缺少有效原文件 SHA-256")
    paragraph_ok = isinstance(evidence.paragraph, int) and not isinstance(evidence.paragraph, bool) and evidence.paragraph >= 1
    page_ok = isinstance(evidence.page, int) and not isinstance(evidence.page, bool) and evidence.page >= 1
    bbox_ok = False
    try:
        if evidence.bbox is not None and len(evidence.bbox) == 4:
            box = [Decimal(str(v)) for v in evidence.bbox]
            bbox_ok = all(v.is_finite() for v in box) and box[0] >= 0 and box[1] >= 0 and box[2] > box[0] and box[3] > box[1]
    except (TypeError, ValueError, InvalidOperation):
        pass
    # If a PDF page is provided it must be locatable on that page; a paragraph
    # number must not accidentally mask invalid PDF coordinates.
    if not ((page_ok and bbox_ok) if evidence.page is not None else paragraph_ok):
        issues.append("缺少有效 PDF 页码/坐标或文档段落定位")
    if evidence.char_start is not None or evidence.char_end is not None:
        start, end = evidence.char_start, evidence.char_end
        if not (isinstance(start, int) and isinstance(end, int) and 0 <= start < end):
            issues.append("字符区间无效")
    return issues


def _issues(fact: Fact) -> list[str]:
    issues = []
    if str(fact.company).strip().lower() in _UNKNOWN:
        issues.append("公司不明确")
    if not fact.metric or _metric(fact).lower() in _UNKNOWN:
        issues.append("指标不明确")
    if not _period_valid(fact.period, _metric(fact)):
        issues.append("期间不明确或格式无效")
    if fact.basis not in _KNOWN_BASES:
        issues.append("调整前后口径不明确")
    if fact.scope not in _KNOWN_SCOPES:
        issues.append("合并/母公司口径不明确")
    if str(fact.currency).strip().lower() in _UNKNOWN:
        issues.append("币种不明确")
    try:
        normalize(fact.value, fact.unit)
    except ValueError as exc:
        issues.append(str(exc))
    if not fact.evidence:
        issues.append("缺少可追溯证据")
    for evidence in fact.evidence:
        issues.extend(_evidence_issues(evidence))
    issues.extend(str(item) for item in fact.warnings if item)
    return list(dict.fromkeys(issues))


def _input(fact: Fact) -> dict[str, Any]:
    result = {key: getattr(fact, key) for key in ("fact_id", "company", "metric", "value", "unit", "period", "basis", "scope", "currency")}
    try:
        result["normalized_value"] = str(normalize(fact.value, fact.unit))
    except ValueError:
        result["normalized_value"] = None
    result["evidence"] = [{"doc_id": e.doc_id, "sha256": e.sha256, "run_id": e.run_id,
                            "block_id": e.block_id, "page": e.page, "paragraph": e.paragraph,
                            "bbox": e.bbox, "quality": e.quality} for e in fact.evidence]
    result["value_locations"] = _primary_locations(fact)
    return result


def _primary_locations(fact: Fact) -> list[dict[str, Any]]:
    """Return value-bearing locations, never the whole context evidence set.

    New extractors explicitly mark the actual numeric rows. Legacy/manual facts
    use their first evidence item. Explicit but invalid/missing marked locations
    are not silently replaced with context evidence.
    """
    keys = ("doc_id", "page", "paragraph", "block_id")
    evidence_locations = [{key: getattr(e, key) for key in keys} for e in fact.evidence]
    if "value_locations" not in fact.attributes:
        return evidence_locations[:1]
    declared = fact.attributes["value_locations"]
    if not isinstance(declared, list):
        return []
    locations = []
    for item in declared:
        if not isinstance(item, dict):
            continue
        location = {key: item.get(key) for key in keys}
        if location in evidence_locations and location not in locations:
            locations.append(location)
    return locations


def _finding(claim: Fact, status: str, rule: str, message: str,
             sources: list[Fact] | None = None, error_type: str = "",
             suggestion: str = "", suggested_value: str | None = None,
             extra: dict[str, Any] | None = None) -> Finding:
    sources = sources or []
    calculation = {"rule_id": rule, "claim": _input(claim), "sources": [_input(s) for s in sources]}
    if extra:
        calculation.update(extra)
    return Finding(claim, status, error_type, rule, message, suggestion,
                   suggested_value, sources, calculation)


def _review(claim: Fact, message: str, sources: list[Fact] | None = None,
            rule: str = "C.EVIDENCE.001", extra: dict[str, Any] | None = None) -> Finding:
    return _finding(claim, "needs_review", rule, message, sources,
                    suggestion="补充或人工核对可靠证据后再比较；当前不生成正确值。", extra=extra)


def _identity_conflict(facts: list[Fact]) -> bool:
    ids: dict[str, set[str]] = {}
    for fact in facts:
        for evidence in fact.evidence:
            ids.setdefault(evidence.doc_id, set()).add(evidence.sha256.lower())
    return any(len(hashes) > 1 for hashes in ids.values())


def _values_conflict(facts: list[Fact]) -> bool:
    values = {(_unit(f.unit)[0], normalize(f.value, f.unit), f.currency) for f in facts}
    return len(values) > 1


def _context(fact: Fact) -> tuple[str, str, str, str, str]:
    return _metric(fact), fact.period, fact.scope, fact.basis, fact.currency


def _same_context(left: Fact, right: Fact) -> bool:
    return _context(left) == _context(right)


def _rounded_expected(claim: Fact, source: Fact) -> Decimal:
    displayed = _number(claim.value, claim.unit)
    normalized = normalize(source.value, source.unit)
    with localcontext() as ctx:
        ctx.prec = max(50, len(normalized.as_tuple().digits) + abs(normalized.adjusted()) + abs(displayed.as_tuple().exponent) + 20)
        target = normalized / _unit(claim.unit)[1]
        return target.quantize(Decimal(1).scaleb(displayed.as_tuple().exponent), rounding=ROUND_HALF_UP)


def _equal(claim: Fact, source: Fact) -> bool:
    return _unit(claim.unit)[0] == _unit(source.unit)[0] and _number(claim.value, claim.unit) == _rounded_expected(claim, source)


def _fmt(number: Decimal) -> str:
    return format(number, "f")


def _prior_period(period: str) -> str | None:
    match = re.fullmatch(r"(\d{4})(FY|H1|H2|9M|Q[1-4])", period)
    if match:
        return str(int(match.group(1)) - 1) + match.group(2)
    try:
        value = date.fromisoformat(period)
        return value.replace(year=value.year - 1).isoformat()
    except ValueError:
        return None


def _derived(claim: Fact, sources: list[Fact]) -> tuple[list[Fact], Finding | None]:
    """Build derived facts only from independently locatable component facts."""
    metric = _metric(claim)
    if not (metric.endswith("_yoy") or metric == "pe"):
        return sources, None
    if metric == "pe":
        requirements = [("price", claim.period), ("eps_basic", claim.period)]
        formula = "price / eps_basic"
    else:
        prior = _prior_period(claim.period)
        if prior is None:
            return [], _review(claim, "同比期间无法确定上年同期。", rule="C.YOY.001")
        base = normalize_metric(metric[:-4])
        requirements = [(base, claim.period), (base, prior)]
        formula = "(current - prior) / prior * 100"
    selected: list[Fact] = []
    for required_metric, required_period in requirements:
        options = [s for s in sources if s.company == claim.company and _metric(s) == required_metric
                   and s.period == required_period and s.scope == claim.scope and s.basis == claim.basis
                   and s.currency == claim.currency]
        valid = [s for s in options if not _issues(s)]
        if any(_issues(s) for s in options):
            return [], _review(claim, "复算候选中存在未通过证据准入的来源，需先排除冲突。", options,
                               "C.DERIVED.001", {"rejected_sources": [{"fact_id": s.fact_id, "reasons": _issues(s)} for s in options if _issues(s)]})
        if not valid:
            # A published growth rate is useful only as a direct quotation. It
            # does not substitute for missing calculation inputs; require both.
            return [], _review(claim, f"缺少可核验的 {required_period} {required_metric}，无法复算。", options, "C.DERIVED.001")
        if _identity_conflict(valid) or _values_conflict(valid):
            return [], _review(claim, "复算输入存在冲突，不能选择性采用其中一项。", valid, "C.MATCH.002")
        selected.append(valid[0])
    if _identity_conflict(selected):
        return [], _review(claim, "复算输入复用了对应不同文件哈希的文档标识。", selected, "C.MATCH.002")
    a, b = (normalize(s.value, s.unit) for s in selected)
    if metric == "pe":
        # The explicit price/eps metric names establish per-share semantics;
        # financial reports also print their column heading simply as yuan.
        if any(_unit(s.unit)[2] not in {"元", "元/股", "元每股"} for s in selected):
            return [], _review(claim, "市盈率复算需要同一股价时点/口径的每股股价和每股收益。", selected, "C.PE.001")
        if a <= 0 or b <= 0:
            return [], _review(claim, "股价或每股收益非正，不能按普通正市盈率规则判错。", selected, "C.PE.001")
        unit, rule = "倍", "C.PE.001"
        with localcontext() as ctx:
            ctx.prec = 50
            expected = a / b
    else:
        if _unit(selected[0].unit)[0] != _unit(selected[1].unit)[0]:
            return [], _review(claim, "同比两期输入的量纲不同。", selected, "C.YOY.001")
        if b <= 0:
            return [], _review(claim, "上年基数为零或负数，同比口径需人工确认。", selected, "C.YOY.001")
        unit, rule = "%", "C.YOY.001"
        with localcontext() as ctx:
            ctx.prec = 50
            expected = (a - b) / b * 100
    derived = Fact(metric, _fmt(expected), unit, claim.period, claim.company,
                   claim.basis, claim.scope, claim.currency,
                   text="根据已定位来源复算", evidence=[e for s in selected for e in s.evidence],
                   attributes={"value_locations": [location for s in selected for location in _primary_locations(s)],
                               "derived": {"formula": formula, "rule_id": rule,
                                            "inputs": [_input(s) for s in selected],
                                            "result": _fmt(expected), "unit": unit}})
    return [derived], None


def _scope_related(left: str, right: str) -> bool:
    return left == right or {left, right} <= {"net_profit", "net_profit_parent"}


def _mismatch_type(claim: Fact, source: Fact) -> str:
    if claim.scope != source.scope or _metric(claim) != _metric(source):
        return "scope"
    if claim.basis != source.basis:
        return "basis"
    if claim.period != source.period:
        return "period"
    return ""


def _citation_error(claim: Fact, sources: list[Fact]) -> Finding | None:
    attrs = claim.attributes
    page, doc_id = attrs.get("citation_page"), attrs.get("citation_doc_id")
    if page is None and doc_id is None:
        return None
    if page is not None:
        try:
            if isinstance(page, bool) or int(page) != float(page) or int(page) < 1:
                raise ValueError
            page = int(page)
        except (TypeError, ValueError, OverflowError):
            return _review(claim, "引用页码不能解释为从 1 开始的 PDF 页码。", sources, "C.CITATION.001")
    # Context headings can explain units/periods on a different page, but citing
    # only that page must not count as locating the actual numeric evidence.
    # Every equivalent source's numeric locations remain eligible.
    locations = [location for source in sources for location in _primary_locations(source)]
    if not locations:
        return _review(claim, "来源没有可验证的数值行定位，不能核准所标引用。", sources, "C.CITATION.001")
    if any((page is None or location["page"] == page) and (doc_id is None or location["doc_id"] == doc_id) for location in locations):
        return None
    return _finding(claim, "confirmed_error", "C.CITATION.001", "所标引用与已核实数值的来源位置不一致。", sources,
                    "citation", "建议将引用改为计算记录中已核实的文档和位置。",
                    extra={"declared_citation": {"doc_id": doc_id, "page": page}, "verified_locations": locations})


def _check(claim: Fact, all_sources: list[Fact]) -> Finding:
    issues = _issues(claim)
    if claim.basis == "unknown" and issues == ["调整前后口径不明确"]:
        candidates = [s for s in all_sources if s.company == claim.company and _metric(s) == _metric(claim)
                      and s.period == claim.period and s.scope == claim.scope and s.currency == claim.currency
                      and s.basis in {"before", "after", "reported"}]
        if (candidates and not any(_issues(s) for s in candidates)
                and {"before", "after"} <= {s.basis for s in candidates}
                and not _identity_conflict(candidates) and not _values_conflict(candidates)
                and _equal(claim, candidates[0])):
            citation = _citation_error(claim, candidates)
            if citation:
                return citation
            return _finding(claim, "no_issue", "C.BASIS.001",
                            "研报未说明调整前后口径，但可靠来源的调整前后数值完全相同，且与研报显示精度一致。",
                            candidates, extra={"basis_invariant": True,
                                               "rounding": "ROUND_HALF_UP at claim displayed precision"})
    if issues:
        return _review(claim, "待核查事实不满足比较条件：" + "；".join(issues), extra={"blocking_reasons": issues})
    sources, derived_error = _derived(claim, all_sources)
    if derived_error is not None:
        return derived_error
    related = [s for s in sources if s.company == claim.company and _scope_related(_metric(claim), _metric(s))]
    if not related:
        return _review(claim, "没有找到同一公司的对应指标来源。", rule="C.MATCH.001")
    valid = [s for s in related if not _issues(s)]
    rejected = [{"fact_id": s.fact_id, "reasons": _issues(s)} for s in related if _issues(s)]
    if not valid:
        return _review(claim, "对应来源均缺少可靠证据或明确口径。", related, extra={"rejected_sources": rejected})
    same_currency = [s for s in valid if s.currency == claim.currency]
    if not same_currency:
        return _review(claim, "来源币种与研报不同，缺少明确汇率与换算日期，不能比较。", valid, "C.CURRENCY.001")
    valid = same_currency
    exact = [s for s in valid if _same_context(claim, s)]
    if exact and (_identity_conflict(exact) or _values_conflict(exact)):
        return _review(claim, "同一指标、期间和口径的来源冲突，不能选取有利候选。", exact, "C.MATCH.002")
    # Missing metadata on an exact-context candidate cannot silently be ignored
    # when a second source would otherwise appear to provide a clean answer.
    if any(_same_context(claim, s) for s in related if _issues(s)):
        return _review(claim, "匹配口径中存在未通过证据准入的来源，需要先排除冲突。", related,
                       extra={"rejected_sources": rejected})
    if exact:
        source = exact[0]
        extra = {"rounding": "ROUND_HALF_UP at claim displayed precision",
                 "expected_in_claim_unit": _fmt(_rounded_expected(claim, source))}
        if source.attributes.get("derived"):
            extra["derivation"] = source.attributes["derived"]
        if _equal(claim, source):
            citation = _citation_error(claim, exact)
            if citation:
                return citation
            return _finding(claim, "no_issue", source.attributes.get("derived", {}).get("rule_id", "C.VALUE.001"),
                            "在公司、期间、口径和币种一致的前提下，数值与来源在研报显示精度内一致。", exact, extra=extra)
        alternates = [s for s in valid if not _same_context(claim, s) and _equal(claim, s)]
        if alternates:
            # A value matching several different contexts does not uniquely
            # explain an error; report the established numeric discrepancy.
            dimensions = {_context(s) for s in alternates}
            if len(dimensions) == 1 and not _identity_conflict(alternates) and not _values_conflict(alternates):
                alt = alternates[0]
                kind = _mismatch_type(claim, alt)
                if kind:
                    extra["matching_alternative"] = _input(alt)
                    return _finding(claim, "confirmed_error", "C.CONTEXT.001",
                                    "研报数值匹配另一期间或口径，与声明条件下的来源数值不一致。", exact + alternates,
                                    kind, f"保留声明的期间和口径时，建议数值改为 {_fmt(_rounded_expected(claim, source))}{claim.unit}。",
                                    _fmt(_rounded_expected(claim, source)), extra)
        claim_family, claim_scale, _ = _unit(claim.unit)
        source_family, source_scale, _ = _unit(source.unit)
        if claim_family != source_family:
            if {claim_family, source_family} <= {"percent", "percentage_point"}:
                return _finding(claim, "confirmed_error", "C.UNIT.002", "百分比与百分点表示不同含义，不能互换。", exact,
                                "unit", f"建议按来源使用 {source.value}{source.unit}，并明确相对变化或绝对差。", source.value, extra)
            return _review(claim, "研报与来源量纲不同，不能仅凭数字判断正确值。", exact, "C.UNIT.001")
        kind = "period" if _metric(claim) == "publication_year" else "number"
        if claim_scale != source_scale and _number(claim.value, claim.unit) == _number(source.value, source.unit):
            kind = "unit"
            extra["unit_scale_ratio"] = str(claim_scale / source_scale)
        expected = _fmt(_rounded_expected(claim, source))
        rule = "C.UNIT.001" if kind == "unit" else "C.PERIOD.001" if kind == "period" else source.attributes.get("derived", {}).get("rule_id", "C.VALUE.001")
        return _finding(claim, "confirmed_error", rule,
                        "数值沿用了来源的原数，但标注的金额/数量单位改变了倍数。" if kind == "unit" else "文档冠名年度与可靠来源不一致。" if kind == "period" else "同一条件下，研报数值与可靠来源不一致。",
                        exact, kind, f"建议将 {claim.value}{claim.unit} 改为 {expected}{claim.unit}，原文由人工确认后修改。", expected, extra)
    # A value can legitimately recur across periods or scopes. Without the
    # intended-context source even a unique alternative match is only a lead.
    alternates = [s for s in valid if _equal(claim, s)]
    if alternates and len({_context(s) for s in alternates}) == 1 and not _identity_conflict(alternates) and not _values_conflict(alternates):
        source = alternates[0]
        kind = _mismatch_type(claim, source)
        if kind:
            return _review(claim, "数值匹配另一期间或口径，但缺少声明条件下的来源；相同数值可能重复出现，尚不能确认错引。",
                           alternates, "C.CONTEXT.002",
                           {"matching_context": _context(source), "suspected_error_type": kind,
                            "replacement_value_available": False})
    return _review(claim, "未找到同一期间、指标和口径的可靠来源；不能把其他期间的数值直接作为正确值。", valid, "C.MATCH.001")


def check_facts(claims: list[Fact], sources: list[Fact]) -> list[Finding]:
    """Return one traceable finding per claim without mutating either input.

    ``citation_page`` and ``citation_doc_id`` in claim attributes identify an
    explicit citation. ``*_yoy`` requires current and prior base metrics; ``pe``
    requires ``price`` and ``eps_basic``. All component facts must pass the same
    evidence and metadata checks used for ordinary numerical comparisons.
    """
    return [_check(claim, sources) for claim in claims]
