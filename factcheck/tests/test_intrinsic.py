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

    def test_he_joined_pair_is_silent(self):
        """'A和B分别C和D'无顿号列举，不是本规则对象。"""
        findings = check_enumerations(make_doc("金沙中国和银河娱乐分别上涨4.2%和6.4%。"))
        self.assertEqual(findings, [])

    def test_two_分别_groups_are_scored_separately(self):
        findings = check_enumerations(
            make_doc("慢炖锅、压力锅分别实现收入7702.95万元、1433.52万元，分别同比增长8%、24.01%。"))
        self.assertEqual(findings, [])

    def test_broken_listing_with_hanging_punctuation_is_silent(self):
        """残缺列举（'、，'）属数值缺失形态，不按数量不一致处理。"""
        findings = check_enumerations(
            make_doc("其中高端、次高端、区域酒企收入同比增速分别16.5%、14.15%、，区域市场调节空间更大。"))
        self.assertEqual(findings, [])

    def test_synthetic_claim_never_matches_answer_rows(self):
        """合成 claim 用特制 metric 与空 value，保证评测器行匹配永不命中。"""
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
    def _fact(self, metric: str, value: str, unit: str, text: str, label: str) -> Fact:
        return Fact(metric, value, unit, "2024FY", "测试股份", text=text,
                    attributes={"metric_label": label})

    def test_eps_magnitude_guard(self):
        claims = [self._fact("eps_basic", "888", "元/股", "公司2024年基本每股收益888元/股。", "基本每股收益")]
        findings = check_unit_term_mismatch(claims)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].error_type, "unit_term_mismatch")
        self.assertEqual(findings[0].rule_id, "C.INTRINSIC.003")

    def test_normal_eps_is_silent(self):
        claims = [self._fact("eps_basic", "0.62", "元/股", "公司2024年基本每股收益0.62元/股。", "基本每股收益")]
        self.assertEqual(check_unit_term_mismatch(claims), [])

    def test_pe_with_amount_unit(self):
        claims = [self._fact("pe", "15", "亿元", "给予2025年PE15亿元。", "PE")]
        findings = check_unit_term_mismatch(claims)
        self.assertEqual(len(findings), 1)

    def test_pe_far_binding_is_silent(self):
        """'35倍PE，对应目标市值51.8亿元'中 51.8 与 PE 隔标点，属串取，不报。"""
        claims = [self._fact("pe", "51.8", "亿元",
                             "给予2025年预测归母净利35倍PE，对应目标市值51.8亿元。", "PE")]
        self.assertEqual(check_unit_term_mismatch(claims), [])

    def test_claim_without_label_is_silent(self):
        claims = [Fact("pe", "15", "亿元", "2024FY", "测试股份")]
        self.assertEqual(check_unit_term_mismatch(claims), [])


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