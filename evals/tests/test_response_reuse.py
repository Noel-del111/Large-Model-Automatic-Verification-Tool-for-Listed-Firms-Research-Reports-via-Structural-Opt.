"""Paid responses may cross runs only with exact requests and transport settings."""
from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from evals import run_v2


class ResponseReuseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.target = self.root / "target"
        self.messages = [{"role": "user", "content": "同一待查原文"}]
        self.purpose = "text_review.detect"
        self.spec = {"mode": "model", "model": "test-model", "endpoint_hash": "e" * 64,
                     "model_request": {"thinking": "", "reasoning_effort": "low", "timeout": 120},
                     "runtime": {"context_tokens": "20000", "max_output_tokens": "4096", "budget_cny": "20",
                                 "input_cny_per_mtok": "2", "output_cny_per_mtok": "8", "max_retries": "0",
                                 "price_date": "2026-10-03", "price_source": "official-price", "ledger": "same-ledger"},
                     "max_input_tokens": 15904, "source_hash": "a" * 64}
        self.trace = {"call_id": "original-paid-call", "status": "ok", "model": "test-model", "purpose": self.purpose,
                      "request_sha256": run_v2.request_messages_hash(self.messages), "max_output_tokens": 4096,
                      "thinking": "", "reasoning_effort": "low", "cost_cny": "0.015", "duration_seconds": 2.0,
                      "input_tokens": 100, "output_tokens": 10, "usage_source": "provider"}
        self.key = run_v2.request_cache_key(self.messages, self.purpose)
        run_v2.write_json(self.source / "run_config.json", self.spec)
        run_v2.write_json(self.source / "model_responses" / (self.key + ".json"), {"content": '{"errors": []}', "trace": self.trace})

    def prepare(self, spec=None, sources=None):
        return run_v2.prepare_response_reuse(sources or [self.source], spec or self.spec, self.target)

    def test_code_changes_allow_exact_replay_without_provider_or_answer_reads(self):
        current = {**self.spec, "source_hash": "b" * 64, "examples_sha256": "new serialization"}
        for filename in ("gold.dev.jsonl", "inputs.dev.jsonl", "predictions/hybrid/example.json"):
            path = self.source / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("must not read or copy", encoding="utf-8")
        original_read_bytes = Path.read_bytes
        reads = []

        def guarded(path):
            self.assertTrue(path.name == "run_config.json" or path.parent == self.source / "model_responses")
            reads.append(path)
            return original_read_bytes(path)

        with patch.object(Path, "read_bytes", guarded):
            manifests, records = self.prepare(current)
        self.assertEqual(len(reads), 2)
        self.assertEqual(manifests[0]["source_hash"], "a" * 64)
        self.assertEqual(manifests[0]["source_run_config_sha256"], run_v2.digest_file(self.source / "run_config.json"))
        run_v2.import_model_responses(self.target / "model_responses", records)
        provider = Mock(side_effect=AssertionError("Exact replay must not call provider"))
        reply = run_v2.SharedCandidateClient(provider, self.target / "model_responses")(self.messages, purpose=self.purpose)
        provider.assert_not_called()
        self.assertEqual(reply["trace"]["call_id"], "original-paid-call")
        self.assertTrue(reply["trace"]["reused_from_prior_run"])
        self.assertFalse((self.target / "predictions").exists())
        self.assertFalse((self.target / "gold.dev.jsonl").exists())

    def test_transport_mismatches_reject_before_cache_import_or_network(self):
        variants = [(("model",), "other-model"), (("endpoint_hash",), "f" * 64),
                    (("model_request", "reasoning_effort"), "high"), (("model_request", "thinking"), "disabled"),
                    (("model_request", "timeout"), 30), (("runtime", "context_tokens"), "32000"),
                    (("runtime", "max_output_tokens"), "8192"), (("runtime", "ledger"), "other-ledger"),
                    (("runtime", "input_cny_per_mtok"), "1"), (("runtime", "max_retries"), "1")]
        for path, value in variants:
            with self.subTest(field=path):
                changed = deepcopy(self.spec)
                owner = changed
                for key in path[:-1]:
                    owner = owner[key]
                owner[path[-1]] = value
                with self.assertRaisesRegex(ValueError, "调用前拒绝"):
                    self.prepare(changed)
        self.assertFalse(self.target.exists())

    def test_old_missing_price_expiry_matches_only_empty_default(self):
        current = deepcopy(self.spec)
        current["runtime"]["price_valid_until"] = ""
        _, records = self.prepare(current)
        self.assertEqual(len(records), 1)
        current["runtime"]["price_valid_until"] = "2026-10-04T16:00:00+00:00"
        with self.assertRaisesRegex(ValueError, "调用前拒绝"):
            self.prepare(current)

    def test_changed_message_or_purpose_is_a_cache_miss(self):
        _, records = self.prepare()
        run_v2.import_model_responses(self.target / "model_responses", records)
        provider = Mock(return_value={"content": '{"errors": []}', "trace": {"call_id": "new-call", "status": "ok"}})
        client = run_v2.SharedCandidateClient(provider, self.target / "model_responses")
        client([{"role": "user", "content": "改变后的待查原文"}], purpose=self.purpose)
        client(self.messages, purpose="text_review.global")
        self.assertEqual(provider.call_count, 2)

    def test_budget_only_change_retains_exact_reply_and_records_both_caps(self):
        current = deepcopy(self.spec)
        current["runtime"]["budget_cny"] = "100"
        manifests, records = self.prepare(current)
        self.assertEqual(manifests[0]["source_budget_cny"], "20")
        self.assertEqual(manifests[0]["target_budget_cny"], "100")
        run_v2.import_model_responses(self.target / "model_responses", records)
        provider = Mock(side_effect=AssertionError("Budget migration must not repeat paid inference"))
        reply = run_v2.SharedCandidateClient(provider, self.target / "model_responses")(self.messages, purpose=self.purpose)
        provider.assert_not_called()
        self.assertEqual(reply["trace"]["call_id"], "original-paid-call")

    def test_failed_records_are_skipped_but_successful_retry_keeps_prior_cost(self):
        failed = {"call_id": "failed-call", "status": "error", "maximum_cny": "0.02", "duration_seconds": 1.0}
        run_v2.write_json(self.source / "model_responses" / ("f" * 64 + ".json"), {"failed": True, "trace": failed, "error_code": "transport_error"})
        success = {**self.trace, "previous_attempts": [failed]}
        run_v2.write_json(self.source / "model_responses" / (self.key + ".json"), {"content": '{"errors": []}', "trace": success})
        manifests, records = self.prepare()
        self.assertEqual((manifests[0]["successful_records"], manifests[0]["failed_records_skipped"]), (1, 1))
        run_v2.import_model_responses(self.target / "model_responses", records)
        replay = run_v2.SharedCandidateClient(Mock(), self.target / "model_responses")(self.messages, purpose=self.purpose)
        calls = run_v2.runtime_calls({"traces": [replay["trace"], replay["trace"]]})
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(call["reused_from_prior_run"] for call in calls.values()))
        self.assertAlmostEqual(sum(float(call.get("cost_cny", call.get("maximum_cny", 0))) for call in calls.values()), .035)

    def test_invalid_filename_and_request_hash_fail_closed(self):
        bad = self.source / "model_responses" / "not-a-sha.json"
        run_v2.write_json(bad, {"content": '{}', "trace": self.trace})
        with self.assertRaisesRegex(ValueError, "文件名"):
            self.prepare()
        bad.unlink()
        changed = {**self.trace, "request_sha256": "b" * 64}
        run_v2.write_json(self.source / "model_responses" / (self.key + ".json"), {"content": '{}', "trace": changed})
        _, records = self.prepare()
        run_v2.import_model_responses(self.target / "model_responses", records)
        provider = Mock(side_effect=AssertionError("Invalid hash must not trigger provider"))
        with self.assertRaisesRegex(ValueError, "当前消息不匹配"):
            run_v2.SharedCandidateClient(provider, self.target / "model_responses")(self.messages, purpose=self.purpose)
        provider.assert_not_called()

    def test_multiple_sources_are_deterministic_and_conflicting_responses_rejected(self):
        second = self.root / "second"
        run_v2.write_json(second / "run_config.json", self.spec)
        run_v2.write_json(second / "model_responses" / (self.key + ".json"), {"content": '{"errors": []}', "trace": self.trace})
        manifests, records = self.prepare(sources=[self.source, second, self.source])
        self.assertEqual(len(manifests), 2)
        self.assertEqual(len(records), 1)
        run_v2.write_json(second / "model_responses" / (self.key + ".json"), {"content": '{"errors": [1]}', "trace": self.trace})
        with self.assertRaisesRegex(ValueError, "不同模型返回"):
            self.prepare(sources=[self.source, second])

    def test_scoring_separates_historical_cost_and_deduplicates_both_arms(self):
        _, records = self.prepare()
        run_v2.import_model_responses(self.target / "model_responses", records)
        replay = run_v2.SharedCandidateClient(Mock(), self.target / "model_responses")(self.messages, purpose=self.purpose)
        inputs, gold = self.root / "inputs.dev.jsonl", self.root / "gold.dev.jsonl"
        inputs.write_text(json.dumps({"doc_id": "a", "content": "甲", "scene": "研报"}), encoding="utf-8")
        gold.write_text(json.dumps({"document_id": "a", "errors": []}), encoding="utf-8")
        run_v2.write_json(self.target / "run_config.json", {**self.spec, "inputs_sha256": run_v2.digest_file(inputs), "detectors": ["model_direct", "hybrid"]})
        run_v2.write_json(self.target / "queue.json", {"document_ids": ["a"]})
        for detector in ("model_direct", "hybrid"):
            run_v2.write_json(self.target / "predictions" / detector / "a.json", {"document_id": "a", "errors": [],
                "coverage": {"complete": True}, "wall_seconds": 0.01, "traces": [replay["trace"]]})
        score_path = self.root / "score.json"
        with redirect_stdout(io.StringIO()):
            run_v2.score(SimpleNamespace(inputs=inputs, gold=gold, runs=self.target, out=score_path))
        usage = json.loads(score_path.read_text(encoding="utf-8"))["unique_model_usage"]
        self.assertEqual(usage["calls"], 1)
        self.assertEqual(usage["prior_run_calls_reused"], 1)
        self.assertEqual(usage["newly_incurred_cny"], 0)
        self.assertEqual(usage["previously_incurred_cny_reused"], .015)


if __name__ == "__main__":
    unittest.main()
