import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from yjparse.contract import BBox, Block, Page, clamp_bbox  # noqa: E402
from yjparse.layout import column_count, order_blocks  # noqa: E402
from yjparse.quality import bag_agreement, decide_status, sequence_agreement  # noqa: E402
from yjparse.textstruct import (  # noqa: E402
    attach_captions_and_sources,
    build_sentences,
    classify_text_block,
)


def _block(bid, x0, y0, x1, y1, kind="text", text="文本"):
    return Block(block_id=bid, type=kind, bbox=BBox(x0, y0, x1, y1), text=text)


class TestLayout(unittest.TestCase):
    def test_two_column_order(self):
        page_w = 600.0
        left = [_block(f"L{i}", 60, 100 + i * 30, 280, 120 + i * 30) for i in range(3)]
        right = [_block(f"R{i}", 320, 100 + i * 30, 540, 120 + i * 30) for i in range(3)]
        interleaved = [left[0], right[0], left[1], right[1], left[2], right[2]]
        ordered = order_blocks(interleaved, page_w)
        ids = [b.block_id for b in ordered]
        self.assertEqual(ids[:3], ["L0", "L1", "L2"])
        self.assertEqual(ids[3:], ["R0", "R1", "R2"])
        self.assertEqual(column_count(interleaved, page_w), 2)
        self.assertEqual([b.order for b in ordered], list(range(6)))

    def test_single_column_stays_top_down(self):
        page_w = 600.0
        blocks = [_block(f"B{i}", 60, 200 - i * 40, 540, 230 - i * 40) for i in range(3)]
        ordered = order_blocks(blocks, page_w)
        self.assertEqual([b.block_id for b in ordered], ["B2", "B1", "B0"])
        self.assertEqual(column_count(blocks, page_w), 1)

    def test_full_width_block_splits_bands(self):
        page_w = 600.0
        head = _block("H", 60, 60, 560, 90, kind="heading")
        left = [_block(f"L{i}", 60, 110 + i * 40, 280, 140 + i * 40) for i in range(2)]
        right = [_block(f"R{i}", 320, 110 + i * 40, 540, 140 + i * 40) for i in range(2)]
        ordered = order_blocks([left[0], right[0], head, left[1], right[1]], page_w)
        self.assertEqual(ordered[0].block_id, "H")


class TestClamp(unittest.TestCase):
    def test_small_overflow_is_clamped(self):
        bbox = BBox(10, 10, 610, 100)
        fixed, changed, ratio = clamp_bbox(bbox, 600, 800)
        self.assertTrue(changed)
        self.assertEqual(fixed.x1, 600)
        self.assertLess(ratio, 0.05)

    def test_large_overflow_reports_ratio(self):
        _, _, ratio = clamp_bbox(BBox(0, 0, 1200, 100), 600, 800)
        self.assertGreater(ratio, 0.25)


