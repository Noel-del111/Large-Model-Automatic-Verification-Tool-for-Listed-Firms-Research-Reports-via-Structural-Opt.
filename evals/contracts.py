# -*- coding: utf-8 -*-
"""审查操作合同（借鉴 FinRiskAtlas 的评测单元设计）。

论文把每个评测族写成一个显式合同 Γ = (c, I, d, Y, s)：能力、可见信息制度、
决策对象、产出结构与评分协议。我们把同样的写法用于自己的五个核查操作，
好处是：操作边界清楚、证据范围明确、评分口径不再含糊，
并且能把"证据不足时应该去取证"这件事变成可评测的输出。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass(frozen=True)
class OperationContract:
    key: str                     # 操作标识
    capability: str              # c：测什么能力
    visible: List[str]           # I：可见证据（制度）
    decision: str                # d：决策对象
    artifact: str                # Y：要求产出的结构
    scoring: List[str]           # s：评分协议
    gate: str = ""               # 达标门槛（来自分工文档）


OPERATIONS: List[OperationContract] = [
    OperationContract(
        key="value_consistency",
        capability="核对研报中的数值与财报/公告是否一致",
        visible=["研报解析结果（数值+单位+期间+页码）", "对应财报同口径数值"],
        decision="该数值是否可判定为错误",
        artifact="结论=确认错误/待人工确认/未发现问题；附研报页码与财报页码",
        scoring=["span 级 precision/recall/F1", "证据定位准确率", "正确内容误报率"],
        gate="精确率≥90%，召回≥80%，误报≤10%",
    ),
    OperationContract(
        key="unit_scale",
        capability="核对数值单位与量级（万元/亿元、%与小数）",
        visible=["研报数值原文与单位", "财报单位与口径说明"],
        decision="单位是否错误或量级失真",
        artifact="结论 + 建议修改值 + 换算依据",
        scoring=["单位类错误的召回与误报", "换算正确率"],
        gate="精确率≥90%",
    ),
    OperationContract(
        key="period_match",
        capability="核对报告期与数据期间是否匹配",
        visible=["研报期间表述", "财报披露期间"],
        decision="期间是否错配或时间信息非法",
        artifact="结论 + 正确期间 + 依据页码",
        scoring=["期间类错误 P/R/F1", "时间非法检出率"],
        gate="召回≥80%",
    ),
    OperationContract(
        key="caliber",
        capability="判断口径（归母/扣非、单季/累计）是否混用",
        visible=["研报指标名称与口径词", "财报同一指标的定义"],
        decision="口径是否可判定；不能判定时应标记证据不足",
        artifact="结论 + 口径判定依据；证据不足时给出取证请求",
        scoring=["口径类错误 P/R/F1", "证据不足判定的准确率"],
        gate="误报≤10%",
    ),
    OperationContract(
        key="evidence_request",
        capability="证据状态控制：继续下结论还是先去取证",
        visible=["当前已收集到的证据与质量状态（含解析失败页）"],
        decision="Proceed（可下结论）还是 Ask（先补证据）",
        artifact="决策 + 需求证据清单（文件、字段、期间）",
        scoring=["BAcc（两分支均衡准确率）", "ERA（证据请求对齐率）",
                 "CRA（应取证样本上的对齐率）"],
        gate="解析失败页不得作为证据；证据不足时不强行给正确值",
    ),
]

BY_KEY = {item.key: item for item in OPERATIONS}


def describe() -> str:
    lines = ["审查操作合同", ""]
    for item in OPERATIONS:
        lines.append(f"[{item.key}] {item.capability}")
        lines.append(f"  可见证据：{'；'.join(item.visible)}")
        lines.append(f"  决策对象：{item.decision}")
        lines.append(f"  产出：{item.artifact}")
        lines.append(f"  评分：{'；'.join(item.scoring)}")
        if item.gate:
            lines.append(f"  门槛：{item.gate}")
        lines.append("")
    return "\n".join(lines)
