"""Extract auditable financial facts from B's blocks, without reading answer keys.

Financial table headings are stateful: a continued page inherits the previous
table's period, scope, unit and column order, never the year in its running header.
The text path also works when a PDF table detector only finds the middle columns.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field, replace
from decimal import Decimal, InvalidOperation
import re

from .models import Block, Document, Evidence, Fact


_NUMBER = re.compile(r"(?<![\d.])(?:[（(]\s*)?[-−﹣]?\d[\d,，]*(?:\.\d+)?(?:\s*[)）])?")
_BASIS = re.compile(r"(?:追溯)?调整前|(?:追溯)?调整后|重述前(?:金额)?|重述后(?:金额)?|(?:累积|累计)?影响金额|(?:追溯)?调整金额")
_DATE = re.compile(r"((?:19|20)\d{2})年(\d{1,2})月(\d{1,2})日")
_FLOW_PERIOD = re.compile(r"((?:19|20)\d{2})(?:年)?(半年度|上半年|1[-—～至]6月|年度|度|年)")
from .metric_catalog import SOURCE_PATTERNS as _METRICS, STOCK as _BALANCE_METRICS, metric_label



def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text).replace("﹣", "-").replace("−", "-")


def _metric(text: str) -> tuple[str, int] | None:
    """Recognize a row label, not a narrative mention of that label."""
    compact = _compact(text)
    label = re.sub(r"^(?:[（(]?[一二三四五六七八九十\d]+[)）.．、]?|其中[:：]|加[:：]|减[:：])+", "", compact)
    for name, pattern in _METRICS:
        match = re.match(pattern, label)
        if match:
            tail = label[match.end():]
            # A capital-reserve transfer row and a paragraph about profits are
            # not the capital-reserve/net-profit stock or flow itself.
            if name == "capital_reserve" and tail.startswith(("转", "弥补")):
                return None
            if tail.startswith(("为", "的", "同比", "增长", "减少", "增加", "情况")):
                return None
            # A longer unregistered concept cannot inherit a prefix metric.
            # Numeric columns and punctuation/footnotes remain row syntax.
            if re.match(r"[\u4e00-\u9fffA-Za-z]", tail):
                return None
            if re.match(r"[（(](?:跌价|减值|周转|占比|比重|增量|增长率|增加额|减少额)", tail):
                return None
            return name, compact.find(match.group()) + len(match.group())
    return None


def _number(text: str) -> str | None:
    value = _compact(text).replace(",", "").replace("，", "")
    if value in ("", "-", "—", "–", "/"):
        return None
    if value.startswith(("(", "（")) and value.endswith((")", "）")):
        value = "-" + value[1:-1]
    try:
        return format(Decimal(value), "f")
    except InvalidOperation:
        return None


def _periods(text: str) -> list[str]:
    compact = _compact(text)
    dated = [(m.start(), f"{m[1]}-{int(m[2]):02d}-{int(m[3]):02d}", m.span()) for m in _DATE.finditer(compact)]
    result = [(p, value) for p, value, _ in dated]
    for match in _FLOW_PERIOD.finditer(compact):
        if any(lo <= match.start() < hi for _, _, (lo, hi) in dated):
            continue
        result.append((match.start(), match[1] + ("H1" if match[2] in ("半年度", "上半年") or "6月" in match[2] else "FY")))
    return [value for _, value in sorted(result)]


def _as_balance(period: str) -> str:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", period):
        return period
    match = re.match(r"(\d{4})(.*)", period)
    if not match:
        return ""
    end = {"H1": "06-30", "Q1": "03-31", "Q3": "09-30"}.get(match[2], "12-31")
    return f"{match[1]}-{end}"


def _as_flow(period: str) -> str:
    if len(period) == 10 and period[4] == "-":
        return period[:4] + ("H1" if period[5:] == "06-30" else "FY")
    return period


def _basis(text: str) -> str:
    if "前" in text:
        return "before"
    if "后" in text:
        return "after"
    return "change"


@dataclass
class _Line:
    text: str
    blocks: list[Block]
    page: int | None
    bbox: list[float] | None
    cells: list[dict] = field(default_factory=list)
    row: int | None = None

    def evidence(self, doc: Document) -> list[Evidence]:
        result = []
        for block in self.blocks:
            ev = block.evidence(doc)
            if self.cells:
                ev = replace(ev, text=self.text, bbox=self.bbox or ev.bbox)
            if ev not in result:
                result.append(ev)
        return result


def _union(boxes: list[list[float]]) -> list[float] | None:
    if not boxes:
        return None
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _value_locations(evidence: list[Evidence]) -> list[dict]:
    """Only numeric-row evidence may validate a claimed value's citation."""
    locations = [{"doc_id": e.doc_id, "page": e.page, "paragraph": e.paragraph, "block_id": e.block_id} for e in evidence]
    return list({tuple(item.values()): item for item in locations}.values())


