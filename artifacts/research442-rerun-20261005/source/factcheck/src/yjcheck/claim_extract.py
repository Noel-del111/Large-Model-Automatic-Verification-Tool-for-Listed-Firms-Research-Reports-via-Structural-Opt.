"""从研报正文提取待核查的事实；位置始终指向原文，不使用 E 的答案。"""
from __future__ import annotations

import re
from dataclasses import replace
from .models import Block, Document, Fact

from .metric_catalog import METRICS, METRIC_RE, STOCK

NUMBER_RE = re.compile(r"(?P<value>[+\-−－]?(?:\d{1,3}(?:[,，]\d{3})+|\d+)(?:\.\d+)?|[（(][\d,，]+(?:\.\d+)?[）)])\s*(?P<unit>(?:亿|万|千)?(?:美元|港元|欧元)|千万元|百万元|亿元|万元|千元|元/股|元／股|元|个百分点|百分点|%|％|倍)")


def period_in(text: str, default: str = "") -> str:
    compact = re.sub(r"\s+", "", text)
    compact = re.sub(r"20\d{2}年度披露", "年度披露", compact)
    matches = list(re.finditer(r"(20\d{2})年?(半年度|上半年|下半年|第一季度|一季度|第二季度|三季度|第三季度|前三季度|年度|年末|年底|年)(?!\d)", compact))
    if not matches:
        return default
    y, suffix = matches[-1].groups()
    kind = {"半年度":"H1", "上半年":"H1", "下半年":"H2", "第一季度":"Q1", "一季度":"Q1",
            "第二季度":"Q2", "第三季度":"Q3", "三季度":"Q3", "前三季度":"9M"}.get(suffix,"FY")
    return y + kind


def _basis(text: str, default: str) -> str:
    hits = list(re.finditer(r"调整前|重述前|调整后|重述后|影响(?:金额|数)?|变动金额", text))
    if not hits:
        return default
    word = hits[-1].group()
    return "before" if word.endswith("前") else "after" if word.endswith("后") else "change"


