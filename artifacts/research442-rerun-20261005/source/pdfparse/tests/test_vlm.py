"""第三层质检与模型端点体检的测试：用本地桩服务做真实的 HTTP 往返。"""

import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sample_pdfs import make_report_pdf  # noqa: E402
from yjparse.config import load_thresholds  # noqa: E402
from yjparse.model_check import probe  # noqa: E402
from yjparse.pipeline import PipelineConfig, parse_document  # noqa: E402
from yjparse.vlm_check import VlmConfig, check_page, parse_verdict, select_pages  # noqa: E402


class _StubHandler(BaseHTTPRequestHandler):
    verdict = {"match": True, "issues": [], "confidence": 0.9}
    seen_payload = {}

    def log_message(self, *args):  # 静音
        return

    def _send(self, payload, code=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.endswith("/models"):
            self._send({"data": [{"id": "stub-vl"}, {"id": "stub-embed"}]})
        else:
            self._send({"error": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        type(self).seen_payload = payload
        if self.path.endswith("/embeddings"):
            self._send({"data": [{"embedding": [0.1] * 8}]})
        elif self.path.endswith("/chat/completions"):
            content = json.dumps(type(self).verdict, ensure_ascii=False)
            self._send({"choices": [{"message": {"content": content}}]})
        else:
            self._send({"error": "not found"}, 404)


class TestVlmCheck(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_address[1]}/v1"
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.pdf = make_report_pdf(cls.root / "sample.pdf")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.tmp.cleanup()

    def _config(self):
        return VlmConfig(base_url=self.base_url, model="stub-vl", api_key="test",
                         timeout=15, max_pages=5)

    def test_parse_verdict_handles_code_fence_and_noise(self):
        verdict = parse_verdict('这是结果：```json\n{"match": false, "issues": '
                                '[{"type": "wrong_number", "detail": "9,986 应为 9,886"}], '
                                '"confidence": 0.8}\n```')
        self.assertEqual(verdict.status, "mismatch")
        self.assertEqual(verdict.issues[0]["type"], "wrong_number")

    def test_parse_verdict_low_confidence_treated_as_match(self):
        verdict = parse_verdict('{"match": false, "issues": [], "confidence": 0.2}',
                                min_confidence=0.5)
        self.assertEqual(verdict.status, "match")

    def test_check_page_sends_image_and_parses_result(self):
        _StubHandler.verdict = {"match": True, "issues": [], "confidence": 0.95}
        verdict = check_page(self.pdf, 1, "解析文本", self._config())
        self.assertEqual(verdict.status, "match")
        payload = _StubHandler.seen_payload
        self.assertEqual(payload["model"], "stub-vl")
        content = payload["messages"][1]["content"]
        image_part = [c for c in content if c["type"] == "image_url"][0]
        self.assertTrue(image_part["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_check_page_reports_error_on_bad_endpoint(self):
        config = VlmConfig(base_url="http://127.0.0.1:9/v1", model="x", timeout=2)
        verdict = check_page(self.pdf, 1, "解析文本", config)
        self.assertEqual(verdict.status, "error")

    def test_select_pages_prefers_suspicious(self):
        class P:
            def __init__(self, page, status):
                self.page, self.status = page, status

        pages = [P(i, "ok") for i in range(1, 11)]
        pages[3].status = "warn"
        pages[7].status = "fail"
        self.assertEqual(select_pages(pages, "auto", 0.1, 20), [4, 8])
        always = select_pages(pages, "always", 0.5, 20)
        self.assertIn(4, always)
        self.assertIn(8, always)
        self.assertLessEqual(len(always), 7)

    def test_pipeline_marks_vlm_result(self):
        _StubHandler.verdict = {"match": False, "issues": [
            {"type": "missing_text", "detail": "漏掉风险提示段落"}], "confidence": 0.88}
        config = PipelineConfig(primary="pymupdf", reference="none", ocr="off",
                                vlm="always", vlm_config=self._config(),
                                thresholds=load_thresholds())
        result = parse_document(self.pdf, self.root / "out", config)
        self.assertGreater(result.quality_report.summary["vlm_checked_pages"], 0)
        self.assertGreater(result.quality_report.summary["vlm_mismatch_pages"], 0)
        notes = " ".join(n for page in result.pages for n in page.notes)
        self.assertIn("vlm_check:mismatch", notes)
        self.assertTrue(any(page.quality.vlm_checked for page in result.pages))


class TestModelCheck(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_address[1]}/v1"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_probe_reports_all_three_capabilities(self):
        results = probe(self.base_url, api_key="k", chat_model="stub-vl",
                        embedding_model="stub-embed", vision_model="stub-vl", timeout=10)
        names = {item.name: item.ok for item in results}
        self.assertEqual(names.get("模型列表"), True)
        self.assertEqual(names.get("Embedding"), True)
        self.assertEqual(names.get("对话"), True)
        self.assertEqual(names.get("视觉"), True)


if __name__ == "__main__":
    unittest.main()
