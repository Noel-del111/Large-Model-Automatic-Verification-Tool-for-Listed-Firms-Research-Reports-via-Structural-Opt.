import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sample_pdfs import make_all  # noqa: E402
from yjparse.config import load_thresholds  # noqa: E402
from yjparse.kb_export import export_kb, parse_result_to_markdown  # noqa: E402
from yjparse.pipeline import PipelineConfig, parse_document  # noqa: E402
from yjparse.retrieval import Bm25Index, compare_across_documents, load_index  # noqa: E402


class TestKnowledgeBaseExport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        samples = make_all(cls.root / "raw")
        cls.out = cls.root / "out"
        cls.kb = cls.root / "kb"
        config = PipelineConfig(primary="pymupdf", reference="none", ocr="off",
                                thresholds=load_thresholds())
        cls.result = parse_document(samples["report"], cls.out, config)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_markdown_has_page_and_block_anchors(self):
        payload = json.loads((self.out / self.result.doc.doc_id / "parse_result.json")
                             .read_text(encoding="utf-8"))
        markdown = parse_result_to_markdown(payload)
        self.assertIn("<!-- Page 1 -->", markdown)
        self.assertIn("<!-- block:", markdown)
        self.assertIn("page:1", markdown)
        self.assertIn("bbox:[", markdown)
        self.assertIn("| 指标 |", markdown)          # 表格转成了 Markdown 表
        self.assertIn("sha256:", markdown)           # 来源与哈希留在文件头

    def test_export_kb_writes_index_and_commands(self):
        manifest = export_kb(self.out, self.kb)
        self.assertEqual(len(manifest["documents"]), 1)
        index_path = Path(manifest["kb_index"])
        rows = load_index(index_path)
        self.assertEqual(len(rows), manifest["blocks"])
        self.assertTrue(all(row["page"] >= 1 for row in rows))
        self.assertTrue(all(row["bbox"] for row in rows))
        target = manifest["viking_targets"][0]
        self.assertIn("ov add-resource", target["command"])
        self.assertTrue(target["uri"].startswith("viking://resources/"))

    def test_offline_search_returns_locatable_hits(self):
        export_kb(self.out, self.kb)
        rows = load_index(self.kb / "kb_index.jsonl")
        index = Bm25Index(rows)
        hits = index.search("营业收入", top_k=3)
        self.assertTrue(hits, "检索没有命中")
        for hit in hits:
            self.assertGreaterEqual(hit["page"], 1)
            self.assertEqual(len(hit["bbox"]), 4)
            self.assertTrue(hit["block_id"])
        groups = compare_across_documents(rows, "营业收入", top_k_per_doc=1)
        self.assertEqual(len(groups), 1)
        self.assertTrue(groups[0]["hits"])


if __name__ == "__main__":
    unittest.main()
