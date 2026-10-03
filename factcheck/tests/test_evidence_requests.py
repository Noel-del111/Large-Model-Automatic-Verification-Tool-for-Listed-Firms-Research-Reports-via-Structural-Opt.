import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend" if (ROOT / "frontend").is_dir() else ROOT.parent / "frontend"
sys.path[:0] = [str(ROOT / "factcheck/src"), str(ROOT / "pdfparse/src"), str(FRONTEND)]
from jsonschema import Draft202012Validator
from yjcheck.models import Block, Document
from yjcheck.pipeline import check_documents
from exports import findings_csv_bytes, report_md, _pdf_lines
from assistant import rule_explanation


class EvidenceRequestTests(unittest.TestCase):
    def report(self, text="2024年营业收入200万元。"):
        return Document("r", "a" * 64, "r-run", "report.docx", "report", "测试公司", "2024FY",
                        [Block("p", text, paragraph=1)])

    def source(self):
        return Document("s", "b" * 64, "s-run", "source.docx", "source", "测试公司", "2024FY", [
            Block("h1", "2024年度合并利润表", paragraph=1), Block("h2", "单位：万元", paragraph=2),
            Block("h3", "项目 2024年度 2023年度", paragraph=3), Block("v1", "营业收入 100 90", paragraph=4)])

    def validate(self, result):
        schema = json.loads((ROOT / "factcheck/schemas/check_result.schema.json").read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(result)

    def test_missing_source_asks_for_actual_metric_and_period(self):
        result = check_documents(self.report(), [])
        self.validate(result)
        finding = result["findings"][0]
        self.assertEqual(finding["decision"], "ask")
        self.assertIsNone(finding["suggested_value"])
        request = finding["evidence_request"][0]
        self.assertEqual((request["doc_role"], request["field"], request["period"]),
                         ("source", "revenue", "2024FY"))

    def test_verified_results_proceed_without_requests(self):
        for value, status in ((100, "no_issue"), (200, "confirmed_error")):
            result = check_documents(self.report(f"2024年营业收入{value}万元。"), [self.source()])
            self.validate(result)
            finding = result["findings"][0]
            self.assertEqual((finding["status"], finding["decision"], finding["evidence_request"]),
                             (status, "proceed", []))

    def test_input_override_requests_the_broken_source_file(self):
        source = self.source()
        source.issues.append("document:hash_mismatch")
        result = check_documents(self.report(), [source])
        self.validate(result)
        for finding in result["findings"]:
            self.assertEqual(finding["decision"], "ask")
            self.assertIsNone(finding["suggested_value"])
            request = finding["evidence_request"][0]
            self.assertEqual((request["doc_role"], request["file"]), ("source", "source.docx"))

    def test_derived_missing_inputs_list_both_periods(self):
        result = check_documents(self.report("2024年营业收入同比增长10%。"), [])
        self.validate(result)
        finding = next(f for f in result["findings"] if f["rule_id"] == "C.DERIVED.001")
        self.assertEqual({(r["field"], r["period"]) for r in finding["evidence_request"]},
                         {("revenue", "2024FY"), ("revenue", "2023FY")})

    def test_no_claims_requests_report_instead_of_source(self):
        result = check_documents(self.report("暂无财务指标。"), [])
        self.validate(result)
        self.assertEqual(result["findings"][0]["evidence_request"][0]["doc_role"], "report")

    def test_exports_and_offline_assistant_include_requests(self):
        result = check_documents(self.report(), [])
        self.assertIn("补充证据清单", findings_csv_bytes(result, {}).decode("utf-8-sig"))
        self.assertIn("2024FY", report_md(result, {}))
        self.assertIn("补充证据", "\n".join(_pdf_lines(result, {})))
        self.assertIn("需要补充", rule_explanation(result["findings"][0]))


if __name__ == "__main__":
    unittest.main()
