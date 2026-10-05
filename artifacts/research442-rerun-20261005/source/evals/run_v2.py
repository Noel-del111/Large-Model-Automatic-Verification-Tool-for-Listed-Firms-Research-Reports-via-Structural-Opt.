"""Separated inference and scoring for the FinED V2 experiment.

The run command never opens gold files. score is a separate, offline process.
Use a fresh output directory after code/input/model changes; cached predictions
are bound to a run fingerprint and never silently reused for a different run.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "factcheck/src"), str(ROOT / "pdfparse/src"), str(ROOT / "evals")]
from yjcheck.model import ModelConfig
from yjcheck.review_hints import all_review_hints
from yjcheck.model_runtime import BudgetedChatClient, ModelCallError, configuration_status

DETECTORS = ("legacy_rules", "model_direct", "hybrid")
FORBIDDEN = {"errors", "gold", "answer", "answers", "output", "error_span", "start_idx", "error_text", "origin_text",
             "error_type", "error_types", "gold_spans", "spans", "label", "labels"}


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def digest_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def assert_input_only(row):
    if not isinstance(row, dict):
        raise ValueError("input document must be an object")
    def check(value):
        if isinstance(value, dict):
            if FORBIDDEN.intersection(value):
                raise ValueError("输入文件含答案字段；请使用prepare_dataset生成的inputs文件")
            for item in value.values():
                check(item)
        elif isinstance(value, list):
            for item in value:
                check(item)
    check(row)
    if not isinstance(row.get("content"), str) or not (row.get("doc_id") or row.get("document_id")):
        raise ValueError("input document requires stable doc_id and text content")


def doc_id(row):
    return row.get("document_id") or row["doc_id"]


def fixed_queue(rows):
    """Research first, round-robin scenes and lengths; no gold-dependent selection."""
    groups = defaultdict(list)
    for row in rows:
        length = len(row["content"])
        band = 0 if length < 2000 else 1 if length < 8000 else 2 if length < 32000 else 3
        groups[(row.get("scene", ""), band)].append(row)
    for group in groups.values():
        group.sort(key=lambda r: doc_id(r))
    keys = sorted(groups, key=lambda k: (k[0] not in {"个股研报", "行业研报"}, k[1], k[0]))
    queue = []
    while any(groups.values()):
        for key in keys:
            if groups[key]:
                queue.append(groups[key].pop(0))
    return queue


def validate_predictions(report, content):
    """Invalid anchors remain false-positive predictions, never earn span credit."""
    errors = report.get("errors", [])
    if not isinstance(errors, list):
        report["errors"] = [{"error_type": "invalid_prediction", "spans": [], "invalid_anchor": True}]
        return report
    for index, error in enumerate(errors):
        if not isinstance(error, dict):
            errors[index] = {"error_type": "invalid_prediction", "spans": [], "invalid_anchor": True}
            continue
        spans = error.get("spans") or []
        valid = isinstance(spans, list) and bool(spans)
        if not valid:
            spans = []
        for span in spans:
            if not isinstance(span, dict):
                valid = False
                break
            start, end = span.get("start"), span.get("end")
            valid = valid and type(start) is int and type(end) is int and 0 <= start < end <= len(content)
            if valid:
                valid = content[start:end] == span.get("text")
        if not valid:
            error["invalid_anchor"] = True
            error["spans"] = []
    return report


def runtime_calls(report):
    calls = {}
    def walk(value, inherited_reuse=False, inherited_prior_run=False):
        if isinstance(value, dict):
            reused = inherited_reuse or bool(value.get("reused_response"))
            prior_run = inherited_prior_run or bool(value.get("reused_from_prior_run"))
            if value.get("call_id"):
                identity = value["call_id"]
                record = {**value, "reused_response": True} if reused else value
                if prior_run:
                    record = {**record, "reused_from_prior_run": True}
                if identity in calls and not calls[identity].get("reused_response"):
                    record = {**record, "reused_response": False}
                calls[identity] = record
            for child in value.values():
                walk(child, reused, prior_run)
        elif isinstance(value, list):
            for child in value:
                walk(child, inherited_reuse, inherited_prior_run)
    walk(report.get("traces", []))
    return calls


def latency_summary(reports):
    """Separate observed arm execution from a sequential, uncached estimate.

    A replayed model response omits its original network/inference time from the
    arm's wall clock. Add that time exactly once, including original retries.
    Fresh calls are already included in wall_seconds and must not be added again.
    This is an estimate, not an independently measured hybrid deployment latency.
    """
    measured, estimated, missing = [], [], 0
    for report in reports:
        wall = report.get("wall_seconds")
        if isinstance(wall, bool) or not isinstance(wall, (int, float)) or not math.isfinite(wall) or wall < 0:
            missing += 1
            continue
        measured.append(wall)
        replay_seconds = []
        valid = True
        for trace in runtime_calls(report).values():
            if not trace.get("reused_response"):
                continue
            duration = trace.get("duration_seconds")
            if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration < 0:
                valid = False
                break
            replay_seconds.append(duration)
        if valid:
            estimated.append(wall + sum(replay_seconds))
        else:
            missing += 1

    def distribution(values):
        values = sorted(values)
        return {"documents": len(values), "p50": statistics.median(values) if values else None,
                "p95": values[min(len(values)-1, max(0, math.ceil(len(values)*.95)-1))] if values else None}

    return {"measured_arm_wall_seconds": distribution(measured),
            "estimated_end_to_end_seconds": distribution(estimated),
            "end_to_end_unavailable_documents": missing,
            "basis": "Measured arm wall includes cache replay; end-to-end estimate adds each unique replayed model call duration (including retries) once. Fresh call durations are already in wall time. Sequential execution assumed; no independent hybrid end-to-end measurement."}


def path_complete(report, mode):
    coverage = report.get("coverage", {})
    return bool(coverage.get("execution_complete", coverage.get("complete")) or (mode == "offline" and coverage.get("rules_complete")))


def response_parse_failed_jobs(report):
    """Successful transport can still return unusable JSON or response shape."""
    return [trace for trace in report.get("traces", [])
            if trace.get("status") == "failed" and (trace.get("error_stage") == "response_parse"
                or (trace.get("runtime_trace") or {}).get("status") == "ok")]


def request_cache_key(messages, purpose):
    return hashlib.sha256(json.dumps({"messages": messages, "purpose": purpose}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def request_messages_hash(messages):
    # Match BudgetedChatClient's recorded request_sha256 exactly.
    return hashlib.sha256(json.dumps(messages, ensure_ascii=False).encode()).hexdigest()


def prepare_response_reuse(directories, spec, destination):
    """Validate only run metadata and successful raw responses, never predictions.

    Rules, scoring and input files may change between runs. Every transport setting
    must remain identical, and replay still requires exact messages and purpose.
    """
    if directories and spec["mode"] != "model":
        raise ValueError("模型响应复用仅支持 --mode model")
    required = ("model", "model_request", "endpoint_hash", "runtime", "max_input_tokens")
    manifests, records, seen = [], {}, set()
    for directory in directories:
        source = Path(directory).resolve()
        if source == Path(destination).resolve():
            raise ValueError("响应复用来源必须是另一个运行目录")
        if source in seen:
            continue
        seen.add(source)
        meta = source / "run_config.json"
        cache = source / "model_responses"
        if meta.resolve().parent != source or cache.resolve().parent != source:
            raise ValueError("响应缓存来源不能通过链接跨出指定运行目录")
        raw_config = meta.read_bytes()
        config = json.loads(raw_config.decode("utf-8"))
        def comparable_value(value, key):
            # Absent optional expiry in historical metadata means no expiry.
            # A nonempty expiry still has to match exactly, like every rate.
            if key == "runtime" and isinstance(value, dict):
                # The shared-ledger spending cap is not sent to the model.
                # An audited authorization may raise it between runs without
                # invalidating a paid response to the exact same request.
                # Ledger identity, rates and every inference setting still match.
                return {k: v for k, v in {"price_valid_until": "", **value}.items() if k != "budget_cny"}
            return value
        if config.get("mode") != "model" or any(key not in config or comparable_value(config[key], key) != comparable_value(spec[key], key) for key in required):
            raise ValueError("响应复用的模型、端点、推理设置或运行预算设置不一致，已在调用前拒绝")
        if not re.fullmatch(r"[0-9a-f]{64}", str(config.get("source_hash", ""))) or not cache.is_dir():
            raise ValueError("响应来源缺少代码指纹或 model_responses 目录")
        provenance = {"source_run": str(source), "source_run_config_sha256": hashlib.sha256(raw_config).hexdigest(),
                      "source_hash": config["source_hash"],
                      "source_budget_cny": config["runtime"].get("budget_cny"),
                      "target_budget_cny": spec["runtime"].get("budget_cny")}
        file_hashes, successful, skipped = {}, 0, 0
        for path in sorted(cache.glob("*.json")):
            if not re.fullmatch(r"[0-9a-f]{64}\.json", path.name) or path.resolve().parent != cache.resolve():
                raise ValueError("响应缓存文件名或路径无效")
            raw = path.read_bytes()
            record = json.loads(raw.decode("utf-8"))
            if not isinstance(record, dict) or not isinstance(record.get("trace"), dict):
                raise ValueError("响应缓存缺少运行 trace")
            trace = record["trace"]
            if record.get("failed") or trace.get("status") in {"error", "reserved"}:
                skipped += 1
                continue
            if (trace.get("status") != "ok" or not isinstance(record.get("content"), str)
                    or not isinstance(trace.get("call_id"), str) or not trace["call_id"]
                    or not re.fullmatch(r"[0-9a-f]{64}", str(trace.get("request_sha256", "")))
                    or not isinstance(trace.get("purpose"), str) or not trace["purpose"]
                    or trace.get("model") != config["model"]
                    or trace.get("thinking", "") != config["model_request"].get("thinking", "")
                    or trace.get("reasoning_effort", "") != config["model_request"].get("reasoning_effort", "")
                    or str(trace.get("max_output_tokens")) != config["runtime"].get("max_output_tokens")):
                raise ValueError("成功响应记录与来源配置或请求哈希不一致")
            request = record.get("cache_request")
            if request is not None and request != {"key": path.stem, "purpose": trace["purpose"], "messages_sha256": trace["request_sha256"]}:
                raise ValueError("响应缓存请求元数据不一致")
            imported = {"content": record["content"], "trace": trace,
                        "cache_request": {"key": path.stem, "purpose": trace["purpose"], "messages_sha256": trace["request_sha256"]},
                        "cache_provenance": {**provenance, "source_response_sha256": hashlib.sha256(raw).hexdigest(),
                                             "prior_provenance": record.get("cache_provenance")}}
            if path.stem in records:
                earlier = records[path.stem]
                if earlier["content"] != imported["content"] or earlier["trace"] != imported["trace"]:
                    raise ValueError("多个来源包含同一请求的不同模型返回，请明确选择一个来源")
            else:
                records[path.stem] = imported
            file_hashes[path.name] = hashlib.sha256(raw).hexdigest()
            successful += 1
        manifests.append({**provenance, "successful_records": successful, "failed_records_skipped": skipped,
                          "successful_responses_sha256": hashlib.sha256(json.dumps(file_hashes, sort_keys=True).encode()).hexdigest()})
    return manifests, records


def import_model_responses(directory, records):
    directory = Path(directory)
    for key, record in records.items():
        target = directory / (key + ".json")
        if target.exists():
            existing = json.loads(target.read_text(encoding="utf-8"))
            if existing != record:
                raise ValueError("目标响应缓存已存在不同记录，禁止覆盖")
        else:
            write_json(target, record)


class SharedCandidateClient:
    """Replay identical requests for a controlled postprocessing comparison.

    Cache belongs to a fingerprinted run; explicitly imported responses retain
    their source provenance. No credentials or headers are saved.
    Failed paid calls are replayed too, so the second arm cannot silently retry.
    """
    def __init__(self, client, directory):
        self.client, self.directory = client, Path(directory)

    def __call__(self, messages, *, purpose="review"):
        key = request_cache_key(messages, purpose)
        request = {"key": key, "purpose": purpose, "messages_sha256": request_messages_hash(messages)}
        path = self.directory / (key + ".json")
        if path.exists():
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.get("cache_request", request) != request or record["trace"].get("request_sha256", request["messages_sha256"]) != request["messages_sha256"]:
                raise ValueError("响应缓存请求与当前消息不匹配，已停止复用")
            trace = {**record["trace"], "reused_response": True}
            if record.get("cache_provenance"):
                trace.update(reused_from_prior_run=True, cache_provenance=record["cache_provenance"])
            if record.get("failed"):
                raise ModelCallError(record["error_code"], trace)
            return {"content": record["content"], "trace": trace}
        try:
            reply = self.client(messages, purpose=purpose)
        except ModelCallError as exc:
            write_json(path, {"failed": True, "error_code": exc.trace.get("error_code", type(exc).__name__), "trace": exc.trace, "cache_request": request})
            raise
        write_json(path, {**reply, "cache_request": request})
        return reply


def run(args):
    # A persisted user stop request must be checked before creating a model
    # client or opening its budget ledger. An already-running batch can finish.
    control_path = ROOT / "data/v2/run_control.json"
    if control_path.is_file():
        control = json.loads(control_path.read_text(encoding="utf-8-sig"))
        blocked = {str((ROOT / item).resolve()) for item in control.get("blocked_output_directories", [])}
        if str(Path(args.out).resolve()) in blocked:
            raise ValueError("用户要求当前200篇后停止；此后续批次已阻止，等待进一步指令。")
    from yjcheck.text_review import detect_text
    rows = read_jsonl(args.inputs)
    for row in rows:
        assert_input_only(row)
    ids = [doc_id(row) for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate input document IDs")
    examples = json.loads(Path(args.examples).read_text(encoding="utf-8")) if args.examples else []
    if not isinstance(examples, list) or any(e.get("source_split") != "dev" for e in examples):
        raise ValueError("few-shot examples must be an explicit dev-only list")
    selected = fixed_queue(rows)[:args.max_documents] if args.max_documents else fixed_queue(rows)
    client = BudgetedChatClient(ModelConfig.from_env()) if args.mode == "model" else None
    detectors = DETECTORS if client else ("legacy_rules", "hybrid")
    code_files = sorted((ROOT / "factcheck/src/yjcheck").glob("*.py")) + [Path(__file__)]
    baseline_archive = getattr(args, "baseline_archive", None)
    if baseline_archive and not Path(baseline_archive).is_file():
        raise ValueError("冻结基线归档不存在，请先按文档保存基线")
    code_files.append(ROOT / "evals/baseline.py")
    spec = {"inputs_sha256": digest_file(args.inputs), "mode": args.mode, "detectors": list(detectors),
            "comparison_protocol": "same_model_response_postprocessing_ablation_v2",
            "source_hash": hashlib.sha256("".join(digest_file(p) for p in code_files).encode()).hexdigest(),
            "model": client.config.model if client else None,
            "model_request": {"thinking": getattr(client.config, "thinking", ""),
                              "reasoning_effort": getattr(client.config, "reasoning_effort", ""),
                              "timeout": getattr(client.config, "timeout", 30)} if client else None,
            "endpoint_hash": hashlib.sha256(client.config.base_url.encode()).hexdigest() if client else None,
            "runtime": {k: str(v) for k, v in vars(client.settings).items()} if client else None,
            "examples_sha256": digest_file(args.examples) if args.examples else None,
            "baseline_archive_sha256": digest_file(baseline_archive) if baseline_archive else None,
            "max_input_tokens": client.settings.context_tokens - client.settings.max_output_tokens if client else 16000}
    out = Path(args.out)
    reuse_manifests, reusable_records = prepare_response_reuse(getattr(args, "reuse_model_responses_from", []) or [], spec, out)
    if reuse_manifests:
        spec["reused_model_response_sources"] = reuse_manifests
        spec["response_reuse_policy"] = "Successful raw responses only; same transport settings and exact messages/purpose; no predictions/gold copied; failed source calls skipped."
    meta = out / "run_config.json"
    if meta.exists() and json.loads(meta.read_text(encoding="utf-8")) != spec:
        raise ValueError("运行配置或代码已变化，请使用新输出目录，禁止混用旧预测")
    write_json(meta, spec)
    import_model_responses(out / "model_responses", reusable_records)
    shared_client = SharedCandidateClient(client, out / "model_responses") if client else None
    selected_ids = [doc_id(r) for r in selected]
    write_json(out / "queue.json", {"document_ids": selected_ids, "requested_documents": len(selected), "total_inputs": len(rows)})
    status = {"mode": args.mode, "requested": len(selected), "completed_documents": 0,
              "real_model_evaluated": False, "stop_reason": None, "detectors": list(detectors)}
    actual_calls = {}
    for row in selected:
        identity = doc_id(row)
        safe_id = hashlib.sha256(identity.encode()).hexdigest()[:24]
        document_complete = True
        for detector in detectors:
            dest = out / "predictions" / detector / (safe_id + ".json")
            cached = json.loads(dest.read_text(encoding="utf-8")) if dest.exists() else None
            if cached and path_complete(cached, args.mode):
                report = cached
            else:
                if cached:
                    with (out / "retry_history.jsonl").open("a", encoding="utf-8") as history:
                        history.write(json.dumps(cached, ensure_ascii=False) + "\n")
                own_examples = [e for e in examples if e.get("document_id") != identity and e.get("source_id") != identity and e.get("content") != row["content"]]
                started = time.monotonic()
                try:
                    if detector == "legacy_rules" and baseline_archive:
                        from baseline import frozen_legacy_detect
                        report = frozen_legacy_detect(row["content"], document_id=identity, scene=row.get("scene", ""), archive=baseline_archive)
                    else:
                        report = detect_text(row["content"], document_id=identity, scene=row.get("scene", ""),
                                             detector=detector, chat=shared_client if detector != "legacy_rules" else None,
                                             examples=own_examples, max_input_tokens=spec["max_input_tokens"])
                except Exception as exc:
                    report = {"document_id": identity, "detector": detector, "errors": [],
                              "coverage": {"complete": False, "reason": type(exc).__name__}, "traces": []}
                report["wall_seconds"] = round(time.monotonic() - started, 6)
                report["source_content_sha256"] = hashlib.sha256(row["content"].encode()).hexdigest()
                validate_predictions(report, row["content"])
                write_json(dest, report)
            if not path_complete(report, args.mode):
                document_complete = False
            actual_calls.update(runtime_calls(report))
            status["real_model_evaluated"] = any(c.get("status") == "ok" and not c.get("local_endpoint", False) for c in actual_calls.values())
            status["model_calls_this_run"] = len(actual_calls)
            status["prior_run_model_calls_reused"] = sum(bool(c.get("reused_from_prior_run")) for c in actual_calls.values())
            status["new_model_calls"] = len(actual_calls) - status["prior_run_model_calls_reused"]
            if client and "BudgetExceeded" in json.dumps(report, ensure_ascii=False):
                status["stop_reason"] = "budget_limit"
                document_complete = False
                break
        status["completed_documents"] += int(document_complete)
        if client:
            status["budget"] = client.ledger.summary()
        write_json(out / "status.json", status)
        if status["stop_reason"]:
            break
    if not client:
        status["limitation"] = "仅离线规则测试；模型直接检测与模型组合流程均未实测。"
    write_json(out / "status.json", status)
    print(json.dumps(status, ensure_ascii=False))
    return 0


def score(args):
    from fined_bench_eval import score_paper_detection
    rows = read_jsonl(args.inputs)
    by_id = {doc_id(row): row for row in rows}
    golds = read_jsonl(args.gold)
    run_dir = Path(args.runs)
    spec = json.loads((run_dir / "run_config.json").read_text(encoding="utf-8"))
    if spec["inputs_sha256"] != digest_file(args.inputs):
        raise ValueError("scoring inputs differ from inference inputs")
    queue = json.loads((run_dir / "queue.json").read_text(encoding="utf-8"))["document_ids"]
    selected = set(queue)
    relevant_gold = [g for g in golds if doc_id(g) in selected]
    if {doc_id(g) for g in relevant_gold} != selected:
        raise ValueError("每个计划输入都必须有gold记录，含零错误文档")
    tracks = {by_id[i].get("source_track", "public_text_detection") for i in selected}
    if len(tracks) != 1:
        raise ValueError("评分输入必须属于单一测试线，公开文本与业务/正常样本分别报告")
    result = {"track": tracks.pop(), "run_config": spec, "requested_documents": len(selected),
              "notes": ["论文描述的项目实现口径，非作者官方评分程序。", "公开留出集不证明排除模型预训练污染。",
                        "语义证据支持和人工复核耗时须在配对业务测试中另测。",
                        "各组实测墙钟包含缓存回放差异，不能直接比较速度；端到端估算补回唯一回放调用耗时，不是独立测得的部署延迟。",
                        "同一原文同一提示共用一次模型返回，对比后处理与验证；每组独立展示的调用费用不可相加，累计成本按唯一call_id汇总。"], "detectors": {}}
    reports_by_detector = {}
    fully_scorable = {doc_id(g) for g in relevant_gold if all(e.get("scorable", True) for e in g.get("errors", []))}
    for detector in spec["detectors"]:
        reports = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((run_dir / "predictions" / detector).glob("*.json"))]
        reports = [validate_predictions(r, by_id[doc_id(r)]["content"]) for r in reports if doc_id(r) in selected]
        reports_by_detector[detector] = reports
        latency = latency_summary(reports)
        hint_reports = [all_review_hints(report) for report in reports]
        counts = Counter(e.get("status", "needs_review") for r in reports for e in r.get("errors", []))
        calls = {}
        for report in reports:
            calls.update(runtime_calls(report))
        measured_calls = list(calls.values())
        entry = {"candidate_detection": score_paper_detection(reports, relevant_gold),
                 "candidate_detection_scope": "emitted errors only; direct includes anchor-rejected errors, hybrid keeps rejected hints separately; use all_review_hints_detection for total hint burden",
                 "all_review_hints_detection": score_paper_detection(hint_reports, relevant_gold),
                 "all_review_hints_detection_scope": "emitted errors plus each unrepresented rejected hint as an unmatched prediction; repeated hints are not necessarily unique real errors",
                 "fully_scorable_documents_sensitivity": {
                     "documents": len(fully_scorable), "excluded_documents": len(selected) - len(fully_scorable),
                     "basis": "only documents with no excluded gold instances; descriptive sensitivity analysis, not replacement of full planned-queue scores",
                     "candidate_detection": score_paper_detection([r for r in reports if doc_id(r) in fully_scorable], [g for g in relevant_gold if doc_id(g) in fully_scorable]),
                     "all_review_hints_detection": score_paper_detection([r for r in hint_reports if doc_id(r) in fully_scorable], [g for g in relevant_gold if doc_id(g) in fully_scorable])},
                 "verified_detection": score_paper_detection([{**r, "errors": [e for e in r.get("errors", []) if e.get("status") == "confirmed_error"]} for r in reports], relevant_gold),
                 "attempted_documents": len(reports), "completed_documents": sum(path_complete(r, spec["mode"]) for r in reports),
                 "execution_failed_or_missing_documents": len(selected) - sum(path_complete(r, spec["mode"]) for r in reports),
                 "missing_prediction_documents": len(selected - {doc_id(r) for r in reports}),
                 "response_parse_failed_documents": sum(bool(response_parse_failed_jobs(r)) for r in reports),
                 "response_parse_failed_jobs": sum(len(response_parse_failed_jobs(r)) for r in reports),
                 "json_syntax_repaired_documents": sum(any(c.get("json_syntax_repair") for c in runtime_calls(r).values()) for r in reports),
                 "validation_complete_documents": sum(r.get("coverage", {}).get("complete", False) for r in reports),
                 "candidate_status_counts": dict(counts), "human_review_candidates": counts.get("needs_review", 0),
                 "human_review_hints_total": sum(e.get("status", "needs_review") == "needs_review" for r in hint_reports for e in r.get("errors", [])),
                 "human_review_hints_basis": "emitted needs_review plus rejected hints not already represented in emitted invalid-anchor errors; duplicate hints count toward workload",
                 "raw_model_candidates": sum(len(r.get("raw_candidates", [])) for r in reports),
                 "rejected_model_candidates": sum(len(r.get("rejected_candidates", [])) for r in reports),
                 "runtime": {"model_calls": len(measured_calls),
                             "accounted_cny": round(sum(float(c.get("cost_cny", c.get("maximum_cny", 0))) for c in measured_calls), 6),
                             "provider_input_tokens": sum(c["input_tokens"] for c in measured_calls if "input_tokens" in c),
                             "provider_output_tokens": sum(c["output_tokens"] for c in measured_calls if "output_tokens" in c),
                             "unknown_usage_calls": sum(c.get("usage_source") != "provider" for c in measured_calls),
                             "evidence_support_accuracy": None, "human_review_seconds": None},
                 "seconds_p50": latency["measured_arm_wall_seconds"]["p50"],
                 "seconds_p95": latency["measured_arm_wall_seconds"]["p95"],
                 "seconds_field_basis": "measured_arm_wall_including_cache_replay; not comparable end-to-end model latency",
                 "latency": latency,
                 "by_scene": {}, "by_length": {}, "by_type": {}}
        for group_name, classifier in (("by_scene", lambda row: row.get("scene", "")),
                                        ("by_length", lambda row: "<2k" if len(row["content"]) < 2000 else "2k-8k" if len(row["content"]) < 8000 else "8k-32k" if len(row["content"]) < 32000 else ">=32k")):
            for key in sorted({classifier(by_id[i]) for i in selected}):
                subset = {i for i in selected if classifier(by_id[i]) == key}
                entry[group_name][key] = score_paper_detection([r for r in reports if doc_id(r) in subset], [g for g in relevant_gold if doc_id(g) in subset])
        types = sorted({str(e.get("type") or e.get("error_type") or "unknown")
                        for g in relevant_gold for e in g.get("errors", [])}
                       | {str(e.get("error_type") or "unknown") for r in reports for e in r.get("errors", [])})
        for error_type in types:
            filtered_preds = [{**r, "errors": [e for e in r.get("errors", []) if e.get("error_type") == error_type]} for r in reports]
            filtered_gold = [{**g, "errors": [e for e in g.get("errors", []) if (e.get("type") or e.get("error_type")) == error_type]} for g in relevant_gold]
            entry["by_type"][error_type] = score_paper_detection(filtered_preds, filtered_gold)
        result["detectors"][detector] = entry
    paired = selected.copy()
    for reports in reports_by_detector.values():
        paired &= {doc_id(r) for r in reports if path_complete(r, spec["mode"])}
    result["paired_complete_documents"] = len(paired)
    result["paired_complete_scores"] = {key: score_paper_detection([r for r in reports if doc_id(r) in paired], [g for g in relevant_gold if doc_id(g) in paired]) for key, reports in reports_by_detector.items()}
    all_calls = {}
    for reports in reports_by_detector.values():
        for report in reports:
            all_calls.update(runtime_calls(report))
    result["unique_model_usage"] = {"calls": len(all_calls),
        "input_tokens": sum(c.get("input_tokens", 0) for c in all_calls.values()),
        "output_tokens": sum(c.get("output_tokens", 0) for c in all_calls.values()),
        "accounted_cny": round(sum(float(c.get("cost_cny", c.get("maximum_cny", 0))) for c in all_calls.values()), 6),
        "cost_basis": "configured_rate_upper_bound", "failed_calls": sum(c.get("status") != "ok" for c in all_calls.values())}
    result["unique_model_usage"].update({
        "transport_or_provider_failed_calls": sum(c.get("status") != "ok" for c in all_calls.values()),
        "response_parse_failed_calls": len({trace["runtime_trace"]["call_id"] for reports in reports_by_detector.values()
                                            for report in reports for trace in response_parse_failed_jobs(report)
                                            if (trace.get("runtime_trace") or {}).get("call_id")}),
        "failure_counts_basis": "failed_calls/transport_or_provider_failed_calls count transport/provider validation failures; response_parse_failed_calls separately counts successful transports with rejected response JSON/schema. Execution failure and missing predictions remain in document denominators.",
        "prior_run_calls_reused": sum(bool(c.get("reused_from_prior_run")) for c in all_calls.values()),
        "newly_incurred_cny": round(sum(float(c.get("cost_cny", c.get("maximum_cny", 0))) for c in all_calls.values() if not c.get("reused_from_prior_run")), 6),
        "previously_incurred_cny_reused": round(sum(float(c.get("cost_cny", c.get("maximum_cny", 0))) for c in all_calls.values() if c.get("reused_from_prior_run")), 6),
        "accounted_cny_basis": "Referenced unique calls including historical reused responses; only newly_incurred_cny is additional cost for this run."})
    result["execution_summary"] = {
        "planned_documents": len(selected), "all_groups_completed_documents": len(paired),
        "failed_or_missing_documents": len(selected - paired),
        "failed_or_missing_document_ids": sorted(selected - paired),
        "response_parse_failed_document_ids": sorted({doc_id(report) for reports in reports_by_detector.values()
                                                      for report in reports if response_parse_failed_jobs(report)})}
    write_json(args.out, result)
    print(json.dumps({"output": str(Path(args.out).resolve()), "documents": len(selected), "paired_complete": len(paired)}, ensure_ascii=False))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--inputs", required=True)
    run_parser.add_argument("--examples")
    run_parser.add_argument("--out", required=True)
    run_parser.add_argument("--mode", choices=("offline", "model"), default="offline")
    run_parser.add_argument("--baseline-archive", default=str(ROOT / "data/v2/baseline/source-d71f8c7.zip"))
    run_parser.add_argument("--max-documents", type=int, default=10, help="0 means all; default pilot=10")
    run_parser.add_argument("--reuse-model-responses-from", action="append", default=[], metavar="RUN_DIR",
                            help="Import successful exact-request model responses from a compatible run; repeat for multiple sources; never reuse predictions")
    scoring = sub.add_parser("score")
    scoring.add_argument("--inputs", required=True)
    scoring.add_argument("--gold", required=True)
    scoring.add_argument("--runs", required=True)
    scoring.add_argument("--out", required=True)
    sub.add_parser("status")
    args = parser.parse_args(argv)
    if args.command == "status":
        print(json.dumps(configuration_status(), indent=2))
        return 0
    if args.command == "run" and args.max_documents < 0:
        parser.error("max-documents must be nonnegative")
    return run(args) if args.command == "run" else score(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
