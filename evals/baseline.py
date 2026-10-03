"""Load the archived V1 intrinsic detector under an isolated module namespace."""
from __future__ import annotations
import hashlib
import importlib
import importlib.util
from pathlib import Path
import sys
import zipfile


def frozen_legacy_detect(content, *, document_id, scene, archive):
    archive = Path(archive).resolve()
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    dest = archive.parent / ("unpacked-" + digest[:12])
    package = dest / "repo/factcheck/src/yjcheck"
    if not (dest / ".complete").exists():
        dest.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as source:
            for entry in source.infolist():
                target = (dest / entry.filename).resolve()
                if not target.is_relative_to(dest.resolve()):
                    raise ValueError("invalid baseline archive path")
                if entry.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(source.read(entry))
        (dest / ".complete").write_text(digest, encoding="ascii")
    name = "_fined_baseline_" + digest[:12]
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, package / "__init__.py", submodule_search_locations=[str(package)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    models = importlib.import_module(name + ".models")
    extract = importlib.import_module(name + ".claim_extract").extract_claims
    intrinsic = importlib.import_module(name + ".intrinsic").check_intrinsic_consistency
    types = importlib.import_module(name + ".error_types").ERROR_TYPES
    document = models.Document(document_id, hashlib.sha256(content.encode()).hexdigest(), "baseline", "", "report",
                               blocks=[models.Block("text", content, paragraph=1)])
    findings = intrinsic(document, extract(document))
    errors = []
    for finding in findings:
        spans = []
        for evidence in finding.claim.evidence:
            start, end = evidence.char_start, evidence.char_end
            if start is not None and end is not None:
                spans.append({"start": start, "end": end, "text": content[start:end]})
        definition = types.get(finding.error_type)
        error_type = (definition.fined_name if definition else None) or finding.error_type
        identity = hashlib.sha256(repr((document_id, error_type, spans, finding.rule_id)).encode()).hexdigest()[:24]
        errors.append({"id": identity, "error_type": error_type, "spans": spans, "reason": finding.message,
                       "status": finding.status, "evidence": [], "detector_id": finding.rule_id})
    return {"schema_version": "text-review/1.0", "document_id": document_id, "scene": scene,
            "detector": "legacy_rules", "errors": errors, "traces": [], "baseline_archive_sha256": digest,
            "coverage": {"complete": True, "processed_chars": len(content), "total_chars": len(content),
                         "model_ran": False, "scope": "archived_intrinsic_rules_only"}}
