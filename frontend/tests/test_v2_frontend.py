from pathlib import Path
import sys
import tempfile
import unittest
import hashlib
import json
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "frontend"), str(ROOT / "factcheck/src"), str(ROOT / "pdfparse/src")]
from review_store import ReviewStore


class ReviewTests(unittest.TestCase):
    def test_pair_summary_includes_rejected_text_hint_without_changing_emitted_counts(self):
        from yjcheck.models import Block, Document
        from yjcheck.pipeline import check_documents
        report = Document("a", "sha", "run", "report.txt", "report", blocks=[Block("b", "普通文字")])
        result = {"document_id": "a", "errors": [], "coverage": {}, "rejected_candidates": [
            {"candidate": {"error_type": "数值缺失", "spans": [{"text": "不存在"}], "reason": "待复核"}, "reason": "absent"}]}
        with patch("yjcheck.text_review.detect_text", return_value=result):
            summary = check_documents(report, [])["summary"]
        self.assertEqual(summary["text_candidates"], 0)
        self.assertEqual(summary["text_needs_review"], 0)
        self.assertEqual(summary["text_rejected_hints"], 1)
        self.assertEqual(summary["text_review_total_hints"], 1)

    def test_timing_and_text_review_preserve_original(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "text_review.json"
            source.write_text('{"errors": []}', encoding="utf-8")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            store = ReviewStore(directory)
            store.set("text:one", "dismissed", "核验", "reviewer", duration_seconds=3.5)
            store.set("text:one", "confirmed", duration_seconds=2)
            item = store.get("text:one")
            self.assertEqual(item["total_duration_seconds"], 5.5)
            self.assertEqual(item["history"][-1]["duration_seconds"], 3.5)
            self.assertEqual(store.stats({"other"})["total"], 0)
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), digest)
            with self.assertRaises(ValueError):
                store.set("bad", "confirmed", duration_seconds=float("nan"))

    def test_timing_report_aggregates_per_reviewer_and_flags_unmeasured(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "text_review.json").write_text('{"errors": []}', encoding="utf-8")
            store = ReviewStore(directory)
            store.set("text:one", "dismissed", "太模糊", "甲", duration_seconds=6)
            store.set("text:one", "confirmed", "", "甲", duration_seconds=4)
            store.set("text:two", "confirmed", "", "乙", duration_seconds=2)
            store.set("text:three", "confirmed", "", "乙")  # 未计时
            report = store.timing_report()
            self.assertEqual(report["total_records"], 3)
            self.assertEqual(report["measured_items"], 2)
            self.assertEqual(report["unmeasured_items"], 1)
            self.assertEqual(report["total_seconds"], 12.0)
            self.assertEqual(report["by_reviewer"]["甲"]["seconds"], 10.0)
            self.assertEqual(report["by_reviewer"]["甲"]["items"], 1)
            self.assertEqual(report["by_reviewer"]["乙"]["items"], 1)
            self.assertEqual(report["by_reviewer"]["乙"]["seconds"], 2.0)
            self.assertEqual(report["by_status"]["confirmed"]["count"], 3)
            self.assertEqual(report["by_status"]["dismissed"]["count"], 0)
            self.assertTrue(report["timing_note"])

    def test_export_rows_are_flat_and_stable_for_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "text_review.json").write_text('{"errors": []}', encoding="utf-8")
            store = ReviewStore(directory)
            store.set("text:one", "confirmed", "依据", "甲", duration_seconds=1.25)
            rows = store.export_rows()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["finding_id"], "text:one")
            self.assertEqual(rows[0]["total_duration_seconds"], 1.25)
            self.assertEqual(rows[0]["timing_method"], "explicit_start_to_save_wall_clock")
            keys = list(rows[0].keys())
            self.assertEqual(keys, ["finding_id", "status", "reviewer", "note", "duration_seconds",
                                    "total_duration_seconds", "timing_method", "updated_at"])


class PageTests(unittest.TestCase):
    def test_rejected_hint_is_reviewable_and_raw_quote_is_plain_text(self):
        from streamlit.testing.v1 import AppTest
        raw_quote = "[do not render link](https://example.invalid) <script>alert(1)</script>"
        report = {"document_id": "test", "errors": [], "coverage": {"execution_complete": True, "complete": False, "model_ran": True},
                  "rejected_candidates": [{"candidate": {"error_type": "数值缺失", "spans": [{"text": raw_quote}],
                                                          "reason": "模型理由"}, "reason": "source text absent"}]}
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "text_review.json"
            source.write_text(json.dumps(report), encoding="utf-8")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            script = "from text_review_view import show_text_report\nimport json\nfrom pathlib import Path\n" + \
                     f"show_text_report(json.loads(Path({str(source)!r}).read_text(encoding='utf-8')), {directory!r})\n"
            app = AppTest.from_string(script, default_timeout=30).run()
            self.assertFalse(app.exception, str(app.exception))
            self.assertTrue(any(metric.label == "待复核提示（含未定位）" and str(metric.value) == "1" for metric in app.metric))
            self.assertTrue(any(element.value == raw_quote for element in app.get("text")))
            self.assertFalse(any(raw_quote in element.value for element in app.markdown))
            next(button for button in app.button if button.label == "保存文本复核").click().run()
            self.assertFalse(app.exception, str(app.exception))
            self.assertEqual(len(ReviewStore(directory).load()), 1)
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), digest)

    def test_default_and_offline_text_flow(self):
        from streamlit.testing.v1 import AppTest
        app = AppTest.from_file(str(ROOT / "frontend/app.py"), default_timeout=30).run()
        self.assertFalse(app.exception, str(app.exception))
        app.sidebar.radio[0].set_value("单份文本检查").run()
        self.assertFalse(app.exception, str(app.exception))
        app.text_area[0].set_value("公司报告日期为2025年2月30日。").run()
        next(button for button in app.button if button.label == "检查文本").click().run()
        self.assertFalse(app.exception, str(app.exception))
        self.assertTrue(any(metric.label == "经规则确认" and str(metric.value) == "1" for metric in app.metric))
        self.assertTrue(any("未运行模型" in caption.value for caption in app.caption))


if __name__ == "__main__":
    unittest.main()
