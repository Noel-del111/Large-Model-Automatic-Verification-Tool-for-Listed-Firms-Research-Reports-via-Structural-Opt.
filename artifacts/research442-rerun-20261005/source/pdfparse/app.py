"""研报解析质检台（Streamlit 可视化）。

用途：上传一份研报 PDF，直接看到解析结果、质量判定、证据高亮图与检索回链。
定位是解析模块的可视化验证与演示工具；面向最终用户的交互页面由 D 负责。

启动：
    run_app.cmd
    :: 或
    .venv\\Scripts\\python.exe -m streamlit run app.py
"""

from __future__ import annotations

import json
import hashlib
import sys
from pathlib import Path

import streamlit as st

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE / "src"))

from yjparse.config import load_thresholds  # noqa: E402
from yjparse.kb_export import export_kb  # noqa: E402
from yjparse.pipeline import PipelineConfig, parse_document  # noqa: E402
from yjparse.preview import render_preview  # noqa: E402
from yjparse.quality import page_text  # noqa: E402
from yjparse.retrieval import Bm25Index, load_index  # noqa: E402
from yjparse.vlm_check import VlmConfig  # noqa: E402

OUT_DIR = BASE / "data" / "web_out"
UPLOAD_DIR = BASE / "data" / "web_upload"
KB_DIR = BASE / "data" / "web_kb"
STATUS_TEXT = {"ok": "正常", "warn": "警告", "fail": "失败"}

st.set_page_config(page_title="研报解析质检台", page_icon="📄", layout="wide")


def settings_panel() -> dict:
    with st.sidebar:
        st.header("解析设置")
        primary = st.selectbox("主引擎", ["pymupdf", "pdfplumber", "docling", "mineru"],
                               help="pymupdf 为默认；docling / mineru 需先安装对应依赖")
        reference = st.selectbox("对照引擎", ["auto", "pdfplumber", "pymupdf", "none"],
                                 help="交叉校验用；none 表示不做双引擎比对")
        ocr = st.radio("OCR 兜底", ["auto", "off", "always"], horizontal=True,
                       help="auto：仅无文本层或位图密集的页面走 OCR")
        vlm = st.radio("视觉模型抽检", ["off", "auto", "always"], horizontal=True,
                       help="需要配置 YJPARSE_VLM_* 环境变量；没有端点时保持 off")
        table_strategy = st.selectbox("表格策略", ["lines", "hybrid", "text"],
                                      help="hybrid 会额外展开无框财务预测表")
        st.divider()
        st.caption("阈值：configs/thresholds.json")
        st.caption("核验口径：页码与坐标必填，缺失即判失败")
    return {"primary": primary, "reference": reference, "ocr": ocr, "vlm": vlm,
            "table_strategy": table_strategy}


def run_pipeline(pdf_paths: list[Path], options: dict) -> list:
    thresholds = load_thresholds()
    config = PipelineConfig(
        primary=options["primary"],
        reference=options["reference"],
        ocr=options["ocr"],
        vlm=options["vlm"],
        vlm_config=VlmConfig.from_env(),
        thresholds=thresholds,
        engine_params={options["primary"]: {"table_strategy": options["table_strategy"]}},
    )
    results = []
    progress = st.progress(0.0, text="开始解析…")
    for index, pdf in enumerate(pdf_paths, start=1):
        progress.progress(index / len(pdf_paths) * 0.8,
                          text=f"解析 {pdf.name}（{index}/{len(pdf_paths)}）")
        results.append(parse_document(pdf, OUT_DIR, config))
    progress.progress(0.9, text="导出检索索引…")
    export_kb(OUT_DIR, KB_DIR)
    progress.progress(1.0, text="完成")
    return results


def metrics_row(results: list) -> None:
    pages = sum(len(r.pages) for r in results)
    failed = sum(len(r.quality_report.failed_pages) for r in results)
    warned = sum(len(r.quality_report.warned_pages) for r in results)
    tables = sum(r.quality_report.summary.get("tables", 0) for r in results)
    sentences = sum(r.quality_report.summary.get("sentences", 0) for r in results)
    cols = st.columns(6)
    cols[0].metric("文档", len(results))
    cols[1].metric("页数", pages)
    cols[2].metric("正常页", pages - failed - warned)
    cols[3].metric("警告页", warned)
    cols[4].metric("失败页", failed, delta=None)
    cols[5].metric("表格 / 句子", f"{tables} / {sentences}")


