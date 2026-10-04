"""Regression tests for evals/analyze_predictions.py; no network or model calls."""
import json
import tempfile
import unittest
from pathlib import Path

from evals import analyze_predictions as ap


def span(text, start):
    return {"start": start, "end": start + len(text), "text": text}


class LoadingTests(unittest.TestCase):
    def test_directory_and_single_file_inputs_both_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.json").write_text(json.dumps(
                {"document_id": "a", "errors": [{"error_type": "模糊语言", "spans": [span("正文", 0)]}]},
                ensure_ascii=False), encoding="utf-8")
            (root / "b.json").write_text(json.dumps(
                {"document_id": "b", "errors": []}, ensure_ascii=False), encoding="utf-8")
            docs = ap.load_prediction_docs(str(root))
            self.assertEqual(set(docs), {"a", "b"})
            joined = root.parent / "joined.json"
            joined.write_text(json.dumps(list(docs.values()), ensure_ascii=False), encoding="utf-8")
            self.assertEqual(set(ap.load_prediction_docs(str(joined))), {"a", "b"})

    def test_missing_input_fails_loudly(self):
        with self.assertRaises(FileNotFoundError):
            ap.load_gold_docs("definitely/not/here.json")

    def test_duplicate_document_ids_are_rejected(self):
        row = {"document_id": "a", "errors": []}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dup.jsonl"
            path.write_text(json.dumps(row, ensure_ascii=False) + "\n" + json.dumps(row, ensure_ascii=False),
                            encoding="utf-8")
            with self.assertRaises(ValueError):
                ap.load_gold_docs(str(path))

    def test_answers_accept_error_type_or_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gold.json"
            path.write_text(json.dumps({"document_id": "a", "errors": [
                {"type": "计算错误", "spans": [span("1+1=3", 0)], "scorable": True},
                {"error_type": "计算错误", "spans": [span("2+2=5", 6)], "scorable": False},
            ]}, ensure_ascii=False), encoding="utf-8")
            docs = ap.load_gold_docs(str(path))
            self.assertEqual(docs["a"]["errors"][0]["error_type"], "计算错误")
            self.assertTrue(docs["a"]["errors"][0]["scorable"])
            self.assertFalse(docs["a"]["errors"][1]["scorable"])


