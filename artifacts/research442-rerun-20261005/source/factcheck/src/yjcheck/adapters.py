"""B→C 边界：核对文件身份、完整页号和证据坐标，不信任解析的 ok 标签。"""
from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from collections import Counter
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET

from .models import Block, Document


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def valid_bbox(value, size=None) -> bool:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return False
    try:
        x0, y0, x1, y1 = (float(x) for x in value)
        if not all(math.isfinite(x) for x in (x0, y0, x1, y1)):
            return False
        if x0 < 0 or y0 < 0 or x1 - x0 < .5 or y1 - y0 < .5:
            return False
        return size is None or (x1 <= float(size[0]) and y1 <= float(size[1]))
    except (TypeError, ValueError, IndexError):
        return False


def _quality(page: dict) -> tuple[str, list[str]]:
    notes = list(page.get("notes", []))
    status = page.get("status", "unknown")
    q = page.get("quality", {})
    if status == "warn" and notes and all(str(n).startswith("info:") for n in notes):
        status = "ok"
    if q.get("table_col_inconsistent") or q.get("vlm_verdict") == "mismatch":
        status = "warn"
    return status, notes


def from_parse_result(data: dict, role: str, original_path: Path | None = None) -> Document:
    """消费 B 的 parse_result.json。原 PDF 必须可访问，以独立校验哈希及页数。"""
    meta = data.get("doc", {})
    original = Path(original_path or meta.get("source_path", ""))
    digest = str(meta.get("sha256", ""))
    doc = Document(f"sha256:{digest}", digest, str(data.get("run_id", "")),
                   str(original.resolve()), role)
    if not re.fullmatch(r"[a-fA-F0-9]{64}", digest) or not doc.run_id:
        doc.issues.append("document:identity_missing")
    expected_pages = None
    expected_sizes = {}
    if not original.is_file():
        doc.issues.append("document:original_unavailable")
    else:
        actual_hash = file_hash(original)
        if actual_hash != digest:
            doc.issues.append("document:hash_mismatch")
        try:
            from pypdf import PdfReader
            reader = PdfReader(original)
            expected_pages = len(reader.pages)
            for n, original_page in enumerate(reader.pages, 1):
                rect = original_page.cropbox if data.get("engine",{}).get("name")=="pymupdf" else original_page.mediabox
                width,height=float(rect.width),float(rect.height)
                if int(original_page.get("/Rotate",0))%180:
                    width,height=height,width
                expected_sizes[n]=[width,height]
        except Exception as exc:
            doc.issues.append(f"document:pdf_unreadable:{type(exc).__name__}")
    pages = data.get("pages", [])
    numbers = [p.get("page") for p in pages]
    if not pages or expected_pages is None or sorted(numbers, key=str) != sorted(range(1, expected_pages + 1), key=str):
        doc.issues.append("document:page_coverage_invalid")
    if expected_pages is not None and meta.get("total_pages") != expected_pages:
        doc.issues.append("document:page_count_mismatch")
    seen = set()
    for page in sorted(pages, key=lambda p: p.get("page", 0)):
        no = page.get("page")
        size = page.get("page_size")
        status, notes = _quality(page)
        real_size=expected_sizes.get(no)
        if (not isinstance(size,(list,tuple)) or len(size)!=2 or real_size is None
                or any(not isinstance(x,(int,float)) or not math.isfinite(x) or abs(x-y)>1 for x,y in zip(size or [],real_size or []))):
            status="fail"
            doc.issues.append(f"page:{no}:geometry_mismatch")
        size=real_size
        if status != "ok":
            doc.issues.append(f"page:{no}:{status}")
        block_counts=Counter(b.get("block_id") for b in page.get("blocks",[]))
        for b in sorted(page.get("blocks", []), key=lambda b: b.get("order", 0)):
            if b.get("type") == "image":
                continue
            key = (no, b.get("block_id"))
            b_status = status
            if key in seen or block_counts[b.get("block_id")]>1 or not b.get("block_id") or not valid_bbox(b.get("bbox"), size):
                b_status = "fail"
                doc.issues.append(f"block:{no}:{b.get('block_id')}:invalid_locator")
            seen.add(key)
            cells = b.get("cells", [])
            text = str(b.get("text", ""))
            if not text and cells:
                by_row = {}
                for c in cells:
                    by_row.setdefault(c.get("row", 0), []).append(c)
                text = "\n".join(" ".join(str(c.get("text", "")) for c in sorted(row, key=lambda c:c.get("col",0)))
                                 for _, row in sorted(by_row.items()))
            doc.blocks.append(Block(str(b.get("block_id", "")), text, no, b.get("bbox"),
                                    type=b.get("type", "text"), status=b_status, notes=notes,
                                    cells=cells, page_size=size))
    doc.metadata = {"format": "pdf", "total_pages": expected_pages,
                    "parser_doc_id": meta.get("doc_id"), "engine": data.get("engine", {}),
                    "parser_quality": data.get("quality_report", {})}
    return doc