def _period(text: str, metric: str, default: str) -> str:
    period = period_in(text, default)
    if re.search(r"上年同期|去年同期", text) and re.match(r"20\d{2}", period):
        period = str(int(period[:4]) - 1) + period[4:]
    if metric in STOCK:
        date = list(re.finditer(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", text))
        if date:
            y, m, d = date[-1].groups()
            return f"{y}-{int(m):02d}-{int(d):02d}"
        if period:
            return period[:4] + {"H1":"-06-30", "Q1":"-03-31", "Q2":"-06-30", "Q3":"-09-30", "9M":"-09-30"}.get(period[4:], "-12-31")
    return period


def _extract_native_claims(doc: Document) -> list[Fact]:
    title = doc.blocks[0].text if doc.blocks else ""
    doc.period = period_in(title, doc.period)
    default_basis = "after" if any(s in title for s in ("追溯调整", "重述")) else "reported"
    claims = []
    for block in doc.blocks:
        if block.type == "table":
            continue  # 表格研报由 source_extract 的结构化表格流程补充
        text = block.text
        for sentence in re.finditer(r"[^。！？；]+[。！？；]?", text):
            raw = sentence.group()
            compact = "".join(c for c in raw if not c.isspace())
            mapping = [sentence.start()+i for i,c in enumerate(raw) if not c.isspace()]
            if not compact:
                continue
            loc = block.evidence(doc, raw, sentence.start(), sentence.end())
            for match in re.finditer(r"(20\d{2})年度披露(?:的)?(?:追溯调整|专项)", compact):
                claims.append(Fact("publication_year", match.group(1), "年", "publication", doc.company,
                                   basis="reported", text=raw, evidence=[loc],
                                   attributes={"value_start":mapping[match.start()],"value_end":mapping[match.start()+3]+1}))
            metrics = list(METRIC_RE.finditer(compact))
            for i, metric_match in enumerate(metrics):
                metric = METRICS[metric_match.group()]
                stop = metrics[i+1].start() if i+1 < len(metrics) else len(compact)
                segment = compact[metric_match.end():stop]
                prefix = compact[:metric_match.start()]
                # 声明仅取指标之后的数值。其他指标出现即停止，避免串取邻项。
                for num in NUMBER_RE.finditer(segment):
                    between = segment[:num.start()]
                    if "同比" in between or "环比" in between:
                        current_metric = metric + ("_yoy" if "同比" in between else "_qoq")
                        basis = _basis(prefix, default_basis)
                    else:
                        current_metric = metric
                        # 当前指标之前的口径影响本句后续指标，数值前明确口径优先。
                        basis = _basis(prefix + metric_match.group() + between, default_basis)
                    context = compact[:metric_match.end()] + between
                    # 期间的绝对年份可沿用，但上年同期等相对指代只属于当前指标。
                    local = compact[max(compact.rfind("，",0,metric_match.start())+1,0):metric_match.end()] + between
                    absolute_context = re.sub(r"上年同期|去年同期", "", context)
                    period = _period(absolute_context + ("上年同期" if re.search(r"上年同期|去年同期",local) else ""), metric, doc.period)
                    unit = num.group("unit").replace("％", "%").replace("／", "/")
                    currency="CNY"
                    for label, code in (("美元","USD"),("港元","HKD"),("欧元","EUR")):
                        if unit.endswith(label):
                            currency=code
                            unit=unit[:-len(label)]+"元"
                    if metric in {"eps_basic", "price"} and unit == "元":
                        unit = "元/股"
                    if current_metric.endswith(("_yoy", "_qoq")) and unit not in {"%", "百分点", "个百分点"}:
                        continue
                    scope_hits=list(re.finditer(r"母公司口径|母公司报表|合并口径|合并报表",context))
                    scope = "parent" if scope_hits and scope_hits[-1].group().startswith("母公司") else "consolidated"
                    prefix_start = max(0, metric_match.start() - 3)
                    if (compact[prefix_start:metric_match.start()] == "母公司"
                            and metric not in {"net_profit_parent", "net_profit_parent_excl", "equity_parent"}):
                        scope = "parent"
                    start = metric_match.end()+num.start()
                    end = metric_match.end()+num.end()
                    attrs = {"value_start": mapping[start], "value_end":mapping[end-1]+1,
                             "metric_label":text[mapping[metric_match.start()]:mapping[metric_match.end()-1]+1],
                             "extraction":"rules-v2"}
                    cite = re.search(r"(?:财报|来源|引用|见|报告)[^。；]{0,15}?(?:第\s*(\d+)\s*页|[Pp](\d+))", compact[metric_match.start():stop])
                    if cite:
                        attrs["citation_page"] = int(cite.group(1) or cite.group(2))
                    value = num.group("value").replace("，", ",").replace("−", "-").replace("－", "-")
                    if current_metric.endswith(("_yoy","_qoq")) and re.search(r"下降|减少|下滑",between) and not value.startswith("-"):
                        value="-"+value.lstrip("+")
                    warnings = []
                    # A known word can be the prefix of a different concept,
                    # e.g. 存货跌价准备 is not 存货. Preserve the candidate for
                    # review, but do not grant it the base metric's semantics.
                    suffix = segment.lstrip("：:，,（(")
                    if (re.match(r"[\u4e00-\u9fffA-Za-z]", suffix)
                            and not re.match(r"(?:为|是|约为|约|达到|分别|同比|环比|(?:由|的)?(?:调整前|调整后|重述前|重述后)|(?:的)?影响(?:金额)?|录得|合并口径|母公司口径)", suffix)):
                        warnings.append("unverified_metric_modifier")
                        attrs["metric_context"] = metric_match.group() + segment
                    if "预测" in context or "预计" in context or "目标" in context:
                        warnings.append("forecast_not_historical_fact")
                    if metric=="gross_margin" and len(list(NUMBER_RE.finditer(segment)))>1:
                        warnings.append("rate_transition_requires_explicit_periods")
                    claims.append(Fact(current_metric, value, unit, period, doc.company,
                                       basis=basis, scope=scope, currency=currency, text=raw, evidence=[loc],
                                       attributes=attrs, warnings=warnings))
    # 保留不同位置的重复声明；同一来源位置的相同候选只输出一次。
    return list({fact.fact_id: fact for fact in claims}.values())


def extract_claims(doc: Document) -> list[Fact]:
    """恢复同页相邻文本行的句子，但每项证据仍映射到 B 的原始区块。"""
    groups=[]
    for index,block in enumerate(doc.blocks):
        join=False
        if groups and index>1 and block.page is not None and block.bbox and block.type!="table":
            previous=groups[-1][-1]
            if previous.page==block.page and previous.bbox and previous.type!="table" and previous.status==block.status:
                a,b=previous.bbox,block.bbox
                horizontal=max(0,min(a[2],b[2])-max(a[0],b[0]))
                gap=b[1]-a[3]
                join=(not re.search(r"[。！？；]$",previous.text.strip())
                      and -1<=gap<=max(24,2*(a[3]-a[1])) and b[1]>a[1]
                      and (abs(a[0]-b[0])<35 or horizontal>=min(a[2]-a[0],b[2]-b[0])*.4))
        if join:
            groups[-1].append(block)
        else:
            groups.append([block])
    assembled=[]
    spans={}
    for group in groups:
        if len(group)==1:
            assembled.append(group[0])
            continue
        text="\n".join(b.text for b in group)
        block_id="joined:"+":".join(b.block_id for b in group)
        bbox=[min(b.bbox[0] for b in group),min(b.bbox[1] for b in group),
              max(b.bbox[2] for b in group),max(b.bbox[3] for b in group)]
        assembled.append(replace(group[0],block_id=block_id,text=text,bbox=bbox))
        cursor=0
        parts=[]
        for block in group:
            parts.append((cursor,cursor+len(block.text),block))
            cursor+=len(block.text)+1
        spans[block_id]=parts
    view=replace(doc,blocks=assembled)
    claims=_extract_native_claims(view)
    doc.period=view.period
    for fact in claims:
        located=[]
        value_locations=[]
        for e in fact.evidence:
            parts=spans.get(e.block_id)
            if not parts:
                located.append(e)
                continue
            for lo,hi,block in parts:
                start=max(e.char_start or 0,lo)
                end=min(e.char_end if e.char_end is not None else hi,hi)
                if end>start:
                    located.append(block.evidence(doc,block.text[start-lo:end-lo],start-lo,end-lo))
                vstart=max(fact.attributes.get("value_start",lo),lo)
                vend=min(fact.attributes.get("value_end",lo),hi)
                if vend>vstart:
                    value_locations.append({"block_id":block.block_id,"page":block.page,
                                            "char_start":vstart-lo,"char_end":vend-lo})
        fact.evidence=located
        if value_locations:
            fact.attributes.pop("value_start",None)
            fact.attributes.pop("value_end",None)
            fact.attributes["value_locations"]=value_locations
        fact.fact_id=""
        fact.__post_init__()
    return claims
