"""Read-only checks of the planned full evaluation, separate from inference.

Execution completion is not a detection-quality threshold or human sign-off.
The audit never reads model credentials, calls a model, or modifies its ledger.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evals"))
from run_v2 import DETECTORS, doc_id, path_complete, read_jsonl, write_json

EXPECTED = {"eval_oct05": 200, "dev": 598}


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_budget_amendment(data, frozen, root=ROOT):
    """Accept a budget-only experiment amendment with ledger and source evidence.

    The already running evaluation retains its original run configuration.
    Only the subsequent development run uses the amended budget/source record.
    """
    data, root = Path(data), Path(root)
    path = data / "release/budget-amendment-100.json"
    if not path.exists():
        return {}, 50, None
    amendment = read(path)
    authorization = amendment["authorization"]
    if (amendment.get("schema_version") != "budget-amendment/1.0"
            or amendment.get("applies_to_splits") != ["dev"]
            or authorization.get("old_limit_cny") != 50 or authorization.get("new_limit_cny") != 100
            or amendment.get("base_freeze_sha256") != digest(data / "release/freeze_manifest.json")):
        raise ValueError("预算变更记录范围或原始冻结指纹不符")
    changes = amendment.get("code_changes", {})
    if set(changes) != {"factcheck/src/yjcheck/model_runtime.py", "evals/run_v2.py"}:
        raise ValueError("预算变更包含未声明的源码范围")
    for name, change in changes.items():
        if (change.get("before_sha256") != frozen["source_files"].get(name)
                or change.get("after_sha256") != digest(root / name)):
            raise ValueError("预算变更源码哈希不符：" + name)
    if digest(data / "release/budget100-inference.patch") != amendment.get("patch_sha256"):
        raise ValueError("预算变更补丁哈希不符")
    code_files = sorted((root / "factcheck/src/yjcheck").glob("*.py")) + [root / "evals/run_v2.py", root / "evals/baseline.py"]
    source_hash = hashlib.sha256("".join(digest(p) for p in code_files).encode()).hexdigest()
    if source_hash != amendment.get("new_runner_source_hash"):
        raise ValueError("预算变更之外的推理源码发生变化")
    ledger_path = data / "model_usage.sqlite3"
    with closing(sqlite3.connect(ledger_path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        actual = db.execute("SELECT old_limit_micro,new_limit_micro,authorization_basis FROM budget_authorizations WHERE authorization_id=?",
                            (authorization["authorization_id"],)).fetchone()
    if actual != (50_000_000, 100_000_000, authorization["authorization_basis"]):
        raise ValueError("预算变更缺少匹配的共享账本授权")
    amended = deepcopy(frozen)
    amended["runner_source_hash"] = source_hash
    amended["runtime_without_credentials"]["YJCHECK_BUDGET_CNY"] = "100"
    return {"dev": amended}, 100, amendment


def runtime_problems(config, frozen):
    settings = frozen.get("runtime_without_credentials", {})
    if not settings:
        return ["frozen_runtime_settings_missing"]
    expected_request = {"thinking": settings.get("YJCHECK_THINKING", ""),
                        "reasoning_effort": settings.get("YJCHECK_REASONING_EFFORT", ""),
                        "timeout": float(settings.get("YJCHECK_TIMEOUT", 30))}
    problems = []
    if config.get("model") != settings.get("YJCHECK_MODEL") or config.get("model_request") != expected_request:
        problems.append("model_or_request_settings_differ_from_freeze")
    endpoint_hash = hashlib.sha256(settings.get("YJCHECK_BASE_URL", "").encode()).hexdigest()
    if config.get("endpoint_hash") != endpoint_hash:
        problems.append("endpoint_differs_from_freeze")
    for key in ("input_cny_per_mtok", "output_cny_per_mtok", "price_source", "price_date",
                "context_tokens", "max_output_tokens", "budget_cny", "max_retries", "price_valid_until"):
        if str(config.get("runtime", {}).get(key, "")) != str(settings.get("YJCHECK_" + key.upper(), "")):
            problems.append("runtime_differs_from_freeze:" + key)
    return problems


def inspect_run(directory, rows, frozen, split):
    """Check document identity and completeness independently of status counters."""
    directory = Path(directory)
    expected = {doc_id(row): row for row in rows}
    problems, arms = [], {}
    config_path = directory / "run_config.json"
    queue_path = directory / "queue.json"
    if not config_path.exists() or not queue_path.exists():
        return {"planned_documents": len(rows), "state": "not_started", "execution_complete": False,
                "integrity_problems": [], "arms": {}, "score_available": False}
    config, queue = read(config_path), read(queue_path)
    problems.extend(runtime_problems(config, frozen))
    if config.get("source_hash") != frozen["runner_source_hash"]:
        problems.append("source_fingerprint_differs_from_freeze")
    if config.get("inputs_sha256") != frozen["inputs_sha256"][split]:
        problems.append("input_fingerprint_differs_from_freeze")
    if config.get("examples_sha256") != frozen["examples_sha256"]:
        problems.append("examples_fingerprint_differs_from_freeze")
    if config.get("mode") != "model" or config.get("detectors") != list(DETECTORS):
        problems.append("comparison_groups_differ_from_plan")
    planned = queue.get("document_ids", [])
    if len(planned) != len(set(planned)) or set(planned) != set(expected):
        problems.append("queue_missing_extra_or_duplicate_documents")
    completed_all = set(expected)
    for arm in DETECTORS:
        predictions = [read(path) for path in sorted((directory / "predictions" / arm).glob("*.json"))]
        identities = [doc_id(report) for report in predictions]
        duplicates = sorted(key for key, count in Counter(identities).items() if count > 1)
        extra = sorted(set(identities) - set(expected))
        missing = sorted(set(expected) - set(identities))
        completed, failed, bad_content = set(), [], []
        for report in predictions:
            identity = doc_id(report)
            if identity not in expected:
                continue
            source_hash = hashlib.sha256(expected[identity]["content"].encode()).hexdigest()
            if report.get("source_content_sha256") != source_hash:
                bad_content.append(identity)
            if report.get("detector") != arm:
                problems.append(arm + ":wrong_detector_identity:" + identity)
            if path_complete(report, "model"):
                completed.add(identity)
            else:
                failed.append(identity)
        if duplicates or extra or bad_content:
            problems.append(arm + ":prediction_identity_or_content_mismatch")
        completed_all &= completed
        arms[arm] = {"attempted_documents": len(set(identities) & set(expected)),
                     "completed_documents": len(completed), "failed_document_ids": sorted(set(failed)),
                     "missing_document_ids": missing, "extra_document_ids": extra,
                     "duplicate_document_ids": duplicates, "wrong_content_document_ids": sorted(set(bad_content))}
    score_path = directory / "score.json"
    score = read(score_path) if score_path.exists() else None
    if score is not None:
        if score.get("run_config") != config:
            problems.append("score_configuration_differs_from_inference")
        if score.get("requested_documents") != len(expected):
            problems.append("score_uses_wrong_planned_denominator")
        if score.get("paired_complete_documents") != len(completed_all):
            problems.append("score_completion_count_differs_from_actual_predictions")
        for arm in DETECTORS:
            scored = score.get("detectors", {}).get(arm, {}).get("all_review_hints_detection", {})
            score_ids = [row["document_id"] for row in scored.get("by_document", [])]
            if set(score_ids) != set(expected) or len(score_ids) != len(expected):
                problems.append(arm + ":score_missing_or_duplicate_planned_documents")
    execution = not problems and len(completed_all) == len(expected)
    return {"planned_documents": len(expected), "all_groups_completed_documents": len(completed_all),
            "state": "complete" if execution and score is not None else "incomplete",
            "execution_complete": execution, "score_available": score is not None,
            "integrity_problems": sorted(set(problems)), "arms": arms,
            "status_snapshot": read(directory / "status.json") if (directory / "status.json").exists() else None}


def audit(data):
    data = Path(data)
    frozen = read(data / "release/freeze_manifest.json")
    problems, rows_by_split, results = [], {}, {}
    try:
        split_freezes, authorized_limit_cny, amendment = load_budget_amendment(data, frozen)
    except (ValueError, KeyError, OSError, sqlite3.Error) as exc:
        split_freezes, authorized_limit_cny, amendment = {}, 50, None
        problems.append("budget_amendment_invalid:" + str(exc))
    for split, count in {**EXPECTED, "holdout_oct07": 199}.items():
        source = data / "dataset" / ("inputs." + split + ".jsonl")
        rows = read_jsonl(source)
        rows_by_split[split] = rows
        if len(rows) != count or len({doc_id(row) for row in rows}) != count:
            problems.append(split + ":wrong_input_count_or_duplicate_identity")
        if digest(source) != frozen["inputs_sha256"][split]:
            problems.append(split + ":input_file_changed_since_freeze")
    for index, split in enumerate(rows_by_split):
        for other in list(rows_by_split)[index + 1:]:
            for field in ("doc_id", "group_id", "normalized_content_sha256"):
                left = {row[field] for row in rows_by_split[split] if row.get(field)}
                right = {row[field] for row in rows_by_split[other] if row.get(field)}
                if left & right:
                    problems.append(split + ":" + other + ":cross_split_" + field)
    held_ids = {doc_id(row) for row in rows_by_split["holdout_oct07"]}
    holdout_queues = []
    for path in sorted(data.glob("*/queue.json")):
        if held_ids & set(read(path).get("document_ids", [])):
            holdout_queues.append(str(path))
    if holdout_queues:
        problems.append("held_out_documents_present_in_an_execution_queue")
    for split in EXPECTED:
        results[split] = inspect_run(data / ("full-" + split), rows_by_split[split], split_freezes.get(split, frozen), split)
    review_path = data / "negative_review.jsonl"
    review = read_jsonl(review_path) if review_path.exists() else []
    review_counts = dict(Counter(row.get("human_review_status", "missing") for row in review))
    # A status field alone is not evidence that a human reviewed a record, and
    # approval alone is not evidence that the signed dataset was evaluated.
    human_approval_claims = sum(row.get("human_review_status") == "approved" for row in review)
    ledger_path = data / "model_usage.sqlite3"
    budget = None
    if ledger_path.exists():
        with closing(sqlite3.connect(ledger_path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            limit = db.execute("SELECT limit_micro FROM budget WHERE singleton=1").fetchone()[0]
            used, calls = db.execute("SELECT COALESCE(SUM(charge_micro),0),COUNT(*) FROM calls").fetchone()
            states = dict(db.execute("SELECT status,COUNT(*) FROM calls GROUP BY status"))
        budget = {"limit_cny": limit / 1e6, "accounted_including_reserved_cny": used / 1e6,
                  "calls": calls, "call_status_counts": states, "basis": "configured upper rates, not provider invoice"}
        if used > limit or limit > authorized_limit_cny * 1_000_000:
            problems.append("cumulative_budget_exceeds_authorized_limit")
    else:
        problems.append("shared_budget_ledger_missing")
    complete = not problems and all(run["state"] == "complete" for run in results.values())
    return {"created_at": datetime.now(timezone.utc).isoformat(),
            "planned_non_holdout_documents": sum(EXPECTED.values()),
            "full_model_execution_and_scoring_complete": complete,
            "integrity_problems": problems, "runs": results,
            "budget": budget,
            "budget_amendment": amendment,
            "holdout": {"documents": 199, "execution_queues_containing_holdout": holdout_queues},
            "normal_candidate_review": {"records": len(review), "status_counts": review_counts,
                                        "claimed_approvals_requiring_separate_audit": human_approval_claims},
            "not_proven_by_this_audit": [
                "No detection-quality acceptance threshold was specified; inspect full P/R/F1 and failure strata.",
                "Human-approved normal samples and their actual model evaluation require separate evidence.",
                "Semantic evidence correctness, excessive escalation and actual human review efficiency require independent annotations/timing.",
                "Four paired E samples are development regression; public text scores do not validate external financial-statement evidence.",
                "October 7 holdout execution remains intentionally deferred, not silently counted as complete."]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=ROOT / "data/v2")
    parser.add_argument("--out", type=Path, default=ROOT / "data/v2/full-acceptance-audit.json")
    parser.add_argument("--require-complete", action="store_true", help="Exit nonzero unless all planned model execution and scoring is complete")
    args = parser.parse_args(argv)
    result = audit(args.data)
    write_json(args.out, result)
    print(json.dumps({"audit": str(args.out.resolve()),
                      "full_model_execution_and_scoring_complete": result["full_model_execution_and_scoring_complete"],
                      "integrity_problems": result["integrity_problems"],
                      "completed_by_split": {split: run.get("all_groups_completed_documents", 0) for split, run in result["runs"].items()}}, ensure_ascii=False))
    return int(args.require_complete and not result["full_model_execution_and_scoring_complete"])


if __name__ == "__main__":
    raise SystemExit(main())
