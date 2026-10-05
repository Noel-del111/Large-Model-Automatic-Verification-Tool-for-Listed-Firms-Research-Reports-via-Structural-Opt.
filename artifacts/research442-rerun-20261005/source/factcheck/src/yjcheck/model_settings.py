"""Read allowlisted local model settings without changing the process environment.

Environment entries take precedence, including explicit empty strings. Errors and
presence checks never include values. The local JSON belongs to ignored data/.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Mapping


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "data/v2/local_model_config.json"
ALLOWED_KEYS = (
    "YJCHECK_BASE_URL", "YJCHECK_MODEL", "YJCHECK_API_KEY", "YJCHECK_TIMEOUT", "YJCHECK_THINKING",
    "YJCHECK_REASONING_EFFORT",
    "YJCHECK_INPUT_CNY_PER_MTOK", "YJCHECK_OUTPUT_CNY_PER_MTOK", "YJCHECK_PRICE_SOURCE",
    "YJCHECK_PRICE_DATE", "YJCHECK_PRICE_VALID_UNTIL", "YJCHECK_CONTEXT_TOKENS", "YJCHECK_MAX_OUTPUT_TOKENS",
    "YJCHECK_BUDGET_CNY", "YJCHECK_MAX_RETRIES",
)


def validate_reasoning_settings(thinking: str = "", reasoning_effort: str = "") -> None:
    """Validate explicit controls without echoing untrusted configuration values.

    An omitted thinking setting leaves the provider's default behavior intact.
    Explicitly disabling thinking cannot be combined with a reasoning budget.
    """
    if thinking not in ("", "enabled", "disabled"):
        raise ValueError("YJCHECK_THINKING 只允许空值、enabled 或 disabled")
    if reasoning_effort not in ("", "low", "high", "max"):
        raise ValueError("YJCHECK_REASONING_EFFORT 只允许空值、low、high 或 max")
    if thinking == "disabled" and reasoning_effort:
        raise ValueError("YJCHECK_THINKING=disabled 时不能设置 YJCHECK_REASONING_EFFORT")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("本地模型配置包含重复字段")
        result[key] = value
    return result


def load_model_settings(*, config_path: Path | None = None,
                        environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return settings only; never log values, execute text, or inject env vars."""
    path = DEFAULT_CONFIG_PATH if config_path is None else Path(config_path)
    environment = os.environ if environ is None else environ
    local = {}
    try:
        with path.open("rb") as source:
            raw = source.read(65_537)
    except FileNotFoundError:
        raw = None
    except OSError:
        raise ValueError("无法读取本地模型配置文件") from None
    if raw is not None:
        if len(raw) > 65_536:
            raise ValueError("本地模型配置文件超过大小限制")
        try:
            local = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_object)
        except (ValueError, UnicodeError):
            raise ValueError("本地模型配置必须是有效且字段不重复的 JSON 对象") from None
        if not isinstance(local, dict) or any(key not in ALLOWED_KEYS for key in local):
            raise ValueError("本地模型配置只允许明确支持的 YJCHECK 配置字段")
        for key, value in local.items():
            if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                raise ValueError("本地模型配置字段须为字符串或数值：" + key)
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("本地模型配置数值须为有限数：" + key)
    return {key: environment[key] if key in environment else str(local[key])
            for key in ALLOWED_KEYS if key in environment or key in local}