class ScoringTests(unittest.TestCase):
    SAMPLE = "甲公司2024年营业收入100万元，净利润20万元。"

    def _gold(self, text, start, kind="数值不一致错误"):
        return {"document_id": "doc1", "errors": [{"error_type": kind, "spans": [span(text, start)], "scorable": True}]}

    def _preds(self, errors):
        return {"doc1": {"document_id": "doc1", "errors": errors}}

    def test_full_typed_containment_counts_tp_fp_fn(self):
        gold = self._gold("100万元", self.SAMPLE.index("100万元"))
        preds = self._preds([
            {"error_type": "数值不一致错误", "spans": [span("营业收入100万元", self.SAMPLE.index("营业收入"))]},
            {"error_type": "模糊语言", "spans": [span("净利润", self.SAMPLE.index("净利润"))]},
        ])
        scored = ap.score_docs(preds, {"doc1": gold})
        self.assertEqual((scored["tp"], scored["fp"], scored["fn"]), (1, 1, 0))
        self.assertEqual(scored["missing_prediction_documents"], [])

    def test_wrong_type_never_counts_as_tp_and_missing_doc_counts_fn(self):
        gold = self._gold("100万元", self.SAMPLE.index("100万元"))
        preds = self._preds([{"error_type": "计算错误", "spans": [span("100万元", self.SAMPLE.index("100万元"))]}])
        scored = ap.score_docs(preds, {"doc1": gold, "doc2": {"document_id": "doc2", "errors": [
            {"error_type": "数值不一致错误", "spans": [span("正文", 0)], "scorable": True}]}})
        self.assertEqual((scored["tp"], scored["fp"], scored["fn"]), (0, 1, 2))
        self.assertEqual(scored["missing_prediction_documents"], ["doc2"])

    def test_unscorable_gold_is_never_converted_to_tp(self):
        gold = {"document_id": "doc1", "errors": [
            {"error_type": "数值不一致错误", "spans": [span("100万元", self.SAMPLE.index("100万元"))], "scorable": False}]}
        preds = self._preds([{"error_type": "数值不一致错误", "spans": [span("100万元", self.SAMPLE.index("100万元"))]}])
        scored = ap.score_docs(preds, {"doc1": gold})
        self.assertEqual(scored["tp"], 0)

    def test_per_type_reports_each_fifteen_class_separately(self):
        gold = self._gold("100万元", self.SAMPLE.index("100万元"), kind="数值单位错误")
        preds = self._preds([{"error_type": "数值单位错误",
                              "spans": [span("营业收入100万元", self.SAMPLE.index("营业收入"))]}])
        result = ap.analyze({"doc1": gold}, None, preds)
        unit = result["combined"]["per_type"]["数值单位错误"]
        self.assertEqual((unit["tp"], unit["fp"], unit["fn"]), (1, 0, 0))
        other = result["combined"]["per_type"]["计算错误"]
        self.assertEqual(other["per_doc"], [])

    def test_focus_misses_separate_wrong_type_partial_and_absent_predictions(self):
        gold = {"doc1": {"document_id": "doc1", "errors": [
            {"error_type": "金融要素缺失", "spans": [span("甲乙丙", 0)], "scorable": True},
            {"error_type": "术语误用", "spans": [span("丁戊己", 10)], "scorable": True},
            {"error_type": "冗余语句", "spans": [span("庚辛壬", 20)], "scorable": True},
        ]}}
        preds = {"doc1": {"document_id": "doc1", "errors": [
            {"error_type": "数值缺失", "spans": [span("甲乙丙", 0)]},
            {"error_type": "术语误用", "spans": [span("丁戊", 10)]},
        ]}}
        audit = ap.focus_miss_audit(preds, gold)
        self.assertEqual(audit["total"], 3)
        categories = {case["error_type"]: case["category"] for case in audit["cases"]}
        self.assertEqual(categories, {"金融要素缺失": "定位包含但类型不同",
                                      "术语误用": "同类型定位不完整",
                                      "冗余语句": "无重叠提示"})

    def test_focus_miss_does_not_call_same_type_matching_competition_wrong_type(self):
        item = {"error_type": "冗余语句", "spans": [span("重复", 0)], "scorable": True}
        gold = {"doc1": {"document_id": "doc1", "errors": [item, dict(item)]}}
        preds = {"doc1": {"document_id": "doc1", "errors": [
            {"error_type": "冗余语句", "spans": [span("重复", 0)]}]}}
        audit = ap.focus_miss_audit(preds, gold)
        self.assertEqual(audit["cases"][0]["category"], "同类型一对一匹配竞争")


