import unittest

from evals.fp_attribution_offline import MODEL_DIRECT_PR, estimate_type


class AttributionTests(unittest.TestCase):
    def test_all_fifteen_types_are_retained(self):
        rows = [estimate_type(t, 0, 1747, pr) for t, pr in MODEL_DIRECT_PR.items()]
        self.assertEqual(len(rows), 15)
        missing = next(r for r in rows if r['error_type'] == '不一致条款')
        self.assertEqual(missing['status'], 'missing_source_metrics')
        self.assertIsNone(missing['pdf_p'])
        self.assertIsNone(missing['fp'])

    def test_zero_gold_does_not_imply_zero_false_positives(self):
        row = estimate_type('模糊语言', 0, 1747, (0, 0))
        self.assertEqual(row['tp'], 0)
        self.assertEqual(row['fn'], 0)
        self.assertIsNone(row['fp'])

    def test_zero_precision_does_not_identify_prediction_count(self):
        row = estimate_type('计算错误', 10, 1747, (0, 0))
        self.assertEqual(row['fn'], 10)
        self.assertIsNone(row['fp'])

    def test_known_redundancy_estimate(self):
        row = estimate_type('冗余语句', 180, 1747, (38.70, 56.11))
        self.assertEqual((row['tp'], row['fp'], row['fn']), (101, 160, 79))


if __name__ == '__main__':
    unittest.main()
