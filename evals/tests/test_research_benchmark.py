import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run_research_benchmark import gold_groups, locate_gold, match, pct, score_cases


class ResearchScoringTests(unittest.TestCase):
    def test_no_predictions_does_not_give_perfect_precision(self):
        result = score_cases([{"candidates": [], "gold": [{"type": "x", "spans": [[0, 4]]}]}], "candidates")
        self.assertIsNone(result["precision_percent"])
        self.assertEqual(result["recall_percent"], 0)
        self.assertIsNone(pct(0, 0))

    def test_multispan_gold_is_one_error_and_duplicate_predictions_are_penalized(self):
        gold = gold_groups("甲错；乙错", [{"error_type": "x", "error_span": ["甲错", "乙错"], "start_idx": [0, 3]}])
        pred = [{"type": "x", "spans": [[0, 2]]}, {"type": "x", "spans": [[3, 5]]}]
        result = score_cases([{"candidates": pred, "gold": gold}], "candidates")
        self.assertEqual((result["tp"], result["fp"], result["gold"]), (1, 1, 1))

    def test_maximum_matching_is_not_greedy(self):
        gold = [{"type": "x", "spans": [[0, 5]]}, {"type": "x", "spans": [[5, 10]]}]
        pred = [{"type": "x", "spans": [[0, 10]]}, {"type": "x", "spans": [[0, 5]]}]
        self.assertEqual(len(match(pred, gold)), 2)

    def test_unresolved_gold_remains_in_recall_denominator(self):
        result = score_cases([{"candidates": [], "gold": [{"type": "x", "spans": []}]}], "candidates")
        self.assertEqual((result["gold"], result["fn"]), (1, 1))

    def test_whitespace_reanchoring_and_strict_type(self):
        self.assertEqual(locate_gold("甲\n乙错", "甲乙错", 99), ([0, 4], "whitespace_reanchored"))
        self.assertEqual(match([{"type": "x", "spans": [[0, 4]]}], [{"type": "y", "spans": [[0, 4]]}]), [])


if __name__ == "__main__":
    unittest.main()
