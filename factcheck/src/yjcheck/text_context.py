"""Lossless text windows with source offsets; estimates are not token counts."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class TextSlice:
    start: int
    end: int
    text: str

    def to_dict(self) -> dict:
        return {"start": self.start, "end": self.end, "text": self.text}


def _char_tokens(text: str, margin: float) -> float:
    """CJK/fullwidth chars count one token each, every four other chars count one.

    Real BPE tokenizers typically pack Chinese text denser than one token per
    char, so this remains a conservative upper bound; ``margin`` adds safety.
    """
    cjk = sum(1 for ch in text if ord(ch) >= 0x2E80)
    return margin * (cjk + (len(text) - cjk) / 4)


def estimated_input_tokens(messages: list[dict]) -> int:
    """Heuristic token upper bound: CJK 1 token/char, ASCII 0.25 token/char,
    plus framing and a 10% safety margin. This replaces the former UTF-8 byte
    bound, which tripled Chinese content and forced needlessly small windows."""
    serialized = json.dumps(messages, ensure_ascii=False)
    return 256 + round(_char_tokens(serialized, 1.1))


def _serialized_size(text: str) -> int:
    # Source text is inside the JSON user payload, itself inside chat JSON.
    # Account for both escaping layers just as the budgeted transport does.
    return len(json.dumps(json.dumps(text, ensure_ascii=False), ensure_ascii=False).encode("utf-8"))


def _windows_by(content: str, fits: Callable[[str], bool], fits_quarter: Callable[[str], bool]) -> list[TextSlice]:
    """Pack ``content`` into lossless windows; prefers paragraph boundaries and
    overlaps at most a quarter of a window. Unusually long paragraphs are split
    losslessly. ``fits``/``fits_quarter`` decide, per measure, whether a slice
    fits the full budget or the overlap quarter."""
    if not content:
        return []
    boundaries = [match.end() for match in re.finditer(r"\n+", content)] + [len(content)]
    windows = []
    start = 0
    while start < len(content):
        low, high = start + 1, len(content)
        end = start
        while low <= high:
            mid = (low + high) // 2
            if fits(content[start:mid]):
                end, low = mid, mid + 1
            else:
                high = mid - 1
        if end == start:
            raise ValueError("budget cannot accommodate a single character")
        if end < len(content):
            options = [b for b in boundaries if start < b <= end]
            if options:
                # Avoid a near-empty window when the next paragraph is oversized.
                boundary = options[-1]
                if boundary - start >= (end - start) // 2:
                    end = boundary
        windows.append(TextSlice(start, end, content[start:end]))
        if end == len(content):
            break
        previous = max([b for b in boundaries if start < b < end], default=start)
        if previous > start and fits_quarter(content[previous:end]):
            start = previous
        else:
            # A bounded suffix preserves a boundary crossing even for a single
            # enormous paragraph. start always advances to avoid infinite loops.
            overlap = min(64, max(1, (end - start) // 8))
            start = max(start + 1, end - overlap)
    return windows


def text_windows(content: str, max_serialized_bytes: int) -> list[TextSlice]:
    """Byte-budget windows (legacy contract); see text_windows_token_budget."""
    if max_serialized_bytes < 16:
        raise ValueError("text window budget must be at least 16 bytes")
    return _windows_by(content,
                       fits=lambda text: _serialized_size(text) <= max_serialized_bytes,
                       fits_quarter=lambda text: _serialized_size(text) <= max_serialized_bytes // 4)


def text_windows_token_budget(content: str, max_estimated_tokens: int) -> list[TextSlice]:
    """Token-budget windows with the same lossless/overlap guarantees.

    Uses the char-aware estimator so CJK content fits far larger windows than
    the byte bound allowed; budget must stay conservative for the transport."""
    if max_estimated_tokens < 64:
        raise ValueError("token budget must be at least 64")
    return _windows_by(content,
                       fits=lambda text: round(_char_tokens(text, 1.05)) <= max_estimated_tokens,
                       fits_quarter=lambda text: round(_char_tokens(text, 1.05)) <= max_estimated_tokens // 4)


def merge_ranges(ranges: list[tuple[int, int]]) -> list[list[int]]:
    merged: list[list[int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    return merged


def missing_ranges(length: int, ranges: list[tuple[int, int]]) -> list[list[int]]:
    missing, last = [], 0
    for start, end in merge_ranges(ranges):
        if start > last:
            missing.append([last, start])
        last = max(last, end)
    if last < length:
        missing.append([last, length])
    return missing
