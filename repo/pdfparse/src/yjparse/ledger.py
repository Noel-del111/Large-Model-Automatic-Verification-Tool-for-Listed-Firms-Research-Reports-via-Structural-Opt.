"""运行留痕：四类结构化日志 + 运行清单。

对应竞赛对“完整记录文件访问、工具调用、计算过程和结果生成”的要求：
  access.jsonl   文件访问
  tools.jsonl    工具与模型调用
  compute.jsonl  计算过程与规则判定
  results.jsonl  结果生成
  manifest.json  一次运行的环境、版本、权重与参数快照
"""

from __future__ import annotations

import importlib.metadata as md
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from .utils import ensure_dir, local_tz_name, now_iso, sha256_text, write_json

TRACKED_PACKAGES = [
    "PyMuPDF",
    "pdfplumber",
    "docling",
    "mineru",
    "paddleocr",
    "pydantic",
]


def _package_versions() -> Dict[str, str]:
    versions: Dict[str, str] = {}
    for name in TRACKED_PACKAGES:
        try:
            versions[name] = md.version(name)
        except Exception:
            continue
    return versions


def _git_commit(repo_dir: Optional[Path]) -> str:
    if not repo_dir:
        return ""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            timeout=5,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


class Ledger:
    """按运行时序追加写日志，破坏性最小、便于审计。"""

    def __init__(self, log_dir: str | os.PathLike, run_id: str, repo_dir: Optional[Path] = None):
        self.run_id = run_id
        self.log_dir = ensure_dir(log_dir)
        self.repo_dir = repo_dir
        self.started_at = now_iso()

    def _write(self, stream: str, payload: Dict[str, Any]) -> None:
        record = {"ts": now_iso(), "run_id": self.run_id, **payload}
        path = self.log_dir / f"{stream}.jsonl"
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    # ---- 四类日志 ----
    def access(self, action: str, path: str | os.PathLike, sha256: str = "",
               actor: str = "pipeline", extra: Optional[Dict[str, Any]] = None) -> None:
        self._write("access", {
            "action": action,
            "path": str(path),
            "sha256": sha256,
            "actor": actor,
            "extra": extra or {},
        })

    def tool(self, tool: str, version: str = "", args: Optional[Dict[str, Any]] = None,
             exit_code: int = 0, duration_s: Optional[float] = None,
             extra: Optional[Dict[str, Any]] = None) -> None:
        self._write("tools", {
            "tool": tool,
            "version": version,
            "args": args or {},
            "exit_code": exit_code,
            "duration_s": round(duration_s, 4) if duration_s is not None else None,
            "extra": extra or {},
        })

    def compute(self, op: str, page: Optional[int] = None,
                inputs: Optional[Dict[str, Any]] = None,
                result: Optional[Dict[str, Any]] = None,
                decision: str = "") -> None:
        self._write("compute", {
            "op": op,
            "page": page,
            "inputs": inputs or {},
            "result": result or {},
            "decision": decision,
        })

    def artifact(self, path: str | os.PathLike, rows: Optional[int] = None,
                 sha256: str = "", kind: str = "") -> None:
        self._write("results", {
            "artifact": str(path),
            "kind": kind,
            "rows": rows,
            "sha256": sha256,
        })

    # ---- 运行清单 ----
    def manifest(self, *, params: Dict[str, Any], thresholds: Dict[str, Any],
                 models: Optional[Iterable[Dict[str, Any]]] = None,
                 inputs: Optional[Iterable[Dict[str, Any]]] = None,
                 artifacts: Optional[Iterable[Dict[str, Any]]] = None,
                 random_seed: int = 0) -> Path:
        payload = {
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": now_iso(),
            "timezone": local_tz_name(),
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
            "git_commit": _git_commit(self.repo_dir),
            "dependency_versions": _package_versions(),
            "params": params,
            "thresholds": thresholds,
            "thresholds_sha256": sha256_text(json.dumps(thresholds, sort_keys=True, ensure_ascii=False)),
            "models": list(models or []),
            "inputs": list(inputs or []),
            "artifacts": list(artifacts or []),
            "random_seed": random_seed,
            "offline": True,
        }
        path = write_json(self.log_dir / "manifest.json", payload)
        self._write("results", {"artifact": str(path), "kind": "manifest"})
        return path