def _lines(doc: Document) -> list[_Line]:
    """Restore geometric rows before using PDF reading order.

    B's band sorter can put all left labels before right-side numbers. Sorting
    by physical baseline restores those rows. Full table rows are retained as
    an additional path; incomplete tables lacking row labels are ignored.
    """
    pages: dict[int | None, list[_Line]] = defaultdict(list)
    table_rows: dict[int | None, list[_Line]] = defaultdict(list)
    for block in doc.blocks:
        if block.type == "table" and block.cells:
            rows: dict[int, list[dict]] = defaultdict(list)
            for cell in block.cells:
                rows[int(cell.get("row", 0))].append(cell)
            if not any(_metric(str(c.get("text", ""))) for cells in rows.values() for c in sorted(cells, key=lambda c: c.get("col", 0))[:1]):
                continue
            for row, cells in sorted(rows.items()):
                cells = sorted(cells, key=lambda c: c.get("col", 0))
                text = " ".join(str(c.get("text", "")) for c in cells)
                boxes = [c["bbox"] for c in cells if isinstance(c.get("bbox"), (list, tuple))]
                bbox = _union(boxes)
                if not bbox and block.bbox:
                    h = (block.bbox[3] - block.bbox[1]) / max(len(rows), 1)
                    bbox = [block.bbox[0], block.bbox[1] + row*h, block.bbox[2], block.bbox[1] + (row+1)*h]
                table_rows[block.page].append(_Line(text, [block], block.page, bbox, cells, row))
            continue
        if not block.text.strip():
            continue
        chunks = [s for s in block.text.splitlines() if s.strip()]
        for i, text in enumerate(chunks):
            bbox = block.bbox
            if bbox and len(chunks) > 1:
                h = (bbox[3] - bbox[1]) / len(chunks)
                bbox = [bbox[0], bbox[1]+i*h, bbox[2], bbox[1]+(i+1)*h]
            pages[block.page].append(_Line(text, [block], block.page, bbox))
    result = []
    for page in sorted(set(pages) | set(table_rows), key=lambda p: -1 if p is None else p):
        parts = sorted(pages[page], key=lambda l: (l.bbox[1], l.bbox[0]) if l.bbox else (float("inf"), 0))
        joined: list[_Line] = []
        for line in parts:
            prior = joined[-1] if joined else None
            if prior and prior.bbox and line.bbox:
                overlap = min(prior.bbox[3], line.bbox[3]) - max(prior.bbox[1], line.bbox[1])
                small_height = min(prior.bbox[3]-prior.bbox[1], line.bbox[3]-line.bbox[1])
                # Only join horizontally separate fragments, never consecutive
                # label lines that happen to overlap due to approximate boxes.
                separated = line.bbox[0] >= prior.bbox[2]-2 or prior.bbox[0] >= line.bbox[2]-2
                if overlap > max(1.5, small_height * .2) and separated:
                    order = [prior, line] if prior.bbox[0] <= line.bbox[0] else [line, prior]
                    joined[-1] = _Line(" ".join(p.text for p in order), [b for p in order for b in p.blocks], page, _union([p.bbox for p in order]))
                    continue
            joined.append(line)
        combined = joined + table_rows[page]
        result.extend(sorted(combined, key=lambda l: (l.bbox[1], bool(l.cells), l.bbox[0]) if l.bbox else (float("inf"), bool(l.cells), 0)))
    return result


@dataclass
class _Context:
    default_period: str = ""
    period: str = ""
    kind: str = ""
    scope: str = "consolidated"
    unit: str = ""
    currency: str = "CNY"
    columns: list[str] = field(default_factory=list)
    column_periods: list[str] = field(default_factory=list)
    heading_evidence: list[Evidence] = field(default_factory=list)
    period_evidence: list[Evidence] = field(default_factory=list)
    unit_evidence: list[Evidence] = field(default_factory=list)
    column_evidence: list[Evidence] = field(default_factory=list)


