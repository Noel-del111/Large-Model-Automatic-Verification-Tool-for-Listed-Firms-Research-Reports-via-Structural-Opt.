"""Generalized extraction and canonical/legacy entrypoint regressions."""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from yjcheck.claim_extract import extract_claims
from yjcheck.intrinsic import check_fact_consistency
from yjcheck.models import Block, Document, Evidence, Fact
from yjcheck.rules import check_facts
from yjcheck.source_extract import extract_source_facts

ROOT = Path(__file__).resolve().parents[2]


def document(*texts, role="report", period="2024FY"):
    return Document(role, "a" * 64, "run", role + ".docx", role, "测试公司", period,
                    [Block(f"b{i}", text, paragraph=i + 1) for i, text in enumerate(texts)])


class GenericMetricTests(unittest.TestCase):
    def test_unknown_metric_suffix_cannot_inherit_base_metric_confirmation(self):
        for label in ("存货跌价准备", "营业收入增量", "营业收入占比", "存货周转率", "存货（跌价准备）"):
            claims = extract_claims(document(f"2024年末{label}为1亿元。"))
            self.assertTrue(claims, label)
            self.assertTrue(all("unverified_metric_modifier" in c.warnings for c in claims), label)
            source = document("2024年度合并资产负债表", "单位：亿元", "项目 2024年12月31日", "存货 10", role="source")
            self.assertTrue(all(f.status == "needs_review" for f in check_facts(claims, extract_source_facts(source))), label)

    def test_compound_table_metric_is_not_extracted_as_base_metric(self):
        from yjcheck.source_extract import _metric
        for label in ("存货跌价准备", "营业收入增量", "营业收入占比", "存货周转率", "存货（跌价准备）"):
            self.assertIsNone(_metric(label + " 1亿元"), label)
        self.assertEqual(_metric("存货 10亿元")[0], "inventory")

    def test_explicit_restatement_grammar_remains_supported(self):
        claims = extract_claims(document("2024年末资本公积的影响金额为1亿元。",
                                         "2024年末资产总计由重述前的13亿元增至重述后的17亿元。"))
        self.assertEqual(len(claims), 3)
        self.assertTrue(all("unverified_metric_modifier" not in c.warnings for c in claims))
        self.assertEqual([c.basis for c in claims], ["change", "before", "after"])

    def test_shared_prose_table_labels_preserve_original_names(self):
        report = document("2024年度营业成本为80万元，营业利润为20万元。", "2024年末负债合计为50万元，所有者权益合计为100万元。")
        source = document("2024年度合并利润表", "单位：万元", "项目 2024年度", "营业成本 80", "营业利润 20",
                          "合并资产负债表", "单位：万元", "项目 2024年12月31日", "负债合计 50", "所有者权益合计 100", role="source")
        claims = extract_claims(report)
        sources = extract_source_facts(source)
        expected = {"operating_cost", "operating_profit", "total_liabilities", "total_equity"}
        self.assertEqual({f.metric for f in claims}, expected)
        self.assertTrue(expected <= {f.metric for f in sources})
        for fact in claims + sources:
            if fact.metric in expected:
                self.assertTrue(fact.attributes["metric_label"])
        self.assertEqual([f.status for f in check_facts(claims, sources)], ["no_issue"] * 4)

    def test_registered_aliases_prose_table_and_rule_agree(self):
        report = document("2024年度经营活动现金流量净额为5万元。")
        source = document("2024年度合并现金流量表", "单位：万元", "项目 2024年度", "经营活动产生的现金流量净额 5", role="source")
        self.assertEqual(check_facts(extract_claims(report), extract_source_facts(source))[0].status, "no_issue")

    def test_distinct_total_revenue_and_revenue_are_not_merged(self):
        report = document("2024年度营业总收入为120万元，营业收入为100万元。")
        claims = extract_claims(report)
        self.assertEqual([f.metric for f in claims], ["revenue_total", "revenue"])
        self.assertEqual(check_fact_consistency(claims), [])

    def test_parent_scope_and_quarter_balance_period(self):
        claims = extract_claims(document("2024年第三季度母公司负债合计为50万元，归母净利润为10万元。"))
        self.assertEqual((claims[0].scope, claims[0].period), ("parent", "2024-09-30"))
        self.assertEqual(claims[1].scope, "consolidated")

    def test_prose_table_conflict_retains_both_locations(self):
        doc = document("2024年度营业成本为90万元。", "2024年度合并利润表", "单位：万元", "项目 2024年度", "营业成本 80")
        claims = extract_claims(doc) + [f for f in extract_source_facts(doc) if f.metric == "operating_cost"]
        conflicts = check_fact_consistency(claims)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0].status, "needs_review")
        self.assertEqual({f.value for f in conflicts[0].evidence}, {"90", "80"})
        self.assertEqual({f.evidence[0].paragraph for f in conflicts[0].evidence}, {1, 5})

    def test_period_scope_rounding_and_unknown_model_candidates_do_not_conflict(self):
        claims = extract_claims(document("2024年度营业成本为1.23亿元。", "2024年度营业成本为12345万元。", "2023年度营业成本为80万元。", "2024年度母公司营业成本为50万元。"))
        self.assertEqual(check_fact_consistency(claims), [])
        unknown = Fact("custom_metric", "1", "元", "2024FY", "测试公司", basis="reported",
                       evidence=[Evidence("x", "r", "a" * 64, "x.docx", "b", paragraph=1)])
        other = Fact("custom_metric", "2", "元", "2024FY", "测试公司", basis="reported", evidence=unknown.evidence)
        self.assertEqual(check_fact_consistency([unknown, other]), [])
        claims[1].value = "99999"
        claims[1].attributes["extraction"] = "model-candidate"
        self.assertEqual(check_fact_consistency(claims), [])

    def test_ocr_arithmetic_cannot_reassign_column_meaning(self):
        facts = extract_source_facts(document("2024年度合并利润表", "单位：万元", "项目 调整前 调整后 影响金额", "营业成本 20 80 100", role="source"))
        costs = [f for f in facts if f.metric == "operating_cost"]
        self.assertEqual({f.basis: f.value for f in costs}, {"before": "20", "after": "80", "change": "100"})
        self.assertTrue(all("restatement_arithmetic_inconsistent" in f.warnings for f in costs))

    def test_raw_metric_whitespace_survives(self):
        facts = extract_claims(document("2024年度营业 成本为80万元。"))
        self.assertEqual(facts[0].attributes["metric_label"], "营业 成本")


