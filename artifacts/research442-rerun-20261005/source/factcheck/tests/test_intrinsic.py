"""Intrinsic 一致性检查回归：借鉴 FinED-Bench 研报类错误形态，全部转人工。"""
from __future__ import annotations

import unittest

from yjcheck.intrinsic import (check_enumerations, check_intrinsic_consistency,
                               check_unit_term_mismatch)
from yjcheck.models import Block, Document, Fact


def make_doc(*texts: str) -> Document:
    blocks = [Block(f"b{i}", text, page=1, paragraph=i + 1) for i, text in enumerate(texts)]
    return Document("doc", "a" * 64, "run", "report.docx", "report", "测试股份", "2024FY", blocks)


class EnumerationTests(unittest.TestCase):
    def test_partial_or_qualified_lists_are_not_compared_as_complete_counts(self):
        normal = ("甲公司、乙公司分别为约1%、约2%。",
                  "甲公司、乙公司分别为1%（2024年）、2%（2024年）。",
                  "甲公司、乙公司分别为100元/吨、200元/吨。",
                  "甲公司、乙公司、丙公司分别上涨1-3%、4%、5%。",
                  "甲公司、乙公司分别为1亿元（同比增长10%）、2亿元（同比增长20%）。",
                  "甲公司、乙公司、丙公司分别为1%、2%以上。")
        for text in normal:
            with self.subTest(text=text):
                self.assertEqual(check_enumerations(make_doc(text)), [])

    def test_comma_context_is_not_an_object_but_explicit_company_list_is_preserved(self):
        for text in ("近年来，公司营业收入分别为1亿元、2亿元和3亿元。",
                     "上半年，公司营业收入分别为1亿元、2亿元和3亿元。"):
            self.assertEqual(check_enumerations(make_doc(text)), [])
        self.assertEqual(check_enumerations(make_doc("甲公司，乙公司分别为1%、2%。")), [])
        findings = check_enumerations(make_doc("甲公司，乙公司分别为1%、2%、3%。"))
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].status, "needs_review")

    def test_reporting_period_is_not_an_enumerated_subject(self):
        texts = ("报告期各期末，公司资产负债率分别为64.61%、67.58%和70.44%。",
                 "报告期内，公司以境内销售为主，内销收入金额分别为1万元、2万元和3万元。",
                 "报告期内，公司直接人工分别为27,003.30万元、34,352.12万元和47,665.34万元。",
                 "报告期内，公司营业收入分别为20.05亿元、26.17亿元、28.66亿元和8.98亿元，净利润分别为1亿元、2亿元、3亿元和4亿元。",
                 "公司流动资产主要由货币资金、应收账款、存货及其他资产构成，报告期各期末，上述四项资产占比合计分别为97.66%、98.83%和97.64%。")
        for text in texts:
            self.assertEqual(check_enumerations(make_doc(text)), [], text)

    def test_other_indicator_series_does_not_extend_corresponding_value_list(self):
        self.assertEqual(check_enumerations(make_doc("甲公司、乙公司分别为1%、2%，同比增长3%、4%。")), [])
        findings = check_enumerations(make_doc("报告期内，甲公司、乙公司分别为1%、2%、3%。"))
        self.assertEqual(len(findings), 1)

    def test_listing_with_shifenwei_has_the_same_object_order(self):
        findings = check_enumerations(make_doc("甲公司、乙公司分别为1%、2%、3%。"))
        self.assertEqual(len(findings), 1)
        self.assertIn("对象 2 个、数值 3 个", findings[0].message)

    def test_listing_object_number_mismatch(self):
        findings = check_enumerations(make_doc("农商行、国有行、城商行分别上涨2.78%、2.52%、1.25%、3.52%。"))
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].status, "needs_review")
        self.assertEqual(findings[0].error_type, "numeric_inconsistency")
        self.assertEqual(findings[0].rule_id, "C.INTRINSIC.001")
        self.assertIn("对象 3 个、数值 4 个", findings[0].message)

    def test_matching_listing_is_silent(self):
        findings = check_enumerations(make_doc("家用电器、基础化工分别上涨8.5%、4.5%。"))
        self.assertEqual(findings, [])

    def test_range_and_ordinal_are_not_counted(self):
        findings = check_enumerations(make_doc("第1名、第2名分别上涨1-3%、4%。"))
        self.assertEqual(findings, [])
        findings = check_enumerations(make_doc("2024年1-3月与2024年4-6月分别实现营收1.2亿元、1.3亿元。"))
        self.assertEqual(findings, [])

    def test_synthetic_claim_preserves_type_and_location(self):
        """文本候选保留类型和原句，不虚构财务数值。"""
        findings = check_enumerations(make_doc("甲公司、乙公司、丙公司分别上涨1%、2%、3%、4%。"))
        self.assertEqual(len(findings), 1)
        claim = findings[0].claim
        self.assertEqual(claim.metric, "numeric_inconsistency")
        self.assertEqual(claim.value, "")
        self.assertEqual(claim.unit, "")
        self.assertEqual(claim.attributes.get("extraction"), "intrinsic")


