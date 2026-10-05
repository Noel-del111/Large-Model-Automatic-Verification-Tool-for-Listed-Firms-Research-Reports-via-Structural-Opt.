"""通用小工具：哈希、时间、标识、目录。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


def sha256_file(path: str | os.PathLike) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def local_tz_name() -> str:
    return time.strftime("%Z") or "unknown"


def new_run_id() -> str:
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    return f"{stamp}_{uuid.uuid4().hex[:6]}"


def safe_name(name: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff.\-]+", "_", name, flags=re.UNICODE)
    return cleaned.strip("_") or "doc"


def ensure_dir(path: str | os.PathLike) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def write_json(path: str | os.PathLike, payload, indent: int = 2) -> Path:
    p = Path(path)
    ensure_dir(p.parent)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=indent)
        fh.write("\n")
    return p


def read_json(path: str | os.PathLike):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)
