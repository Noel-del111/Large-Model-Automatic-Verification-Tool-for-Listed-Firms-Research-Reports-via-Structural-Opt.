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

# ---------------------------------------------------------------------------
# 设计系统：颜色、字阶、间距、圆角、阴影统一在这里定义，页面各处只引用变量
# ---------------------------------------------------------------------------
DESIGN = {
    "paper": "#FBF9F6",       # 纸张底色，避免纯白刺眼
    "surface": "#FFFFFF",
    "ink": "#1A1A1A",         # 正文主色
    "ink_soft": "#5A5A57",    # 次要文字
    "line": "#E4DED5",        # 细分隔线
    "accent": "#8C1D18",      # 印章红，用于强调与品牌
    "accent_soft": "#F3E4E2",
    "ok": "#2F6B4F",
    "warn": "#8A5D10",
    "fail": "#A32A24",
    "radius": "14px",
    "space": "8px",
    "shadow": "0 1px 2px rgba(26,26,26,.04), 0 8px 24px rgba(26,26,26,.06)",
}

st.set_page_config(page_title="研报解析质检台", page_icon="📄", layout="wide")


def inject_css() -> None:
    """注入设计系统样式：字阶、留白、层级、微交互、响应式与无障碍。"""
    d = DESIGN
    st.markdown(f"""
    <style>
    :root {{
      --paper:{d['paper']}; --surface:{d['surface']}; --ink:{d['ink']};
      --ink-soft:{d['ink_soft']}; --line:{d['line']}; --accent:{d['accent']};
      --accent-soft:{d['accent_soft']}; --ok:{d['ok']}; --warn:{d['warn']};
      --fail:{d['fail']}; --radius:{d['radius']}; --space:{d['space']};
      --shadow:{d['shadow']};
      --font-display:"Source Han Serif SC","Noto Serif SC","Songti SC",Georgia,serif;
      --font-ui:"Inter","Source Han Sans SC","PingFang SC","Microsoft YaHei",system-ui,sans-serif;
      --font-mono:"JetBrains Mono","SFMono-Regular",Consolas,monospace;
      --step-0:.875rem; --step-1:1rem; --step-2:1.25rem; --step-3:1.6rem; --step-4:2.4rem;
    }}
    .stApp {{ background:var(--paper); color:var(--ink); font-family:var(--font-ui); }}
    .block-container {{ max-width:1180px; padding:3.5rem 2.5rem 6rem; }}

    /* 标题层级：衬线大字 + 细水平线，营造审阅文档的质感 */
    h1, h2, h3 {{ font-family:var(--font-display); color:var(--ink); letter-spacing:.01em; }}
    h1 {{ font-size:var(--step-4); line-height:1.15; margin:0 0 .5rem; font-weight:600; }}
    h2 {{ font-size:var(--step-3); margin:2.5rem 0 1rem; font-weight:600; }}
    h3 {{ font-size:var(--step-2); margin:2rem 0 .75rem; font-weight:600; }}
    p, li {{ font-size:var(--step-1); line-height:1.85; color:var(--ink); }}
    .hero-sub {{ color:var(--ink-soft); font-size:var(--step-2); line-height:1.7; margin:0 0 1.5rem; }}
    .hero-rule {{ height:1px; background:var(--line); margin:2rem 0 2.5rem; border:0; }}

    /* 状态徽标：小、克制、可扫读 */
    .chip {{ display:inline-flex; align-items:center; gap:.4rem; padding:.25rem .7rem;
             border-radius:999px; font-size:var(--step-0); font-weight:600;
             border:1px solid var(--line); background:var(--surface); }}
    .chip-ok {{ color:var(--ok); border-color:color-mix(in srgb, var(--ok) 30%, var(--line)); }}
    .chip-warn {{ color:var(--warn); border-color:color-mix(in srgb, var(--warn) 30%, var(--line)); }}
    .chip-fail {{ color:var(--fail); border-color:color-mix(in srgb, var(--fail) 30%, var(--line)); }}

    /* 指标卡：留白充分，数字用等宽字体便于对齐比较 */
    .metrics {{ display:grid; gap:calc(var(--space) * 2);
                grid-template-columns:repeat(auto-fit, minmax(140px, 1fr)); margin:0 0 2rem; }}
    .metric {{ background:var(--surface); border:1px solid var(--line); border-radius:var(--radius);
               padding:1.25rem 1.35rem; box-shadow:var(--shadow);
               transition:transform .18s ease, box-shadow .18s ease; }}
    .metric:hover {{ transform:translateY(-2px); box-shadow:0 2px 4px rgba(26,26,26,.06), 0 12px 32px rgba(26,26,26,.10); }}
    .metric .label {{ font-size:var(--step-0); color:var(--ink-soft); letter-spacing:.06em; text-transform:uppercase; }}
    .metric .value {{ font-family:var(--font-mono); font-size:var(--step-3);
                      font-weight:600; margin-top:.35rem; }}
    .metric .hint {{ font-size:var(--step-0); color:var(--ink-soft); margin-top:.25rem; }}

    /* 证据行：左侧竖线像批注栏，页码与坐标用等宽字体，方便核对 */
    .evidence {{ border-left:2px solid var(--accent); padding:.6rem 0 .6rem 1rem; margin:.6rem 0; }}
    .evidence .meta {{ font-family:var(--font-mono); font-size:var(--step-0); color:var(--ink-soft); }}
    .evidence .snippet {{ margin-top:.25rem; }}

    /* 区块卡片与表格 */
    div[data-testid="stDataFrame"] {{ border:1px solid var(--line); border-radius:var(--radius);
                                      overflow:hidden; background:var(--surface); }}
    .stTabs [data-baseweb="tab-list"] {{ gap:1.5rem; border-bottom:1px solid var(--line); }}
    .stTabs [data-baseweb="tab"] {{ font-size:var(--step-1); padding:.6rem 0; }}
    .stTabs [aria-selected="true"] {{ color:var(--accent) !important; }}

    /* 微交互：按钮悬浮、按下、键盘焦点环，全部走同一套缓动 */
    .stButton > button {{ border-radius:999px; padding:.55rem 1.4rem; font-weight:600;
                          border:1px solid var(--line); background:var(--surface);
                          transition:transform .12s ease, box-shadow .18s ease, background .18s ease; }}
    .stButton > button:hover {{ transform:translateY(-1px); box-shadow:var(--shadow); }}
    .stButton > button:active {{ transform:translateY(0) scale(.99); }}
    .stButton > button:focus-visible {{ outline:2px solid var(--accent); outline-offset:2px; }}
    .stDownloadButton > button {{ border-radius:999px; }}
    [data-testid="stFileUploaderDropzone"] {{ border:1px dashed var(--line); border-radius:var(--radius);
                                              background:var(--surface); padding:1.5rem; }}

    /* 无障碍：尊重系统的减少动效设置 */
    @media (prefers-reduced-motion: reduce) {{
      * {{ transition:none !important; animation:none !important; }}
    }}
    /* 响应式：窄屏单列、缩小字阶与留白 */
    @media (max-width: 900px) {{
      .block-container {{ padding:2rem 1.1rem 4rem; }}
      :root {{ --step-3:1.35rem; --step-4:1.9rem; }}
      .metrics {{ grid-template-columns:repeat(auto-fit, minmax(120px, 1fr)); }}
    }}
    </style>
    """, unsafe_allow_html=True)


