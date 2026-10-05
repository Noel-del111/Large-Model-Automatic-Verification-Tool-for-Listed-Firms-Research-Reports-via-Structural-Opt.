"""Human sign-off is required before normal candidates can become gold."""
import json
from pathlib import Path
import tempfile
import unittest

from evals.prepare_review_pack import import_approved, jsonl, prepare_review_pack, read_jsonl


class ReviewPackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "sft_data_wo_errors.json"
        self.source.write_text(json.dumps([
            {"name": "某行业点评", "origin_text": "收入为100万元。", "input": "不要复制提示词", "output": "不要复制答案"},
            {"name": "招标公告", "origin_text": "预算100万元。"}
        ], ensure_ascii=False), encoding="utf-8")
        self.pack = self.root / "review.jsonl"
        self.summary = prepare_review_pack(self.source, self.pack)

    def test_all_candidates_pending_and_no_source_labels_imported(self):
        records = read_jsonl(self.pack)
        self.assertEqual(self.summary["by_origin"], {"public_sft_original": 1, "synthetic_control": 6})
        for row in records:
            self.assertEqual(row["source_track"], "negative_review")
            self.assertEqual(row["human_review_status"], "pending")
            self.assertIsNone(row["reviewer"])
            self.assertFalse(row["eligible_for_blind_evaluation"])
            self.assertFalse({"errors", "input", "output", "answer"} & row.keys())
        with self.assertRaisesRegex(ValueError, "human approval"):
            import_approved(self.pack, self.root / "out")
        self.assertFalse((self.root / "out").exists())

    def approved_record(self):
        row = read_jsonl(self.pack)[0]
        return {**row, "human_review_status": "approved", "reviewer": "Test reviewer (fixture only)",
                "approved_at": "2026-10-03T10:00:00+08:00"}

    def test_approval_outputs_separate_inputs_gold_and_audit(self):
        row = self.approved_record()
        self.pack.write_text(jsonl([row]), encoding="utf-8")
        out = self.root / "approved"
        manifest = import_approved(self.pack, out)
        self.assertEqual(manifest["documents"], 1)
        self.assertEqual(read_jsonl(out / "gold.negative_review.jsonl"), [{"document_id": row["doc_id"], "errors": []}])
        self.assertEqual(read_jsonl(out / "approvals.jsonl")[0]["reviewer"], row["reviewer"])
        for item in read_jsonl(out / "inputs.negative_review.jsonl"):
            self.assertFalse({"errors", "approved_at", "reviewer", "review_comments", "review_note"} & item.keys())
        with self.assertRaises(FileExistsError):
            import_approved(self.pack, out)

    def test_unsigned_tampered_ambiguous_and_blind_records_rejected(self):
        for change in ({"reviewer": " "}, {"approved_at": None}, {"approved_at": "2026-10-03T10:00:00"},
                       {"content": "更改文本"}, {"eligible_for_blind_evaluation": True},
                       {"source_track": "eval_oct05"}, {"human_review_status": "rejected"}):
            with self.subTest(change=change):
                self.pack.write_text(jsonl([{**self.approved_record(), **change}]), encoding="utf-8")
                with self.assertRaises(ValueError):
                    import_approved(self.pack, self.root / "out")
                self.assertFalse((self.root / "out").exists())
                self.pack.unlink()
                prepare_review_pack(self.source, self.pack)

    def test_duplicate_and_partial_approvals_fail_before_any_write(self):
        signed = self.approved_record()
        pending = read_jsonl(self.pack)[1]
        for rows in ([signed, signed], [signed, pending]):
            self.pack.write_text(jsonl(rows), encoding="utf-8")
            with self.assertRaises(ValueError):
                import_approved(self.pack, self.root / "out")
            self.assertFalse((self.root / "out").exists())

    def test_prepare_never_overwrites_human_comments(self):
        before = self.pack.read_bytes()
        with self.assertRaises(FileExistsError):
            prepare_review_pack(self.source, self.pack)
        self.assertEqual(self.pack.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
