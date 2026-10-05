"""Model paraphrases of one numeric occurrence must not duplicate checked facts."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "factcheck/src"), str(ROOT / "pdfparse/src")]
from yjcheck.claim_extract import extract_claims
from yjcheck.model import ModelConfig
from yjcheck.models import Block, Document, Fact
from yjcheck.pipeline import _merge_model_claims, check_documents


class ModelClaimAlignmentTests(unittest.TestCase):
    def setUp(self):
        self.report = Document("report", "a" * 64, "run", "report.docx", "report", "测试公司", "2024FY", [
            Block("title", "测试公司2024年度追溯调整模拟研报", paragraph=1),
            Block("p", "本次调整对资本公积的影响金额为 100,000万元，对未分配利润的影响为 -414.10万元。", paragraph=2)])

    def candidate(self, metric="capital_reserve", value="100,000", quote="本次调整对资本公积的影响金额为 100,000万元", start=None):
        block = self.report.blocks[1]
        start = block.text.index(quote) if start is None else start
        return Fact(metric, value, "万元", "2024FY", "测试公司", basis="change", scope="unknown", text=quote,
                    evidence=[block.evidence(self.report, quote, start, start + len(quote))],
                    warnings=["model_semantics_require_review"], attributes={"extraction": "model", "model": "saved-reply"})

    def test_same_occurrence_merges_period_scope_disagreement_and_preserves_raw_candidate(self):
        claims = extract_claims(self.report)
        original = deepcopy(claims)
        candidates = [self.candidate(), self.candidate("retained_earnings", "-414.10", "对未分配利润的影响为 -414.10万元")]
        raw = [candidate.to_dict() for candidate in candidates]
        self.assertEqual(_merge_model_claims(claims, candidates, self.report), 2)
        self.assertEqual(len(claims), 2)
        for claim, before, candidate in zip(claims, original, raw):
            self.assertEqual((claim.fact_id, claim.period, claim.scope, claim.warnings),
                             (before.fact_id, before.period, before.scope, before.warnings))
            alternative = claim.attributes["model_extraction_alternatives"][0]
            self.assertEqual(alternative["candidate"], candidate)
            self.assertEqual(alternative["dimension_differences"]["period"], {"canonical": "2024-12-31", "model": "2024FY"})

    def test_same_value_in_different_occurrence_never_merges(self):
        self.report.blocks[1].text = "本次资本公积影响100,000万元。另一次资本公积影响100,000万元。"
        claims = extract_claims(self.report)[:1]
        candidate = self.candidate(quote="另一次资本公积影响100,000万元")
        self.assertEqual(_merge_model_claims(claims, [candidate], self.report), 0)
        self.assertEqual(len(claims), 2)

    def test_equal_before_after_amounts_align_only_by_explicit_original_labels(self):
        block = self.report.blocks[1]
        block.text = "重述后，公司2024年度实现营业收入9.47亿元，重述前为9.47亿元。"
        claims = extract_claims(self.report)
        candidate = Fact("revenue", "9.47", "亿元", "2024FY", "测试公司", basis="before", scope="consolidated",
                         text=block.text, evidence=[block.evidence(self.report, block.text, 0, len(block.text))],
                         warnings=["model_semantics_require_review"], attributes={"extraction": "model"})
        self.assertEqual(_merge_model_claims(claims, [candidate], self.report), 1)
        self.assertEqual(len(claims), 2)
        before = next(claim for claim in claims if claim.basis == "before")
        after = next(claim for claim in claims if claim.basis == "after")
        self.assertEqual(len(before.attributes["model_extraction_alternatives"]), 1)
        self.assertNotIn("model_extraction_alternatives", after.attributes)
        self.assertFalse(before.warnings)

    def test_unresolved_same_id_candidate_cannot_overwrite_existing_rule_claim(self):
        claims = extract_claims(self.report)
        candidate = deepcopy(claims[0])
        original_id = candidate.fact_id
        candidate.attributes = {"extraction": "model"}
        candidate.evidence[0].char_start = None
        candidate.warnings = ["model_semantics_require_review"]
        self.assertEqual(_merge_model_claims(claims, [candidate], self.report), 0)
        self.assertEqual(claims[0].fact_id, original_id)
        self.assertFalse(claims[0].warnings)
        self.assertNotEqual(candidate.fact_id, original_id)
        self.assertEqual(candidate.attributes["original_model_fact_id"], original_id)
        self.assertIn("model_semantics_require_review", candidate.warnings)

    def test_missing_ambiguous_or_mismatched_location_never_merges(self):
        for kind in ("missing", "wrong", "ambiguous"):
            with self.subTest(kind=kind):
                report = deepcopy(self.report)
                candidate = self.candidate()
                if kind == "missing":
                    candidate.evidence[0].char_start = None
                elif kind == "wrong":
                    candidate.evidence[0].text = "假原文"
                else:
                    block = report.blocks[1]
                    block.text = "资本公积影响100,000万元，资本公积影响100,000万元。"
                    candidate.evidence = [block.evidence(report, block.text, 0, len(block.text))]
                claims = extract_claims(report)
                count = len(claims)
                self.assertEqual(_merge_model_claims(claims, [candidate], report), 0)
                self.assertEqual(len(claims), count + 1)

    def test_original_verdicts_survive_model_duplicates_and_model_only_stays_review(self):
        source = Document("source", "b" * 64, "source-run", "source.pdf", "source", "测试公司", "2024FY", [Block("p", "原始财报", page=1)])
        source_facts = [Fact("capital_reserve", "10000", "万元", "2024-12-31", "测试公司", basis="change", evidence=[source.blocks[0].evidence(source)]),
                        Fact("retained_earnings", "-414.10", "万元", "2024-12-31", "测试公司", basis="change", evidence=[source.blocks[0].evidence(source)])]
        candidates = [self.candidate(), self.candidate("retained_earnings", "-414.10", "对未分配利润的影响为 -414.10万元")]
        trace = {"status": "ok", "response": "original paid response", "runtime": {"call_id": "saved-call"}}
        with patch("yjcheck.pipeline.extract_source_facts", side_effect=lambda document: source_facts if document.role == "source" else []):
            baseline = check_documents(deepcopy(self.report), [source])
            with patch("yjcheck.model.extract_with_model", return_value=(candidates, [trace])):
                combined = check_documents(deepcopy(self.report), [source], ModelConfig("https://example.invalid", "test"))
        self.assertEqual([(f["claim"]["metric"], f["status"]) for f in combined["findings"]],
                         [(f["claim"]["metric"], f["status"]) for f in baseline["findings"]])
        self.assertEqual(combined["summary"]["model_candidates_aligned"], 2)
        self.assertEqual(combined["model_traces"], [trace])
        with patch("yjcheck.pipeline.extract_claims", return_value=[]), patch("yjcheck.pipeline.extract_source_facts", return_value=[]), \
                patch("yjcheck.model.extract_with_model", return_value=([self.candidate()], [trace])):
            model_only = check_documents(deepcopy(self.report), [], ModelConfig("https://example.invalid", "test"))
        self.assertEqual(model_only["summary"]["model_candidates_aligned"], 0)
        self.assertEqual(model_only["findings"][0]["status"], "needs_review")
        self.assertIn("model_semantics_require_review", model_only["findings"][0]["claim"]["warnings"])


if __name__ == "__main__":
    unittest.main()
