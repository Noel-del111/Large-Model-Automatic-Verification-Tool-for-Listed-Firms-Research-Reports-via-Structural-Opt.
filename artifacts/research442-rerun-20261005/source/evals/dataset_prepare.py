"""Prepare an audited, group-disjoint FinED corpus without exposing answers to inference.

Run: python evals/dataset_prepare.py --data <FinED-Bench-main> --out data/v2/dataset
Only the two evaluation JSON files are consumed; SFT examples are not evaluation data.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import unicodedata
from typing import Any

try:
    from .fined_bench_eval import anchor_span
except ImportError:
    from fined_bench_eval import anchor_span

SEED = 20261003
SPLITS = {"dev": 0.6, "eval_oct05": 0.2, "holdout_oct07": 0.2}
SOURCES = ("eval_data.json", "eval_data_hard.json")


def _sha(value: str | bytes) -> str:
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def normalized_text(text: str) -> str:
    """Whitespace-only duplicate normalization preserves numbers and punctuation."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def length_bucket(chars: int) -> str:
    for limit, label in ((2000, "lt_2000"), (8000, "2000_7999"), (32000, "8000_31999")):
        if chars < limit:
            return label
    return "ge_32000"


def grouping_keys(title: str, content: str) -> list[str]:
    """Use observable source identifiers, never gold errors or inferred company mentions.

    Company grouping is deliberately conservative: a security code in a title, or a
    legal company name at the beginning of the title. Ordinary body mentions do not
    establish the document's issuer. Unknown companies remain explicitly unknown.
    """
    title = unicodedata.normalize("NFKC", title).strip()
    title_key = re.sub(r"[\W_]+", "", title).casefold()
    keys = ["text:" + _sha(normalized_text(content))]
    if title_key:
        keys.append("title:" + title_key)
    for code in re.findall(r"(?<!\d)(?:[036]\d{5})(?!\d)", title):
        keys.append("company:security:" + code)
    legal = re.match(r"(?:关于)?([\u4e00-\u9fffA-Za-z0-9]{2,50}?(?:股份有限公司|有限责任公司|有限公司))", title)
    if legal:
        keys.append("company:legal:" + legal.group(1))
    return sorted(set(keys))


def _source_paths(root: Path) -> list[Path]:
    roots = (root / "fined_bench", root, root / "FinED-Bench-main" / "fined_bench")
    for candidate in roots:
        paths = [candidate / name for name in SOURCES]
        if all(path.is_file() for path in paths):
            return paths
    raise FileNotFoundError("Expected both fined_bench/eval_data.json and eval_data_hard.json")


def _gold_errors(row: dict, document_id: str) -> tuple[list[dict], list[dict]]:
    errors, exclusions = [], []
    for index, error in enumerate(row.get("errors", []), 1):
        error_id = f"{document_id}:error:{index:04d}"
        if not isinstance(error, dict):
            record = {"id": error_id, "type": "", "spans": [], "scorable": False,
                      "exclusion_reasons": ["invalid_error_object"], "original_annotation": error}
            errors.append(record)
            exclusions.append({"document_id": document_id, "error_id": error_id, "reasons": record["exclusion_reasons"]})
            continue
        starts, texts = error.get("start_idx") or [], error.get("error_span") or []
        reasons = []
        if not isinstance(starts, list) or not isinstance(texts, list) or len(starts) != len(texts) or not texts:
            reasons.append("invalid_span_arrays")
        starts = starts if isinstance(starts, list) else []
        texts = texts if isinstance(texts, list) else []
        spans = []
        for span_index, text in enumerate(texts):
            start = starts[span_index] if span_index < len(starts) else None
            anchored = anchor_span(row["content"], text, start)
            spans.append({"text": anchored.get("text", text), "original_text": text,
                          "original_start": start, "start": anchored.get("start"),
                          "end": anchored.get("end"), "anchor_method": anchored["method"],
                          "candidates": anchored.get("candidates", [])})
            if not anchored["accepted"]:
                reasons.append(f"span_{span_index}:{anchored['method']}")
        if not isinstance(error.get("error_type"), str) or not error["error_type"].strip():
            reasons.append("missing_error_type")
        record = {"id": error_id, "type": error.get("error_type", ""), "spans": spans,
                  "scorable": not reasons, "exclusion_reasons": reasons}
        if reasons:
            record["original_annotation"] = error
        errors.append(record)
        if reasons:
            exclusions.append({"document_id": document_id, "error_id": error_id, "reasons": reasons})
    return errors, exclusions


