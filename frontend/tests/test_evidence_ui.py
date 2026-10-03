import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "frontend"), str(ROOT / "repo/factcheck/src"), str(ROOT / "repo/pdfparse/src")]
from streamlit.testing.v1 import AppTest
from yjcheck.models import Block, Document
from yjcheck.pipeline import check_documents, write_result, verify_artifacts


class EvidenceUITests(unittest.TestCase):
    def test_missing_evidence_is_visible_and_old_results_still_render(self):
        doc = Document("r", "a" * 64, "r-run", "report.docx", "report", "测试公司", "2024FY",
                       [Block("p", "2024年营业收入200万元。", paragraph=1)])
        result = check_documents(doc, [])
        with tempfile.TemporaryDirectory() as temp:
            directory = write_result(result, Path(temp))
            app = AppTest.from_file(str(ROOT / "frontend/app.py"), default_timeout=20)
            app.session_state["check_result"] = result
            app.session_state["check_dir"] = str(directory)
            app.run()
            self.assertFalse(app.exception, [e.message for e in app.exception])
            self.assertTrue(any("补充证据" in w.value for w in app.warning))
            self.assertTrue(any("营业收入" in m.value and "补充" in m.value for m in app.markdown))
            self.assertTrue(verify_artifacts(directory))
            for finding in result["findings"]:
                finding.pop("decision")
                finding.pop("evidence_request")
            app.session_state["check_result"] = result
            app.run()
            self.assertFalse(app.exception, [e.message for e in app.exception])


if __name__ == "__main__":
    unittest.main()
