"""轻量离线检索：在解析索引上做 BM25 检索，支持多文档横向对比。

定位是兜底与演示：不依赖任何外部服务，能把“检索命中 → 页码与坐标 → 原文高亮”
整条链路跑通；正式环境可以换成 OpenViking 或 RAGFlow，
替换后仍沿用同一份 kb_index.jsonl 与 block_id 回链约定。
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

WORD_PATTERN = re.compile(r"[0-9A-Za-z%.\-]+")
CJK_PATTERN = re.compile(r"[\u4e00-\u9fff]")


def tokenize(text: str) -> List[str]:
    """中文按单字加二元组、英文数字按词切分，兼顾召回与区分度。"""
    if not text:
        return []
    lowered = text.lower()
    cjk = CJK_PATTERN.findall(lowered)
    tokens = list(cjk)
    tokens.extend(a + b for a, b in zip(cjk, cjk[1:]))
    tokens.extend(WORD_PATTERN.findall(lowered))
    return tokens


def load_index(kb_index: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with open(kb_index, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _row_text(row: Dict[str, Any]) -> str:
    parts = [row.get("text") or ""]
    if row.get("caption"):
        parts.append(row["caption"])
    for cell in row.get("cells") or []:
        parts.append(cell.get("text") or "")
    return " ".join(p for p in parts if p)


class Bm25Index:
    def __init__(self, rows: Sequence[Dict[str, Any]], k1: float = 1.5, b: float = 0.75):
        self.rows = [row for row in rows if row.get("page_status") != "fail"]
        self.k1, self.b = k1, b
        self.doc_tokens: List[Counter] = []
        self.doc_len: List[int] = []
        self.df: Counter = Counter()
        for row in self.rows:
            tokens = tokenize(_row_text(row))
            counter = Counter(tokens)
            self.doc_tokens.append(counter)
            self.doc_len.append(len(tokens))
            for token in counter:
                self.df[token] += 1
        self.n = max(len(self.rows), 1)
        self.avg_len = sum(self.doc_len) / self.n if self.n else 1.0

    def search(self, query: str, top_k: int = 10,
               doc_ids: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]:
        query_tokens = tokenize(query)
        if not query_tokens:
            return []
        allowed = set(doc_ids) if doc_ids else None
        scored: List[tuple] = []
        for index, row in enumerate(self.rows):
            if allowed and row.get("doc_id") not in allowed:
                continue
            score = 0.0
            counter = self.doc_tokens[index]
            length = self.doc_len[index] or 1
            for token in set(query_tokens):
                freq = counter.get(token, 0)
                if not freq:
                    continue
                idf = math.log(1 + (self.n - self.df[token] + 0.5) / (self.df[token] + 0.5))
                denom = freq + self.k1 * (1 - self.b + self.b * length / self.avg_len)
                score += idf * freq * (self.k1 + 1) / denom
            if score > 0:
                scored.append((score, row))
        scored.sort(key=lambda item: item[0], reverse=True)
        hits = []
        for score, row in scored[:top_k]:
            hits.append({
                "score": round(score, 4),
                "doc_id": row.get("doc_id"),
                "page": row.get("page"),
                "block_id": row.get("block_id"),
                "type": row.get("type"),
                "bbox": row.get("bbox"),
                "page_status": row.get("page_status"),
                "text": _row_text(row)[:300],
                "source_path": row.get("source_path"),
            })
        return hits


def compare_across_documents(rows: Sequence[Dict[str, Any]], query: str,
                             top_k_per_doc: int = 2) -> List[Dict[str, Any]]:
    """多文档横向对比：对每个文档各取最相关的片段，便于并列比较。"""
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row.get("doc_id", "")].append(row)
    result = []
    for doc_id, doc_rows in sorted(groups.items()):
        index = Bm25Index(doc_rows)
        hits = index.search(query, top_k=top_k_per_doc)
        if hits:
            result.append({"doc_id": doc_id, "hits": hits})
    return result