class CompatibilityTests(unittest.TestCase):
    def test_legacy_pythonpath_loads_canonical_sources(self):
        with tempfile.TemporaryDirectory() as work:
            env = dict(os.environ, PYTHONPATH=os.pathsep.join(str(ROOT / "repo" / area / "src") for area in ("factcheck", "pdfparse")))
            command = "import json,yjcheck.pipeline,yjparse.pipeline; print(json.dumps([yjcheck.pipeline.__file__,yjparse.pipeline.__file__]))"
            paths = json.loads(subprocess.check_output([sys.executable, "-c", command], cwd=work, env=env, text=True))
            self.assertEqual([Path(p).resolve() for p in paths], [ROOT / area / "src" / package / "pipeline.py" for area, package in (("factcheck", "yjcheck"), ("pdfparse", "yjparse"))])

    def test_legacy_cli_preserves_relative_output_argument_from_other_cwd(self):
        with tempfile.TemporaryDirectory() as work:
            env = dict(os.environ, PYTHONPATH="")
            output = Path(work) / "relative-output"
            output.mkdir()
            (output / "check_result.json").write_text('{"run_id":"compat"}', encoding="utf-8")
            (output / "findings.csv").write_text("status\n", encoding="utf-8")
            (output / "report.md").write_text("compatibility fixture\n", encoding="utf-8")
            manifest = {"run_id": "compat", "files": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir()}}
            (output / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            results = [subprocess.run([sys.executable, str(ROOT / prefix / "factcheck/run.py"), "verify", "relative-output"],
                                      cwd=work, env=env, text=True, capture_output=True)
                       for prefix in (".", "repo")]
            self.assertEqual(results[0].returncode, results[1].returncode)
            self.assertEqual(results[0].stdout, results[1].stdout)
            self.assertEqual(results[0].returncode, 0, results[0].stderr)
            self.assertEqual(results[0].stdout.strip(), "verified")


if __name__ == "__main__":
    unittest.main()
