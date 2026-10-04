from __future__ import annotations

import json
import unittest

from yjcheck.text_context import estimated_input_tokens, missing_ranges, text_windows, text_windows_token_budget
from yjcheck.text_review import detect_text, _messages
from yjcheck.text_taxonomy import FINED_ERROR_TYPES, canonical_error_type


class MockChat:
    def __init__(self, callback=None):
        self.calls = []
        self.callback = callback or (lambda payload, purpose: [])

    def __call__(self, messages, *, purpose):
        payload = json.loads(messages[-1]["content"])
        self.calls.append((messages, purpose))
        errors = self.callback(payload, purpose)
        return {"content": json.dumps({"errors": errors}, ensure_ascii=False),
                "trace": {"provider": "unit-test", "call": len(self.calls)}}


def error_at(text, source, kind="模糊语言", start=None):
    index = source.index(text) if start is None else start
    return {"error_type": kind, "spans": [{"start": index, "end": index + len(text), "text": text}],
            "reason": "这是用于验证输出契约的候选。"}


class TextContractTests(unittest.TestCase):
    def test_priority_miss_types_have_explicit_contrastive_boundaries(self):
        messages = _messages([], "focus-guidance", "", [])
        prompt = messages[0]["content"]
        for phrase in ("金融要素缺失只在", "若只是明确数值空槽", "术语误用必须指出",
                       "冗余语句必须能定位", "同一重复问题用一个error和多个span"):
            self.assertIn(phrase, prompt)

    def test_markdown_table_amounts_are_not_bound_to_a_ratio_heading(self):
        source = "毛利及毛利率分析\n\n单位：万元、%\n\n| 项目 | 金额 | 占比 |\n| --- | --- | --- |\n| 主营业务 | 100 | 90 |\n\n经营情况平稳。"
        result = detect_text(source, document_id="table-boundary", chat=MockChat())
        self.assertEqual(result["errors"], [])

    def test_structured_rule_blocks_keep_global_source_offsets(self):
        source = "标题\n\n| 项目 | 金额 |\n| --- | --- |\n| 收入 | 100 |\n\n甲公司、乙公司分别为1%、2%、3%。"
        result = detect_text(source, document_id="structured-offset", chat=MockChat())
        self.assertTrue(result["errors"])
        for error in result["errors"]:
            for span in error["spans"]:
                self.assertEqual(source[span["start"]:span["end"]], span["text"])
                self.assertGreater(span["start"], source.index("甲公司")-1)

    def test_negated_and_corrected_examples_are_never_automatically_confirmed(self):
        for source in ("说明：错误示例为2023年2月30日，请勿填写该日期。",
                       "说明：‘1+2=4’是错误算式，正确结果为3。",
                       "不要写成毛利率为5万元，这是错误示例。"):
            result = detect_text(source, document_id="counterexample", chat=MockChat())
            self.assertTrue(result["errors"])
            self.assertTrue(all(e["status"] == "needs_review" for e in result["errors"]))
            self.assertTrue(any(e["validation"] == "context_requires_review" for e in result["errors"]))

    def test_unique_whitespace_quote_recovers_original_source_for_both_arms(self):
        source = "价格为\u00a0 100 元。"
        raw = {"error_type": "模糊语言", "spans": [{"text": "价格为 100 元。"}], "reason": "待核查"}
        for detector in ("model_direct", "hybrid"):
            result = detect_text(source, document_id="whitespace", detector=detector,
                                 chat=MockChat(lambda payload, purpose: [raw]))
            self.assertEqual(result["errors"][0]["spans"], [{"start": 0, "end": len(source), "text": source}])
            self.assertEqual(result["errors"][0]["status"], "needs_review")
            self.assertTrue(result["coverage"]["candidate_quality_complete"])

    def test_ambiguous_whitespace_quote_is_not_assigned_arbitrary_location(self):
        source = "价格为\u00a0100元。价格为  100元。"
        raw = {"error_type": "模糊语言", "spans": [{"text": "价格为 100元。"}], "reason": "待核查"}
        result = detect_text(source, document_id="whitespace-ambiguous", detector="hybrid",
                             chat=MockChat(lambda payload, purpose: [raw]))
        self.assertEqual(result["errors"], [])
        self.assertEqual(len(result["rejected_candidates"]), 1)
        self.assertFalse(result["coverage"]["candidate_quality_complete"])

    def test_empty_model_output_is_valid_and_not_a_parse_failure(self):
        chat = MockChat()
        result = detect_text("本年度经营平稳。", document_id="empty-errors", chat=chat)
        self.assertEqual(result["errors"], [])
        self.assertTrue(result["coverage"]["complete"])
        self.assertTrue(result["coverage"]["model_ran"])
        self.assertEqual(len(chat.calls), 1)

    def test_empty_source_has_no_invented_error_or_model_call(self):
        chat = MockChat()
        result = detect_text("", document_id="blank", chat=chat)
        self.assertEqual(result["errors"], [])
        self.assertEqual(chat.calls, [])
        self.assertEqual(result["coverage"]["total_chars"], 0)

    def test_offline_is_explicit_and_never_claims_model_success(self):
        result = detect_text("2025年2月30日", document_id="offline")
        self.assertFalse(result["coverage"]["complete"])
        self.assertFalse(result["coverage"]["model_ran"])
        self.assertTrue(result["coverage"]["rules_complete"])
        self.assertIn("model_unavailable_offline_rules_only", result["coverage"]["truncation_reasons"])

    def test_missing_errors_is_not_a_clean_model_result(self):
        result = detect_text("待核查原文", document_id="malformed",
                             chat=lambda messages, *, purpose: {"content": "{}", "trace": {}})
        self.assertFalse(result["coverage"]["complete"])
        self.assertEqual(result["coverage"]["processed_chars"], 0)
        self.assertEqual(result["traces"][0]["status"], "failed")

    def test_parse_failure_keeps_paid_call_trace(self):
        result = detect_text("待核查原文", document_id="trace",
                             chat=lambda messages, *, purpose: {"content": "not json", "trace": {"cost_cny": "0.01"}})
        self.assertFalse(result["coverage"]["complete"])
        self.assertEqual(result["traces"][0]["runtime_trace"]["cost_cny"], "0.01")

    def test_model_direct_preserves_failed_predictions_and_original_offsets(self):
        raw = error_at("不存在的文字", "", start=-9)
        result = detect_text("实际原文", document_id="direct", detector="model_direct",
                             chat=MockChat(lambda payload, purpose: [raw]))
        self.assertEqual(result["errors"][0]["spans"], [])
        self.assertEqual(result["errors"][0]["original_spans"], raw["spans"])
        self.assertTrue(result["errors"][0]["invalid_anchor"])
        self.assertEqual(result["errors"][0]["validation"], "anchor_rejected")
        self.assertEqual(result["errors"][0]["status"], "needs_review")
        self.assertEqual(result["errors"][0]["evidence"], [])
        self.assertTrue(result["coverage"]["execution_complete"])
        self.assertFalse(result["coverage"]["candidate_quality_complete"])
        self.assertFalse(result["coverage"]["complete"])

    def test_different_invalid_direct_candidates_do_not_collapse_by_type(self):
        raw = [error_at("不存在甲", "", start=-9), error_at("不存在乙", "", start=-9)]
        result = detect_text("实际原文", document_id="invalid-distinct", detector="model_direct",
                             chat=MockChat(lambda payload, purpose: raw))
        self.assertEqual(len(result["errors"]), 2)
        self.assertEqual(len({error["id"] for error in result["errors"]}), 2)

    def test_fifteen_types_match_release_labels(self):
        self.assertEqual(len(set(FINED_ERROR_TYPES)), 15)
        for name in ("模糊语言", "不一致条款", "属性值缺失错误"):
            self.assertIn(name, FINED_ERROR_TYPES)
        self.assertEqual(canonical_error_type("Clause Conflict"), "不一致条款")
        self.assertEqual(canonical_error_type("Calculation Error"), "计算错误")


