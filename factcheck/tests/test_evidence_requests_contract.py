"""Public paired-result compatibility and evidence-request export regressions."""
from copy import deepcopy
import csv
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "factcheck/src"), str(ROOT / "pdfparse/src")]

from jsonschema import Draft202012Validator
from yjcheck.models import Block, Document
from yjcheck.pipeline import check_documents, verify_artifacts, write_result


REQUEST_KEYS = {"doc_role", "field", "period", "company", "basis", "scope", "file", "request_type", "reason"}
COUNT_KEYS = ("evidence_requests", "evidence_request_items")


def report(text):
    return Document("report", "a" * 64, "report-run", "report.docx", "report", "测试公司", "2024FY",
                    [Block("paragraph", text, paragraph=1)])


def source():
    return Document("source", "b" * 64, "source-run", "source.docx", "source", "测试公司", "2024FY", [
        Block("title", "2024年度合并利润表", paragraph=1),
        Block("unit", "单位：万元", paragraph=2),
        Block("columns", "项目 2024年度 2023年度", paragraph=3),
        Block("revenue", "营业收入 100 90", paragraph=4),
    ])


def legacy_without_requests(result):
    result = deepcopy(result)
    result["schema_version"] = "1.0.0"
    for finding in result["findings"]:
        finding.pop("decision", None)
        finding.pop("evidence_request", None)
    for key in COUNT_KEYS:
        result["summary"].pop(key, None)
    return result


class EvidenceRequestsContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        schema = json.loads((ROOT / "factcheck/schemas/check_result.schema.json").read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        cls.validator = Draft202012Validator(schema)
        cls.results = {
            "no_issue": check_documents(report("2024年营业收入100万元。"), [source()]),
            "confirmed_error": check_documents(report("2024年营业收入200万元。"), [source()]),
            "needs_review": check_documents(report("2024年货币资金300万元。"), [source()]),
        }
        cls.two_periods = check_documents(report("2024年营业收入同比增长20%。"), [])

    def assert_invalid(self, payload):
        self.assertTrue(list(self.validator.iter_errors(payload)), "Invalid public contract was accepted")

    def test_real_three_state_results_publish_versioned_decisions(self):
        for status, result in self.results.items():
            with self.subTest(status=status):
                self.assertEqual(result["schema_version"], "1.1.0")
                self.validator.validate(result)
                self.assertEqual(len(result["findings"]), 1)
                finding = result["findings"][0]
                self.assertEqual(finding["status"], status)
                expected = "ask" if status == "needs_review" else "proceed"
                self.assertEqual(finding["decision"], expected)
                self.assertEqual(bool(finding["evidence_request"]), status == "needs_review")
                for item in finding["evidence_request"]:
                    self.assertEqual(set(item), REQUEST_KEYS)

    def test_new_version_requires_both_finding_fields_and_summary_counts(self):
        for fields in (("decision",), ("evidence_request",), ("decision", "evidence_request")):
            changed = deepcopy(self.results["needs_review"])
            for key in fields:
                del changed["findings"][0][key]
            with self.subTest(finding_missing=fields):
                self.assert_invalid(changed)
        for fields in ((COUNT_KEYS[0],), (COUNT_KEYS[1],), COUNT_KEYS):
            changed = deepcopy(self.results["needs_review"])
            for key in fields:
                del changed["summary"][key]
            with self.subTest(summary_missing=fields):
                self.assert_invalid(changed)

    def test_legacy_all_missing_remains_valid_without_implied_proceed(self):
        for status, result in self.results.items():
            legacy = legacy_without_requests(result)
            with self.subTest(status=status):
                self.validator.validate(legacy)
                self.assertEqual(legacy["findings"][0]["status"], status)
                self.assertNotIn("decision", legacy["findings"][0])
                self.assertNotIn("evidence_request", legacy["findings"][0])

    def test_legacy_existing_paired_fields_are_valid_but_half_groups_are_rejected(self):
        legacy = deepcopy(self.results["needs_review"])
        legacy["schema_version"] = "1.0.0"
        self.validator.validate(legacy)
        for key in ("decision", "evidence_request"):
            changed = deepcopy(legacy)
            del changed["findings"][0][key]
            with self.subTest(finding_missing=key):
                self.assert_invalid(changed)

    def test_historical_1_0_with_only_finding_request_count_remains_valid(self):
        # The 7035ac2 producer already emitted finding decision/request pairs,
        # but its summary did not yet have the material-item count.
        legacy = deepcopy(self.two_periods)
        legacy["schema_version"] = "1.0.0"
        del legacy["summary"]["evidence_request_items"]
        self.validator.validate(legacy)
        self.assertEqual(legacy["summary"]["evidence_requests"], 1)
        self.assertEqual(len(legacy["findings"][0]["evidence_request"]), 2)
        self.assertNotIn("evidence_request_items", legacy["summary"])
        new_result = deepcopy(legacy)
        new_result["schema_version"] = "1.1.0"
        self.assert_invalid(new_result)

    def test_decision_and_request_cardinality_follow_status_in_both_versions(self):
        request = deepcopy(self.results["needs_review"]["findings"][0]["evidence_request"])
        invalid = (
            ("needs_review", "proceed", []),
            ("needs_review", "ask", []),
            ("needs_review", "continue", request),
            ("confirmed_error", "ask", request),
            ("confirmed_error", "proceed", request),
            ("no_issue", "ask", request),
            ("no_issue", "proceed", request),
        )
        for version in ("1.0.0", "1.1.0"):
            for status, decision, requests in invalid:
                changed = deepcopy(self.results[status])
                changed["schema_version"] = version
                changed["findings"][0].update(decision=decision, evidence_request=requests)
                with self.subTest(version=version, status=status, decision=decision, count=len(requests)):
                    self.assert_invalid(changed)

    def test_request_required_keys_types_and_enumerations_are_enforced(self):
        for key in sorted(REQUEST_KEYS):
            changed = deepcopy(self.results["needs_review"])
            del changed["findings"][0]["evidence_request"][0][key]
            with self.subTest(missing_key=key):
                self.assert_invalid(changed)
        invalid = {"doc_role": "external", "field": "", "reason": "", "period": 2024,
                   "company": [], "file": 3, "basis": "invented", "scope": "division",
                   "request_type": "send_message"}
        for key, value in invalid.items():
            changed = deepcopy(self.results["needs_review"])
            changed["findings"][0]["evidence_request"][0][key] = value
            with self.subTest(invalid_key=key):
                self.assert_invalid(changed)
        for invalid_requests in (None, {}, "source required", [None]):
            changed = deepcopy(self.results["needs_review"])
            changed["findings"][0]["evidence_request"] = invalid_requests
            with self.subTest(invalid_requests=invalid_requests):
                self.assert_invalid(changed)

    def test_unknown_dimensions_remain_nullable_and_request_types_are_explicit(self):
        for kind in ("provide_source", "repair_input", "clarify_context", "resolve_conflict"):
            changed = deepcopy(self.results["needs_review"])
            item = changed["findings"][0]["evidence_request"][0]
            item.update({key: None for key in ("period", "company", "file", "basis", "scope")})
            item["request_type"] = kind
            with self.subTest(request_type=kind):
                self.validator.validate(changed)

    def test_summary_counts_are_nonnegative_integers_in_both_versions(self):
        for version in ("1.0.0", "1.1.0"):
            for key in COUNT_KEYS:
                for value in (-1, 0.5, "1", True, None):
                    changed = deepcopy(self.results["needs_review"])
                    changed["schema_version"] = version
                    changed["summary"][key] = value
                    with self.subTest(version=version, key=key, value=value):
                        self.assert_invalid(changed)

    def test_two_period_requests_count_one_finding_and_two_material_items(self):
        result = self.two_periods
        self.validator.validate(result)
        self.assertEqual(len(result["findings"]), 1)
        finding = result["findings"][0]
        self.assertEqual((finding["rule_id"], finding["decision"]), ("C.DERIVED.001", "ask"))
        self.assertEqual([(v["field"], v["period"]) for v in finding["evidence_request"]],
                         [("revenue", "2024FY"), ("revenue", "2023FY")])
        self.assertEqual(result["summary"]["evidence_requests"], 1)
        self.assertEqual(result["summary"]["evidence_request_items"], 2)
        two_claims = check_documents(report("2024年营业收入同比增长20%，2024年净利润同比增长10%。"), [])
        self.validator.validate(two_claims)
        self.assertEqual(two_claims["summary"]["evidence_requests"], 2)
        self.assertEqual(two_claims["summary"]["evidence_request_items"], 4)

    def test_input_quality_override_precedes_decision_attachment(self):
        uncertain = source()
        uncertain.issues = ["document:incomplete"]
        result = check_documents(report("2024年营业收入100万元。"), [uncertain])
        self.validator.validate(result)
        finding = result["findings"][0]
        self.assertEqual((finding["status"], finding["decision"]), ("needs_review", "ask"))
        self.assertTrue(any(r["request_type"] == "repair_input" and r["doc_role"] == "source"
                            for r in finding["evidence_request"]))

    def test_json_csv_markdown_export_requests_and_manifest_covers_their_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            target = write_result(deepcopy(self.two_periods), Path(directory))
            persisted = json.loads((target / "check_result.json").read_text(encoding="utf-8"))
            self.validator.validate(persisted)
            self.assertEqual(persisted["findings"][0]["evidence_request"],
                             self.two_periods["findings"][0]["evidence_request"])
            with (target / "findings.csv").open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 1)
            for period in ("2024FY", "2023FY"):
                self.assertIn(period, rows[0]["补充证据清单"])
                self.assertIn(period, (target / "report.md").read_text(encoding="utf-8"))
            manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["run_id"], persisted["run_id"])
            self.assertEqual(set(manifest["files"]), {"check_result.json", "findings.csv", "report.md"})
            for name, digest in manifest["files"].items():
                self.assertEqual(hashlib.sha256((target / name).read_bytes()).hexdigest(), digest)
            self.assertTrue(verify_artifacts(target))
            with (target / "report.md").open("a", encoding="utf-8") as stream:
                stream.write("\n篡改补证说明\n")
            self.assertFalse(verify_artifacts(target))

    def test_legacy_export_preserves_absence_and_leaves_request_column_blank(self):
        legacy = legacy_without_requests(self.results["needs_review"])
        with tempfile.TemporaryDirectory() as directory:
            target = write_result(legacy, Path(directory))
            persisted = json.loads((target / "check_result.json").read_text(encoding="utf-8"))
            self.validator.validate(persisted)
            self.assertNotIn("decision", persisted["findings"][0])
            self.assertNotIn("evidence_request", persisted["findings"][0])
            self.assertNotIn("evidence_requests", persisted["summary"])
            with (target / "findings.csv").open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(rows[0]["补充证据清单"], "")
            self.assertEqual(rows[0]["状态"], "待人工确认")
            self.assertNotIn("proceed", (target / "report.md").read_text(encoding="utf-8"))
            self.assertTrue(verify_artifacts(target))

    def test_untrusted_formula_prefixes_and_request_pipes_do_not_break_exports(self):
        result = deepcopy(self.two_periods)
        finding = result["findings"][0]
        finding["claim"]["text"] = "=1+1|研报原句\n下一行"
        finding["suggestion"] = "+1+1"
        finding["message"] = "-1+1"
        finding["rule_id"] = "@untrusted"
        finding["evidence_request"][0].update(field="revenue|net_profit", reason="核对甲|乙\n换行说明",
                                               file="财报|公告.docx", company="甲|乙公司", basis="after",
                                               scope="parent", request_type="resolve_conflict")
        self.validator.validate(result)
        with tempfile.TemporaryDirectory() as directory:
            target = write_result(result, Path(directory))
            with (target / "findings.csv").open(encoding="utf-8-sig", newline="") as stream:
                row = next(csv.DictReader(stream))
            for column, prefix in (("研报原文", "="), ("修改建议", "+"), ("依据说明", "-"), ("规则", "@")):
                self.assertTrue(row[column].startswith("'" + prefix), column)
            self.assertIn("核对甲|乙\n换行说明", row["补充证据清单"])
            markdown = (target / "report.md").read_text(encoding="utf-8")
            self.assertIn("revenue\\|net_profit", markdown)
            self.assertIn("核对甲\\|乙 换行说明", markdown)
            self.assertIn("财报\\|公告.docx", markdown)
            self.assertIn("甲|乙公司", row["补充证据清单"])
            self.assertIn("甲\\|乙公司", markdown)
            for dimension in ("调整后", "母公司口径", "澄清冲突"):
                self.assertIn(dimension, row["补充证据清单"])
                self.assertIn(dimension, markdown)
            self.assertNotIn("核对甲|乙\n换行说明", markdown)
            persisted = json.loads((target / "check_result.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted["findings"][0]["claim"]["text"], finding["claim"]["text"])
            self.assertTrue(verify_artifacts(target))


if __name__ == "__main__":
    unittest.main()
