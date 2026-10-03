"""Reproducibility, leakage, grouping and audit checks for local dataset preparation."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from evals.dataset_prepare import SEED, SPLITS, _gold_errors, assign_splits, grouping_keys, prepare_dataset


def row(title, content, errors=None, scene="个股研报"):
    return {"title": title, "scene": scene, "content": content,
            "errors": errors if errors is not None else [{"start_idx": [0], "error_span": [content[:1]], "error_type": "数值单位错误"}]}


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class DatasetPreparationTests(unittest.TestCase):
    def test_malformed_annotations_are_retained_and_excluded(self):
        original = [None, {"start_idx": "bad", "error_span": ["甲"], "error_type": "x"},
                    {"start_idx": [0], "error_span": "甲", "error_type": "x"}]
        errors, excluded = _gold_errors({"content": "甲乙", "errors": original}, "d")
        self.assertEqual(len(errors), 3)
        self.assertEqual(len(excluded), 3)
        self.assertTrue(all(not e["scorable"] for e in errors))
        self.assertEqual([e["original_annotation"] for e in errors], original)

    def test_source_groups_are_transitive_and_company_aware(self):
        specs = [("同源", "甲"), ("同源", "乙"), ("另一个", " 乙 "),
                 ("测试股份有限公司年报", "丙"), ("测试股份有限公司半年报", "丁")]
        docs = [{"doc_id": str(i), "scene": "研报", "length_bucket": "lt_2000",
                 "group_keys": grouping_keys(title, content)} for i, (title, content) in enumerate(specs)]
        assign_splits(docs)
        self.assertEqual(len({d["group_id"] for d in docs[:3]}), 1)
        self.assertEqual(len({d["group_id"] for d in docs[3:]}), 1)
        self.assertEqual(len({d["split"] for d in docs[:3]}), 1)

    def test_preparation_is_reproducible_answer_separated_and_audited(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "fined_bench"
            source.mkdir()
            standard = [row(f"文档{i}", f"内容{i}甲乙") for i in range(30)]
            standard.append(row("多段", "甲中乙", [{"start_idx": [0, 2], "error_span": ["甲", "乙"], "error_type": "时间矛盾"}]))
            standard.append(row("歧义", "甲甲", [{"start_idx": [99], "error_span": ["甲"], "error_type": "冗余语句"}]))
            standard.append(row("错长", "甲乙", [{"start_idx": [], "error_span": ["甲"], "error_type": "冗余语句"}]))
            files = [(source / "eval_data.json", standard), (source / "eval_data_hard.json", [row("长文档", "长" * 40000, [], "公司章程")])]
            for path, rows in files:
                path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
            before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path, _ in files}
            first = prepare_dataset(root, root / "out1")
            second = prepare_dataset(root, root / "out2")
            self.assertEqual(first, second)
            self.assertEqual(first["seed"], SEED)
            self.assertEqual(first["documents"], 34)
            self.assertEqual(first["excluded_errors"], 2)
            documents, targets = [], []
            for split in SPLITS:
                documents.extend(read_jsonl(root / "out1" / f"inputs.{split}.jsonl"))
                targets.extend(read_jsonl(root / "out1" / f"gold.{split}.jsonl"))
                self.assertEqual((root / "out1" / f"inputs.{split}.jsonl").read_bytes(),
                                 (root / "out2" / f"inputs.{split}.jsonl").read_bytes())
            self.assertEqual(len({d["doc_id"] for d in documents}), 34)
            for document in documents:
                self.assertFalse({"errors", "error_type", "answer", "gold", "spans"} & document.keys())
                self.assertEqual(document["length_chars"], len(document["content"]))
            multi_id = next(d["doc_id"] for d in documents if d["title"] == "多段")
            multi = next(g for g in targets if g["document_id"] == multi_id)
            self.assertEqual(len(multi["errors"]), 1)
            self.assertEqual(len(multi["errors"][0]["spans"]), 2)
            self.assertTrue(multi["errors"][0]["scorable"])
            self.assertEqual(before, {path: hashlib.sha256(path.read_bytes()).hexdigest() for path, _ in files})
            examples = json.loads((root / "out1" / "examples.dev.json").read_text(encoding="utf-8"))
            self.assertLessEqual(len(examples), 3)
            by_id = {d["doc_id"]: d for d in documents}
            gold_by_id = {g["document_id"]: g for g in targets}
            for example in examples:
                self.assertEqual(example["source_split"], "dev")
                self.assertEqual(by_id[example["source_id"]]["split"], "dev")
                self.assertEqual(example["content"], by_id[example["source_id"]]["content"])
                self.assertEqual(len(example["errors"]), len(gold_by_id[example["source_id"]]["errors"]))
                self.assertTrue(example["complete_annotation"])
                self.assertLessEqual(len(example["content"]), 2000)
                self.assertLessEqual(len(example["errors"]), 4)
                for error in example["errors"]:
                    for span in error["spans"]:
                        self.assertEqual(example["content"][span["start"]:span["end"]], span["text"])

    def test_stable_ids_ignore_output_location_and_record_order_in_split_assignment(self):
        docs = [{"doc_id": str(i), "scene": "a" if i % 2 else "b", "length_bucket": "lt_2000",
                 "group_keys": [f"title:{i}"]} for i in range(100)]
        shuffled = [dict(d) for d in reversed(docs)]
        stats = assign_splits(docs)
        assign_splits(shuffled)
        self.assertEqual({d["doc_id"]: d["split"] for d in docs}, {d["doc_id"]: d["split"] for d in shuffled})
        self.assertEqual(stats["actual_documents"], {"dev": 60, "eval_oct05": 20, "holdout_oct07": 20})


if __name__ == "__main__":
    unittest.main()
