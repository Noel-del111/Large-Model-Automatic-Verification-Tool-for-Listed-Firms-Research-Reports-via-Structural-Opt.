"""Offline provenance checks; copied evidence fixtures never touch the package."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "artifacts/research442-rerun-20261005"
spec = importlib.util.spec_from_file_location("reconstruct442", ROOT / "tools/reconstruct_research442_requests.py")
reconstruct = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reconstruct)


class ReconstructionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document_id = json.loads((PACKAGE / "run/queue.json").read_text())["document_ids"][0]
        cls.reference = reconstruct.reconstruct_document_requests(PACKAGE, cls.document_id)
        cls.response_path = cls.reference["requests"][0]["response_source"]

    def fixture(self, destination):
        paths = ["manifest.json", "run/run_config.json", "run/queue.json",
                 "inputs/inputs.research442.jsonl", "inputs/examples.dev.v2.json",
                 "source/evals/run_v2.py", "source/evals/baseline.py", self.response_path]
        paths += [p.relative_to(PACKAGE).as_posix()
                  for p in (PACKAGE / "source/factcheck/src/yjcheck").glob("*.py")]
        for relative in paths:
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(PACKAGE / relative, target)
        return destination

    def update_manifest(self, package, relative):
        path = package / relative
        rows = json.loads((package / "manifest.json").read_text())
        for row in rows:
            if row["path"] == relative:
                row["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
                row["bytes"] = path.stat().st_size
        (package / "manifest.json").write_text(json.dumps(rows), encoding="utf-8")

    def test_real_frozen_request_matches_both_historical_fingerprints(self):
        record = self.reference
        self.assertEqual(record["status"], "reconstructed_verified")
        self.assertEqual(len(record["requests"]), 1)
        call = record["requests"][0]
        self.assertTrue(call["cache_match"])
        self.assertTrue(call["trace_match"])
        self.assertEqual(call["messages_sha256"], reconstruct.request_messages_hash(call["messages"]))
        self.assertIn(self.document_id, call["messages"][-1]["content"])
        self.assertIn("not an original HTTP", record["limitations"][0])
        self.assertEqual(record["provenance"]["new_model_calls"], 0)

    def test_current_imports_are_not_replaced(self):
        import sys
        sentinel = object()
        old = sys.modules.get("yjcheck", sentinel)
        sys.modules["yjcheck"] = sentinel
        try:
            result = reconstruct.reconstruct_document_requests(PACKAGE, self.document_id)
            self.assertEqual(result["status"], "reconstructed_verified")
            self.assertIs(sys.modules["yjcheck"], sentinel)
        finally:
            if old is sentinel:
                sys.modules.pop("yjcheck", None)
            else:
                sys.modules["yjcheck"] = old

    def test_missing_response_is_unverified_and_does_not_create_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            package = self.fixture(Path(directory))
            (package / self.response_path).unlink()
            before = {p.relative_to(package).as_posix() for p in package.rglob("*")}
            result = reconstruct.reconstruct_document_requests(package, self.document_id)
            self.assertEqual(result["status"], "reconstructed_unverified")
            self.assertEqual(result["requests"][0]["status"], "cache_missing")
            self.assertIsNone(result["requests"][0]["trace_match"])
            self.assertEqual(before, {p.relative_to(package).as_posix() for p in package.rglob("*")})

    def test_trace_mismatch_is_not_a_verified_reconstruction(self):
        with tempfile.TemporaryDirectory() as directory:
            package = self.fixture(Path(directory))
            path = package / self.response_path
            record = json.loads(path.read_text(encoding="utf-8"))
            record["trace"]["request_sha256"] = "0" * 64
            path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
            self.update_manifest(package, self.response_path)
            result = reconstruct.reconstruct_document_requests(package, self.document_id)
            call = result["requests"][0]
            self.assertEqual(call["status"], "fingerprint_mismatch")
            self.assertTrue(call["cache_match"])
            self.assertFalse(call["trace_match"])
            self.assertEqual(result["status"], "reconstructed_unverified")

    def test_tampered_response_fails_integrity_despite_detector_catching_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            package = self.fixture(Path(directory))
            path = package / self.response_path
            path.write_bytes(path.read_bytes() + b" ")
            with self.assertRaisesRegex(ValueError, "Package hash mismatch"):
                reconstruct.reconstruct_document_requests(package, self.document_id)

    def test_gold_is_not_needed_for_reconstruction(self):
        with tempfile.TemporaryDirectory() as directory:
            package = self.fixture(Path(directory))
            self.assertFalse((package / "inputs/gold.research442.jsonl").exists())
            result = reconstruct.reconstruct_document_requests(package, self.document_id)
            self.assertEqual(result["status"], "reconstructed_verified")
            self.assertFalse(list(package.rglob("__pycache__")))

    def test_worker_blocks_network_even_for_rehashed_source(self):
        with tempfile.TemporaryDirectory() as directory:
            package = self.fixture(Path(directory))
            relative = "source/factcheck/src/yjcheck/__init__.py"
            path = package / relative
            path.write_text(path.read_text(encoding="utf-8") +
                            '\nimport socket\nsocket.create_connection(("127.0.0.1", 9))\n', encoding="utf-8")
            self.update_manifest(package, relative)
            code = sorted((package / "source/factcheck/src/yjcheck").glob("*.py"))
            code += [package / "source/evals/run_v2.py", package / "source/evals/baseline.py"]
            config_path = package / "run/run_config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["source_hash"] = hashlib.sha256("".join(hashlib.sha256(p.read_bytes()).hexdigest()
                                                           for p in code).encode()).hexdigest()
            config_path.write_text(json.dumps(config), encoding="utf-8")
            self.update_manifest(package, "run/run_config.json")
            with self.assertRaisesRegex(ValueError, "forbids network"):
                reconstruct.reconstruct_document_requests(package, self.document_id)

    def test_unknown_document_and_duplicate_selection_fail(self):
        with self.assertRaisesRegex(ValueError, "outside the frozen queue"):
            reconstruct.reconstruct_document_requests(PACKAGE, "not-a-document")
        with self.assertRaisesRegex(ValueError, "unique"):
            reconstruct.reconstruct_requests(PACKAGE, [self.document_id, self.document_id])


if __name__ == "__main__":
    unittest.main()
