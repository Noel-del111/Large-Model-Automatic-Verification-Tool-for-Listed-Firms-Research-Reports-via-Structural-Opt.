"""Budget enforcement must hold across retries, restarts and missing usage."""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from yjcheck.model import ModelConfig
from yjcheck.model_runtime import BudgetLedger, BudgetedChatClient, BudgetExceeded, ModelCallError, RuntimeSettings


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "cost.sqlite3"
        self.addCleanup(self.temp.cleanup)

    def test_restart_and_concurrent_reservations(self):
        ledger = BudgetLedger(self.path, "0.01")
        def reserve(_):
            try:
                return ledger.reserve("0.006", {})
            except BudgetExceeded:
                return None
        with ThreadPoolExecutor(4) as pool:
            outcomes = list(pool.map(reserve, range(4)))
        self.assertEqual(sum(bool(x) for x in outcomes), 1)
        reopened = BudgetLedger(self.path, "20")
        self.assertEqual(reopened.summary()["budget_cny"], .01)
        self.assertEqual(reopened.summary()["accounted_cny"], .006)

    def test_budget_increase_requires_explicit_authorization_and_preserves_charges(self):
        ledger = BudgetLedger(self.path, "50")
        self.assertEqual(ledger.summary()["budget_cny"], 20)
        first = ledger.reserve("2.5", {"historical": True})
        ledger.settle(first, "1.5", {"status": "ok", "historical": True})
        second = ledger.reserve("0.5", {"uncertain": True})
        with ledger.connect() as db:
            old_calls = db.execute("SELECT * FROM calls ORDER BY id").fetchall()
        self.assertEqual(BudgetLedger(self.path, "50").summary()["budget_cny"], 20)
        record = BudgetLedger.authorize_increase(self.path, "50", authorization_id="user-authorized-50",
                                                authorization_basis="提高到50元，完成全量验收")
        self.assertEqual((record["old_limit_cny"], record["new_limit_cny"]), (20, 50))
        self.assertEqual(record["accounted_cny_at_authorization"], 2)
        self.assertEqual(record["calls_at_authorization"], 2)
        self.assertFalse(record["replayed"])
        self.assertEqual(datetime.fromisoformat(record["created_at"]).utcoffset().total_seconds(), 0)
        reopened = BudgetLedger(self.path, "50")
        self.assertEqual(reopened.summary()["budget_cny"], 50)
        self.assertEqual(reopened.summary()["uncertain_calls"], 1)
        with reopened.connect() as db:
            self.assertEqual(db.execute("SELECT * FROM calls ORDER BY id").fetchall(), old_calls)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM budget_authorizations").fetchone()[0], 1)
        replay = BudgetLedger.authorize_increase(self.path, "50", authorization_id="user-authorized-50",
                                                authorization_basis="提高到50元，完成全量验收")
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["created_at"], record["created_at"])
        lowered = BudgetLedger(self.path, "1")
        self.assertEqual(lowered.summary()["budget_cny"], 1)
        self.assertEqual(lowered.summary()["accounted_cny"], 2)
        with self.assertRaises(BudgetExceeded):
            lowered.reserve("0", {})
        replay = BudgetLedger.authorize_increase(self.path, "50", authorization_id="user-authorized-50",
                                                authorization_basis="提高到50元，完成全量验收")
        self.assertEqual(replay["current_budget_cny"], 1)
        self.assertEqual(BudgetLedger(self.path, "50").summary()["budget_cny"], 1)

    def test_budget_authorization_rejects_invalid_over_cap_or_conflicting_replays(self):
        ledger = BudgetLedger(self.path)
        for value in ("100.000001", "101", "NaN", "-1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                BudgetLedger(self.path, value)
            with self.subTest(migration=value), self.assertRaises(ValueError):
                BudgetLedger.authorize_increase(self.path, value, authorization_id="id", authorization_basis="提高到50元，完成全量验收")
        for auth_id, basis in (("", "user"), ("id", "")):
            with self.assertRaises(ValueError):
                BudgetLedger.authorize_increase(self.path, "50", authorization_id=auth_id, authorization_basis=basis)
        with self.assertRaises(ValueError):
            BudgetLedger.authorize_increase(self.path.with_name("missing.sqlite3"), "50", authorization_id="id", authorization_basis="user")
        BudgetLedger.authorize_increase(self.path, "40", authorization_id="id", authorization_basis="user")
        for value, auth_id, basis in (("50", "id", "user"), ("40", "id", "changed"), ("30", "new", "user")):
            with self.subTest(value=value, auth_id=auth_id), self.assertRaises(ValueError):
                BudgetLedger.authorize_increase(self.path, value, authorization_id=auth_id, authorization_basis=basis)
        self.assertEqual(ledger.summary()["budget_cny"], 40)
        self.assertEqual(ledger.summary()["accounted_cny"], 0)

    def test_budget_authorization_cli_is_local_and_preserves_existing_ledger(self):
        import importlib.util
        from contextlib import redirect_stdout
        spec = importlib.util.spec_from_file_location("authorize_budget", Path(__file__).resolve().parents[2] / "evals/authorize_budget.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        ledger = BudgetLedger(self.path)
        ledger.reserve("0.5", {})
        output = io.StringIO()
        with redirect_stdout(output):
            result = module.main(["--ledger", str(self.path), "--limit-cny", "50", "--authorization-id", "cli-user-id",
                                  "--authorization-basis", "提高到50元，完成全量验收"])
        self.assertEqual(result, 0)
        record = json.loads(output.getvalue())
        self.assertEqual(record["current_accounted_cny"], .5)
        self.assertEqual(ledger.summary()["budget_cny"], 50)

    def test_second_authorization_updates_live_ledger_without_resetting_history(self):
        ledger = BudgetLedger(self.path, "50")
        BudgetLedger.authorize_increase(self.path, "50", authorization_id="first-50", authorization_basis="提高到50元")
        original = ledger.reserve("49", {"status": "reserved"})
        with self.assertRaises(BudgetExceeded):
            ledger.reserve("2", {})
        self.assertEqual(BudgetLedger(self.path, "100").summary()["budget_cny"], 50)
        record = BudgetLedger.authorize_increase(self.path, "100", authorization_id="second-100", authorization_basis="价格限制调整为100元")
        self.assertEqual((record["old_limit_cny"], record["new_limit_cny"]), (50, 100))
        # A running instance reads the same shared cap for each reservation.
        second = ledger.reserve("2", {})
        self.assertNotEqual(second, original)
        self.assertEqual(BudgetLedger(self.path, "100").summary()["accounted_cny"], 51)
        with self.assertRaises(BudgetExceeded):
            ledger.reserve("50", {})
        with ledger.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM calls").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM budget_authorizations").fetchone()[0], 2)

    def settings(self, **kw):
        values = dict(input_cny_per_mtok="10", output_cny_per_mtok="20", price_source="https://provider.example/prices",
                      price_date="2026-10-03", context_tokens=32768, max_output_tokens=128, ledger=self.path)
        values.update(kw)
        return RuntimeSettings(**values)

    def client(self, opener, **kw):
        return BudgetedChatClient(ModelConfig("https://provider.example/v1", "test", "secret-not-logged"),
                                  self.settings(**kw), opener=opener)

    def response(self, *, usage=True, finish_reason="stop"):
        result = {"choices": [{"message": {"content": '{"errors": []}'}, "finish_reason": finish_reason}]}
        if usage:
            result["usage"] = {"prompt_tokens": 10, "completion_tokens": 5}
        return io.BytesIO(json.dumps(result).encode())

    def test_provider_usage_settles_and_does_not_log_key(self):
        client = self.client(lambda *a, **k: self.response())
        reply = client([{"role": "user", "content": "test"}], purpose="baseline")
        self.assertEqual(reply["trace"]["usage_source"], "provider")
        self.assertAlmostEqual(client.ledger.summary()["accounted_cny"], .0002)
        with client.ledger.connect() as db:
            self.assertNotIn("secret-not-logged", str(db.execute("SELECT trace FROM calls").fetchall()))

    def test_missing_usage_keeps_maximum_charge(self):
        client = self.client(lambda *a, **k: self.response(usage=False))
        trace = client([{"role": "user", "content": "test"}], purpose="review")["trace"]
        self.assertEqual(trace["cost_cny"], trace["maximum_cny"])

    def test_retry_costs_are_not_refunded_and_errors_are_sanitized(self):
        def fail(*a, **kw):
            raise TimeoutError("PROVIDER_SECRET")
        client = self.client(fail, max_retries=1)
        with self.assertRaises(ModelCallError) as exc:
            client([{"role": "user", "content": "test"}])
        self.assertEqual(client.ledger.summary()["calls"], 2)
        self.assertNotIn("PROVIDER_SECRET", str(exc.exception))
        self.assertGreater(client.ledger.summary()["accounted_cny"], 0)

    def test_retry_trace_retains_all_calls_for_group_reporting(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "evals"))
        from run_v2 import runtime_calls
        for succeeds in (True, False):
            with self.subTest(succeeds=succeeds):
                attempts = []
                def opener(*args, **kwargs):
                    attempts.append(True)
                    if len(attempts) == 1 or not succeeds:
                        raise TimeoutError("PRIVATE_PROVIDER_BODY")
                    return self.response()
                client = self.client(opener, max_retries=1,
                                     ledger=self.path.with_name(f"retry-{succeeds}.sqlite3"))
                if succeeds:
                    trace = client([{"role": "user", "content": "test"}])["trace"]
                else:
                    with self.assertRaises(ModelCallError) as error:
                        client([{"role": "user", "content": "test"}])
                    trace = error.exception.trace
                self.assertIs(client.last_trace, trace)
                self.assertEqual(len(trace["previous_attempts"]), 1)
                first = trace["previous_attempts"][0]
                self.assertEqual(first["previous_attempts"], [])
                self.assertEqual(first["status"], "error")
                self.assertNotEqual(first["call_id"], trace["call_id"])
                self.assertNotIn("PRIVATE_PROVIDER_BODY", json.dumps(trace))
                calls = runtime_calls({"traces": [trace]})
                self.assertEqual(len(calls), 2)
                self.assertAlmostEqual(sum(float(call["cost_cny"]) for call in calls.values()),
                                       client.ledger.summary()["accounted_cny"], places=6)
                self.assertTrue(all("duration_seconds" in call for call in calls.values()))

    def test_budget_failure_does_not_call_provider(self):
        calls = []
        client = self.client(lambda *a, **k: calls.append(1), budget_cny="0")
        with self.assertRaises(BudgetExceeded):
            client([{"role": "user", "content": "test"}])
        self.assertEqual(calls, [])

    def test_output_truncation_is_failure(self):
        client = self.client(lambda *a, **k: self.response(finish_reason="length"))
        with self.assertRaises(ModelCallError) as error:
            client([{"role": "user", "content": "test"}])
        self.assertEqual(client.ledger.summary()["uncertain_calls"], 1)
        trace = error.exception.trace
        self.assertEqual(trace["error_code"], "model_output_truncated")
        self.assertEqual(trace["finish_reason"], "length")
        self.assertEqual(trace["response_content"], '{"errors": []}')
        self.assertTrue(trace["response_content_incomplete"])
        self.assertEqual(trace["output_tokens"], 5)

    def test_truncated_audit_redacts_credentials_and_keeps_response_identity(self):
        body = {"model": "test-resolved", "system_fingerprint": "fp123",
                "choices": [{"message": {"content": 'partial secret-not-logged'}, "finish_reason": "length"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
        client = self.client(lambda *a, **k: io.BytesIO(json.dumps(body).encode()))
        with self.assertRaises(ModelCallError) as error:
            client([{"role": "user", "content": "test"}])
        trace = error.exception.trace
        self.assertEqual(trace["response_content"], "partial [REDACTED]")
        self.assertEqual(trace["response_model"], "test-resolved")
        self.assertEqual(trace["system_fingerprint"], "fp123")
        with client.ledger.connect() as db:
            stored = db.execute("SELECT trace FROM calls").fetchone()[0]
        self.assertNotIn("secret-not-logged", stored)
        self.assertEqual(json.loads(stored)["error_code"], "model_output_truncated")

    def test_error_diagnostics_never_copy_provider_exception_messages(self):
        def fail(*args, **kwargs):
            raise ValueError("model_output_truncated PRIVATE_PROVIDER_BODY")
        client = self.client(fail)
        with self.assertRaises(ModelCallError) as error:
            client([{"role": "user", "content": "test"}])
        self.assertEqual(error.exception.trace["error_code"], "model_call_failed")
        self.assertNotIn("PRIVATE_PROVIDER_BODY", json.dumps(error.exception.trace))
        self.assertNotIn("response_content", error.exception.trace)

    def test_finish_reason_whitelist(self):
        client = self.client(lambda *a, **k: self.response(finish_reason="PRIVATE_PROVIDER_BODY"))
        trace = client([{"role": "user", "content": "test"}])["trace"]
        self.assertEqual(trace["finish_reason"], "unknown")
        self.assertNotIn("PRIVATE_PROVIDER_BODY", json.dumps(trace))

    def test_json_format_only_for_official_deepseek_json_tasks(self):
        for host, purpose, expected in (
            ("api.deepseek.com", "text_review.detect", True),
            ("api.deepseek.com", "text_review.global", True),
            ("api.deepseek.com", "facts-v1", True),
            ("api.deepseek.com", "assistant-explanation-v2", False),
            ("api.deepseek.com", "review", False),
            ("provider.example", "text_review.detect", False),
            ("api.deepseek.com.example", "facts-v1", False),
        ):
            with self.subTest(host=host, purpose=purpose):
                requests = []
                def opener(request, **kwargs):
                    requests.append(json.loads(request.data))
                    return self.response()
                client = BudgetedChatClient(ModelConfig(f"https://{host}/v1", "test", "secret-not-logged"),
                                            self.settings(), opener=opener)
                trace = client([{"role": "user", "content": "Return JSON."}], purpose=purpose)["trace"]
                self.assertEqual("response_format" in requests[0], expected)
                self.assertEqual("response_format" in trace, expected)
                if expected:
                    self.assertEqual(requests[0]["response_format"], {"type": "json_object"})

    def test_invalid_rates_and_limit_fail_before_network(self):
        for kw in ({"budget_cny": "101"}, {"input_cny_per_mtok": "NaN"}, {"output_cny_per_mtok": "-1"}):
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                self.client(lambda *a, **k: None, **kw)

    def test_price_expiry_validation_happens_before_any_call_or_reservation(self):
        attempts = []
        for expiry in ("PRIVATE_INVALID_DATE", "2026-10-05T00:00:00", "2026-10-05T00:00:00+08:00", "2000-01-01T00:00:00Z"):
            with self.subTest(expiry=expiry), self.assertRaises(ValueError) as error:
                self.client(lambda *a, **k: attempts.append(True), price_valid_until=expiry)
            self.assertNotIn("PRIVATE_INVALID_DATE", str(error.exception))
        self.assertEqual(attempts, [])
        self.assertFalse(self.path.exists())

    def test_price_expiry_setting_is_optional_and_loaded_without_config_side_effects(self):
        with patch("yjcheck.model_runtime.load_model_settings", return_value={}):
            self.assertEqual(RuntimeSettings.from_env(local=True).price_valid_until, "")
            self.assertEqual(RuntimeSettings.from_env(local=True).budget_cny, "20")
        with patch("yjcheck.model_runtime.load_model_settings", return_value={
                "YJCHECK_PRICE_VALID_UNTIL": "2099-01-01T00:00:00Z", "YJCHECK_BUDGET_CNY": "50"}):
            settings = RuntimeSettings.from_env(local=True)
            self.assertEqual(settings.price_valid_until, "2099-01-01T00:00:00Z")
            self.assertEqual(settings.budget_cny, "50")

    def test_price_expiry_is_rechecked_per_request_and_retry_and_logged(self):
        expiry = "2099-01-01T00:00:00+00:00"
        client = self.client(lambda *a, **k: self.response(), price_valid_until=expiry)
        trace = client([{"role": "user", "content": "test"}])["trace"]
        self.assertEqual(trace["price_valid_until"], expiry)
        with patch("yjcheck.model_runtime.datetime") as clock:
            clock.now.return_value = datetime(2099, 1, 1, tzinfo=timezone.utc)
            with self.assertRaisesRegex(ValueError, "价格核验有效期"):
                client([{"role": "user", "content": "test"}])
        self.assertEqual(client.ledger.summary()["calls"], 1)
        attempts = []
        def opener(*a, **k):
            attempts.append(True)
            client.price_valid_until = datetime(2000, 1, 1, tzinfo=timezone.utc)
            raise TimeoutError("PRIVATE_PROVIDER_BODY")
        client = self.client(opener, max_retries=1, price_valid_until=expiry)
        with self.assertRaisesRegex(ValueError, "价格核验有效期"):
            client([{"role": "user", "content": "test"}])
        self.assertEqual(len(attempts), 1)
        self.assertEqual(client.ledger.summary()["calls"], 2)
        self.assertEqual(client.ledger.summary()["uncertain_calls"], 1)

    def test_reasoning_effort_payload_and_trace_for_compatible_endpoints(self):
        for host in ("api.deepseek.com", "provider.example"):
            for thinking, effort in (("", ""), ("disabled", ""), ("", "low"),
                                     ("enabled", "low"), ("enabled", "high"), ("enabled", "max")):
                with self.subTest(host=host, thinking=thinking, effort=effort):
                    requests = []
                    def opener(request, **kwargs):
                        requests.append(json.loads(request.data))
                        return self.response()
                    config = ModelConfig(f"https://{host}/v1", "test", "secret-not-logged",
                                         thinking=thinking, reasoning_effort=effort)
                    client = BudgetedChatClient(config, self.settings(), opener=opener)
                    trace = client([{"role": "user", "content": "test"}])["trace"]
                    self.assertEqual(requests[0].get("reasoning_effort", ""), effort)
                    self.assertEqual("reasoning_effort" in requests[0], bool(effort))
                    self.assertEqual(trace["reasoning_effort"], effort)
                    self.assertEqual(trace["thinking"], thinking)
                    with client.ledger.connect() as db:
                        stored = json.loads(db.execute("SELECT trace FROM calls WHERE id=?", (trace["call_id"],)).fetchone()[0])
                    self.assertEqual(stored["reasoning_effort"], effort)
                    self.assertNotIn("secret-not-logged", json.dumps(stored))

    def test_invalid_or_mutated_reasoning_settings_rejected_before_spending(self):
        for effort in ("medium", "SECRET_INVALID_EFFORT", None, []):
            with self.subTest(effort=effort), self.assertRaises(ValueError) as error:
                ModelConfig("https://provider.example/v1", "test", "secret-not-logged", reasoning_effort=effort)
            self.assertNotIn("SECRET_INVALID_EFFORT", str(error.exception))
        calls = []
        config = ModelConfig("https://provider.example/v1", "test", "secret-not-logged", thinking="enabled", reasoning_effort="low")
        client = BudgetedChatClient(config, self.settings(), opener=lambda *a, **k: calls.append(True))
        config.thinking = "disabled"
        with self.assertRaisesRegex(ValueError, "disabled"):
            client([{"role": "user", "content": "test"}])
        self.assertEqual(calls, [])
        self.assertEqual(client.ledger.summary()["calls"], 0)
        with self.assertRaisesRegex(ValueError, "disabled"):
            BudgetedChatClient(config, self.settings(), opener=lambda *a, **k: calls.append(True))


if __name__ == "__main__":
    unittest.main()
