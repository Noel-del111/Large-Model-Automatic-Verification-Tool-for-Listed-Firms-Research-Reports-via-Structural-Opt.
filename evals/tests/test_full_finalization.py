"""A completed report must never conceal an interrupted or invalid full run."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from evals import finalize_full_acceptance as finalizer


class FullFinalizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.out = self.data / "report.md"

    def scores(self):
        for split in ("eval_oct05", "dev"):
            path = self.data / ("full-" + split) / "score.json"
            path.parent.mkdir()
            path.write_text(json.dumps({"run_config": {"mode": "model"},
                                        "detectors": {name: {} for name in ("legacy_rules", "model_direct", "hybrid")}}),
                            encoding="utf-8")

    def audited(self, complete=True):
        return {"integrity_problems": [], "runs": {}, "created_at": "2026-10-03T00:00:00Z",
                "full_model_execution_and_scoring_complete": complete,
                "budget": {"limit_cny": 50, "accounted_including_reserved_cny": 12,
                           "calls": 200, "call_status_counts": {"ok": 199, "error": 1}}}

    def test_partial_scores_cannot_be_published_as_full_report(self):
        with patch.object(finalizer, "audit") as audit:
            with self.assertRaisesRegex(ValueError, "尚未生成"):
                finalizer.collect(self.data, self.out)
        audit.assert_not_called()
        self.assertFalse(self.out.exists())

    def test_integrity_failure_blocks_report_even_if_complete_flag_is_true(self):
        self.scores()
        result = self.audited()
        result["runs"] = {"dev": {"integrity_problems": ["missing_planned_denominator"]}}
        with patch.object(finalizer, "audit", return_value=result):
            with self.assertRaisesRegex(ValueError, "missing_planned_denominator"):
                finalizer.collect(self.data, self.out)
        self.assertFalse(self.out.exists())
        self.assertTrue((self.data / "full-acceptance-audit.json").exists())

    def test_failed_execution_is_reported_without_claiming_acceptance(self):
        self.scores()
        with patch.object(finalizer, "audit", return_value=self.audited(False)):
            state = finalizer.collect(self.data, self.out)
        report = self.out.read_text(encoding="utf-8")
        self.assertIn("验收尚未通过", report)
        self.assertNotIn("三组执行及评分完整性检查通过", report)
        self.assertEqual(state["state"], "reports_ready_with_execution_failures")
        self.assertEqual(state["new_model_calls_by_finalizer"], 0)
        self.assertEqual(set(state["scoring_sha256"]), {"eval_oct05", "dev"})
        self.assertIn("199 篇 10 月 7 日保留集继续封存", report)

    def test_complete_report_keeps_scope_and_auditable_hashes(self):
        self.scores()
        with patch.object(finalizer, "audit", return_value=self.audited()):
            state = finalizer.collect(self.data, self.out)
        report = self.out.read_text(encoding="utf-8")
        self.assertTrue(report.startswith("# 第二版全量模型评测报告"))
        self.assertIn("不混合为独立测试分数", report)
        self.assertEqual(state["state"], "reports_ready")
        self.assertEqual(len(state["report_sha256"]), 64)
        saved = json.loads((self.data / "full-finalization-status.json").read_text(encoding="utf-8"))
        self.assertEqual(saved, state)

    def test_eval_only_handoff_does_not_claim_deferred_full_run_complete(self):
        self.scores()
        (self.data / "full-dev/score.json").unlink()
        result = self.audited(False)
        result["runs"] = {"eval_oct05": {"state": "complete", "integrity_problems": []},
                          "dev": {"state": "not_started", "integrity_problems": []}}
        with patch.object(finalizer, "audit", return_value=result):
            state = finalizer.collect(self.data, self.out, "eval200")
        self.assertTrue(state["scope_execution_and_scoring_complete"])
        self.assertFalse(state["full_model_execution_and_scoring_complete"])
        self.assertEqual(set(state["scoring_sha256"]), {"eval_oct05"})
        self.assertIn("598 篇开发扩展未执行", self.out.read_text(encoding="utf-8"))
        report = self.out.read_text(encoding="utf-8")
        self.assertIn("最终独立测试尚未进行", report)
        self.assertIn("没有训练或微调模型权重", report)
        self.assertIn("历史划分 ID `eval_oct05`", report)
        self.assertTrue((self.data / "eval200-finalization-status.json").is_file())

    def test_terminal_worker_without_scores_stops_instead_of_waiting_forever(self):
        worker = Mock()
        worker.alive.return_value = False
        with patch.object(finalizer.time, "sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "不自动重启"):
                finalizer.wait_for_reports(self.data, worker, 30)
        sleep.assert_not_called()

    def test_observation_failure_is_not_treated_as_completed_worker(self):
        worker = Mock()
        worker.alive.side_effect = OSError("cannot observe")
        with patch.object(finalizer.time, "sleep") as sleep:
            with self.assertRaisesRegex(OSError, "cannot observe"):
                finalizer.wait_for_reports(self.data, worker, 30)
        sleep.assert_not_called()

    def test_final_write_racing_with_worker_exit_is_accepted(self):
        worker = Mock()
        worker.alive.return_value = False
        with patch.object(finalizer, "reports_available", side_effect=[False, True]):
            finalizer.wait_for_reports(self.data, worker, 30)


if __name__ == "__main__":
    unittest.main()
