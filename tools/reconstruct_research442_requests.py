"""Reconstruct and verify frozen Research442 messages, entirely offline.

Public entry points run in an isolated, read-only worker so importing frozen
``yjcheck`` never replaces the application's current modules. Reconstructed
messages are not original HTTP request logs. No credentials or gold are read.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.abc
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Iterable


LIMITATIONS = [
    "Reconstructed messages verified against recorded fingerprints; not an original HTTP request capture.",
    "Headers, credentials, exact HTTP bytes and unrecorded provider-side transformations are unavailable.",
    "The package manifest provides local consistency, not an independently signed provenance guarantee.",
    "Direct and hybrid arms shared responses; matching requests do not represent independent model runs.",
]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def request_messages_hash(messages: list[dict]) -> str:
    """Historical BudgetedChatClient/run_v2 algorithm (key order preserved)."""
    return _sha(json.dumps(messages, ensure_ascii=False).encode("utf-8"))


def request_cache_key(messages: list[dict], purpose: str) -> str:
    """Historical shared-response key, including purpose and sorted object keys."""
    return _sha(json.dumps({"messages": messages, "purpose": purpose},
                           ensure_ascii=False, sort_keys=True).encode("utf-8"))


def reconstruct_requests(package: str | Path,
                         document_ids: Iterable[str] | None = None) -> list[dict]:
    """Return verified reconstruction records in frozen queue order.

    Integrity failures and unknown IDs raise ValueError. An absent response
    yields a cache_missing request rather than claiming an observed model call.
    A string document_ids argument is rejected to avoid treating it as letters.
    """
    if isinstance(document_ids, str):
        raise TypeError("document_ids must be an iterable of document IDs, not a string")
    selected = list(document_ids) if document_ids is not None else None
    if selected is not None and (any(not isinstance(x, str) or not x for x in selected)
                                 or len(selected) != len(set(selected))):
        raise ValueError("document_ids must contain unique, nonempty strings")
    payload = {"package": str(Path(package).resolve()), "document_ids": selected}
    worker = subprocess.run(
        [sys.executable, "-I", "-B", str(Path(__file__).resolve()), "--worker"],
        input=json.dumps(payload, ensure_ascii=False), capture_output=True,
        encoding="utf-8", check=False,
    )
    if worker.returncode:
        raise ValueError(worker.stderr.strip() or "Offline reconstruction worker failed")
    return json.loads(worker.stdout)


def reconstruct_document_requests(package: str | Path, document_id: str) -> dict:
    """Convenience API for a diagnostic page showing one historical document."""
    return reconstruct_requests(package, [document_id])[0]


def summarize(records: list[dict]) -> dict:
    requests = [request for record in records for request in record["requests"]]
    return {
        "documents": len(records), "requests": len(requests),
        "document_statuses": dict(Counter(x["status"] for x in records)),
        "request_statuses": dict(Counter(x["status"] for x in requests)),
        "cache_matches": sum(x["cache_match"] is True for x in requests),
        "trace_matches": sum(x["trace_match"] is True for x in requests),
        "unique_recorded_calls": len({x["call_id"] for x in requests if x.get("call_id")}),
        "new_model_calls": 0,
        "evidence_kind": "reconstructed_messages_not_original_http",
    }


def _deny_side_effects(event: str, args: tuple) -> None:
    if event.startswith("socket.") or event in {"subprocess.Popen", "os.system", "os.posix_spawn"}:
        raise RuntimeError("Offline reconstruction forbids network and subprocess access")
    if event in {"os.mkdir", "os.remove", "os.rename", "os.rmdir", "os.link", "os.symlink", "os.truncate"}:
        raise RuntimeError("Offline reconstruction worker is read-only")
    if event == "open":
        path, mode, flags = args
        if isinstance(path, (str, bytes, os.PathLike)):
            name = Path(os.fsdecode(path)).name.lower()
            if name.startswith("gold.") or name == "local_model_config.json":
                raise RuntimeError("Reconstruction does not read gold or local model credentials")
        write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
        if (isinstance(mode, str) and any(x in mode for x in "wax+")) or flags & write_flags:
            raise RuntimeError("Offline reconstruction worker is read-only")


class _FrozenPackage:
    def __init__(self, package: Path):
        self.root = package.resolve()
        manifest = json.loads((self.root / "manifest.json").read_text(encoding="utf-8"))
        self.manifest = {row["path"]: row for row in manifest}
        if len(self.manifest) != len(manifest):
            raise ValueError("Duplicate package manifest entries")
        self.checked: dict[str, str] = {}

    def read(self, relative: str) -> bytes:
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError(f"Package path escapes root: {relative}")
        expected = self.manifest.get(relative)
        if expected is None:
            raise ValueError(f"File is not covered by package manifest: {relative}")
        data = path.read_bytes()
        digest = _sha(data)
        if digest != expected["sha256"]:
            raise ValueError(f"Package hash mismatch: {relative}")
        self.checked[relative] = digest
        return data

    def json(self, relative: str):
        return json.loads(self.read(relative))


class _VerifiedSourceLoader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Compile verified source bytes; never load an unverified cached .pyc."""
    def __init__(self, frozen: _FrozenPackage):
        self.frozen = frozen

    def find_spec(self, fullname, path=None, target=None):
        if fullname != "yjcheck" and not fullname.startswith("yjcheck."):
            return None
        relative = "source/factcheck/src/" + fullname.replace(".", "/")
        is_package = fullname == "yjcheck"
        relative += "/__init__.py" if is_package else ".py"
        spec = importlib.util.spec_from_loader(fullname, self, is_package=is_package)
        spec.origin = str(self.frozen.root / relative)
        spec.loader_state = relative
        return spec

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        relative = module.__spec__.loader_state
        module.__file__ = str(self.frozen.root / relative)
        exec(compile(self.frozen.read(relative), module.__file__, "exec"), module.__dict__)


