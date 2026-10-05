"""Clean-checkout rebuilding and preservation of mutable human review."""
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "artifacts/research442-rerun-20261005"
NOTES = ROOT / "artifacts/case-diagnostics-20261005"
REBUILD = ROOT / "tools/rebuild_case_diagnostics.py"
RENDER = ROOT / "tools/render_case_diagnostics.py"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def execute(script, *args):
    return subprocess.run([sys.executable, "-I", "-B", str(script), *map(str, args)],
                          capture_output=True, encoding="utf-8")


class CasePackPortabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.temporary.name)
        cls.notes = cls.directory / "compact-notes"
        (cls.notes / "cases").mkdir(parents=True)
        selection = read(NOTES / "selection.json")
        selection["cases"] = selection["cases"][:64]
        selection.pop("supplemental_selection", None)
        (cls.notes / "selection.json").write_text(json.dumps(selection, ensure_ascii=False), encoding="utf-8")
        for case in selection["cases"]:
            name = case["case_id"] + ".analysis.json"
            shutil.copyfile(NOTES / "cases" / name, cls.notes / "cases" / name)
        cls.review_bytes = (json.dumps({"case_id": "C001", "status": "reviewed", "analyst": "Fixture analyst",
                "reviewer": "Fixture reviewer", "final_conclusion": "Fixture only; not a real case signoff",
                "evidence_refs": ["fixture source"], "reviewed_at": "2026-10-06T00:00:00Z"}, indent=1) + "\n")
        # JSONL entries must be one line, but whitespace is deliberately kept
        # so a renderer that rewrites the original human entry is detectable.
        cls.review_bytes = ("  " + json.dumps(json.loads(cls.review_bytes), ensure_ascii=False) + "  \n" +
                            json.dumps({"case_id": "C002", "status": "unresolved", "final_conclusion": "Still uncertain"}) + "\n").encode("utf-8")
        (cls.notes / "human_review.jsonl").write_bytes(cls.review_bytes)
        (cls.notes / "README.md").write_text("[流程](../../docs/case-diagnostics/typical-case-guide.md#review)\n[典型性](typicality.md)\n", encoding="utf-8")
        (cls.notes / "typicality.md").write_text("Fixture typicality report.\n", encoding="utf-8")
        (cls.notes / "typicality.json").write_text('{"fixture": true}\n', encoding="utf-8")
        cls.notes_before = {p.relative_to(cls.notes).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                            for p in cls.notes.rglob("*") if p.is_file()}
        cls.out = cls.directory / "rebuilt"
        result = execute(REBUILD, "--package", PACKAGE, "--notes", cls.notes, "--out", cls.out)
        if result.returncode:
            raise AssertionError(result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_one_command_needs_only_tracked_analysis_selection_and_frozen_package(self):
        self.assertFalse((self.notes / "documents").exists())
        self.assertFalse((self.notes / "requests").exists())
        self.assertEqual(len(list((self.out / "cases").glob("*.evidence.json"))), 64)
        self.assertEqual(len(list((self.out / "requests").glob("*.json"))), 59)
        self.assertTrue((self.out / "index.html").is_file())
        # Selection fingerprints must survive Windows/Unix round trips.
        self.assertNotIn(b"\r\n", (self.out / "selection.json").read_bytes())
        self.assertEqual(self.notes_before, {p.relative_to(self.notes).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                                             for p in self.notes.rglob("*") if p.is_file()})

    def test_absent_upstream_source_is_honest_in_metadata_markdown_and_html(self):
        source = read(self.out / "original_source_verification.json")
        self.assertEqual(source["status"], "not_available")
        self.assertFalse(source["all442_exact_match"])
        self.assertEqual(source["verified_records"], 0)
        md = (self.out / "cases/C001.md").read_text(encoding="utf-8")
        self.assertIn("未重新核验其文件哈希或正文", md)
        self.assertNotIn("442项与原始源文件正文及源文件哈希均核对一致", md)
        page = (self.out / "index.html").read_text(encoding="utf-8")
        payload = re.search(r'<script type="application/json" id="payload">(.*?)</script>', page, re.S)
        self.assertEqual(json.loads(payload[1])["source_verification"]["status"], "not_available")
        self.assertEqual(read(self.out / "request_verification_summary.json")["cache_matches"], 442)

    def test_human_entries_are_byte_preserved_missing_entries_appended_and_counts_dynamic(self):
        path = self.out / "human_review.jsonl"
        first = path.read_bytes()
        self.assertTrue(first.startswith(self.review_bytes))
        rows = [json.loads(line) for line in first.decode("utf-8").splitlines() if line.strip()]
        self.assertEqual(len(rows), 64)
        self.assertEqual(len({row["case_id"] for row in rows}), 64)
        validation = read(self.out / "validation.json")
        self.assertEqual(validation["human_review_counts"], {"reviewed": 1, "unresolved": 1, "pending": 62})
        self.assertEqual(validation["human_finalized_cases"], 1)
        self.assertIn("人工已完整裁定 1 条", (self.out / "index.md").read_text(encoding="utf-8"))
        # The builder stores only the batch name, not the author's machine path.
        self.assertEqual(read(self.out / "provenance.json")["package"], PACKAGE.name)
        result = execute(RENDER, self.out)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(path.read_bytes(), first)

    def test_frozen_package_resolution_accepts_old_paths_and_explicit_override(self):
        spec = importlib.util.spec_from_file_location("case_render_resolution", RENDER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for recorded in (str(PACKAGE), "artifacts/" + PACKAGE.name,
                         "Z:\\unavailable-old-checkout\\artifacts\\" + PACKAGE.name):
            with self.subTest(recorded=recorded):
                self.assertEqual(module.resolve_package(self.out, {"package": recorded}), PACKAGE)
        missing_override = self.directory / "explicit-but-missing"
        self.assertEqual(module.resolve_package(self.out, {"package": PACKAGE.name}, missing_override), missing_override)

    def test_rebuild_copies_typicality_and_rebases_repository_docs_only(self):
        for name in ("typicality.md", "typicality.json"):
            self.assertEqual((self.out / name).read_bytes(), (self.notes / name).read_bytes())
        md = (self.out / "README.md").read_text(encoding="utf-8")
        target = re.search(r"\[流程\]\(([^)]+)\)", md)[1]
        self.assertTrue(target.endswith("#review"))
        resolved = (self.out / unquote(target.split("#", 1)[0])).resolve()
        self.assertEqual(resolved, ROOT / "docs/case-diagnostics/typical-case-guide.md")
        self.assertTrue(resolved.is_file())
        self.assertIn("[典型性](typicality.md)", md)

    def test_upstream_verification_is_labelled_as_an_attached_record(self):
        spec = importlib.util.spec_from_file_location("case_render_source", RENDER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            pack = Path(directory)
            (pack / "original_source_verification.json").write_text(json.dumps({"status": "verified", "verified_records": 442}), encoding="utf-8")
            self.assertIn("所附原文件核验记录显示，442", module.source_verification(pack)["message"])

    def test_incomplete_review_does_not_count_as_signoff(self):
        spec = importlib.util.spec_from_file_location("case_render", RENDER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            pack = Path(directory)
            (pack / "human_review.jsonl").write_text(json.dumps({"case_id": "C001", "status": "reviewed"}) + "\n")
            self.assertEqual(module.review_snapshot(pack, ["C001"])["C001"]["display_status"], "incomplete")

    def test_frozen_source_links_resolve_and_generated_links_are_labelled(self):
        md = (self.out / "cases/C001.md").read_text(encoding="utf-8")
        links = re.findall(r"\]\(([^)]+)\)", md)
        frozen = [link for link in links if "research442-rerun-20261005" in link]
        self.assertGreaterEqual(len(frozen), 4)
        for link in frozen:
            target = (self.out / "cases" / unquote(link.split("#", 1)[0])).resolve()
            self.assertTrue(target.is_file(), link)
            self.assertTrue(target.is_relative_to(PACKAGE))
        self.assertIn("需本地 rebuild", md)
        self.assertLess((self.out / "index.md").read_text(encoding="utf-8").index("会议与优化准入"),
                        (self.out / "index.md").read_text(encoding="utf-8").index("交互阅读页"))

    def test_rebuild_refuses_to_overwrite_material(self):
        before = (self.out / "human_review.jsonl").read_bytes()
        result = execute(REBUILD, "--package", PACKAGE, "--notes", self.notes, "--out", self.out)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("overwrite", result.stderr)
        self.assertEqual((self.out / "human_review.jsonl").read_bytes(), before)

    @unittest.skipUnless(shutil.which("node"), "Node is optional; used only to syntax-check the standalone viewer")
    def test_standalone_viewer_javascript_parses(self):
        page = (self.out / "index.html").read_text(encoding="utf-8")
        script = re.findall(r"<script>(.*?)</script>", page, re.S)[0]
        result = subprocess.run([shutil.which("node"), "-e", "new Function(require('fs').readFileSync(0,'utf8'))"],
                                input=script, text=True, encoding="utf-8", capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
