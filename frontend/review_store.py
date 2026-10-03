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

    def timing_report(self) -> dict:
        """按复核人聚合实测耗时；未计时条目明确统计，不当作 0 秒。"""
        records = self.load()
        reviewers: dict[str, dict[str, float]] = {}
        total_seconds = 0.0
        measured = unmeasured = 0
        by_status = {status: {"count": 0, "seconds": 0.0} for status in REVIEW_STATUSES}
        for entry in records.values():
            status = entry.get("status", "unreviewed")
            by_status[status]["count"] += 1
            timed = entry.get("timing_method") == "explicit_start_to_save_wall_clock"
            duration = entry.get("total_duration_seconds")
            if timed and isinstance(duration, (int, float)) and math.isfinite(duration):
                measured += 1
                total_seconds += duration
                by_status[status]["seconds"] += duration
                reviewer = (entry.get("reviewer") or "").strip() or "未署名"
                bucket = reviewers.setdefault(reviewer, {"items": 0, "seconds": 0.0, "last_updated": ""})
                bucket["items"] += 1
                bucket["seconds"] += duration
                if entry.get("updated_at", "") > bucket["last_updated"]:
                    bucket["last_updated"] = entry["updated_at"]
            else:
                unmeasured += 1
        return {
            "total_records": len(records),
            "measured_items": measured,
            "unmeasured_items": unmeasured,
            "total_seconds": round(total_seconds, 3),
            "by_reviewer": {name: {**bucket, "seconds": round(bucket["seconds"], 3),
                                   "avg_seconds": round(bucket["seconds"] / max(bucket["items"], 1), 3)}
                            for name, bucket in sorted(reviewers.items())},
            "by_status": {status: {"count": value["count"],
                                   "seconds": round(value["seconds"], 3)}
                          for status, value in by_status.items()},
            "timing_note": "未计时条目单独统计，不视为 0 秒；total_seconds 只累计已计时记录。",
        }

    def export_rows(self) -> list[dict]:
        """复核记录扁平化，供 CSV 导出。"""
        rows = []
        for identity, entry in sorted(self.load().items()):
            rows.append({
                "finding_id": identity,
                "status": entry.get("status", "unreviewed"),
                "reviewer": entry.get("reviewer", ""),
                "note": entry.get("note", ""),
                "duration_seconds": entry.get("duration_seconds"),
                "total_duration_seconds": entry.get("total_duration_seconds"),
                "timing_method": entry.get("timing_method", "not_measured"),
                "updated_at": entry.get("updated_at", ""),
            })
        return rows
