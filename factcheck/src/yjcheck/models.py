"""C 模块公共契约。金额使用十进制字符串，PDF 页码从 1 开始。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from hashlib import sha256
from typing import Any

# Paired check_result contract. The independent text-review schema is unchanged.
SCHEMA_VERSION = "1.1.0"
STATUSES = ("confirmed_error", "needs_review", "no_issue")
STATUS_LABELS = dict(zip(STATUSES, ("已确认错误", "待人工确认", "未发现问题")))


@dataclass
class Evidence:
    doc_id: str = ""
    run_id: str = ""
    sha256: str = ""
    file: str = ""
    block_id: str = ""
    page: int | None = None
    bbox: list[float] | None = None
    paragraph: int | None = None
    text: str = ""
    char_start: int | None = None
    char_end: int | None = None
    quality: str = "ok"
    notes: list[str] = field(default_factory=list)


@dataclass
class Block:
    block_id: str
    text: str
    page: int | None = None
    bbox: list[float] | None = None
    paragraph: int | None = None
    type: str = "text"
    status: str = "ok"
    notes: list[str] = field(default_factory=list)
    cells: list[dict[str, Any]] = field(default_factory=list)
    page_size: list[float] | None = None

    def evidence(self, doc: Document, text: str | None = None,
                 start: int | None = None, end: int | None = None) -> Evidence:
        return Evidence(doc.doc_id, doc.run_id, doc.sha256, doc.path, self.block_id,
                        self.page, self.bbox, self.paragraph,
                        self.text if text is None else text, start, end, self.status,
                        list(self.notes))


@dataclass
class Document:
    doc_id: str
    sha256: str
    run_id: str
    path: str
    role: str
    company: str = ""
    period: str = ""
    blocks: list[Block] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return "\n".join(b.text for b in self.blocks)


@dataclass
class Fact:
    metric: str
    value: str
    unit: str
    period: str
    company: str
    basis: str = "unknown"  # before / after / change / reported / unknown
    scope: str = "consolidated"  # consolidated / parent / unknown
    currency: str = "CNY"
    text: str = ""
    evidence: list[Evidence] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)
    fact_id: str = ""

    def __post_init__(self) -> None:
        self.value = str(self.value)
        if not self.fact_id:
            identity = repr((self.company, self.metric, self.value, self.unit,
                             self.period, self.basis, self.scope, self.currency,
                             [(e.doc_id, e.sha256, e.run_id, e.page, e.paragraph, e.block_id, e.char_start, e.text) for e in self.evidence]))
            self.fact_id = sha256(identity.encode()).hexdigest()[:20]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Finding:
    claim: Fact
    status: str
    error_type: str = ""
    rule_id: str = ""
    message: str = ""
    suggestion: str = ""
    suggested_value: str | None = None
    evidence: list[Fact] = field(default_factory=list)
    calculation: dict[str, Any] = field(default_factory=dict)
    review_status: str = "unreviewed"

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["id"] = self.claim.fact_id
        result["status_label"] = STATUS_LABELS[self.status]
        return result
