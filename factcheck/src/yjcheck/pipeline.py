"""从文件到可审计核查结果；无联网或答案表依赖的离线基线。"""
from __future__ import annotations

import csv
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .adapters import bind_company, file_hash, load_document
from .claim_extract import extract_claims
from .models import Fact, Finding, SCHEMA_VERSION
from .rules import check_facts
from .source_extract import extract_source_facts


def check_documents(report, sources, model_config=None) -> dict:
    claims = extract_claims(report)
    # 研报中的指标表与财报使用同一套行列/表头抽取，表格断言也进入核查。
    table_claims=[f for f in extract_source_facts(report)
                  if f.metric!="publication_year" and "missing_column_heading" not in f.warnings]
    for fact in table_claims:
        if not any(c.metric==fact.metric and c.value.replace(",","")==fact.value.replace(",","")
                   and c.unit==fact.unit and c.period==fact.period and c.basis==fact.basis
                   and any(_same_location(a,b) for a in c.evidence for b in fact.evidence[:1])
                   for c in claims):
            claims.append(fact)
    source_facts = [f for doc in sources for f in extract_source_facts(doc)]
    model_traces = []
    if model_config is not None:
        from .model import extract_with_model
        extra, model_traces = extract_with_model(report, model_config)
        # 模型候选与规则交叉验证。规则已识别的声明保留确定性解释；新增候选转人工。
        keys = {(f.metric,f.value.replace(",",""),f.unit,f.period,f.evidence[0].block_id) for f in claims}
        for fact in extra:
            if (fact.metric,fact.value.replace(",",""),fact.unit,fact.period,fact.evidence[0].block_id) not in keys:
                claims.append(fact)
    blocked = [i for d in [report,*sources] for i in d.issues if i.startswith("document:")]
    findings = check_facts(claims, source_facts)
    if blocked:
        for finding in findings:
            finding.status="needs_review"
            finding.error_type="input_quality"
            finding.rule_id="INPUT_IDENTITY_OR_COMPLETENESS"
            finding.message="文件身份或完整性未通过："+"; ".join(sorted(set(blocked)))
            finding.suggestion="修复输入并重新解析后复核"
            finding.suggested_value=None
    if not claims:
        findings.append(Finding(Fact("document_coverage","","",report.period,report.company),
                                "needs_review","coverage","NO_CLAIMS","未提取到支持范围内的核查项", "检查输入或补充抽取规则"))
    input_issues=[{"file":d.path,"issues":d.issues} for d in [report,*sources] if d.issues]
    summary={s:sum(f.status==s for f in findings) for s in ("confirmed_error","needs_review","no_issue")}
    summary.update({"claims":len(claims),"source_facts":len(source_facts),
                    "input_issues":len(input_issues), "coverage":"supported_claims_only",
                    "complete":bool(claims) and not input_issues and not summary["needs_review"] and not any(t["status"]!="ok" for t in model_traces)})
    return {"schema_version":SCHEMA_VERSION,"run_id":uuid.uuid4().hex,
            "created_at":datetime.now(timezone.utc).isoformat(),"summary":summary,
            "documents":[{"role":d.role,"doc_id":d.doc_id,"sha256":d.sha256,"run_id":d.run_id,
                          "path":d.path,"company":d.company,"metadata":d.metadata} for d in [report,*sources]],
            "input_issues":input_issues,"findings":[f.to_dict() for f in findings],
            "source_facts":[f.to_dict() for f in source_facts],"model_traces":model_traces}


def _same_location(a,b):
    if a.doc_id!=b.doc_id or a.page!=b.page or a.paragraph!=b.paragraph:
        return False
    if a.block_id==b.block_id:
        return True
    if a.bbox and b.bbox:
        return min(a.bbox[2],b.bbox[2])>max(a.bbox[0],b.bbox[0]) and min(a.bbox[3],b.bbox[3])>max(a.bbox[1],b.bbox[1])
    return False


def _md(text) -> str:
    return str(text or "").replace("|","\\|").replace("\n"," ").replace("\r","")


def write_result(result: dict, out: Path) -> Path:
    dest=Path(out)/result["run_id"]
    dest.mkdir(parents=True,exist_ok=False)
    (dest/"check_result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    rows=[]
    for f in result["findings"]:
        claim=f["claim"]
        locations=[]
        for fact in f["evidence"]:
            for e in fact["evidence"]:
                loc=f"第{e['page']}页" if e["page"] is not None else f"第{e['paragraph']}段"
                locations.append(f"{Path(e['file']).name} {loc}")
        rows.append([f["status_label"],f["error_type"],claim["text"],f["suggestion"],f["message"],
                     "; ".join(dict.fromkeys(locations)),f["rule_id"],f["review_status"]])
    header=["状态","错误类型","研报原文","修改建议","依据说明","来源位置","规则","人工复核状态"]
    with (dest/"findings.csv").open("w",encoding="utf-8-sig",newline="") as stream:
        writer=csv.writer(stream)
        # 防止在 Excel 中将研报中的 =/+/−/@ 字段当作公式执行。
        writer.writerow(header)
        writer.writerows([["'"+str(v) if str(v).startswith(("=","+","-","@")) else v for v in row] for row in rows])
    lines=["# 研报核查结果", "", "仅覆盖已提取的受支持事实，不表示已审查文章全部论断。", "",
           f"已确认错误 {result['summary']['confirmed_error']}；待人工确认 {result['summary']['needs_review']}；未发现问题 {result['summary']['no_issue']}。", "",
           "| "+" | ".join(header)+" |", "|"+"---|"*len(header)]
    lines += ["| "+" | ".join(_md(v) for v in row)+" |" for row in rows]
    if result["input_issues"]:
        lines += ["", "## 输入质量问题", "", *[f"- {_md(x['file'])}: {_md('; '.join(x['issues']))}" for x in result["input_issues"]]]
    (dest/"report.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    manifest={"run_id":result["run_id"],"files":{p.name:file_hash(p) for p in dest.iterdir() if p.is_file()}}
    (dest/"manifest.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    return dest


def run_check(report_path, source_paths, out, company=None, engine="pdfplumber", model_config=None):
    out=Path(out)
    work=out/"work"/uuid.uuid4().hex
    report=load_document(report_path,"report",work,engine)
    sources=[load_document(p,"source",work,engine) for p in source_paths]
    bind_company(report,sources,company)
    result=check_documents(report,sources,model_config)
    return result,write_result(result,out)


def verify_artifacts(directory: str | Path) -> bool:
    directory=Path(directory).resolve()
    try:
        manifest=json.loads((directory/"manifest.json").read_text(encoding="utf-8"))
        expected={"check_result.json","findings.csv","report.md"}
        if set(manifest.get("files",{})) != expected:
            return False
        payload=json.loads((directory/"check_result.json").read_text(encoding="utf-8"))
        if not manifest.get("run_id") or manifest["run_id"]!=payload.get("run_id"):
            return False
        return all((directory/name).is_file() and file_hash(directory/name)==digest for name,digest in manifest["files"].items())
    except (ValueError,OSError,TypeError):
        return False
