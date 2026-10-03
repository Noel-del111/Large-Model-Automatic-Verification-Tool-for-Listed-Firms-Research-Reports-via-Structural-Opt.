"""解析流水线：主引擎抽取 + 对照引擎交叉校验 + 质量判定 + 留痕。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .artifacts import write_artifacts
from .contract import (
    DocumentMeta,
    EngineInfo,
    Page,
    ParseResult,
    QualityReport,
    clamp_bbox,
    validate_bbox,
)
from .engines import build_engine, registry
from .ledger import Ledger
from .quality import apply_page_quality, normalize_text, page_text
from .utils import new_run_id, now_iso, safe_name, sha256_file


@dataclass
class PipelineConfig:
    primary: str = "pymupdf"
    reference: str = "auto"          # auto：优先 pdfplumber，其次与主引擎同名
    ocr: str = "auto"                # auto：仅对无文本层或位图密集页面启用；off 关闭；always 全量
    ocr_dpi: int = 200
    ocr_min_score: float = 0.5
    vlm: str = "off"                 # off / auto（只抽问题页）/ always（按比例全篇抽检）
    vlm_config: Any = None           # VlmConfig；未提供时从环境变量读取
    thresholds: Dict[str, Any] = field(default_factory=dict)
    engine_params: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    random_seed: int = 0


def run_vlm_checks(pages: List[Page], pdf_path: Path, config: PipelineConfig,
                   ledger: Ledger) -> Dict[str, int]:
    """第三层质检：用视觉模型抽检页面，核对解析文本与页面图像是否一致。"""
    from .quality import page_text
    from .vlm_check import VlmConfig, check_page, select_pages

    vlm_config = config.vlm_config or VlmConfig.from_env()
    if (config.vlm or "off").lower() == "off":
        return {"checked": 0, "mismatch": 0, "errors": 0}
    if not vlm_config.configured:
        ledger.tool("vlm.check", exit_code=1,
                    extra={"error": "未配置视觉模型端点，已跳过"})
        return {"checked": 0, "mismatch": 0, "errors": 0}

    selected = select_pages(pages, config.vlm, vlm_config.sample_ratio,
                            vlm_config.max_pages)
    checked = mismatch = errors = 0
    for page_no in selected:
        page = next(p for p in pages if p.page == page_no)
        verdict = check_page(pdf_path, page_no, page_text(page), vlm_config)
        page.quality.vlm_checked = True
        page.quality.vlm_verdict = verdict.status
        page.quality.vlm_confidence = verdict.confidence
        ledger.tool("vlm.check", version=vlm_config.model,
                    args={"page": page_no, "endpoint": vlm_config.base_url},
                    duration_s=verdict.duration_s,
                    exit_code=0 if verdict.status != "error" else 1,
                    extra={"verdict": verdict.status, "confidence": verdict.confidence,
                           "issues": verdict.issues[:3]})
        if verdict.status == "error":
            errors += 1
            page.notes.append(f"vlm_check:error:{verdict.raw[:60]}")
            continue
        checked += 1
        if verdict.status == "mismatch":
            mismatch += 1
            kinds = ",".join(sorted({i["type"] for i in verdict.issues})) or "unspecified"
            page.status = "warn" if page.status == "ok" else page.status
            page.notes.append(
                f"vlm_check:mismatch:{kinds}（置信度 {verdict.confidence}）")
        else:
            page.notes.append(f"vlm_check:ok（置信度 {verdict.confidence}）")
    ledger.compute(op="vlm_check_summary",
                   result={"selected": len(selected), "checked": checked,
                           "mismatch": mismatch, "errors": errors,
                           "model": vlm_config.model},
                   decision="checked")
    return {"checked": checked, "mismatch": mismatch, "errors": errors}


def _pages_needing_ocr(pages: List[Page], mode: str, thresholds: Dict) -> List[Page]:
    """挑出需要 OCR 的页面：没有文本层的整页图文，或位图占据大半版面的页面。"""
    mode = (mode or "off").lower()
    if mode == "off":
        return []
    if mode == "always":
        return list(pages)
    min_chars = int(thresholds.get("min_text_layer_chars_per_page", 30))
    image_ratio = float(thresholds.get("ocr_image_area_ratio", 0.45))
    selected = []
    for page in pages:
        # Missing extractor pages must be recovered by an alternate parser;
        # OCR cannot turn a coverage failure into trustworthy page evidence.
        if page.engine_stats.get("coverage_violations"):
            continue
        text = ""
        for block in page.blocks:
            if block.text:
                text += block.text
            elif block.cells:
                text += " ".join(c.text for c in block.cells)
        chars = len(text.replace(" ", ""))
        covered = max(sum(b.bbox.area for b in page.blocks if b.type == "image" and b.bbox),
                      float(page.engine_stats.get("image_area", 0)))
        page_area = max(page.page_size[0] * page.page_size[1], 1.0)
        # A short text or blank page alone is not evidence of a scanned page.
        # This avoids loading OCR for ordinary covers, clipped or empty pages.
        if (covered > 0 and chars < min_chars) or (covered / page_area >= image_ratio and chars < 300):
            selected.append(page)
    return selected


def resolve_reference(primary: str, reference: str) -> Optional[str]:
    if reference and reference.lower() == "none":
        return None
    available = {name: info["available"] for name, info in registry().items()}
    if reference and reference.lower() not in {"auto", "none", ""}:
        return reference.lower() if available.get(reference.lower()) else None
    for candidate in ("pdfplumber", primary):
        if available.get(candidate):
            return candidate
    return None


def _page_violations(page: Page, tol: float, clamp_ratio: float) -> Tuple[List[str], List[str]]:
    """区分硬问题与软问题。

    硬问题（坐标缺失、坐标严重越界、退化矩形）判失败；
    软问题（小幅溢出、图片整体溢出）先把框收拢回页面并给出告警，
    因为研报里的整页图表与跨栏元素经常有像素级溢出，判失败会产生大量误报。
    """
    hard: List[str] = []
    soft: List[str] = []
    clamped = 0
    w, h = page.page_size
    for block in page.blocks:
        issue = validate_bbox(block.bbox)
        if issue:
            hard.append(f"{block.block_id}:{issue}")
            continue
        if block.bbox.out_of_page(w, h, tol):
            fixed, changed, ratio = clamp_bbox(block.bbox, w, h)
            if changed and (ratio <= clamp_ratio or block.type == "image"):
                block.bbox = fixed
                issue = validate_bbox(fixed)
                if issue:
                    hard.append(f"{block.block_id}:{issue}_after_clamp")
                    continue
                clamped += 1
            else:
                hard.append(f"{block.block_id}:bbox_out_of_page(overflow={ratio:.2f})")
    if clamped:
        soft.append(f"info:bbox_clamped:{clamped} 个块坐标溢出页面，已收拢到页内")
    return hard, soft


def _pdf_page_sizes(path: Path, coordinate_engine: str = "pymupdf") -> List[Tuple[float, float]]:
    """Read source metadata independently of any extractor's returned pages."""
    if coordinate_engine == "pdfplumber":
        # pdfplumber uses MediaBox coordinates; PyMuPDF uses the CropBox.
        # A clipped PDF must not inherit dimensions from another coordinate system.
        import pdfplumber
        try:
            with pdfplumber.open(str(path)) as pdf:
                sizes = [(float(p.width), float(p.height)) for p in pdf.pages]
        except Exception:
            sizes = []
        if sizes:
            return sizes
        # pdfminer 对部分 xref 损坏的 PDF 可能读不出页面：退回 pymupdf 读取页数，
        # 引擎是否可用由上游 fallback 逻辑决定，这里只保证页元数据不再丢失。
    try:
        import pymupdf
    except ImportError:
        import pdfplumber
        with pdfplumber.open(str(path)) as pdf:
            return [(float(p.width), float(p.height)) for p in pdf.pages]
    with pymupdf.open(str(path)) as pdf:
        return [(float(p.rect.width), float(p.rect.height)) for p in pdf]


