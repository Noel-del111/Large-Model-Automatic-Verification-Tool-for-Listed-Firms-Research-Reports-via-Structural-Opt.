"""Represent all model hints for review and scoring, including rejected anchors."""
from collections import Counter
from hashlib import sha256
import json


def all_review_hints(report):
    """Retain rejected hints as unmatched predictions without counting them twice.

    Direct-model reports already emit rejected anchors in errors; hybrid reports
    keep them in rejected_candidates only. Identical repeated raw rejections are
    still separate review hints, not a claim that they are distinct real errors.

    Every hint receives a stable ``hint_id``; genuine repeated hints are
    distinguished by a traceable occurrence ordinal, so they are not collapsed by
    a naive set-dedup while still carrying the full workload.
    """
    from yjcheck.text_taxonomy import canonical_error_type
    errors = list(report.get("errors", []))
    document_id = str(report.get("document_id", ""))

    def signature(kind, spans, reason):
        return json.dumps([canonical_error_type(kind), spans, str(reason)], ensure_ascii=False, sort_keys=True)

    def make_hint_id(kind, spans, reason, occurrence):
        payload = json.dumps([document_id, canonical_error_type(kind), spans, str(reason), occurrence],
                             ensure_ascii=False, sort_keys=True)
        return sha256(payload.encode()).hexdigest()[:24]

    occurrence = Counter()

    def assign_hint_id(hint, spans):
        key = signature(hint.get("error_type", ""), spans, hint.get("reason", ""))
        occurrence[key] += 1
        hint["hint_id"] = make_hint_id(hint.get("error_type", ""), spans, hint.get("reason", ""), occurrence[key])
        return key

    for error in errors:
        assign_hint_id(error, error.get("original_spans") or error.get("spans"))

    represented = Counter(signature(e.get("error_type", ""), e.get("original_spans"), e.get("reason", ""))
                          for e in errors if e.get("invalid_anchor"))
    for rejected in report.get("rejected_candidates", []):
        raw = rejected.get("candidate", {})
        if not isinstance(raw, dict):
            raw = {"reason": str(raw)}
        key = signature(raw.get("error_type", ""), raw.get("spans"), raw.get("reason", ""))
        if represented[key]:
            represented[key] -= 1
            continue
        hint = {"error_type": canonical_error_type(raw.get("error_type", "")), "spans": [],
                "original_spans": raw.get("spans"), "reason": str(raw.get("reason", "")),
                "status": "needs_review", "invalid_anchor": True, "validation": "anchor_rejected",
                "rejection_reason": rejected.get("reason", ""), "review_hint_only": True}
        assign_hint_id(hint, raw.get("spans"))
        errors.append(hint)
    return {**report, "errors": errors}

