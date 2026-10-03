import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from operation_eval import evaluate
from check_result_adapter import export_decisions


class EvaluationTests(unittest.TestCase):
    def test_missing_predictions_count_as_wrong_decisions_and_missed_findings(self):
        gold = [dict(item_id="a", operation="value_consistency", recorded_action="proceed",
                     findings=[dict(doc_id="d", start=0, end=5, type="number")]),
                dict(item_id="b", operation="evidence_request", recorded_action="ask",
                     required_evidence=[dict(doc_role="source", field="revenue", period="2025H1")])]
        report = evaluate([], gold)
        self.assertEqual(report["missing_predictions"], 2)
        self.assertEqual(report["operations"]["value_consistency"]["detection"]["false_negative"], 1)
        for value in report["operations"].values():
            self.assertEqual(value["decision"]["balanced_accuracy"], 0)
        self.assertEqual(report["operations"]["evidence_request"]["cra"], 0)

    def test_duplicate_ids_rejected(self):
        item = dict(item_id="a", decision="ask")
        with self.assertRaises(ValueError):
            evaluate([item, item], [])
        with self.assertRaises(ValueError):
            evaluate([], [item, item])

    def test_predictions_cannot_match_another_items_gold(self):
        span = dict(doc_id="d", start=0, end=5, type="number")
        gold = [dict(item_id="a", findings=[]), dict(item_id="b", findings=[span])]
        report = evaluate([dict(item_id="a", findings=[span]), dict(item_id="b", findings=[])], gold)
        detection = report["operations"]["value_consistency"]["detection"]
        self.assertEqual(detection["true_positive"], 0)
        self.assertEqual(detection["false_positive"], 1)
        self.assertEqual(detection["false_negative"], 1)

    def test_invalid_actions_rejected(self):
        with self.assertRaises(ValueError):
            evaluate([dict(item_id="a", decision="askk")], [dict(item_id="a")])

    def test_adapter_keeps_decisions_without_inventing_global_spans(self):
        finding = dict(id="same", decision="ask", evidence_request=[
            dict(doc_role="source", field="revenue", period="2025H1")])
        rows = export_decisions(dict(run_id="run", findings=[finding, finding]))
        self.assertNotEqual(rows[0]["item_id"], rows[1]["item_id"])
        self.assertEqual(rows[0]["findings"], [])
        self.assertEqual(rows[0]["evidence_request"], finding["evidence_request"])
        with self.assertRaises(ValueError):
            export_decisions(dict(run_id="old", findings=[dict(id="x")]))


if __name__ == "__main__":
    unittest.main()
