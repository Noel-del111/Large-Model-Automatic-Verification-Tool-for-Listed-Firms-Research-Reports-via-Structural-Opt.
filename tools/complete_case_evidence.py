"""Attach offline request reconstruction and original text checks to a case pack."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from reconstruct_research442_requests import reconstruct_requests, summarize


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def complete(package, pack, source=None):
    package, pack = package.resolve(), pack.resolve()
    source = source.resolve() if source is not None else None
    if pack == package or pack.is_relative_to(package):
        raise ValueError("Cannot write diagnosis inside the frozen package")
    selection = json.loads((pack / "selection.json").read_text(encoding="utf-8"))
    provenance = json.loads((pack / "provenance.json").read_text(encoding="utf-8"))
    if hashlib.sha256((package / "manifest.json").read_bytes()).hexdigest() != provenance["package_manifest_sha256"]:
        raise ValueError("Case pack and source package differ")
    selected = {c["document_id"] for c in selection["cases"]}
    # Reconstruction checks the frozen input bytes even when the upstream
    # dataset JSON is unavailable on another machine. The two claims differ.
    records = reconstruct_requests(package)
    if not selected <= {record["document_id"] for record in records}:
        raise ValueError("Selected document is outside the frozen request queue")
    verified, source_hash = [], None
    if source is not None:
        raw = source.read_bytes()
        original = json.loads(raw)
        if not isinstance(original, list):
            raise ValueError("Original source must be a JSON array")
        source_hash = hashlib.sha256(raw).hexdigest()
        for line in (package / "inputs/inputs.research442.jsonl").read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            index = row["source_row"]
            if type(index) is not int or not 1 <= index <= len(original):
                raise ValueError(f"Original source index unavailable: {row['doc_id']}")
            entry = original[index - 1]
            record = {"document_id": row["doc_id"], "source_row_1based": index,
                      "content_exact_match": entry["content"] == row["content"],
                      "source_hash_match": source_hash == row["source_sha256"]}
            if not record["content_exact_match"] or not record["source_hash_match"]:
                raise ValueError(f"Original source mismatch: {row['doc_id']}")
            verified.append(record)
    (pack / "requests").mkdir(exist_ok=True)
    for r in records:
        if r["document_id"] in selected:
            save(pack / "requests" / f"{r['document_id'].split(':')[-1]}.json", r)
    save(pack / "request_verification_summary.json", summarize(records))
    compact = [{"document_id": r["document_id"], "status": r["status"], "requests": [
        {key: x.get(key) for key in ["job_index", "status", "messages_sha256", "cache_key", "cache_match", "trace_match", "call_id", "response_source", "response_sha256", "evidence_kind"]}
        for x in r["requests"]]} for r in records]
    (pack / "request_verification_all442.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in compact), encoding="utf-8", newline="\n")
    save(pack / "original_source_verification.json", {
         "status": "verified" if source is not None else "not_available",
         "source_path": str(source) if source is not None else None, "source_sha256": source_hash,
         "records": verified, "verified_records": len(verified), "frozen_input_records": len(records),
         "all442_exact_match": len(verified) == len(records) == 442,
         "note": ("原始基准JSON文本；不是PDF/OCR产物。行号为JSON数组1-based索引。" if source is not None else
                  "本机未提供上游eval_data.json，未重新核验其文件哈希或正文。冻结输入与历史请求指纹已独立校验；不能替代上游原文件核验。")})
    print(json.dumps(summarize(records), ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--pack", type=Path, required=True)
    parser.add_argument("--original-source", type=Path, help="Optional upstream eval_data.json; absence is reported as not_available")
    args = parser.parse_args()
    complete(args.package, args.pack, args.original_source)
