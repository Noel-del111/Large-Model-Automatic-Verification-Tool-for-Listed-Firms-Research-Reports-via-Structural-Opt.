import unittest
import hashlib
import json
import tempfile
from pathlib import Path

from evals.fp_attribution_offline import MODEL_DIRECT_PR, SPLITS, estimate_type, load_dataset, validate_scope


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

    def test_invalid_counts_and_metrics(self):
        for gold, total, pr in [(-1, 10, (50, 50)), (11, 10, (50, 50)),
                                (1, 10, (float('nan'), 50)), (1, 10, (50, 101))]:
            with self.subTest(gold=gold, pr=pr), self.assertRaises(ValueError):
                estimate_type('计算错误', gold, total, pr)

    def test_missing_metrics_with_gold_remains_unknown(self):
        row = estimate_type('不一致条款', 10, 20, None)
        self.assertIsNone(row['tp'])
        self.assertIsNone(row['fn'])

    def test_scope(self):
        splits = dict(zip(SPLITS, (265, 89, 88)))
        validate_scope(splits, 1747, 45)
        for counts, total, excluded in [(splits, 1746, 45), (splits, 1747, 44), ({}, 1747, 45)]:
            with self.assertRaises(ValueError):
                validate_scope(counts, total, excluded)

    def make_dataset(self, path, defect=None):
        manifest = {'files': {}, 'documents': 3, 'errors': 3,
                    'scorable_errors': 3, 'excluded_errors': 0}
        for split in SPLITS:
            doc_id = 'duplicate' if defect == 'duplicate' else split
            input_row = {'doc_id': doc_id, 'scene': '行业研报'}
            error = {'type': '计算错误', 'scorable': True}
            if defect == 'unknown_type':
                error['type'] = 'unknown'
            if defect == 'missing_scorable':
                del error['scorable']
            gold_row = {'document_id': 'mismatch' if defect == 'mismatch' else doc_id, 'errors': [error]}
            for kind, row in [('inputs', input_row), ('gold', gold_row)]:
                key = f'{kind}.{split}'
                raw = (json.dumps(row) + '\n').encode('utf-8')
                (path / f'{key}.jsonl').write_bytes(raw)
                manifest['files'][key] = {'sha256': hashlib.sha256(raw).hexdigest()}
        if defect == 'count':
            manifest['documents'] = 4
        (path / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        if defect == 'hash':
            (path / 'inputs.dev.jsonl').write_bytes(b'{}\n')

    def test_dataset_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            self.make_dataset(path)
            docs, gold, hashes = load_dataset(path)
            self.assertEqual((len(docs), len(gold), len(hashes)), (3, 3, 6))

    def test_dataset_rejects_corruption(self):
        for defect in ('hash', 'duplicate', 'mismatch', 'unknown_type', 'missing_scorable', 'count'):
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as directory:
                path = Path(directory)
                self.make_dataset(path, defect)
                with self.assertRaises(ValueError):
                    load_dataset(path)


if __name__ == '__main__':
    unittest.main()
