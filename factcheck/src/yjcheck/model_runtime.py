"""Auditable, fail-closed Chat Completions transport for the shared V2 budget.

Reservations survive crashes and uncertain provider failures. UTF-8 byte counts
are conservative token estimates, not measured tokenizer counts. Provider usage
is used for settlement; absent usage keeps the maximum reservation charged.
"""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import urllib.request
from urllib.parse import urlparse
import uuid

from .model_settings import ALLOWED_KEYS, load_model_settings, validate_reasoning_settings

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_LEDGER = ROOT / "data/v2/model_usage.sqlite3"
MICROS = Decimal(1_000_000)
DEFAULT_BUDGET_CNY = Decimal("20")
AUTHORIZED_BUDGET_CEILING_CNY = Decimal("100")
JSON_PURPOSES = frozenset({"facts-v1", "text_review.detect", "text_review.global"})
SAFE_FINISH_REASONS = frozenset({"stop", "length", "tool_calls", "function_call", "content_filter", "insufficient_system_resource"})
LOCAL_ERROR_CODES = frozenset({"model_response_too_large", "invalid_model_response", "invalid_model_choices",
                               "provider_usage_exceeds_reserved_maximum", "model_output_truncated", "invalid_model_content"})


class _ResponseValidationError(ValueError):
    """Only these locally generated codes may be copied into an audit record."""


def _error_code(exc):
    if isinstance(exc, _ResponseValidationError) and str(exc) in LOCAL_ERROR_CODES:
        return str(exc)
    if isinstance(exc, json.JSONDecodeError):
        return "invalid_response_json"
    if isinstance(exc, TimeoutError):
        return "transport_timeout"
    if isinstance(exc, OSError):
        return "transport_error"
    return "model_call_failed"


class BudgetExceeded(RuntimeError):
    pass


class ModelCallError(RuntimeError):
    def __init__(self, reason, trace):
        super().__init__(reason)
        self.trace = trace


def estimate_tokens(messages):
    """Upper estimate for byte-tokenized chat payloads, including envelope margin."""
    return len(json.dumps(messages, ensure_ascii=False).encode("utf-8")) + 256


def _amount(value):
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ValueError("Price/budget must be a finite nonnegative number") from None
    if not result.is_finite() or result < 0:
        raise ValueError("Price/budget must be a finite nonnegative number")
    return result


@dataclass(frozen=True)
class RuntimeSettings:
    input_cny_per_mtok: str = "0"
    output_cny_per_mtok: str = "0"
    price_source: str = ""
    price_date: str = ""
    context_tokens: int = 32768
    max_output_tokens: int = 4096
    budget_cny: str = "20"
    ledger: Path = DEFAULT_LEDGER
    max_retries: int = 0
    price_valid_until: str = ""

    @classmethod
    def from_env(cls, *, local=False):
        settings = load_model_settings()
        names = ("INPUT_CNY_PER_MTOK", "OUTPUT_CNY_PER_MTOK", "PRICE_SOURCE", "PRICE_DATE", "CONTEXT_TOKENS")
        if not local:
            missing = ["YJCHECK_" + name for name in names if not settings.get("YJCHECK_" + name)]
            if missing:
                raise ValueError("真实调用前需配置价格与上下文：" + ", ".join(missing))
        integers = {}
        for name, default in (("CONTEXT_TOKENS", "32768"), ("MAX_OUTPUT_TOKENS", "4096"), ("MAX_RETRIES", "0")):
            try:
                integers[name] = int(settings.get("YJCHECK_" + name, default))
            except (TypeError, ValueError):
                raise ValueError("配置字段须为整数：YJCHECK_" + name) from None
        return cls(
            settings.get("YJCHECK_INPUT_CNY_PER_MTOK", "0"),
            settings.get("YJCHECK_OUTPUT_CNY_PER_MTOK", "0"),
            settings.get("YJCHECK_PRICE_SOURCE", "local-mock" if local else ""),
            settings.get("YJCHECK_PRICE_DATE", date.today().isoformat() if local else ""),
            integers["CONTEXT_TOKENS"], integers["MAX_OUTPUT_TOKENS"],
            settings.get("YJCHECK_BUDGET_CNY", "20"), DEFAULT_LEDGER.with_name("mock_usage.sqlite3") if local else DEFAULT_LEDGER,
            integers["MAX_RETRIES"],
            settings.get("YJCHECK_PRICE_VALID_UNTIL", ""),
        )


