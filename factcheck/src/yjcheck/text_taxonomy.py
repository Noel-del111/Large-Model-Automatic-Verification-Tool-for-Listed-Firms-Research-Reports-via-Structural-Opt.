"""The fifteen labels present in the released FinED-Bench JSON files."""
from __future__ import annotations

FINED_ERROR_TYPES = (
    "时间信息非法", "冗余语句", "格式错误", "数值缺失", "属性值缺失错误",
    "术语误用", "法规引用错误", "模糊语言", "数值单位错误", "金融要素缺失",
    "语义逻辑矛盾", "时间矛盾", "数值不一致错误", "计算错误", "不一致条款",
)

# Summaries of Appendix B in the local FinED-Bench paper (PDF pp. 12–14).
# Operational evidence thresholds belong to the caller's prompt, not the paper.
FINED_TYPE_DEFINITIONS = (
    "时间信息非法：日期或时间本身不成立；冗余语句：同一信息不必要地重复；"
    "格式错误：电话、日期等属性值不符合明确的取值格式，非普通排版风格；"
    "数值缺失：明确数值位置为空；属性值缺失错误：名称、网址、类别等非数值属性值为空；"
    "术语误用：金融或行业术语使用不当导致含义错误；法规引用错误：法律文件或条款引用有误；"
    "模糊语言：关键表达有实质不同的解释；数值单位错误：数字的单位或量纲错误；"
    "金融要素缺失：当前金融事项必需的组成信息缺失，不等同于单个数值空槽；"
    "语义逻辑矛盾：同一条件下命题不能同时成立，包括数字与高于/低于等比较叙述相反；"
    "时间矛盾：事件先后、期间或时间关系冲突；数值不一致错误：同一对象、期间、指标与口径存在冲突数值；"
    "计算错误：明确的计算关系或合计不成立；不一致条款：同一情形下不同条款的规定互斥。"
)

_ALIASES = dict(zip((
    "invalid_time", "redundant_statement", "format_error", "numeric_missing", "attribute_missing",
    "term_misuse", "legal_reference", "ambiguous_expression", "unit_term_mismatch", "financial_element_missing",
    "semantic_contradiction", "time_conflict", "numeric_inconsistency", "calc_error", "clause_conflict",
), FINED_ERROR_TYPES))
_ALIASES.update(dict(zip((
    "Illegal Time", "Redundant Statements", "Value Format Errors", "Numerical Missing",
    "Non-Numerical Attribute Value Missing", "Terminology Misuse", "Incorrect Legal Reference",
    "Ambiguous Expression", "Numerical Unit Error", "Omitted Financial Element", "Conflicting Expression",
    "Time Contradiction", "Numerical Inconsistency", "Calculation Error", "Clause Conflict",
), FINED_ERROR_TYPES)))
_ALIASES.update({"模糊表达": "模糊语言", "条款冲突": "不一致条款", "单位错误": "数值单位错误",
                 "属性值缺失": "属性值缺失错误", "unit": "数值单位错误"})
_ALIASES = {key.casefold(): value for key, value in _ALIASES.items()}


def canonical_error_type(value: str) -> str:
    """Preserve unknown labels so the caller can reject them explicitly."""
    value = str(value).strip()
    return value if value in FINED_ERROR_TYPES else _ALIASES.get(value.casefold(), value)
