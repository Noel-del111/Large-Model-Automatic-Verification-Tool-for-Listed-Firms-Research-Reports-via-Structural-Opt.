"""核查层错误类型字典：集中定义 error_type 代码、中文标签与 FinED-Bench 类型对应关系。

用途：
- intrinsic 检查产出新错误类型（numeric_inconsistency / time_conflict / unit_term_mismatch）；
- 评测器（tools/evaluate_samples.py）解析答案表错误类型名称时引用 FINED_ANSWER_MAP；
- 前端标签与报告文案的中文名以 ZH_LABELS 为准。

detectable=False 的类型仅作为答案表/人工标注的预留归类，规则引擎不产出。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ErrorTypeDef:
    code: str
    zh_label: str
    fined_name: str | None
    detectable: bool  # 规则引擎能否产出该类型


ERROR_TYPES: dict[str, ErrorTypeDef] = {
    # 既有类型（保留字面量，兼容存量 finding 与答案表）
    "number": ErrorTypeDef("number", "数值", None, True),
    "unit": ErrorTypeDef("unit", "单位", "数值单位错误", True),
    "period": ErrorTypeDef("period", "期间", None, True),
    "basis": ErrorTypeDef("basis", "调整前后口径", None, True),
    "scope": ErrorTypeDef("scope", "归属范围", None, True),
    "citation": ErrorTypeDef("citation", "引用", "法规引用错误", True),
    "input_quality": ErrorTypeDef("input_quality", "输入质量", None, False),
    "coverage": ErrorTypeDef("coverage", "覆盖范围", None, False),
    # 新增：研报自身一致性检查（一律转人工，needs_review）
    "numeric_inconsistency": ErrorTypeDef("numeric_inconsistency", "数值不一致", "数值不一致错误", True),
    "time_conflict": ErrorTypeDef("time_conflict", "时间矛盾", "时间矛盾", True),
    "unit_term_mismatch": ErrorTypeDef("unit_term_mismatch", "单位-术语不匹配", "数值单位错误", True),
    # 预留：仅转人工 / 人工标注类（规则引擎不产出，仅支持答案表标注与报告归类）
    "calc_error": ErrorTypeDef("calc_error", "计算错误", "计算错误", False),
    "numeric_missing": ErrorTypeDef("numeric_missing", "数值缺失", "数值缺失", False),
    "redundant_statement": ErrorTypeDef("redundant_statement", "冗余语句", "冗余语句", False),
    "invalid_time": ErrorTypeDef("invalid_time", "时间信息非法", "时间信息非法", False),
    "term_misuse": ErrorTypeDef("term_misuse", "术语误用", "术语误用", False),
    "semantic_contradiction": ErrorTypeDef("semantic_contradiction", "语义逻辑矛盾", "语义逻辑矛盾", False),
    "financial_element_missing": ErrorTypeDef("financial_element_missing", "金融要素缺失", "金融要素缺失", False),
    "attribute_missing": ErrorTypeDef("attribute_missing", "属性值缺失", "属性值缺失错误", False),
    "format_error": ErrorTypeDef("format_error", "格式错误", "格式错误", False),
}

ZH_LABELS = {code: definition.zh_label for code, definition in ERROR_TYPES.items()}


def zh_label(error_type: str) -> str:
    return ZH_LABELS.get(error_type, error_type or "—")