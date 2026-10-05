# -*- coding: utf-8 -*-
"""研报纠错助手 · 核查台（D 展示层）。

上传一份研报草稿与对应财报/公告，跑 C 核查流水线，五个视图：
概览 / 结果列表 / 证据对照 / 人工复核 / 导出。

定位为面向最终用户的交互页面；复核结论写入运行目录 review.json，
不改写 C 的三份产物（check_result.json / findings.csv / report.md），
因此 C 的 manifest 哈希校验始终有效。

启动：
    ..\\.venv\\Scripts\\python.exe -m streamlit run app.py
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import streamlit as st

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
REPO = ROOT
for extra in (REPO / "factcheck" / "src", REPO / "pdfparse" / "src"):
    sys.path.insert(0, str(extra))

from yjcheck.model import ModelConfig  # noqa: E402
from yjcheck.pipeline import run_check, verify_artifacts  # noqa: E402

from assistant import (TraceLog, ask_finding_question,  # noqa: E402
                       ask_report_question, rule_explanation)
from exports import (ERROR_TYPE_LABELS, REVIEW_LABELS, STATUS_LABELS,  # noqa: E402
                     evidence_request_contract,
                     finding_request_view, format_evidence_requests, findings_csv_bytes, full_json_bytes,
                     report_md, report_pdf_bytes)
from highlight import CLAIM_COLOR, SOURCE_COLOR, render_location_image  # noqa: E402
from review_store import REVIEW_STATUSES, ReviewStore  # noqa: E402
from text_review_view import text_review_page, show_text_report  # noqa: E402

DATA_DIR = BASE / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
CHECK_OUT = DATA_DIR / "check"

st.set_page_config(page_title="研报纠错助手 · 核查台", layout="wide")


# ---------------------------------------------------------------- 上传与设置

def settings_panel() -> dict:
    with st.sidebar:
        st.header("输入材料")
        report_upload = st.file_uploader("研报草稿（PDF / DOCX）", type=["pdf", "docx"],
                                         key="upload_report", help="带表格的 DOCX 请先转成 PDF")
        source_uploads = st.file_uploader("财报 / 公告（PDF，可多份）", type=["pdf"],
                                          accept_multiple_files=True, key="upload_sources",
                                          help="对应公司的审计报告、半年报、年报、更正公告等")
        company = st.text_input("公司简称（可选）", key="company_input",
                                help="留空时从研报首页标题自动识别；身份仍须由财报首页标题确认")
        st.divider()
        st.header("核查设置")
        engine = st.selectbox("解析引擎", ["pdfplumber", "pymupdf"], key="engine_select",
                              help="pdfplumber 为 C 默认引擎；pymupdf 表格定位更稳")
        use_model = st.checkbox("大模型辅助抽取", key="use_model",
                                help="按 YJCHECK_BASE_URL / YJCHECK_MODEL / YJCHECK_API_KEY 环境变量配置的兼容端点；"
                                     "模型只提交候选，判定仍走确定性规则")
        st.divider()
        if st.button("没有文件？生成演示样例并预填", key="demo_pdfs"):
            sys.path.insert(0, str(BASE / "tools"))
            from make_demo_pdfs import make_all  # noqa: F401

            st.session_state["demo_paths"] = {str(k): str(v) for k, v in make_all().items()}
            st.toast("已生成演示研报与财报，可直接点「开始核查」。")
    return {"report": report_upload, "sources": source_uploads,
            "company": (company or "").strip(), "engine": engine, "use_model": use_model}


def save_upload(upload) -> Path:
    content = upload.getbuffer()
    target = UPLOAD_DIR / hashlib.sha256(content).hexdigest()[:16] / Path(upload.name).name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return target


def execute_check(report_path: Path, source_paths: list[Path], company: str,
                  engine: str, use_model: bool):
    model_config = None
    if use_model:
        config = ModelConfig.from_env()
        if not config.base_url or not config.model:
            st.warning("已勾选大模型辅助抽取，但未配置 YJCHECK_BASE_URL / YJCHECK_MODEL，本次按离线规则运行。")
        else:
            config.review_text = True
            model_config = config
    progress = st.progress(0.0, text="解析与核查中…")
    result, out_dir = run_check(str(report_path), [str(path) for path in source_paths],
                                str(CHECK_OUT), company=company or None,
                                engine=engine, model_config=model_config)
    progress.progress(1.0, text="完成")
    return result, out_dir, model_config is not None


def find_default_index(findings: list[dict]) -> int:
    selected = st.session_state.get("selected_finding", "")
    for index, finding in enumerate(findings):
        if finding.get("id") == selected:
            return index
    return 0


def finding_label(finding: dict) -> str:
    claim = finding.get("claim", {})
    value = f"{claim.get('value', '')}{claim.get('unit', '')}".strip()
    return (f"{STATUS_LABELS.get(finding.get('status', ''), finding.get('status', ''))}"
            f"｜{claim.get('company', '')} {claim.get('metric', '')} {value} {claim.get('period', '')}")


# ---------------------------------------------------------------- 概览

def overview_view(result: dict, check_dir: Path) -> None:
    summary = result.get("summary", {})
    cols = st.columns(6)
    cols[0].metric("已确认错误", summary.get("confirmed_error", 0))
    cols[1].metric("待人工确认", summary.get("needs_review", 0))
    cols[2].metric("未发现问题", summary.get("no_issue", 0))
    cols[3].metric("提取声明", summary.get("claims", 0))
    cols[4].metric("来源事实", summary.get("source_facts", 0))
    cols[5].metric("输入问题", summary.get("input_issues", 0))
    request_contract = evidence_request_contract(result)
    if request_contract["status"] == "available":
        cols = st.columns(2)
        cols[0].metric("待补证/待澄清发现", request_contract["finding_count"])
        cols[1].metric("补证/澄清请求条数", request_contract["item_count"])
        if request_contract["item_count_source"] == "derived_from_complete_lists":
            st.caption("补证请求条数由旧版结果中已提供的完整请求列表计算；原 JSON 未被倒填。")
        else:
            st.caption("以上补证统计来自本次核查原结果，不随人工复核状态改写。")
    elif request_contract["status"] != "not_applicable":
        st.warning(request_contract["label"])
    automatic = summary.get("automatic_claims", summary.get("confirmed_error", 0) + summary.get("no_issue", 0))
    st.caption(f"自动判断声明 {automatic} 项；待复核声明 {summary.get('review_claims', summary.get('needs_review', 0))} 项。"
               "全文应核查项尚需独立标注，不能由提取数量推算全文覆盖率。")
    if result.get("text_review"):
        st.caption(f"文本补充检查待复核提示 {summary.get('text_review_total_hints', summary.get('text_needs_review', 0))} 条，"
                   f"其中未通过定位或格式校验 {summary.get('text_rejected_hints', 0)} 条；提示数量包含重复核验工作，不代表独立错误数。")
    reasons = summary.get("needs_review_by_rule") or {}
    if reasons:
        with st.expander("待复核原因"):
            st.dataframe([{"原因": key, "数量": value} for key, value in reasons.items()], hide_index=True)
    if result.get("runtime"):
        st.caption(f"完整处理耗时：{result['runtime'].get('total_seconds', '—')} 秒")
    complete = summary.get("complete")
    meaning = "通过" if complete else "未通过"
    st.caption(
        f"运行 {result.get('run_id', '')}；覆盖口径 {summary.get('coverage', '')}。"
        f"complete={meaning}：仅表示已提取的支持范围内事实均有确定结论且输入无已知质量问题，"
        "不表示已审查整篇研报的全部论断。")
    rows = []
    for doc in result.get("documents", []):
        rows.append({
            "角色": {"report": "研报", "source": "财报/公告"}.get(doc.get("role"), doc.get("role", "")),
            "文件": Path(str(doc.get("path", ""))).name or doc.get("doc_id", ""),
            "公司": doc.get("company", ""),
            "格式": (doc.get("metadata") or {}).get("format", ""),
            "SHA-256 前 16 位": str(doc.get("sha256", ""))[:16],
        })
    st.dataframe(rows, width="stretch", hide_index=True)
    issues = result.get("input_issues", []) or []
    if issues:
        expander = st.expander(f"输入质量问题（{len(issues)} 份文件）", expanded=True)
        with expander:
            for item in issues:
                st.markdown(f"**{Path(str(item.get('file', ''))).name}**")
                for issue in item.get("issues", []):
                    st.caption(f"- {issue}")
    if st.button("校验运行产物完整性", key="verify_artifacts"):
        ok = verify_artifacts(str(check_dir))
        if ok:
            st.success("三份产物与 manifest 哈希一致，运行标识匹配。")
        else:
            st.error("产物校验失败：文件缺失、被修改或运行标识不一致。")


# ---------------------------------------------------------------- 结果列表

def filter_panel(findings: list[dict]):
    statuses = [f.get("status", "") for f in findings]
    error_types = [f.get("error_type", "") for f in findings]
    companies = sorted({(f.get("claim") or {}).get("company", "") or "未绑定" for f in findings})
    col1, col2, col3 = st.columns(3)
    chosen_status = col1.multiselect("状态", ["confirmed_error", "needs_review", "no_issue"],
                                     default=["confirmed_error"],
                                     format_func=lambda s: STATUS_LABELS.get(s, s))
    chosen_types = col2.multiselect("错误类型", sorted({t for t in error_types if t}),
                                    format_func=lambda t: ERROR_TYPE_LABELS.get(t, t))
    chosen_company = col3.selectbox("公司", companies)
    return chosen_status, chosen_types, chosen_company


def list_view(result: dict) -> None:
    findings = result.get("findings", [])
    if not findings:
        st.info("没有可展示的发现。")
        return
    chosen_status, chosen_types, chosen_company = filter_panel(findings)
    filtered = [
        f for f in findings
        if (not chosen_status or f.get("status") in chosen_status)
        and (not chosen_types or f.get("error_type") in chosen_types)
        and ((f.get("claim") or {}).get("company", "") or "未绑定") == chosen_company
    ]
    rows = []
    for finding in filtered:
        claim = finding.get("claim", {})
        rows.append({
            "id": finding.get("id", ""),
            "状态": STATUS_LABELS.get(finding.get("status", ""), finding.get("status", "")),
            "错误类型": ERROR_TYPE_LABELS.get(finding.get("error_type", ""), finding.get("error_type", "")),
            "公司": claim.get("company", ""),
            "指标": claim.get("metric", ""),
            "研报原文": claim.get("text", ""),
            "声明值": f"{claim.get('value', '')}{claim.get('unit', '')}".strip(),
            "期间": claim.get("period", ""),
            "建议值": finding.get("suggested_value") or "",
            "依据位置": location_text(finding.get("evidence", [])),
            "规则": finding.get("rule_id", ""),
        })
    st.dataframe(rows, width="stretch", hide_index=True, height=420,
                 on_select="rerun", selection_mode="single-row", key="findings_table")
    event = st.session_state.get("findings_table")
    if event and event.selection and event.selection.rows:
        st.session_state["selected_finding"] = filtered[event.selection.rows[0]]["id"]
        st.info("已选中一条发现，切换到「证据对照」视图查看定位与依据。")
    if not filtered:
        st.info("当前筛选条件下没有发现。")


def location_text(facts: list[dict]) -> str:
    locations = []
    for fact in facts:
        for evidence in fact.get("evidence", []):
            file_name = Path(str(evidence.get("file", ""))).name or evidence.get("doc_id", "")
            if evidence.get("page") is not None:
                locations.append(f"{file_name} 第{evidence['page']}页")
            elif evidence.get("paragraph") is not None:
                locations.append(f"{file_name} 第{evidence['paragraph']}段")
            else:
                locations.append(file_name)
    return "；".join(dict.fromkeys(locations))


# ---------------------------------------------------------------- 证据对照

def evidence_sites(evidence_list: list[dict]) -> list[tuple[str, int | None, list[float] | None]]:
    """返回 [(file, page, bbox), ...]，供高亮渲染使用。"""
    sites = []
    for evidence in evidence_list:
        sites.append((str(evidence.get("file", "")), evidence.get("page"), evidence.get("bbox")))
    return sites


def evidence_text_lines(evidence_list: list[dict]) -> list[str]:
    lines = []
    for evidence in evidence_list:
        file_name = Path(str(evidence.get("file", ""))).name or evidence.get("doc_id", "")
        if evidence.get("page") is not None:
            pos = f"第 {evidence['page']} 页"
        elif evidence.get("paragraph") is not None:
            pos = f"第 {evidence['paragraph']} 段"
        else:
            pos = "位置缺失"
        bbox = ",".join(f"{float(v):.0f}" for v in evidence.get("bbox") or [])
        lines.append(f"`{file_name}` {pos}　block={evidence.get('block_id', '')}　bbox=[{bbox}]")
        quality = str(evidence.get("quality", "") or "")
        if quality and quality != "ok":
            notes = "；".join(str(n) for n in evidence.get("notes", []) or [])
            lines.append(f"证据质量 {quality}：{notes}（OCR 兜底证据，复核时优先人工核对）")
        if evidence.get("text"):
            lines.append(f"> {str(evidence['text'])[:200]}")
    return lines


def highlight_images(sites: list[tuple[str, int | None, list[float] | None]],
                     color: tuple[int, int, int]):
    """按 (file, page) 聚合渲染高亮图；返回 [(文件, 页码, png 字节), ...]"""
    groups: dict[tuple[str, int], list[list[float]]] = {}
    for file_name, page_no, bbox in sites:
        if not file_name or not page_no or not bbox:
            continue
        groups.setdefault((file_name, int(page_no)), []).append(bbox)
    images = []
    for (file_name, page_no), boxes in groups.items():
        png = render_location_image(file_name, page_no,
                                    [(box, color) for box in boxes])
        if png:
            images.append((Path(file_name).name, page_no, png))
    return images


def evidence_view(result: dict) -> None:
    findings = result.get("findings", [])
    if not findings:
        st.info("没有可对照的发现。")
        return
    index = find_default_index(findings)
    selected = st.selectbox("选择发现", range(len(findings)),
                            format_func=lambda i: finding_label(findings[i]),
                            index=index, key="finding_picker")
    finding = findings[selected]
    st.session_state["selected_finding"] = finding.get("id", "")
    claim = finding.get("claim", {})

    st.markdown(f"### {STATUS_LABELS.get(finding.get('status', ''), '')} · "
                f"{ERROR_TYPE_LABELS.get(finding.get('error_type', ''), finding.get('error_type', ''))}")
    value_text = f"{claim.get('value', '')}{claim.get('unit', '')}".strip()
    st.caption(f"公司 {claim.get('company', '') or '未绑定'}　指标 {claim.get('metric', '')}　"
               f"声明值 {value_text}　期间 {claim.get('period', '')}　"
               f"口径 {claim.get('basis', '')}/{claim.get('scope', '')}　币种 {claim.get('currency', '')}")
    st.markdown(f"> {claim.get('text', '')}")
    if finding.get("suggestion"):
        suggest = finding.get("suggestion", "")
        if finding.get("suggested_value") is not None:
            suggest += f"（建议值 {finding['suggested_value']}）"
        st.markdown(f"**修改建议**：{suggest}")

    with st.expander("计算过程与规则（calculation）", expanded=False):
        st.json(finding.get("calculation", {}))

    request_view = finding_request_view(result, finding)
    st.markdown("### 补充证据 / 人工澄清")
    if request_view["status"] == "invalid":
        st.error(request_view["label"])
    elif request_view["status"] == "legacy_missing":
        st.info(request_view["label"])
    else:
        st.caption(request_view["label"] + "。处理提示不是判错状态，也不代表人工已通过。")
    if request_view["items"]:
        st.caption("以下为原结果中的请求记录；文件字段只是文字线索，不会自动访问。")
        for line in format_evidence_requests(request_view["items"]).splitlines():
            st.text(line)

    left, right = st.columns(2)
    with left:
        st.markdown("**研报侧（声明位置）**")
        claim_sites = evidence_sites(claim.get("evidence", []))
        for line in evidence_text_lines(claim.get("evidence", [])):
            st.caption(line)
        for file_name, page_no, png in highlight_images(claim_sites, CLAIM_COLOR):
            st.image(png, caption=f"{file_name} · 第 {page_no} 页 · 红框=声明位置", width="stretch")
        if not claim.get("evidence"):
            st.caption("无定位证据（输入问题或抽取失败）。")
    with right:
        st.markdown("**财报侧（依据位置）**")
        drawn = False
        for fact in finding.get("evidence", []):
            fact_sites = evidence_sites(fact.get("evidence", []))
            attr = fact.get("attributes", {})
            st.caption(f"{fact.get('metric', '')} {fact.get('value', '')}{fact.get('unit', '')}　"
                       f"{fact.get('period', '')}　basis={fact.get('basis', '')}　"
                       f"scope={fact.get('scope', '')}"
                       + (f"　cell=({attr.get('cell', {}).get('row', '')}, {attr.get('cell', {}).get('col', '')})" if attr.get("cell") else ""))
            for line in evidence_text_lines(fact.get("evidence", [])):
                st.caption(line)
            for file_name, page_no, png in highlight_images(fact_sites, SOURCE_COLOR):
                st.image(png, caption=f"{file_name} · 第 {page_no} 页 · 蓝框=依据位置", width="stretch")
                drawn = True
        if not finding.get("evidence"):
            st.caption("无来源依据（证据不足，结论为待人工确认）。")
        if not drawn:
            st.caption("高亮图不可用：按「页码 + 原文摘录」回查原文（见上方定位行）。")


# ---------------------------------------------------------------- 人工复核

def review_view(result: dict, check_dir: Path) -> None:
    store = ReviewStore(check_dir)
    findings = result.get("findings", [])
    if not findings:
        st.info("没有需要复核的发现。")
        return
    stats = store.stats({f.get("id") for f in findings})
    reviewed = sum(stats.get(s, 0) for s in ("confirmed", "dismissed", "contested"))
    st.caption(f"复核进度：{reviewed}/{len(findings)}。复核结论写入 {check_dir.name}/review.json，"
               "不改写 check_result.json 等核查产物。")
    reviewer = st.text_input("复核人", value=st.session_state.get("reviewer_name", ""),
                             key="reviewer_input")
    if reviewer:
        st.session_state["reviewer_name"] = reviewer
    for finding in findings:
        current = store.get(finding.get("id", ""))
        with st.expander(finding_label(finding), expanded=False):
            request_view = finding_request_view(result, finding)
            st.caption("补证提示：" + request_view["label"])
            if request_view["items"]:
                st.caption("原结果请求详情（材料、字段、期间、公司、口径、范围、文件线索、类型和原因）：")
                for line in format_evidence_requests(request_view["items"]).splitlines():
                    st.text(line)
            col1, col2 = st.columns([1, 2])
            status = col1.selectbox(
                "复核结论", REVIEW_STATUSES,
                index=REVIEW_STATUSES.index(current.get("status", "unreviewed")),
                format_func=lambda s: REVIEW_LABELS.get(s, s),
                key=f"rev_status_{finding.get('id', '')}", label_visibility="collapsed")
            note = col2.text_input("复核备注", value=current.get("note", ""),
                                   key=f"rev_note_{finding.get('id', '')}",
                                   label_visibility="collapsed",
                                   placeholder="可选：复核依据或处理说明")
            timer_key = "review_timer_" + finding.get("id", "")
            if st.button("开始计时复核", key="start_" + timer_key):
                import time
                st.session_state[timer_key] = time.monotonic()
                st.toast("已开始计时；保存时记录本次复核耗时。")
            if st.button("保存复核", key=f"rev_save_{finding.get('id', '')}"):
                import time
                started = st.session_state.pop(timer_key, None)
                elapsed = time.monotonic() - started if started is not None else None
                entry = store.set(finding.get("id", ""), status, note, reviewer or "未署名", duration_seconds=elapsed)
                st.toast("已保存复核结论：%s" % REVIEW_LABELS.get(entry["status"], entry["status"]))
                st.rerun()
            history = current.get("history", [])
            if history:
                st.caption("此前：%s（%s · %s）" % (
                    REVIEW_LABELS.get(history[-1].get("status", ""), ""),
                    history[-1].get("reviewer", "") or "未署名",
                    history[-1].get("updated_at", "")))


# ---------------------------------------------------------------- 导出

def export_view(result: dict, check_dir: Path) -> None:
    store = ReviewStore(check_dir)
    reviews = store.load()
    run_id = result.get("run_id", "run")
    stats = store.stats({f.get("id") for f in result.get("findings", [])})
    reviewed = sum(stats.get(s, 0) for s in ("confirmed", "dismissed", "contested"))
    st.caption(f"复核进度：{reviewed}/{stats.get('total', 0)}。导出的 CSV / Markdown / JSON 均并入复核状态；"
               "PDF 报告需要 PyMuPDF（内置 CJK 字体无需联网）。")
    row1 = st.columns(3)
    check_json = (check_dir / "check_result.json")
    if check_json.is_file():
        row1[0].download_button("完整结果 JSON（C 产物）", check_json.read_bytes(),
                                file_name=f"{run_id}_check_result.json",
                                mime="application/json", key="dl_result_json")
    findings_csv = (check_dir / "findings.csv")
    if findings_csv.is_file():
        row1[1].download_button("发现清单 CSV（C 产物）", findings_csv.read_bytes(),
                                file_name=f"{run_id}_findings.csv",
                                mime="text/csv", key="dl_findings_csv")
    row1[2].download_button("复核合并清单 CSV", findings_csv_bytes(result, reviews),
                            file_name=f"{run_id}_findings_review.csv",
                            mime="text/csv", key="dl_review_csv")
    row2 = st.columns(3)
    row2[0].download_button("纠错报告 Markdown（含复核）",
                            report_md(result, reviews).encode("utf-8"),
                            file_name=f"{run_id}_report.md",
                            mime="text/markdown", key="dl_report_md")
    row2[1].download_button("复核后完整 JSON", full_json_bytes(result, reviews),
                            file_name=f"{run_id}_full.json",
                            mime="application/json", key="dl_full_json")
    pdf = report_pdf_bytes(result, reviews)
    if pdf:
        row2[2].download_button("纠错报告 PDF", pdf,
                                file_name=f"{run_id}_report.pdf",
                                mime="application/pdf", key="dl_report_pdf")
    else:
        row2[2].button("纠错报告 PDF", disabled=True,
                       help="PDF 导出不可用：本机缺少 PyMuPDF 或字体注册失败")


# ---------------------------------------------------------------- 助手问答

def assistant_view(result: dict, check_dir: Path) -> None:
    config = ModelConfig.from_env()
    trace_log = TraceLog(check_dir)
    if config.base_url and config.model:
        st.caption(f"已配置模型端点 {config.base_url} / {config.model}。"
                   "判定结论仍由确定性规则负责，助手只做解释与汇总，不改判定、不编造数值。")
    else:
        st.caption("未配置模型（YJCHECK_BASE_URL / YJCHECK_MODEL / YJCHECK_API_KEY，"
                   "DeepSeek 填 https://api.deepseek.com/v1）。当前为离线模式：展示规则解释卡与离线摘要。")

    findings = result.get("findings", [])
    st.markdown("**单条追问**：解释某条发现的判定依据")
    if findings:
        index = find_default_index(findings)
        selected = st.selectbox("选择发现", range(len(findings)),
                                format_func=lambda i: finding_label(findings[i]),
                                index=index, key="assistant_finding_picker")
        finding = findings[selected]
        question = st.text_input("追问", key="assistant_question",
                                 placeholder="例如：为什么判定为数值错误？依据在财报第几页？应该改成多少？")
        if st.button("生成解释", key="assistant_go"):
            cache_key = (finding.get("id", ""), question)
            if cache_key != st.session_state.get("assistant_cache_key"):
                st.session_state["assistant_answer"] = ask_finding_question(
                    result, finding, question,
                    config if (config.base_url and config.model) else None, trace_log)
                st.session_state["assistant_cache_key"] = cache_key
        answer = st.session_state.get("assistant_answer")
        if answer and answer.get("answer"):
            st.markdown(answer["answer"])
            for warning in answer.get("warnings", []):
                st.warning(warning)
            if answer.get("mode") == "offline":
                st.caption("以上为离线规则解释卡（未调用模型）。")
        else:
            with st.expander("离线规则解释卡（可直接查看）", expanded=False):
                st.markdown(rule_explanation(finding))
    else:
        st.info("没有可解释的发现。")

    st.divider()
    st.markdown("**报告问答**：就整份核查结果提问")
    report_question = st.text_input("提问", key="assistant_report_question",
                                    placeholder="例如：这份报告有哪些问题？哪些该优先处理？")
    if st.button("提问", key="assistant_report_go"):
        report_key = report_question
        if report_key != st.session_state.get("assistant_report_cache_key"):
            st.session_state["assistant_report_answer"] = ask_report_question(
                result, report_question,
                config if (config.base_url and config.model) else None, trace_log)
            st.session_state["assistant_report_cache_key"] = report_key
    report_answer = st.session_state.get("assistant_report_answer")
    if report_answer and report_answer.get("answer"):
        st.markdown(report_answer["answer"])
        for warning in report_answer.get("warnings", []):
            st.warning(warning)
        hits = report_answer.get("hits", [])
        if hits:
            with st.expander(f"检索命中的相关发现（{len(hits)} 条）", expanded=False):
                for hit in hits:
                    payload = hit["payload"]
                    claim = payload.get("claim", {})
                    st.markdown(
                        f"- {STATUS_LABELS.get(payload.get('status', ''), '')}"
                        f"｜{claim.get('company', '')} {claim.get('metric', '')} "
                        f"{claim.get('value', '')}{claim.get('unit', '')}"
                        f"　{payload.get('suggestion', '')[:80]}")
            if report_answer.get("mode") == "offline":
                st.caption("以上为离线摘要（未调用模型）。")


# ---------------------------------------------------------------- 入口

def main() -> None:
    st.title("研报纠错助手 · 核查台")
    mode = st.sidebar.radio("核查方式", ["研报与财报对照", "单份文本检查"], key="review_mode")
    if mode == "单份文本检查":
        text_review_page()
        return
    st.caption("上传研报草稿与对应财报 → 输出错误位置、错误类型、原文、依据与修改建议；证据可回链原文。")
    options = settings_panel()

    report_path = save_upload(options["report"]) if options["report"] else None
    source_paths = [save_upload(item) for item in (options["sources"] or [])]
    demo_paths = st.session_state.get("demo_paths") or {}
    if not report_path and demo_paths.get("report"):
        report_path = Path(demo_paths["report"])
    if not source_paths and demo_paths.get("source"):
        source_paths = [Path(demo_paths["source"])]

    start = st.button("开始核查", type="primary",
                      disabled=not (report_path and source_paths))
    if start:
        try:
            result, out_dir, model_used = execute_check(
                report_path, source_paths, options["company"], options["engine"], options["use_model"])
        except (ValueError, OSError, ImportError) as exc:
            st.error(f"核查未完成：{exc}")
            return
        st.session_state["check_result"] = result
        st.session_state["check_dir"] = str(out_dir)
        st.session_state["selected_finding"] = ""
        st.session_state.pop("findings_table", None)
        st.success(f"核查完成，产物目录：{Path(out_dir).resolve()}"
                   + ("；本次启用了大模型候选抽取（判定仍走确定性规则）。" if model_used else "（离线规则，未调用模型）。"))

    result = st.session_state.get("check_result")
    check_dir = st.session_state.get("check_dir")
    if not result or not check_dir:
        st.info("上传至少一份研报与一份财报后点「开始核查」。"
                "D 不修改原文件；未覆盖范围（扫描件全核查、自由引用、复杂估值等）不进入自动判定。")
        return

    tabs = st.tabs(["概览", "结果列表", "证据对照", "人工复核", "导出", "助手问答", "文本补充检查"])
    with tabs[0]:
        overview_view(result, Path(check_dir))
    with tabs[1]:
        list_view(result)
    with tabs[2]:
        evidence_view(result)
    with tabs[3]:
        review_view(result, Path(check_dir))
    with tabs[4]:
        export_view(result, Path(check_dir))
    with tabs[5]:
        assistant_view(result, Path(check_dir))
    with tabs[6]:
        if result.get("text_review"):
            show_text_report(result["text_review"], Path(check_dir))
        else:
            st.info("这份历史结果未包含文本补充检查，重新运行后可查看。")


if __name__ == "__main__":
    main()
