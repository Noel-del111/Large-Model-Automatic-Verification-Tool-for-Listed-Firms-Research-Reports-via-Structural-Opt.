"""Local credentials must remain local; environment priority is explicit."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from yjcheck.model import ModelConfig
from yjcheck.model_runtime import BudgetedChatClient, RuntimeSettings, configuration_status
from yjcheck.model_settings import ALLOWED_KEYS, load_model_settings


class LocalModelSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config_path = Path(self.temp.name) / "local_model_config.json"
        self.addCleanup(patch.stopall)
        patch("yjcheck.model_settings.DEFAULT_CONFIG_PATH", self.config_path).start()
        patch.dict(os.environ, {}, clear=True).start()

    def save(self, values):
        self.config_path.write_text(json.dumps(values), encoding="utf-8")

    def settings(self):
        return {
            "YJCHECK_BASE_URL": "https://provider.example/v1", "YJCHECK_MODEL": "test-model",
            "YJCHECK_API_KEY": "SECRET_LOCAL_VALUE", "YJCHECK_TIMEOUT": 120,
            "YJCHECK_THINKING": "disabled", "YJCHECK_INPUT_CNY_PER_MTOK": "10",
            "YJCHECK_OUTPUT_CNY_PER_MTOK": "20", "YJCHECK_PRICE_SOURCE": "https://provider.example/prices",
            "YJCHECK_PRICE_DATE": "2026-10-03", "YJCHECK_CONTEXT_TOKENS": 32768,
            "YJCHECK_MAX_OUTPUT_TOKENS": 128, "YJCHECK_BUDGET_CNY": "20",
        }

    def test_model_runtime_status_share_local_settings_without_env_mutation(self):
        self.save(self.settings())
        before = dict(os.environ)
        model = ModelConfig.from_env()
        runtime = RuntimeSettings.from_env()
        status = configuration_status()
        self.assertEqual(model.api_key, "SECRET_LOCAL_VALUE")
        self.assertEqual(model.timeout, 120)
        self.assertEqual(model.thinking, "disabled")
        self.assertEqual(runtime.context_tokens, 32768)
        self.assertEqual(runtime.max_output_tokens, 128)
        self.assertEqual(runtime.input_cny_per_mtok, "10")
        self.assertEqual(set(status), set(ALLOWED_KEYS))
        self.assertTrue(status["YJCHECK_API_KEY"])
        self.assertTrue(all(isinstance(value, bool) for value in status.values()))
        self.assertNotIn("SECRET_LOCAL_VALUE", json.dumps(status))
        self.assertEqual(dict(os.environ), before)

    def test_environment_overrides_local_including_explicit_empty_values(self):
        self.save(self.settings())
        with patch.dict(os.environ, {"YJCHECK_MODEL": "environment-model", "YJCHECK_API_KEY": "",
                                     "YJCHECK_THINKING": "", "YJCHECK_MAX_OUTPUT_TOKENS": "256"}):
            self.assertEqual(ModelConfig.from_env().model, "environment-model")
            self.assertEqual(ModelConfig.from_env().api_key, "")
            self.assertEqual(ModelConfig.from_env().thinking, "")
            self.assertEqual(RuntimeSettings.from_env().max_output_tokens, 256)
            self.assertFalse(configuration_status()["YJCHECK_API_KEY"])

    def test_missing_file_preserves_defaults_and_required_price_failure(self):
        self.assertEqual(load_model_settings(), {})
        self.assertEqual(ModelConfig.from_env().timeout, 30)
        self.assertEqual(ModelConfig.from_env().thinking, "")
        self.assertEqual(ModelConfig.from_env().reasoning_effort, "")
        self.assertFalse(any(configuration_status().values()))
        with self.assertRaisesRegex(ValueError, "YJCHECK_INPUT_CNY_PER_MTOK"):
            RuntimeSettings.from_env()

    def test_invalid_json_and_unknown_fields_do_not_echo_values(self):
        for text in ('{"YJCHECK_API_KEY":"SECRET_LOCAL_VALUE",',
                     '["SECRET_LOCAL_VALUE"]',
                     '{"UNTRUSTED_SECRET_FIELD":"SECRET_LOCAL_VALUE"}',
                     '{"YJCHECK_API_KEY":"first","YJCHECK_API_KEY":"SECRET_LOCAL_VALUE"}',
                     '{"YJCHECK_API_KEY":{"nested":"SECRET_LOCAL_VALUE"}}',
                     '{"YJCHECK_TIMEOUT":NaN}'):
            with self.subTest(text=text):
                self.config_path.write_text(text, encoding="utf-8")
                with self.assertRaises(ValueError) as error:
                    load_model_settings()
                self.assertNotIn("SECRET_LOCAL_VALUE", str(error.exception))
                self.assertNotIn("UNTRUSTED_SECRET_FIELD", str(error.exception))

    def test_configuration_is_data_not_executable(self):
        literal = "__import__('os').environ.update({'EXECUTED': 'yes'})"
        self.save({"YJCHECK_API_KEY": literal})
        self.assertEqual(ModelConfig.from_env().api_key, literal)
        self.assertNotIn("EXECUTED", os.environ)

    def test_config_size_is_bounded_and_errors_redacted(self):
        self.config_path.write_text("SECRET_LOCAL_VALUE" * 5000, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "大小限制") as error:
            load_model_settings()
        self.assertNotIn("SECRET_LOCAL_VALUE", str(error.exception))

    def test_timeout_thinking_and_runtime_integer_errors_hide_values(self):
        for key in ("YJCHECK_TIMEOUT", "YJCHECK_THINKING", "YJCHECK_REASONING_EFFORT", "YJCHECK_CONTEXT_TOKENS"):
            self.save({**self.settings(), key: "SECRET_LOCAL_VALUE"})
            with self.subTest(key=key), self.assertRaises(ValueError) as error:
                if key == "YJCHECK_CONTEXT_TOKENS":
                    RuntimeSettings.from_env()
                else:
                    ModelConfig.from_env()
            self.assertNotIn("SECRET_LOCAL_VALUE", str(error.exception))
        for value in ("0", "-2", "NaN", "Infinity"):
            self.save({**self.settings(), "YJCHECK_TIMEOUT": value})
            with self.subTest(timeout=value), self.assertRaises(ValueError):
                ModelConfig.from_env()

    def test_reasoning_effort_local_setting_environment_override_and_conflict(self):
        self.save({**self.settings(), "YJCHECK_THINKING": "enabled", "YJCHECK_REASONING_EFFORT": "low"})
        self.assertEqual(ModelConfig.from_env().reasoning_effort, "low")
        self.assertTrue(configuration_status()["YJCHECK_REASONING_EFFORT"])
        with patch.dict(os.environ, {"YJCHECK_REASONING_EFFORT": "max"}):
            self.assertEqual(ModelConfig.from_env().reasoning_effort, "max")
        with patch.dict(os.environ, {"YJCHECK_REASONING_EFFORT": "", "YJCHECK_THINKING": "disabled"}):
            self.assertEqual(ModelConfig.from_env().reasoning_effort, "")
            self.assertFalse(configuration_status()["YJCHECK_REASONING_EFFORT"])
        with patch.dict(os.environ, {"YJCHECK_THINKING": "disabled"}):
            with self.assertRaisesRegex(ValueError, "disabled"):
                ModelConfig.from_env()

    def test_explicit_thinking_payload_response_metadata_and_upper_bound_cost(self):
        self.save(self.settings())
        model = ModelConfig.from_env()
        runtime = RuntimeSettings(input_cny_per_mtok="10", output_cny_per_mtok="20",
                                  price_source="https://provider.example/prices", price_date="2026-10-03",
                                  context_tokens=32768, max_output_tokens=128,
                                  ledger=Path(self.temp.name) / "budget.sqlite3")
        captured = []
        def opener(request, *, timeout):
            captured.append((json.loads(request.data), timeout))
            return io.BytesIO(json.dumps({"model": "resolved-model-version", "system_fingerprint": "fp_test",
                  "choices": [{"message": {"content": '{"errors": []}'}, "finish_reason": "stop"}],
                  "usage": {"prompt_tokens": 10, "completion_tokens": 5, "prompt_cache_hit_tokens": 10}}).encode())
        client = BudgetedChatClient(model, runtime, opener=opener)
        trace = client([{"role": "user", "content": "sample"}])["trace"]
        payload, timeout = captured[0]
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertEqual(payload["temperature"], 0)
        self.assertEqual(timeout, 120)
        self.assertEqual(trace["response_model"], "resolved-model-version")
        self.assertEqual(trace["system_fingerprint"], "fp_test")
        self.assertEqual(trace["cost_basis"], "configured_rate_upper_bound")
        self.assertEqual(trace["input_tokens"], 10)
        self.assertEqual(trace["cost_cny"], "0.0002")
        self.assertEqual(client.ledger.summary()["cost_basis"], "configured_rate_upper_bound")
        self.assertNotIn("SECRET_LOCAL_VALUE", json.dumps(trace))
        model.thinking = ""
        client([{"role": "user", "content": "sample"}])
        self.assertNotIn("thinking", captured[1][0])

    def test_price_validation_does_not_echo_invalid_config_values(self):
        for key in ("YJCHECK_PRICE_DATE", "YJCHECK_INPUT_CNY_PER_MTOK"):
            self.save({**self.settings(), key: "SECRET_LOCAL_VALUE"})
            with self.subTest(key=key), self.assertRaises(ValueError) as error:
                BudgetedChatClient(ModelConfig.from_env(), RuntimeSettings.from_env())
            self.assertNotIn("SECRET_LOCAL_VALUE", str(error.exception))

    def test_missing_remote_key_rejected_before_network_or_ledger(self):
        calls = []
        ledger = Path(self.temp.name) / "no-call.sqlite3"
        settings = RuntimeSettings(ledger=ledger)
        for key in ("", "   "):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "YJCHECK_API_KEY"):
                BudgetedChatClient(ModelConfig("https://provider.example/v1", "test", key), settings,
                                   opener=lambda *args, **kwargs: calls.append(True))
        self.assertEqual(calls, [])
        self.assertFalse(ledger.exists())
        # Explicit loopback mocks remain usable without any authentication value.
        client = BudgetedChatClient(ModelConfig("http://127.0.0.1:1234/v1", "test"), settings,
                                    opener=lambda *args, **kwargs: calls.append(True))
        self.assertTrue(client.local)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