def docs_table(results: list) -> None:
    rows = []
    for result in results:
        summary = result.quality_report.summary
        rows.append({
            "文档": result.doc.doc_id,
            "状态": STATUS_TEXT.get(result.quality_report.status, result.quality_report.status),
            "页数": len(result.pages),
            "区块": summary.get("blocks", 0),
            "表格": summary.get("tables", 0),
            "句子": summary.get("sentences", 0),
            "标题": summary.get("headings", 0),
            "图表标题/来源": summary.get("captions_attached", 0),
            "OCR 页": summary.get("ocr_pages", 0),
            "VLM 抽检": summary.get("vlm_checked_pages", 0),
            "双引擎一致度": summary.get("avg_engine_agreement"),
            "解析耗时(s)": result.engine.duration_s,
        })
    st.dataframe(rows, width="stretch", hide_index=True)


def pages_table(results: list) -> None:
    rows = []
    for result in results:
        for page in result.pages:
            rows.append({
                "文档": result.doc.doc_id,
                "页": page.page,
                "状态": STATUS_TEXT.get(page.status, page.status),
                "字符数": page.quality.char_count,
                "区块": page.quality.block_count,
                "表格": page.quality.table_count,
                "文字密度": page.quality.text_coverage,
                "乱码率": page.quality.garbled_ratio,
                "一致度": page.quality.engine_agreement_bag,
                "原因": "；".join(page.notes),
            })
    if not rows:
        st.info("暂无数据")
        return
    only_issue = st.toggle("只看警告与失败页", value=False)
    data = [r for r in rows if not only_issue or r["状态"] != "正常"]
    st.dataframe(data, width="stretch", hide_index=True, height=420)
    st.download_button("下载逐页质量表 (CSV)",
                       "\n".join([",".join(map(str, r.values())) for r in data]).encode("utf-8-sig"),
                       file_name="quality_table.csv", mime="text/csv")


def evidence_panel(results: list) -> None:
    options = [f"{r.doc.doc_id}" for r in results]
    doc_name = st.selectbox("选择文档", options, key="evidence_doc")
    result = next(r for r in results if r.doc.doc_id == doc_name)
    issue_pages = [p.page for p in result.pages if p.status != "ok"] or \
                  [p.page for p in result.pages][:1]
    page_no = st.selectbox("选择页面（默认列出有问题的页）", issue_pages,
                           key=f"evidence_page_{result.doc.doc_id}_{result.run_id}")
    context = (result.doc.doc_id, result.run_id, page_no)
    if st.session_state.get("evidence_context") != context:
        st.session_state["evidence_image"] = ""
        st.session_state["evidence_context"] = context
    render = st.button("生成证据高亮图", type="primary")
    if render:
        paths = render_preview(OUT_DIR / result.doc.doc_id / "parse_result.json",
                               pages=[page_no], dpi=110)
        st.session_state["evidence_image"] = str(paths[0]) if paths else ""
        st.session_state["evidence_page_info"] = page_no
    image_path = st.session_state.get("evidence_image")
    if image_path and Path(image_path).exists():
        page = next((p for p in result.pages if p.page == st.session_state.get("evidence_page_info")), None)
        if page is None:
            return
        st.caption(f"第 {page.page} 页　状态：{STATUS_TEXT.get(page.status, page.status)}　"
                   f"原因：{'；'.join(page.notes) or '无'}")
        st.caption("红框=文本块　蓝框=表格与单元格　灰框=图片　橙框=页眉页脚")
        st.image(image_path, width="stretch")
        with st.expander("本页解析文本（前 1500 字）"):
            st.text(page_text(page)[:1500])


