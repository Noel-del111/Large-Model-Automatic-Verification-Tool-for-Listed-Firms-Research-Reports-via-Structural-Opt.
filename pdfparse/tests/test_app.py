"""可视化页面测试：用 Streamlit 官方无头测试框架执行 app.py，确认页面能跑通。"""

import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "src"))
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "tests"))

try:
    from streamlit.testing.v1 import AppTest

    _HAS_STREAMLIT = True
except Exception:  # pragma: no cover - 未安装 streamlit
    _HAS_STREAMLIT = False


@unittest.skipUnless(_HAS_STREAMLIT, "未安装 streamlit，跳过可视化页面测试")
class TestApp(unittest.TestCase):
    def test_app_runs_without_exception(self):
        app = AppTest.from_file(str(BASE / "app.py"), default_timeout=120)
        app.run()
        self.assertEqual(len(app.exception), 0, [str(e) for e in app.exception])
        self.assertIn("研报解析质检台", str(app.title[0].value))
        # 未上传文件时应给出引导，而不是报错
        self.assertTrue(any("上传" in str(info.value) for info in app.info))

    def test_sidebar_options_present(self):
        app = AppTest.from_file(str(BASE / "app.py"), default_timeout=120)
        app.run()
        labels = [box.label for box in app.selectbox]
        self.assertIn("主引擎", labels)
        self.assertIn("表格策略", labels)
        radio_labels = [r.label for r in app.radio]
        self.assertIn("OCR 兜底", radio_labels)
        self.assertIn("视觉模型抽检", radio_labels)

    def test_result_panels_render_with_real_parse(self):
        """把真实解析结果放进会话状态，确认指标卡、逐页表、检索面板都能渲染。"""
        import app as app_module
        from sample_pdfs import make_report_pdf
        from yjparse.config import load_thresholds
        from yjparse.kb_export import export_kb
        from yjparse.pipeline import PipelineConfig, parse_document

        with tempfile.TemporaryDirectory() as tmp:
            pdf = make_report_pdf(Path(tmp) / "示例研报.pdf")
            out_dir = Path(tmp) / "out"
            result = parse_document(pdf, out_dir, PipelineConfig(
                primary="pymupdf", reference="none", ocr="off",
                thresholds=load_thresholds()))
            export_kb(out_dir, Path(tmp) / "kb")

            app = AppTest.from_file(str(BASE / "app.py"), default_timeout=180)
            app.session_state["results"] = [result]
            app.session_state["search_image"] = ""
            app.run()
            self.assertEqual(len(app.exception), 0, [str(e) for e in app.exception])
            # 六张指标卡（文档、页数、正常页、警告页、失败页、表格/句子）
            self.assertGreaterEqual(len(app.metric), 6)
            self.assertIn("页数", [m.label for m in app.metric])


if __name__ == "__main__":
    unittest.main()
