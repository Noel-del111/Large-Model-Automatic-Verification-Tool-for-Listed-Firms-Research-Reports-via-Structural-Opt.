"""Evidence-level acceptance of the frozen instance diagnostic export."""
from collections import Counter, defaultdict
import hashlib
import importlib.util
import json
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "artifacts/research442-rerun-20261005"
BUILDER = ROOT / "tools/build_case_diagnostics.py"
spec = importlib.util.spec_from_file_location("case_builder", BUILDER)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def cli(package, out):
    return subprocess.run([sys.executable, "-I", "-B", str(BUILDER), "--package", str(package),
                           "--out", str(out)], capture_output=True, encoding="utf-8")


def source_inventory():
    return {p.relative_to(PACKAGE).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in (PACKAGE / "source").rglob("*") if p.is_file()}


class CaseDiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.out = Path(cls.temporary.name) / "pack"
        cls.inventory_before = source_inventory()
        result = cli(PACKAGE, cls.out)
        if result.returncode:
            raise AssertionError(result.stderr)
        cls.selection = read(cls.out / "selection.json")
        cls.provenance = read(cls.out / "provenance.json")
        cls.cases = [read(p) for p in sorted((cls.out / "cases").glob("*.evidence.json"))]
        cls.documents = {d["document_id"]: d for d in
                         (read(p) for p in (cls.out / "documents").glob("*.json"))}
        cls.canonical_cases = rows(PACKAGE / "verification/cases.jsonl")
        cls.golds = {x["document_id"]: x for x in rows(PACKAGE / "inputs/gold.research442.jsonl")}

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_exact_instance_strata_and_deterministic_selection(self):
        self.assertEqual(len(self.cases), 64)
        failures = [c for c in self.cases if c["cohort"] == "diagnostic"]
        controls = [c for c in self.cases if c["cohort"] == "control"]
        self.assertEqual((len(failures), len(controls)), (52, 12))
        self.assertEqual(Counter(c["result"] for c in failures), {"FP": 28, "FN": 24})
        self.assertEqual({c["result"] for c in controls}, {"TP"})
        strata = Counter((c["error_type"], c["result"]) for c in failures)
        self.assertEqual(set(strata.values()), {2})
        self.assertEqual(len({c["error_type"] for c in controls}), 12)
        self.assertEqual(len({builder.identity(c) for c in self.cases}), 64)
        shuffled = list(self.canonical_cases)
        random.Random(20261005).shuffle(shuffled)
        failures2, controls2 = builder.sample_cases(shuffled)
        self.assertEqual([builder.identity(c) for c in self.cases],
                         [builder.identity(c) for c in failures2 + controls2])
        available = defaultdict(set)
        for c in self.canonical_cases:
            if c["arm"] == "hybrid":
                available[builder.kind(c), c["result"]].add(c["document_id"])
        for stratum in strata:
            picked = {c["document_id"] for c in failures if (c["error_type"], c["result"]) == stratum}
            self.assertEqual(len(picked), min(2, len(available[stratum])))

    def test_full_replay_matches_frozen_score_with_excluded_gold_indices(self):
        self.assertTrue(self.provenance["hybrid_case_replay_exact_match"])
        score = read(PACKAGE / "verification/score.json")["detectors"]["hybrid"]["all_review_hints_detection"]
        expected = {label: score[key] for label, key in
                    (("TP", "true_positive"), ("FP", "false_positive"), ("FN", "false_negative"))}
        self.assertEqual(self.provenance["hybrid_totals"], expected)
        original_index_after_exclusion = 0
        for case in self.canonical_cases:
            if case["arm"] != "hybrid" or case["gold_index"] is None:
                continue
            golds = self.golds[case["document_id"]]["errors"]
            index = case["gold_index"]
            self.assertEqual(case["gold"], golds[index])
            self.assertTrue(golds[index].get("scorable", True))
            original_index_after_exclusion += any(not g.get("scorable", True) for g in golds[:index])
        # The canonical corpus actually exercises the index-shift hazard;
        # merely checking a document containing only scorable gold would not.
        self.assertGreater(original_index_after_exclusion, 0)

    def test_selected_objects_use_all_hints_and_original_gold_arrays(self):
        invalid_hints = 0
        for case in self.cases:
            document = self.documents[case["document_id"]]
            self.assertEqual(document["gold"], self.golds[case["document_id"]])
            if case["all_hints_index"] is not None:
                hint = document["all_hints"][case["all_hints_index"]]
                self.assertEqual(case["prediction"], hint)
                if hint.get("invalid_anchor"):
                    invalid_hints += 1
                    self.assertFalse(hint["spans"])
                    self.assertGreaterEqual(case["all_hints_index"], len(document["report"]["errors"]))
            if case["gold_index"] is not None:
                self.assertEqual(case["gold"], document["gold"]["errors"][case["gold_index"]])
            paired = [tuple(pair) for pair in document["matched_pairs"]]
            if case["result"] == "TP":
                self.assertIn((case["all_hints_index"], case["gold_index"]), paired)
            elif case["result"] == "FP":
                self.assertNotIn(case["all_hints_index"], {p for p, _ in paired})
            else:
                self.assertNotIn(case["gold_index"], {g for _, g in paired})
        self.assertEqual(invalid_hints, 6)

    def test_related_fp_fn_are_evidence_not_invented_root_causes(self):
        for case in self.cases:
            self.assertEqual(case["root_cause"], "pending_human_review")
            self.assertEqual(case["review"]["status"], "pending_human_review")
            self.assertIsNone(case["review"]["human_conclusion"])
            document = self.documents[case["document_id"]]
            for related in case["related_predictions"]:
                self.assertEqual(related["candidate"], document["all_hints"][related["all_hints_index"]])
                self.assertNotIn("root_cause", related)
            for related in case["related_golds"]:
                self.assertEqual(related["gold"], document["gold"]["errors"][related["gold_index"]])
                self.assertNotIn("root_cause", related)
            checks = case["scoring_checks"]
            keys = [(row["all_hints_index"], row["gold_index"]) for row in checks]
            self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(self.provenance["human_finalized_cases"], 0)

    def test_response_links_match_frozen_bytes_and_prediction_calls(self):
        for document in self.documents.values():
            expected = {t["runtime_trace"]["call_id"]: t for t in document["report"]["traces"]}
            self.assertEqual({x["trace"]["call_id"] for x in document["responses"]}, set(expected))
            for response in document["responses"]:
                path = PACKAGE / response["source"]
                original = read(path)
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), response["sha256"])
                self.assertEqual(response["content"], original["content"])
                self.assertEqual(response["trace"], original["trace"])
                self.assertEqual(response["cache_request"], original["cache_request"])
                trace = expected[response["trace"]["call_id"]]
                self.assertEqual(response["job_index"], trace["job_index"])
                self.assertEqual(response["trace"]["request_sha256"], trace["runtime_trace"]["request_sha256"])

    def test_cli_preserves_existing_output_and_frozen_source_inventory(self):
        sentinel = self.out / "human-review-sentinel.txt"
        sentinel.write_text("must not be overwritten", encoding="utf-8")
        result = cli(PACKAGE, self.out)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("overwrite", result.stderr)
        self.assertEqual(sentinel.read_text(), "must not be overwritten")
        forbidden = PACKAGE / ("must-not-exist-" + uuid.uuid4().hex)
        result = cli(PACKAGE, forbidden)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("outside the frozen package", result.stderr)
        self.assertFalse(forbidden.exists())
        self.assertEqual(source_inventory(), self.inventory_before)

    def test_tampered_derived_payload_is_rejected_even_when_identity_is_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "package"
            shutil.copytree(PACKAGE, package, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            target = self.cases[0]
            path = package / "verification/cases.jsonl"
            cases = rows(path)
            for case in cases:
                if case["arm"] == "hybrid" and builder.identity(case) == builder.identity(target):
                    case["prediction"]["reason"] = "INJECTED BODY WITH UNCHANGED INDICES"
                    break
            path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in cases), encoding="utf-8")
            first = cli(package, Path(directory) / "unverified-output")
            self.assertNotEqual(first.returncode, 0)
            self.assertIn("Frozen verification mismatch", first.stderr)
            verification_manifest = package / "verification/manifest.json"
            manifest = read(verification_manifest)
            for row in manifest:
                if row["path"] == "cases.jsonl":
                    row["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            verification_manifest.write_text(json.dumps(manifest), encoding="utf-8")
            second = cli(package, Path(directory) / "rehashed-output")
            self.assertNotEqual(second.returncode, 0)
            self.assertIn("Selected prediction differs from source", second.stderr)

    def test_committed_pack_evidence_matches_fresh_export(self):
        existing = ROOT / "artifacts/case-diagnostics-20261005"
        # Compact checkouts contain authored notes and lightweight evidence,
        # not duplicated document/request caches. Supplements append after
        # the original 64 identities; the default builder remains that base.
        self.assertEqual(read(existing / "selection.json")["cases"][:len(self.cases)], self.selection["cases"])
        for case in self.cases:
            path = existing / "cases" / (case["case_id"] + ".evidence.json")
            if path.exists():
                self.assertEqual(read(path), case)
            analysis = read(existing / "cases" / (case["case_id"] + ".analysis.json"))
            self.assertEqual(analysis["case_id"], case["case_id"])
            self.assertTrue(analysis["evidence"])


if __name__ == "__main__":
    unittest.main()
