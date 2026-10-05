"""Independent tests of input trust, evidence positions, models and output artifacts."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "factcheck" / "src"), str(ROOT / "pdfparse" / "src")]

import pymupdf
from yjcheck import __main__ as cli
from yjcheck.adapters import bind_company, file_hash, from_parse_result, load_document
from yjcheck.claim_extract import extract_claims
from yjcheck.model import ModelConfig, extract_with_model
from yjcheck.models import Block, Document, Evidence, Fact
from yjcheck.pipeline import check_documents, verify_artifacts, write_result
from yjcheck.rules import check_facts
from yjcheck.source_extract import extract_source_facts


def doc(role="report", text="2024年营业收入100万元。"):
    return Document(role, ("a" if role == "report" else "b") * 64, "run_" + role,
                    role + ".docx", role, "测试公司", "2024FY",
                    [Block("p1", text, paragraph=1)])


def candidate(**changes):
    item = dict(block_id="p1", quote="2024年营业收入100万元。", metric="revenue",
                value="100", unit="万元", period="2024FY", basis="reported",
                scope="consolidated")
    item.update(changes)
    return item


def model_response(facts):
    body = json.dumps({"choices": [{"message": {"content": json.dumps({"facts": facts}, ensure_ascii=False)}}]})
    return io.BytesIO(body.encode())


class InputBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pdf = self.root / "original.pdf"
        with pymupdf.open() as document:
            page = document.new_page(width=595, height=842)
            page.insert_text((50, 100), "Original source revenue 100")
            document.save(self.pdf)
        self.payload = {
            "run_id": "parse-run", "doc": {"doc_id": "original", "sha256": file_hash(self.pdf),
                "source_path": str(self.pdf), "total_pages": 1},
            "engine": {"name": "pdfplumber"}, "quality_report": {"status": "ok"},
            "pages": [{"page": 1, "page_size": [595, 842], "status": "ok", "notes": [],
                       "blocks": [{"block_id": "b1", "order": 0, "type": "text",
                                   "text": "Original source revenue 100", "bbox": [50, 80, 300, 110]}]}],
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_valid_pdf_identity_and_location_survive_adapter(self):
        result = from_parse_result(self.payload, "source")
        self.assertEqual(result.issues, [])
        self.assertEqual(result.sha256, file_hash(self.pdf))
        self.assertEqual(result.blocks[0].bbox, [50, 80, 300, 110])
        self.assertEqual(result.blocks[0].page, 1)

    def test_original_hash_missing_file_and_coverage_are_independently_checked(self):
        cases = []
        changed = deepcopy(self.payload)
        changed["doc"]["sha256"] = "0" * 64
        cases.append((changed, "document:hash_mismatch"))
        changed = deepcopy(self.payload)
        changed["doc"]["source_path"] = str(self.root / "missing.pdf")
        cases.append((changed, "document:original_unavailable"))
        changed = deepcopy(self.payload)
        changed["pages"] = []
        cases.append((changed, "document:page_coverage_invalid"))
        changed = deepcopy(self.payload)
        changed["pages"].append(deepcopy(changed["pages"][0]))
        cases.append((changed, "document:page_coverage_invalid"))
        changed = deepcopy(self.payload)
        changed["doc"]["total_pages"] = 99
        cases.append((changed, "document:page_count_mismatch"))
        for payload, issue in cases:
            with self.subTest(issue=issue):
                self.assertIn(issue, from_parse_result(payload, "source").issues)

    def test_bbox_nonfinite_degenerate_missing_and_outside_page_are_rejected(self):
        for box in [None, [1, 2, 1, 4], [1, 2, float("nan"), 4], [-1, 2, 3, 4], [1, 2, 999, 4]]:
            with self.subTest(box=box):
                payload = deepcopy(self.payload)
                payload["pages"][0]["blocks"][0]["bbox"] = box
                result = from_parse_result(payload, "source")
                self.assertEqual(result.blocks[0].status, "fail")
                self.assertTrue(result.issues)

    def test_forged_page_dimensions_cannot_approve_off_page_evidence(self):
        payload = deepcopy(self.payload)
        payload["pages"][0]["page_size"] = [10000, 10000]
        payload["pages"][0]["blocks"][0]["bbox"] = [900, 900, 1000, 1000]
        result = from_parse_result(payload, "source")
        self.assertEqual(result.blocks[0].status, "fail")
        self.assertTrue(result.issues)

    def test_missing_page_size_does_not_disable_source_pdf_bounds(self):
        payload = deepcopy(self.payload)
        payload["pages"][0].pop("page_size")
        payload["pages"][0]["blocks"][0]["bbox"] = [900, 900, 1000, 1000]
        result = from_parse_result(payload, "source")
        self.assertEqual(result.blocks[0].status, "fail")

    def test_page_quality_and_duplicate_block_locators_remain_untrusted(self):
        for status, notes in [("fail", []), ("warn", ["ocr_used:blocks=2"]), ("unknown", [])]:
            payload = deepcopy(self.payload)
            payload["pages"][0].update(status=status, notes=notes)
            with self.subTest(status=status):
                self.assertNotEqual(from_parse_result(payload, "source").blocks[0].status, "ok")
        payload = deepcopy(self.payload)
        payload["pages"][0]["blocks"].append(deepcopy(payload["pages"][0]["blocks"][0]))
        result = from_parse_result(payload, "source")
        self.assertTrue(any("invalid_locator" in issue for issue in result.issues))
        self.assertTrue(all(b.status == "fail" for b in result.blocks))

    def test_cropped_pdf_uses_each_engines_real_coordinate_system(self):
        path = self.root / "cropped.pdf"
        with pymupdf.open() as document:
            page = document.new_page(width=595, height=842)
            page.insert_text((50, 100), "CropBox source revenue 100")
            page.set_cropbox(pymupdf.Rect(20, 30, 575, 800))
            document.save(path)
        for engine, size in [("pdfplumber", [595, 842]), ("pymupdf", [555, 770])]:
            with self.subTest(engine=engine):
                result = load_document(path, "source", self.root / engine, engine=engine)
                self.assertEqual(result.issues, [])
                self.assertEqual(result.blocks[0].page_size, size)

    def test_relative_original_path_in_b_json_is_resolved_from_json_directory(self):
        payload = deepcopy(self.payload)
        payload["doc"]["source_path"] = self.pdf.name
        target = self.root / "parse_result.json"
        target.write_text(json.dumps(payload), encoding="utf-8")
        result = load_document(target, "source", self.root / "work")
        self.assertFalse(result.issues)
        self.assertEqual(Path(result.path), self.pdf)

    def test_external_parse_json_cannot_invent_original_financial_text(self):
        payload = deepcopy(self.payload)
        payload["pages"][0]["blocks"][0]["text"] = "2024年营业收入900万元。"
        target = self.root / "forged_result.json"
        target.write_text(json.dumps(payload), encoding="utf-8")
        result = load_document(target, "source", self.root / "work")
        # Either independently recover original contents or reject this imported
        # content at document level. The original-file hash does not authenticate
        # arbitrary text added to an external JSON payload.
        self.assertTrue("900万元" not in result.text or
                        any(issue.startswith("document:") for issue in result.issues))

    def test_same_named_pdfs_have_different_content_identities(self):
        other = self.root / "second" / self.pdf.name
        other.parent.mkdir()
        with pymupdf.open() as document:
            page = document.new_page()
            page.insert_text((50, 100), "Different source 200")
            document.save(other)
        payload = deepcopy(self.payload)
        payload["doc"].update(source_path=str(other), sha256=file_hash(other))
        self.assertNotEqual(from_parse_result(self.payload, "source").doc_id,
                            from_parse_result(payload, "source").doc_id)

    def test_competitor_mention_cannot_bind_another_issuers_source(self):
        report = doc(text="测试公司（300001）2024年度研报")
        source = doc("source", "其他公司股份有限公司2024年年度报告")
        source.blocks.append(Block("p2", "本公司的主要竞争对手包括测试公司。", paragraph=2))
        identity = bind_company(report, [source])
        self.assertEqual(identity, "测试公司")
        self.assertEqual(source.company, "")
        self.assertIn("document:company_unverified", source.issues)

    def test_filename_alone_cannot_verify_a_sources_issuer(self):
        report = doc(text="测试公司（300001）2024年度研报")
        source = doc("source", "其他公司股份有限公司2024年年度报告")
        source.path = str(self.root / "测试公司_错误附件.pdf")
        bind_company(report, [source])
        self.assertEqual(source.company, "")
        self.assertIn("document:company_unverified", source.issues)

    def test_docx_preserves_tabs_and_breaks_for_character_evidence(self):
        target = self.root / "report.docx"
        xml = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
               '<w:body><w:p><w:r><w:t>测试公司2024年度研报</w:t></w:r></w:p><w:p/>'
               '<w:p><w:r><w:t>营业收入</w:t></w:r><w:r><w:tab/><w:t>100万元。</w:t>'
               '<w:br/><w:t>净利润20万元。</w:t></w:r></w:p></w:body></w:document>')
        with ZipFile(target, "w") as package:
            package.writestr("word/document.xml", xml)
        result = load_document(target, "report", self.root / "work")
        result.company = "测试公司"
        self.assertEqual(result.blocks[1].paragraph, 3)
        self.assertEqual(result.blocks[1].text, "营业收入\t100万元。\n净利润20万元。")
        claims = extract_claims(result)
        self.assertEqual(len(claims), 2)
        for fact in claims:
            evidence = fact.evidence[0]
            block = next(b for b in result.blocks if b.block_id == evidence.block_id)
            self.assertIsNone(evidence.page)
            self.assertIsNone(evidence.bbox)
            self.assertEqual(block.text[evidence.char_start:evidence.char_end], evidence.text)
            self.assertIn(fact.value, block.text[fact.attributes["value_start"]:fact.attributes["value_end"]])


class ModelBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.config = ModelConfig("http://127.0.0.1:9/v1", "unit-test-model", "DO_NOT_LOG_THIS_KEY")

    def test_model_quote_block_metric_value_unit_and_dimension_forgery_are_rejected(self):
        invalid = [candidate(block_id="missing"), candidate(quote="不存在的原文100万元"),
                   candidate(metric="unknown_metric"), candidate(value="200"),
                   candidate(unit="亿元"), candidate(basis="invented"), candidate(scope="invented")]
        with patch("yjcheck.model.urllib.request.urlopen", return_value=model_response(invalid)):
            facts, traces = extract_with_model(doc(), self.config)
        self.assertEqual(facts, [])
        self.assertEqual(len(traces[0]["rejected"]), len(invalid))
        self.assertNotIn(self.config.api_key, json.dumps(traces))

    def test_model_cannot_assign_another_metrics_value_from_same_quote(self):
        text = "2024年营业收入100万元，净利润20万元。"
        with patch("yjcheck.model.urllib.request.urlopen", return_value=model_response([
            candidate(quote=text, metric="revenue", value="20")])):
            facts, traces = extract_with_model(doc(text=text), self.config)
        self.assertFalse(facts)
        self.assertTrue(traces[0]["rejected"])

    def test_model_candidates_never_produce_automatic_financial_verdicts(self):
        # Unsupported context must remain a review candidate even if its value
        # happens to equal a source from the invented year and scope.
        with patch("yjcheck.model.urllib.request.urlopen", return_value=model_response([
            candidate(period="2023FY", scope="parent", basis="after")])):
            facts, traces = extract_with_model(doc(), self.config)
        if facts:
            source = deepcopy(facts[0])
            source.warnings = []
            source.evidence[0].doc_id = "source"
            source.evidence[0].sha256 = "b" * 64
            findings = check_facts(facts, [source])
            self.assertTrue(all(f.status == "needs_review" for f in findings))
        else:
            self.assertTrue(traces[0]["rejected"])

    def test_model_exact_quote_has_reversible_character_location(self):
        value = doc(text="开头。2024年营业收入100万元。结尾。")
        with patch("yjcheck.model.urllib.request.urlopen", return_value=model_response([candidate()])):
            facts, traces = extract_with_model(value, self.config)
        self.assertEqual(len(facts), 1)
        e = facts[0].evidence[0]
        self.assertEqual(value.blocks[0].text[e.char_start:e.char_end], e.text)
        self.assertEqual(traces[0]["accepted"], 1)

    def test_failed_model_endpoint_is_reported_without_secrets_or_fabricated_facts(self):
        with patch("yjcheck.model.urllib.request.urlopen", side_effect=OSError("secret server body")):
            facts, traces = extract_with_model(doc(), self.config)
        self.assertEqual(facts, [])
        self.assertEqual(traces[0]["status"], "error")
        self.assertNotIn("secret server body", json.dumps(traces))
        self.assertNotIn(self.config.api_key, json.dumps(traces))

    def test_invalid_response_is_logged_as_failure(self):
        for body in (b"not JSON", b'{}', b'x' * 2_000_001):
            with self.subTest(size=len(body)), patch("yjcheck.model.urllib.request.urlopen", return_value=io.BytesIO(body)):
                facts, traces = extract_with_model(doc(), self.config)
                self.assertFalse(facts)
                self.assertEqual(traces[0]["status"], "error")

    def test_untrusted_model_url_is_rejected_before_network(self):
        with patch("yjcheck.model.urllib.request.urlopen") as network:
            for url in ("file:///tmp/key", "http://remote.example/v1", "https://user:secret@example.test/v1"):
                with self.subTest(url=url), self.assertRaises(ValueError):
                    extract_with_model(doc(), ModelConfig(url, "test"))
            network.assert_not_called()


class PipelineArtifactBoundaryTests(unittest.TestCase):
    def test_no_claims_or_no_sources_never_mean_a_complete_pass(self):
        for report in (doc(text="无财务数值的介绍。"), doc()):
            result = check_documents(report, [])
            self.assertFalse(result["summary"]["complete"])
            self.assertGreater(result["summary"]["needs_review"], 0)
            self.assertEqual(result["summary"]["no_issue"], 0)

    def test_document_integrity_issues_block_otherwise_matching_facts(self):
        report = doc()
        source = doc("source")
        source.issues.append("document:hash_mismatch")
        facts = extract_claims(source)
        with patch("yjcheck.pipeline.extract_source_facts", return_value=facts):
            result = check_documents(report, [source])
        self.assertFalse(result["summary"]["complete"])
        self.assertTrue(all(f["status"] == "needs_review" for f in result["findings"]))
        self.assertTrue(all(f["suggested_value"] is None for f in result["findings"]))

    def test_model_failure_keeps_summary_incomplete(self):
        source = doc("source")
        with patch("yjcheck.pipeline.extract_source_facts", return_value=extract_claims(source)), \
             patch("yjcheck.model.extract_with_model", return_value=([], [{"status": "error", "error": "TimeoutError"}])):
            result = check_documents(doc(), [source], ModelConfig("http://127.0.0.1", "mock"))
        self.assertFalse(result["summary"]["complete"])
        self.assertEqual(result["model_traces"][0]["status"], "error")

    def test_artifact_hashes_detect_changes_missing_files_and_unsafe_manifest_paths(self):
        result = check_documents(doc(), [])
        with tempfile.TemporaryDirectory() as tmp:
            target = write_result(result, Path(tmp))
            self.assertTrue(verify_artifacts(target))
            report = target / "report.md"
            contents = report.read_bytes()
            report.write_bytes(contents + b"tampered")
            self.assertFalse(verify_artifacts(target))
            report.write_bytes(contents)
            report.unlink()
            self.assertFalse(verify_artifacts(target))
            report.write_bytes(contents)
            manifest = json.loads((target / "manifest.json").read_text())
            manifest["files"]["../elsewhere"] = "a" * 64
            (target / "manifest.json").write_text(json.dumps(manifest))
            self.assertFalse(verify_artifacts(target))

    def test_manifest_run_identity_must_match_the_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = write_result(check_documents(doc(), []), Path(tmp))
            manifest_path = target / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["run_id"] = "another-run"
            manifest_path.write_text(json.dumps(manifest))
            self.assertFalse(verify_artifacts(target))

    def test_fact_identity_distinguishes_run_file_hash_and_currency(self):
        first = extract_claims(doc())[0]
        for field, value in (("run_id", "another"), ("sha256", "c" * 64)):
            evidence = deepcopy(first.evidence)
            setattr(evidence[0], field, value)
            other = Fact(first.metric, first.value, first.unit, first.period, first.company,
                         basis=first.basis, evidence=evidence)
            self.assertNotEqual(first.fact_id, other.fact_id)
        other = Fact(first.metric, first.value, first.unit, first.period, first.company,
                     basis=first.basis, currency="USD", evidence=deepcopy(first.evidence))
        self.assertNotEqual(first.fact_id, other.fact_id)

    def test_repeated_pdf_sentence_on_other_page_is_not_dropped(self):
        report = doc()
        report.blocks = [Block("line0", "2024年营业收入100万元。", page=n,
                               bbox=[20, 80, 550, 100]) for n in (1, 2)]
        claims = extract_claims(report)
        self.assertEqual(len(claims), 2)
        self.assertEqual({c.evidence[0].page for c in claims}, {1, 2})
        self.assertNotEqual(claims[0].fact_id, claims[1].fact_id)

    def test_citation_must_locate_value_row_not_only_context_heading(self):
        claim = extract_claims(doc())[0]
        source = deepcopy(claim)
        source.evidence = [
            Evidence(doc_id="source", run_id="source-run", sha256="b" * 64,
                     block_id="p2_row", page=2, bbox=[20, 100, 550, 120], text="营业收入100万元"),
            Evidence(doc_id="source", run_id="source-run", sha256="b" * 64,
                     block_id="p1_heading", page=1, bbox=[20, 100, 550, 120], text="2024年度合并利润表 单位：万元"),
        ]
        claim.attributes["citation_page"] = 1
        finding = check_facts([claim], [source])[0]
        self.assertEqual((finding.status, finding.error_type), ("confirmed_error", "citation"))
        claim.attributes["citation_page"] = 2
        self.assertEqual(check_facts([claim], [source])[0].status, "no_issue")

    def test_failed_title_cannot_supply_unrecorded_default_period(self):
        source = doc("source")
        source.period = ""
        lines = ["2024年度财务报表", "合并利润表", "单位：万元", "项目 本期金额 上期金额", "营业收入 100 80"]
        source.blocks = [Block(f"row{n}", text, page=1, bbox=[20, 80+n*20, 550, 95+n*20],
                               status="fail" if n == 0 else "ok") for n, text in enumerate(lines)]
        claim = extract_claims(doc())[0]
        findings = check_facts([claim], extract_source_facts(source))
        self.assertEqual(findings[0].status, "needs_review")


class CliBoundaryTests(unittest.TestCase):
    def test_missing_input_and_failed_verify_have_documented_codes(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["check", "--report", str(Path(tmp) / "missing.pdf"),
                                       "--source", str(Path(tmp) / "source.pdf"), "--out", tmp]), 2)
            self.assertEqual(cli.main(["verify", tmp]), 1)

    def test_corrupt_docx_returns_input_error_without_uncaught_exception(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            broken = Path(tmp) / "report.docx"
            broken.write_bytes(b"not a zip")
            self.assertEqual(cli.main(["check", "--report", str(broken), "--source", str(broken), "--out", tmp]), 2)

    def test_wrong_shape_json_returns_input_error_without_uncaught_exception(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            broken = Path(tmp) / "parse.json"
            broken.write_text("[]", encoding="utf-8")
            self.assertEqual(cli.main(["check", "--report", str(broken), "--source", str(broken), "--out", tmp]), 2)


if __name__ == "__main__":
    unittest.main()
