"""Generate full-run deliverables after the existing inference worker finishes.

This optional observer performs no inference or budget migration. On Windows it
can hold a handle to the exact existing worker, so a stale status file cannot be
mistaken for proof that the job is still running. It never restarts the worker.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evals"))
from audit_full_acceptance import audit
from build_model_report import link, load_runs, render
from run_v2 import write_json


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def selected_splits(scope):
    if scope not in {"full", "eval200"}:
        raise ValueError("Unknown finalization scope")
    return ("eval_oct05",) if scope == "eval200" else ("eval_oct05", "dev")


def reports_available(data, scope="full"):
    return all((Path(data) / ("full-" + split) / "score.json").is_file() for split in selected_splits(scope))


def collect(data, out, scope="full"):
    data, out = Path(data), Path(out)
    splits = selected_splits(scope)
    if not reports_available(data, scope):
        raise ValueError("指定完整队列的评分产物尚未生成，不能提前生成报告")
    result = audit(data)
    write_json(data / "full-acceptance-audit.json", result)
    problems = result["integrity_problems"] + [problem for run in result["runs"].values() for problem in run["integrity_problems"]]
    if problems:
        raise ValueError("全量结果完整性核验未通过：" + "; ".join(problems))
    budget = result["budget"]
    ledger = {"captured_at": result["created_at"], "budget_cny": budget["limit_cny"],
              "accounted_cny": budget["accounted_including_reserved_cny"], "calls": budget["calls"],
              "uncertain_calls": sum(count for state, count in budget["call_status_counts"].items() if state != "ok"),
              "call_status_counts": budget["call_status_counts"], "cost_basis": "configured_rate_upper_bound"}
    ledger_path = data / ("ledger-eval200-final.json" if scope == "eval200" else "ledger-full-final.json")
    write_json(ledger_path, ledger)
    labels = {"eval_oct05": "开发阶段200篇", "dev": "开发扩展598篇"}
    scopes = {"eval_oct05": "development_stage", "dev": "dev_expansion"}
    runs = load_runs([labels[s] + "=" + str(data / ("full-" + s)) for s in splits],
                     [labels[s] + "=" + scopes[s] for s in splits])
    content = render(runs, out, ledger_path, [run["label"] for run in runs])
    complete = (result["runs"]["eval_oct05"]["state"] == "complete" if scope == "eval200"
                else result["full_model_execution_and_scoring_complete"])
    count = 200 if scope == "eval200" else 798
    result_line = (f"{count} 篇的三组执行及评分完整性检查通过。" if complete else
                   f"评分保留了全部 {count} 篇计划分母，但存在未完成的执行，验收尚未通过；失败明细见审计文件。")
    title = "# 第二版 200 篇开发阶段实验与交接报告" if scope == "eval200" else "# 第二版全量模型评测报告"
    scope_line = ("用户最新指令为完成当前 200 篇后停止并提交进度 PR。598 篇开发扩展未执行，等待进一步指令，不计作完成。"
                  if scope == "eval200" else "本轮范围为 200 篇冻结评测与 598 篇开发扩展，分别报告，不混合为独立测试分数。")
    introduction = (title + "\n\n" + result_line + "\n\n" + scope_line +
                    "199 篇 10 月 7 日保留集继续封存。用户已确认先完成模型全量评测；"
                    "正常文本人审、语义证据支持及真人复核效率留待后续，仍列为未测量。")
    introduction += ("\n\n**范围说明：本次 200 篇属于开发阶段实验与阶段性验证，最终独立测试尚未进行。** "
                     "已实际完成模型推理和评分，没有训练或微调模型权重；不能表述成没有任何测试。"
                     "历史划分 ID `eval_oct05` 及运行时冻结的参数保持原样，当前交接不将该批结果作为最终测试成绩。"
                     "如果据此继续修改提示或规则，该批只用于验证和回归。"
                     "598 篇开发池此前已有小样本和离线实验，尚未完成该池真实模型全量扩跑。"
                     "当前 200 篇也不是项目累计使用过的全部样本。工程单元测试不等于最终模型能力测试。")
    if result.get("budget_amendment"):
        introduction += ("\n\n运行期间用户将累计上限由 50 元提高至 100 元，历史费用保留。"
                         "本批仍使用启动时冻结的模型、提示及源码；原运行配置中 50 元是启动快照，"
                         "后续授权以共享账本及预算变更记录为准。")
    content = content.replace("# 第二版真实模型阶段报告", introduction, 1)
    content += "\n业务配对结果见 " + link("独立配对回归报告", ROOT / "docs/V2_PAIRED_RESULTS.md", out) + "。\n"
    content += "完整性证据见 " + link("执行范围及完整性审计", data / "full-acceptance-audit.json", out) + "。\n"
    content += "数据、版本、交付和未完成项见 " + link("本次更新的范围与边界", ROOT / "docs/V2_SCOPE_AND_HANDOFF.md", out) + "。\n"
    out.parent.mkdir(parents=True, exist_ok=True)
    temporary = out.with_suffix(out.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(out)
    state = {"created_at": datetime.now(timezone.utc).isoformat(),
             "state": "reports_ready" if complete else "reports_ready_with_execution_failures",
             "requested_scope": scope, "scope_execution_and_scoring_complete": complete,
             "full_model_execution_and_scoring_complete": result["full_model_execution_and_scoring_complete"],
             "report": str(out.resolve()), "report_sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
             "scoring_sha256": {split: hashlib.sha256((data / ("full-" + split) / "score.json").read_bytes()).hexdigest()
                                for split in splits},
             "new_model_calls_by_finalizer": 0, "budget": budget}
    write_json(data / ("eval200-finalization-status.json" if scope == "eval200" else "full-finalization-status.json"), state)
    return state


class WorkerHandle:
    """Keep one OS handle; do not mistake PID reuse for the original worker."""
    def __init__(self, pid):
        if os.name != "nt":
            raise ValueError("--wait-for-worker 当前仅支持 Windows；其他环境直接执行汇总命令")
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel.WaitForSingleObject.restype = wintypes.DWORD
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.handle = self.kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE only, no process mutation.
        if not self.handle:
            raise OSError(ctypes.get_last_error(), "无法取得指定任务的观察句柄；未尝试启动或终止任务")

    def alive(self):
        state = self.kernel.WaitForSingleObject(self.handle, 0)
        if state == 258:  # WAIT_TIMEOUT: the exact process is still alive.
            return True
        if state == 0:    # Signaled: process terminated.
            return False
        raise OSError(ctypes.get_last_error(), "无法读取任务状态；不能据此判断任务已结束")

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def wait_for_reports(data, worker, poll_seconds, scope="full"):
    last = None
    while not reports_available(data, scope):
        if not worker.alive():
            # A just-finished worker may have written the second score between
            # the first file check and the process-handle observation.
            if reports_available(data, scope):
                return
            raise RuntimeError("原进程已结束，但指定范围评分尚未齐全；保留进度，不自动重启或补发调用")
        progress = {}
        for split in selected_splits(scope):
            path = Path(data) / ("full-" + split) / "status.json"
            if path.exists():
                state = read(path)
                progress[split] = {key: state.get(key) for key in ("requested", "completed_documents", "stop_reason")}
        serialized = json.dumps(progress, ensure_ascii=False, sort_keys=True)
        if serialized != last:
            print(serialized, flush=True)
            last = serialized
        time.sleep(poll_seconds)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=ROOT / "data/v2")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--scope", choices=("full", "eval200"), default="full")
    parser.add_argument("--wait-for-worker", type=int)
    parser.add_argument("--poll-seconds", type=float, default=30)
    args = parser.parse_args(argv)
    if args.out is None:
        args.out = ROOT / "docs" / ("V2_EVAL200_RESULTS.md" if args.scope == "eval200" else "V2_FULL_RESULTS.md")
    if not 1 <= args.poll_seconds <= 60:
        parser.error("poll-seconds 必须在 1 到 60 秒之间")
    worker = None
    try:
        if args.wait_for_worker and not reports_available(args.data, args.scope):
            worker = WorkerHandle(args.wait_for_worker)
            print(json.dumps({"observer_started": True, "worker_pid": args.wait_for_worker,
                              "observer_pid": os.getpid(), "new_model_calls": 0}), flush=True)
            wait_for_reports(args.data, worker, args.poll_seconds, args.scope)
        result = collect(args.data, args.out, args.scope)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 0 if result["scope_execution_and_scoring_complete"] else 1
    except (ValueError, OSError, RuntimeError) as exc:
        failure = {"state": "finalization_needs_attention", "created_at": datetime.now(timezone.utc).isoformat(),
                   "reason": str(exc), "new_model_calls_by_finalizer": 0,
                   "inference_was_not_restarted": True}
        write_json(args.data / ("eval200-finalization-status.json" if args.scope == "eval200" else "full-finalization-status.json"), failure)
        print(json.dumps(failure, ensure_ascii=False), file=sys.stderr, flush=True)
        return 2
    finally:
        if worker is not None:
            worker.close()


if __name__ == "__main__":
    raise SystemExit(main())