def load_document(path: str | Path, role: str, work_dir: str | Path,
                  engine: str = "pdfplumber") -> Document:
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError(f"输入文件不存在：{path}")
    if role not in {"report", "source"}:
        raise ValueError("role 必须为 report 或 source")
    suffix = path.suffix.lower()
    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        original = Path(data.get("doc", {}).get("source_path", ""))
        if not original.is_absolute():
            original = path.parent / original
        doc = from_parse_result(data, role, original)
        # 文件哈希不能认证一个外部 JSON 的正文。导入时从原 PDF 重新提取，
        # 避免修改 JSON 中的数字后仍借用正确文件哈希生成结论。
        if not any(i.startswith("document:") for i in doc.issues):
            verified=load_document(original,role,work_dir,engine)
            imported_status={b.page:b.status for b in doc.blocks if b.status!="ok"}
            for block in verified.blocks:
                if block.page in imported_status:
                    block.status=imported_status[block.page]
            verified.issues=list(dict.fromkeys(verified.issues+doc.issues))
            verified.metadata["imported_parse"]={"path":str(path),"sha256":file_hash(path),"run_id":doc.run_id,
                                                "content_policy":"reparsed_original_pdf"}
            doc=verified
    elif suffix == ".pdf":
        from yjparse.pipeline import PipelineConfig, parse_document
        digest = file_hash(path)
        out = Path(work_dir) / "parse" / digest
        engines = [engine, "pymupdf"] if engine != "pymupdf" else [engine]
        result = None
        last_error = None
        for name in engines:
            try:
                parsed = parse_document(path, out,
                                        PipelineConfig(primary=name, reference="none", ocr="auto"))
            except ValueError as exc:
                last_error = exc
                if "没有页面" in str(exc) and name != engines[-1]:
                    continue
                raise
            text_chars = sum(len(str(b.get("text", "") or "").replace(" ", ""))
                             for page in parsed.to_dict().get("pages", [])
                             for b in page.get("blocks", []))
            pages = parsed.to_dict().get("pages", [])
            failed = sum(1 for page in pages if page.get("status") == "fail")
            usable_ratio = (len(pages) - failed) / max(len(pages), 1)
            result = parsed
            # 有可读文本且失败页占比不过半才视为可用；占位补齐页即使被 OCR
            # 补上文字也仍是解析失败，必须换引擎而不是当作证据来源。
            if text_chars > 0 and usable_ratio >= 0.5:
                break
        if result is None:
            if last_error is not None:
                raise last_error
            raise ValueError(f"解析失败，未获得可读页面：{path}")
        doc = from_parse_result(result.to_dict(), role, path)
    elif suffix == ".docx":
        digest = file_hash(path)
        doc = Document(f"sha256:{digest}", digest, uuid.uuid4().hex, str(path), role)
        with ZipFile(path) as z:
            root = ET.fromstring(z.read("word/document.xml"))
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        for n, para in enumerate(root.findall(".//w:body/w:p", ns), 1):
            pieces=[]
            for node in para.iter():
                tag=node.tag.rsplit("}",1)[-1]
                if tag=="t":
                    pieces.append(node.text or "")
                elif tag=="tab":
                    pieces.append("\t")
                elif tag in {"br","cr"}:
                    pieces.append("\n")
            text = "".join(pieces)
            if text.strip():
                doc.blocks.append(Block(f"paragraph_{n}", text, paragraph=n))
        if root.findall(".//w:body/w:tbl", ns):
            doc.issues.append("document:docx_tables_unsupported")
        doc.metadata = {"format": "docx", "locator": "paragraph; no invented PDF page or bbox"}
    else:
        raise ValueError("支持 PDF、DOCX 研报与 B 的 parse_result.json")
    if not doc.text.strip():
        doc.issues.append("document:no_text")
    return doc


def bind_company(report: Document, sources: list[Document], company: str | None = None) -> str:
    """以报告主体标题和明确的简称定义绑定公司，普通正文提及不能证明发行人。"""
    title = "\n".join(b.text for b in report.blocks[:20])
    match = re.search(r"([\u4e00-\u9fffA-Za-z]{2,30})[（(]\d{6}[）)]", title)
    identity = company or (match.group(1) if match else "")
    if not identity:
        match = re.search(r"([\u4e00-\u9fff]{2,30}(?:股份有限公司|有限责任公司|有限公司))", title)
        identity = match.group(1) if match else ""
    for doc in [report, *sources]:
        content = re.sub(r"\s+", "", doc.text)
        first_page=next((b.page for b in doc.blocks if b.page is not None),None)
        if first_page is not None:
            # OCR 扫描件首页标题可能残损：法定名称核对放宽到前 3 页。
            title_blocks=[b.text for b in doc.blocks
                          if b.page is not None and first_page <= b.page <= first_page + 2]
        else:
            title_blocks=[b.text for b in doc.blocks[:1]]
        title_text="\n".join(re.sub(r"\s+","",s) for s in title_blocks)
        # OCR 扫描件的公司全称可能跨块/跨行：整页紧凑拼接后也要能匹配到。
        title_text += "\n" + re.sub(r"\s+", "", "".join(title_blocks))
        legal_names=[]
        for line in title_text.splitlines():
            legal_names.extend(re.findall(r"([\u4e00-\u9fffA-Za-z]{2,60}(?:股份有限公司|有限责任公司|有限公司))",line))
        legal_names=[re.sub(r"^(?:关于|编制单位|委托单位)","",n) for n in legal_names]
        verified=any(identity in name for name in legal_names) if identity else False
        if identity and not verified:
            for legal in legal_names:
                if re.search(re.escape(legal)+r"[（(]以下简称[:：]?[‘“\"']"+re.escape(identity)+r"[’”\"']",content):
                    verified=True
                    break
        # 简称为标题开头并紧随股票代码、年度或报告类型，同样属于显式标题。
        if identity and re.match(re.escape(identity)+r"(?:[（(]\d{6}[）)]|20\d{2}|年度|研报|财务)",title_text):
            verified=True
        if verified:
            doc.company = identity
        else:
            doc.company = ""
            doc.issues.append("document:company_unverified")
    return identity