class DeterministicTests(unittest.TestCase):
    def test_legacy_does_not_run_new_calendar_or_calculation_checks(self):
        result = detect_text("2025年2月30日；1+1=3。", document_id="old", detector="legacy_rules")
        self.assertEqual(result["errors"], [])
        self.assertTrue(result["coverage"]["complete"])
        self.assertFalse(result["coverage"]["requested_model"])

    def test_legacy_current_intrinsic_candidates_remain_for_review(self):
        result = detect_text("报告期间为2017-2014年。", document_id="old-time", detector="legacy_rules")
        self.assertTrue(result["errors"])
        self.assertTrue(all(error["status"] == "needs_review" for error in result["errors"]))
        self.assertTrue(all(error["detector_id"].startswith("legacy.") for error in result["errors"]))

    def test_calendar_supports_leap_year_and_exact_source_evidence(self):
        text = "2024年2月29日有效；2025年2月29日无效。"
        errors = detect_text(text, document_id="dates")["errors"]
        dates = [error for error in errors if error["error_type"] == "时间信息非法"]
        self.assertEqual(len(dates), 1)
        self.assertEqual(dates[0]["status"], "confirmed_error")
        self.assertEqual(dates[0]["spans"][0]["text"], "2025年2月29日无效。")
        self.assertEqual(dates[0]["verification_spans"][0]["text"], "2025年2月29日")
        self.assertEqual(dates[0]["evidence"][0]["text"], "2025年2月29日")
        for span in dates[0]["spans"] + dates[0]["verification_spans"]:
            self.assertEqual(text[span["start"]:span["end"]], span["text"])

    def test_invalid_month_and_day_ranges_are_confirmed(self):
        text = "截至2024年13月成立；截至1月41日；6月40日公告。"
        invalid = [e for e in detect_text(text, document_id="tm")["errors"]
                   if e["error_type"] == "时间信息非法"]
        self.assertEqual(len(invalid), 3)
        self.assertTrue(all(e["status"] == "confirmed_error" for e in invalid))
        # 合法边界不报
        ok = detect_text("2024年12月31日；1月31日。", document_id="tm-ok")["errors"]
        self.assertFalse(any(e["error_type"] == "时间信息非法" for e in ok))

    def test_decimal_equations_normalize_units_and_rounding(self):
        text = "1亿元+2000万元=1.2亿元；20%-10%=10%；1/3=0.33；-2+1=-1；10万元+20万元=40万元。"
        calculations = [e for e in detect_text(text, document_id="math")["errors"] if e["error_type"] == "计算错误"]
        self.assertEqual(len(calculations), 1)
        self.assertEqual(calculations[0]["spans"][0]["text"], "10万元+20万元=40万元。")
        self.assertEqual(calculations[0]["verification_spans"][0]["text"], "10万元+20万元=40万元")
        self.assertEqual(calculations[0]["evidence"][-1]["expected_display"], "30")

    def test_incompatible_arithmetic_dimensions_are_not_recomputed(self):
        result = detect_text("10万元+20%=30万元", document_id="mixed")
        self.assertFalse(any(e["error_type"] == "计算错误" for e in result["errors"]))

    def test_binary_calculator_never_scores_a_partial_long_expression(self):
        result = detect_text("1+2+3=6；1+2=4-1；100/(20+5)=4；1,00+2=102。", document_id="compound")
        self.assertFalse(any(e["error_type"] == "计算错误" for e in result["errors"]))

    def test_binary_calculator_does_not_parse_scientific_or_symbolic_suffixes(self):
        result = detect_text("1e3+2=1002；0x10+1=17；x1+2=4；1+2=4e0。", document_id="unsupported-math")
        self.assertFalse(any(e["error_type"] == "计算错误" for e in result["errors"]))

    def test_ratio_currency_mismatch_has_proof_but_high_eps_does_not(self):
        result = detect_text("毛利率为10亿元。基本每股收益600元/股。", document_id="units")
        verified = [e for e in result["errors"] if e["status"] == "confirmed_error"]
        self.assertEqual(len(verified), 1)
        self.assertEqual(verified[0]["spans"][0]["text"], "毛利率为10亿元。")
        self.assertEqual(verified[0]["verification_spans"][0]["text"], "毛利率为10亿元")
        eps = [e for e in result["errors"] if "600" in str(e["spans"])]
        self.assertTrue(eps)
        self.assertTrue(all(e["status"] == "needs_review" for e in eps))

    def test_redundant_literal_duplicates_are_confirmed(self):
        cases = [
            "风险提示：食品安全风险，食品安全风险，市场竞争加剧。",
            "产品销量快速增长，经营业绩显著改善。产品销量快速增长，经营业绩显著改善。",
            "新增省级新产品试制计划15只，累计288只，累计288只。",
        ]
        for text in cases:
            with self.subTest(text=text):
                redundant = [e for e in detect_text(text, document_id="dup")["errors"]
                             if e["error_type"] == "冗余语句"]
                self.assertEqual(len(redundant), 1)
                self.assertEqual(redundant[0]["status"], "confirmed_error")
                self.assertTrue(redundant[0]["detector_id"].startswith("verified.redundant"))

    def test_redundant_distinct_periods_or_lists_are_not_flagged(self):
        text = ("公司营收同比增长15.3%，净利润同比增长12.5%。"
                "甲、乙、丙三家公司分别上涨1%、2%、3%。"
                "2024年营收100万元，2025年营收200万元。")
        self.assertFalse(any(e["error_type"] == "冗余语句"
                             for e in detect_text(text, document_id="no-dup")["errors"]))

    def test_empty_placeholder_is_confirmed_attribute_missing(self):
        text = "翔楼新材()发布公告；公司发布“”多模态方案；《》纳入年检范围。"
        missing = [e for e in detect_text(text, document_id="ph")["errors"]
                   if e["error_type"] == "属性值缺失错误"]
        self.assertEqual(len(missing), 3)
        self.assertTrue(all(e["status"] == "confirmed_error" for e in missing))
        self.assertTrue(all(e["detector_id"] == "verified.empty_placeholder" for e in missing))

    def test_suspended_punctuation_is_confirmed_factor_missing(self):
        text = "东北亚LNG到岸价格为10.81美元/百万英热，。"
        missing = [e for e in detect_text(text, document_id="sp")["errors"]
                   if e["error_type"] == "金融要素缺失"]
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]["status"], "confirmed_error")
        self.assertEqual(missing[0]["detector_id"], "verified.suspended_punctuation")

    def test_percent_gap_is_confirmed_numeric_missing(self):
        text = "销售额同比增长 %，核心一级资本充足率%。"
        missing = [e for e in detect_text(text, document_id="pg")["errors"]
                   if e["error_type"] == "数值缺失"]
        self.assertEqual(len(missing), 2)
        self.assertTrue(all(e["status"] == "confirmed_error" for e in missing))
        # 合法百分数不得误报
        ok = detect_text("营收同比增长10%，占比5%。", document_id="pg-ok")["errors"]
        self.assertFalse(any(e["error_type"] == "数值缺失" for e in ok))

    def test_stock_code_gap_is_confirmed_format_error(self):
        text = "雪峰科技(603)、隆基绿能(601)。"
        fmt = [e for e in detect_text(text, document_id="sc")["errors"]
               if e["error_type"] == "格式错误"]
        self.assertEqual(len(fmt), 2)
        self.assertTrue(all(e["status"] == "confirmed_error" for e in fmt))
        # 6 位合法代码与年份不报
        ok = detect_text("贵州茅台(600519)。报告期（2024年）。", document_id="sc-ok")["errors"]
        self.assertFalse(any(e["error_type"] == "格式错误" for e in ok))

    def test_unit_gap_is_confirmed_numeric_missing(self):
        text = "1-4月累计销售重卡约辆，全国各类型银行共发行理财产品 只。"
        missing = [e for e in detect_text(text, document_id="ug")["errors"]
                   if e["error_type"] == "数值缺失"]
        self.assertEqual(len(missing), 2)
        self.assertTrue(all(e["status"] == "confirmed_error" for e in missing))
        # 约N单位 / 数字+单位 不误报
        ok = detect_text("销售重卡约10辆，发行理财产品100只。", document_id="ug-ok")["errors"]
        self.assertFalse(any(e["error_type"] == "数值缺失" for e in ok))

    def test_same_name_different_period_company_and_basis_are_not_conflicts(self):
        text = ("甲公司2024年营业收入100万元。甲公司2025年营业收入200万元。"
                "乙公司2024年营业收入300万元。甲公司2024年调整后营业收入400万元。"
                "甲公司2024年营业收入0.01亿元。")
        self.assertFalse(any(e["detector_id"] == "hybrid.cross_section_numeric"
                             for e in detect_text(text, document_id="periods")["errors"]))

    def test_all_verified_rules_return_original_sentence_and_narrow_proof(self):
        sentences = ["报告称结算日为2025年2月30日。", "合计金额按1+1=3计算；", "本期毛利率为10亿元，情况如下。"]
        tokens = ["2025年2月30日", "1+1=3", "毛利率为10亿元"]
        source = "标题\n" + "\n".join(sentences)
        findings = [e for e in detect_text(source, document_id="sentence-contract")["errors"]
                    if e["status"] == "confirmed_error"]
        self.assertEqual(len(findings), 3)
        for sentence, token in zip(sentences, tokens):
            finding = next(e for e in findings if e["verification_spans"][0]["text"] == token)
            self.assertEqual(finding["spans"][0]["text"], sentence)
            self.assertEqual(finding["evidence"][0]["text"], token)
            for span in finding["spans"] + finding["verification_spans"]:
                self.assertEqual(source[span["start"]:span["end"]], span["text"])

    def test_cross_boundary_token_falls_back_to_original_exact_span(self):
        source = "结算日2025年\n2月30日待核查。"
        findings = [e for e in detect_text(source, document_id="multiline-date")["errors"]
                    if e["status"] == "confirmed_error"]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["spans"], findings[0]["verification_spans"])
        self.assertEqual(findings[0]["spans"][0]["text"], "2025年\n2月30日")

    def test_distinct_verified_errors_in_one_sentence_are_not_collapsed(self):
        source = "两个日期为2025年2月30日与2025年4月31日。"
        findings = [e for e in detect_text(source, document_id="two-dates")["errors"]
                    if e["status"] == "confirmed_error"]
        self.assertEqual(len(findings), 2)
        self.assertEqual(len({e["id"] for e in findings}), 2)
        self.assertTrue(all(e["spans"][0]["text"] == source for e in findings))
        self.assertEqual({e["verification_spans"][0]["text"] for e in findings}, {"2025年2月30日", "2025年4月31日"})

    def test_same_entity_and_period_conflict_links_remote_evidence(self):
        text = "甲公司2024年营业收入100万元。\n" + "这里是其他内容。\n" * 100 + "甲公司2024年营业收入200万元。"
        errors = detect_text(text, document_id="remote-offline")["errors"]
        conflicts = [e for e in errors if e["detector_id"] == "hybrid.cross_section_numeric"]
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(len(conflicts[0]["spans"]), 2)
        self.assertEqual(conflicts[0]["status"], "needs_review")


