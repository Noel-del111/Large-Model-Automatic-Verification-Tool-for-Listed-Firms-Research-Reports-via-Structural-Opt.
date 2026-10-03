"""One audited JSON punctuation repair, never model-content or truncation repair."""
from __future__ import annotations

import json


def load_review_json(text: str, *, allow_final_object_closer: bool = False):
    """Return parsed JSON and optional single-character insertion metadata.

    The sole accepted invalid form lacks the final error object's ``}`` before
    the last ``]}``. Brackets inside quoted strings and escapes are ignored.
    No missing values, partial strings, extra commas, or runtime truncation are
    recoverable. A unique structural insertion must produce valid JSON.
    """
    try:
        return json.loads(text), None
    except json.JSONDecodeError as original_error:
        if not allow_final_object_closer:
            raise
        stack, in_string, escaped, insertion = [], False, False, None
        for index, character in enumerate(text):
            if in_string:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    in_string = False
                continue
            if character == '"':
                in_string = True
            elif character in "{[":
                stack.append(character)
            elif character in "}]":
                expected = "{" if character == "}" else "["
                if not stack or stack[-1] != expected:
                    if (insertion is not None or character != "]" or stack != ["{", "[", "{"]
                            or "".join(text[index:].split()) != "]}"
                            or not text[:index].rstrip().endswith('"')):
                        raise original_error
                    insertion = index
                    stack.pop()  # The uniquely missing final error-object closer.
                stack.pop()
        if insertion is None or in_string or stack:
            raise original_error
        repaired = text[:insertion] + "}" + text[insertion:]
        try:
            parsed = json.loads(repaired)
        except json.JSONDecodeError:
            raise original_error from None
        if (not isinstance(parsed, dict) or set(parsed) != {"errors"}
                or not isinstance(parsed["errors"], list) or not parsed["errors"]
                or not all(isinstance(error, dict) for error in parsed["errors"])):
            raise original_error
        return parsed, {"rule": "insert_final_error_object_closer_before_array_tail_v1",
                        "insertion_position": insertion, "inserted_text": "}",
                        "semantic_content_unchanged": True}