class TimeConflictTests(unittest.TestCase):
    def test_backward_year_range(self):
        findings = check_intrinsic_consistency(
            make_doc("我国特高压投资规模的第一阶段是2017-2014年，投资额度达1966亿元。"), [])
        time = [f for f in findings if f.rule_id == "C.INTRINSIC.002"]
        self.assertEqual(len(time), 1)
        self.assertIn("倒置", time[0].message)

    def test_backward_from_to(self):
        findings = check_intrinsic_consistency(
            make_doc("该产品价格从2025年1月的1.8万元/吨上涨至2024年5月的2.9万元/吨。"), [])
        time = [f for f in findings if f.rule_id == "C.INTRINSIC.002"]
        self.assertEqual(len(time), 1)

    def test_forward_range_is_silent(self):
        findings = check_intrinsic_consistency(
            make_doc("2020-2024年中国SaaS市场规模CAGR为25.24%。"), [])
        self.assertFalse(any(f.rule_id == "C.INTRINSIC.002" for f in findings))

    def test_normal_multi_year_narration_is_silent(self):
        findings = check_intrinsic_consistency(
            make_doc("公司2024年实现营业收入42.37亿元，预计2025年营收达50亿元。"), [])
        self.assertFalse(any(f.rule_id == "C.INTRINSIC.002" for f in findings))

    def test_single_year_is_silent(self):
        findings = check_intrinsic_consistency(
            make_doc("2024年实现营收10.00亿元，同比增长5.00%。"), [])
        self.assertFalse(any(f.rule_id == "C.INTRINSIC.002" for f in findings))


class UnitTermTests(unittest.TestCase):
    def test_eps_magnitude_guard(self):
        claims = [Fact("eps_basic", "888", "元/股", "2024FY", "测试股份")]
        findings = check_unit_term_mismatch(claims)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].error_type, "unit_term_mismatch")
        self.assertEqual(findings[0].rule_id, "C.INTRINSIC.003")

    def test_normal_eps_is_silent(self):
        claims = [Fact("eps_basic", "0.62", "元/股", "2024FY", "测试股份")]
        self.assertEqual(check_unit_term_mismatch(claims), [])

    def test_pe_with_amount_unit(self):
        claims = [Fact("pe", "15", "亿元", "2024FY", "测试股份")]
        findings = check_unit_term_mismatch(claims)
        self.assertEqual(len(findings), 1)


class ContractTests(unittest.TestCase):
    def test_everything_needs_review_without_suggested_value(self):
        doc = make_doc("农商行、国有行、城商行分别上涨2.78%、2.52%、1.25%、3.52%。")
        claims = [Fact("eps_basic", "999", "元/股", "2024FY", "测试股份")]
        findings = check_intrinsic_consistency(doc, claims)
        self.assertTrue(findings)
        for finding in findings:
            self.assertEqual(finding.status, "needs_review")
            self.assertIsNone(finding.suggested_value)
            self.assertIn("人工", finding.suggestion)


if __name__ == "__main__":
    unittest.main()