class CandidateValidationTests(unittest.TestCase):
    def test_both_arms_accept_unique_exact_sentence_without_model_offsets(self):
        sentence = "这个承诺不够明确。"
        text = "前段。" + sentence + "末段。"
        raw = {"error_type": "模糊语言", "spans": [{"text": sentence}], "reason": "范围不明确。"}
        for detector in ("model_direct", "hybrid"):
            with self.subTest(detector=detector):
                result = detect_text(text, document_id="optional-offset", detector=detector,
                                     chat=MockChat(lambda payload, purpose: [raw]))
                self.assertEqual(result["errors"][0]["spans"], error_at(sentence, text)["spans"])
                self.assertEqual(result["errors"][0]["original_spans"], [{"text": sentence}])
                self.assertTrue(result["coverage"]["execution_complete"])

    def test_both_arms_apply_identical_source_only_offset_repair(self):
        text = "前段。这个承诺不够明确。末段。"
        raw = error_at("这个承诺不够明确", "", start=999)
        results = [detect_text(text, document_id="shared-anchor", detector=detector,
                   chat=MockChat(lambda payload, purpose: [raw]))
                   for detector in ("model_direct", "hybrid")]
        for result in results:
            found = result["errors"][0]
            self.assertEqual(found["spans"], error_at("这个承诺不够明确", text)["spans"])
            self.assertEqual(found["original_spans"], raw["spans"])
            self.assertEqual(result["raw_candidates"][0]["candidate"], raw)
            self.assertEqual(found["status"], "needs_review")
            self.assertTrue(result["coverage"]["execution_complete"])
            self.assertTrue(result["coverage"]["candidate_quality_complete"])
        self.assertEqual(results[0]["errors"][0]["spans"], results[1]["errors"][0]["spans"])

    def test_both_arms_accept_correct_offsets_for_a_repeated_quote(self):
        text = "相同文本。相同文本。"
        raw = error_at("相同文本", text, start=5)
        for detector in ("model_direct", "hybrid"):
            with self.subTest(detector=detector):
                result = detect_text(text, document_id="repeat-valid", detector=detector,
                                     chat=MockChat(lambda payload, purpose: [raw]))
                self.assertEqual(result["errors"][0]["spans"], raw["spans"])
                self.assertEqual(result["errors"][0]["warnings"], [])
                self.assertTrue(result["coverage"]["complete"])

    def test_both_arms_refuse_to_choose_first_repeated_quote(self):
        text = "相同文本。相同文本。"
        raw = error_at("相同文本", "", start=999)
        for detector in ("model_direct", "hybrid"):
            with self.subTest(detector=detector):
                result = detect_text(text, document_id="repeat-ambiguous", detector=detector,
                                     chat=MockChat(lambda payload, purpose: [raw]))
                self.assertEqual(len(result["rejected_candidates"]), 1)
                self.assertTrue(result["coverage"]["execution_complete"])
                self.assertFalse(result["coverage"]["candidate_quality_complete"])
                self.assertFalse(result["coverage"]["complete"])
                if detector == "model_direct":
                    self.assertEqual(len(result["errors"]), 1)
                    self.assertEqual(result["errors"][0]["spans"], [])
                    self.assertEqual(result["errors"][0]["original_spans"], raw["spans"])
                else:
                    self.assertEqual(result["errors"], [])

    def test_multispan_offset_repair_preserves_one_error(self):
        source = "第一条含糊。中间文字。第二条含糊。"
        raw = {"error_type": "不一致条款", "spans": [
            error_at("第一条含糊", "", start=-1)["spans"][0],
            error_at("第二条含糊", "", start=-1)["spans"][0]], "reason": "两条需共同核查。"}
        for detector in ("model_direct", "hybrid"):
            with self.subTest(detector=detector):
                result = detect_text(source, document_id="multi-repair", detector=detector,
                                     chat=MockChat(lambda payload, purpose: [raw]))
                self.assertEqual(len(result["errors"]), 1)
                self.assertEqual(len(result["errors"][0]["spans"]), 2)
                self.assertEqual(result["errors"][0]["original_spans"], raw["spans"])

    def test_direct_valid_source_anchor_does_not_confirm_model_claim(self):
        text = "2025年2月30日"
        result = detect_text(text, document_id="direct-unverified", detector="model_direct",
                             chat=MockChat(lambda payload, purpose: [error_at(text, text, "时间信息非法")]))
        self.assertEqual(result["errors"][0]["status"], "needs_review")
        self.assertNotIn("deterministic", str(result["errors"][0]["evidence"]))

    def test_wrong_offset_unique_exact_text_is_reanchored(self):
        text = "首段。这个承诺不够明确。末段。"
        raw = error_at("这个承诺不够明确", "", start=999)
        result = detect_text(text, document_id="anchor", chat=MockChat(lambda payload, purpose: [raw]))
        found = result["errors"][0]
        self.assertEqual(found["spans"][0]["start"], text.index("这个承诺"))
        self.assertEqual(found["status"], "needs_review")
        self.assertIn("offset_reanchored_by_unique_exact_text", found["warnings"])

    def test_absent_or_ambiguous_spans_are_not_silently_accepted(self):
        for source, raw in [("实际文本", error_at("虚构文本", "", start=0)),
                            ("相同文本。相同文本。", error_at("相同文本", "", start=999))]:
            result = detect_text(source, document_id="bad-anchor", chat=MockChat(lambda payload, purpose: [raw]))
            self.assertEqual(result["errors"], [])
            self.assertEqual(len(result["rejected_candidates"]), 1)
            self.assertFalse(result["coverage"]["complete"])

    def test_unknown_types_are_visible_rejections(self):
        source = "原始文字"
        raw = error_at(source, source, kind="杜撰类别")
        result = detect_text(source, document_id="unknown", chat=MockChat(lambda payload, purpose: [raw]))
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["rejected_candidates"][0]["reason"], "unknown error type")

    def test_model_status_cannot_confirm_qualitative_claim(self):
        source = "具有一定的增长潜力。"
        raw = {**error_at(source, source), "status": "confirmed_error", "evidence": [{"proof": "trust me"}]}
        result = detect_text(source, document_id="qualitative", chat=MockChat(lambda payload, purpose: [raw]))
        self.assertEqual(result["errors"][0]["status"], "needs_review")
        self.assertNotIn("trust me", str(result["errors"][0]))

    def test_model_error_can_be_confirmed_only_with_computed_proof(self):
        text = "2025年2月30日"
        result = detect_text(text, document_id="proof", chat=MockChat(lambda payload, purpose: [error_at(text, text, "时间信息非法")]))
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(result["errors"][0]["status"], "confirmed_error")
        self.assertIn("deterministic", str(result["errors"][0]["evidence"]))

    def test_overlapping_model_claims_do_not_inherit_narrow_proof(self):
        text = "2025年2月30日，与2025年3月1日。"
        for snippet in (text, "2025年", "30日，与2025年3月1日"):
            with self.subTest(snippet=snippet):
                raw = error_at(snippet, text, "时间信息非法")
                result = detect_text(text, document_id="narrow-proof", chat=MockChat(lambda payload, purpose: [raw]))
                candidates = [e for e in result["errors"] if e["detector_id"] == "hybrid.model"]
                self.assertEqual(len(candidates), 1)
                self.assertEqual(candidates[0]["status"], "needs_review")
                confirmed = [e for e in result["errors"] if e["status"] == "confirmed_error"]
                self.assertEqual(len(confirmed), 1)
                self.assertEqual(confirmed[0]["spans"][0]["text"], text)
                self.assertEqual(confirmed[0]["verification_spans"][0]["text"], "2025年2月30日")

    def test_exact_token_model_candidate_merges_with_rule_original_sentence(self):
        source = "结算日为2025年2月30日，请核查。"
        token = "2025年2月30日"
        raw = error_at(token, source, "时间信息非法")
        result = detect_text(source, document_id="token-confirmation", chat=MockChat(lambda payload, purpose: [raw]))
        self.assertEqual(len(result["errors"]), 1)
        finding = result["errors"][0]
        self.assertEqual(finding["status"], "confirmed_error")
        self.assertEqual(finding["spans"][0]["text"], source)
        self.assertEqual(finding["verification_spans"][0]["text"], token)
        self.assertIn("hybrid.model", finding["detector_ids"])

    def test_model_reason_naming_calendar_proof_merges_despite_other_dates(self):
        source = "2025年5月26日晚间，公司股票自2025年4月31日至2025年5月26日期间交易。"
        raw = {**error_at(source, source, "时间信息非法"),
               "reason": "2025年4月31日不存在，4月只有30天。"}
        result = detect_text(source, document_id="calendar-same-proof",
                             chat=MockChat(lambda payload, purpose: [raw]))
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(result["errors"][0]["status"], "confirmed_error")
        self.assertEqual(set(result["errors"][0]["detector_ids"]),
                         {"hybrid.model", "verified.calendar"})

    def test_backward_range_rule_and_model_hint_merge_on_exact_proof_token(self):
        source = "供给端预计2023-2021年间均保持10%以上。"
        snippet = "2023-2021年间均保持10%以上"
        raw = {**error_at(snippet, source, "时间矛盾"),
               "reason": "年份区间起始年晚于结束年。"}
        result = detect_text(source, document_id="range-same-proof",
                             chat=MockChat(lambda payload, purpose: [raw]))
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(set(result["errors"][0]["detector_ids"]),
                         {"hybrid.model", "legacy.C.INTRINSIC.002"})

    def test_duplicate_candidates_collapse_but_types_have_distinct_ids(self):
        text = "这句话有歧义。"
        raw = error_at(text, text)
        another = error_at(text, text, "术语误用")
        chat = MockChat(lambda payload, purpose: [raw, raw, another])
        a = detect_text(text, document_id="stable", chat=chat)
        b = detect_text(text, document_id="stable", chat=chat)
        self.assertEqual(len(a["errors"]), 2)
        self.assertEqual([e["id"] for e in a["errors"]], [e["id"] for e in b["errors"]])
        self.assertEqual(len({e["id"] for e in a["errors"]}), 2)


