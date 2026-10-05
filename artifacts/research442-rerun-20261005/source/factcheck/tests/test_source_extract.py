from __future__ import annotations

import unittest
from pathlib import Path
import tempfile

from dataclasses import asdict

from yjcheck.models import Block, Document, Evidence, Fact
from yjcheck.rules import check_facts
from yjcheck.source_extract import extract_source_facts


def document(pages, period="2025FY"):
    blocks = []
    for page, lines in enumerate(pages, 1):
        for i, text in enumerate(lines):
            blocks.append(Block(f"p{page}_{i}", text, page=page, bbox=[20, 80+i*20, 550, 92+i*20]))
    return Document("test", "a"*64, "run", "source.pdf", "source", "测试公司", period, blocks)


def select(facts, metric, basis="after", period="2024FY", scope="consolidated"):
    return [f for f in facts if (f.metric, f.basis, f.period, f.scope) == (metric, basis, period, scope)]


class SourceExtractionTests(unittest.TestCase):
    def test_column_order_is_read_from_labels(self):
        for header, row in [
            ("项目 重述前金额 累积影响金额 重述后金额", "营业收入 80.00 20.00 100.00"),
            ("项目 调整前 调整后 影响金额", "营业收入 80.00 100.00 20.00"),
            ("项目 追溯调整后 追溯调整前 追溯调整金额", "营业收入 100.00 80.00 20.00"),
        ]:
            with self.subTest(header=header):
                facts = extract_source_facts(document([["（一）对2024年度合并利润表项目的影响如下：", "单位：万元", header, row]]))
                self.assertEqual(select(facts, "revenue")[0].value, "100.00")
                self.assertEqual(select(facts, "revenue", "before")[0].value, "80.00")
                self.assertEqual(select(facts, "revenue", "change")[0].value, "20.00")
                self.assertEqual(select(facts, "revenue")[0].unit, "万元")

    def test_continuation_does_not_inherit_cover_or_running_header_year(self):
        doc = document([
            ["2025年度专项说明", "（一）对2024年12月31日合并资产负债表项目的影响如下：", "单位：人民币元", "项目 调整前 调整后 影响金额", "货币资金 8.00 9.00 1.00"],
            ["2025年度专项说明", "资本公积 10.00 14.00 4.00", "（二）对2024度合并利润表项目的影响如下：", "项目 调整前 调整后 影响金额", "归属于母公司的净利润 9.00 7.00 -2.00"],
        ])
        facts = extract_source_facts(doc)
        capital = select(facts, "capital_reserve", period="2024-12-31")[0]
        self.assertEqual(capital.value, "14.00")
        self.assertEqual(capital.evidence[0].page, 2)
        self.assertEqual({e["page"] for e in capital.attributes["value_locations"]}, {2})
        self.assertEqual({e.page for e in capital.evidence}, {1, 2})
        self.assertEqual(select(facts, "net_profit_parent")[0].value, "7.00")

    def test_period_and_parent_scope_switches_within_page(self):
        doc = document([["3、合并利润表", "单位：元", "项目 2025年半年度 2024年半年度", "一、营业收入 10.00 8.00", "五、净利润 -2.00 -1.00", "归属于母公司股东的净利润 -1.50 -0.75", "（一）基本每股收益 -0.03 -0.01", "4、母公司利润表", "单位：元", "项目 2025年半年度 2024年半年度", "一、营业收入 4.00 3.00"]], period="2025H1")
        facts = extract_source_facts(doc)
        self.assertEqual(select(facts, "revenue", "reported", "2025H1")[0].value, "10.00")
        self.assertEqual(select(facts, "revenue", "reported", "2024H1", "parent")[0].value, "3.00")
        self.assertEqual(select(facts, "net_profit_parent", "reported", "2025H1")[0].value, "-1.50")
        self.assertEqual(select(facts, "eps_basic", "reported", "2025H1")[0].unit, "元/股")

    def test_balance_beginning_and_end_periods(self):
        facts = extract_source_facts(document([["1、合并资产负债表", "2025年06月30日", "单位：元", "项目 期末余额 期初余额", "存货 9.50 12.00"]], period="2025H1"))
        self.assertEqual(select(facts, "inventory", "reported", "2025-06-30")[0].value, "9.50")
        self.assertEqual(select(facts, "inventory", "reported", "2024-12-31")[0].value, "12.00")

    def test_geometric_recovery_of_disordered_label_amount_fragments(self):
        doc = document([["对2024年度合并利润表项目的影响如下：", "单位：元", "项目 调整前 调整后 影响金额"]])
        doc.blocks += [Block("label", "归属于母公司所有者的净利润", page=1, bbox=[20, 200, 220, 210]),
                       Block("other", "少数股东损益", page=1, bbox=[20, 230, 150, 240]),
                       Block("numbers", "100.00 97.00 -3.00", page=1, bbox=[250, 202, 550, 211])]
        facts = extract_source_facts(doc)
        fact = select(facts, "net_profit_parent")[0]
        self.assertEqual(fact.value, "97.00")
        self.assertEqual({e.block_id for e in fact.evidence[:2]}, {"label", "numbers"})

    def test_table_empty_change_cell_preserves_after_column(self):
        doc = document([["对2024年度合并利润表项目的影响如下：", "单位：万元"]])
        cells = []
        rows = [["项目", "重述前金额", "累积影响金额", "重述后金额"], ["营业收入", "(12.00)", "", "(12.00)"]]
        for r, row in enumerate(rows):
            for c, text in enumerate(row):
                cells.append({"row": r, "col": c, "text": text, "bbox": [20+c*100, 140+r*20, 120+c*100, 160+r*20]})
        doc.blocks.append(Block("table", "", page=1, bbox=[20, 140, 420, 180], type="table", cells=cells))
        facts = extract_source_facts(doc)
        self.assertEqual(select(facts, "revenue")[0].value, "-12.00")
        self.assertEqual(select(facts, "revenue", "change")[0].value, "0.00")
        self.assertEqual(select(facts, "revenue")[0].attributes["cell"], {"row": 1, "col": 3})

    def test_wrapped_parent_profit_label_with_explanation_before_amounts(self):
        facts = extract_source_facts(document([["对2024年度合并利润表项目的影响如下：", "单位：元", "项目 调整后 调整前 影响金额", "1.归属于母公司所有者的净利润（净亏损以", '“-”号填列） 17.25 12.00 5.25']]))
        self.assertEqual(select(facts, "net_profit_parent")[0].value, "17.25")

    def test_distinct_deducted_profit_margin_and_per_share_price_rows(self):
        facts = extract_source_facts(document([["合并利润表", "单位：元", "项目 2025年度 2024年度", "扣除非经常性损益后归属于母公司股东的净利润 900.00 800.00", "毛利率（%） 28.00 25.00", "股价（元/股） 8.20 7.00"]]))
        self.assertEqual(select(facts, "net_profit_parent_excl", "reported", "2025FY")[0].value, "900.00")
        self.assertEqual(select(facts, "gross_margin", "reported", "2025FY")[0].unit, "%")
        self.assertEqual(select(facts, "price", "reported", "2025FY")[0].unit, "元/股")

    def test_margin_requires_row_or_table_percent_unit(self):
        for heading, warning in [("单位：元", True), ("单位：%", False)]:
            facts = extract_source_facts(document([["合并利润表", heading, "项目 2025年度 2024年度", "毛利率 28.00 25.00"]]))
            fact = select(facts, "gross_margin", "reported", "2025FY")[0]
            self.assertEqual("missing_unit" in fact.warnings, warning)
            self.assertEqual(fact.unit, "" if warning else "%")

    def test_fail_page_cannot_supply_facts(self):
        doc = document([["对2024年度合并利润表项目的影响如下：", "单位：元", "项目 调整前 调整后 影响金额", "营业收入 1.00 2.00 1.00"]])
        doc.blocks[-1].status = "fail"
        self.assertFalse(select(extract_source_facts(doc), "revenue"))

    def test_failed_title_cannot_supply_default_period(self):
        doc = document([["2024年度财务报表", "合并利润表", "单位：万元", "项目 本期金额 上期金额", "营业收入 100.00 80.00"]], period="")
        doc.blocks[0].status = "fail"
        facts = [f for f in extract_source_facts(doc) if f.metric == "revenue"]
        self.assertTrue(facts)
        self.assertTrue(all("missing_period" in f.warnings for f in facts))

    def test_warning_title_period_provenance_remains_visible(self):
        doc = document([["2024年度财务报表", "合并利润表", "单位：万元", "项目 本期金额 上期金额", "营业收入 100.00 80.00"]], period="")
        doc.blocks[0].status = "warn"
        facts = [f for f in extract_source_facts(doc) if f.metric == "revenue"]
        self.assertTrue(all(any(e.quality == "warn" for e in f.evidence) for f in facts))

    def test_duplicate_value_keeps_all_equivalent_numeric_locations(self):
        doc = document([["对2024年度合并利润表项目的影响如下：", "单位：元", "项目 调整前 调整后 影响金额", "营业收入 80.00 100.00 20.00"], ["营业收入 80.00 100.00 20.00"]])
        facts = select(extract_source_facts(doc), "revenue")
        self.assertEqual(len(facts), 1)
        self.assertEqual({e["page"] for e in facts[0].attributes["value_locations"]}, {1, 2})

    def test_report_title_year_is_not_confused_with_signature_year(self):
        doc = document([["2025年度对以前年度财务报表追溯调整专项报告", "会计专字[2026]第100号"]])
        doc.blocks[0].text = "2025年度对以前年度报告披露的财务报表数据追溯调整专项报告"
        facts = extract_source_facts(doc)
        fact = select(facts, "publication_year", "reported", "publication")[0]
        self.assertEqual(fact.value, "2025")
        self.assertEqual(fact.unit, "年")
        self.assertEqual(fact.attributes["meaning"], "report_title_fiscal_year")

    def publication_claim(self):
        return Fact("publication_year", "2023", "年", "publication", "测试公司", basis="reported",
                    evidence=[Evidence(doc_id="claim", sha256="b" * 64, block_id="p1", paragraph=1,
                                       text="2023年度披露的追溯调整")])

    def test_repeated_ocr_title_does_not_downgrade_reliable_same_year(self):
        title = "2025年度对以前年度报告披露的财务报表数据追溯调整专项报告"
        for warn_first in (False, True):
            with self.subTest(warn_first=warn_first):
                doc = document([[title], [title]])
                warning = doc.blocks[0 if warn_first else 1]
                warning.status = "warn"
                warning.notes = ["ocr_used:blocks=1:avg_score=0.98"]
                fact = select(extract_source_facts(doc), "publication_year", "reported", "publication")[0]
                self.assertEqual(len(fact.evidence), 1)
                self.assertTrue(all(e.quality == "ok" for e in fact.evidence))
                self.assertEqual({location["block_id"] for location in fact.attributes["value_locations"]},
                                 {fact.evidence[0].block_id})
                self.assertEqual(fact.attributes["alternative_evidence"], [asdict(warning.evidence(doc))])
                self.assertEqual(fact.text, fact.evidence[0].text)
                finding = check_facts([self.publication_claim()], [fact])[0]
                self.assertEqual((finding.status, finding.error_type, finding.suggested_value),
                                 ("confirmed_error", "period", "2025"))

    def test_only_ocr_title_still_requires_review(self):
        doc = document([["2025年度对以前年度报告披露的财务报表数据追溯调整专项报告"]])
        doc.blocks[0].status = "warn"
        doc.blocks[0].notes = ["ocr_used:blocks=1:avg_score=0.98"]
        fact = select(extract_source_facts(doc), "publication_year", "reported", "publication")[0]
        self.assertEqual(fact.evidence[0].quality, "warn")
        self.assertEqual(fact.attributes["alternative_evidence"], [])
        self.assertEqual(check_facts([self.publication_claim()], [fact])[0].status, "needs_review")

    def test_conflicting_title_year_survives_quality_selection(self):
        doc = document([["2025年度对以前年度报告披露的财务报表数据"],
                        ["2025年度对以前年度报告披露的财务报表数据"],
                        ["2024年度对以前年度报告披露的财务报表数据"]])
        doc.blocks[1].status = "warn"
        doc.blocks[1].notes = ["ocr_used:blocks=1:avg_score=0.98"]
        doc.blocks[2].status = "warn"
        doc.blocks[2].notes = ["ocr_used:blocks=1:avg_score=0.98"]
        fact = select(extract_source_facts(doc), "publication_year", "reported", "publication")[0]
        self.assertEqual(fact.value, "2025")
        self.assertIn("conflicting_publication_years", fact.warnings)
        self.assertTrue(all(e.quality == "ok" for e in fact.evidence))
        self.assertEqual(fact.attributes["alternative_evidence"],
                         [asdict(b.evidence(doc)) for b in doc.blocks[1:]])
        self.assertEqual(check_facts([self.publication_claim()], [fact])[0].status, "needs_review")


