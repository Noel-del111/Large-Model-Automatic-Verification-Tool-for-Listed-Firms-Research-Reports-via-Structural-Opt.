from copy import deepcopy
import csv
import io
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "frontend"))

from exports import (evidence_request_contract, finding_request_view, format_evidence_requests, findings_csv_bytes,
                     full_json_bytes, report_md, report_pdf_bytes)

FIXTURES = ROOT / "factcheck" / "examples" / "r09"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class R09ExportTests(unittest.TestCase):
    def test_two_periods_are_consistent_in_every_export(self):
        result = fixture("04_ask_two_periods.json")
        original = deepcopy(result)
        contract = evidence_request_contract(result)
        self.assertEqual((contract["finding_count"], contract["item_count"]), (1, 2))
        csv_text = findings_csv_bytes(result, {}).decode("utf-8-sig")
        md = report_md(result, {})
        for text in (csv_text, md):
            self.assertIn("2024FY", text)
            self.assertIn("2023FY", text)
            self.assertIn("补充来源材料", text)
        merged = json.loads(full_json_bytes(result, {}))
        self.assertEqual(merged["findings"][0]["evidence_request"], original["findings"][0]["evidence_request"])
        self.assertEqual(result, original)

    def test_no_request_is_explicit_but_not_a_correctness_claim(self):
        for name in ("01_no_issue.json", "02_confirmed_error.json"):
            result = fixture(name)
            view = finding_request_view(result, result["findings"][0])
            self.assertEqual(view["label"], "本次核查未提出补证请求")
            self.assertNotIn("全文正确", report_md(result, {}))

    def test_legacy_missing_and_derived_counts_are_distinct(self):
        missing = fixture("05_legacy_without_requests.json")
        self.assertEqual(evidence_request_contract(missing)["status"], "legacy_missing")
        self.assertIn("旧版结果未提供补证信息", report_md(missing, {}))
        self.assertNotIn("补证/澄清请求 0 条", report_md(missing, {}))
        derived = fixture("06_legacy_request_count_only.json")
        contract = evidence_request_contract(derived)
        self.assertEqual(contract["item_count"], 2)
        self.assertEqual(contract["item_count_source"], "derived_from_complete_lists")
        merged = json.loads(full_json_bytes(derived, {}))
        self.assertNotIn("evidence_request_items", merged["summary"])

    def test_invalid_contracts_are_reported_not_repaired(self):
        base = fixture("03_needs_review.json")
        mutations = []
        for key in ("decision", "evidence_request"):
            item = deepcopy(base)
            item["findings"][0].pop(key)
            mutations.append(item)
        item = deepcopy(base); item["findings"][0]["decision"] = "proceed"; mutations.append(item)
        item = deepcopy(base); item["findings"][0]["evidence_request"] = []; mutations.append(item)
        item = deepcopy(base); item["summary"]["evidence_request_items"] = 99; mutations.append(item)
        item = deepcopy(base); item["findings"][0]["evidence_request"][0]["request_type"] = "download_now"; mutations.append(item)
        item = deepcopy(base); item["findings"][0]["evidence_request"][0].pop("reason"); mutations.append(item)
        item = deepcopy(base); item["summary"]["evidence_requests"] = True; mutations.append(item)
        item = deepcopy(base); item["findings"][0]["decision"] = []; mutations.append(item)
        item = deepcopy(base); item["findings"][0]["evidence_request"][0]["request_type"] = []; mutations.append(item)
        item = deepcopy(base); item["findings"][0]["evidence_request"][0]["doc_role"] = []; mutations.append(item)
        item = deepcopy(base); item["findings"][0]["evidence_request"][0]["basis"] = []; mutations.append(item)
        for result in mutations:
            original = deepcopy(result)
            with self.subTest(result=result):
                self.assertEqual(evidence_request_contract(result)["status"], "invalid")
                self.assertIn("不符合契约", report_md(result, {}))
                view = finding_request_view(result, result["findings"][0])
                if result["findings"][0].get("evidence_request"):
                    self.assertTrue(view["items"], "契约异常时仍须保留原始请求供核查")
                full_json_bytes(result, {})
                self.assertEqual(result, original)

    def test_review_detail_retains_all_nine_request_fields(self):
        result = fixture("04_ask_two_periods.json")
        view = finding_request_view(result, result["findings"][0])
        text = format_evidence_requests(view["items"])
        for label in ("材料=", "字段=", "期间=", "公司=", "调整口径=", "合并范围=",
                      "文件线索=", "请求类型=", "原因="):
            self.assertIn(label, text)
        self.assertIn("2024FY", text)
        self.assertIn("2023FY", text)

    def test_special_text_is_safe_and_pdf_is_complete(self):
        result = fixture("03_needs_review.json")
        request = result["findings"][0]["evidence_request"][0]
        request["reason"] = "=危险公式|长原因\n" + "补充核对内容" * 500
        request["file"] = "C:/不存在/来源|文件.pdf"
        csv_text = findings_csv_bytes(result, {}).decode("utf-8-sig")
        rows = list(csv.reader(io.StringIO(csv_text)))
        self.assertEqual(len(rows[0]), len(rows[1]))
        self.assertFalse(rows[1][13].startswith(("=", "+", "-", "@")))
        self.assertIn("原因==危险公式", rows[1][13])
        md = report_md(result, {})
        self.assertIn("\\|", md)
        pdf = report_pdf_bytes(result, {})
        if pdf is None:
            self.skipTest("PyMuPDF is unavailable")
        self.assertTrue(pdf.startswith(b"%PDF"))
        from pypdf import PdfReader
        document = PdfReader(io.BytesIO(pdf))
        extracted = "".join(page.extract_text() or "" for page in document.pages)
        self.assertGreater(len(document.pages), 1)
        self.assertIn("不存在/来源|文件.pdf", extracted.replace("\n", ""))
        self.assertGreater(extracted.count("补充核对内容"), 100)

    def test_text_review_contract_is_not_misclassified(self):
        contract = evidence_request_contract({"schema_version": "text-review/1.0", "findings": []})
        self.assertEqual(contract["status"], "not_applicable")


if __name__ == "__main__":
    unittest.main()