class ContextAndBudgetTests(unittest.TestCase):
    @staticmethod
    def window_budget(document_id, extra=600):
        return estimated_input_tokens(_messages([], document_id, "", [])) + extra

    def test_window_offsets_cover_all_characters_with_overlap(self):
        source = "第一段。\n" * 5 + '中间\\"\x01' * 30 + "\n最后一段。"
        windows = text_windows(source, 150)
        self.assertGreater(len(windows), 1)
        self.assertEqual(missing_ranges(len(source), [(w.start, w.end) for w in windows]), [])
        for window in windows:
            self.assertEqual(window.text, source[window.start:window.end])
        self.assertTrue(any(b.start < a.end for a, b in zip(windows, windows[1:])))

    def test_middle_window_detection_keeps_global_offsets_and_budget(self):
        target = "这条描述过于模糊"
        source = "普通段落内容。\n" * 45 + target + "。\n" + "后续普通段落。\n" * 45
        def candidate(payload, purpose):
            for part in payload["contexts"]:
                if target in part["text"]:
                    return [error_at(target, source)]
            return []
        chat = MockChat(candidate)
        budget = self.window_budget("middle", extra=600)
        result = detect_text(source, document_id="middle", chat=chat, max_input_tokens=budget)
        self.assertTrue(result["coverage"]["complete"], result["coverage"])
        self.assertGreater(len(chat.calls), 1)
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(result["errors"][0]["spans"][0]["start"], source.index(target))
        self.assertTrue(all(estimated_input_tokens(messages) <= budget for messages, _ in chat.calls))

    def test_remote_metric_sentences_receive_a_global_model_pass(self):
        first, last = "甲公司2024年营业收入100万元。", "甲公司2024年营业收入200万元。"
        source = first + "\n" + "无指标的普通内容。\n" * 100 + last
        def candidate(payload, purpose):
            if purpose != "text_review.global":
                return []
            self.assertEqual([p["text"].strip() for p in payload["contexts"]], [first, last])
            return [{"error_type": "数值不一致错误", "spans": [error_at(first, source)["spans"][0], error_at(last, source)["spans"][0]],
                     "reason": "前后两处需要共同核查。"}]
        chat = MockChat(candidate)
        budget = self.window_budget("global", extra=600)
        result = detect_text(source, document_id="global", chat=chat, max_input_tokens=budget)
        self.assertTrue(result["coverage"]["complete"], result["coverage"])
        self.assertTrue(any(purpose == "text_review.global" for _, purpose in chat.calls))
        self.assertTrue(any(len(error["spans"]) == 2 for error in result["errors"]))

    def test_budget_exception_stops_calls_and_preserves_unprocessed_ranges(self):
        class BudgetExceeded(RuntimeError):
            pass
        calls = []
        def chat(messages, *, purpose):
            calls.append(messages)
            if len(calls) == 2:
                raise BudgetExceeded("run budget exhausted")
            return {"content": '{"errors":[]}', "trace": {}}
        source = "这是一段用于覆盖检查的文字。\n" * 100
        budget = self.window_budget("budget", extra=600)
        result = detect_text(source, document_id="budget", chat=chat, max_input_tokens=budget)
        self.assertEqual(len(calls), 2)
        self.assertFalse(result["coverage"]["complete"])
        self.assertGreater(result["coverage"]["processed_chars"], 0)
        self.assertFalse(result["coverage"]["execution_complete"])
        self.assertLess(result["coverage"]["processed_chars"], len(source))
        self.assertTrue(result["coverage"]["unprocessed_ranges"])
        self.assertIn("BudgetExceeded", str(result["coverage"]["truncation_reasons"]))

    def test_failed_global_check_does_not_claim_global_completion(self):
        source = "甲公司2024年营业收入100万元。\n" + "普通内容。\n" * 100 + "甲公司2024年营业收入200万元。"
        def chat(messages, *, purpose):
            if purpose == "text_review.global":
                raise RuntimeError("global check unavailable")
            return {"content": '{"errors":[]}', "trace": {}}
        budget = self.window_budget("global-failure", extra=600)
        result = detect_text(source, document_id="global-failure", chat=chat, max_input_tokens=budget)
        self.assertEqual(result["coverage"]["processed_chars"], len(source))
        self.assertFalse(result["coverage"]["complete"])
        self.assertFalse(result["coverage"]["global_linking_complete"])
        self.assertFalse(result["coverage"]["execution_complete"])

    def test_global_candidate_rejection_does_not_force_paid_reexecution(self):
        source = "甲公司2024年营业收入100万元。\n" + "普通内容。\n" * 100 + "甲公司2024年营业收入200万元。"
        def candidate(payload, purpose):
            return [error_at("虚构原句", "", start=-1)] if purpose == "text_review.global" else []
        budget = self.window_budget("global-rejection", extra=600)
        result = detect_text(source, document_id="global-rejection", chat=MockChat(candidate), max_input_tokens=budget)
        self.assertEqual(result["coverage"]["processed_chars"], len(source))
        self.assertTrue(result["coverage"]["execution_complete"])
        self.assertTrue(result["coverage"]["global_linking_complete"])
        self.assertFalse(result["coverage"]["candidate_quality_complete"])
        self.assertFalse(result["coverage"]["complete"])

    def test_prompt_too_large_is_not_sent(self):
        chat = MockChat()
        result = detect_text("原文", document_id="tiny-context", chat=chat, max_input_tokens=100)
        self.assertFalse(result["coverage"]["complete"])
        self.assertEqual(chat.calls, [])

    def test_oversized_global_group_is_explicit_even_when_windows_finish(self):
        source = "\n".join(f"甲公司2024年营业收入{i}万元。" for i in range(40))
        chat = MockChat()
        budget = self.window_budget("many-links", extra=400)
        result = detect_text(source, document_id="many-links", chat=chat, max_input_tokens=budget)
        self.assertEqual(result["coverage"]["processed_chars"], len(source))
        self.assertFalse(result["coverage"]["complete"])
        self.assertFalse(result["coverage"]["global_linking_complete"])
        self.assertIn("global_link_group_exceeds_context_budget", result["coverage"]["truncation_reasons"])
        self.assertTrue(all(estimated_input_tokens(messages) <= budget for messages, _ in chat.calls))

    def test_estimator_matches_char_weighted_transport_rule(self):
        messages = [{"role": "user", "content": '中文\\"\n\x01'}]
        serialized = json.dumps(messages, ensure_ascii=False)
        cjk = sum(1 for ch in serialized if ord(ch) >= 0x2E80)
        self.assertEqual(estimated_input_tokens(messages), 256 + round(1.1 * (cjk + (len(serialized) - cjk) / 4)))

    def test_estimator_charges_cjk_less_than_utf8_bytes(self):
        messages = [{"role": "user", "content": "营业收入利润增长比率连续多年持续提升"}]
        self.assertLess(estimated_input_tokens(messages), len(json.dumps(messages, ensure_ascii=False).encode("utf-8")) + 256)


