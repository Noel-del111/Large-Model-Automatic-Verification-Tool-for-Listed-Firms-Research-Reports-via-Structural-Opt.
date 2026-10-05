import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sample_pdfs import make_all  # noqa: E402
from yjparse.artifacts import verify_run  # noqa: E402
from yjparse.config import load_thresholds  # noqa: E402
from yjparse.engines import registry  # noqa: E402
from yjparse.pipeline import PipelineConfig, parse_document  # noqa: E402


def _ocr_ok() -> bool:
    try:
        from yjparse.ocr import ocr_available

        return ocr_available()
    except Exception:
        return False


class TestPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.samples = make_all(cls.root / "raw")
        cls.out = cls.root / "out"
        cls.thresholds = load_thresholds()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _config(self, reference="none"):
        return PipelineConfig(primary="pymupdf", reference=reference,
                              thresholds=self.thresholds)

    def test_parse_report_pdf(self):
        result = parse_document(self.samples["report"], self.out, self._config())
        self.assertEqual(len(result.pages), 2)
        self.assertEqual(result.quality_report.status in ("ok", "warn"), True)
        for page in result.pages:
            self.assertGreater(len(page.blocks), 0, f"第 {page.page} 页没有块")
            for block in page.blocks:
                self.assertIsNotNone(block.bbox, f"{block.block_id} 缺少坐标")
        tables = [b for p in result.pages for b in p.blocks if b.type == "table"]
        self.assertGreaterEqual(len(tables), 1, "未识别到表格")
        self.assertTrue(tables[0].cells, "表格没有单元格")

    def test_tables_have_source_and_page(self):
        result = parse_document(self.samples["report"], self.out, self._config())
        payload = json.loads((self.out / result.doc.doc_id / "parse_result.json")
                             .read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], "1.1.0")
        self.assertTrue(payload["doc"]["sha256"])
        self.assertIn("quality_report", payload)
        self.assertIn(payload["quality_report"]["status"], ("ok", "warn", "fail"))
        for page in payload["pages"]:
            for block in page["blocks"]:
                self.assertEqual(len(block["bbox"]), 4)

    def test_artifacts_and_verify(self):
        result = parse_document(self.samples["report"], self.out, self._config())
        doc_dir = self.out / result.doc.doc_id
        for name in ("parse_result.json", "pages.jsonl", "blocks.jsonl",
                     "quality_report.json", "quality_table.csv"):
            self.assertTrue((doc_dir / name).exists(), f"缺少产物 {name}")
        checks = verify_run(self.out)
        self.assertTrue(checks)
        self.assertTrue(all(c["match"] for c in checks), "复现校验未全部通过")
        lines = (doc_dir / "blocks.jsonl").read_text(encoding="utf-8").strip().splitlines()
        self.assertTrue(all(json.loads(line)["page"] >= 1 for line in lines))

    def test_scanned_pdf_is_flagged(self):
        result = parse_document(self.samples["scanned"], self.out, self._config())
        self.assertEqual(result.quality_report.status, "fail")
        self.assertIn(1, result.quality_report.failed_pages)
        reasons = " ".join(result.quality_report.reasons.get("1", []))
        self.assertIn("no_text_layer", reasons)

    @unittest.skipUnless(registry().get("pdfplumber", {}).get("available"),
                         "未安装 pdfplumber，跳过交叉校验用例")
    def test_cross_check_with_pdfplumber(self):
        result = parse_document(self.samples["report"], self.out, self._config("pdfplumber"))
        self.assertIsNotNone(result.reference_engine)
        agreements = [p.quality.engine_agreement_bag for p in result.pages
                      if p.quality.engine_agreement_bag is not None]
        self.assertTrue(agreements, "没有产生交叉校验结果")
        self.assertGreater(min(agreements), 0.5)

    def test_preview_renders_png(self):
        from yjparse.preview import render_preview

        result = parse_document(self.samples["report"], self.out, self._config())
        images = render_preview(self.out / result.doc.doc_id / "parse_result.json",
                                pages=[1], dpi=60)
        self.assertEqual(len(images), 1)
        self.assertTrue(images[0].exists())
        self.assertGreater(images[0].stat().st_size, 1000)

    @unittest.skipUnless(_ocr_ok(), "未安装 RapidOCR，跳过 OCR 用例")
    def test_ocr_recovers_scanned_page(self):
        from sample_pdfs import make_report_pdf
        from yjparse.pipeline import PipelineConfig

        pdf = make_report_pdf(self.root / "raw" / "ocr_sample.pdf")
        config = PipelineConfig(primary="pymupdf", reference="none",
                                ocr="always", ocr_dpi=150,
                                thresholds=self.thresholds)
        result = parse_document(pdf, self.root / "out_ocr", config)
        self.assertGreater(result.quality_report.summary.get("ocr_pages", 0), 0)
        ocr_blocks = [b for p in result.pages for b in p.blocks if b.level == "ocr"]
        self.assertTrue(ocr_blocks, "OCR 没有产出文本块")
        self.assertTrue(all(b.bbox is not None for b in ocr_blocks))


if __name__ == "__main__":
    unittest.main()
