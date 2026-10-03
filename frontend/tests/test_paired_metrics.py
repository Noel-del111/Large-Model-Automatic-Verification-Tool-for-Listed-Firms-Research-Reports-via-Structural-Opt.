"""Business paired-evaluation denominators, distinct from FinED text scoring."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest

MODULE = Path(__file__).resolve().parents[1] / "tools" / "metrics.py"
SPEC = importlib.util.spec_from_file_location("paired_metrics_under_test", MODULE)
metrics_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(metrics_module)
compute_metrics = metrics_module.compute_metrics


def row(expected="数值错误", status="confirmed_error", error_type="number", **fields):
    result = {"original": {"error_type": expected, "text": "需核查的原文"},
              "match_status": "unique" if status is not None else "missing",
              "duplicate_of_row": None}
    if status is not None:
        result["prediction"] = {"status": status, "error_type": error_type, "claim": {"text": "原文"}}
    result.update(fields)
    return result


def evaluation(rows=(), extras=()):
    return {"expected_cases": ["测试公司"], "cases": [
        {"case": "测试公司", "rows": list(rows), "additional_unannotated_predictions": list(extras)}]}


class PairedMetricsTests(unittest.TestCase):
    def test_preserves_rates_api_and_declares_precision_subset(self):
        result = compute_metrics(evaluation([row()]))
        for key in ("precision", "recall", "false_positive_rate", "evidence_accuracy", "suggestion_completeness"):
            self.assertIn(key, result["rates"])
        self.assertEqual(result["rates"]["precision"], 1)
        self.assertIn("子集", result["rate_scopes"]["precision"])
        self.assertIn("已标注子集", result["targets"]["precision"]["label"])

    def test_unknown_extra_confirmation_widens_precision_bounds_by_status(self):
        extras = ([{"status": "confirmed_error"}] * 2 + [{"status": "needs_review"}] * 3
                  + [{"status": "no_issue"}])
        result = compute_metrics(evaluation([row(), row(expected="正确")], extras))
        self.assertEqual(result["rates"]["precision"], 0.5)
        self.assertEqual(result["precision_bounds"]["lower"], 0.25)
        self.assertEqual(result["precision_bounds"]["upper"], 0.75)
        self.assertEqual(result["precision_bounds"]["denominator"], 4)
        self.assertEqual(result["counts"]["predictions_total"], 8)
        self.assertEqual(result["rates"]["review_burden"], 0.375)
        self.assertEqual(result["prediction_status_counts"]["additional_unannotated"],
                         {"confirmed_error": 2, "needs_review": 3, "no_issue": 1})

    def test_missing_ambiguous_and_abstained_errors_are_false_negatives(self):
        result = compute_metrics(evaluation([row(), row(status=None), row(status="needs_review"),
                                             row(match_status="ambiguous")]))
        self.assertEqual(result["counts"]["true_positive"], 1)
        self.assertEqual(result["counts"]["false_negative"], 3)
        self.assertEqual(result["rates"]["recall"], 0.25)
        self.assertEqual(result["counts"]["missing_prediction_rows"], 1)
        self.assertEqual(result["counts"]["ambiguous_prediction_rows"], 1)
        self.assertEqual(result["rates"]["extraction_coverage"], 0.5)

    def test_normal_content_review_and_auto_coverage_have_full_denominators(self):
        result = compute_metrics(evaluation([
            row(expected="正确", status="needs_review"), row(expected="正确", status="no_issue"),
            row(expected="正确"), row(expected="正确", status=None)]))
        self.assertEqual(result["rates"]["correct_content_review_rate"], 0.25)
        self.assertEqual(result["rates"]["false_positive_rate"], 0.25)
        self.assertEqual(result["rates"]["automatic_coverage"], 0.5)
        self.assertEqual(result["rates"]["extraction_coverage"], 0.75)
        self.assertIn("代理", result["rate_scopes"]["extraction_coverage"])

    def test_type_accuracy_is_not_conditioned_on_correct_type_or_confirmed_status(self):
        result = compute_metrics(evaluation([
            row(error_type="unit", error_type_match=False),
            row(status="needs_review", error_type_match=True), row(status=None)]))
        self.assertEqual(result["rates"]["precision"], 1)
        self.assertEqual(result["rates"]["type_accuracy"], 0.5)
        self.assertEqual(result["rates"]["type_recall"], 0.3333)
        self.assertEqual(result["counts"]["type_rated"], 2)

    def test_type_mapping_works_without_precomputed_type_match(self):
        result = compute_metrics(evaluation([
            row(expected="数值单位错误", error_type="unit_term_mismatch"),
            row(expected="模糊语言", error_type="ambiguous_expression"),
            row(expected="不一致条款", error_type="clause_conflict"),
            row(expected="正确", status="no_issue", error_type="")]))
        self.assertEqual(result["rates"]["type_accuracy"], 1)
        self.assertEqual(result["counts"]["type_rated"], 3)

    def test_duplicate_answer_rows_do_not_inflate_statuses_or_accuracy(self):
        result = compute_metrics(evaluation([row(), row(duplicate_of_row=2)]))
        self.assertEqual(result["counts"]["expected_claims_unique"], 1)
        self.assertEqual(result["prediction_status_counts"]["all"], {"confirmed_error": 1})
        self.assertEqual(result["precision_bounds"]["denominator"], 1)

    def test_no_confirmations_and_no_labels_are_unknown_not_perfect(self):
        result = compute_metrics(evaluation())
        self.assertTrue(all(value is None for value in result["rates"].values()))
        self.assertIsNone(result["precision_bounds"]["lower"])
        self.assertIsNone(result["precision_bounds"]["upper"])
        unknown = compute_metrics(evaluation(extras=[{"status": "confirmed_error"}]))
        self.assertIsNone(unknown["rates"]["precision"])
        self.assertEqual(unknown["precision_bounds"]["lower"], 0)
        self.assertEqual(unknown["precision_bounds"]["upper"], 1)

    def test_source_page_match_does_not_invent_semantic_support_or_human_time(self):
        result = compute_metrics(evaluation([row(source_page_match=True)]))
        self.assertEqual(result["rates"]["evidence_accuracy"], 1)
        self.assertTrue(all(item["value"] is None for item in result["unmeasured"].values()))

    def test_report_discloses_bounds_denominators_and_unmeasured_values(self):
        source = evaluation([row()], [{"status": "confirmed_error"}, {"status": "needs_review"}])
        result = compute_metrics(source)
        report = metrics_module.report_markdown(source, result, "fixture")
        for phrase in ("已标注子集", "50.0%—100.0%", "抽取匹配覆盖代理", "正常内容待复核率", "均未测量"):
            self.assertIn(phrase, report)

    def test_metrics_do_not_mutate_source_and_historical_case_alias_is_resolved(self):
        source = evaluation([row()])
        original = deepcopy(source)
        compute_metrics(source)
        self.assertEqual(source, original)
        source = {"cases": [{"case": name} for name in ("佛然能源", "兰石重装", "大地海洋", "群兴玩具")]}
        self.assertEqual(compute_metrics(source)["missing_cases"], [])


if __name__ == "__main__":
    unittest.main()
