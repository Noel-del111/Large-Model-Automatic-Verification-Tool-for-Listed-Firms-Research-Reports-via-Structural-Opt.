"""An acceptance gate must not trust optimistic status counters or reduced scores."""
from copy import deepcopy
from contextlib import closing
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import sqlite3

from evals.audit_full_acceptance import inspect_run, load_budget_amendment, digest
from evals.run_v2 import DETECTORS, write_json


class AcceptanceAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run = Path(self.temp.name)
        self.rows = [{"doc_id": "one", "content": "真实原文甲"}, {"doc_id": "two", "content": "真实原文乙"}]
        self.frozen = {"runner_source_hash": "frozen-source", "inputs_sha256": {"dev": "frozen-inputs"},
                       "examples_sha256": "dev-examples"}
        self.config = {"source_hash": "frozen-source", "inputs_sha256": "frozen-inputs",
                       "examples_sha256": "dev-examples", "mode": "model", "detectors": list(DETECTORS)}
        self.frozen["runtime_without_credentials"] = {"YJCHECK_MODEL": "test-model", "YJCHECK_BASE_URL": "https://test.example",
            "YJCHECK_TIMEOUT": 480, "YJCHECK_THINKING": "enabled", "YJCHECK_REASONING_EFFORT": "low",
            "YJCHECK_INPUT_CNY_PER_MTOK": "1", "YJCHECK_OUTPUT_CNY_PER_MTOK": "4", "YJCHECK_PRICE_SOURCE": "official",
            "YJCHECK_PRICE_DATE": "2026-10-03", "YJCHECK_CONTEXT_TOKENS": "1000000", "YJCHECK_MAX_OUTPUT_TOKENS": "65536",
            "YJCHECK_BUDGET_CNY": "50", "YJCHECK_MAX_RETRIES": "0", "YJCHECK_PRICE_VALID_UNTIL": "2026-10-04T16:00:00Z"}
        self.config.update({"model": "test-model", "endpoint_hash": hashlib.sha256(b"https://test.example").hexdigest(),
                            "model_request": {"thinking": "enabled", "reasoning_effort": "low", "timeout": 480.0},
                            "runtime": {key.removeprefix("YJCHECK_").lower(): str(value)
                                        for key, value in self.frozen["runtime_without_credentials"].items()}})
        write_json(self.run / "run_config.json", self.config)
        write_json(self.run / "queue.json", {"document_ids": ["one", "two"]})
        write_json(self.run / "status.json", {"requested": 2, "completed_documents": 2})
        for arm in DETECTORS:
            for row in self.rows:
                write_json(self.run / "predictions" / arm / (row["doc_id"] + ".json"), {
                    "document_id": row["doc_id"], "detector": arm, "errors": [],
                    "source_content_sha256": hashlib.sha256(row["content"].encode()).hexdigest(),
                    "coverage": {"execution_complete": True}})
        self.score = {"run_config": self.config, "requested_documents": 2, "paired_complete_documents": 2,
                      "detectors": {arm: {"all_review_hints_detection": {"by_document": [
                          {"document_id": "one"}, {"document_id": "two"}]}} for arm in DETECTORS}}
        write_json(self.run / "score.json", self.score)

    def inspect(self):
        return inspect_run(self.run, self.rows, self.frozen, "dev")

    def test_complete_empty_predictions_are_legal(self):
        result = self.inspect()
        self.assertTrue(result["execution_complete"])
        self.assertEqual(result["state"], "complete")
        self.assertEqual(result["integrity_problems"], [])

    def test_optimistic_status_does_not_hide_missing_prediction(self):
        (self.run / "predictions/hybrid/two.json").unlink()
        result = self.inspect()
        self.assertFalse(result["execution_complete"])
        self.assertEqual(result["all_groups_completed_documents"], 1)
        self.assertEqual(result["arms"]["hybrid"]["missing_document_ids"], ["two"])

    def test_duplicate_predictions_cannot_replace_missing_documents(self):
        source = self.run / "predictions/model_direct/one.json"
        (source.parent / "duplicate.json").write_bytes(source.read_bytes())
        result = self.inspect()
        self.assertFalse(result["execution_complete"])
        self.assertEqual(result["arms"]["model_direct"]["duplicate_document_ids"], ["one"])

    def test_wrong_content_cannot_pass_even_with_matching_document_id(self):
        path = self.run / "predictions/hybrid/one.json"
        report = json.loads(path.read_text(encoding="utf-8"))
        report["source_content_sha256"] = "a-different-document"
        write_json(path, report)
        self.assertFalse(self.inspect()["execution_complete"])

    def test_scoring_cannot_remove_failed_document_from_denominator(self):
        score = deepcopy(self.score)
        score["detectors"]["model_direct"]["all_review_hints_detection"]["by_document"] = [{"document_id": "one"}]
        write_json(self.run / "score.json", score)
        result = self.inspect()
        self.assertIn("model_direct:score_missing_or_duplicate_planned_documents", result["integrity_problems"])

    def test_source_drift_and_partial_queue_fail_gate(self):
        write_json(self.run / "run_config.json", {**self.config, "source_hash": "different"})
        write_json(self.run / "queue.json", {"document_ids": ["one"]})
        result = self.inspect()
        self.assertIn("source_fingerprint_differs_from_freeze", result["integrity_problems"])
        self.assertIn("queue_missing_extra_or_duplicate_documents", result["integrity_problems"])

    def test_missing_score_is_not_completed_acceptance(self):
        (self.run / "score.json").unlink()
        result = self.inspect()
        self.assertTrue(result["execution_complete"])
        self.assertEqual(result["state"], "incomplete")

    def test_cheaper_model_or_different_output_budget_cannot_claim_frozen_run(self):
        changed = deepcopy(self.config)
        changed["model"] = "different-model"
        changed["runtime"]["max_output_tokens"] = "8192"
        write_json(self.run / "run_config.json", changed)
        result = self.inspect()
        self.assertIn("model_or_request_settings_differ_from_freeze", result["integrity_problems"])
        self.assertIn("runtime_differs_from_freeze:max_output_tokens", result["integrity_problems"])


class BudgetAmendmentAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "data/v2"
        self.release = self.data / "release"
        self.release.mkdir(parents=True)
        names = ["factcheck/src/yjcheck/model_runtime.py", "evals/run_v2.py", "evals/baseline.py"]
        self.frozen = {"source_files": {}, "runtime_without_credentials": {"YJCHECK_BUDGET_CNY": "50"}}
        changes = {}
        for name in names:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("new " + name, encoding="utf-8")
            self.frozen["source_files"][name] = hashlib.sha256(("old " + name).encode()).hexdigest()
            if name != "evals/baseline.py":
                changes[name] = {"before_sha256": self.frozen["source_files"][name], "after_sha256": digest(path)}
        write_json(self.release / "freeze_manifest.json", self.frozen)
        (self.release / "budget100-inference.patch").write_text("reviewed budget patch", encoding="utf-8")
        source_hash = hashlib.sha256("".join(digest(self.root / name) for name in names).encode()).hexdigest()
        self.amendment = {"schema_version": "budget-amendment/1.0", "applies_to_splits": ["dev"],
                          "authorization": {"old_limit_cny": 50, "new_limit_cny": 100,
                                            "authorization_id": "user-100", "authorization_basis": "价格限制调整为100元"},
                          "base_freeze_sha256": digest(self.release / "freeze_manifest.json"),
                          "code_changes": changes, "new_runner_source_hash": source_hash,
                          "patch_sha256": digest(self.release / "budget100-inference.patch")}
        write_json(self.release / "budget-amendment-100.json", self.amendment)
        with closing(sqlite3.connect(self.data / "model_usage.sqlite3")) as db, db:
            db.execute("CREATE TABLE budget_authorizations (authorization_id TEXT PRIMARY KEY, old_limit_micro INTEGER, new_limit_micro INTEGER, authorization_basis TEXT)")
            db.execute("INSERT INTO budget_authorizations VALUES (?,?,?,?)", ("user-100", 50_000_000, 100_000_000, "价格限制调整为100元"))

    def load(self):
        return load_budget_amendment(self.data, self.frozen, self.root)

    def test_authorized_amendment_keeps_current_eval_freeze_unchanged(self):
        original = deepcopy(self.frozen)
        overrides, ceiling, _ = self.load()
        self.assertEqual(ceiling, 100)
        self.assertEqual(set(overrides), {"dev"})
        self.assertEqual(overrides["dev"]["runtime_without_credentials"]["YJCHECK_BUDGET_CNY"], "100")
        self.assertEqual(self.frozen, original)

    def test_editing_ceiling_record_without_matching_ledger_is_rejected(self):
        with closing(sqlite3.connect(self.data / "model_usage.sqlite3")) as db, db:
            db.execute("DELETE FROM budget_authorizations")
        with self.assertRaisesRegex(ValueError, "共享账本授权"):
            self.load()

    def test_unrecorded_source_change_cannot_be_called_budget_only(self):
        (self.root / "evals/baseline.py").write_text("changed baseline algorithm", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "推理源码"):
            self.load()

    def test_original_freeze_or_patch_tampering_is_rejected(self):
        (self.release / "budget100-inference.patch").write_text("different patch", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "补丁"):
            self.load()


if __name__ == "__main__":
    unittest.main()
