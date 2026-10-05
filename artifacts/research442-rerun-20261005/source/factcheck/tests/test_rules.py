from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import unittest

from yjcheck.models import Evidence, Fact
from yjcheck.rules import check_facts, normalize


def fact(value="100", unit="万元", **kwargs):
    defaults = dict(metric="revenue", value=value, unit=unit, period="2024FY", company="样例公司",
                    basis="reported", scope="consolidated", currency="CNY", text=f"营业收入{value}{unit}",
                    evidence=[Evidence(doc_id="source-a", sha256="a" * 64, block_id="b1", page=1, bbox=[1, 1, 100, 20], text=f"营业收入{value}{unit}")])
    defaults.update(kwargs)
    return Fact(**defaults)


def result(claim, sources):
    return check_facts([claim], sources)[0]


class NormalizeTests(unittest.TestCase):
    def test_exact_decimal_units_and_negative_accounting(self):
        self.assertEqual(normalize("1.23", "亿元"), Decimal("123000000"))
        self.assertEqual(normalize("（1,234.50）", "万元"), Decimal("-12345000"))
        self.assertEqual(normalize("−2.5%", "%"), Decimal("-.025"))
        self.assertEqual(normalize("0.5", "百分点"), Decimal(".005"))
        self.assertEqual(normalize("0.5", "个百分点"), Decimal(".005"))
        self.assertEqual(normalize("1.2亿元", "亿元"), Decimal("120000000"))

    def test_missing_ambiguous_and_nonfinite_rejected(self):
        for value, unit in [("--", "元"), ("NaN", "元"), ("Inf", "元"), ("约1", "元"),
                            ("12,34", "元"), ("1.2亿元", "万元"), ("100", ""), ("100", "unknown")]:
            with self.subTest(value=value, unit=unit), self.assertRaises(ValueError):
                normalize(value, unit)


