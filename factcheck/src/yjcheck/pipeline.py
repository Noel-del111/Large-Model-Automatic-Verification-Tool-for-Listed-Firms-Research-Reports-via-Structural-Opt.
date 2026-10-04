"""从文件到可审计核查结果；无联网或答案表依赖的离线基线。"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .adapters import bind_company, file_hash, load_document
from .claim_extract import METRICS, METRIC_RE, NUMBER_RE, extract_claims
from .intrinsic import check_intrinsic_consistency
from .models import Fact, Finding, SCHEMA_VERSION
from .rules import check_facts
from .source_extract import extract_source_facts


def _literal_value(value):
    return re.sub(r"[\s,，]", "", str(value)).replace("−", "-").replace("－", "-")


def _value_occurrences(fact, report):
    """Locate the literal number, independently of the model's period/scope.

    A sentence/paragraph overlap alone is insufficient: repeated equal numbers
    must remain separate claims. Missing or ambiguous numeric anchors cannot merge.
    """
    blocks = {block.block_id: block for block in report.blocks}
    locations = fact.attributes.get("value_locations", [])
    if not locations and "value_start" in fact.attributes and len(fact.evidence) == 1:
        locations = [{"block_id": fact.evidence[0].block_id,
                      "char_start": fact.attributes["value_start"], "char_end": fact.attributes.get("value_end")}]
    occurrences = set()
    explicitly_labelled = set()
    for evidence in fact.evidence:
        block = blocks.get(evidence.block_id)
        if block is None or evidence.doc_id != report.doc_id:
            continue
        bounds = [(location.get("char_start"), location.get("char_end")) for location in locations
                  if location.get("block_id") == evidence.block_id]
        explicit = bool(bounds)
        if not bounds:
            bounds = [(evidence.char_start, evidence.char_end)]
        for start, end in bounds:
            if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(block.text):
                continue
            raw = block.text[start:end]
            if not explicit and raw != evidence.text:
                continue
            if not explicit and fact.attributes.get("extraction") == "model" and block.text.count(raw) != 1:
                continue
            mapping = [start + index for index, char in enumerate(raw) if not char.isspace()]
            compact = "".join(char for char in raw if not char.isspace())
            for number in NUMBER_RE.finditer(compact):
                unit = number["unit"].replace("％", "%").replace("／", "/")
                if _literal_value(number["value"]) != _literal_value(fact.value) or unit != fact.unit:
                    continue
                if not explicit:
                    metrics = list(METRIC_RE.finditer(compact[:number.start()]))
                    if not metrics or METRICS[metrics[-1].group()] != fact.metric:
                        continue
                occurrence = (evidence.doc_id, block.block_id, mapping[number.start()], mapping[number.end()-1]+1)
                occurrences.add(occurrence)
                labels = list(re.finditer(r"调整前|重述前|调整后|重述后|影响(?:金额|数)?|变动金额", compact[:number.start()]))
                if labels:
                    word = labels[-1].group()
                    stated_basis = "before" if word.endswith("前") else "after" if word.endswith("后") else "change"
                    if stated_basis == fact.basis:
                        explicitly_labelled.add(occurrence)
    if len(occurrences) > 1 and len(explicitly_labelled) == 1:
        # Equal before/after values in one quote are distinguishable only when
        # the original text explicitly labels the particular numeric occurrence.
        return explicitly_labelled
    return occurrences


def _merge_model_claims(claims, candidates, report):
    """Attach another interpretation of one observed amount to its existing fact.

    The canonical fact's dimensions, warnings and verdict inputs stay unchanged.
    Unmatched model candidates keep their original review-required semantics.
    """
    aligned = 0
    for candidate in candidates:
        positions = _value_occurrences(candidate, report)
        matches = []
        if len(positions) == 1:
            doc_id, block_id, start, end = next(iter(positions))
            for claim in claims:
                if (claim.metric, _literal_value(claim.value), claim.unit, claim.company, claim.currency) != (
                        candidate.metric, _literal_value(candidate.value), candidate.unit, candidate.company, candidate.currency):
                    continue
                locations = _value_occurrences(claim, report)
                if len(locations) == 1 and any(doc_id == doc and block_id == block and min(end, hi) > max(start, lo)
                                                for doc, block, lo, hi in locations):
                    matches.append(claim)
        if len(matches) == 1:
            canonical = matches[0]
            canonical.attributes.setdefault("model_extraction_alternatives", []).append({
                "alignment": "same_metric_literal_value_unit_and_unique_numeric_occurrence",
                "model_semantics": "unverified; canonical dimensions were not changed",
                "dimension_differences": {field: {"canonical": getattr(canonical, field), "model": getattr(candidate, field)}
                                          for field in ("period", "basis", "scope", "currency")
                                          if getattr(canonical, field) != getattr(candidate, field)},
                "candidate": candidate.to_dict()})
            aligned += 1
        else:
            if any(claim.fact_id == candidate.fact_id for claim in claims):
                # Fact IDs omit extraction provenance and exact numeric offsets.
                # An unresolved model interpretation must not replace a rule fact
                # in the downstream identity map, nor be silently discarded.
                candidate.attributes["original_model_fact_id"] = candidate.fact_id
                candidate.attributes["alignment_warning"] = "unresolved_model_identity_collision"
                candidate.fact_id = hashlib.sha256(("unresolved_model:" + candidate.fact_id + ":" + str(len(claims))).encode()).hexdigest()[:20]
            claims.append(candidate)
    return aligned


def check_documents(report, sources, model_config=None) -> dict:
    started = time.monotonic()
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
    extracted_at = time.monotonic()
    model_traces = []
    model_candidates_aligned = 0
    if model_config is not None:
        from .model import extract_with_model
        extra, model_traces = extract_with_model(report, model_config)
        model_candidates_aligned = _merge_model_claims(claims, extra, report)
    blocked = [i for d in [report,*sources] for i in d.issues if i.startswith("document:")]
    modeled_at = time.monotonic()
    claims = list({claim.fact_id: claim for claim in claims}.values())
    fact_findings = check_facts(claims, source_facts)
    intrinsic_findings = check_intrinsic_consistency(report, claims)
    findings = fact_findings + intrinsic_findings
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
    review_ids = {f.claim.fact_id for f in findings if f.status == "needs_review"}
    summary.update({
        "automatic_claims": len({f.claim.fact_id for f in fact_findings
                                 if f.status in {"confirmed_error", "no_issue"}} - review_ids),
        "review_claims": len({f.claim.fact_id for f in fact_findings} & review_ids),
        "intrinsic_candidates": len(intrinsic_findings),
        "model_candidates_aligned": model_candidates_aligned,
        "needs_review_by_rule": dict(sorted(Counter(f.rule_id for f in findings if f.status == "needs_review").items())),
        "stage_seconds": {"extraction": round(extracted_at - started, 6),
                          "model": round(modeled_at - extracted_at, 6),
                          "verification": round(time.monotonic() - modeled_at, 6)},
    })
    # 结构化补证请求：needs_review 才产出 ask 与所需材料；confirmed_error/no_issue 为 proceed。
    from .evidence_requests import attach_evidence_requests
    evidence_docs = [{"role": d.role, "issues": d.issues, "path": d.path} for d in [report, *sources]]
    finding_dicts = [attach_evidence_requests(f.to_dict(), evidence_docs) for f in findings]
    ask_findings = [fd for fd in finding_dicts if fd["decision"] == "ask"]
    summary["evidence_requests"] = len(ask_findings)
    summary["evidence_request_items"] = sum(len(fd["evidence_request"]) for fd in ask_findings)
    result = {"schema_version":SCHEMA_VERSION,"run_id":uuid.uuid4().hex,
            "created_at":datetime.now(timezone.utc).isoformat(),"summary":summary,
            "documents":[{"role":d.role,"doc_id":d.doc_id,"sha256":d.sha256,"run_id":d.run_id,
                          "path":d.path,"company":d.company,"metadata":d.metadata} for d in [report,*sources]],
            "input_issues":input_issues,"findings":finding_dicts,
            "source_facts":[f.to_dict() for f in source_facts],"model_traces":model_traces}
    # Text-only findings have their own contract; do not forge financial Facts
    # or external evidence to squeeze them into the paired check schema.
    from .text_review import detect_text
    text_client = None
    if model_config is not None and getattr(model_config, "review_text", False):
        from .model_runtime import BudgetedChatClient
        text_client = BudgetedChatClient(model_config)
    text_result = detect_text(report.text, document_id=report.doc_id, scene="研报", detector="hybrid", chat=text_client,
                              max_input_tokens=text_client.settings.context_tokens-text_client.settings.max_output_tokens if text_client else 16000)
    offset = 0
    locations = []
    for block in report.blocks:
        locations.append({"start": offset, "end": offset+len(block.text), "block_id": block.block_id,
                          "page": block.page, "bbox": block.bbox, "quality": block.status})
        offset += len(block.text)+1
    text_result["source_locations"] = locations
    for error in text_result["errors"]:
        sites = [site for site in locations if any(span["start"] < site["end"] and site["start"] < span["end"] for span in error["spans"])]
        error["source_locations"] = sites
        if blocked or any(site["quality"] != "ok" for site in sites):
            error["status"] = "needs_review"
            error["validation"] = "input_quality_requires_review"
    if blocked:
        text_result["coverage"]["input_quality_limitations"] = blocked
    result["text_review"] = text_result
    summary["text_candidates"] = len(text_result["errors"])
    summary["text_confirmed"] = sum(e["status"] == "confirmed_error" for e in text_result["errors"])
    summary["text_needs_review"] = sum(e["status"] == "needs_review" for e in text_result["errors"])
    from .review_hints import all_review_hints
    summary["text_rejected_hints"] = len(text_result.get("rejected_candidates", []))
    summary["text_review_total_hints"] = sum(e.get("status", "needs_review") == "needs_review"
                                             for e in all_review_hints(text_result)["errors"])
    summary["stage_seconds"]["text_review"] = round(time.monotonic()-modeled_at-summary["stage_seconds"]["verification"], 6)
    return result


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
    from .evidence_requests import request_text
    rows=[]
    for f in result["findings"]:
        claim=f["claim"]
        locations=[]
        for fact in f["evidence"]:
            for e in fact["evidence"]:
                loc=f"第{e['page']}页" if e["page"] is not None else f"第{e['paragraph']}段"
                locations.append(f"{Path(e['file']).name} {loc}")
        rows.append([f["status_label"],f["error_type"],claim["text"],f["suggestion"],f["message"],
                     "; ".join(dict.fromkeys(locations)),f["rule_id"],f["review_status"],
                     request_text(f)])
    header=["状态","错误类型","研报原文","修改建议","依据说明","来源位置","规则","人工复核状态","补充证据清单"]
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
    started = time.monotonic()
    out=Path(out)
    work=out/"work"/uuid.uuid4().hex
    report=load_document(report_path,"report",work,engine)
    sources=[load_document(p,"source",work,engine) for p in source_paths]
    bind_company(report,sources,company)
    parsed_at = time.monotonic()
    result=check_documents(report,sources,model_config)
    result["runtime"] = {"parse_seconds": round(parsed_at-started, 6), "total_seconds": round(time.monotonic()-started, 6)}
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
