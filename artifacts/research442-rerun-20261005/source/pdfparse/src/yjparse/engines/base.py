"""引擎统一接口与注册表。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

from ..contract import Page


class EngineUnavailable(RuntimeError):
    """引擎依赖缺失或不可用。"""


@dataclass
class EngineCapabilities:
    coordinates: bool = True
    tables: bool = False
    reading_order: bool = False
    scanned_ocr: bool = False
    offline: bool = True
    notes: List[str] = field(default_factory=list)


class BaseEngine:
    name = "base"
    install_hint = ""

    def __init__(self, **params: Any):
        self.params = params

    # ---- 元信息 ----
    def version(self) -> str:
        return ""

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities()

    def info(self) -> Dict[str, Any]:
        caps = self.capabilities()
        return {
            "name": self.name,
            "version": self.version(),
            "coordinates": caps.coordinates,
            "tables": caps.tables,
            "reading_order": caps.reading_order,
            "scanned_ocr": caps.scanned_ocr,
            "offline": caps.offline,
            "notes": caps.notes,
        }

    @classmethod
    def available(cls) -> bool:
        return False

    # ---- 能力 ----
    def parse(self, path: Path, doc_id: str, ledger=None) -> List[Page]:
        raise NotImplementedError

    def page_texts(self, path: Path) -> Dict[int, str]:
        """原生文本层逐页文本，用于交叉校验。不支持时返回空字典。"""
        return {}


REGISTRY: Dict[str, Type[BaseEngine]] = {}


def register(cls: Type[BaseEngine]) -> Type[BaseEngine]:
    REGISTRY[cls.name] = cls
    return cls


def registry() -> Dict[str, Dict[str, Any]]:
    """返回所有引擎的可用状态，供 doctor 命令展示。"""
    out: Dict[str, Dict[str, Any]] = {}
    for name, cls in REGISTRY.items():
        available = cls.available()
        entry: Dict[str, Any] = {
            "available": available,
            "install_hint": cls.install_hint,
        }
        if available:
            try:
                entry.update(cls().info())
            except Exception as exc:  # pragma: no cover - 环境相关
                entry["available"] = False
                entry["install_hint"] = f"初始化失败：{exc}"
        out[name] = entry
    return out


def build_engine(name: str, **params: Any) -> BaseEngine:
    key = (name or "").lower()
    if key not in REGISTRY:
        raise EngineUnavailable(f"未知引擎：{name}，可选：{', '.join(sorted(REGISTRY))}")
    cls = REGISTRY[key]
    if not cls.available():
        raise EngineUnavailable(f"引擎 {key} 不可用。{cls.install_hint}")
    return cls(**params)


def _ensure_import():
    """延迟导入辅助：把注册动作放在模块导入时统一完成。"""
    import importlib

    for module in ("pymupdf_engine", "pdfplumber_engine", "docling_engine", "mineru_engine"):
        try:
            importlib.import_module(f"{__package__}.{module}")
        except Exception:  # pragma: no cover - 单个引擎导入失败不应影响其他引擎
            continue


_ensure_import()