class OrdinaryRulesTests(unittest.TestCase):
    def test_equivalent_units_and_rounding(self):
        out = result(fact("1.23", "亿元"), [fact("12345", "万元")])
        self.assertEqual(out.status, "no_issue")
        self.assertEqual(out.calculation["expected_in_claim_unit"], "1.23")
        self.assertEqual(result(fact("1.24", "亿元"), [fact("12350", "万元")]).status, "no_issue")
        self.assertEqual(result(fact("-1.24", "亿元"), [fact("-12350", "万元")]).status, "no_issue")

    def test_boundary_rounding_is_not_a_loose_tolerance(self):
        self.assertEqual(result(fact("1.23", "亿元"), [fact("12350", "万元")]).status, "confirmed_error")
        self.assertEqual(result(fact("1.2", "亿元"), [fact("12500", "万元")]).status, "confirmed_error")

    def test_number_error_has_replacement_and_audit(self):
        out = result(fact("101.0"), [fact("100")])
        self.assertEqual((out.status, out.error_type, out.suggested_value), ("confirmed_error", "number", "100.0"))
        self.assertEqual(out.calculation["sources"][0]["normalized_value"], "1000000")

    def test_only_explicit_numeric_copy_classified_as_unit_error(self):
        self.assertEqual(result(fact("100", "亿元"), [fact("100", "万元")]).error_type, "unit")
        self.assertEqual(result(fact("10", "亿元"), [fact("100", "万元")]).error_type, "number")

    def test_percent_is_not_percentage_point(self):
        out = result(fact("2", "%"), [fact("2", "百分点")])
        self.assertEqual((out.status, out.error_type), ("confirmed_error", "unit"))

    def test_negative_and_zero_amounts_supported(self):
        self.assertEqual(result(fact("-2"), [fact("-2")]).status, "no_issue")
        self.assertEqual(result(fact("0"), [fact("0")]).status, "no_issue")
        self.assertEqual(result(fact("-1706", "万元"), [fact("-17,061,684.85", "元")]).status, "no_issue")

    def test_unknown_basis_can_pass_only_when_before_after_are_invariant(self):
        claim = fact(basis="unknown")
        self.assertEqual(result(claim, [fact(basis="before"), fact(basis="after")]).status, "no_issue")
        self.assertEqual(result(claim, [fact(basis="before"), fact("99", basis="after")]).status, "needs_review")
        self.assertEqual(result(claim, [fact(basis="before")]).status, "needs_review")
        self.assertEqual(result(claim, [fact(basis="before"), fact(basis="after"), fact("0", basis="change")]).status, "no_issue")

    def test_unknown_metadata_does_not_become_a_pass(self):
        for changes in [{"basis": "unknown"}, {"scope": "unknown"}, {"company": ""},
                        {"period": ""}, {"currency": "unknown"}, {"evidence": []}]:
            with self.subTest(changes=changes):
                self.assertEqual(result(fact(**changes), [fact()]).status, "needs_review")
                self.assertEqual(result(fact(), [fact(**changes)]).status, "needs_review")

    def test_foreign_currency_requires_explicit_conversion(self):
        self.assertEqual(result(fact(), [fact(currency="USD")]).status, "needs_review")

    def test_bad_or_unlocatable_evidence_rejected(self):
        for changes in [{"quality": "fail"}, {"quality": "warn"}, {"quality": "unknown"},
                        {"sha256": "short"}, {"bbox": [1, 1, 1, 2]}, {"page": 0},
                        {"block_id": ""}, {"bbox": None}, {"page": None}]:
            source = fact()
            for key, value in changes.items():
                setattr(source.evidence[0], key, value)
            with self.subTest(changes=changes):
                self.assertEqual(result(fact(), [source]).status, "needs_review")

    def test_valid_docx_paragraph_can_be_checked(self):
        source = fact(evidence=[Evidence(doc_id="docx", sha256="a" * 64, block_id="p1", paragraph=1)])
        self.assertEqual(result(fact(), [source]).status, "no_issue")

    def test_conflicting_candidates_cannot_be_selected_by_value(self):
        self.assertEqual(result(fact(), [fact("100"), fact("101")]).status, "needs_review")

    def test_same_docid_different_hash_cannot_hide_a_collision(self):
        first, second = fact(), fact()
        second.evidence[0].sha256 = "b" * 64
        self.assertEqual(result(fact(), [first, second]).status, "needs_review")

    def test_same_filename_different_identity_is_not_conflated(self):
        first, second = fact(), fact()
        first.evidence[0].file = second.evidence[0].file = "annual_report.pdf"
        second.evidence[0].doc_id = "source-b"
        second.evidence[0].sha256 = "b" * 64
        self.assertEqual(result(fact(), [first, second]).status, "no_issue")

    def test_failed_exact_candidate_not_silently_discarded(self):
        bad = fact("99")
        bad.evidence[0].quality = "fail"
        self.assertEqual(result(fact(), [bad, fact()]).status, "needs_review")

    def test_same_value_in_another_period_is_only_a_review_lead(self):
        self.assertEqual(result(fact("100"), [fact("90", period="2023FY")]).status, "needs_review")
        out = result(fact("100"), [fact("100", period="2023FY")])
        self.assertEqual(out.status, "needs_review")
        self.assertEqual(out.calculation["suspected_error_type"], "period")
        self.assertIsNone(out.suggested_value)

    def test_same_value_in_another_scope_or_basis_cannot_prove_misquote(self):
        for changes in [{"scope": "parent"}, {"basis": "before"}]:
            with self.subTest(changes=changes):
                out = result(fact(), [fact(**changes)])
                self.assertEqual(out.status, "needs_review")
                self.assertIsNone(out.suggested_value)

    def test_context_error_uses_intended_period_for_replacement(self):
        out = result(fact("90"), [fact("100"), fact("90", period="2023FY")])
        self.assertEqual((out.error_type, out.suggested_value), ("period", "100"))

    def test_scope_and_adjustment_precede_number(self):
        cases = [(fact("90", scope="parent"), "scope"), (fact("90", basis="before"), "basis"),
                 (fact("90", metric="net_profit"), "scope")]
        for alternate, expected in cases:
            claim = fact("90")
            source = fact("100")
            if alternate.metric == "net_profit":
                claim.metric = source.metric = "net_profit_parent"
            with self.subTest(expected=expected):
                self.assertEqual(result(claim, [source, alternate]).error_type, expected)

    def test_nonunique_alternative_does_not_invent_context_error(self):
        out = result(fact("90"), [fact("100"), fact("90", period="2023FY"), fact("90", period="2022FY")])
        self.assertEqual(out.error_type, "number")

    def test_explicit_publication_year_is_checkable(self):
        out = result(fact("2024", "年", metric="publication_year", period="publication"),
                     [fact("2025", "年", metric="publication_year", period="publication")])
        self.assertEqual(out.status, "confirmed_error")
        self.assertEqual(out.error_type, "period")

    def test_citation_page_and_identity_are_checked(self):
        self.assertEqual(result(fact(attributes={"citation_page": 2}), [fact()]).error_type, "citation")
        self.assertEqual(result(fact(attributes={"citation_doc_id": "wrong"}), [fact()]).error_type, "citation")
        self.assertEqual(result(fact(attributes={"citation_page": 1}), [fact()]).status, "no_issue")
        self.assertEqual(result(fact(attributes={"citation_page": "page1"}), [fact()]).status, "needs_review")

    def test_citation_accepts_any_equivalent_verified_source(self):
        source = fact()
        source.evidence[0].page = 2
        self.assertEqual(result(fact(attributes={"citation_page": 2}), [fact(), source]).status, "no_issue")

    def test_legacy_citation_uses_first_evidence_not_context_pages(self):
        source = fact()
        source.evidence[0].page = 2
        source.evidence.append(Evidence(doc_id="source-a", sha256="a" * 64, block_id="heading", page=1,
                                        bbox=[1, 1, 100, 20], text="2024年度合并利润表"))
        self.assertEqual(result(fact(attributes={"citation_page": 1}), [source]).error_type, "citation")
        self.assertEqual(result(fact(attributes={"citation_page": 2}), [source]).status, "no_issue")

    def test_marked_value_location_overrides_context_evidence_order(self):
        source = fact()
        source.evidence.append(Evidence(doc_id="source-a", sha256="a" * 64, block_id="value", page=2,
                                        bbox=[1, 1, 100, 20], text="营业收入100万元"))
        source.attributes["value_locations"] = [{"doc_id": "source-a", "block_id": "value", "page": 2, "paragraph": None}]
        self.assertEqual(result(fact(attributes={"citation_page": 1}), [source]).error_type, "citation")
        self.assertEqual(result(fact(attributes={"citation_page": 2}), [source]).status, "no_issue")

    def test_value_locations_cannot_invent_a_page_without_evidence(self):
        source = fact(attributes={"value_locations": [{"doc_id": "source-a", "block_id": "missing", "page": 9, "paragraph": None}]})
        self.assertEqual(result(fact(attributes={"citation_page": 9}), [source]).status, "needs_review")
        self.assertEqual(result(fact(attributes={"citation_page": 1}), [source]).status, "needs_review")

    def test_inputs_are_unchanged(self):
        claim, sources = fact("99"), [fact()]
        before = deepcopy((claim, sources))
        result(claim, sources)
        self.assertEqual((claim, sources), before)


