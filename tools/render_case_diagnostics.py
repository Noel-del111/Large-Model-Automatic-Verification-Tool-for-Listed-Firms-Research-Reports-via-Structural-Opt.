"""Render individually authored diagnostic notes into an offline review pack.

Evidence and analysis JSON are inputs, never inferred human decisions. Rendering
does not overwrite human review entries or change any original benchmark score.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import quote


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def bullets(values):
    return "\n".join("- " + str(x) for x in values)


def block(value):
    return "\n```json\n" + json.dumps(value, ensure_ascii=False, indent=2) + "\n```\n"


def href(base, target, fragment=""):
    return quote(Path(os.path.relpath(target, base)).as_posix(), safe="/._-") + fragment


def review_snapshot(pack, case_ids):
    records = {}
    path = pack / "human_review.jsonl"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            cid = row.get("case_id")
            if cid in records:
                raise ValueError(f"Duplicate human review entry: {cid}")
            records[cid] = row
    labels = {"pending": "待人工裁定", "unresolved": "人工记录：仍未解决", "reviewed": "人工已裁定"}
    result = {}
    for cid in case_ids:
        row = records.get(cid, {"case_id": cid, "status": "pending"})
        status = row.get("status", "pending")
        if status == "reviewed" and not all(row.get(k) for k in
                ("analyst", "reviewer", "final_conclusion", "evidence_refs", "reviewed_at")):
            status = "incomplete"
        result[cid] = {"record": row, "display_status": status,
                       "label": labels.get(status, "人工记录未完整，尚不能认定已签核")}
    return result


def source_verification(pack):
    path = pack / "original_source_verification.json"
    record = read(path) if path.exists() else {"status": "not_available"}
    status = record.get("status", "verified" if record.get("all442_exact_match") else "not_available")
    if status == "verified":
        count = record.get("verified_records", len(record.get("records", [])))
        text = f"所附原文件核验记录显示，{count} 项冻结输入与提供的上游原始 JSON 正文及源文件哈希一致。"
    else:
        text = "本机未提供上游 eval_data.json，未重新核验其文件哈希或正文；冻结输入与历史请求指纹已独立校验。"
    return {"status": status, "message": text}


def resolve_package(pack, provenance, package=None):
    """Resolve portable batch names and historical paths without ignoring an override."""
    if package is not None:
        return Path(package).resolve()
    recorded = Path(provenance["package"])
    candidates = [recorded]
    if not recorded.is_absolute():
        candidates.extend((pack / recorded, pack.parent / recorded))
    # Old packs can contain a Windows absolute path even when rebuilt on POSIX.
    batch_name = str(provenance["package"]).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    candidates.append(Path(__file__).resolve().parents[1] / "artifacts" / batch_name)
    for candidate in candidates:
        resolved = candidate.resolve()
        if (resolved / "manifest.json").is_file():
            return resolved
    raise FileNotFoundError("Frozen package is unavailable; provide --package with its current directory")


def render(pack, package=None):
    pack = pack.resolve()
    selection, provenance = read(pack / "selection.json"), read(pack / "provenance.json")
    package = resolve_package(pack, provenance, package)
    if pack == package or pack.is_relative_to(package):
        raise ValueError("Cannot render diagnosis inside the frozen package")
    if hashlib.sha256((package / "manifest.json").read_bytes()).hexdigest() != provenance["package_manifest_sha256"]:
        raise ValueError("Case pack and source package differ")
    human = review_snapshot(pack, [c["case_id"] for c in selection["cases"]])
    human_counts = Counter(x["display_status"] for x in human.values())
    source_check = source_verification(pack)
    gold_lines = {json.loads(line)["document_id"]: n for n, line in
                  enumerate((package / "inputs/gold.research442.jsonl").read_text(encoding="utf-8").splitlines(), 1) if line.strip()}
    data, docs, requests = [], {}, {}
    required = ["expected", "observed", "evidence", "excluded_explanations", "conclusion", "conclusion_status", "uncertainties", "minimal_validation", "proposed_intermediate_metric", "human_review"]
    for chosen in selection["cases"]:
        cid, did = chosen["case_id"], chosen["document_id"].split(":")[-1]
        evidence = read(pack / f"cases/{cid}.evidence.json")
        analysis = read(pack / f"cases/{cid}.analysis.json")
        if analysis.get("case_id") != cid or any(k not in analysis for k in required):
            raise ValueError(f"Missing independent analysis fields: {cid}")
        if not analysis["conclusion"] or not analysis["evidence"] or not analysis["uncertainties"]:
            raise ValueError(f"Incomplete case conclusion: {cid}")
        if analysis["human_review"].get("status") != "pending":
            raise ValueError(f"AI note must not claim human signoff: {cid}")
        docs[did] = read(pack / evidence["document_ref"])
        requests[did] = read(pack / f"requests/{did}.json")
        if evidence["document_id"] != chosen["document_id"] or docs[did]["document_id"] != chosen["document_id"] or requests[did]["document_id"] != chosen["document_id"]:
            raise ValueError(f"Evidence document identity mismatch: {cid}")
        if requests[did]["status"] != "reconstructed_verified":
            raise ValueError(f"Unverified request: {cid}")
        row = {**chosen, "evidence": evidence, "analysis": analysis, "short_doc_id": did,
               "human_review": human[cid]["record"], "human_review_label": human[cid]["label"]}
        data.append(row)
    def evidence_link(source, row, base):
        document = docs[row["short_doc_id"]]
        request = requests[row["short_doc_id"]]
        source_code = re.fullmatch(r"artifacts/[^/]+/(source/.+):(\d+)", source)
        if source_code:
            return href(base, package / source_code[1], "#L" + source_code[2])
        pointer = source.partition("#")[2]
        own_document = source.partition("#")[0] == f"documents/{row['short_doc_id']}.json"
        if own_document and pointer.startswith("/input"):
            return href(base, package / "inputs/inputs.research442.jsonl", "#L" + str(request["source_locations"]["input"]["line"]))
        if own_document and pointer.startswith("/gold"):
            return href(base, package / "inputs/gold.research442.jsonl", "#L" + str(gold_lines[row["document_id"]]))
        if own_document and pointer.startswith("/report"):
            return href(base, package / document["sources"]["prediction"])
        match = re.match(r"/responses/(\d+)", pointer)
        if own_document and match:
            return href(base, package / document["responses"][int(match[1])]["source"])
        relative = source.partition("#")[0]
        if relative.startswith(("documents/", "requests/", "cases/")):
            return href(base, pack / relative, "#" + pointer if pointer else "")
        return None
    same_doc = {row["case_id"]: [r["case_id"] for r in data if r["document_id"] == row["document_id"] and r["case_id"] != row["case_id"]] for row in data}
    for row in data:
        cid, did, a, e = row["case_id"], row["short_doc_id"], row["analysis"], row["evidence"]
        d, request = docs[did], requests[did]
        row["evidence_links"] = [{**x, "href": evidence_link(x["source"], row, pack)} for x in a["evidence"]]
        refs = "\n".join(
            f"- [{x['source']}]({evidence_link(x['source'], row, pack / 'cases')})：{x['fact']}"
            if evidence_link(x["source"], row, pack / "cases") else f"- `{x['source']}`：{x['fact']}"
            for x in a["evidence"])
        original_input = href(pack / "cases", package / "inputs/inputs.research442.jsonl",
                              "#L" + str(request["source_locations"]["input"]["line"]))
        original_gold = href(pack / "cases", package / "inputs/gold.research442.jsonl",
                             "#L" + str(gold_lines[row["document_id"]]))
        original_prediction = href(pack / "cases", package / d["sources"]["prediction"])
        original_responses = " · ".join(f"[原始响应 {i+1}]({href(pack / 'cases', package / response['source'])})"
                                         for i, response in enumerate(d["responses"]))
        cohort_label = {"control": "成功对照", "diagnostic": "待审诊断实例", "supplemental": "机制补充样本"}.get(row["cohort"], row["cohort"])
        note = f"""# {cid} · {row['error_type']} · {row['result']} · 文档 {did}

