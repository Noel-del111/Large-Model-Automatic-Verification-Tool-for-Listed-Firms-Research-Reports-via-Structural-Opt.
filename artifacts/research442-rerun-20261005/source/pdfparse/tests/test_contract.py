import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from yjparse.contract import (  # noqa: E402
    BBox,
    Block,
    Cell,
    DocumentMeta,
    EngineInfo,
    Page,
    ParseResult,
    QualityReport,
    validate_result,
)


class TestBBox(unittest.TestCase):
    def test_from_any_normalizes(self):
        bbox = BBox.from_any([100, 200, 50, 120])
        self.assertEqual(bbox.to_list(), [50.0, 120.0, 100.0, 200.0])

    def test_degenerate_and_out_of_page(self):
        self.assertTrue(BBox(10, 10, 10, 10).is_degenerate())
        self.assertFalse(BBox(10, 10, 60, 60).is_degenerate())
        self.assertTrue(BBox(-5, 10, 60, 60).out_of_page(595, 842))
        self.assertFalse(BBox(10, 10, 60, 60).out_of_page(595, 842))


class TestContract(unittest.TestCase):
    def _result(self, blocks):
        page = Page(page=1, page_size=(595.0, 842.0), blocks=blocks)
        return ParseResult(
            run_id="r1",
            doc=DocumentMeta(doc_id="d1", source_path="x.pdf", sha256="0" * 64, total_pages=1),
            engine=EngineInfo(name="pymupdf", version="1.0"),
            pages=[page],
            quality_report=QualityReport(),
        )

    def test_missing_bbox_is_violation(self):
        result = self._result([Block(block_id="b1", type="text", bbox=None)])
        violations = validate_result(result)
        self.assertTrue(any("bbox_missing" in v for v in violations))

    def test_out_of_page_bbox_is_violation(self):
        result = self._result([Block(block_id="b1", type="text", bbox=BBox(0, 0, 700, 40))])
        self.assertTrue(any("bbox_out_of_page" in v for v in validate_result(result)))

    def test_page_count_mismatch(self):
        result = self._result([Block(block_id="b1", type="text", bbox=BBox(1, 1, 10, 10))])
        result.doc.total_pages = 3
        self.assertTrue(any("page_count_mismatch" in v for v in validate_result(result)))

    def test_table_grid(self):
        block = Block(
            block_id="t1", type="table", bbox=BBox(0, 0, 100, 50),
            cells=[Cell(row=0, col=0, text="指标"), Cell(row=0, col=1, text="2025E"),
                   Cell(row=1, col=0, text="营收"), Cell(row=1, col=1, text="9,986")],
        )
        self.assertEqual(block.as_block_list(),
                         [["指标", "2025E"], ["营收", "9,986"]])


if __name__ == "__main__":
    unittest.main()
