"""Claim regressions using real DOCX/PDF inputs and original-block locators.

PDFs are generated with a Chinese CID font at test time, then processed through
B's ordinary parser and C's adapter. No parser/extractor is mocked here.
"""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

from yjcheck.adapters import bind_company, load_document
from yjcheck.claim_extract import extract_claims
from yjcheck.models import Block, Document, Evidence, Fact
from yjcheck.pipeline import run_check, verify_artifacts


REPO = Path(__file__).resolve().parents[2]


def make_document(text: str) -> Document:
    return Document("doc", "a" * 64, "run", "report.docx", "report", "测试股份", "2024FY",
                    [Block("p1", text, paragraph=1)])


def make_pdf(path: Path, lines: list[str]) -> None:
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    pdf = canvas.Canvas(str(path), pagesize=(595, 842))
    pdf.setFont("STSong-Light", 11)
    for number, line in enumerate(lines):
        pdf.drawString(48, 780 - number * 20, line)
    pdf.save()


def make_docx(path: Path, paragraphs: list[str]) -> None:
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:body>' + ''.join('<w:p><w:r><w:t xml:space="preserve">' + escape(p)
                                      + '</w:t></w:r></w:p>' for p in paragraphs)
                + '<w:sectPr/></w:body></w:document>')
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document)
        archive.writestr("[Content_Types].xml", '<?xml version="1.0"?>'
                         '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                         '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                         '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                         '</Types>')
        archive.writestr("_rels/.rels", '<?xml version="1.0"?>'
                         '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                         '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                         '</Relationships>')


def signature(fact: Fact) -> tuple:
    return fact.metric, fact.value, fact.unit, fact.period, fact.basis, fact.scope, fact.currency


class LocatorAssertions:
    def assert_original_quotes(self, facts, original_blocks):
        for fact in facts:
            self.assertTrue(fact.evidence)
            for evidence in fact.evidence:
                with self.subTest(fact=signature(fact), block=evidence.block_id):
                    self.assertIn(evidence.block_id, original_blocks,
                                  "Evidence must refer to original B blocks, not invented joined blocks")
                    block = original_blocks[evidence.block_id]
                    self.assertIsInstance(evidence.char_start, int)
                    self.assertIsInstance(evidence.char_end, int)
                    self.assertEqual(block.text[evidence.char_start:evidence.char_end], evidence.text)
                    self.assertGreater(evidence.char_end, evidence.char_start)
                    self.assertLessEqual(evidence.char_end, len(block.text))
                    self.assertEqual(evidence.page, block.page)
                    self.assertEqual(evidence.paragraph, block.paragraph)
                    if block.page is not None:
                        self.assertTrue(evidence.bbox)


class ClaimTextRegressionTests(unittest.TestCase, LocatorAssertions):
    def test_textual_decrease_produces_negative_growth(self):
        claims = extract_claims(make_document("2024年营业收入同比下降20%。"))
        self.assertEqual([(f.metric, f.value, f.unit) for f in claims], [("revenue_yoy", "-20", "%")])

    def test_currency_is_extracted_instead_of_silently_assumed(self):
        for label, code in [("美元", "USD"), ("港元", "HKD"), ("欧元", "EUR")]:
            with self.subTest(currency=code):
                claims = extract_claims(make_document(f"2024年营业收入为100万{label}。"))
                self.assertEqual([(f.value, f.unit, f.currency) for f in claims], [("100", "万元", code)])

    def test_each_metric_keeps_its_own_citation(self):
        claims = extract_claims(make_document("2024年营业收入100万元（见财报第2页），净利润10万元（见财报第3页）。"))
        self.assertEqual([(f.metric, f.attributes["citation_page"]) for f in claims], [("revenue", 2), ("net_profit", 3)])

    def test_explicit_scope_switch_overrides_previous_scope(self):
        claims = extract_claims(make_document("2024年母公司口径营业收入100万元，合并口径净利润20万元。"))
        self.assertEqual([(f.metric, f.scope) for f in claims], [("revenue", "parent"), ("net_profit", "consolidated")])

    def test_previous_year_does_not_leak_into_current_year_metric(self):
        claims = extract_claims(make_document("2024年营业收入100万元，上年同期90万元，本年净利润20万元。"))
        self.assertEqual([(f.metric, f.period) for f in claims], [("revenue", "2024FY"), ("revenue", "2023FY"), ("net_profit", "2024FY")])

    def test_ambiguous_rate_transition_is_retained_for_review(self):
        claims = extract_claims(make_document("2024年毛利率由20%提升至25%，增加5个百分点。"))
        self.assertEqual(len(claims), 3)
        self.assertTrue(all("rate_transition_requires_explicit_periods" in f.warnings for f in claims))

    def test_rate_and_pe_do_not_borrow_amounts_from_later_clauses(self):
        samples = (
            "2024年分子砌块收入9.36亿元，毛利率为42%，其中杂环化合物收入4.75亿元。",
            "公司毛利率37.56%，减值损失1.15亿元。",
            "给予2025年PE20倍，目标价格54.31元，对应市值77.4亿元。",
            "预计公司毛利率保持稳定，减值损失约28亿元。",
        )
        for text in samples:
            with self.subTest(text=text):
                claims = extract_claims(make_document(text))
                rate_claims = [fact for fact in claims if fact.metric in {"gross_margin", "pe"}]
                self.assertFalse(any("亿" in fact.unit or "万" in fact.unit for fact in rate_claims), rate_claims)

    def test_immediate_wrong_rate_unit_is_still_extracted_for_guardrail(self):
        claims = extract_claims(make_document("公司毛利率为28亿元。"))
        self.assertEqual([(fact.metric, fact.value, fact.unit) for fact in claims],
                         [("gross_margin", "28", "亿元")])

    def test_evidence_file_hash_participates_in_fact_identity(self):
        first = Fact("revenue", "100", "万元", "2024FY", "测试股份", basis="reported",
                     evidence=[Evidence(doc_id="same", sha256="a" * 64, run_id="run", block_id="b1", paragraph=1, text="same")])
        second = Fact("revenue", "100", "万元", "2024FY", "测试股份", basis="reported",
                      evidence=[Evidence(doc_id="same", sha256="b" * 64, run_id="run", block_id="b1", paragraph=1, text="same")])
        self.assertNotEqual(first.fact_id, second.fact_id)

    def test_whitespace_offsets_slice_the_original_quote(self):
        document = make_document("2024年营业收入为 100 万元，归属于母公司股东的\n净利润为 20 万元。")
        original = {b.block_id: deepcopy(b) for b in document.blocks}
        claims = extract_claims(document)
        self.assertEqual([f.metric for f in claims], ["revenue", "net_profit_parent"])
        self.assert_original_quotes(claims, original)

    def test_line_assembly_does_not_borrow_amount_from_another_column(self):
        document = Document("doc", "a" * 64, "run", "report.pdf", "report", "测试股份", "2024FY", [
            Block("title", "测试股份2024年研究报告", page=1, bbox=[48, 60, 260, 72]),
            Block("left", "2024年营业收入为", page=1, bbox=[48, 100, 190, 112]),
            Block("right", "999万元。", page=1, bbox=[350, 120, 450, 132]),
        ])
        self.assertEqual(extract_claims(document), [])

    def test_line_assembly_does_not_borrow_amount_across_page_boundary(self):
        document = Document("doc", "a" * 64, "run", "report.pdf", "report", "测试股份", "2024FY", [
            Block("title", "测试股份2024年研究报告", page=1, bbox=[48, 60, 260, 72]),
            Block("p1", "2024年营业收入为", page=1, bbox=[48, 100, 190, 112]),
            Block("p2", "999万元。", page=2, bbox=[48, 120, 150, 132]),
        ])
        self.assertEqual(extract_claims(document), [])


class PdfReportExtractionTests(unittest.TestCase, LocatorAssertions):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="yjcheck-report-pdf-")
        cls.directory = Path(cls.temp.name)
        cls.title = "测试股份（600001）2024年研究报告"
        cls.body_lines = ["公司2024年归属于母公司股东的", "净利润为100万元，营业收入为",
                          "500万元（见财报第2页），基本每股收益为1.20元（见财报第3页）。"]
        cls.pdf_path = cls.directory / "report.pdf"
        cls.docx_path = cls.directory / "report.docx"
        make_pdf(cls.pdf_path, [cls.title, *cls.body_lines])
        make_docx(cls.docx_path, [cls.title, "\n".join(cls.body_lines)])
        cls.pdf_doc = load_document(cls.pdf_path, "report", cls.directory / "work", engine="pdfplumber")
        cls.docx_doc = load_document(cls.docx_path, "report", cls.directory / "work", engine="pdfplumber")
        bind_company(cls.pdf_doc, [])
        bind_company(cls.docx_doc, [])

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_wrapped_pdf_label_and_value_match_docx_facts(self):
        expected = extract_claims(deepcopy(self.docx_doc))
        actual = extract_claims(deepcopy(self.pdf_doc))
        self.assertEqual(len(expected), 3)
        self.assertEqual(sorted(signature(f) for f in actual), sorted(signature(f) for f in expected))
        self.assertFalse(self.pdf_doc.issues)

    def test_wrapped_fact_has_original_label_and_value_evidence(self):
        doc = deepcopy(self.pdf_doc)
        original = {b.block_id: deepcopy(b) for b in doc.blocks}
        facts = extract_claims(doc)
        self.assert_original_quotes(facts, original)
        profit = next((f for f in facts if f.metric == "net_profit_parent"), None)
        self.assertIsNotNone(profit, "Wrapped 归母净利润 must not become general 净利润")
        quoted = "".join(e.text for e in profit.evidence).replace(" ", "").replace("\n", "")
        self.assertIn("归属于母公司股东的净利润为100万元", quoted)
        self.assertGreaterEqual(len({e.block_id for e in profit.evidence}), 2)

    def test_pdf_multiple_metric_citations_stay_separate(self):
        claims = extract_claims(deepcopy(self.pdf_doc))
        cited = {f.metric: f.attributes.get("citation_page") for f in claims}
        self.assertEqual(cited.get("revenue"), 2)
        self.assertEqual(cited.get("eps_basic"), 3)

    def test_joined_value_locations_slice_original_numeric_text(self):
        doc = deepcopy(self.pdf_doc)
        original = {b.block_id: deepcopy(b) for b in doc.blocks}
        claims = extract_claims(doc)
        expected = {"net_profit_parent": "100万元", "revenue": "500万元", "eps_basic": "1.20元"}
        self.assertEqual(len(claims), 3)
        for fact in claims:
            locations = fact.attributes.get("value_locations", [])
            self.assertTrue(locations)
            raw = "".join(original[loc["block_id"]].text[loc["char_start"]:loc["char_end"]] for loc in locations)
            self.assertEqual("".join(raw.split()), expected[fact.metric])


class PdfPipelineEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="yjcheck-pdf-e2e-")
        self.directory = Path(self.temp.name)
        self.source = self.directory / "source.pdf"
        make_pdf(self.source, ["测试股份2024年度财务报告", "合并利润表", "单位：万元",
                               "项目 2024年度 2023年度", "营业收入 90.00 80.00"])

    def tearDown(self):
        self.temp.cleanup()

    def test_pdf_report_and_source_reach_traceable_numeric_verdict(self):
        for value, status in [("100", "confirmed_error"), ("90", "no_issue")]:
            with self.subTest(value=value):
                report = self.directory / f"report-{value}.pdf"
                make_pdf(report, ["测试股份（600001）2024年研究报告", f"2024年营业收入为{value}万元。"])
                payload, destination = run_check(report, [self.source], self.directory / "out", engine="pdfplumber")
                self.assertEqual(payload["summary"]["claims"], 1)
                self.assertEqual(payload["summary"][status], 1, payload)
                self.assertTrue(payload["summary"]["complete"], payload["input_issues"])
                finding = payload["findings"][0]
                self.assertEqual(finding["claim"]["evidence"][0]["page"], 1)
                self.assertTrue(finding["evidence"][0]["evidence"][0]["bbox"])
                self.assertEqual(finding["calculation"]["sources"][0]["normalized_value"], "900000.00")
                if status == "confirmed_error":
                    self.assertEqual(finding["suggested_value"], "90")
                self.assertTrue(verify_artifacts(destination))
                self.assertEqual(json.loads((destination / "check_result.json").read_text(encoding="utf-8"))["summary"], payload["summary"])

    def test_local_cli_check_and_verify_real_pdf_pair(self):
        report = self.directory / "cli-report.pdf"
        make_pdf(report, ["测试股份（600001）2024年研究报告", "2024年营业收入为100万元。"])
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        command = [sys.executable, str(REPO / "factcheck" / "run.py"), "check", "--report", str(report),
                   "--source", str(self.source), "--out", str(self.directory / "cli-out"), "--engine", "pdfplumber"]
        process = subprocess.run(command, cwd=REPO, env=env, encoding="utf-8", capture_output=True, timeout=60)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        output = json.loads(process.stdout.strip().splitlines()[-1])
        self.assertEqual(output["summary"]["confirmed_error"], 1, output)
        verified = subprocess.run([sys.executable, str(REPO / "factcheck" / "run.py"), "verify", output["output"]],
                                  cwd=REPO, env=env, encoding="utf-8", capture_output=True, timeout=60)
        self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)
        self.assertIn("verified", verified.stdout)


class LocalESampleClaimTests(unittest.TestCase, LocatorAssertions):
    def test_all_four_delivered_docx_have_expected_extraction_coverage(self):
        fixture_root = REPO.parent / "E测试样本最新版"
        self.assertTrue(fixture_root.is_dir(), "E 的本地验收样本目录缺失，不能声称此项通过")
        paths = sorted(fixture_root.rglob("*.docx"))
        self.assertEqual(len(paths), 4)
        expected_counts = {"佛然能源": 6, "兰石重装": 6, "大地海洋": 8, "群兴玩具": 8}
        for path in paths:
            with self.subTest(sample=path.parent.name):
                document = load_document(path, "report", "unused-docx-work")
                bind_company(document, [])
                original = {b.block_id: deepcopy(b) for b in document.blocks}
                claims = extract_claims(document)
                self.assertEqual(len(claims), expected_counts[path.parent.name])
                self.assertFalse(document.issues)
                self.assert_original_quotes(claims, original)


if __name__ == "__main__":
    unittest.main()