def _reconstruct_in_worker(package: Path, selected: list[str] | None) -> list[dict]:
    frozen = _FrozenPackage(package)
    config = frozen.json("run/run_config.json")
    queue = frozen.json("run/queue.json")["document_ids"]
    if len(queue) != len(set(queue)):
        raise ValueError("Duplicate document IDs in frozen queue")
    input_path = "inputs/inputs.research442.jsonl"
    raw_inputs = frozen.read(input_path)
    raw_examples = frozen.read("inputs/examples.dev.v2.json")
    for data, key in ((raw_inputs, "inputs_sha256"), (raw_examples, "examples_sha256")):
        if _sha(data) != config[key]:
            raise ValueError(f"Frozen configuration mismatch: {key}")
    rows = {}
    line_numbers = {}
    for number, line in enumerate(raw_inputs.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        identity = row.get("doc_id") or row.get("document_id")
        if not isinstance(identity, str) or identity in rows:
            raise ValueError("Missing or duplicate input document identity")
        rows[identity] = row
        line_numbers[identity] = number
    if not set(queue) <= rows.keys():
        raise ValueError("Frozen queue references missing inputs")
    if selected is not None and not set(selected) <= set(queue):
        raise ValueError("Requested document ID is outside the frozen queue")
    source = frozen.root / "source"
    code_files = sorted((source / "factcheck/src/yjcheck").glob("*.py"))
    code_files += [source / "evals/run_v2.py", source / "evals/baseline.py"]
    source_hash = _sha("".join(_sha(frozen.read(p.relative_to(frozen.root).as_posix()))
                                for p in code_files).encode())
    if source_hash != config["source_hash"]:
        raise ValueError("Frozen source fingerprint differs from run configuration")
    # Isolated worker and -B ensure frozen modules neither replace live modules
    # in the caller nor create __pycache__ within the evidence package.
    sys.meta_path.insert(0, _VerifiedSourceLoader(frozen))
    from yjcheck.text_review import detect_text
    examples = json.loads(raw_examples)
    if not isinstance(examples, list) or any(x.get("source_split") != "dev" for x in examples):
        raise ValueError("Frozen examples are not an explicit dev-only list")
    if type(config.get("max_input_tokens")) is not int or config["max_input_tokens"] <= 0:
        raise ValueError("Invalid frozen input token budget")
    source_locations = {
        "input": {"path": input_path},
        "examples": "inputs/examples.dev.v2.json",
        "config": "run/run_config.json",
        "message_builder": "source/factcheck/src/yjcheck/text_review.py:_messages",
        "job_planner": "source/factcheck/src/yjcheck/text_review.py:detect_text",
        "example_filter": "source/evals/run_v2.py:run",
        "hash_algorithms": "source/evals/run_v2.py:request_cache_key,request_messages_hash",
    }
    records = []
    for identity in queue:
        if selected is not None and identity not in selected:
            continue
        row = rows[identity]
        own_examples = [e for e in examples if e.get("document_id") != identity
                        and e.get("source_id") != identity and e.get("content") != row["content"]]
        calls = []
        integrity_errors = []

        def capture(messages, *, purpose="review"):
            key = request_cache_key(messages, purpose)
            digest = request_messages_hash(messages)
            relative = f"run/model_responses/{key}.json"
            contexts = json.loads(messages[-1]["content"])["contexts"]
            call = {"job_index": len(calls), "purpose": purpose, "messages": messages,
                    "messages_sha256": digest, "cache_key": key,
                    "status": "cache_missing", "cache_match": None, "trace_match": None,
                    "call_id": None, "response_source": relative, "response_sha256": None,
                    "source_locations": {"input": input_path, "line": line_numbers[identity],
                                         "ranges": [[part["start"], part["end"]] for part in contexts]},
                    "evidence_kind": "reconstructed_messages_not_original_http"}
            calls.append(call)
            if not (frozen.root / relative).is_file():
                # Generate planned messages, while explicitly withholding any
                # claim that this request was historically sent to a provider.
                return {"content": '{"errors":[]}', "trace": {}}
            try:
                response = frozen.json(relative)
            except (ValueError, OSError) as exc:
                # detect_text catches chat failures; integrity failures must
                # still escape the outer reconstruction API rather than look
                # like an ordinary historical model failure.
                integrity_errors.append(str(exc))
                raise
            call["response_sha256"] = frozen.checked[relative]
            trace = response.get("trace") or {}
            expected = {"key": key, "purpose": purpose, "messages_sha256": digest}
            call["cache_match"] = response.get("cache_request") == expected
            call["trace_match"] = trace.get("request_sha256") == digest and trace.get("purpose") == purpose
            call["call_id"] = trace.get("call_id")
            call["recorded_trace_status"] = trace.get("status")
            call["recorded_trace"] = trace
            call["status"] = ("reconstructed_verified" if call["cache_match"] and call["trace_match"]
                              else "fingerprint_mismatch")
            if response.get("failed") or not isinstance(response.get("content"), str):
                call["status"] = "recorded_failure" if call["status"] == "reconstructed_verified" else call["status"]
                raise RuntimeError("Recorded response failure; no provider call was attempted")
            return {"content": response["content"], "trace": trace}

        report = detect_text(row["content"], document_id=identity, scene=row.get("scene", ""),
                             detector="model_direct", chat=capture, examples=own_examples,
                             max_input_tokens=config["max_input_tokens"])
        if integrity_errors:
            raise ValueError(integrity_errors[0])
        statuses = {x["status"] for x in calls}
        status = "reconstructed_verified" if calls and statuses == {"reconstructed_verified"} else "reconstructed_unverified"
        limitations = list(LIMITATIONS)
        if not calls:
            status = "no_model_request_reconstructed"
        if "cache_missing" in statuses:
            limitations.append("Missing responses: generated messages are planned requests; historical execution order is unverified.")
        locations = {**source_locations, "input": {"path": input_path, "line": line_numbers[identity]}}
        records.append({
            "schema_version": "request-reconstruction/1.0", "document_id": identity,
            "status": status, "requests": calls, "limitations": limitations,
            "source_locations": locations,
            "provenance": {"package": str(frozen.root), "source_hash": source_hash,
                           "inputs_sha256": config["inputs_sha256"], "examples_sha256": config["examples_sha256"],
                           "run_config_sha256": frozen.checked["run/run_config.json"],
                           "source_content_sha256": _sha(row["content"].encode("utf-8")),
                           "selected_example_ids": [e.get("source_id") for e in own_examples],
                           "max_input_tokens": config["max_input_tokens"],
                           "new_model_calls": 0, "replayed_detector": "model_direct"},
            "reconstruction_coverage": report.get("coverage", {}),
        })
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path)
    parser.add_argument("--document-id", action="append", dest="document_ids")
    parser.add_argument("--out", type=Path, help="New JSONL sidecar outside the frozen package")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
        payload = json.load(sys.stdin)
        sys.addaudithook(_deny_side_effects)
        try:
            records = _reconstruct_in_worker(Path(payload["package"]), payload["document_ids"])
            json.dump(records, sys.stdout, ensure_ascii=False)
        except Exception as exc:
            print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
            raise SystemExit(2)
        return
    if args.package is None:
        parser.error("--package is required")
    if args.out and args.out.resolve().is_relative_to(args.package.resolve()):
        parser.error("--out must be outside the frozen package")
    records = reconstruct_requests(args.package, args.document_ids)
    if args.out:
        with args.out.open("x", encoding="utf-8", newline="\n") as stream:
            for record in records:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(json.dumps(summarize(records), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
