# -*- coding: utf-8 -*-
"""D 展示层：人工复核状态机与持久化。

复核记录写在与 C 产物同级的 ``review.json``，不修改 check_result.json 等
三份产物，因此 C 的 manifest 哈希校验不受影响，复核轨迹本身可追溯、可回流评测。
"""
from __future__ import annotations

import json
import math
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
        if not any((self.run_dir / name).is_file() for name in ("check_result.json", "text_review.json")):
            raise ValueError("复核记录必须放在包含 check_result.json 或 text_review.json 的运行目录下")
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

    def set(self, finding_id: str, status: str, note: str = "", reviewer: str = "", *, duration_seconds: float | None = None) -> dict[str, Any]:
        if status not in REVIEW_STATUSES:
            raise ValueError(f"非法复核状态：{status!r}，允许 {REVIEW_STATUSES}")
        records = self.load()
        previous = records.get(finding_id, {})
        if duration_seconds is not None and (not math.isfinite(duration_seconds) or duration_seconds < 0):
            raise ValueError("复核耗时必须是非负有限数")
        entry = {
            "status": status,
            "note": (note or "").strip(),
            "reviewer": (reviewer or "").strip(),
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "duration_seconds": round(duration_seconds, 3) if duration_seconds is not None else None,
            "total_duration_seconds": round(float(previous.get("total_duration_seconds") or 0) + (duration_seconds or 0), 3),
            "timing_method": "explicit_start_to_save_wall_clock" if duration_seconds is not None else "not_measured",
            "history": previous.get("history", []) + [
                {"status": previous.get("status", "unreviewed"),
                 "reviewer": previous.get("reviewer", ""),
                 "duration_seconds": previous.get("duration_seconds"),
                 "updated_at": previous.get("updated_at", "")}
            ] if previous else [],
        }
        records[finding_id] = entry
        self._save(records)
        return entry

    def stats(self, finding_ids=None) -> dict[str, int]:
        counts = {status: 0 for status in REVIEW_STATUSES}
        for identity, entry in self.load().items():
            if finding_ids is not None and identity not in finding_ids:
                continue
            status = entry.get("status", "unreviewed")
            counts[status] = counts.get(status, 0) + 1
        counts["total"] = sum(counts.values())
        return counts