def _update_context(line: _Line, ctx: _Context, doc: Document) -> bool:
    text = _compact(line.text)
    is_heading = False
    kinds = (("资产负债表", "balance"), ("利润表", "income"), ("现金流量表", "cashflow"), ("所有者权益变动表", "equity"), ("股东权益变动表", "equity"))
    for title, kind in kinds:
        if title not in text or len(text) > 90:
            continue
        # Exclude descriptive prose listing many statements or accounting rules.
        if sum(other in text for other, _ in kinds) > 1 or any(s in text for s in ("编制", "包括", "相应地", "应当", "合并利润表、", "时，", "以下简称")):
            continue
        if not (re.search(r"(?:^|[）)、])(?:对)?(?:(?:19|20)\d{2}.*)?(?:合并|母公司)?" + title, text)
                or re.search(r"(?:19|20)\d{2}.*(?:合并|母公司)"+title+r"$",text)
                or ("项目" in text and "影响" in text and "下表" in text)):
            continue
        ctx.kind = kind
        ctx.scope = "parent" if "母公司" in text else "consolidated"
        periods = _periods(text)
        chosen = periods[-1] if periods else (ctx.period or ctx.default_period)
        if periods:
            ctx.period_evidence = line.evidence(doc)
        ctx.period = _as_balance(chosen) if kind == "balance" else _as_flow(chosen)
        ctx.columns = []
        ctx.column_periods = []
        ctx.heading_evidence = line.evidence(doc)
        ctx.column_evidence = []
        is_heading = True
        break
    unit = re.search(r"(?:单位[:：]?|金额单位(?:均)?为)(?:人民币)?(亿元|百万元|万元|千元|元/股|元|%|％)", text)
    if unit:
        ctx.unit = unit[1].replace("％", "%")
        ctx.unit_evidence = line.evidence(doc)
    if ctx.kind == "balance" and _DATE.fullmatch(text):
        ctx.period = _periods(text)[0]
        ctx.period_evidence = line.evidence(doc)
    headings = list(_BASIS.finditer(text))
    if len(headings) >= 2 and len(text) < 120 and not _metric(text):
        ctx.columns = [_basis(m.group()) for m in headings]
        ctx.column_periods = []
        ctx.column_evidence = line.evidence(doc)
        return True
    if "期末余额" in text and "期初余额" in text:
        end = _as_balance(ctx.period or ctx.default_period)
        start = f"{int(end[:4])-1}-12-31" if end else ""
        positions = [(text.find("期末余额"), end), (text.find("期初余额"), start)]
        ctx.column_periods = [p for _, p in sorted(positions)]
        ctx.columns = ["reported"] * len(positions)
        ctx.column_evidence = line.evidence(doc)
        return True
    periods = _periods(text)
    if periods and ("项目" in text or (len(periods)>=2 and len(text)<50)) and not _metric(text):
        ctx.column_periods = periods
        ctx.columns = ["reported"] * len(periods)
        ctx.period = periods[0]
        ctx.period_evidence = line.evidence(doc)
        ctx.column_evidence = line.evidence(doc)
        return True
    if any(k in text for k in ("本期金额", "本期发生额")) and any(k in text for k in ("上期金额", "上期发生额", "上年同期")) and len(text) < 80:
        current = _as_flow(ctx.period or ctx.default_period)
        previous = str(int(current[:4])-1)+current[4:] if current else ""
        current_at = min((text.find(k) for k in ("本期金额", "本期发生额") if k in text), default=0)
        previous_at = min((text.find(k) for k in ("上期金额", "上期发生额", "上年同期") if k in text), default=0)
        ctx.column_periods = [p for _, p in sorted(((current_at, current), (previous_at, previous)))]
        ctx.columns = ["reported"]*2
        ctx.column_evidence = line.evidence(doc)
        return True
    return is_heading or bool(unit)


def _row_values(line: _Line, end: int) -> tuple[list[tuple[int, str]], bool]:
    """Return value column indices. Empty cells keep their original position."""
    if line.cells:
        label_cell = next((c for c in line.cells if _metric(str(c.get("text", "")))), None)
        if label_cell is not None:
            first_col = int(label_cell.get("col", 0)) + 1
            values = []
            for cell in line.cells:
                col = int(cell.get("col", 0))
                if col >= first_col:
                    value = _number(str(cell.get("text", "")))
                    if value is not None:
                        values.append((col-first_col, value))
            return values, True
    # Keep spaces between numeric columns, including when all are ungrouped.
    # Locate the label in original text by counting non-whitespace characters.
    offset, seen = 0, 0
    for offset, char in enumerate(line.text):
        if not char.isspace():
            seen += 1
        if seen >= end:
            offset += 1
            break
    tail = line.text[offset:]
    # Parenthetical label explanations have no financial number; a numeric
    # bracket (negative amount) must remain available to the number parser.
    tail = re.sub(r"[（(][^\d)]*?[)）]", "", tail)
    values = []
    for match in _NUMBER.finditer(tail):
        value = _number(match.group())
        if value is not None:
            values.append((len(values), value))
    return values, False


