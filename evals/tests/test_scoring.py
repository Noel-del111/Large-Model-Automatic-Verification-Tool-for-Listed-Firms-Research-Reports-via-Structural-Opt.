"""Metric contract regressions; no model/network calls."""
import unittest

from evals.fined_bench_eval import anchor_span, check_dataset, score_detection, score_paper_detection
from evals.operation_eval import evaluate


def span(text, start):
    return {"text": text, "start": start, "end": start + len(text)}


def gold(error_spans, error_type="number", **extra):
    return {"id": "g", "type": error_type, "spans": error_spans, "scorable": True, **extra}


def prediction(error_spans, error_type="number"):
    return {"error_type": error_type, "spans": error_spans}


class AnchoringTests(unittest.TestCase):
    def test_direct_offset_disambiguates_repeated_text(self):
        result = anchor_span("收入收入", "收入", 2)
        self.assertEqual((result["method"], result["start"]), ("original_offset", 2))

    def test_bad_offset_never_chooses_first_duplicate(self):
        self.assertEqual(anchor_span("收入收入", "收入", 99)["method"], "ambiguous_exact")

    def test_whitespace_mapping_retains_original_coordinates(self):
        result = anchor_span("甲收 入\n100元乙", "收入100元", 99)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["text"], "收 入\n100元")
        self.assertEqual((result["start"], result["end"]), (1, 9))

    def test_fuzzy_partial_and_empty_spans_are_not_gold(self):
        self.assertFalse(anchor_span("营业收入为100万元", "营业收入为200万元", 0)["accepted"])
        self.assertFalse(anchor_span("anything", "", 0)["accepted"])

    def test_dataset_audit_handles_malformed_offsets_without_crashing(self):
        result = check_dataset([{"scene": "研报", "content": "甲乙", "errors": [
            {"start_idx": [None], "error_span": ["甲"], "error_type": "x"},
            {"start_idx": "invalid", "error_span": ["乙"], "error_type": "x"},
            {"start_idx": [0], "error_span": [None], "error_type": "x"},
            {"start_idx": [0], "error_span": "甲", "error_type": "x"},
        ]}])
        self.assertEqual(result["invalid_span_arrays"], 2)
        self.assertEqual(result["offset_bad"], 3)
        self.assertEqual(result["reanchored"], 2)
        self.assertEqual(result["unresolved"], 1)


class PaperScoringTests(unittest.TestCase):
    def score(self, preds, golds):
        return score_paper_detection([{"document_id": "d", "errors": preds}],
                                     [{"document_id": "d", "errors": golds}])

    def test_multispan_error_counts_once_and_requires_every_span(self):
        target = gold([span("甲", 0), span("乙", 3)])
        result = self.score([prediction([span("甲", 0)])], [target])
        self.assertEqual((result["true_positive"], result["false_positive"], result["false_negative"]), (0, 1, 1))
        result = self.score([prediction([span("甲xx乙", 0)])], [target])
        self.assertEqual(result["true_positive"], 1)

    def test_maximum_matching_and_permutation_invariance(self):
        preds = [prediction([span("abcdef", 0)]), prediction([span("b", 1)])]
        targets = [gold([span("b", 1)], id="a"), gold([span("e", 4)], id="b")]
        result = self.score(preds, targets)
        self.assertEqual(result["true_positive"], 2)
        self.assertEqual(result, self.score(list(reversed(preds)), list(reversed(targets))))

    def test_duplicate_prediction_is_false_positive(self):
        pred = prediction([span("甲", 0)])
        result = self.score([pred, pred], [gold([span("甲", 0)])])
        self.assertEqual((result["true_positive"], result["false_positive"]), (1, 1))

    def test_type_accuracy_does_not_inherit_type_filter(self):
        result = self.score([prediction([span("甲", 0)], "wrong")], [gold([span("甲", 0)])])
        self.assertEqual(result["true_positive"], 0)
        self.assertEqual(result["type_accuracy"], 0)

    def test_wrong_quote_and_partial_overlap_cannot_match(self):
        target = gold([span("abcd", 0)])
        for pred in [prediction([span("abXd", 0)]), prediction([span("bc", 1)])]:
            self.assertEqual(self.score([pred], [target])["true_positive"], 0)
        self.assertEqual(self.score([prediction([{"text": "abcd", "start": 0, "end": 999}])], [target])["true_positive"], 0)

    def test_missing_unknown_clean_and_excluded_documents_are_audited(self):
        result = score_paper_detection(
            [{"document_id": "unknown", "errors": [prediction([span("甲", 0)])]}],
            [{"document_id": "d", "errors": [gold([span("甲", 0)]), gold([], scorable=False)]},
             {"document_id": "clean", "errors": []}])
        self.assertEqual((result["false_positive"], result["false_negative"], result["excluded_gold_errors"]), (1, 1, 1))
        self.assertEqual(result["missing_prediction_documents"], ["clean", "d"])
        self.assertEqual(result["unknown_prediction_documents"], ["unknown"])

    def test_duplicate_document_ids_are_rejected(self):
        row = {"document_id": "d", "errors": []}
        with self.assertRaisesRegex(ValueError, "duplicate document_id"):
            score_paper_detection([row, row], [row])

    def test_legacy_overlap_is_a_named_diagnostic_with_maximum_matching(self):
        targets = [{"doc_id": "d", "start": 0, "end": 2, "type": "a"},
                   {"doc_id": "d", "start": 3, "end": 5, "type": "a"}]
        preds = [{"doc_id": "d", "start": 0, "end": 5, "type": "a"},
                 {"doc_id": "d", "start": 0, "end": 1, "type": "a"}]
        result = score_detection(preds, targets)
        self.assertEqual(result["true_positive"], 2)
        self.assertEqual(result["match_mode"], "legacy_overlap")
        wrong = score_detection([dict(targets[0], type="wrong")], targets[:1])
        self.assertEqual(wrong["type_accuracy"], 0)


class OperationScoringTests(unittest.TestCase):
    def test_missing_prediction_counts_as_miss_and_failed_decision(self):
        target = {"item_id": "a", "operation": "value_consistency", "recorded_action": "proceed",
                  "findings": [{"doc_id": "d", "start": 0, "end": 1, "type": "x"}]}
        result = evaluate([], [target])
        scored = result["operations"]["value_consistency"]
        self.assertEqual(result["missing_predictions"], 1)
        self.assertEqual(scored["detection"]["false_negative"], 1)
        self.assertEqual(scored["decision"]["balanced_accuracy"], 0)
        self.assertEqual(scored["era"], 0)

    def test_duplicate_item_ids_rejected_in_both_inputs(self):
        item = {"item_id": "x"}
        for predictions, golds in (([item, item], [item]), ([item], [item, item])):
            with self.assertRaisesRegex(ValueError, "duplicate item_id"):
                evaluate(predictions, golds)

    def test_findings_cannot_match_other_item_on_same_document(self):
        finding = {"doc_id": "d", "start": 0, "end": 1, "type": "x"}
        result = evaluate([{"item_id": "a", "findings": [finding, finding], "decision": "proceed"}],
                          [{"item_id": "a", "findings": [finding]}, {"item_id": "b", "findings": [finding]}])
        scored = result["operations"]["value_consistency"]["detection"]
        self.assertEqual((scored["true_positive"], scored["false_positive"], scored["false_negative"]), (1, 1, 1))


if __name__ == "__main__":
    unittest.main()