def hero(chips: list) -> None:
    st.markdown(
        '<h1>研报解析质检台</h1>'
        '<p class="hero-sub">上传研报 PDF，抽取文本与表格，保留页码与坐标，'
        '判定解析质量，并把每条结论回链到原文位置。</p>'
        + " ".join(chips) + '<hr class="hero-rule"/>',
        unsafe_allow_html=True)


def metric_cards(items: list) -> None:
    """items: [(标签, 数值, 说明)]"""
    cards = "".join(
        f'<div class="metric"><div class="label">{label}</div>'
        f'<div class="value">{value}</div>'
        f'<div class="hint">{hint}</div></div>'
        for label, value, hint in items)
    st.markdown(f'<div class="metrics">{cards}</div>', unsafe_allow_html=True)


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
    metric_cards([
        ("文档", len(results), "本次解析的研报数"),
        ("页数", pages, "全部页面"),
        ("正常页", pages - failed - warned, "可直接引用"),
        ("警告页", warned, "需人工看一眼"),
        ("失败页", failed, "不进入自动结论"),
        ("表格", tables, f"句子 {sentences} 条"),
    ])


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
        st.markdown(
            f'<div class="evidence">'
            f'<div class="meta">第 {hit["page"]} 页 · {hit["block_id"]} · '
            f'bbox=[{bbox}] · 相关度 {hit["score"]:.2f} · '
            f'{STATUS_TEXT.get(hit["page_status"], hit["page_status"])}</div>'
            f'<div class="snippet">{hit["text"][:160]}</div></div>',
            unsafe_allow_html=True)
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
    inject_css()
    hero(['<span class="chip chip-ok">页码与坐标可回溯</span>',
          '<span class="chip chip-warn">解析失败自动识别</span>',
          '<span class="chip">检索命中即可高亮</span>'])
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
