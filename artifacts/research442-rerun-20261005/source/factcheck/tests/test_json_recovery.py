"""Only the observed single final object-closer typo is recoverable offline."""
import copy
import hashlib
import json
import unittest

from yjcheck.json_recovery import load_review_json
from yjcheck.text_review import _parse_response


class JsonRecoveryTests(unittest.TestCase):
    def test_unique_closer_with_quoted_brackets_and_escapes_keeps_content(self):
        value = {"errors": [{"error_type": "冗余语句", "spans": [{"text": '原句包含 ]} 与 "引号"'}], "reason": '理由包含 \\ 与 "及 ]}"'}]}
        valid = json.dumps(value, ensure_ascii=False)
        broken = valid[:-3] + valid[-2:]
        parsed, repair = load_review_json(broken, allow_final_object_closer=True)
        self.assertEqual(parsed, value)
        self.assertEqual(broken[:repair["insertion_position"]] + "}" + broken[repair["insertion_position"]:], valid)

    def test_trace_preserves_raw_response_and_audits_only_insertion(self):
        original = '  ```json\n{"errors":[{"error_type":"冗余语句","spans":[{"text":"重复。"}],"reason":"重复。"]}\n```  '
        response = {"content": original, "trace": {"finish_reason": "stop", "call_id": "paid-call", "cost_cny": "0.1"}}
        before = copy.deepcopy(response)
        errors, trace = _parse_response(response)
        self.assertEqual(response, before)
        audit = trace["json_syntax_repair"]
        repaired = original[:audit["insertion_position"]] + "}" + original[audit["insertion_position"]:]
        self.assertEqual(audit["original_response_sha256"], hashlib.sha256(original.encode()).hexdigest())
        self.assertEqual(audit["repaired_response_sha256"], hashlib.sha256(repaired.encode()).hexdigest())
        self.assertEqual(errors[0]["reason"], "重复。")
        self.assertEqual(trace["call_id"], "paid-call")

    def test_other_damage_unknown_completion_and_runtime_truncation_are_not_repaired(self):
        bad = ['{"errors":[{"reason":"a"]}', '{"errors":[{"reason":"a"},]}',
               '{"errors":[{"reason":"a]}', '{"errors":[{"spans":[{"text":"a"]]}',
               '{"errors":[{"reason":"a" "extra":"b"]}', '{"other":[{"reason":"a"]}']
        for text in bad[1:]:
            with self.subTest(text=text), self.assertRaises(json.JSONDecodeError):
                load_review_json(text, allow_final_object_closer=True)
        for trace in ({}, {"finish_reason": "length"}, {"finish_reason": "stop", "response_content_incomplete": True}):
            with self.subTest(trace=trace), self.assertRaises(ValueError):
                _parse_response({"content": bad[0], "trace": trace})
        with self.assertRaises(ValueError):
            _parse_response({"content": '{"errors":[]}', "trace": {"finish_reason": "length"}})

    def test_valid_json_is_not_marked_repaired(self):
        parsed, audit = load_review_json('{"errors":[]}', allow_final_object_closer=True)
        self.assertEqual(parsed, {"errors": []})
        self.assertIsNone(audit)


if __name__ == "__main__":
    unittest.main()
