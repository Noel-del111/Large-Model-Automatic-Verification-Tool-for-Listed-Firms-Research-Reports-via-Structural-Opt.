"""A public handoff summary must retain counts and exclude private raw payloads."""
from copy import deepcopy
import json
import unittest

from evals.export_progress_snapshot import build_snapshot


class ProgressSnapshotTests(unittest.TestCase):
    def setUp(self):
        metrics = {"true_positive": 1, "false_positive": 2, "false_negative": 3,
                   "precision": 1/3, "recall": .25, "f1": 2/7,
                   "by_document": [{"document_id": "raw-id", "private_text": "PRIVATE_SENTINEL",
                                    "true_positive": 1, "false_positive": 2, "false_negative": 3}]}
        arm = {"attempted_documents": 200, "missing_prediction_documents": 0,
               "all_review_hints_detection": metrics, "by_scene": {"个股研报": metrics},
               "by_length": {"short": metrics}, "by_type": {"计算错误": metrics},
               "traces": [{"api_key": "PRIVATE_SENTINEL"}]}
        self.score = {"requested_documents": 200, "paired_complete_documents": 200,
                      "run_config": {"model": "test-model", "api_key": "PRIVATE_SENTINEL",
                                     "runtime": {"ledger": "PRIVATE_SENTINEL", "budget_cny": "50"}},
                      "detectors": {name: deepcopy(arm) for name in ("legacy_rules", "model_direct", "hybrid")}}
        self.state = {"state": "reports_ready", "requested_scope": "eval200", "scope_execution_and_scoring_complete": True,
                      "scoring_sha256": {"eval_oct05": "score-hash"}, "budget": {"limit_cny": 100}}

    def test_public_output_preserves_primary_counts_but_not_raw_payloads(self):
        snapshot = build_snapshot(self.score, "score-hash", self.state)
        self.assertNotIn("PRIVATE_SENTINEL", json.dumps(snapshot))
        self.assertNotIn("raw-id", json.dumps(snapshot))
        self.assertEqual(snapshot["detectors"]["hybrid"]["all_review_hints_detection"]["false_positive"], 2)
        self.assertEqual(snapshot["runtime_at_launch"]["budget_cny"], "50")
        self.assertEqual(snapshot["budget_at_finalization"]["limit_cny"], 100)
        self.assertFalse(snapshot["claims"]["model_weight_training_performed"])
        self.assertFalse(snapshot["claims"]["final_independent_test_performed"])
        self.assertTrue(snapshot["claims"]["model_inference_and_scoring_performed"])
        self.assertEqual(snapshot["claims"]["historical_split_id"], "eval_oct05")

    def test_incomplete_scope_or_changed_score_cannot_be_published_as_complete(self):
        with self.assertRaises(ValueError):
            build_snapshot(self.score, "different-hash", self.state)
        for count in (0, 199):
            with self.subTest(count=count), self.assertRaises(ValueError):
                build_snapshot({**self.score, "paired_complete_documents": count}, "score-hash", self.state)
        with self.assertRaises(ValueError):
            build_snapshot(self.score, "score-hash", {**self.state, "requested_scope": "full"})

    def test_all_attempted_with_failure_is_publishable_but_not_successful(self):
        score = {**self.score, "paired_complete_documents": 199}
        state = {**self.state, "state": "reports_ready_with_execution_failures",
                 "scope_execution_and_scoring_complete": False}
        snapshot = build_snapshot(score, "score-hash", state)
        self.assertEqual(snapshot["all_groups_attempted_documents"], 200)
        self.assertEqual(snapshot["all_groups_completed_documents"], 199)
        self.assertEqual(snapshot["failed_or_missing_documents"], 1)
        self.assertFalse(snapshot["scope_execution_and_scoring_complete"])
        self.assertEqual(snapshot["detectors"]["hybrid"]["all_review_hints_detection"]["false_negative"], 3)

    def test_unfinished_queue_cannot_be_exported_as_a_completed_attempt(self):
        score = deepcopy(self.score)
        score["paired_complete_documents"] = 199
        state = {**self.state, "state": "reports_ready_with_execution_failures",
                 "scope_execution_and_scoring_complete": False}
        for field, value in (("attempted_documents", 199), ("missing_prediction_documents", 1)):
            with self.subTest(field=field):
                unfinished = deepcopy(score)
                unfinished["detectors"]["hybrid"][field] = value
                with self.assertRaises(ValueError):
                    build_snapshot(unfinished, "score-hash", state)


if __name__ == "__main__":
    unittest.main()