def _check_page_coverage(pages: List[Page], sizes: List[Tuple[float, float]]) -> None:
    """Keep failures visible; never infer source page count from extractor output."""
    counts: Dict[int, int] = {}
    for page in pages:
        counts[page.page] = counts.get(page.page, 0) + 1
    for page in pages:
        issues = []
        if page.page < 1 or page.page > len(sizes):
            issues.append("page_number_out_of_range")
        else:
            page.page_size = sizes[page.page - 1]
        if counts[page.page] > 1:
            issues.append("duplicate_page")
        page.engine_stats["coverage_violations"] = issues
    for number, size in enumerate(sizes, start=1):
        if number not in counts:
            pages.append(Page(page=number, page_size=size,
                              engine_stats={"coverage_violations": ["missing_source_page"]}))
    pages.sort(key=lambda page: page.page)


def parse_document(pdf_path: Path, out_dir: Path, config: PipelineConfig,
                   run_id: Optional[str] = None,
                   ledger: Optional[Ledger] = None,
                   log_dir: Optional[Path] = None) -> ParseResult:
    pdf_path = Path(pdf_path)
    out_dir = Path(out_dir)
    run_id = run_id or new_run_id()
    project = Path(__file__).resolve().parents[2]
    ledger = ledger or Ledger(log_dir or (out_dir / "logs" / run_id), run_id, repo_dir=project)

    digest = sha256_file(pdf_path)
    doc_id = f"{safe_name(pdf_path.stem)}-{digest[:16]}"
    ledger.access("read", pdf_path, sha256=digest, actor="pipeline")
    primary_name = config.primary.lower()
    source_sizes = _pdf_page_sizes(pdf_path, primary_name)
    if not source_sizes:
        raise ValueError("源 PDF 没有页面，不能建立可核查的解析结果")

    params = dict(config.engine_params.get(primary_name, {}))
    if primary_name == "mineru" and "output" not in params:
        # MinerU 适配器读取离线产物；未指定时退回到与输入同名的目录
        candidate = pdf_path.with_suffix("")
        params["output"] = str(candidate)
    engine = build_engine(primary_name, **params)

    started = time.time()
    ledger.tool(f"{primary_name}.parse", version=engine.version(),
                args={"path": str(pdf_path), **params})
    pages = engine.parse(pdf_path, doc_id=doc_id, ledger=ledger)
    _check_page_coverage(pages, source_sizes)
    duration = time.time() - started

    reference_name = resolve_reference(primary_name, config.reference)
    ref_texts: Dict[int, str] = {}
    reference_info: Optional[Dict[str, Any]] = None
    if reference_name:
        try:
            ref_engine = build_engine(reference_name)
            ref_started = time.time()
            ref_texts = ref_engine.page_texts(pdf_path)
            ledger.tool(f"{reference_name}.page_texts", version=ref_engine.version(),
                        args={"path": str(pdf_path)}, duration_s=time.time() - ref_started)
            reference_info = {"name": reference_name, "version": ref_engine.version(),
                              "params": {}}
        except Exception as exc:
            ledger.tool(f"{reference_name}.page_texts", exit_code=1,
                        extra={"error": str(exc)})
            ref_texts = {}

    tol = float(config.thresholds.get("bbox_out_of_page_tolerance_pt", 2.0))
    clamp_ratio = float(config.thresholds.get("bbox_clamp_max_overflow_ratio", 0.25))
    min_chars = int(config.thresholds.get("min_text_layer_chars_per_page", 30))

    # OCR 兜底：扫描件与位图图表的文字在这里补进来
    ocr_pages = _pages_needing_ocr(pages, config.ocr, config.thresholds)
    if ocr_pages:
        from .ocr import OcrRunner, lines_to_blocks, ocr_available, ocr_version, render_page_image
        from .layout import order_blocks

        if not ocr_available():
            ledger.tool("ocr.recognize", exit_code=1,
                        extra={"error": "OCR 依赖缺失", "pages": len(ocr_pages)})
        else:
            runner = OcrRunner(min_score=config.ocr_min_score)
            started_ocr = time.time()
            recognized = 0
            for page in ocr_pages:
                try:
                    image, origin, scale = render_page_image(
                        pdf_path, page.page, dpi=config.ocr_dpi)
                    lines = runner.recognize_image(image, origin=origin, scale=scale)
                except Exception as exc:  # pragma: no cover - 环境相关
                    ledger.tool("ocr.recognize", exit_code=1,
                                extra={"page": page.page, "error": str(exc)})
                    continue
                if not lines:
                    continue
                blocks = lines_to_blocks(lines, page.page,
                                         page_w=page.page_size[0], page_h=page.page_size[1])
                page.blocks = order_blocks(page.blocks + blocks, page.page_size[0],
                                           page.page_size[1], mode="band")
                page.engine_stats["ocr_blocks"] = len(blocks)
                page.engine_stats["ocr_avg_score"] = round(
                    sum(b.confidence or 0.0 for b in blocks) / len(blocks), 4)
                recognized += 1
            ledger.tool("ocr.recognize", version=ocr_version(),
                        args={"pages": [p.page for p in ocr_pages], "dpi": config.ocr_dpi,
                              "min_score": config.ocr_min_score},
                        duration_s=time.time() - started_ocr,
                        extra={"recognized_pages": recognized})

    # 文档级判定：有文本层的文档里出现的无文本页通常是整页图表；
    # 整篇几乎没有文本层时，才按扫描件处理。
    text_pages = 0
    for page in pages:
        if len(page_text(page).replace(" ", "").replace("\n", "")) >= min_chars:
            text_pages += 1
    doc_context = {
        "has_text_layer": text_pages >= max(1, int(len(pages) * 0.5)),
        "text_layer_pages": text_pages,
        "total_pages": len(source_sizes),
    }
    ledger.compute(op="document_context", inputs={"min_chars": min_chars},
                   result=doc_context, decision="routed")

    for page in pages:
        violations, soft_notes = _page_violations(page, tol, clamp_ratio)
        violations.extend(page.engine_stats.get("coverage_violations", []))
        soft_notes.extend(page.notes)
        skipped = int(page.engine_stats.get("text_tables_skipped", 0))
        if skipped:
            soft_notes.append(
                f"info:borderless_tables_detected={skipped}"
                "（疑似无框表格，如需结构化请用 table_strategy=hybrid 重跑）"
            )
        if page.engine_stats.get("ocr_blocks"):
            soft_notes.append(
                f"ocr_used:blocks={page.engine_stats['ocr_blocks']}"
                f":avg_score={page.engine_stats.get('ocr_avg_score')}"
            )
        reference_text = ref_texts.get(page.page)
        apply_page_quality(page, normalize_text(reference_text) if reference_text else None,
                           violations, config.thresholds, doc_context=doc_context,
                           tables_filtered=int(page.engine_stats.get("tables_filtered", 0)),
                           soft_notes=soft_notes)
        if reference_text is not None and page.quality.engine_agreement_bag is not None:
            ledger.compute(
                op="cross_check_page",
                page=page.page,
                inputs={"reference": reference_name, "threshold_warn":
                        config.thresholds.get("engine_agreement_warn")},
                result={
                    "agreement_bag": page.quality.engine_agreement_bag,
                    "agreement_sequence": page.quality.engine_agreement,
                },
                decision=page.status,
            )
        ledger.compute(
            op="page_quality",
            page=page.page,
            inputs={"thresholds_sha": "see_manifest"},
            result=page.quality.to_dict(),
            decision=page.status,
        )

    report = _build_report(pages, threshold_summary={
        "primary": primary_name,
        "reference": reference_name,
    })
    vlm_stats = run_vlm_checks(pages, pdf_path, config, ledger)
    # 抽检可能把页面从 ok 提升为 warn，这里重算一次状态视图
    report.status = QualityReport.worst([p.status for p in pages])
    report.page_status = {str(p.page): p.status for p in pages}
    report.failed_pages = [p.page for p in pages if p.status == "fail"]
    report.warned_pages = [p.page for p in pages if p.status == "warn"]
    report.reasons = {str(p.page): p.notes for p in pages if p.notes}
    report.summary["failed"] = len(report.failed_pages)
    report.summary["warned"] = len(report.warned_pages)
    report.summary["ok"] = sum(1 for p in pages if p.status == "ok")
    total_rows = sum(len(p.blocks) for p in pages)
    report.summary.update({
        "pages": len(pages),
        "blocks": total_rows,
        "tables": sum(p.quality.table_count for p in pages),
        "sentences": sum(p.quality.sentence_count for p in pages),
        "headings": sum(p.quality.heading_count for p in pages),
        "captions_attached": sum(int(p.engine_stats.get("captions_attached", 0))
                                 for p in pages),
        "ocr_pages": sum(1 for p in pages if p.engine_stats.get("ocr_blocks")),
        "vlm_checked_pages": vlm_stats["checked"],
        "vlm_mismatch_pages": vlm_stats["mismatch"],
        "vlm_errors": vlm_stats["errors"],
        "tables_filtered": sum(p.quality.tables_filtered for p in pages),
        "image_only_pages": sum(
            1 for p in pages if any("image_only_page" in n for n in p.notes)),
        "two_column_pages": sum(
            1 for p in pages if int(p.engine_stats.get("columns", 1)) > 1),
        "rotated_pages": sum(
            1 for p in pages if int(p.engine_stats.get("rotation", 0) or 0) % 360 != 0),
        "avg_text_coverage": round(
            sum(p.quality.text_coverage for p in pages) / len(pages), 2) if pages else 0.0,
        "avg_engine_agreement": _avg_agreement(pages),
    })

    result = ParseResult(
        run_id=run_id,
        doc=DocumentMeta(
            doc_id=doc_id,
            source_path=str(pdf_path),
            sha256=digest,
            total_pages=len(source_sizes),
            bytes=pdf_path.stat().st_size,
        ),
        engine=EngineInfo(
            name=primary_name,
            version=engine.version(),
            strategy=str(params.get("strategy", "baseline")),
            model=str(params.get("model", "")),
            model_sha256=str(params.get("model_sha256", "")),
            params=params,
            started_at=now_iso(),
            duration_s=round(duration, 3),
        ),
        pages=pages,
        quality_report=report,
        reference_engine=reference_info,
    )

    written = write_artifacts(result, out_dir, ledger=ledger)
    ledger.manifest(
        params={"primary": primary_name, "reference": reference_name,
                "engine_params": params, "out_dir": str(out_dir)},
        thresholds=config.thresholds,
        models=[{"name": params.get("model", ""), "sha256": params.get("model_sha256", "")}]
        if params.get("model") else [],
        inputs=[{"path": str(pdf_path), "sha256": digest, "bytes": pdf_path.stat().st_size}],
        artifacts=[{"artifact": str(path), "sha256": sha256_file(path), "kind": kind}
                   for kind, path in written.items()],
        random_seed=config.random_seed,
    )
    return result


def _avg_agreement(pages: List[Page]) -> Optional[float]:
    values = [p.quality.engine_agreement_bag for p in pages
              if p.quality.engine_agreement_bag is not None]
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def _build_report(pages: List[Page], threshold_summary: Dict[str, Any]) -> QualityReport:
    report = QualityReport()
    report.page_status = {str(p.page): p.status for p in pages}
    report.failed_pages = [p.page for p in pages if p.status == "fail"]
    report.warned_pages = [p.page for p in pages if p.status == "warn"]
    report.reasons = {str(p.page): p.notes for p in pages if p.notes}
    report.status = QualityReport.worst([p.status for p in pages])
    violations: List[str] = []
    for page in pages:
        if page.status == "fail":
            violations.extend(f"p{page.page}:{note}" for note in page.notes)
    report.violations = sorted(set(violations))
    report.summary = {
        "failed": len(report.failed_pages),
        "warned": len(report.warned_pages),
        "ok": sum(1 for p in pages if p.status == "ok"),
        **threshold_summary,
    }
    return report
