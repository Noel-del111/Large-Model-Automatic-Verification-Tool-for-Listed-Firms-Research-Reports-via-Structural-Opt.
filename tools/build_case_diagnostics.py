"""Build an offline, instance-level diagnostic pack from the frozen 442 batch.

No inference, gold changes, production changes, or human sign-off are performed.
Export is deterministic and refuses to overwrite an existing pack.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def rows(path):
    return [json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines() if x.strip()]


def write(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def kind(row):
    obj = row.get("gold") if row["result"] == "FN" else row.get("prediction")
    return obj.get("type", obj.get("error_type"))


def identity(row):
    return (row["document_id"], row["result"], row["all_hints_index"], row["gold_index"])


def rank(row):
    return hashlib.sha256(json.dumps(identity(row), ensure_ascii=False).encode()).hexdigest()


def overlaps(a, b):
    return any(type(p.get("start")) is int and type(g.get("start")) is int
               and p["start"] < g["end"] and g["start"] < p["end"]
               for p in a.get("spans", []) for g in b.get("spans", []))


def sample_cases(cases):
    """Two distinct documents per stratum when possible; known probes are pinned.

    Purposeful diagnostic sample, not a prevalence or performance estimator.
    Within each stratum, stable hash ordering avoids ordering by document filename.
    """
    hybrid = [c for c in cases if c["arm"] == "hybrid"]
    buckets = defaultdict(list)
    for c in hybrid:
        buckets[kind(c), c["result"]].append(c)
    positive = sorted({kind(c) for c in hybrid if c["result"] in {"TP", "FN"}})
    only_fp = sorted({kind(c) for c in hybrid if c["result"] == "FP"} - set(positive))
    selected = []
    for error_type in positive + only_fp:
        for label in (["FP", "FN"] if error_type in positive else ["FP"]):
            pool = sorted(buckets[error_type, label], key=rank)
            chosen = []
            if error_type == "金融要素缺失" and label == "FN":
                chosen = [next(c for c in pool if c["document_id"].endswith(":000368")
                               and c["gold"]["id"].endswith(":0003"))]
            if label == "FP" and any(c["prediction"].get("invalid_anchor") for c in pool):
                chosen.append(next(c for c in pool if c["prediction"].get("invalid_anchor")))
                pool = sorted(pool, key=lambda c: (bool(c["prediction"].get("invalid_anchor")), rank(c)))
            while len(chosen) < min(2, len(pool)):
                remaining = [c for c in pool if identity(c) not in {identity(x) for x in chosen}]
                diverse = [c for c in remaining if c["document_id"] not in {x["document_id"] for x in chosen}]
                chosen.append((diverse or remaining)[0])
            selected.extend(chosen)
    controls = []
    for error_type in positive:
        pool = sorted(buckets[error_type, "TP"], key=rank)
        if error_type == "金融要素缺失":
            row = next(c for c in pool if c["document_id"].endswith(":000334") and c["gold"]["id"].endswith(":0004"))
        elif error_type == "时间信息非法":
            row = next(c for c in pool if c["document_id"].endswith(":000322") and c["prediction"].get("status") == "confirmed_error")
        else:
            row = pool[0]
        controls.append(row)
    if len(selected) != 52 or len(controls) != 12:
        raise ValueError(f"Unexpected strata: {len(selected)} failures / {len(controls)} controls")
    return selected, controls


def _export_worker(package, out, supplemental_selection=None):
    package, out = package.resolve(), out.resolve()
    if out.is_relative_to(package) or out == package:
        raise ValueError("Diagnostic output must be outside the frozen package")
    # Pin every source byte before importing the historical scoring code.
    manifest = read(package / "manifest.json")
    for entry in manifest:
        p = (package / entry["path"]).resolve()
        if not p.is_relative_to(package) or digest(p) != entry["sha256"]:
            raise ValueError(f"Frozen input mismatch: {entry['path']}")
    for entry in read(package / "verification/manifest.json"):
        p = (package / "verification" / entry["path"]).resolve()
        if not p.is_relative_to(package / "verification") or digest(p) != entry["sha256"]:
            raise ValueError(f"Frozen verification mismatch: {entry['path']}")
    # CLI runs in a fresh process; do not create bytecode in the frozen source.
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(package / "source/evals"), str(package / "source/factcheck/src")]
    from run_v2 import validate_predictions
    from yjcheck.review_hints import all_review_hints
    from fined_bench_eval import _contains_error, _maximum_matching

    inputs = {r["doc_id"]: r for r in rows(package / "inputs/inputs.research442.jsonl")}
    golds = {r["document_id"]: r for r in rows(package / "inputs/gold.research442.jsonl")}
    all_cases = rows(package / "verification/cases.jsonl")
    selected, controls = sample_cases(all_cases)
    supplements = []
    if supplemental_selection is not None:
        used = {identity(c) for c in selected + controls}
        canonical = {identity(c): c for c in all_cases if c["arm"] == "hybrid"}
        for entry in read(supplemental_selection)["cases"]:
            key = identity(entry)
            if entry.get("arm") != "hybrid" or key not in canonical or key in used:
                raise ValueError(f"Invalid or duplicate supplemental identity: {key}")
            if canonical[key]["result"] not in {"FP", "FN"}:
                raise ValueError("Supplemental diagnostic cases must be FP or FN")
            supplements.append(canonical[key])
            used.add(key)
    chosen_cases = selected + controls + supplements
    if out.exists():
        raise FileExistsError(f"Refusing to overwrite review material: {out}")
    out.mkdir(parents=True)
    response_index = {}
    for p in (package / "run/model_responses").glob("*.json"):
        cache = read(p)
        response_index[cache["trace"]["call_id"]] = (p, cache)
    documents = {}
    # Recompute all hybrid outcomes, including maximum-matching competition.
    expected, actual = set(), set()
    for c in all_cases:
        if c["arm"] == "hybrid":
            expected.add(identity(c))
    totals = Counter()
    for path in sorted((package / "run/predictions/hybrid").glob("*.json")):
        report = read(path)
        did = report["document_id"]
        inp, gold = inputs[did], golds[did]
        if hashlib.sha256(inp["content"].encode()).hexdigest() != inp["content_sha256"]:
            raise ValueError(f"Input hash mismatch: {did}")
        hints = all_review_hints(validate_predictions(deepcopy(report), inp["content"]))["errors"]
        targets = [g for g in gold["errors"] if g.get("scorable", True)]
        pairs = _maximum_matching(hints, targets, lambda p, g: _contains_error(p, g) and p.get("error_type", p.get("type")) == g.get("type", g.get("error_type")))
        pi, gi = {id(p): i for i, p in enumerate(hints)}, {id(g): i for i, g in enumerate(gold["errors"])}
        match = [(pi[id(p)], gi[id(g)]) for p, g in pairs]
        mp, mg = {i for i, _ in match}, {j for _, j in match}
        outcomes = [("TP", i, j) for i, j in match] + [("FP", i, None) for i in range(len(hints)) if i not in mp] + [("FN", None, gi[id(g)]) for g in targets if gi[id(g)] not in mg]
        for label, i, j in outcomes:
            actual.add((did, label, i, j))
            totals[label] += 1
        if did not in {c["document_id"] for c in chosen_cases}:
            continue
        responses = []
        for trace in report["traces"]:
            call_id = trace.get("runtime_trace", {}).get("call_id")
            if call_id in response_index:
                p, cache = response_index[call_id]
                responses.append({"job_index": trace["job_index"], "source": p.relative_to(package).as_posix(), "sha256": digest(p), **cache})
        documents[did] = {"document_id": did, "input": inp, "gold": gold, "report": report,
                          "all_hints": hints, "matched_pairs": match, "responses": responses,
                          "sources": {"prediction": path.relative_to(package).as_posix(), "prediction_sha256": digest(path)},
                          "limitations": ["原始基准输入为文本；本批次没有PDF解析/OCR链路，不能据此评估OCR。", "历史未留存完整HTTP报文；请求重建另列验证结果。", "空间重叠只用于检索相关证据，不代表同一业务错误。"]}
    if actual != expected:
        raise ValueError(f"Case replay mismatch: missing={len(expected-actual)}, extra={len(actual-expected)}")
    records = []
    for n, row in enumerate(chosen_cases, 1):
        record = deepcopy(row)
        case_id = f"C{n:03d}"
        doc = documents[row["document_id"]]
        if row["prediction"] is not None and row["prediction"] != doc["all_hints"][row["all_hints_index"]]:
            raise ValueError(f"Selected prediction differs from source: {case_id}")
        if row["gold"] is not None and row["gold"] != doc["gold"]["errors"][row["gold_index"]]:
            raise ValueError(f"Selected gold differs from source: {case_id}")
        target = row["gold"] if row["result"] == "FN" else row["prediction"]
        related_p = [{"all_hints_index": i, "same_type": p.get("error_type") == kind(row), "overlap": overlaps(target, p), "candidate": p}
                     for i, p in enumerate(doc["all_hints"]) if overlaps(target, p) or p.get("error_type") == kind(row)]
        related_g = [{"gold_index": i, "same_type": g["type"] == kind(row), "overlap": overlaps(target, g), "gold": g}
                     for i, g in enumerate(doc["gold"]["errors"]) if overlaps(target, g) or g["type"] == kind(row)]
        scoring = [{"all_hints_index": i, "gold_index": j, "same_type": p.get("error_type") == g["type"], "contains_all_gold_spans": _contains_error(p, g), "overlap": overlaps(p, g), "gold_scorable": g.get("scorable", True), "assigned_pair": (i, j) in [tuple(x) for x in doc["matched_pairs"]]}
                   for i, p in enumerate(doc["all_hints"]) for j, g in enumerate(doc["gold"]["errors"])
                   if (row["all_hints_index"] is not None and i == row["all_hints_index"]) or (row["gold_index"] is not None and j == row["gold_index"])]
        cohort = "supplemental" if n > len(selected) + len(controls) else ("control" if n > len(selected) else "diagnostic")
        record.update(case_id=case_id, error_type=kind(row), cohort=cohort, document_ref=f"documents/{row['document_id'].split(':')[-1]}.json", related_predictions=related_p, related_golds=related_g, scoring_checks=scoring,
                      review={"status": "pending_human_review", "analyst": None, "reviewer": None, "human_conclusion": None, "reviewed_at": None})
        records.append(record)
        write(out / f"cases/{case_id}.evidence.json", record)
    for did, doc in documents.items():
        write(out / f"documents/{did.split(':')[-1]}.json", doc)
    write(out / "selection.json", {"version": 1, "arm": "hybrid", "sampling_unit": "error_instance", "sample_type": "purposive_diagnostic_not_prevalence_estimate", "strata": "12 positive types x (2 FP + 2 FN); 2 FP-only types x 2 FP; 12 TP controls", "selection_rule": "stable SHA256 rank; distinct documents within each stratum when possible; one invalid-anchor FP if available; pinned 000368 FN / 000334 TP / 000322 confirmed TP", **({"supplemental_selection": read(supplemental_selection)} if supplemental_selection else {}), "cases": [{k: r[k] for k in ["case_id", "document_id", "result", "error_type", "cohort", "all_hints_index", "gold_index"]} for r in records]})
    write(out / "provenance.json", {"package": package.name, "package_manifest_sha256": digest(package / "manifest.json"), "verified_source_files": len(manifest), "batch": "research442-rerun-20261005", "old_pdf_batch_reproduced": False, "score_protocol": "all-hints typed full-span containment + deterministic one-to-one maximum matching", "hybrid_case_replay_exact_match": actual == expected, "hybrid_totals": dict(totals), "sample_count": len(records), "sample_documents": len(documents), "new_api_calls": 0, "human_finalized_cases": 0})
    print(json.dumps({"out": str(out), "cases": len(records), "documents": len(documents), "replayed_hybrid_totals": dict(totals)}, ensure_ascii=False))


def export(package, out, supplemental_selection=None):
    """Keep historical module imports isolated from the caller's application."""
    command = [sys.executable, "-I", "-B", str(Path(__file__).resolve()),
               "--worker", "--package", str(Path(package).resolve()), "--out", str(Path(out).resolve())]
    if supplemental_selection is not None:
        command += ["--supplemental-selection", str(Path(supplemental_selection).resolve())]
    proc = subprocess.run(command,
                          capture_output=True, encoding="utf-8")
    if proc.returncode:
        raise ValueError(proc.stderr.strip() or "Offline case export failed")
    print(proc.stdout.strip())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--supplemental-selection", type=Path)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    (_export_worker if args.worker else export)(args.package, args.out, args.supplemental_selection)


if __name__ == "__main__":
    main()
