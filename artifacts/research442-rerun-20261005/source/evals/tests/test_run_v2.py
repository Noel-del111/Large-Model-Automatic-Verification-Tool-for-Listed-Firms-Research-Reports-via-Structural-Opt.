"""Runner boundary, resumption and budget contracts; every detector/client is mocked."""
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from evals import run_v2
from yjcheck import text_review


def input_document(identity="a", content="甲乙", **extra):
    return {"doc_id": identity, "title": identity, "scene": "个股研报", "content": content, **extra}


def complete_report(content, *, document_id, detector, **kwargs):
    return {"document_id": document_id, "detector": detector, "errors": [],
            "coverage": {"complete": True}, "traces": []}


class FakeClient:
    def __init__(self, config):
        self.config = config
        self.settings = SimpleNamespace(context_tokens=8000, max_output_tokens=1000)
        self.ledger = SimpleNamespace(summary=lambda: {"calls": 0, "actual_cost_cny": 0})


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inputs = self.root / "inputs.dev.jsonl"
        self.out = self.root / "run"
        self.write_inputs([input_document()])

    def write_inputs(self, documents):
        self.inputs.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in documents), encoding="utf-8")

    def test_user_stop_blocks_next_batch_before_any_client_or_input_read(self):
        run_v2.write_json(self.root / "data/v2/run_control.json", {
            "blocked_output_directories": [str(self.out)]})
        with patch.object(run_v2, "ROOT", self.root), patch.object(run_v2, "BudgetedChatClient") as client, \
                patch.object(run_v2, "read_jsonl") as reader:
            with self.assertRaisesRegex(ValueError, "200篇后停止"):
                run_v2.run(self.args(mode="model"))
        client.assert_not_called()
        reader.assert_not_called()
        self.assertFalse(self.out.exists())

    def args(self, **extra):
        return SimpleNamespace(**{"inputs": str(self.inputs), "examples": None, "out": str(self.out),
                                  "mode": "offline", "max_documents": 0, **extra})

    def execute(self, args=None):
        with redirect_stdout(io.StringIO()):
            return run_v2.run(args or self.args())

    def test_nested_gold_fields_are_rejected(self):
        for metadata in ({"errors": []}, {"nested": [{"answer": "secret"}]}, {"origin_text": "answer"},
                         {"error_type": "数值单位错误"}, {"gold_spans": []}, {"spans": []}, {"labels": ["x"]}):
            with self.assertRaisesRegex(ValueError, "答案字段"):
                run_v2.assert_input_only(input_document(metadata=metadata))

    def test_shared_response_cache_avoids_duplicate_payment_and_preserves_call_id(self):
        calls = []
        def provider(messages, **kwargs):
            calls.append(messages)
            return {"content": '{"errors": []}', "trace": {"call_id": "one", "status": "ok", "cost_cny": "0.01"}}
        client = run_v2.SharedCandidateClient(provider, self.root / "responses")
        messages = [{"role": "user", "content": "待查原文"}]
        one = client(messages)
        two = run_v2.SharedCandidateClient(provider, self.root / "responses")(messages)
        self.assertEqual(len(calls), 1)
        self.assertEqual(one["trace"]["call_id"], two["trace"]["call_id"])
        self.assertTrue(two["trace"]["reused_response"])

    def test_failed_paid_response_is_shared_without_second_paid_retry(self):
        calls = []
        def provider(messages, **kwargs):
            calls.append(messages)
            raise run_v2.ModelCallError("model_output_truncated", {"call_id": "one", "status": "error", "error_code": "model_output_truncated"})
        client = run_v2.SharedCandidateClient(provider, self.root / "responses")
        for _ in range(2):
            with self.assertRaises(run_v2.ModelCallError):
                client([{"role": "user", "content": "text"}])
        self.assertEqual(len(calls), 1)

    def test_candidate_quality_failure_does_not_trigger_paid_retries(self):
        self.assertTrue(run_v2.path_complete({"coverage": {"execution_complete": True, "complete": False}}, "model"))
        self.assertFalse(run_v2.path_complete({"coverage": {"execution_complete": False, "complete": False}}, "model"))

    def test_latency_estimate_adds_replayed_calls_once_without_double_counting_fresh_calls(self):
        trace = {"call_id": "call", "duration_seconds": 2.0}
        direct = run_v2.latency_summary([{"wall_seconds": 2.1, "traces": [trace]}])
        hybrid = run_v2.latency_summary([{"wall_seconds": .1, "traces": [
            {**trace, "reused_response": True}, {**trace, "reused_response": True}]}])
        self.assertEqual(direct["estimated_end_to_end_seconds"]["p50"], 2.1)
        self.assertEqual(hybrid["estimated_end_to_end_seconds"]["p50"], 2.1)
        self.assertEqual(hybrid["measured_arm_wall_seconds"]["p50"], .1)
        mixed = run_v2.latency_summary([{"wall_seconds": 2.2, "traces": [trace, {**trace, "reused_response": True}]}])
        self.assertEqual(mixed["estimated_end_to_end_seconds"]["p50"], 2.2)

    def test_replayed_retry_history_contributes_duration_and_missing_duration_is_not_zero(self):
        report = {"wall_seconds": .1, "traces": [{"call_id": "success", "duration_seconds": 2.,
            "reused_response": True, "previous_attempts": [{"call_id": "retry", "duration_seconds": 3.}]}]}
        self.assertAlmostEqual(run_v2.latency_summary([report])["estimated_end_to_end_seconds"]["p50"], 5.1)
        del report["traces"][0]["previous_attempts"][0]["duration_seconds"]
        summary = run_v2.latency_summary([report])
        self.assertIsNone(summary["estimated_end_to_end_seconds"]["p50"])
        self.assertEqual(summary["end_to_end_unavailable_documents"], 1)
        self.assertEqual(summary["measured_arm_wall_seconds"]["p50"], .1)

    def test_rejected_hints_are_false_positives_without_double_counting_direct_output(self):
        from evals.fined_bench_eval import score_paper_detection
        raw = {"error_type": "数值缺失", "spans": [{"text": "不存在原句"}], "reason": "缺数值"}
        rejection = {"candidate": raw, "reason": "absent", "job_index": 0}
        hybrid = {"document_id": "a", "errors": [], "rejected_candidates": [rejection]}
        direct = {**hybrid, "errors": [{"error_type": raw["error_type"], "reason": raw["reason"],
            "original_spans": raw["spans"], "spans": [], "invalid_anchor": True, "status": "needs_review"}]}
        for report in (hybrid, direct):
            hints = run_v2.all_review_hints(report)
            scored = score_paper_detection([hints], [{"document_id": "a", "errors": []}])
            self.assertEqual((scored["true_positive"], scored["false_positive"]), (0, 1))
            self.assertEqual(len(report["errors"]), 0 if report is hybrid else 1)
        direct["rejected_candidates"] = [rejection, rejection]
        self.assertEqual(len(run_v2.all_review_hints(direct)["errors"]), 2)

    def test_inference_never_reads_gold_and_filters_own_fewshot(self):
        examples = self.root / "examples.dev.json"
        examples.write_text(json.dumps([
            {"source_split": "dev", "source_id": "a", "document_id": "a", "content": "甲乙", "errors": []},
            {"source_split": "dev", "source_id": "b", "document_id": "b", "content": "丙丁", "errors": []}
        ], ensure_ascii=False), encoding="utf-8")
        original_read = Path.read_text

        def guarded(path, *args, **kwargs):
            self.assertFalse(path.name.startswith("gold"), f"Inference accessed {path}")
            return original_read(path, *args, **kwargs)

        with patch.object(Path, "read_text", guarded), patch.object(text_review, "detect_text", side_effect=complete_report) as detector:
            self.execute(self.args(examples=str(examples)))
        self.assertEqual(detector.call_count, 2)
        for call in detector.call_args_list:
            self.assertEqual([e["source_id"] for e in call.kwargs["examples"]], ["b"])

    def test_fewshot_source_id_is_checked_even_when_document_alias_differs(self):
        examples = self.root / "examples.dev.json"
        examples.write_text(json.dumps([{"source_split": "dev", "source_id": "a", "document_id": "alias", "content": "片段", "errors": []}], ensure_ascii=False), encoding="utf-8")
        with patch.object(text_review, "detect_text", side_effect=complete_report) as detector:
            self.execute(self.args(examples=str(examples)))
        self.assertTrue(all(not call.kwargs["examples"] for call in detector.call_args_list))

    def test_non_dev_examples_and_duplicate_input_ids_are_rejected(self):
        examples = self.root / "examples.json"
        examples.write_text(json.dumps([{"source_split": "holdout_oct07"}]), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "dev-only"):
            self.execute(self.args(examples=str(examples)))
        self.write_inputs([input_document(), input_document()])
        with self.assertRaisesRegex(ValueError, "duplicate input"):
            self.execute()

    def test_completed_cache_is_reused_and_bound_to_input_fingerprint(self):
        with patch.object(text_review, "detect_text", side_effect=complete_report) as detector:
            self.execute()
            self.execute()
            self.assertEqual(detector.call_count, 2)
            self.write_inputs([input_document(content="变更文本")])
            with self.assertRaisesRegex(ValueError, "新输出目录"):
                self.execute()

    def test_changed_examples_invalidate_cache(self):
        examples = self.root / "examples.dev.json"
        examples.write_text("[]", encoding="utf-8")
        with patch.object(text_review, "detect_text", side_effect=complete_report):
            self.execute(self.args(examples=str(examples)))
            examples.write_text('[{"source_split":"dev","source_id":"b","content":"x","errors":[]}]', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "新输出目录"):
                self.execute(self.args(examples=str(examples)))

    def test_incomplete_cached_detector_is_retried_without_repeating_completed_one(self):
        def first(content, **kwargs):
            result = complete_report(content, **kwargs)
            if kwargs["detector"] == "hybrid":
                result["coverage"] = {"complete": False, "reason": "temporary_failure"}
            return result
        with patch.object(text_review, "detect_text", side_effect=first):
            self.execute()
        with patch.object(text_review, "detect_text", side_effect=complete_report) as detector:
            self.execute()
            self.assertEqual(detector.call_count, 1)
            self.assertEqual(detector.call_args.kwargs["detector"], "hybrid")
        status = json.loads((self.out / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["completed_documents"], 1)

    def test_invalid_anchor_is_retained_as_unmatched_prediction(self):
        report = {"errors": [{"error_type": "x", "spans": [{"start": 0, "end": 1, "text": "假"}]}]}
        checked = run_v2.validate_predictions(report, "甲乙")
        self.assertEqual(len(checked["errors"]), 1)
        self.assertTrue(checked["errors"][0]["invalid_anchor"])
        self.assertEqual(checked["errors"][0]["spans"], [])

    def test_boolean_offsets_and_malformed_spans_are_invalid(self):
        for span in ({"start": False, "end": 1, "text": "甲"}, "not-an-object", None):
            with self.subTest(span=span):
                checked = run_v2.validate_predictions({"errors": [{"spans": [span]}]}, "甲乙")
                self.assertTrue(checked["errors"][0]["invalid_anchor"])

    def test_budget_stop_prevents_following_detector_and_does_not_claim_model_use(self):
        self.write_inputs([input_document("a"), input_document("b", "丙丁")])
        class BudgetExceeded(RuntimeError):
            pass
        def detect(content, **kwargs):
            if kwargs["detector"] == "model_direct":
                raise BudgetExceeded("test budget exhausted before any request")
            return complete_report(content, **kwargs)
        config = SimpleNamespace(model="mock", base_url="https://example.invalid", api_key="")
        with patch.object(run_v2.ModelConfig, "from_env", return_value=config), \
             patch.object(run_v2, "BudgetedChatClient", FakeClient), \
             patch.object(text_review, "detect_text", side_effect=detect) as detector:
            self.execute(self.args(mode="model"))
        self.assertEqual([call.kwargs["detector"] for call in detector.call_args_list], ["legacy_rules", "model_direct"])
        status = json.loads((self.out / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["stop_reason"], "budget_limit")
        self.assertFalse(status["real_model_evaluated"])

    def test_score_keeps_missing_planned_documents_in_gold_denominator(self):
        self.write_inputs([input_document("a"), input_document("b", "丙丁")])
        with patch.object(text_review, "detect_text", side_effect=complete_report):
            self.execute()
        # Simulate a process ending after the first document was persisted.
        missing_name = hashlib.sha256(b"b").hexdigest()[:24] + ".json"
        for detector in ("legacy_rules", "hybrid"):
            (self.out / "predictions" / detector / missing_name).unlink()
        gold_path = self.root / "gold.dev.jsonl"
        gold_path.write_text("".join(json.dumps({"document_id": identity, "errors": [
            {"id": identity, "type": "number", "scorable": True,
             "spans": [{"start": 0, "end": 1, "text": text}]}]}, ensure_ascii=False) + "\n"
            for identity, text in (("a", "甲"), ("b", "丙"))), encoding="utf-8")
        result_path = self.root / "score.json"
        args = SimpleNamespace(inputs=str(self.inputs), gold=str(gold_path), runs=str(self.out), out=str(result_path))
        with redirect_stdout(io.StringIO()):
            run_v2.score(args)
        result = json.loads(result_path.read_text(encoding="utf-8"))
        for scored in result["detectors"].values():
            self.assertEqual(scored["candidate_detection"]["false_negative"], 2)
            self.assertEqual(scored["candidate_detection"]["missing_prediction_documents"], ["b"])
        gold_path.write_text(json.dumps({"document_id": "a", "errors": []}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "gold记录"):
            run_v2.score(args)

    def test_negative_review_score_keeps_track_and_prediction_only_type(self):
        self.write_inputs([input_document(source_track="negative_review")])
        def false_alarm(content, **kwargs):
            report = complete_report(content, **kwargs)
            report["errors"] = [{"error_type": "数值单位错误", "status": "needs_review",
                                  "spans": [{"start": 0, "end": 1, "text": content[:1]}]}]
            return report
        with patch.object(text_review, "detect_text", side_effect=false_alarm):
            self.execute()
        gold_path = self.root / "gold.negative_review.jsonl"
        gold_path.write_text(json.dumps({"document_id": "a", "errors": []}), encoding="utf-8")
        result_path = self.root / "score.json"
        with redirect_stdout(io.StringIO()):
            run_v2.score(SimpleNamespace(inputs=str(self.inputs), gold=str(gold_path),
                                         runs=str(self.out), out=str(result_path)))
        result = json.loads(result_path.read_text(encoding="utf-8"))
        self.assertEqual(result["track"], "negative_review")
        for scored in result["detectors"].values():
            self.assertEqual(scored["candidate_detection"]["false_positive"], 1)
            self.assertEqual(scored["by_type"]["数值单位错误"]["false_positive"], 1)

    def test_fully_scorable_sensitivity_does_not_remove_full_queue_denominator(self):
        self.write_inputs([input_document("a"), input_document("b", "丙丁")])
        with patch.object(text_review, "detect_text", side_effect=complete_report):
            self.execute()
        gold_path = self.root / "gold.dev.jsonl"
        gold_path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in [
            {"document_id": "a", "errors": [{"type": "数值缺失", "scorable": True, "spans": [{"start": 0, "end": 1, "text": "甲"}]}]},
            {"document_id": "b", "errors": [{"type": "数值缺失", "scorable": False, "spans": []}]}]), encoding="utf-8")
        result_path = self.root / "score.json"
        with redirect_stdout(io.StringIO()):
            run_v2.score(SimpleNamespace(inputs=str(self.inputs), gold=str(gold_path), runs=str(self.out), out=str(result_path)))
        result = json.loads(result_path.read_text(encoding="utf-8"))
        self.assertEqual(result["requested_documents"], 2)
        for arm in result["detectors"].values():
            self.assertEqual(arm["candidate_detection"]["excluded_gold_errors"], 1)
            self.assertEqual(arm["fully_scorable_documents_sensitivity"]["documents"], 1)
            self.assertEqual(arm["fully_scorable_documents_sensitivity"]["excluded_documents"], 1)

    def test_score_distinguishes_response_parse_failure_from_successful_transport(self):
        def fake(content, **kwargs):
            report = complete_report(content, **kwargs)
            if kwargs["detector"] == "hybrid":
                report["coverage"] = {"execution_complete": False, "complete": False}
                report["traces"] = [{"status": "failed", "error_stage": "response_parse", "runtime_trace": {
                    "call_id": "paid", "status": "ok", "duration_seconds": 1., "cost_cny": "0.1"}}]
            return report
        with patch.object(text_review, "detect_text", side_effect=fake):
            self.execute()
        gold_path = self.root / "gold.dev.jsonl"
        gold_path.write_text(json.dumps({"document_id": "a", "errors": []}), encoding="utf-8")
        result_path = self.root / "score.json"
        with redirect_stdout(io.StringIO()):
            run_v2.score(SimpleNamespace(inputs=str(self.inputs), gold=str(gold_path), runs=str(self.out), out=str(result_path)))
        result = json.loads(result_path.read_text(encoding="utf-8"))
        self.assertEqual(result["unique_model_usage"]["transport_or_provider_failed_calls"], 0)
        self.assertEqual(result["unique_model_usage"]["response_parse_failed_calls"], 1)
        self.assertEqual(result["execution_summary"]["failed_or_missing_documents"], 1)
        self.assertEqual(result["detectors"]["hybrid"]["response_parse_failed_documents"], 1)
        self.assertEqual(result["detectors"]["hybrid"]["execution_failed_or_missing_documents"], 1)


if __name__ == "__main__":
    unittest.main()
