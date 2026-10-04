"""Regression tests for evals/normal_review.py sign-off workflow; no model calls."""
import unittest

from evals import normal_review as nr


def span(text, start):
    return {"start": start, "end": start + len(text), "text": text}


class QueueTests(unittest.TestCase):
    def test_queue_excludes_confirmed_and_includes_pending(self):
        normal = [{"document_id": "n1", "content": "正常文本"}]
        preds = {"n1": {"document_id": "n1", "errors": [
            {"error_type": "计算错误", "spans": [span("1+1=3", 0)], "status": "confirmed_error"},
            {"error_type": "模糊语言", "spans": [span("或将", 6)], "status": "needs_review",
             "review_priority": "low", "reason": "待核"},
            {"error_type": "数值缺失", "spans": [], "status": "needs_review",
             "validation": "anchor_rejected", "original_spans": [{"text": "缺失处"}]},
        ]}}
        queue = nr.build_queue(normal, preds)
        self.assertEqual(len(queue), 2)
        self.assertEqual([item["error_type"] for item in queue], ["模糊语言", "数值缺失"])
        self.assertTrue(all(item["decision"] is None for item in queue))
        self.assertTrue(all(item["candidate_id"] for item in queue))
        self.assertEqual(len({item["candidate_id"] for item in queue}), 2)

    def test_queue_ignores_documents_absent_from_predictions(self):
        normal = [{"document_id": "n1", "content": "正文"}, {"document_id": "n2", "content": "正文"}]
        preds = {"n1": {"document_id": "n1", "errors": [{"error_type": "模糊语言",
                "spans": [span("正文", 0)], "status": "needs_review"}]}}
        queue = nr.build_queue(normal, preds)
        self.assertEqual([item["document_id"] for item in queue], ["n1"])


class SignoffTests(unittest.TestCase):
    def _queue(self):
        normal = [{"document_id": "n1", "content": "正常文本"}]
        preds = {"n1": {"document_id": "n1", "errors": [{"error_type": "模糊语言",
                "spans": [span("或将", 0)], "status": "needs_review"}]}}
        return nr.build_queue(normal, preds)

    def test_decisions_apply_and_duplicates_are_rejected(self):
        queue = self._queue()
        cid = queue[0]["candidate_id"]
        merged = nr.merge_signoffs(queue, [{"candidate_id": cid, "verdict": "dismissed",
                                            "reviewer": "甲", "note": "上下文已说明", "duration_seconds": 12.5}])
        self.assertEqual(merged[0]["decision"], "dismissed")
        self.assertEqual(merged[0]["timing_method"], "explicit_signoff_wall_clock")
        with self.assertRaises(ValueError):
            nr.merge_signoffs(merged, [{"candidate_id": cid, "verdict": "confirmed"}])

    def test_invalid_verdict_unknown_id_and_bad_duration_fail(self):
        queue = self._queue()
        cid = queue[0]["candidate_id"]
        for decision in ({"candidate_id": cid, "verdict": "maybe"},
                         {"candidate_id": "nonexistent", "verdict": "confirmed"},
                         {"candidate_id": cid, "verdict": "confirmed", "duration_seconds": -1}):
            with self.subTest(decision=decision), self.assertRaises(ValueError):
                nr.merge_signoffs(queue, [decision])


class ReportTests(unittest.TestCase):
    def test_false_positive_rates_use_decided_denominator_only(self):
        queue = [
            {"candidate_id": "a", "document_id": "n1", "error_type": "模糊语言", "decision": "dismissed",
             "timing_method": "explicit_signoff_wall_clock", "duration_seconds": 5},
            {"candidate_id": "b", "document_id": "n1", "error_type": "数值缺失", "decision": "confirmed",
             "timing_method": "not_measured"},
            {"candidate_id": "c", "document_id": "n2", "error_type": "术语误用", "decision": None,
             "timing_method": "not_measured"},
            {"candidate_id": "d", "document_id": "n2", "error_type": "术语误用", "decision": "dismissed",
             "timing_method": "not_measured"},
        ]
        report = nr.false_positive_report(queue)
        self.assertEqual(report["decided"], 3)
        self.assertEqual(report["dismissed"], 2)
        self.assertEqual(report["undecided"], 1)
        self.assertAlmostEqual(report["review_dismissal_rate"], 2 / 3, places=4)
        self.assertEqual(report["status"], "measured")
        self.assertEqual(report["by_document"]["n1"]["dismissed"], 1)
        self.assertEqual(report["by_type"]["术语误用"]["decided"], 1)
        self.assertEqual(report["untimed_dismissed_items"], ["d"])

    def test_no_decided_items_yields_not_measured_null_rate(self):
        queue = [{"candidate_id": "a", "document_id": "n1", "error_type": "模糊语言",
                  "decision": None, "timing_method": "not_measured"}]
        report = nr.false_positive_report(queue)
        self.assertEqual(report["decided"], 0)
        self.assertIsNone(report["review_dismissal_rate"])
        self.assertEqual(report["status"], "not_measured")


if __name__ == "__main__":
    unittest.main()