class TestQualityMetrics(unittest.TestCase):
    def test_sentence_split_keeps_bbox_inside_block(self):
        lines = [
            ("公司2025年上半年营业收入同比增长18.6%。归母净利润", BBox(60, 100, 540, 114)),
            ("同比增长22.1%，业绩符合预期。", BBox(60, 116, 300, 130)),
        ]
        sentences = build_sentences(lines)
        self.assertEqual([s.text for s in sentences],
                         ["公司2025年上半年营业收入同比增长18.6%。",
                          "归母净利润同比增长22.1%，业绩符合预期。"])
        for sentence in sentences:
            self.assertIsNotNone(sentence.bbox)
            self.assertGreaterEqual(sentence.bbox.x0, 59.0)
            self.assertLessEqual(sentence.bbox.x1, 541.0)
            self.assertGreaterEqual(sentence.bbox.y0, 99.0)
            self.assertLessEqual(sentence.bbox.y1, 131.0)

    def test_heading_classification(self):
        self.assertEqual(classify_text_block("二、盈利预测与估值", 15.0, 10.5), ("heading", "h2"))
        self.assertEqual(classify_text_block("投资要点", 18.0, 10.5)[0], "heading")
        self.assertEqual(classify_text_block("我们预计公司 2025 年归母净利润", 10.5, 10.5),
                         ("text", "body"))
        long_text = "公司" * 40
        self.assertEqual(classify_text_block(long_text, 18.0, 10.5), ("text", "body"))

    def test_caption_and_source_attachment(self):
        table = Block(block_id="t1", type="table", bbox=BBox(60, 200, 540, 400), cells=[])
        caption = Block(block_id="c1", type="text", bbox=BBox(60, 184, 300, 198),
                        text="表 1 主要财务指标")
        source = Block(block_id="s1", type="text", bbox=BBox(60, 404, 300, 418),
                       text="资料来源：公司公告")
        attached = attach_captions_and_sources([table, caption, source])
        self.assertEqual(attached, 2)
        self.assertEqual(table.caption, "表 1 主要财务指标")
        self.assertIn("资料来源：公司公告", table.source_note)
        self.assertEqual(caption.type, "caption")

    def test_bag_agreement_ignores_order(self):
        a = "公司上半年营业收入同比增长18.6%"
        b = "同比增长18.6%公司上半年营业收入"
        self.assertLess(sequence_agreement(a, b), 0.8)
        self.assertEqual(bag_agreement(a, b), 1.0)

    def test_bag_agreement_detects_missing_text(self):
        a = "营业收入 8,420 净利润 1,120"
        b = "营业收入 8,420"
        value = bag_agreement(a, b)
        self.assertIsNotNone(value)
        self.assertLess(value, 0.9)

    def test_low_coverage_is_informational_only(self):
        """文字密度只记录、不判警告：研报里图表页天然字少，误报会淹没真正的问题页。"""
        page = Page(page=1, page_size=(595.0, 842.0),
                    blocks=[_block("b1", 60, 60, 540, 90, text="短句" * 20)])
        from yjparse.quality import compute_page_quality

        quality = compute_page_quality(page)
        # A4 页面约 500 千平方点，200 字对应密度约 0.4
        self.assertLess(quality.text_coverage, 0.8)
        status, reasons = decide_status(
            quality, [], {"text_coverage_warn": 0.8, "min_text_layer_chars_per_page": 30})
        self.assertEqual(status, "ok")
        self.assertTrue(any(r.startswith("info:low_text_coverage") for r in reasons))

    def test_image_only_page_is_warning_not_failure(self):
        from yjparse.quality import compute_page_quality

        page = Page(page=1, page_size=(595.0, 842.0), blocks=[
            Block(block_id="i1", type="image", bbox=BBox(0, 0, 595, 842)),
        ])
        quality = compute_page_quality(page)
        status, reasons = decide_status(
            quality, [], {"min_text_layer_chars_per_page": 30,
                          "image_only_page_min_area_ratio": 0.3},
            doc_context={"has_text_layer": True})
        self.assertEqual(status, "warn")
        self.assertTrue(any("image_only_page" in r for r in reasons))

    def test_blank_page_in_text_document_fails(self):
        from yjparse.quality import compute_page_quality

        page = Page(page=2, page_size=(595.0, 842.0), blocks=[])
        quality = compute_page_quality(page)
        status, reasons = decide_status(
            quality, [], {"min_text_layer_chars_per_page": 30},
            doc_context={"has_text_layer": True})
        self.assertEqual(status, "fail")
        self.assertTrue(any("no_text_layer:empty_page" in r for r in reasons))

    def test_scanned_document_fails(self):
        from yjparse.quality import compute_page_quality

        page = Page(page=1, page_size=(595.0, 842.0), blocks=[])
        quality = compute_page_quality(page)
        status, reasons = decide_status(
            quality, [], {"min_text_layer_chars_per_page": 30},
            doc_context={"has_text_layer": False})
        self.assertEqual(status, "fail")
        self.assertTrue(any("no_text_layer:scanned_pdf" in r for r in reasons))


if __name__ == "__main__":
    unittest.main()
