"""知识库导出：把解析结果转成可直接入库的形态。

设计要点：
1. 每个块都带 HTML 注释锚点（页码、坐标、块编号），入库后仍能回到原文位置；
2. 同时产出检索索引 JSONL，检索命中后可按块编号取到页码与坐标；
3. 产出接入清单，列出目标 URI 与现成命令，OpenViking / RAGFlow 都能用。

为什么用 HTML 注释做锚点：OpenViking 自己的 PDF 解析也是用 <!-- Page N --> 保留页码，
Markdown 渲染时注释不可见，不影响人读，也不会污染向量化的正文。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .utils import ensure_dir, sha256_file, write_json

HEADING_PREFIX = {"h1": "##", "h2": "###", "h3": "####", "h4": "#####"}


def _anchor(block: Dict[str, Any], page: int) -> str:
    bbox = block.get("bbox") or []
    coords = ",".join(f"{v:.1f}" for v in bbox) if bbox else ""
    return (f"<!-- block:{block['block_id']} page:{page} "
            f"type:{block['type']} bbox:[{coords}] -->")


def _table_markdown(block: Dict[str, Any]) -> str:
    cells = block.get("cells") or []
    if not cells:
        return block.get("text", "").strip()
    rows = max(c["row"] for c in cells) + 1
    cols = max(c["col"] for c in cells) + 1
    grid = [["" for _ in range(cols)] for _ in range(rows)]
    for cell in cells:
        grid[cell["row"]][cell["col"]] = (cell.get("text") or "").replace("\n", " ").strip()
    if not grid:
        return ""
    header = grid[0]
    lines = ["| " + " | ".join(header) + " |",
             "| " + " | ".join("---" for _ in header) + " |"]
    for row in grid[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def parse_result_to_markdown(result: Dict[str, Any]) -> str:
    """把一次解析结果转成带锚点的 Markdown。"""
    doc = result["doc"]
    engine = result["engine"]
    lines: List[str] = [
        f"<!-- source:{doc['source_path']} sha256:{doc['sha256']} "
        f"engine:{engine['name']}@{engine.get('version', '')} run:{result['run_id']} -->",
        "",
        f"# {doc.get('title') or doc['doc_id']}",
        "",
    ]
    for page in result["pages"]:
        lines.append(f"<!-- Page {page['page']} -->")
        lines.append(f"<!-- page_status:{page.get('status', 'unknown')} -->")
        lines.append("")
        if page.get("status") == "fail":
            lines.append("<!-- rejected: failed page excluded from evidence -->")
            continue
        for block in page["blocks"]:
            kind = block["type"]
            text = (block.get("text") or "").strip()
            if kind == "caption" and not text:
                continue
            if kind == "image" and not text:
                lines.append(_anchor(block, page["page"]))
                if block.get("caption"):
                    lines.append(f"*{block['caption'].strip()}*")
                if block.get("source_note"):
                    lines.append(f"> {block['source_note'].strip()}")
                lines.append("")
                continue
            lines.append(_anchor(block, page["page"]))
            if kind == "heading":
                prefix = HEADING_PREFIX.get(block.get("level", "h2"), "###")
                lines.append(f"{prefix} {text}")
            elif kind == "table":
                if block.get("caption"):
                    lines.append(f"**{block['caption'].strip()}**")
                lines.append(_table_markdown(block))
                if block.get("source_note"):
                    lines.append(f"> {block['source_note'].strip()}")
            elif text:
                lines.append(text.replace("\n", " "))
            if kind == "heading" and block.get("source_note"):
                lines.append(f"> {block['source_note'].strip()}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def export_document(parse_result_path: Path, kb_dir: Path) -> Dict[str, Any]:
    payload = json.loads(Path(parse_result_path).read_text(encoding="utf-8"))
    doc_id = payload["doc"]["doc_id"]
    markdown_dir = ensure_dir(Path(kb_dir) / "markdown")
    markdown_path = markdown_dir / f"{doc_id}.md"
    markdown_path.write_text(parse_result_to_markdown(payload), encoding="utf-8")

    rows = []
    for page in payload["pages"]:
        if page.get("status") == "fail":
            continue
        for block in page["blocks"]:
            rows.append({
                "doc_id": doc_id,
                "run_id": payload["run_id"],
                "source_path": payload["doc"]["source_path"],
                "page": page["page"],
                "page_size": page["page_size"],
                "page_status": page["status"],
                "block_id": block["block_id"],
                "type": block["type"],
                "level": block.get("level"),
                "bbox": block.get("bbox"),
                "text": block.get("text") or "",
                "cells": block.get("cells") or [],
                "sentences": block.get("sentences") or [],
                "caption": block.get("caption") or "",
                "source_note": block.get("source_note") or "",
                "confidence": block.get("confidence"),
            })
    return {
        "doc_id": doc_id,
        "source_path": payload["doc"]["source_path"],
        "sha256": payload["doc"]["sha256"],
        "markdown": str(markdown_path),
        "markdown_sha256": sha256_file(markdown_path),
        "rows": rows,
        "pages": len(payload["pages"]),
        "status": payload["quality_report"]["status"],
    }


def export_kb(out_dir: Path, kb_dir: Path, project: str = "research-reports") -> Dict[str, Any]:
    """把一个产物目录下的所有文档导出成知识库可入库的形态。"""
    out_dir = Path(out_dir)
    kb_dir = ensure_dir(kb_dir)
    documents = []
    index_rows: List[Dict[str, Any]] = []
    for result_path in sorted(out_dir.glob("*/parse_result.json")):
        exported = export_document(result_path, kb_dir)
        index_rows.extend(exported.pop("rows"))
        documents.append(exported)

    index_path = kb_dir / "kb_index.jsonl"
    with open(index_path, "w", encoding="utf-8") as fh:
        for row in index_rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    manifest = {
        "project": project,
        "kb_index": str(index_path),
        "blocks": len(index_rows),
        "documents": documents,
        "viking_targets": [
            {
                "doc_id": doc["doc_id"],
                "markdown": doc["markdown"],
                "uri": f"viking://resources/{project}/{doc['doc_id']}",
                "command": (f"ov add-resource \"{doc['markdown']}\" "
                            f"--to viking://resources/{project}/{doc['doc_id']} --wait --timeout 300"),
            }
            for doc in documents
        ],
        "ragflow_hint": ("在 RAGFlow 里按文档上传同一份 Markdown；页码与坐标在 kb_index.jsonl 中，"
                         "检索命中后用 block_id 回查即可定位原文"),
    }
    manifest_path = write_json(kb_dir / "ingest_manifest.json", manifest)
    manifest["manifest"] = str(manifest_path)
    return manifest
