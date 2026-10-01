"""配置加载：阈值与引擎参数。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent.parent
DEFAULT_THRESHOLDS = PROJECT_ROOT / "configs" / "thresholds.json"


def load_thresholds(path: str | Path | None = None) -> Dict[str, Any]:
    target = Path(path) if path else DEFAULT_THRESHOLDS
    with open(target, "r", encoding="utf-8") as fh:
        return json.load(fh)


def project_root() -> Path:
    return PROJECT_ROOT
