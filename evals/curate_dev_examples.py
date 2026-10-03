"""Reproduce curated development prompt reasons without changing dataset answers.

This offline authoring step reads only prepared dev inputs, dev examples and an
explicit versioned reason patch. It never loads a gold or held-out split file.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def example_signature(example: dict) -> str:
    """Bind a patch to all example fields except the authored explanations."""
    stripped = deepcopy(example)
    for error in stripped["errors"]:
        error.pop("reason", None)
    canonical = json.dumps(stripped, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _sha(canonical.encode("utf-8"))


def curate_examples(examples: list[dict], patch: dict, dev_inputs: list[dict]) -> list[dict]:
    if patch.get("schema_version") != "1.0.0" or patch.get("source_split") != "dev":
        raise ValueError("Expected a version 1.0.0 development-only reason patch")
    if not examples or not isinstance(patch.get("documents"), list):
        raise ValueError("Examples and patch documents must be nonempty arrays")
    dev_by_id = {}
    for document in dev_inputs:
        if document.get("split") != "dev":
            raise ValueError("The input corpus must contain development documents only")
        document_id = document["doc_id"]
        if document_id in dev_by_id:
            raise ValueError("Duplicate development document ID")
        dev_by_id[document_id] = document
    example_ids = [example.get("source_id") for example in examples]
    patch_ids = [document.get("source_id") for document in patch["documents"]]
    if len(set(example_ids)) != len(example_ids) or example_ids != patch_ids:
        raise ValueError("Patch document IDs/order do not match the development examples")
    result = deepcopy(examples)
    for example, revision in zip(result, patch["documents"]):
        source_id = example["source_id"]
        if example.get("source_split") != "dev" or example.get("document_id") != source_id:
            raise ValueError("Example provenance must identify the same development document")
        document = dev_by_id.get(source_id)
        if document is None or example.get("content") != document.get("content"):
            raise ValueError("Example is absent from dev inputs or its content differs")
        if not example.get("complete_annotation"):
            raise ValueError("Only examples retaining the complete original annotations may be revised")
        if example_signature(example) != revision.get("example_sha256_without_reasons"):
            raise ValueError("Example content, annotations or metadata changed; review a new patch")
        reasons = revision.get("reasons")
        if (not isinstance(reasons, list) or len(reasons) != len(example["errors"])
                or any(not isinstance(reason, str) or not reason.strip() or len(reason) > 80
                       for reason in reasons)):
            raise ValueError("Patch must supply one nonempty reason of at most 80 characters per error")
        for error, reason in zip(example["errors"], reasons):
            error["reason"] = reason
    return result


def write_curated_examples(examples_path: Path, patch_path: Path, dev_inputs_path: Path,
                           out_path: Path, audit_path: Path) -> dict:
    input_paths = {path.resolve() for path in (examples_path, patch_path, dev_inputs_path)}
    if out_path.resolve() in input_paths or audit_path.resolve() in input_paths or out_path.resolve() == audit_path.resolve():
        raise ValueError("Outputs must not overwrite input or audit files")
    original = examples_path.read_bytes()
    patch_bytes = patch_path.read_bytes()
    dev_bytes = dev_inputs_path.read_bytes()
    patch = json.loads(patch_bytes.decode("utf-8-sig"))
    examples = curate_examples(json.loads(original.decode("utf-8-sig")), patch,
                               [json.loads(line) for line in dev_bytes.decode("utf-8-sig").splitlines() if line.strip()])
    payload = (json.dumps(examples, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    audit = {"original_examples_sha256": _sha(original), "updated_examples_sha256": _sha(payload),
             "reason_patch_sha256": _sha(patch_bytes), "dev_inputs_sha256": _sha(dev_bytes),
             "revision": patch["revision"], "documents": len(examples),
             "reasons": sum(len(example["errors"]) for example in examples),
             "change": "Only reasons replaced; document IDs, content, error types, spans and other metadata unchanged.",
             "reviewer_kind": patch.get("reviewer_kind", "Development prompt authoring; not human gold approval"),
             "read_policy": "Only the supplied dev examples, reason patch and dev inputs were read; no gold or held-out inputs."}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(payload)
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return audit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--examples", default="data/v2/dataset/examples.dev.json")
    parser.add_argument("--patch", default=str(Path(__file__).with_name("dev_example_reason_patch.v2.json")))
    parser.add_argument("--dev-inputs", default="data/v2/dataset/inputs.dev.jsonl")
    parser.add_argument("--out", default="data/v2/dataset/examples.dev.v2.json")
    parser.add_argument("--audit", default="data/v2/dataset/examples.dev.v2.audit.json")
    args = parser.parse_args()
    audit = write_curated_examples(*(Path(getattr(args, key)) for key in ("examples", "patch", "dev_inputs", "out", "audit")))
    print(json.dumps(audit, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