**{a['conclusion']}**

结论状态：`{a['conclusion_status']}`。分析材料由AI整理；本次渲染时人工记录为 **{row['human_review_label']}**。分析人／复核人／最终结论以 [人工复核记录](../human_review.jsonl) 中对应条目为准；渲染后变更需重新生成页面。

批次：`research442-rerun-20261005`；检测器：hybrid；错误实例：gold_index={row['gold_index']}，all_hints_index={row['all_hints_index']}。本例是 {cohort_label}。
同文档其他抽查条目：{', '.join(same_doc[cid]) or '无'}。同文档或同一问题的FP/FN两端不可重复计算业务收益。

## 1. 预期与金标核查

{a['expected']}

金标对象：{block(e['gold'])}

[完整冻结金标]({original_gold}) · [冻结最终预测及原始候选]({original_prediction}) · {original_responses}

## 2. 输入核查

冻结元数据记载上游来源为 `eval_data.json` 数组第 {d['input']['source_row']} 项（1-based）。{source_check['message']}详见 [原文件核对](../original_source_verification.json)。[本例冻结输入]({original_input}) 可直接在仓库中读取。该批输入是基准文本，没有PDF/OCR解析步骤。

本例正文字符数：{len(d['input']['content'])}；内容 SHA-256：`{d['input']['content_sha256']}`。覆盖记录为 `{d['report']['coverage']['context_mode']}`，处理字符 {d['report']['coverage']['processed_chars']}。

