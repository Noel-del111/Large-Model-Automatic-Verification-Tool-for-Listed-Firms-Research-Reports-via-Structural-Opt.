"""Shared, explicit metric vocabulary for prose, tables and deterministic checks.

Aliases express known equivalent labels, not model-inferred business meaning.
In particular, total revenue and operating revenue retain separate identities.
"""
from __future__ import annotations

import re


CATALOG: dict[str, tuple[str, ...]] = {
    "net_profit_parent_excl": ("扣除非经常性损益后归属于母公司股东的净利润", "扣除非经常性损益后归属于上市公司股东的净利润", "扣非归母净利润", "扣非净利润"),
    "net_profit_parent": ("归属于母公司股东的净利润", "归属于母公司所有者的净利润", "归属于母公司的净利润", "归母净利润", "归母利润"),
    "equity_parent": ("归属于母公司所有者权益合计", "归属于母公司所有者权益", "归属于母公司股东权益合计", "归属于母公司股东权益", "归母所有者权益", "归母权益"),
    "revenue_total": ("营业总收入",),
    "revenue": ("营业收入", "营收"),
    "operating_cost": ("营业成本",),
    "operating_profit": ("营业利润",),
    "total_liabilities": ("负债合计", "负债总计", "负债总额", "总负债"),
    "total_equity": ("所有者权益合计", "所有者权益总计", "股东权益合计", "股东权益总计"),
    "total_liabilities_equity": ("负债和所有者权益总计", "负债及所有者权益总计", "负债和股东权益总计"),
    "total_assets": ("资产总计", "资产总额", "总资产"),
    "operating_cashflow": ("经营活动产生的现金流量净额", "经营活动现金流量净额", "经营现金流净额"),
    "eps_basic": ("基本每股收益", "每股收益"),
    "price": ("每股市价", "股票价格", "股价"),
    "gross_margin": ("毛利率",),
    "cash": ("货币资金",),
    "inventory": ("存货",),
    "retained_earnings": ("未分配利润",),
    "capital_reserve": ("资本公积",),
    "net_profit": ("净利润",),
    "pe": ("市盈率", "PE"),
    "publication_year": ("文档所属年度",),
}

# Preserve the finite grammatical variants recognized by the original table
# extractor, including labels without an owner noun or the particle 的.
for _name in ("net_profit_parent", "net_profit_parent_excl", "equity_parent"):
    _labels = set(CATALOG[_name])
    for _entity in (("母公司", "上市公司") if _name == "net_profit_parent_excl" else ("母公司",)):
        for _owner in ("股东", "所有者", ""):
            for _particle in ("的", ""):
                for _tail in (("权益合计", "权益总计") if _name == "equity_parent" else ("净利润",)):
                    _labels.add(("扣除非经常性损益后" if _name == "net_profit_parent_excl" else "")
                                + "归属于" + _entity + _owner + _particle + _tail)
    CATALOG[_name] = tuple(_labels)

# Enumerate only grammatical variants of explicit legal accounting labels.
for _name in ("net_profit_parent", "net_profit_parent_excl", "equity_parent"):
    _labels = set(CATALOG[_name])
    for _label in tuple(_labels):
        if "归属于" in _label:
            for _prefix in ("归属于", "归属於", "归属"):
                for _suffix in (_label, _label.replace("的", "")):
                    _labels.add(_suffix.replace("归属于", _prefix))
    CATALOG[_name] = tuple(sorted(_labels, key=lambda x: (-len(x), x)))

METRICS = {label: name for name, labels in CATALOG.items() for label in labels}
METRIC_RE = re.compile("|".join(re.escape(s) for s in sorted(METRICS, key=lambda s: (-len(s), s))))
STOCK = frozenset({"cash", "inventory", "retained_earnings", "equity_parent", "total_assets", "capital_reserve", "total_liabilities", "total_equity", "total_liabilities_equity"})
SOURCE_PATTERNS = tuple(
    (name, "(?:" + "|".join(re.escape(label) for label in sorted(labels, key=lambda s: (-len(s), s))) + ")")
    for name, labels in sorted(CATALOG.items(), key=lambda item: -max(map(len, item[1])))
    if name != "publication_year"
)


def normalize_metric(label: str) -> str:
    """Map explicit aliases, including growth-rate suffixes, without guessing."""
    label = str(label).strip()
    for suffix in ("_yoy", "_qoq"):
        if label.endswith(suffix):
            return METRICS.get(label[:-len(suffix)], label[:-len(suffix)]) + suffix
    return METRICS.get(label, label)


def metric_label(text: str, metric: str) -> str:
    """Return the original matched label with its original whitespace."""
    positions = [i for i, char in enumerate(text) if not char.isspace()]
    compact = "".join(text[i] for i in positions)
    for match in METRIC_RE.finditer(compact):
        if METRICS[match.group()] == metric:
            return text[positions[match.start()]:positions[match.end() - 1] + 1]
    return ""
