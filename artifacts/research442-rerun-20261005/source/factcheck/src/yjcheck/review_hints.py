"""Represent all model hints for review and scoring, including rejected anchors."""
from collections import Counter
import json


def all_review_hints(report):
    """Retain rejected hints as unmatched predictions without counting them twice.

    Direct-model reports already emit rejected anchors in errors; hybrid reports
    keep them in rejected_candidates only. Identical repeated raw rejections are
    still separate review hints, not a claim that they are distinct real errors.
    """
    from yjcheck.text_taxonomy import canonical_error_type
    errors = list(report.get("errors", []))

    def signature(kind, spans, reason):
        return json.dumps([canonical_error_type(kind), spans, str(reason)], ensure_ascii=False, sort_keys=True)

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
        errors.append({"error_type": canonical_error_type(raw.get("error_type", "")), "spans": [],
                       "original_spans": raw.get("spans"), "reason": str(raw.get("reason", "")),
                       "status": "needs_review", "invalid_anchor": True, "validation": "anchor_rejected",
                       "rejection_reason": rejected.get("reason", ""), "review_hint_only": True})
    return {**report, "errors": errors}