[完整重建请求（含system prompt、示例、当前上下文；需本地 rebuild）](../requests/{did}.json) 的 {len(request['requests'])} 个请求与历史缓存和trace消息指纹一致。该证据是**重建且可验证的messages，不是原始HTTP报文**；完整消息存在不代表模型正确关注了每一处信息。

## 3. 原始回复与 4. 后处理

{a['observed']}

[完整文档证据（需本地 rebuild）](../documents/{did}.json) 汇总全文、全部gold、原始响应content、raw_candidates、rejected_candidates、最终提示、调用与覆盖记录；上方冻结源链接无需生成该汇总即可检查。既可检查局部引用，也可检查未入选候选及匹配竞争。没有逐步运行事件的部分，按冻结代码重建解释，不冒称原运行事件日志。

最终本实例预测：{block(e['prediction'])}

关键证据：

{refs}

## 5. 评分核查

评分器要求同类型、覆盖每个完整金标span、文本一致，再做文档内最大一对一匹配。FP在这里指未匹配预测，不能直接当作人工认定的业务误报。FN也不等价于原始回复完全没有相关判断。

逐对检查：{block(e['scoring_checks'])}

## 独立结论及排除范围

{a['conclusion']}

已排除／限定的解释：

{bullets(a['excluded_explanations'])}

尚未确认：

{bullets(a['uncertainties'])}

## 最小验证

{block(a['minimal_validation'])}

## 由本例提出的中间指标

{block(a['proposed_intermediate_metric'])}