def search_panel(results: list) -> None:
    index_path = KB_DIR / "kb_index.jsonl"
    if not index_path.exists():
        st.info("先解析一份研报，这里会出现在此文档中的检索结果。")
        return
    rows = load_index(index_path)
    query = st.text_input("检索关键词", placeholder="例如：归母净利润 增速")
    mode = st.radio("检索方式", ["单文档检索", "多文档横向对比"], horizontal=True)
    top = st.slider("返回条数", 1, 10, 5)
    if not query:
        return
    context = (query, mode, tuple((r.doc.doc_id, r.run_id) for r in results))
    if st.session_state.get("search_context") != context:
        st.session_state["search_image"] = ""
        st.session_state["search_context"] = context
    index = Bm25Index(rows)
    if mode == "单文档检索":
        hits = index.search(query, top_k=top)
    else:
        hits = []
        for result in results:
            hits.extend(index.search(query, top_k=2, doc_ids=[result.doc.doc_id]))
        hits = sorted(hits, key=lambda h: h["score"], reverse=True)[:top]
    if not hits:
        st.warning("没有命中。")
        return
    for hit in hits:
        bbox = ",".join(f"{v:.0f}" for v in (hit["bbox"] or []))
        st.markdown(f"**{hit['score']:.2f}**　{hit['doc_id']}　第 {hit['page']} 页　"
                    f"`{hit['block_id']}`　[{hit['type']}]　"
                    f"状态 {STATUS_TEXT.get(hit['page_status'], hit['page_status'])}")
        st.caption(f"bbox=[{bbox}]　{hit['text'][:160]}")
        if st.button("定位到这一页", key=f"locate_{hit['doc_id']}_{hit['page']}_{hit['block_id']}"):
            parse_result = OUT_DIR / hit["doc_id"] / "parse_result.json"
            if parse_result.exists():
                paths = render_preview(parse_result, pages=[hit["page"]], dpi=110,
                                       only_blocks=[hit["block_id"]])
                if paths:
                    st.session_state["search_image"] = str(paths[0])
    if st.session_state.get("search_image"):
        st.image(st.session_state["search_image"], caption="命中位置已高亮",
                 width="stretch")


def downloads_panel(results: list) -> None:
    for result in results:
        doc_dir = OUT_DIR / result.doc.doc_id
        st.subheader(result.doc.doc_id)
        cols = st.columns(4)
        for col, (label, name, mime) in zip(cols, [
            ("结构化结果 JSON", "parse_result.json", "application/json"),
            ("逐页质量表 CSV", "quality_table.csv", "text/csv"),
            ("区块明细 JSONL", "blocks.jsonl", "application/jsonl"),
            ("检索索引 JSONL", "kb_index.jsonl", "application/jsonl"),
        ]):
            path = doc_dir / name
            if name == "kb_index.jsonl":
                path = KB_DIR / name
            if path.exists():
                col.download_button(label, path.read_bytes(), file_name=f"{result.doc.doc_id}_{name}",
                                    mime=mime, key=f"dl_{result.doc.doc_id}_{name}")


def sample_button() -> None:
    if st.button("生成一份示例研报试跑（无需自备文件）"):
        sys.path.insert(0, str(BASE / "tests"))
        from sample_pdfs import make_report_pdf  # noqa: E402

        path = make_report_pdf(UPLOAD_DIR / "示例研报_2025H1.pdf")
        st.session_state["sample_path"] = str(path)
        st.success(f"已生成示例：{path.name}，点击“开始解析”。")


def main() -> None:
    st.title("研报解析质检台")
    st.caption("上传研报 PDF → 抽取文本与表格、保留页码与坐标、判定解析质量、回链到原文位置")
    options = settings_panel()

    uploads = st.file_uploader("上传研报 PDF（可多选，支持批量对比）", type=["pdf"],
                               accept_multiple_files=True)
    sample_button()
    pdf_paths: list[Path] = []
    if uploads:
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        for item in uploads:
            contents = item.getbuffer()
            target = UPLOAD_DIR / hashlib.sha256(contents).hexdigest() / Path(item.name).name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(contents)
            if target not in pdf_paths:
                pdf_paths.append(target)
    if st.session_state.get("sample_path"):
        pdf_paths.append(Path(st.session_state["sample_path"]))

    start = st.button("开始解析", type="primary", disabled=not pdf_paths)
    if start:
        st.session_state["results"] = run_pipeline(pdf_paths, options)
        st.session_state["search_image"] = ""

    results = st.session_state.get("results")
    if not results:
        st.info("上传 PDF 后点“开始解析”。解析结果包含：结构化 JSON、逐页质量表、证据高亮图、检索索引。")
        return

    metrics_row(results)
    tabs = st.tabs(["概览", "逐页质量", "证据定位", "检索与对比", "下载产物"])
    with tabs[0]:
        docs_table(results)
        st.caption("状态含义：正常可直接引用；警告表示有可解释疑点；失败页仅提示解析异常，不进入自动结论。")
    with tabs[1]:
        pages_table(results)
    with tabs[2]:
        evidence_panel(results)
    with tabs[3]:
        search_panel(results)
    with tabs[4]:
        downloads_panel(results)


if __name__ == "__main__":
    main()
