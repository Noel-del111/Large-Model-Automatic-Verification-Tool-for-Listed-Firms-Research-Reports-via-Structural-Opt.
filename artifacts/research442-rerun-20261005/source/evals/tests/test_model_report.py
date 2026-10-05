"""Protect report scope and metric choices; no API, inference or data fixtures."""
import json
from pathlib import Path
import tempfile
import unittest

from evals.build_model_report import load_runs, render, research_and_other_scores


class ModelReportTests(unittest.TestCase):
    def test_research_aggregate_includes_rejected_hints_and_failed_documents(self):
        rows = [{"document_id": "research_stock", "true_positive": 1, "false_positive": 3, "false_negative": 1},
                {"document_id": "research_industry_failed", "true_positive": 0, "false_positive": 0, "false_negative": 8},
                {"document_id": "insurance", "true_positive": 20, "false_positive": 0, "false_negative": 0}]
        arm = {"all_review_hints_detection": {"by_document": rows}, "by_scene": {
            "个股研报": {"false_positive": 0, "by_document": [{"document_id": "research_stock"}]},
            "行业研报": {"by_document": [{"document_id": "research_industry_failed"}]},
            "保险合同": {"by_document": [{"document_id": "insurance"}]}}}
        scores = research_and_other_scores(arm)
        primary = scores["研报主测（个股＋行业）"]
        self.assertEqual((primary["documents"], primary["true_positive"], primary["false_positive"], primary["false_negative"]), (2, 1, 3, 9))
        self.assertAlmostEqual(primary["precision"], .25)
        self.assertAlmostEqual(primary["recall"], .1)
        self.assertAlmostEqual(primary["f1"], 1/7)
        self.assertEqual(scores["其他金融文档补测"]["true_positive"], 20)

    def test_missing_or_conflicting_scene_membership_cannot_be_guessed(self):
        arm = {"all_review_hints_detection": {"by_document": [{"document_id": "a"}]}, "by_scene": {}}
        self.assertIsNone(research_and_other_scores(arm))
        arm["by_scene"] = {"个股研报": {"by_document": [{"document_id": "a"}]},
                           "保险合同": {"by_document": [{"document_id": "a"}]}}
        with self.assertRaises(ValueError):
            research_and_other_scores(arm)

    def test_primary_scope_never_substitutes_emitted_score_or_sums_cumulative_cost(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            primary = {"true_positive": 1, "false_positive": 1, "false_negative": 0,
                       "precision": .5, "recall": 1., "f1": 2/3, "by_document": [{"document_id": "a"}]}
            emitted = {**primary, "false_positive": 0, "precision": 1., "f1": 1.}
            score = {"run_config": {"mode": "model", "model": "test"}, "requested_documents": 1,
                     "paired_complete_documents": 1, "unique_model_usage": {"accounted_cny": 3.},
                     "detectors": {"hybrid": {"candidate_detection": emitted,
                         "all_review_hints_detection": primary, "human_review_hints_total": 2,
                         "rejected_model_candidates": 1}}}
            (path / "score.json").write_text(json.dumps(score), encoding="utf-8")
            runs = load_runs(["tuning=" + directory, "validation=" + directory], ["validation=dev_validation"])
            result = render(runs, path / "report.md")
            self.assertIn("| tuning | 组合流程 | 1 | 1 | 0 | 50.00% | 100.00% | 66.67% | 2 | 1 |", result)
            self.assertIn("存在交集，不能作为全部新样本的独立验证", result)
            self.assertIn("累计花费与当前余额不在本报告中推断", result)
            self.assertNotIn("6.000000", result)
            del score["detectors"]["hybrid"]["all_review_hints_detection"]
            (path / "score.json").write_text(json.dumps(score), encoding="utf-8")
            result = render(load_runs(["tuning=" + directory], []), path / "report.md")
            self.assertIn("| tuning | 组合流程 | 未测量 | 未测量 | 未测量 | 未测量 | 未测量 | 未测量 |", result)

    def test_scope_labels_cannot_silently_claim_independent_evaluation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            score = {"run_config": {"mode": "model"}, "detectors": {"hybrid": {}}}
            (path / "score.json").write_text(json.dumps(score), encoding="utf-8")
            self.assertEqual(load_runs(["a=" + directory], [])[0]["scope"], "dev_tuning")
            with self.assertRaises(ValueError):
                load_runs(["a=" + directory], ["a=blind"])
            with self.assertRaises(ValueError):
                load_runs(["a=" + directory], ["missing=frozen_eval"])


if __name__ == "__main__":
    unittest.main()