class DerivedRulesTests(unittest.TestCase):
    def test_derived_citations_only_include_numeric_input_locations(self):
        current, prior = fact("120"), fact("100", period="2023FY")
        for page, source in [(2, current), (3, prior)]:
            source.evidence[0].page = page
            source.evidence.append(Evidence(doc_id="source-a", sha256="a" * 64, block_id="heading", page=1,
                                            bbox=[1, 1, 100, 20], text="合并利润表单位万元"))
            source.attributes["value_locations"] = [{"doc_id": "source-a", "block_id": "b1", "page": page, "paragraph": None}]
        claim = fact("20", "%", metric="revenue_yoy", attributes={"citation_page": 1})
        self.assertEqual(result(claim, [current, prior]).error_type, "citation")
        for page in (2, 3):
            claim.attributes["citation_page"] = page
            checked = result(claim, [current, prior])
            self.assertEqual(checked.status, "no_issue")
            self.assertEqual({x["page"] for x in checked.evidence[0].attributes["value_locations"]}, {2, 3})

    def test_yoy_uses_normalized_current_and_previous_year(self):
        claim = fact("20.0", "%", metric="revenue_yoy")
        out = result(claim, [fact("1.2", "亿元"), fact("10000", "万元", period="2023FY")])
        self.assertEqual(out.status, "no_issue")
        self.assertEqual(out.calculation["derivation"]["formula"], "(current - prior) / prior * 100")

    def test_yoy_errors_and_negative_current(self):
        out = result(fact("10.0", "%", metric="revenue_yoy"), [fact("120"), fact("100", period="2023FY")])
        self.assertEqual((out.error_type, out.suggested_value), ("number", "20.0"))
        self.assertEqual(result(fact("-150", "%", metric="revenue_yoy"), [fact("-50"), fact("100", period="2023FY")]).status, "no_issue")

    def test_yoy_zero_negative_base_and_missing_input_need_review(self):
        claim = fact("20", "%", metric="revenue_yoy")
        for sources in [[fact("120")], [fact("120"), fact("0", period="2023FY")],
                        [fact("120"), fact("-100", period="2023FY")],
                        [fact("20", "%", metric="revenue_yoy")]]:
            with self.subTest(sources=sources):
                self.assertEqual(result(claim, sources).status, "needs_review")

    def test_yoy_rejects_failed_input(self):
        current, prior = fact("120"), fact("100", period="2023FY")
        prior.evidence[0].quality = "fail"
        self.assertEqual(result(fact("20", "%", metric="revenue_yoy"), [current, prior]).status, "needs_review")
        self.assertEqual(result(fact("20", "%", metric="revenue_yoy"), [current, prior, fact("100", period="2023FY")]).status, "needs_review")

    def test_pe_requires_verified_price_and_eps(self):
        claim = fact("10.0", "倍", metric="pe")
        sources = [fact("20", "元/股", metric="price"), fact("2", "元/股", metric="eps_basic")]
        self.assertEqual(result(claim, sources).status, "no_issue")
        sources[0].unit = sources[1].unit = "元"
        self.assertEqual(result(claim, sources).status, "no_issue")
        self.assertEqual(result(claim, sources[:1]).status, "needs_review")
        sources[1].value = "0"
        self.assertEqual(result(claim, sources).status, "needs_review")


if __name__ == "__main__":
    unittest.main()