class BudgetLedger:
    """SQLite transactions prevent simultaneous callers overspending the budget."""
    def __init__(self, path, limit_cny="20"):
        self.path = Path(path)
        limit = _amount(limit_cny)
        if limit > AUTHORIZED_BUDGET_CEILING_CNY:
            raise ValueError(f"本轮已授权累计预算不得超过{AUTHORIZED_BUDGET_CEILING_CNY}元")
        self.limit = int(limit * MICROS)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS budget (singleton INTEGER PRIMARY KEY, limit_micro INTEGER NOT NULL)")
            # A configuration value alone never authorizes the expanded budget.
            # New ledgers start at at most 20; existing ledgers retain their cap.
            initial_limit = min(self.limit, int(DEFAULT_BUDGET_CNY * MICROS))
            db.execute("INSERT OR IGNORE INTO budget VALUES (1, ?)", (initial_limit,))
            # Lowering is allowed. Reopening a ledger must never reset spent money.
            db.execute("UPDATE budget SET limit_micro=MIN(limit_micro, ?) WHERE singleton=1", (self.limit,))
            db.execute("CREATE TABLE IF NOT EXISTS calls (id TEXT PRIMARY KEY, charge_micro INTEGER NOT NULL, status TEXT NOT NULL, trace TEXT NOT NULL)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            with db:
                yield db
        finally:
            db.close()

    @classmethod
    def authorize_increase(cls, path, limit_cny, *, authorization_id, authorization_basis):
        """Explicit, audited migration; preserve every historical call and charge.

        A repeated authorization ID is idempotent, including after a later cap
        decrease: replaying it cannot silently restore the former higher cap.
        Caller must supply the direct human authorization, never infer approval
        from the configured value or available account balance.
        """
        limit = _amount(limit_cny)
        if limit > AUTHORIZED_BUDGET_CEILING_CNY:
            raise ValueError(f"本轮已授权累计预算不得超过{AUTHORIZED_BUDGET_CEILING_CNY}元")
        if not isinstance(authorization_id, str) or not authorization_id.strip() or len(authorization_id) > 200:
            raise ValueError("预算迁移需要非空、至多200字符的授权标识")
        if not isinstance(authorization_basis, str) or not authorization_basis.strip() or len(authorization_basis) > 2000:
            raise ValueError("预算迁移需要非空、至多2000字符的直接用户授权依据")
        ledger = cls.__new__(cls)
        ledger.path = Path(path)
        if not ledger.path.is_file():
            raise ValueError("预算迁移只适用于已存在的账本；不得建立新账本绕过历史费用")
        new_limit = int(limit * MICROS)
        with ledger.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT limit_micro FROM budget WHERE singleton=1").fetchone()
            if current is None:
                raise ValueError("账本缺少当前预算，不能进行授权迁移")
            old_limit = current[0]
            used, calls = db.execute("SELECT COALESCE(SUM(charge_micro),0),COUNT(*) FROM calls").fetchone()
            db.execute("CREATE TABLE IF NOT EXISTS budget_authorizations (authorization_id TEXT PRIMARY KEY, "
                       "created_at TEXT NOT NULL, old_limit_micro INTEGER NOT NULL, new_limit_micro INTEGER NOT NULL, "
                       "authorization_basis TEXT NOT NULL, accounted_micro INTEGER NOT NULL, calls INTEGER NOT NULL)")
            previous = db.execute("SELECT created_at,old_limit_micro,new_limit_micro,authorization_basis,accounted_micro,calls "
                                  "FROM budget_authorizations WHERE authorization_id=?", (authorization_id,)).fetchone()
            if previous:
                if previous[2] != new_limit or previous[3] != authorization_basis:
                    raise ValueError("授权标识已用于不同的预算迁移，不能覆盖原审计记录")
                created_at, recorded_old, recorded_new, basis, recorded_used, recorded_calls = previous
                replayed = True
            else:
                if new_limit <= old_limit:
                    raise ValueError("显式授权迁移仅用于提高上限；降低请使用普通账本配置")
                created_at = datetime.now(timezone.utc).isoformat()
                recorded_old, recorded_new, basis = old_limit, new_limit, authorization_basis
                recorded_used, recorded_calls = used, calls
                db.execute("INSERT INTO budget_authorizations VALUES (?,?,?,?,?,?,?)",
                           (authorization_id, created_at, old_limit, new_limit, basis, used, calls))
                db.execute("UPDATE budget SET limit_micro=? WHERE singleton=1", (new_limit,))
                replayed = False
            current_limit = db.execute("SELECT limit_micro FROM budget WHERE singleton=1").fetchone()[0]
        return {"authorization_id": authorization_id, "created_at": created_at,
                "old_limit_cny": recorded_old / 1_000_000, "new_limit_cny": recorded_new / 1_000_000,
                "authorization_basis": basis, "accounted_cny_at_authorization": recorded_used / 1_000_000,
                "calls_at_authorization": recorded_calls, "current_budget_cny": current_limit / 1_000_000,
                "current_accounted_cny": used / 1_000_000, "current_calls": calls, "replayed": replayed}

    def reserve(self, cost, trace):
        micros = int((_amount(cost) * MICROS).to_integral_value(rounding=ROUND_CEILING))
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            limit = db.execute("SELECT limit_micro FROM budget WHERE singleton=1").fetchone()[0]
            used = db.execute("SELECT COALESCE(SUM(charge_micro),0) FROM calls").fetchone()[0]
            if any(json.loads(row[0]).get("reservation_exceeded") for row in db.execute("SELECT trace FROM calls WHERE status='error'")):
                raise BudgetExceeded("提供方用量超出预留上界，后续调用已停止，需核对定价和token上界")
            if used + micros > limit:
                raise BudgetExceeded("下一次请求的最大费用将超过累计预算，已停止")
            call_id = uuid.uuid4().hex
            db.execute("INSERT INTO calls VALUES (?,?,?,?)", (call_id, micros, "reserved", json.dumps(trace, ensure_ascii=False)))
        return call_id

    def settle(self, call_id, cost, trace):
        micros = int((_amount(cost) * MICROS).to_integral_value(rounding=ROUND_CEILING))
        with self.connect() as db:
            db.execute("UPDATE calls SET charge_micro=?,status=?,trace=? WHERE id=?",
                       (micros, trace["status"], json.dumps(trace, ensure_ascii=False), call_id))

    def summary(self):
        with self.connect() as db:
            limit = db.execute("SELECT limit_micro FROM budget WHERE singleton=1").fetchone()[0]
            rows = db.execute("SELECT charge_micro,status FROM calls").fetchall()
        return {"budget_cny": limit / 1_000_000, "accounted_cny": sum(r[0] for r in rows) / 1_000_000,
                "calls": len(rows), "uncertain_calls": sum(r[1] != "ok" for r in rows),
                "cost_basis": "configured_rate_upper_bound",
                "note": "Conservative accounting at configured upper rates, not a provider invoice. Unknown usage and interrupted calls retain the maximum reservation."}


class BudgetedChatClient:
    def __init__(self, config, settings=None, *, opener=None):
        parsed = urlparse(config.base_url)
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("模型地址必须是不含凭据、查询或片段的HTTP(S) URL")
        self.local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        self.deepseek_json = parsed.hostname == "api.deepseek.com"
        if parsed.scheme == "http" and not self.local:
            raise ValueError("远程模型端点必须使用HTTPS")
        if not config.model or not parsed.hostname:
            raise ValueError("模型名称和端点不能为空")
        if not self.local and (not isinstance(config.api_key, str) or not config.api_key.strip()):
            raise ValueError("远程模型调用前需配置 YJCHECK_API_KEY")
        validate_reasoning_settings(getattr(config, "thinking", ""), getattr(config, "reasoning_effort", ""))
        self.config = config
        self.settings = settings or RuntimeSettings.from_env(local=self.local)
        self.price_valid_until = None
        if self.settings.price_valid_until:
            try:
                expiry = datetime.fromisoformat(self.settings.price_valid_until)
                if expiry.tzinfo is None or expiry.utcoffset() != timezone.utc.utcoffset(expiry):
                    raise ValueError
            except (TypeError, ValueError):
                raise ValueError("YJCHECK_PRICE_VALID_UNTIL 必须是带UTC时区的ISO日期时间") from None
            self.price_valid_until = expiry
        self._check_price_validity()
        self.input_rate = _amount(self.settings.input_cny_per_mtok)
        self.output_rate = _amount(self.settings.output_cny_per_mtok)
        if not self.local:
            if not self.settings.price_source.startswith("https://"):
                raise ValueError("真实调用需要官方价格来源链接")
            try:
                date.fromisoformat(self.settings.price_date)
            except (ValueError, TypeError):
                raise ValueError("YJCHECK_PRICE_DATE 必须为有效的 YYYY-MM-DD 日期") from None
            if not (self.input_rate or self.output_rate):
                raise ValueError("真实调用不能默认按零价结算")
        if self.settings.context_tokens <= self.settings.max_output_tokens + 512 or self.settings.max_output_tokens <= 0:
            raise ValueError("无效上下文或输出预算")
        if self.settings.max_retries not in {0, 1}:
            raise ValueError("重试次数只允许0或1，重试也计费")
        self.ledger = BudgetLedger(self.settings.ledger, self.settings.budget_cny)
        self.opener = opener or urllib.request.urlopen
        self.last_trace = None

    def _check_price_validity(self):
        if self.price_valid_until is not None and datetime.now(timezone.utc) >= self.price_valid_until:
            raise ValueError("已超过价格核验有效期；请重新核对费率后再发起调用")

    def __call__(self, messages, *, purpose="review"):
        # ModelConfig is mutable; reject conflicting changes before reserving funds.
        validate_reasoning_settings(getattr(self.config, "thinking", ""), getattr(self.config, "reasoning_effort", ""))
        estimated = estimate_tokens(messages)
        if estimated + self.settings.max_output_tokens > self.settings.context_tokens:
            raise ValueError("context_budget_exceeded: input is not silently truncated")
        maximum = (Decimal(estimated) * self.input_rate + Decimal(self.settings.max_output_tokens) * self.output_rate) / MICROS
        previous_attempts = []
        for attempt in range(self.settings.max_retries + 1):
            self._check_price_validity()
            trace = {"model": self.config.model, "purpose": purpose, "attempt": attempt,
                     "previous_attempts": list(previous_attempts),
                     "created_at": datetime.now(timezone.utc).isoformat(),
                     "request_sha256": hashlib.sha256(json.dumps(messages, ensure_ascii=False).encode()).hexdigest(),
                     "input_token_estimate": estimated, "estimate_method": "utf8_bytes_upper_bound",
                     "max_output_tokens": self.settings.max_output_tokens,
                     "price_source": self.settings.price_source, "price_date": self.settings.price_date,
                     "price_valid_until": self.settings.price_valid_until,
                     "input_cny_per_mtok": str(self.input_rate), "output_cny_per_mtok": str(self.output_rate),
                     "cost_basis": "configured_rate_upper_bound", "thinking": getattr(self.config, "thinking", ""),
                     "reasoning_effort": getattr(self.config, "reasoning_effort", ""),
                     "maximum_cny": str(maximum), "status": "reserved", "local_endpoint": self.local}
            call_id = self.ledger.reserve(maximum, trace)
            trace["call_id"] = call_id
            started = time.monotonic()
            settled_cost = maximum
            try:
                payload = {"model": self.config.model, "temperature": 0, "max_tokens": self.settings.max_output_tokens, "messages": messages}
                if self.deepseek_json and purpose in JSON_PURPOSES:
                    payload["response_format"] = {"type": "json_object"}
                    trace["response_format"] = "json_object"
                if getattr(self.config, "thinking", ""):
                    payload["thinking"] = {"type": self.config.thinking}
                if getattr(self.config, "reasoning_effort", ""):
                    payload["reasoning_effort"] = self.config.reasoning_effort
                headers = {"Content-Type": "application/json"}
                if self.config.api_key:
                    headers["Authorization"] = "Bearer " + self.config.api_key
                req = urllib.request.Request(self.config.base_url.rstrip("/") + "/chat/completions",
                                             json.dumps(payload, ensure_ascii=False).encode(), headers, method="POST")
                with self.opener(req, timeout=self.config.timeout) as response:
                    raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise _ResponseValidationError("model_response_too_large")
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise _ResponseValidationError("invalid_model_response")
                trace["response_model"] = result.get("model") if isinstance(result.get("model"), str) else None
                trace["system_fingerprint"] = result.get("system_fingerprint") if isinstance(result.get("system_fingerprint"), str) else None
                choices = result.get("choices")
                if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                    raise _ResponseValidationError("invalid_model_choices")
                choice = choices[0]
                finish = choice.get("finish_reason")
                trace["finish_reason"] = finish if isinstance(finish, str) and finish in SAFE_FINISH_REASONS else "unknown"
                usage = result.get("usage") or {}
                if not isinstance(usage, dict):
                    usage = {}
                inp, out = usage.get("prompt_tokens"), usage.get("completion_tokens")
                valid = all(isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in (inp, out))
                if valid:
                    settled_cost = (Decimal(inp) * self.input_rate + Decimal(out) * self.output_rate) / MICROS
                    trace.update({"input_tokens": inp, "output_tokens": out, "usage_source": "provider"})
                    if settled_cost > maximum:
                        trace["reservation_exceeded"] = True
                        raise _ResponseValidationError("provider_usage_exceeds_reserved_maximum")
                else:
                    trace["usage_source"] = "maximum_reservation_no_provider_usage"
                if choice.get("finish_reason") == "length":
                    message = choice.get("message")
                    partial = message.get("content") if isinstance(message, dict) else None
                    if isinstance(partial, str):
                        # Preserve returned model text for local truncation audits,
                        # never transport exception bodies or credentials.
                        trace["response_content"] = partial.replace(self.config.api_key, "[REDACTED]") if self.config.api_key else partial
                        trace["response_content_incomplete"] = True
                    raise _ResponseValidationError("model_output_truncated")
                message = choice.get("message")
                content = message.get("content") if isinstance(message, dict) else None
                if not isinstance(content, str):
                    raise _ResponseValidationError("invalid_model_content")
                trace["status"] = "ok"
            except Exception as exc:
                trace.update({"status": "error", "error": type(exc).__name__, "error_code": _error_code(exc),
                              "duration_seconds": round(time.monotonic() - started, 6), "cost_cny": str(settled_cost)})
                self.ledger.settle(call_id, settled_cost, trace)
                self.last_trace = trace
                # Do not log provider error bodies, URLs with keys, or headers.
                if attempt < self.settings.max_retries and isinstance(exc, (TimeoutError, OSError)):
                    previous_attempts.append(trace)
                    continue
                raise ModelCallError(trace["error_code"], trace) from None
            trace.update({"duration_seconds": round(time.monotonic() - started, 6), "cost_cny": str(settled_cost)})
            self.ledger.settle(call_id, settled_cost, trace)
            self.last_trace = trace
            return {"content": content, "trace": trace}


def configuration_status():
    """Return presence only; never expose authentication values."""
    settings = load_model_settings()
    return {key: bool(settings.get(key)) for key in ALLOWED_KEYS}