class GoldIsolationTests(unittest.TestCase):
    def test_dataset_record_cannot_be_passed_as_text(self):
        with self.assertRaises(TypeError):
            detect_text({"content": "原文", "errors": ["答案"]}, document_id="test")

    def test_unknown_test_or_unlabelled_examples_are_rejected(self):
        for provenance in [{}, {"source_split": "test", "source_id": "held-out"},
                           {"source_split": "dev"}, {"source_split": "unknown", "source_id": "unknown"}]:
            with self.subTest(provenance=provenance), self.assertRaises(ValueError):
                detect_text("目标原文", document_id="test", examples=[{"content": "开发文本", "errors": [], **provenance}])

    def test_target_or_extra_gold_fields_cannot_enter_examples(self):
        good = {"source_split": "dev", "source_id": "dev-1", "content": "开发文本", "errors": [], "complete_annotation": True}
        for change in [{"document_id": "test"}, {"content": "目标原文"}, {"gold": [{"答案": 1}]}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                detect_text("目标原文", document_id="test", examples=[{**good, **change}])

    def test_allowed_development_example_is_sent_and_target_has_no_gold(self):
        example = {"source_split": "dev", "source_id": "dev-1", "content": "开发文本", "errors": [], "complete_annotation": True}
        chat = MockChat()
        result = detect_text("目标原文", document_id="test", chat=chat, examples=[example])
        messages = chat.calls[0][0]
        self.assertEqual([m["role"] for m in messages], ["system", "user", "assistant", "user"])
        target = json.loads(messages[-1]["content"])
        self.assertNotIn("errors", target)
        self.assertTrue(result["coverage"]["complete"])

    def test_partial_or_unverified_example_annotations_are_rejected(self):
        good = {"source_split": "dev", "source_id": "dev-1", "content": "开发文本", "errors": []}
        for completeness in ({}, {"complete_annotation": False}, {"complete_annotation": 1}):
            with self.subTest(completeness=completeness), self.assertRaisesRegex(ValueError, "complete_annotation"):
                detect_text("目标原文", document_id="test", examples=[{**good, **completeness}])

    def test_complete_development_example_keeps_all_error_instances(self):
        source = "2025年2月30日。1+1=3。"
        example = {"source_split": "dev", "source_id": "dev-1", "content": source,
                   "complete_annotation": True,
                   "errors": [error_at("2025年2月30日", source, "时间信息非法"),
                              error_at("1+1=3", source, "计算错误")]}
        chat = MockChat()
        detect_text("目标原文", document_id="test", chat=chat, examples=[example])
        shown = json.loads(chat.calls[0][0][2]["content"])
        self.assertEqual(len(shown["errors"]), 2)

    def test_sft_negative_example_demonstrates_empty_result(self):
        example = {"source_split": "sft_negative", "source_id": "sftneg-1",
                   "document_id": "sftneg-1", "content": "免税销售金额1.15亿元。",
                   "scene": "unknown", "complete_annotation": True, "errors": []}
        chat = MockChat()
        result = detect_text("目标原文", document_id="test", chat=chat, examples=[example])
        self.assertEqual([m["role"] for m in chat.calls[0][0]],
                         ["system", "user", "assistant", "user"])
        shown = json.loads(chat.calls[0][0][2]["content"])
        self.assertEqual(shown["errors"], [])
        self.assertTrue(result["coverage"]["complete"])

    def test_sft_negative_example_with_error_labels_is_rejected(self):
        source = "2025年2月30日。"
        example = {"source_split": "sft_negative", "source_id": "sftneg-2",
                   "document_id": "sftneg-2", "content": source,
                   "complete_annotation": True,
                   "errors": [error_at("2025年2月30日", source, "时间信息非法")]}
        with self.assertRaisesRegex(ValueError, "sft_negative"):
            detect_text("目标原文", document_id="test", examples=[example])


class ContainmentAndTriageTests(unittest.TestCase):
    def test_contained_single_span_candidate_inherits_deterministic_confirmation(self):
        source = "公司于2023年2月30日发布年报。"
        chat = MockChat(lambda payload, purpose: [error_at("公司于2023年2月30日发布年报", source, "时间信息非法")])
        result = detect_text(source, document_id="contained", chat=chat)
        findings = [e for e in result["errors"] if e["error_type"] == "时间信息非法"]
        self.assertEqual(len(findings), 1, result["errors"])
        self.assertEqual(findings[0]["status"], "confirmed_error")
        self.assertEqual(findings[0]["validation"], "deterministic")
        self.assertEqual(findings[0]["review_priority"], "confirmed")
        self.assertIn("verified.calendar", findings[0].get("detector_ids", []))

    def test_narrower_or_multi_span_candidates_stay_for_review(self):
        source = "公司于2023年2月30日发布年报。"
        narrower = MockChat(lambda payload, purpose: [error_at("2023年2月30", source, "时间信息非法")])
        result = detect_text(source, document_id="narrower", chat=narrower)
        pending = [e for e in result["errors"] if e.get("review_priority") in ("high", "low")]
        self.assertTrue(any(e["status"] == "needs_review" and e["detector_id"] == "hybrid.model" for e in pending))

    def test_review_priority_tiers_for_deterministic_and_model_candidates(self):
        source = "甲公司、乙公司分别为1%、2%、3%。"
        chat = MockChat(lambda payload, purpose: [
            error_at("分别为", source, "模糊语言"),
            error_at("1%", source, "数值不一致错误")])
        result = detect_text(source, document_id="tiers", chat=chat)
        tiers = {(e["detector_id"], e["error_type"]): e.get("review_priority") for e in result["errors"]}
        self.assertEqual(tiers[("legacy.C.INTRINSIC.001", "数值不一致错误")], "high")
        self.assertEqual(tiers[("hybrid.model", "模糊语言")], "low")
        self.assertEqual(tiers[("hybrid.model", "数值不一致错误")], "high")

    def test_token_budget_windows_are_lossless_and_larger_than_byte_windows(self):
        source = "这是一段用于覆盖检查的文字。\n" * 60
        byte_windows = text_windows(source, 150)
        token_windows = text_windows_token_budget(source, 150)
        self.assertLess(len(token_windows), len(byte_windows))
        self.assertEqual(missing_ranges(len(source), [(w.start, w.end) for w in token_windows]), [])
        for window in token_windows:
            self.assertEqual(window.text, source[window.start:window.end])

    def test_example_or_hypothetical_context_never_auto_confirms(self):
        for source in ("参考示例：2023年2月30日并非可用日期。",
                       "假设结算日为2023年2月30日。",
                       "毛利率不得填写为5万元。",
                       "练习：3+2=6。若按此填写将出错。"):
            with self.subTest(source=source):
                result = detect_text(source, document_id="context-guard", chat=MockChat())
                self.assertTrue(all(e["status"] == "needs_review" for e in result["errors"]), source)


if __name__ == "__main__":
    unittest.main()