def assign_splits(documents: list[dict], seed: int = SEED) -> dict:
    """Connected components stay together; greedily balance scene x length strata.

    Exact 60/20/20 counts need not be possible with indivisible source groups. The
    manifest records actual counts and deviations rather than claiming exact ratios.
    """
    parent = list(range(len(documents)))

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    owners = {}
    for index, document in enumerate(documents):
        for key in document["group_keys"]:
            if key in owners:
                left, right = find(index), find(owners[key])
                if left != right:
                    parent[max(left, right)] = min(left, right)
            else:
                owners[key] = index
    components = defaultdict(list)
    for index, document in enumerate(documents):
        components[find(index)].append(document)
    groups = []
    for members in components.values():
        group_id = "group:" + _sha("\n".join(sorted(d["doc_id"] for d in members)))[:20]
        strata = Counter((d["scene"], d["length_bucket"]) for d in members)
        groups.append((group_id, members, strata))
    groups.sort(key=lambda group: (-len(group[1]), _sha(f"{seed}:{group[0]}")))
    totals = Counter((d["scene"], d["length_bucket"]) for d in documents)
    counts = {split: Counter() for split in SPLITS}
    sizes = Counter()
    for group_id, members, strata in groups:
        def cost(split):
            ratio = SPLITS[split]
            delta = 0.0
            for stratum, n in strata.items():
                target = totals[stratum] * ratio
                before = counts[split][stratum] - target
                delta += ((before + n) ** 2 - before ** 2) / max(target, 1)
            target = len(documents) * ratio
            before = sizes[split] - target
            delta += 0.25 * ((before + len(members)) ** 2 - before ** 2) / max(target, 1)
            return delta, _sha(f"{seed}:{group_id}:{split}")
        split = min(SPLITS, key=cost)
        counts[split].update(strata)
        sizes[split] += len(members)
        for document in members:
            document.update(group_id=group_id, split=split)
    return {"groups": len(groups), "largest_group": max((len(g[1]) for g in groups), default=0),
            "method": "connected_source_groups_scene_length_greedy",
            "target_ratios": SPLITS, "actual_documents": dict(sizes),
            "strata": [{"scene": scene, "length_bucket": bucket, "total": total,
                        "splits": {split: counts[split][(scene, bucket)] for split in SPLITS}}
                       for (scene, bucket), total in sorted(totals.items())]}


def _jsonl(path: Path, records: list[dict]) -> dict:
    payload = "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records)
    path.write_text(payload, encoding="utf-8", newline="\n")
    return {"path": path.name, "sha256": _sha(payload), "records": len(records)}


