"""Mechanical coverage must not masquerade as semantic causes or random sampling."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools/audit_case_typicality.py"
PACKAGE = ROOT / "artifacts/research442-rerun-20261005"
PACK = ROOT / "artifacts/case-diagnostics-20261005"
spec = importlib.util.spec_from_file_location("typicality", TOOL)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def passage(text, start=0):
    return {"start": start, "end": start + len(text), "text": text}


def pred(text="abc", start=0, error_type="A", **extra):
    return {"error_type": error_type, "spans": [passage(text, start)], **extra}


def gold(text="abc", start=0, error_type="A", **extra):
    return {"type": error_type, "spans": [passage(text, start)], "scorable": True, **extra}


def case(result, prediction=None, target=None, pi=None, gi=None, did="doc"):
    return {"document_id": did, "arm": "hybrid", "result": result, "prediction": prediction,
            "gold": target, "all_hints_index": pi, "gold_index": gi}


def document(hints=(), golds=(), raw=(), pairs=(), content="abc def abc"):
    return {"hints": list(hints), "golds": list(golds), "raw_candidates": list(raw),
            "matched_pairs": list(pairs), "content": content, "length_chars": len(content),
            "scene": "行业研报", "f1": .5, "counts": {}, "execution_complete": True}


def selection_row(row, cid, cohort):
    return {**{k: row[k] for k in ("document_id", "result", "all_hints_index", "gold_index")},
            "case_id": cid, "cohort": cohort,
            "error_type": audit.kind(row["gold"] if row["result"] == "FN" else row["prediction"])}


class GeometryTests(unittest.TestCase):
    def test_two_spans_do_not_silently_union_into_full_gold(self):
        p = {"error_type": "A", "spans": [passage("ab"), passage("cd", 2)]}
        g = gold("abcd")
        c = case("FN", target=g, gi=0)
        labels = audit.mechanical_labels(c, document([p], [g], content="abcd"))
        self.assertFalse(audit.contains(p, g))
        self.assertIn("same_type_partial_overlap", labels)
        self.assertNotIn("one_to_one_competition", labels)

    def test_other_type_quote_coverage_does_not_claim_same_semantic_problem(self):
        p = pred("abc def", error_type="term", reason="Only discusses abc")
        g = gold("def", 4, "date")
        labels = audit.mechanical_labels(case("FN", target=g, gi=0), document([p], [g]))
        self.assertIn("other_type_full_containment", labels)
        self.assertNotIn("same_type_partial_overlap", labels)
        self.assertNotIn("root_cause", labels)

    def test_compatibility_is_not_competition_without_another_assigned_owner(self):
        p, g = pred(), gold()
        c = case("FP", prediction=p, pi=0)
        d = document([p, deepcopy(p)], [g], pairs=[(1, 0)])
        self.assertIn("one_to_one_competition", audit.mechanical_labels(c, d))
        d["matched_pairs"] = []
        self.assertNotIn("one_to_one_competition", audit.mechanical_labels(c, d))
        d["matched_pairs"] = [(0, 1)]
        d["golds"].append(deepcopy(g))
        fn = case("FN", target=g, gi=0)
        self.assertIn("one_to_one_competition", audit.mechanical_labels(fn, d))

    def test_empty_raw_and_nonempty_unrelated_raw_are_distinct(self):
        g = gold()
        c = case("FN", target=g, gi=0)
        d = document(golds=[g])
        empty = audit.mechanical_labels(c, d)
        self.assertIn("fn_raw_candidates_empty", empty)
        self.assertNotIn("fn_raw_present_no_exact_quote_overlap", empty)
        d["raw_candidates"] = [{"candidate": pred("def", 4)}]
        nonempty = audit.mechanical_labels(c, d)
        self.assertNotIn("fn_raw_candidates_empty", nonempty)
        self.assertIn("fn_raw_present_no_exact_quote_overlap", nonempty)

    def test_ambiguous_raw_quotes_can_touch_gold_without_any_final_anchor(self):
        g = gold("abc", 8)
        c = case("FN", target=g, gi=0)
        d = document(golds=[g], raw=[{"candidate": {"error_type": "A", "spans": [{"text": "abc"}]}}])
        labels = audit.mechanical_labels(c, d)
        self.assertIn("no_anchored_overlap", labels)
        self.assertIn("fn_raw_quote_overlap_without_final_overlap", labels)
        self.assertNotIn("fn_raw_present_no_exact_quote_overlap", labels)

    def test_invalid_anchor_absence_and_ambiguity_can_coexist_without_no_overlap_fp(self):
        p = {"error_type": "A", "spans": [], "invalid_anchor": True,
             "original_spans": [{"text": "abc"}, {"text": "missing"}]}
        labels = audit.mechanical_labels(case("FP", prediction=p, pi=0), document([p], [gold()]))
        self.assertTrue({"invalid_anchor", "invalid_anchor_exact_quote_absent",
                         "invalid_anchor_exact_quote_ambiguous"}.issubset(labels))
        self.assertNotIn("no_anchored_overlap", labels)

    def test_unscorable_gold_is_not_a_business_counterpart_for_fp(self):
        p, g = pred(), gold(scorable=False)
        labels = audit.mechanical_labels(case("FP", prediction=p, pi=0), document([p], [g]))
        self.assertIn("no_anchored_overlap", labels)
        self.assertNotIn("one_to_one_competition", labels)


class CohortTests(unittest.TestCase):
    def setUp(self):
        self.fp = case("FP", prediction=pred(), pi=0)
        self.fn = case("FN", target=gold(), gi=0, did="other")
        self.tp = case("TP", prediction=pred(), target=gold(), pi=1, gi=1)
        self.canonical = [self.fp, self.fn, self.tp]
        self.selection = {"cases": [selection_row(self.fp, "C001", "diagnostic"),
                                     selection_row(self.tp, "C002", "control"),
                                     selection_row(self.fn, "C003", "supplemental")]}

    def test_valid_supplement_is_in_expanded_failure_denominator_only(self):
        groups = audit.selected_cohorts(self.selection, self.canonical)
        self.assertEqual([len(groups[n]) for n in ("baseline", "supplemental", "controls", "expanded")], [1, 1, 1, 2])
        self.assertTrue(all(c["result"] != "TP" for c in groups["expanded"]))

    def test_duplicate_or_invalid_supplement_is_rejected(self):
        duplicate = deepcopy(self.selection)
        duplicate["cases"].append({**duplicate["cases"][0], "case_id": "C004", "cohort": "supplemental"})
        with self.assertRaisesRegex(ValueError, "Duplicate selected identity"):
            audit.selected_cohorts(duplicate, self.canonical)
        invalid = deepcopy(self.selection)
        invalid["cases"][-1]["gold_index"] = 99
        with self.assertRaisesRegex(ValueError, "not in frozen"):
            audit.selected_cohorts(invalid, self.canonical)
        mislabeled = deepcopy(self.selection)
        mislabeled["cases"][1]["cohort"] = "supplemental"
        with self.assertRaisesRegex(ValueError, "Cohort/result"):
            audit.selected_cohorts(mislabeled, self.canonical)


class FrozenAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.temp.name)
        cls.selection = audit.read(PACK / "selection.json")
        (cls.out / "selection.json").write_text(json.dumps(cls.selection, ensure_ascii=False), encoding="utf-8")
        cls.inventory = {str(p.relative_to(PACKAGE)): (p.stat().st_size, p.stat().st_mtime_ns)
                         for p in (PACKAGE / "source").rglob("*") if p.is_file()}
        proc = subprocess.run([sys.executable, "-X", "utf8", "-B", str(TOOL), "--package", str(PACKAGE), "--pack", str(cls.out)],
                              capture_output=True, encoding="utf-8")
        if proc.returncode:
            raise AssertionError(proc.stderr)
        cls.report = audit.read(cls.out / "typicality.json")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_frozen_counts_strata_and_control_denominators(self):
        r = self.report
        self.assertTrue(r["provenance"]["frozen_case_replay_exact"])
        self.assertEqual(r["population"]["results"]["full"], {"FP": 625, "FN": 512})
        self.assertEqual(r["population"]["results"]["baseline"], {"FP": 28, "FN": 24})
        self.assertEqual(r["population"]["tp_controls"], 12)
        expected = sum(c["cohort"] == "supplemental" for c in self.selection["cases"])
        self.assertEqual(r["population"]["failures"]["expanded"], 52 + expected)
        self.assertEqual(sum(x["full"] for x in r["category_result_distribution"]), 1137)
        self.assertEqual({x["baseline"] for x in r["category_result_distribution"] if x["full"]}, {2})
        self.assertGreater(r["category_result_total_variation"]["baseline"], .1)

    def test_real_gaps_are_exposed_before_supplement_and_closed_only_when_covered(self):
        r = self.report
        self.assertEqual(len(r["zero_f1_documents"]), 5)
        self.assertEqual(sum(bool(x["baseline_case_ids"]) for x in r["zero_f1_documents"]), 1)
        long = next(x for x in r["distributions"]["length"] if x["bucket"] == "4000–7999")
        self.assertEqual((long["full_failures"], long["baseline_failures"]), (34, 0))
        mech = {x["label"]: x for x in r["mechanical_labels"]}
        self.assertEqual((mech["fn_raw_candidates_empty"]["full"], mech["fn_raw_candidates_empty"]["baseline"]), (3, 0))
        self.assertEqual((mech["fn_raw_quote_overlap_without_final_overlap"]["full"], mech["fn_raw_quote_overlap_without_final_overlap"]["baseline"]), (33, 0))
        for name in audit.CORE:
            self.assertGreater(mech[name]["baseline"], 0)
        self.assertLessEqual(len(r["supplement_recommendations"]), 8)
        self.assertEqual(r["remaining_after_recommendations"], [])
        self.assertTrue(all(x["identity"][1] in {"FP", "FN"} for x in r["supplement_recommendations"]))

    def test_repeatable_compact_artifact_and_frozen_source_untouched(self):
        proc = subprocess.run([sys.executable, "-X", "utf8", "-B", str(TOOL), "--package", str(PACKAGE), "--pack", str(self.out)],
                              capture_output=True, encoding="utf-8")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.report, audit.read(self.out / "typicality.json"))
        text = (self.out / "typicality.json").read_text(encoding="utf-8")
        self.assertNotIn('"content":', text)
        self.assertNotIn('"spans":', text)
        self.assertLess(len(text), 100000)
        after = {str(p.relative_to(PACKAGE)): (p.stat().st_size, p.stat().st_mtime_ns)
                 for p in (PACKAGE / "source").rglob("*") if p.is_file()}
        self.assertEqual(self.inventory, after)

    def test_frozen_directory_is_not_a_valid_output_location(self):
        proc = subprocess.run([sys.executable, "-X", "utf8", "-B", str(TOOL), "--package", str(PACKAGE), "--pack", str(PACKAGE / "do-not-write-audit")],
                              capture_output=True, encoding="utf-8")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("outside frozen package", proc.stderr)
        self.assertFalse((PACKAGE / "do-not-write-audit").exists())


if __name__ == "__main__":
    unittest.main()
