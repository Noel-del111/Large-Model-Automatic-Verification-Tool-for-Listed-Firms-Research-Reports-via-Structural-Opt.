"""Regression cases for the B-to-C evidence integrity review (2026-09-26)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from yjparse.artifacts import verify_run
from yjparse.contract import BBox, Block, Page, validate_result
from yjparse.engines.pdfplumber_engine import PdfPlumberEngine
from yjparse.engines.pymupdf_engine import PyMuPDFEngine
from yjparse.kb_export import export_kb, parse_result_to_markdown
from yjparse.ocr import OcrLine, lines_to_blocks
from yjparse.pipeline import PipelineConfig, _page_violations, _pages_needing_ocr, parse_document, resolve_reference
from yjparse.report import summarize
from yjparse.retrieval import Bm25Index
from yjparse.textstruct import attach_captions_and_sources


def pdf(path, texts):
    path.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open() as document:
        for text in texts:
            page = document.new_page(width=595, height=842)
            page.insert_text((60, 100), text, fontsize=12)
        document.save(path)
    return path


class TestHandoffRegressions(unittest.TestCase):
    def test_auto_ocr_requires_image_evidence_and_preserves_coverage_failures(self):
        blank = Page(page=1, page_size=(100, 100))
        short = Page(page=2, page_size=(100, 100),
                     blocks=[Block(block_id="short", type="text", bbox=BBox(0, 0, 90, 20), text="Cover", order=0)])
        scan = Page(page=3, page_size=(100, 100), engine_stats={"image_area": 10000})
        missing = Page(page=4, page_size=(100, 100), engine_stats={"image_area": 10000, "coverage_violations": ["missing_source_page"]})
        self.assertEqual([p.page for p in _pages_needing_ocr([blank, short, scan, missing], "auto", {})], [3])
        self.assertEqual(_pages_needing_ocr([blank], "always", {}), [blank])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = PipelineConfig(reference="none", ocr="off")

    def tearDown(self):
        self.temp.cleanup()

    def test_same_filename_and_reparse_preserve_history_and_latest_exports(self):
        source_a = pdf(self.root / "a" / "annual.pdf", ["Alpha revenue 100"])
        source_b = pdf(self.root / "b" / "annual.pdf", ["Beta revenue 200"])
        output = self.root / "out"
        first = parse_document(source_a, output, self.config)
        second = parse_document(source_b, output, self.config)
        again = parse_document(source_a, output, self.config)
        self.assertNotEqual(first.doc.doc_id, second.doc.doc_id)
        self.assertEqual(first.doc.doc_id, again.doc.doc_id)
        self.assertNotEqual(first.run_id, again.run_id)
        historic = output / first.doc.doc_id / "runs" / first.run_id / "parse_result.json"
        self.assertEqual(json.loads(historic.read_text(encoding="utf-8"))["run_id"], first.run_id)
        current = output / first.doc.doc_id / "parse_result.json"
        self.assertEqual(json.loads(current.read_text(encoding="utf-8"))["run_id"], again.run_id)
        self.assertTrue(all(item["match"] for item in verify_run(output)))
        manifest = export_kb(output, self.root / "kb")
        self.assertEqual(len(manifest["documents"]), 2)
        all_markdown = " ".join(Path(d["markdown"]).read_text(encoding="utf-8")
                                for d in manifest["documents"])
        self.assertIn("Alpha revenue 100", all_markdown)
        self.assertIn("Beta revenue 200", all_markdown)
        self.assertEqual(summarize(output)["documents"], 2)

    def test_partial_and_empty_mineru_outputs_fail_against_source_page_count(self):
        source = pdf(self.root / "source.pdf", ["One", "Two"])
        for entries in ([{"page_idx": 0, "type": "text", "bbox": [60, 60, 200, 80],
                          "text": "One"}], []):
            offline = self.root / "mineru.json"
            offline.write_text(json.dumps(entries), encoding="utf-8")
            result = parse_document(source, self.root / "out", PipelineConfig(
                primary="mineru", reference="none", ocr="off",
                engine_params={"mineru": {"output": str(offline)}}))
            self.assertEqual(result.doc.total_pages, 2)
            self.assertEqual([p.page for p in result.pages], [1, 2])
            self.assertEqual(result.pages[1].status, "fail")
            self.assertEqual(result.quality_report.status, "fail")
            self.assertIn("missing_source_page", result.pages[1].notes)
            self.assertEqual(result.pages[0].page_size, (595, 842))

    def test_duplicate_and_out_of_range_page_numbers_cannot_pass(self):
        source = pdf(self.root / "source.pdf", ["One", "Two"])
        invalid = [Page(n, (595, 842), blocks=[Block(f"b{index}", bbox=BBox(10, 10, 200, 40),
                   text="valid source text")]) for index, n in enumerate([1, 1, 3])]
        with patch.object(PyMuPDFEngine, "parse", return_value=invalid):
            result = parse_document(source, self.root / "out", self.config)
        self.assertEqual(result.quality_report.status, "fail")
        self.assertTrue(all(p.status == "fail" for p in result.pages))
        self.assertTrue(any("duplicate_page" in v for v in validate_result(result)))
        self.assertIn("page_number_invalid", validate_result(result))

    def test_ocr_does_not_concatenate_rows_or_columns(self):
        lines = [OcrLine(value, BBox(x, y, x + 40, y + 10), .95)
                 for value, x, y in [("2024", 10, 10), ("2025", 100, 10),
                                      ("100", 10, 25), ("200", 100, 25)]]
        blocks = lines_to_blocks(lines, 1)
        self.assertEqual([b.text for b in blocks], ["2024", "2025", "100", "200"])
        stacked = lines_to_blocks([lines[0], lines[2]], 1)
        self.assertEqual(stacked[0].text, "2024\n100")

    def test_failed_page_never_becomes_markdown_or_search_evidence(self):
        source = pdf(self.root / "source.pdf", ["Untrusted profit 500"])
        result = parse_document(source, self.root / "out", self.config)
        result.pages[0].status = "fail"
        payload = result.to_dict()
        text = parse_result_to_markdown(payload)
        self.assertIn("page_status:fail", text)
        self.assertNotIn("Untrusted profit", text)
        path = self.root / "out" / result.doc.doc_id / "parse_result.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        manifest = export_kb(self.root / "out", self.root / "kb")
        self.assertEqual(manifest["blocks"], 0)
        index = Bm25Index([{"doc_id": "legacy", "page_status": "fail", "text": "profit 500"}])
        self.assertEqual(index.search("profit"), [])

    def test_pdfplumber_table_cells_have_correct_row_column_coordinates(self):
        path = self.root / "table.pdf"
        with pymupdf.open() as document:
            page = document.new_page()
            for r, row in enumerate([["2024", "2025"], ["100", "200"]]):
                for c, value in enumerate(row):
                    rect = pymupdf.Rect(50 + c * 100, 100 + r * 30, 150 + c * 100, 130 + r * 30)
                    page.draw_rect(rect)
                    page.insert_text((rect.x0 + 5, rect.y0 + 18), value)
            document.save(path)
        result = parse_document(path, self.root / "out", PipelineConfig(
            primary="pdfplumber", reference="none", ocr="off"))
        tables = [b for b in result.pages[0].blocks if b.type == "table"]
        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0].as_block_list(), [["2024", "2025"], ["100", "200"]])
        for cell in tables[0].cells:
            self.assertEqual(cell.bbox.to_list(), [50 + 100 * cell.col, 100 + 30 * cell.row,
                                                  150 + 100 * cell.col, 130 + 30 * cell.row])

    def test_sentence_offsets_address_exact_multiline_block_text(self):
        path = self.root / "sentences.pdf"
        with pymupdf.open() as document:
            page = document.new_page()
            page.insert_text((60, 100), "营收同比\n增长20%。利润下降5%。", fontname="china-s")
            document.save(path)
        pages = PyMuPDFEngine().parse(path, "sentences")
        sentences = [s for p in pages for b in p.blocks for s in b.sentences]
        self.assertEqual(len(sentences), 2)
        for page in pages:
            for block in page.blocks:
                for sentence in block.sentences:
                    self.assertEqual(block.text[sentence.char_start:sentence.char_end], sentence.text)
        self.assertEqual(sentences[1].text, "利润下降5%。")

    def test_side_by_side_tables_keep_their_own_captions_and_sources(self):
        blocks = []
        for name, x in [("left", 30), ("right", 330)]:
            blocks.extend([
                Block(name, "table", BBox(x, 100, x + 240, 300)),
                Block(name + "_c", bbox=BBox(x, 80, x + 120, 95), text="表1 " + name),
                Block(name + "_s", bbox=BBox(x, 305, x + 200, 320), text="来源：" + name),
            ])
        self.assertEqual(attach_captions_and_sources(blocks), 4)
        self.assertEqual(blocks[0].caption, "表1 left")
        self.assertEqual(blocks[0].source_note, "来源：left")
        self.assertEqual(blocks[3].caption, "表1 right")
        self.assertEqual(blocks[3].source_note, "来源：right")

    def test_ambiguous_spanning_caption_remains_unassigned(self):
        left = Block("left", "table", BBox(20, 100, 250, 300))
        right = Block("right", "table", BBox(350, 100, 580, 300))
        caption = Block("caption", bbox=BBox(20, 80, 580, 95), text="表1 shared")
        self.assertEqual(attach_captions_and_sources([left, right, caption]), 0)
        self.assertFalse(left.caption or right.caption)

    def test_clamping_outside_block_to_zero_area_is_hard_failure(self):
        page = Page(1, (595, 842), [Block("outside", bbox=BBox(600, 100, 610, 120))])
        hard, soft = _page_violations(page, 2, .25)
        self.assertTrue(any("bbox_degenerate" in item for item in hard))
        self.assertFalse(soft)

    def test_none_reference_never_resolves_to_an_engine(self):
        with patch("yjparse.pipeline.registry", side_effect=AssertionError("must not resolve")):
            self.assertIsNone(resolve_reference("pymupdf", "none"))


if __name__ == "__main__":
    unittest.main()