此指标为待会议裁定的候选，未据此修改产品、评分公式或优化优先级。
"""
        (pack / f"cases/{cid}.md").write_text(note, encoding="utf-8", newline="\n")
    cohorts = Counter(x["cohort"] for x in data)
    count_text = f"{cohorts['diagnostic']} 个诊断实例 + {cohorts['control']} 个成功对照 + {cohorts['supplemental']} 个补充样本；{len(docs)} 篇文档"
    human_text = f"渲染时人工已完整裁定 {human_counts['reviewed']} 条，待处理或未完整 {len(data)-human_counts['reviewed']} 条；当前状态以 human_review.jsonl 为准。"
    index = ["# 逐 case 诊断复核入口", "", count_text + "；每条均有独立AI初步分析。" + human_text, "", "[会议与优化准入](meeting.md) · [使用与重建说明](README.md) · [交互阅读页（需本地 rebuild）](index.html)", "", "抽样单位为错误实例，按类型与FP/FN分层；每层尽量不同文档，优先保留定位失败机制，并固定两个已讨论案例及日历自动确认对照。补充样本另列机制覆盖依据。这是诊断抽样，不能据其占比估计总体根因。", "", "|Case|文档|类型|评分|逐案AI初步结论|人工记录快照|", "|---|---|---|---|---|---|"]
    for r in data:
        conclusion = r["analysis"]["conclusion"].replace("|", "／").replace("\n", " ")
        index.append(f"|[{r['case_id']}](cases/{r['case_id']}.md)|{r['short_doc_id']}|{r['error_type']}|{r['result']}|{conclusion}|{r['human_review_label']}|")
    (pack / "index.md").write_text("\n".join(index) + "\n", encoding="utf-8", newline="\n")
    review_path = pack / "human_review.jsonl"
    existing_review_bytes = review_path.read_bytes() if review_path.exists() else b""
    existing_review_ids = {json.loads(line)["case_id"] for line in existing_review_bytes.decode("utf-8").splitlines() if line.strip()}
    new_reviews = "".join(json.dumps({"case_id": r["case_id"], "status": "pending", "analyst": None, "reviewer": None, "gold_supported": None, "business_verdict": None, "primary_divergence_stage": None, "final_conclusion": None, "evidence_refs": [], "excluded_explanations": [], "remaining_unknowns": [], "validation_result": None, "review_minutes": None, "reviewed_at": None}, ensure_ascii=False) + "\n" for r in data if r["case_id"] not in existing_review_ids)
    if new_reviews:
        with review_path.open("ab") as stream:
            if existing_review_bytes and not existing_review_bytes.endswith(b"\n"):
                stream.write(b"\n")
            stream.write(new_reviews.encode("utf-8"))
    payload = json.dumps({"cases": data, "documents": docs, "requests": requests,
                          "source_verification": source_check}, ensure_ascii=False).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    page = HTML.replace("__SUMMARY__", html.escape(count_text)).replace("__HUMAN_SUMMARY__", html.escape(human_text)).replace("__DATA__", payload)
    (pack / "index.html").write_text(page, encoding="utf-8", newline="\n")
    dump(pack / "validation.json", {"case_count": len(data), "diagnostic_cases": cohorts["diagnostic"], "control_cases": cohorts["control"], "supplemental_cases": cohorts["supplemental"], "documents": len(docs), "independent_analysis_files": len(data), "human_review_counts": dict(human_counts), "human_finalized_cases": human_counts["reviewed"], "original_source_status": source_check["status"], "all_selected_requests_verified": True, "selection": dict(Counter(x["result"] for x in data)), "new_model_calls": 0, "limits": ["字段完整性不是人工业务正确性签核。", "本批为新442，不能复现旧PDF批次。", "当前读取页展示AI初步分析与渲染时人工状态；后续签核以human_review.jsonl为准。"]})
    (pack / "cases.jsonl").write_text("".join(json.dumps({**r["evidence"], "analysis": r["analysis"], "same_document_case_ids": same_doc[r["case_id"]]}, ensure_ascii=False) + "\n" for r in data), encoding="utf-8", newline="\n")
    manifest = [{"path": p.relative_to(pack).as_posix(), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(pack.rglob("*")) if p.is_file() and p.name not in {"manifest.json", "human_review.jsonl"}]
    dump(pack / "manifest.json", {"files": manifest, "mutable_exclusions": ["human_review.jsonl"], "note": "诊断材料完整性清单；真人复核单独维护，不覆盖历史证据。"})
    print(json.dumps(read(pack / "validation.json"), ensure_ascii=False))


HTML = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>442 · 逐案诊断复核</title>
<style>
:root{font-family:system-ui,"Microsoft YaHei",sans-serif;color:#162b35;background:#f3f5f4;line-height:1.65}*{box-sizing:border-box}body{margin:0}header{background:#173c3b;color:white;padding:26px 32px}h1{font-size:27px;margin:0 0 5px}header p{margin:6px 0;color:#d8e4df}.layout{display:grid;grid-template-columns:330px 1fr;max-width:1600px;margin:auto}aside{padding:22px 16px;border-right:1px solid #d3ddd7;height:calc(100vh - 158px);position:sticky;top:0;overflow:auto}input,select{padding:10px;border:1px solid #b5c9bf;border-radius:5px;width:100%;margin:4px 0 9px;background:white;color:#162b35}button{cursor:pointer;font:inherit}#list button{width:100%;text-align:left;padding:12px;margin:4px 0;border:1px solid #d2ddd6;background:white;border-radius:5px}#list button.active{border-left:5px solid #147165;background:#e2eeea}main{padding:26px 36px;min-width:0}h2{font-size:24px;margin-top:0}h3{font-size:18px;margin-top:28px}p{overflow-wrap:anywhere}.lead{border-left:4px solid #147165;background:white;padding:18px 22px}.meta{color:#546a6c;font-size:14px}.tag{display:inline-block;font-size:12px;border-radius:3px;padding:1px 7px;background:#dce8e2;margin-right:6px}.pending{background:#ffedc7}.card,details{background:white;border:1px solid #d6e0da;border-radius:6px;padding:14px 18px;margin:12px 0}summary{cursor:pointer;font-weight:600}pre{white-space:pre-wrap;word-break:break-word;max-height:520px;overflow:auto;font:13px/1.65 ui-monospace,Consolas,monospace}a{color:#096860}header a{color:#d1f3e8}li{margin:8px 0}.two{display:grid;grid-template-columns:1fr 1fr;gap:12px}.key{font-weight:600;color:#36565c}.evidence{font-size:14px}#count{font-size:13px;color:#56716a;padding:8px 0}@media(max-width:900px){.layout{grid-template-columns:1fr}aside{height:auto;position:static;border-right:0}#list{max-height:240px;overflow:auto}main{padding:20px}.two{grid-template-columns:1fr}}@media print{aside{display:none}.layout{display:block}header{color:black;background:white}main{padding:0}pre{max-height:none}details{break-inside:avoid}}
</style></head><body><header><h1>逐 case 深度诊断</h1><p>__SUMMARY__ · 新 442 冻结批次</p><p>__HUMAN_SUMMARY__ <a href="meeting.md">会议与优化准入</a> · <a href="README.md">说明</a> · <a href="human_review.jsonl">人工复核记录</a></p></header>
<div class="layout"><aside><label for="search">搜索 case、文档、类型或结论</label><input id="search" placeholder="例如 000368 / 引用 / 确认"><label for="kind">筛选评分结果</label><select id="kind"><option value="">全部实例</option><option>FP</option><option>FN</option><option>TP</option></select><div id="count"></div><nav id="list" aria-label="案例列表"></nav></aside><main id="detail"></main></div>
<script type="application/json" id="payload">__DATA__</script>
<script>
'use strict';const D=JSON.parse(document.getElementById('payload').textContent);const E=s=>String(s??'未记录').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));const J=x=>'<pre>'+E(JSON.stringify(x,null,2))+'</pre>';const L=xs=>'<ul>'+xs.map(x=>'<li>'+E(x)+'</li>').join('')+'</ul>';let current=D.cases.some(x=>x.case_id===location.hash.slice(1))?location.hash.slice(1):'C001';
function details(t,x){return '<details><summary>'+E(t)+'</summary>'+J(x)+'</details>'}
function show(id){current=id;const c=D.cases.find(x=>x.case_id===id),a=c.analysis,e=c.evidence,d=D.documents[c.short_doc_id],r=D.requests[c.short_doc_id];location.hash=id;
document.getElementById('detail').innerHTML='<h2>'+E(id+' · '+c.error_type)+'</h2><p><span class="tag">'+E(c.result)+'</span><span class="tag">'+E(({control:'成功对照',diagnostic:'诊断实例',supplemental:'机制补充样本'})[c.cohort]||c.cohort)+'</span><span class="tag pending">'+E(c.human_review_label)+'</span></p><p class="meta">文档 '+E(c.short_doc_id)+' · gold_index='+E(c.gold_index)+' · all_hints_index='+E(c.all_hints_index)+' · <a href="cases/'+id+'.md">独立分析文档</a></p><div class="lead">'+E(a.conclusion)+'</div><h3>1 · 预期与金标</h3><p>'+E(a.expected)+'</p>'+details('本例金标（FP 无配对金标，请查看全文所有金标）',e.gold)+'<h3>2 · 输入是否完整进入请求构造</h3><p>正文 '+d.input.content.length+' 字；覆盖模式 '+E(d.report.coverage.context_mode)+'；重建 '+r.requests.length+' 个请求，全部匹配缓存和调用 trace 的消息指纹。'+E(D.source_verification.message)+'</p><p class="meta">这些是重建且可验证的消息，不是原始HTTP报文。完整输入不证明模型正确利用了全部信息。此批为文本，未经过OCR。</p>'+details('完整请求：system prompt、few-shot、当前任务与指纹',r)+details('输入正文及来源元数据',d.input)+'<h3>3 · 原始回复 / 4 · 后处理</h3><p>'+E(a.observed)+'</p>'+details('原始模型回复与调用记录',d.responses)+details('原始候选与明确拒绝项',{raw_candidates:d.report.raw_candidates,rejected_candidates:d.report.rejected_candidates})+details('本例最终预测',e.prediction)+details('全部最终提示与文档内所有金标',{all_hints:d.all_hints,gold:d.gold,matched_pairs:d.matched_pairs})+'<h3>5 · 评分差异</h3><p>同类型、每个金标span完整包含、文本一致，再做最大一对一匹配。未匹配预测并不自动等于业务误报。</p>'+details('逐对匹配检查（索引从0开始）',e.scoring_checks)+details('人工复核快照（渲染后变更请查独立记录）',c.human_review)+'<h3>关键证据</h3><div class="card evidence">'+c.evidence_links.map(x=>'<p class="key">'+(x.href?'<a href="'+E(x.href)+'">'+E(x.source)+'</a>':E(x.source))+'</p><p>'+E(x.fact)+'</p>').join('')+'</div><div class="two"><section><h3>已排除与结论边界</h3>'+L(a.excluded_explanations)+'</section><section><h3>尚未确认</h3>'+L(a.uncertainties)+'</section></div><h3>最小验证办法</h3><div class="card">'+Object.entries(a.minimal_validation).map(([k,v])=>'<p><span class="key">'+E(({change:'改动',control:'对照',success_criterion:'成功标准',regression_check:'回归检查'})[k]||k)+'</span>：'+E(v)+'</p>').join('')+'</div><h3>本例反向提出的中间指标</h3><div class="card">'+Object.entries(a.proposed_intermediate_metric).map(([k,v])=>'<p><span class="key">'+E(({name:'指标',definition:'定义及分母',why_this_case:'与本例的关系'})[k]||k)+'</span>：'+E(v)+'</p>').join('')+'</div><p class="meta">候选指标尚未实施。人工最终结论、分析人、复核人和耗时请填写 human_review.jsonl；当前页面不代替签核。先逐案讨论，再依据会议结果决定优化。</p>';
document.querySelectorAll('#list button').forEach(b=>b.classList.toggle('active',b.dataset.id===id));}
function filter(){const q=document.getElementById('search').value.toLowerCase(),kind=document.getElementById('kind').value;const cases=D.cases.filter(c=>(!kind||c.result===kind)&&JSON.stringify([c.case_id,c.document_id,c.error_type,c.analysis.conclusion]).toLowerCase().includes(q));document.getElementById('count').textContent=cases.length+' / '+D.cases.length+' 个实例';document.getElementById('list').innerHTML=cases.map(c=>'<button data-id="'+c.case_id+'" class="'+(c.case_id===current?'active':'')+'"><span class="tag">'+E(c.result)+'</span>'+E(c.case_id+' · '+c.short_doc_id)+'<br>'+E(c.error_type)+'</button>').join('');document.querySelectorAll('#list button').forEach(b=>b.addEventListener('click',()=>show(b.dataset.id)));}
window.addEventListener('hashchange',()=>{const id=location.hash.slice(1);if(id!==current&&D.cases.some(x=>x.case_id===id)){show(id);filter();}});document.getElementById('search').addEventListener('input',filter);document.getElementById('kind').addEventListener('change',filter);filter();show(current);
</script></body></html>'''


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack", type=Path)
    parser.add_argument("--package", type=Path, help="Frozen package location; overrides recorded local path")
    args = parser.parse_args()
    render(args.pack, args.package)
