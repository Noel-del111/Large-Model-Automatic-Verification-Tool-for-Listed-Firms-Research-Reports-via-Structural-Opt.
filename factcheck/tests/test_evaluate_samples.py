"""Paired model evaluation must preserve answer isolation and honest text scope."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from factcheck.tools import evaluate_samples as evaluator
from yjcheck.model import ModelConfig
from yjcheck.model_runtime import RuntimeSettings


class EvaluateSamplesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.samples = self.root / "samples"
        self.folder = self.samples / "case1"
        self.folder.mkdir(parents=True)
        for name in ("report.docx", "source.pdf", "answers.xlsx"):
            (self.folder / name).write_bytes(("fixture:" + name).encode())
        self.out = self.root / "output"
        self.result = {"findings": [], "summary": {"claims": 0}, "input_issues": [], "model_traces": [],
                       "runtime": {"total_seconds": 1},
                       "text_review": {"document_id": "r1", "errors": [{"status": "needs_review", "error_type": "错别字"}],
                                       "raw_candidates": [{}], "rejected_candidates": [], "traces": [],
                                       "coverage": {"execution_complete": True}}}
        self.events = []

    def infer(self, report, sources, out, *, model_config=None):
        self.events.append("inference")
        self.assertEqual(report, self.folder / "report.docx")
        self.assertEqual(sources, [self.folder / "source.pdf"])
        self.assertNotIn(self.folder / "answers.xlsx", [report, *sources])
        return deepcopy(self.result), out / "run1"

    def read_answers(self, path):
        self.assertEqual(self.events, ["inference"])
        self.events.append("answers")
        self.assertEqual(path, self.folder / "answers.xlsx")
        return []

    def test_default_is_offline_and_text_candidates_are_not_paired_gold(self):
        with patch.object(evaluator, "run_check", side_effect=self.infer) as checker, \
                patch.object(evaluator, "read_answer_rows", side_effect=self.read_answers), \
                patch.object(evaluator.ModelConfig, "from_env", side_effect=AssertionError("Offline must not load model config")):
            result = evaluator.evaluate(self.samples, self.out)
        self.assertIsNone(checker.call_args.kwargs["model_config"])
        self.assertEqual(result["run_config"]["mode"], "offline")
        self.assertIsNone(result["run_config"]["model_settings"])
        self.assertEqual(result["summary"]["unique_model_usage"]["calls"], 0)
        self.assertEqual(result["summary"]["unique_expected_claims"], 0)
        self.assertEqual(result["summary"]["text_review"]["candidates"], 1)
        self.assertIsNone(result["summary"]["text_review"]["detection_metrics"])
        self.assertFalse(result["cases"][0]["text_review"]["gold_available"])
        self.assertEqual(result["cases"][0]["additional_unannotated_predictions"], [])

    def test_model_enables_text_review_saves_safe_settings_and_hashes(self):
        config = ModelConfig("https://example.invalid/private-endpoint", "configured-model", "private-test-api-key", reasoning_effort="low")
        runtime = RuntimeSettings("2", "8", "https://prices.example.org", "2026-10-03", 1000000, 8192,
                                  "20", self.root / "usage.sqlite3", 1)
        with patch.object(evaluator, "run_check", side_effect=self.infer) as checker, \
                patch.object(evaluator, "read_answer_rows", side_effect=self.read_answers), \
                patch.object(evaluator.ModelConfig, "from_env", return_value=config), \
                patch.object(evaluator.RuntimeSettings, "from_env", return_value=runtime):
            result = evaluator.evaluate(self.samples, self.out, model=True)
        passed_config = checker.call_args.kwargs["model_config"]
        self.assertTrue(passed_config.review_text)
        self.assertEqual(passed_config.reasoning_effort, "low")
        self.assertFalse(config.review_text, "Enabling evaluation should not mutate another caller's config")
        serialized = (self.out / "evaluation.json").read_text(encoding="utf-8")
        config_json = (self.out / "run_config.json").read_text(encoding="utf-8")
        for forbidden in (config.api_key, config.base_url):
            self.assertNotIn(forbidden, serialized + config_json)
        snapshot = result["run_config"]["model_settings"]
        self.assertEqual(snapshot["endpoint_sha256"], hashlib.sha256(config.base_url.encode()).hexdigest())
        self.assertEqual(snapshot["max_output_tokens"], 8192)
        self.assertEqual(snapshot["few_shot_examples"], 0)
        self.assertEqual(result["run_config"]["mode"], "model")
        self.assertIn("factcheck/tools/evaluate_samples.py", result["run_config"]["source_code_sha256"])
        case = result["cases"][0]
        self.assertEqual(case["input_hashes"]["report"]["sha256"], hashlib.sha256((self.folder / "report.docx").read_bytes()).hexdigest())
        self.assertEqual(case["answer_workbook_hash_after_inference"]["sha256"], hashlib.sha256((self.folder / "answers.xlsx").read_bytes()).hexdigest())

    def test_answer_bytes_are_not_hashed_before_inference(self):
        original_info = evaluator._file_info

        def guarded_info(path):
            if path.suffix == ".xlsx":
                self.assertIn("inference", self.events)
            return original_info(path)

        with patch.object(evaluator, "run_check", side_effect=self.infer), \
                patch.object(evaluator, "read_answer_rows", side_effect=self.read_answers), \
                patch.object(evaluator, "_file_info", side_effect=guarded_info):
            evaluator.evaluate(self.samples, self.out)

    def test_retries_and_shared_traces_are_counted_once_with_reservation_costs(self):
        failed = {"call_id": "first", "status": "error", "maximum_cny": "0.2", "duration_seconds": 2,
                  "api_key": "must-not-copy", "base_url": "must-not-copy", "response_content": "must-not-copy"}
        success = {"call_id": "second", "status": "ok", "cost_cny": "0.03", "maximum_cny": "0.3",
                   "input_tokens": 10, "output_tokens": 5, "usage_source": "provider", "duration_seconds": 3,
                   "previous_attempts": [failed]}
        result = {"model_traces": [{"runtime": success}], "text_review": {"traces": [{"runtime_trace": success}, {"runtime_trace": failed}]}}
        calls = evaluator._runtime_calls(result)
        usage = evaluator._usage_summary(calls)
        self.assertEqual(usage["calls"], 2)
        self.assertEqual(usage["failed_calls"], 1)
        self.assertEqual(usage["unknown_usage_calls"], 1)
        self.assertAlmostEqual(usage["accounted_cny"], 0.23)
        self.assertEqual((usage["input_tokens"], usage["output_tokens"], usage["duration_seconds"]), (10, 5, 5))
        self.assertNotIn("must-not-copy", json.dumps(usage))

    def test_missing_model_configuration_fails_before_inference_or_answers(self):
        with patch.object(evaluator.ModelConfig, "from_env", return_value=ModelConfig("", "")), \
                patch.object(evaluator, "run_check") as checker, patch.object(evaluator, "read_answer_rows") as answers:
            with self.assertRaises(ValueError):
                evaluator.evaluate(self.samples, self.out, model=True)
        checker.assert_not_called()
        answers.assert_not_called()


if __name__ == "__main__":
    unittest.main()