def _publication_fact(doc: Document) -> Fact | None:
    """The contract names this publication_year, but it means title year.

    A '2025 年度对以前年度...' report may be signed in 2026. The financial
    reporting year in that title must not be replaced by the signature date.
    """
    candidates: list[tuple[str, Block]] = []
    for index, block in enumerate(doc.blocks):
        if block.type == "table" or block.status == "fail":
            continue
        nearby = [b for b in doc.blocks[index:index+3] if b.page == block.page and b.type != "table"]
        text = _compact("".join(b.text for b in nearby))
        match = re.search(r"((?:19|20)\d{2})年度对以前年度(?:报告)?披露", text)
        if match and match[1] in block.text:
            candidates.append((match[1], block))
    if not candidates:
        return None
    year = Counter(y for y, _ in candidates).most_common(1)[0][0]
    blocks = [b for y, b in candidates if y == year]
    warnings = ["conflicting_publication_years"] if len({y for y, _ in candidates}) > 1 else []
    evidence = list({(b.page, b.block_id): b.evidence(doc) for b in blocks}.values())
    # Repeated title text on an OCR page is corroboration, not a prerequisite
    # for a fully located native-text title of the same year. Retain the less
    # reliable copies for audit without making them primary value locations.
    # If every copy requires review, keep that evidence as primary so the
    # ordinary evidence gate still rejects automatic confirmation. Cross-year
    # disagreement always keeps the warning above, regardless of quality.
    reliable = [e for e in evidence if e.quality == "ok"]
    primary = reliable or evidence
    all_evidence = list({(b.page, b.block_id): b.evidence(doc) for _, b in candidates}.values())
    alternative = [asdict(e) for e in all_evidence if e not in primary]
    return Fact("publication_year", year, "年", "publication", doc.company,
                basis="reported", text=primary[0].text, evidence=primary,
                warnings=warnings, attributes={"meaning": "report_title_fiscal_year",
                                               "value_locations": _value_locations(primary),
                                               "alternative_evidence": alternative})


