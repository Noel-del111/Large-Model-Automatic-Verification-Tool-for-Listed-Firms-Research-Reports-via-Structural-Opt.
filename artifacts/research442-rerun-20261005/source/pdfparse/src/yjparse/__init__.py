"""研报 PDF 解析模块（yjparse）。

核心目标：把任意研报 PDF 转成带页码与坐标的结构化结果，并对解析质量给出可解释的判定。
"""

__version__ = "0.1.0"

from .contract import (  # noqa: F401
    SCHEMA_VERSION,
    BBox,
    Block,
    Cell,
    DocumentMeta,
    EngineInfo,
    Page,
    ParseResult,
    QualityReport,
    Sentence,
    validate_result,
)
