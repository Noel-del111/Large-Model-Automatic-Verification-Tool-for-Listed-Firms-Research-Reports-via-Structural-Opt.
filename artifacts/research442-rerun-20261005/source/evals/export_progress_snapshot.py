"""Publishable aggregate evidence for the 200-document handoff, without raw text."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evals"))
from build_model_report import research_and_other_scores


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def numeric_fields(value):
    """Only aggregate numbers, never per-document lists, quotes or free text."""
    return {key: item for key, item in value.items()
            if item is None or isinstance(item, (int, float, bool))}


def build_snapshot(score, source_sha256, finalization):
    completed = score.get("paired_complete_documents")
    successful = completed == 200
    expected_state = "reports_ready" if successful else "reports_ready_with_execution_failures"
    arms_complete = all(score.get("detectors", {}).get(name, {}).get("attempted_documents") == 200
                        and score["detectors"][name].get("missing_prediction_documents") == 0
                        for name in ("legacy_rules", "model_direct", "hybrid"))
    if (score.get("requested_documents") != 200 or not isinstance(completed, int) or not 0 <= completed <= 200
            or not arms_complete or finalization.get("state") != expected_state
            or finalization.get("requested_scope") != "eval200"
            or finalization.get("scope_execution_and_scoring_complete") is not successful
            or finalization.get("scoring_sha256", {}).get("eval_oct05") != source_sha256):
        raise ValueError("200篇队列尚未完整处理、失败状态不一致或评分哈希不符；不能发布交接快照")
    config = score["run_config"]
    arms = {}
    for name in ("legacy_rules", "model_direct", "hybrid"):
        arm = score["detectors"][name]
        exported = numeric_fields(arm)
        for field in ("all_review_hints_detection", "candidate_detection", "verified_detection", "candidate_status_counts"):
            exported[field] = numeric_fields(arm.get(field, {}))
        for field in ("by_scene", "by_length", "by_type"):
            exported[field] = {label: {**numeric_fields(metrics), "documents": len(metrics.get("by_document", []))}
                               for label, metrics in arm.get(field, {}).items()}
        exported["research_and_other_all_hints"] = research_and_other_scores(arm)
        exported["latency"] = {key: numeric_fields(value) if isinstance(value, dict) else value
                               for key, value in arm.get("latency", {}).items()
                               if isinstance(value, (dict, int, float))}
        sensitivity = arm.get("fully_scorable_documents_sensitivity", {})
        exported["fully_scorable_documents_sensitivity"] = {**numeric_fields(sensitivity),
            "all_review_hints_detection": numeric_fields(sensitivity.get("all_review_hints_detection", {}))}
        arms[name] = exported
    safe_runtime = {key: value for key, value in config.get("runtime", {}).items()
                    if key in {"context_tokens", "max_output_tokens", "input_cny_per_mtok", "output_cny_per_mtok",
                               "price_date", "price_source", "price_valid_until", "budget_cny", "max_retries"}}
    return {"schema_version": "eval200-public-aggregate/1.0", "created_at": datetime.now(timezone.utc).isoformat(),
            "scope": "200 development-stage validation documents; not a final independent test; historical split eval_oct05 retained",
            "claims": {"model_weight_training_performed": False, "model_inference_and_scoring_performed": True,
                       "final_independent_test_performed": False, "final_holdout_documents": 199,
                       "historical_split_id": "eval_oct05", "current_use": "development_validation_and_regression",
                       "development_pool_documents": 598, "development_pool_full_model_run_performed": False,
                       "prior_pilot_and_offline_experiments_exist": True},
            "requested_documents": 200, "all_groups_attempted_documents": 200,
            "all_groups_completed_documents": completed, "failed_or_missing_documents": 200 - completed,
            "scope_execution_and_scoring_complete": successful, "state": expected_state,
            "source_score_sha256": source_sha256,
            "protocol": "Same model response shared by model_direct and hybrid; postprocessing ablation",
            "metric_scope": "all_review_hints_detection includes rejected hints; by_scene/by_length/by_type are accepted-output diagnostics",
            "run_config": {key: config[key] for key in ("mode", "model", "model_request", "source_hash", "inputs_sha256",
                                                        "examples_sha256", "baseline_archive_sha256", "comparison_protocol") if key in config},
            "runtime_at_launch": safe_runtime, "detectors": arms,
            "unique_model_usage": numeric_fields(score.get("unique_model_usage", {})),
            "budget_at_finalization": {**numeric_fields(finalization["budget"]),
                                       "call_status_counts": numeric_fields(finalization["budget"].get("call_status_counts", {}))},
            "limitations": ["No raw document text, answers, predictions, credentials, or local ledger path are published here.",
                            "Source score and raw replies remain local; full file hashes enable later comparison, not independent reconstruction without source data.",
                            "Normal-text human approval, semantic evidence support and actual human review efficiency remain unmeasured.",
                            "Execution completeness does not establish a quality acceptance threshold or completion of the deferred 798-document plan."]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--score", type=Path, default=ROOT / "data/v2/full-eval_oct05/score.json")
    parser.add_argument("--finalization", type=Path, default=ROOT / "data/v2/eval200-finalization-status.json")
    parser.add_argument("--out", type=Path, default=ROOT / "docs/validation/v2-eval200/aggregate.json")
    args = parser.parse_args(argv)
    snapshot = build_snapshot(read(args.score), hashlib.sha256(args.score.read_bytes()).hexdigest(), read(args.finalization))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.out.resolve()), "documents": 200, "new_model_calls": 0}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
