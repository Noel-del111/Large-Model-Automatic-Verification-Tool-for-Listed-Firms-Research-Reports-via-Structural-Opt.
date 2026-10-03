"""Protect split isolation and unchanged labels during few-shot reason authoring."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch as mock_patch

from evals.curate_dev_examples import curate_examples, example_signature, write_curated_examples


class CuratedExampleTests(unittest.TestCase):
    def setUp(self):
        self.examples = [{"source_split": "dev", "source_id": "dev1", "document_id": "dev1",
                          "content": "三项共10元，其中4元、3元和2元。", "complete_annotation": True,
                          "errors": [{"error_type": "计算错误", "reason": "开发集示例",
                                      "spans": [{"start": 0, "end": 6, "text": "三项共10元"}]}]}]
        self.inputs = [{"doc_id": "dev1", "split": "dev", "content": self.examples[0]["content"]}]
        self.patch = {"schema_version": "1.0.0", "revision": "test1", "source_split": "dev",
                      "documents": [{"source_id": "dev1", "example_sha256_without_reasons": example_signature(self.examples[0]),
                                     "reasons": ["三项金额合计9元，与10元不符。"]}]}

    def test_only_reasons_change_and_input_is_not_mutated(self):
        before = deepcopy(self.examples)
        result = curate_examples(self.examples, self.patch, self.inputs)
        self.assertEqual(self.examples, before)
        self.assertEqual(result[0]["errors"][0]["reason"], "三项金额合计9元，与10元不符。")
        self.assertEqual(example_signature(result[0]), example_signature(before[0]))
        self.assertEqual(curate_examples(result, self.patch, self.inputs), result)

    def test_changed_content_type_offsets_or_metadata_fail_closed(self):
        changes = [("content", "另一份原文"), ("complete_annotation", False), ("new_metadata", "x")]
        for key, value in changes:
            with self.subTest(field=key):
                changed = deepcopy(self.examples)
                changed[0][key] = value
                with self.assertRaises(ValueError):
                    curate_examples(changed, self.patch, self.inputs)
        for key, value in [("error_type", "语义矛盾"), ("spans", [{"start": 1, "end": 7, "text": "项共10元，"}])]:
            with self.subTest(field=key):
                changed = deepcopy(self.examples)
                changed[0]["errors"][0][key] = value
                with self.assertRaises(ValueError):
                    curate_examples(changed, self.patch, self.inputs)

    def test_non_dev_provenance_missing_ids_and_mismatched_content_are_rejected(self):
        for split in ("eval_oct05", "holdout_oct07"):
            with self.subTest(split=split):
                example = deepcopy(self.examples)
                example[0]["source_split"] = split
                with self.assertRaises(ValueError):
                    curate_examples(example, self.patch, self.inputs)
                documents = deepcopy(self.inputs)
                documents[0]["split"] = split
                with self.assertRaises(ValueError):
                    curate_examples(self.examples, self.patch, documents)
        for documents in ([], [{"doc_id": "dev1", "split": "dev", "content": "篡改的正文"}], self.inputs * 2):
            with self.subTest(documents=documents):
                with self.assertRaises(ValueError):
                    curate_examples(self.examples, self.patch, documents)

    def test_changed_patch_membership_and_incomplete_reasons_are_rejected(self):
        for documents in ([], self.patch["documents"] * 2):
            with self.subTest(documents=documents):
                changed = {**self.patch, "documents": documents}
                with self.assertRaises(ValueError):
                    curate_examples(self.examples, changed, self.inputs)
        for reasons in ([], [""], ["长" * 81], ["理由", "多余理由"], [None]):
            with self.subTest(reasons=reasons):
                changed = deepcopy(self.patch)
                changed["documents"][0]["reasons"] = reasons
                with self.assertRaises(ValueError):
                    curate_examples(self.examples, changed, self.inputs)

    def test_artifacts_are_reproducible_audited_and_read_only_dev_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            examples, patch, inputs = (root / name for name in ("examples.json", "patch.json", "inputs.dev.jsonl"))
            examples.write_text(json.dumps(self.examples, ensure_ascii=False), encoding="utf-8")
            patch.write_text(json.dumps(self.patch, ensure_ascii=False), encoding="utf-8")
            inputs.write_text(json.dumps(self.inputs[0], ensure_ascii=False) + "\n", encoding="utf-8")
            input_bytes = {path: path.read_bytes() for path in (examples, patch, inputs)}
            original_read_bytes = Path.read_bytes
            reads = []

            def guarded_read(path):
                self.assertIn(path, input_bytes, "Curator must not open gold or held-out files")
                reads.append(path)
                return original_read_bytes(path)

            with mock_patch.object(Path, "read_bytes", guarded_read):
                audit = write_curated_examples(examples, patch, inputs, root / "out1.json", root / "audit1.json")
                second = write_curated_examples(examples, patch, inputs, root / "out2.json", root / "audit2.json")
            self.assertEqual(set(reads), set(input_bytes))
            self.assertEqual(audit, second)
            self.assertEqual((root / "out1.json").read_bytes(), (root / "out2.json").read_bytes())
            self.assertEqual((root / "audit1.json").read_bytes(), (root / "audit2.json").read_bytes())
            self.assertEqual(audit["updated_examples_sha256"], hashlib.sha256((root / "out1.json").read_bytes()).hexdigest())
            self.assertEqual(input_bytes, {path: path.read_bytes() for path in input_bytes})

    def test_inputs_cannot_be_overwritten(self):
        with self.assertRaises(ValueError):
            write_curated_examples(Path("examples.json"), Path("patch.json"), Path("inputs.jsonl"),
                                   Path("examples.json"), Path("audit.json"))


if __name__ == "__main__":
    unittest.main()