class LocalPdfRegressionTests(unittest.TestCase):
    """Optional local evidence regressions; no spreadsheet/answer file is read."""
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[3] / "E测试样本最新版"
        if not root.is_dir():
            raise unittest.SkipTest("Local public PDF fixtures are not distributed with the code")
        from yjcheck.adapters import load_document
        cls.temp = tempfile.TemporaryDirectory(prefix="yjcheck-source-tests-")
        cls.documents = {}
        cls.facts = {}
        for path in root.rglob("*.pdf"):
            doc = load_document(path, "source", Path(cls.temp.name), engine="pdfplumber")
            doc.company = path.parent.name
            cls.documents[path.parent.name] = doc
            cls.facts[path.parent.name] = extract_source_facts(doc)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "temp"):
            cls.temp.cleanup()

    def test_restated_reports_keep_actual_source_page_and_correct_columns(self):
        cases = [
            ("佛然能源", "net_profit_parent", "853259112.62", "2024FY", 8),
            ("佛然能源", "cash", "2854736385.53", "2024-12-31", 5),
            ("兰石重装", "capital_reserve", "2460716157.54", "2024-12-31", 6),
            ("兰石重装", "net_profit_parent", "155457792.13", "2024FY", 7),
            ("大地海洋", "total_assets", "1736218407.91", "2024-12-31", 6),
            ("大地海洋", "revenue", "1412067226.16", "2024FY", 8),
        ]
        for company, metric, value, period, page in cases:
            with self.subTest(company=company, metric=metric):
                fact = select(self.facts[company], metric, period=period)[0]
                self.assertEqual(fact.value, value)
                self.assertEqual(fact.evidence[0].page, page)
                self.assertFalse(fact.warnings)
                self.assertTrue(fact.evidence[0].bbox)

    def test_halfyear_consolidated_and_parent_are_kept_separate(self):
        facts = self.facts["群兴玩具"]
        self.assertEqual(select(facts, "revenue", "reported", "2025H1")[0].value, "175533587.43")
        self.assertEqual(select(facts, "net_profit_parent", "reported", "2025H1")[0].value, "-17061684.85")
        self.assertEqual(select(facts, "eps_basic", "reported", "2025H1")[0].value, "-0.03")
        self.assertEqual(select(facts, "revenue", "reported", "2025H1", "parent")[0].value, "5300468.58")
        self.assertEqual(select(facts, "cash", "reported", "2025-06-30")[0].value, "21861087.34")


if __name__ == "__main__":
    unittest.main()