class AuditAndDeltaTests(unittest.TestCase):
    def test_auto_confirm_audit_lists_tp_and_fp_origins(self):
        source = "日期为2023年2月30日。"
        gold = {"document_id": "doc1", "errors": [{"error_type": "时间信息非法",
                "spans": [span("2023年2月30日", source.index("2023年2月30日"))], "scorable": True}]}
        preds = {"doc1": {"document_id": "doc1", "errors": [
            {"error_type": "时间信息非法", "spans": [span("2023年2月30日", source.index("2023年2月30日"))],
             "status": "confirmed_error", "detector_id": "verified.calendar", "reason": "无效日期"},
            {"error_type": "时间信息非法", "spans": [span("日期为", source.index("日期为"))],
             "status": "confirmed_error", "detector_id": "hybrid.model", "reason": "重复且错误的锚点"},
        ]}}
        audit = ap.auto_confirm_audit(preds, {"doc1": gold})
        self.assertEqual(audit["confirmed_total"], 2)
        self.assertEqual(audit["confirmed_fp"], 1)
        self.assertEqual(audit["mismatches"][0]["error_type"], "时间信息非法")

    def test_review_burden_counts_pending_by_type_and_priority(self):
        preds = {"doc1": {"document_id": "doc1", "errors": [
            {"error_type": "模糊语言", "status": "needs_review", "review_priority": "low",
             "spans": [span("正文", 0)]},
            {"error_type": "数值不一致错误", "status": "needs_review", "review_priority": "high",
             "spans": [span("正文2", 10)]},
            {"error_type": "计算错误", "status": "confirmed_error", "review_priority": "confirmed",
             "spans": [span("正文3", 20)]},
        ]}}
        burden = ap.review_burden(preds)
        self.assertEqual(burden["pending_total"], 2)
        self.assertEqual(burden["by_priority"], {"high": 1, "low": 1, "unspecified": 0})
        self.assertEqual(burden["by_type"]["模糊语言"], 1)

    def test_delta_isolates_combined_vs_direct_changes_per_doc(self):
        source = "甲公司2024年营业收入100万元。"
        gold = {"doc1": {"document_id": "doc1", "errors": [{"error_type": "数值单位错误",
                "spans": [span("100万元", source.index("100万元"))], "scorable": True}]}}
        direct = {"doc1": {"document_id": "doc1", "errors": [{"error_type": "数值单位错误",
                           "spans": [span("100万元", source.index("100万元"))]}]}}
        combined = {"doc1": {"document_id": "doc1", "errors": [
            {"error_type": "数值单位错误", "spans": [span("100万元", source.index("100万元"))]},
            {"error_type": "模糊语言", "spans": [span("营业收入", source.index("营业收入"))]},
        ]}}
        result = ap.analyze({"doc1": gold}, direct, combined)
        row = result["delta"]["per_doc"][0]
        self.assertEqual((row["tp_delta"], row["fp_delta"], row["fn_delta"]), (0, 1, 0))
        self.assertEqual((result["delta"]["total_tp_delta"], result["delta"]["total_fp_delta"]), (0, 1))

    def test_emit_writes_summary_and_csv_without_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            per_type = {kind: {"tp": 0, "fp": 0, "fn": 0, "precision": 0.0, "recall": 0.0,
                               "f1": 0.0, "per_doc": [], "missing_prediction_documents": []}
                        for kind in ap.FINED_TYPES}
            per_type["计算错误"] = {**per_type["计算错误"], "tp": 1, "fn": 0, "precision": 1.0,
                                    "recall": 1.0, "f1": 1.0,
                                    "per_doc": [{"document_id": "d", "tp": 1, "fp": 0, "fn": 0,
                                                 "n_pred": 1, "n_gold": 1, "precision": 1.0,
                                                 "recall": 1.0, "f1": 1.0}]}
            result = {"combined": {"overall": {"tp": 1, "fp": 0, "fn": 0, "precision": 1.0, "recall": 1.0,
                                               "f1": 1.0, "per_doc": [{"document_id": "d", "tp": 1, "fp": 0,
                                                                       "fn": 0, "n_pred": 1, "n_gold": 1,
                                                                       "precision": 1.0, "recall": 1.0, "f1": 1.0}],
                                               "missing_prediction_documents": []},
                      "per_type": per_type,
                      "review_burden": {"pending_total": 0, "by_type": {}, "by_priority": {}},
                      "auto_confirm": {"confirmed_total": 0, "confirmed_tp": 0, "confirmed_fp": 0,
                                       "mismatches": [], "matched": [], "per_type": {
                                           t: {"tp": 0, "fp": 0} for t in ap.FINED_TYPES}}}}
            directory = Path(tmp) / "out"
            ap.emit(result, directory)
            self.assertTrue((directory / "summary.json").is_file())
            self.assertTrue((directory / "combined_per_type.csv").is_file())


if __name__ == "__main__":
    unittest.main()