def prepare_dataset(data_root: str | Path, out_dir: str | Path, seed: int = SEED) -> dict[str, Any]:
    sources, documents, golds, exclusions = [], [], {}, []
    for path in _source_paths(Path(data_root)):
        raw = path.read_bytes()
        source_hash = _sha(raw)
        rows = json.loads(raw.decode("utf-8-sig"))
        if not isinstance(rows, list):
            raise ValueError(f"{path.name}: expected a document array")
        sources.append({"file": path.name, "sha256": source_hash, "documents": len(rows)})
        for row_index, row in enumerate(rows, 1):
            if not isinstance(row.get("content"), str) or not isinstance(row.get("errors"), list):
                raise ValueError(f"{path.name}:{row_index}: invalid content/errors")
            doc_id = f"fined:{path.stem}:{source_hash[:16]}:{row_index:06d}"
            content, title, scene = row["content"], str(row.get("title", "")), str(row.get("scene", "unknown"))
            document = {"doc_id": doc_id, "title": title, "scene": scene, "content": content,
                        "length_chars": len(content), "length_bucket": length_bucket(len(content)),
                        "source_file": path.name, "source_sha256": source_hash, "source_row": row_index,
                        "content_sha256": _sha(content), "normalized_content_sha256": _sha(normalized_text(content)),
                        "group_keys": grouping_keys(title, content)}
            documents.append(document)
            errors, excluded = _gold_errors(row, doc_id)
            golds[doc_id] = {"document_id": doc_id, "errors": errors}
            exclusions.extend(excluded)
    assignment = assign_splits(documents, seed)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    files = {}
    for split in SPLITS:
        subset = sorted((d for d in documents if d["split"] == split), key=lambda d: d["doc_id"])
        files[f"inputs.{split}"] = _jsonl(out / f"inputs.{split}.jsonl", subset)
        files[f"gold.{split}"] = _jsonl(out / f"gold.{split}.jsonl", [golds[d["doc_id"]] for d in subset])
    # A small, separately disclosed development-only few-shot file. Use complete
    # short documents with all their annotations, never an incompletely labelled
    # excerpt. The runner must exclude source_id == the current input document_id.
    examples = []
    candidates = sorted((d for d in documents if d["split"] == "dev" and d["length_chars"] <= 2000),
                        key=lambda d: (d["scene"] not in {"个股研报", "行业研报"}, d["length_chars"], d["doc_id"]))
    for document in candidates:
        errors = golds[document["doc_id"]]["errors"]
        if not 1 <= len(errors) <= 4 or not all(error["scorable"] for error in errors):
            continue
        examples.append({"source_split": "dev", "source_id": document["doc_id"],
                         "document_id": document["doc_id"], "content": document["content"],
                         "complete_annotation": True,
                         "errors": [{"error_type": error["type"],
                                     "spans": [{key: span[key] for key in ("text", "start", "end")} for span in error["spans"]],
                                     "reason": "开发集示例"} for error in errors]})
        if len(examples) == 3:
            break
    example_payload = json.dumps(examples, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    (out / "examples.dev.json").write_text(example_payload, encoding="utf-8", newline="\n")
    files["examples.dev"] = {"path": "examples.dev.json", "sha256": _sha(example_payload), "records": len(examples)}
    exact_duplicates = Counter(d["content_sha256"] for d in documents)
    normalized_duplicates = Counter(d["normalized_content_sha256"] for d in documents)
    audit = {"by_scene": dict(sorted(Counter(d["scene"] for d in documents).items())),
             "by_error_type": dict(sorted(Counter(str(e["type"]) for g in golds.values() for e in g["errors"]).items())),
             "anchor_methods": dict(sorted(Counter(s["anchor_method"] for g in golds.values() for e in g["errors"] for s in e["spans"]).items())),
             "exact_duplicate_groups": sum(n > 1 for n in exact_duplicates.values()),
             "normalized_duplicate_groups": sum(n > 1 for n in normalized_duplicates.values()),
             "normalized_duplicate_extra_documents": sum(n - 1 for n in normalized_duplicates.values() if n > 1)}
    manifest = {"schema_version": "1.0.0", "seed": seed, "sources": sources,
                "documents": len(documents), "errors": sum(len(g["errors"]) for g in golds.values()),
                "scorable_errors": sum(e["scorable"] for g in golds.values() for e in g["errors"]),
                "excluded_errors": len(exclusions), "exclusions": exclusions,
                "grouping": assignment, "files": files, "audit": audit,
                "length_unit": "unicode_characters_not_tokens",
                "length_boundaries": [2000, 8000, 32000],
                "anchor_policy": "original_offset_or_unique_exact_or_unique_whitespace_normalized; no fuzzy auto-gold",
                "company_grouping": "title_security_codes_or_title_prefix_legal_names_only; unidentified_companies_not_inferred",
                "input_policy": "inputs contain no errors, gold spans, error labels, answers, or predictions",
                "split_policy": "60/20/20 targets subject to indivisible connected source groups; no answer-based balancing"}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True)
    parser.add_argument("--out", default="data/v2/dataset")
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()
    manifest = prepare_dataset(args.data, args.out, args.seed)
    print(json.dumps({"documents": manifest["documents"], "scorable_errors": manifest["scorable_errors"],
                      "excluded_errors": manifest["excluded_errors"], "splits": manifest["grouping"]["actual_documents"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
