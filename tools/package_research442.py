"""Build an explicitly authorized public R01 evidence package, without model calls."""
import argparse
import hashlib
import json
from pathlib import Path
import re


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    src, out = args.source.resolve(), args.out.resolve()
    if out.exists():
        raise ValueError("Output must be a new directory")
    run = src / "data/v2/runs/research442"
    dataset = src / "data/v2/dataset"
    spec = read(run / "run_config.json")
    queue = read(run / "queue.json")["document_ids"]
    if len(queue) != 442 or len(set(queue)) != 442:
        raise ValueError("Expected 442 unique queued documents")
    for name, key in (("inputs.research442.jsonl", "doc_id"), ("gold.research442.jsonl", "document_id")):
        rows = [json.loads(line) for line in (dataset / name).read_text(encoding="utf-8").splitlines()]
        ids = [row[key] for row in rows]
        if len(ids) != 442 or set(ids) != set(queue):
            raise ValueError(f"Dataset/queue mismatch: {name}")
    checks = {"inputs": digest(dataset / "inputs.research442.jsonl") == spec["inputs_sha256"],
              "examples": digest(dataset / "examples.dev.v2.json") == spec["examples_sha256"]}
    code = sorted((src / "factcheck/src/yjcheck").glob("*.py")) + [src / "evals/run_v2.py", src / "evals/baseline.py"]
    checks["source"] = hashlib.sha256("".join(digest(p) for p in code).encode()).hexdigest() == spec["source_hash"]
    baseline = src / "data/v2/baseline/source-d71f8c7.zip"
    checks["baseline"] = digest(baseline) == spec["baseline_archive_sha256"]
    if not all(checks.values()):
        raise ValueError(f"Fingerprint mismatch: {checks}")
    counts = {}
    for arm in spec["detectors"]:
        paths = sorted((run / "predictions" / arm).glob("*.json"))
        ids = [read(p)["document_id"] for p in paths]
        if len(ids) != 442 or set(ids) != set(queue):
            raise ValueError(f"Prediction coverage mismatch: {arm}")
        counts[arm] = len(ids)
    responses = sorted((run / "model_responses").glob("*.json"))
    response_status = {}
    for path in responses:
        state = read(path).get("trace", {}).get("status", "missing")
        response_status[state] = response_status.get(state, 0) + 1
    if len(responses) != 442:
        raise ValueError("Expected 442 response records")
    # Explicit allowlist: never publish local_model_config, credentials, ledger or smoke run.
    selected = [(p, Path("run") / p.relative_to(run)) for p in run.rglob("*.json")]
    selected += [(dataset / n, Path("inputs") / n) for n in
                 ("inputs.research442.jsonl", "gold.research442.jsonl", "examples.dev.v2.json")]
    selected += [(baseline, Path("baseline") / baseline.name)]
    for directory in ("evals", "factcheck", "pdfparse"):
        selected += [(p, Path("source") / p.relative_to(src)) for p in (src / directory).rglob("*.py")]
    selected += [(p, Path("source") / p.relative_to(src)) for p in (src / "factcheck").rglob("*.json")
                 if "schemas" in p.parts]
    manifest = []
    for original, relative in selected:
        raw = original.read_bytes()
        published = raw
        if original.suffix in {".json", ".jsonl", ".py"}:
            text = raw.decode("utf-8")
            if re.search(r"\bsk-[A-Za-z0-9_-]{16,}|Bearer\s+[A-Za-z0-9_.-]{16,}", text):
                raise ValueError(f"Possible credential; publication blocked: {relative}")
            if relative.as_posix() in {"run/run_config.json", "run/score.json"}:
                obj = json.loads(text)
                config = obj.get("run_config", obj)
                config.get("runtime", {}).update(ledger="[omitted: local billing ledger]")
                published = (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        target = out / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(published)
        manifest.append({"path": relative.as_posix(), "bytes": len(published),
                         "sha256": digest(target), "original_sha256": hashlib.sha256(raw).hexdigest(),
                         "modified": raw != published})
    verification = {"batch": "research442-rerun-20261005", "queued_documents": len(queue),
                    "prediction_counts": counts, "response_status": response_status,
                    "fingerprints_verified": checks, "old_batch_recovered": False,
                    "excluded": ["local_model_config.json", "model_usage.sqlite3", "smoke run"],
                    "credential_scan": "pattern scan passed; explicit source allowlist",
                    "publication_authorization": "User explicitly authorized public inputs, gold and model responses in chat on 2026-10-05"}
    (out / "verification.json").write_text(json.dumps(verification, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(verification, ensure_ascii=False, indent=2))
    print("files", len(manifest), "bytes", sum(row["bytes"] for row in manifest))


if __name__ == "__main__":
    main()
