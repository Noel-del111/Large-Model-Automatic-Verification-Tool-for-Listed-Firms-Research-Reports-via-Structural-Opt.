# -*- coding: utf-8 -*-
"""D 展示层：人工复核状态机与持久化。

复核记录写在与 C 产物同级的 ``review.json``，不修改 check_result.json 等
三份产物，因此 C 的 manifest 哈希校验不受影响，复核轨迹本身可追溯、可回流评测。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

REVIEW_STATUSES = ("unreviewed", "confirmed", "dismissed", "contested")
REVIEW_LABELS = {
    "unreviewed": "未复核",
    "confirmed": "确认（采纳系统判定）",
    "dismissed": "驳回（误报）",
    "contested": "存疑（与系统分歧）",
}


class ReviewStore:
    """按 finding_id 保存复核结论；写入原子化，损坏文件自动回退为空记录。"""

    def __init__(self, run_dir: str | Path):
        self.run_dir = Path(run_dir)
        if not (self.run_dir / "check_result.json").is_file():
            raise ValueError("复核记录必须放在包含 check_result.json 的运行目录下")
        self.path = self.run_dir / "review.json"

    def load(self) -> dict[str, dict[str, Any]]:
        if not self.path.is_file():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {k: v for k, v in data.items() if isinstance(v, dict)}

    def _save(self, records: dict[str, dict[str, Any]]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def get(self, finding_id: str) -> dict[str, Any]:
        return self.load().get(finding_id, {"status": "unreviewed", "note": '', "reviewer": ''})

    def set(self, finding_id: str, status: str, note: str = "", reviewer: str = "") -> dict[str, Any]:
        if status not in REVIEW_STATUSES:
            raise ValueError(f"非法复核状态：{status!r}，允许 {REVIEW_STATUSES}")
        records = self.load()
        previous = records.get(finding_id, {})
        entry = {
            "status": status,
            "note": (note or "").strip(),
            "reviewer": (reviewer or "").strip(),
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "history": previous.get("history", []) + [
                {"status": previous.get("status", "unreviewed"),
                 "reviewer": previous.get("reviewer", ""),
                 "updated_at": previous.get("updated_at", "")}
            ] if previous else [],
        }
        records[finding_id] = entry
        self._save(records)
        return entry

    def stats(self) -> dict[str, int]:
        counts = {status: 0 for status in REVIEW_STATUSES}
        for entry in self.load().values():
            status = entry.get("status", "unreviewed")
            counts[status] = counts.get(status, 0) + 1
        counts["total"] = sum(counts.values())
        return counts