def extract_source_facts(doc: Document) -> list[Fact]:
    """Extract facts from financial source B output with original evidence.

    No business numbers, company names, answer spreadsheets or original PDFs
    are consulted here. Ambiguous columns/periods are marked for review instead
    of silently becoming authoritative financial evidence.
    """
    default = doc.period
    default_evidence: list[Evidence] = []
    if not default:
        for block in doc.blocks:
            if block.status == "fail":
                continue
            if any(t in block.text for t in ("财务报表", "年度", "半年度")):
                periods = _periods(block.text)
                if periods:
                    default = periods[0]
                    default_evidence = [block.evidence(doc)]
                    break
    ctx = _Context(default_period=default, period=default, period_evidence=default_evidence)
    facts: list[Fact] = []
    publication = _publication_fact(doc)
    if publication:
        facts.append(publication)
    lines = _lines(doc)
    for index, line in enumerate(lines):
        if all(b.status == "fail" for b in line.blocks):
            continue
        if _update_context(line, ctx, doc):
            continue
        found = _metric(line.text)
        if not found or ctx.kind == "equity":
            continue
        metric, end = found
        values, indexed = _row_values(line, end)
        row_evidence = line.evidence(doc)
        # A wrapped label can put all amounts on the next physical text line.
        if not values and not line.cells:
            for next_line in lines[index+1:index+3]:
                if next_line.page != line.page or _metric(next_line.text):
                    break
                if line.bbox and next_line.bbox and next_line.bbox[1]-line.bbox[3] > 16:
                    break
                prefix = re.split(r"\d", _compact(next_line.text), maxsplit=1)[0]
                wrapped_explanation = "填列" in prefix and len(prefix) <= 24
                if re.search(r"[\u4e00-\u9fff]", next_line.text) and not wrapped_explanation:
                    break
                possible = [(i, _number(m.group())) for i, m in enumerate(_NUMBER.finditer(next_line.text))]
                if possible:
                    values = [(i, v) for i, v in possible if v is not None]
                    row_evidence += next_line.evidence(doc)
                    break
        if not values or len(values) > 6:
            continue
        warnings: list[str] = []
        columns = ctx.columns or ["reported"] * len(values)
        if not indexed and len(columns) == 3 and len(values) == 2 and "change" in columns:
            # Blank change columns are common in unchanged restated rows. Only
            # infer that missing position when equality proves the difference.
            if Decimal(values[0][1]) == Decimal(values[1][1]):
                slots = [i for i, basis in enumerate(columns) if basis != "change"]
                values = list(zip(slots, (v for _, v in values)))
            elif columns[-1] != "change":
                warnings.append("ambiguous_missing_column")
        if not indexed and len(values) == 3 and set(columns) == {"before", "change", "after"}:
            # Arithmetic exposes ambiguity; it never authorizes an OCR reorder.
            amounts = {columns[col]: Decimal(value) for col, value in values}
            if amounts["after"] != amounts["before"] + amounts["change"]:
                warnings.append("restatement_arithmetic_inconsistent")
        if len(values) > len(columns):
            warnings.append("column_count_mismatch")
        row_facts = []
        for col, value in values:
            basis = columns[col] if col < len(columns) else "unknown"
            period = ctx.column_periods[col] if col < len(ctx.column_periods) else ctx.period
            period = _as_balance(period) if metric in _BALANCE_METRICS else _as_flow(period)
            unit = "元/股" if metric == "eps_basic" else ctx.unit
            if metric == "price":
                unit = "元/股" if "元" in line.text or ctx.unit in {"元", "元/股"} else ""
            if metric == "gross_margin":
                unit = "%" if re.search(r"[%％]|百分比", line.text) or ctx.unit == "%" else ""
            fact_warnings = list(warnings)
            if not period:
                fact_warnings.append("missing_period")
            if not unit:
                fact_warnings.append("missing_unit")
            if not ctx.columns:
                fact_warnings.append("missing_column_heading")
            evidence = row_evidence + ctx.heading_evidence + ctx.period_evidence + ctx.column_evidence + ctx.unit_evidence
            unique = list({(e.doc_id, e.block_id, e.text): e for e in evidence}.values())
            attrs = {"source_column": col, "statement": ctx.kind,
                     "metric_label": metric_label(line.text, metric), "extraction": "rules-v2",
                     "value_locations": _value_locations(row_evidence)}
            if line.cells:
                attrs["cell"] = {"row": line.row, "col": col+1}
            fact = Fact(metric, value, unit, period, doc.company, basis=basis,
                        scope=ctx.scope, currency=ctx.currency, text=line.text,
                        evidence=unique, warnings=fact_warnings, attributes=attrs)
            row_facts.append(fact)
        facts.extend(row_facts)
        # An explicit blank difference cell can be supported by a calculation;
        # this is retained as a derived fact with both inputs in the evidence.
        bases = {f.basis: f for f in row_facts}
        if "change" in columns and "change" not in bases and "before" in bases and "after" in bases and not warnings:
            before, after = bases["before"], bases["after"]
            facts.append(Fact(metric, format(Decimal(after.value)-Decimal(before.value), "f"), after.unit,
                              after.period, doc.company, basis="change", scope=ctx.scope,
                              evidence=after.evidence, text=line.text,
                              warnings=list(after.warnings), attributes={"derived": {"formula": "after - before", "before": before.value, "after": after.value, "rule_id": "C.SOURCE.DIFFERENCE"},
                              "value_locations": _value_locations(row_evidence)}))
    # The complete text and cells often describe the same row. Prefer a cell
    # reference when both have the same result, while retaining distinct values
    # so a conflict remains visible to the matcher.
    dedup: dict[tuple, Fact] = {}
    for fact in facts:
        key = (fact.metric, Decimal(fact.value), fact.unit, fact.period, fact.company, fact.basis, fact.scope, fact.currency)
        previous = dedup.get(key)
        if previous is None:
            dedup[key] = fact
            continue
        best = fact if (len(fact.warnings), "cell" not in fact.attributes) < (len(previous.warnings), "cell" not in previous.attributes) else previous
        other = previous if best is fact else fact
        best.evidence = list({(e.doc_id, e.page, e.paragraph, e.block_id, e.text): e for e in [*best.evidence, *other.evidence]}.values())
        locations = [*best.attributes.get("value_locations", []), *other.attributes.get("value_locations", [])]
        best.attributes["value_locations"] = list({(loc["doc_id"], loc["page"], loc["paragraph"], loc["block_id"]): loc for loc in locations}.values())
        best.fact_id = ""
        best.__post_init__()
        dedup[key] = best
    return list(dedup.values())
