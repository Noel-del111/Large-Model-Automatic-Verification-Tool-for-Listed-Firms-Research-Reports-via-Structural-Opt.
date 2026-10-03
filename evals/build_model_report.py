"""Create a source-linked Chinese model report from completed score artifacts.

No inference or API access. Scope labels are explicit operator declarations;
overlap checks can disprove new-sample independence, not certify unseen history.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCOPES = {"dev_tuning": "开发集调参", "dev_validation": "新开发样本验证",
          "dev_expansion": "开发集扩展测试", "frozen_eval": "冻结评测",
          "development_stage": "开发阶段验证（非最终测试）"}
ARMS = {"legacy_rules": "冻结旧版规则", "model_direct": "模型直接检测", "hybrid": "组合流程"}


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def pct(value):
    return "未测量" if value is None else f"{value:.2%}"


def number(value, digits=4):
    return "未测量" if value is None else f"{value:.{digits}f}"


def cell(value):
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def assignment(value):
    label, separator, path = value.partition("=")
    if not separator or not label.strip() or not path.strip():
        raise ValueError("参数须使用 LABEL=VALUE，且两侧非空")
    return label.strip(), path.strip()


def link(label, path, out):
    relative = Path(os.path.relpath(Path(path).resolve(), Path(out).resolve().parent)).as_posix()
    return f"[{cell(label)}](<{relative}>)"


def score_ids(score):
    ids = set()
    for arm in score.get("detectors", {}).values():
        ids.update(row["document_id"] for row in arm.get("candidate_detection", {}).get("by_document", []))
    return ids


def load_runs(run_args, scope_args):
    scopes = dict(assignment(value) for value in scope_args)
    runs, labels = [], set()
    for value in run_args:
        label, directory = assignment(value)
        if label in labels:
            raise ValueError("运行标签不可重复：" + label)
        labels.add(label)
        scope = scopes.get(label, "dev_tuning")
        if scope not in SCOPES:
            raise ValueError("未知范围标签：" + scope)
        directory = Path(directory)
        score = read(directory / "score.json")
        if score.get("run_config", {}).get("mode") != "model":
            raise ValueError("模型报告只接收已评分的 model 运行：" + label)
        if not score.get("detectors"):
            raise ValueError("评分中缺少检测组：" + label)
        runs.append({"label": label, "directory": directory, "score": score, "scope": scope, "ids": score_ids(score)})
    if set(scopes) - labels:
        raise ValueError("scope 指向未提供的运行：" + ",".join(sorted(set(scopes) - labels)))
    return runs


def rates_row(score):
    if score is None:
        return "未测量 | 未测量 | 未测量 | 未测量 | 未测量 | 未测量"
    return " | ".join(str(score[key]) for key in ("true_positive", "false_positive", "false_negative")) + \
           " | " + " | ".join(pct(score.get(key)) for key in ("precision", "recall", "f1"))


def research_and_other_scores(arm):
    """Use scene groups only for membership, and all hints for TP/FP/FN.

    Adding scene-level emitted scores would silently omit rejected hints. Ratios
    must be recomputed from counts, not averaged over documents or scenarios.
    """
    rows = arm.get("all_review_hints_detection", {}).get("by_document", [])
    locations = {}
    for scene, score in arm.get("by_scene", {}).items():
        for row in score.get("by_document", []):
            identity = row["document_id"]
            if identity in locations and locations[identity] != scene:
                raise ValueError("同一文档被分入多个场景")
            locations[identity] = scene
    identities = [row["document_id"] for row in rows]
    if len(identities) != len(set(identities)):
        raise ValueError("全部提示评分包含重复文档")
    if not rows or set(identities) != set(locations):
        return None
    scopes = {"研报主测（个股＋行业）": [], "其他金融文档补测": []}
    for row in rows:
        scope = "研报主测（个股＋行业）" if locations[row["document_id"]] in {"个股研报", "行业研报"} else "其他金融文档补测"
        scopes[scope].append(row)
    result = {}
    for scope, selected in scopes.items():
        if not selected:
            continue
        totals = {key: sum(row[key] for row in selected) for key in ("true_positive", "false_positive", "false_negative")}
        tp, fp, fn = (totals[key] for key in ("true_positive", "false_positive", "false_negative"))
        result[scope] = {**totals, "documents": len(selected),
                         "precision": tp / (tp + fp) if tp + fp else 0.,
                         "recall": tp / (tp + fn) if tp + fn else 0.,
                         "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.}
    return result


def render(runs, out, ledger_path=None, details=()):
    lines = ["# 第二版真实模型阶段报告", "",
             "本报告只读取已完成评分的真实模型运行产物，未在生成报告时发起调用。主指标覆盖全部输出提示，包含无法定位或格式无效的模型返回；已接受输出的指标另列，防止过滤无效提示后误称系统效果提升。", "",
             "生成时间（UTC）：" + datetime.now(timezone.utc).isoformat(timespec="seconds") + "。", "",
             "## 实验范围与配置", "",
             "范围标签由执行者指定；默认均为开发集调参。新开发样本验证仍属于开发集，不能称为 10 月 5 日或 10 月 7 日的保留集盲测。样本集合有交集的运行不能相加为独立样本数。", "",
             "| 运行 | 范围 | 计划篇数 | 所有组执行完成 | 模型 / 思考 / 强度 | 输出上限 token |",
             "| --- | --- | ---: | ---: | --- | ---: |"]
    for run in runs:
        s = run["score"]
        config = s.get("run_config", {})
        req = config.get("model_request") or {}
        lines.append(f"| {cell(run['label'])} | {SCOPES[run['scope']]} | {s.get('requested_documents', '未记录')} | {s.get('paired_complete_documents', '未记录')} | {cell(config.get('model'))} / {cell(req.get('thinking', '未记录'))} / {cell(req.get('reasoning_effort') or '默认/未记录')} | {cell((config.get('runtime') or {}).get('max_output_tokens', '未记录'))} |")
    tuning_ids = set().union(*(r["ids"] for r in runs if r["scope"] == "dev_tuning"))
    for run in runs:
        if run["scope"] in {"dev_validation", "frozen_eval"}:
            overlap = len(run["ids"] & tuning_ids)
            lines += ["", f"- {cell(run['label'])}：与本报告已列调参文档交集 {overlap} 篇。" +
                      ("存在交集，不能作为全部新样本的独立验证。" if overlap else "此检查仅覆盖本报告列出的运行，不证明未受其他调参或预训练数据影响。")]

    lines += ["", "## 全部提示口径：主要检测结果", "",
              "原句相同或包含、错误类型一致、确定性一对一匹配；多片段错误仍为一个实例。所有计划文档进入分母，缺失预测仍计漏检。无效定位提示无法得分，仍计入 FP；这衡量提示质量，不代表每条未匹配提示已经人工证实为事实错误。", "",
              "| 运行 | 流程 | TP | FP | FN | P | R | F1 | 待复核提示总量 | 被拒提示 |",
              "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for run in runs:
        for key, arm in run["score"]["detectors"].items():
            lines.append(f"| {cell(run['label'])} | {ARMS.get(key, key)} | {rates_row(arm.get('all_review_hints_detection'))} | {arm.get('human_review_hints_total', '未测量')} | {arm.get('rejected_model_candidates', '未记录')} |")
    lines += ["", "待复核总量包含已输出 needs_review 与尚未计入其中的拒绝提示；直接检测已输出的无效候选不重复计数。重复提示计入工作量，不能把该数解释为独立错误数。执行完成只表示请求和处理路径完成，候选质量或全文事实正确性仍需另审。", "",
              "## 研报主测与其他文档补测", "",
              "按预先确定的场景分类，将个股研报与行业研报合并作为主测，其余金融文档作为补测。分组仅使用场景归属；计数来自包含拒绝提示的全部提示口径，失败文档保留，P/R/F1 从 TP、FP、FN 合计重新计算，不平均各文档或各场景的百分比。", "",
              "| 运行 | 流程 | 范围 | 文档数 | TP | FP | FN | P | R | F1 |",
              "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for run in runs:
        for key, arm in run["score"]["detectors"].items():
            scopes = research_and_other_scores(arm)
            if scopes is None:
                lines.append(f"| {cell(run['label'])} | {ARMS.get(key, key)} | 场景归属或逐文档全部提示计数缺失 | 未测量 | {rates_row(None)} |")
                continue
            for scope, metrics in scopes.items():
                lines.append(f"| {cell(run['label'])} | {ARMS.get(key, key)} | {scope} | {metrics['documents']} | {rates_row(metrics)} |")
    lines += ["", "以上每一行仍是其对应运行的范围；调参样本、冻结评测和不同参数的长文试验不合并为一项总体提升。", "",
              "## 已接受输出与自动确认", "",
              "已接受输出沿用 errors 字段：直接检测包括已标记无效定位的错误项，组合组将拒绝提示另存。因此跨组判断以全部提示主表为准。下面同时披露原口径，便于复核后处理的取舍。", "",
              "| 运行 | 流程 | 已接受输出 P / R / F1 | 独立类型准确率 | 自动确认数 | 自动确认 TP / FP / FN | 自动确认 P / R / F1 |",
              "| --- | --- | --- | ---: | ---: | --- | --- |"]
    for run in runs:
        for key, arm in run["score"]["detectors"].items():
            emitted, verified = arm.get("candidate_detection", {}), arm.get("verified_detection", {})
            prf = lambda row: " / ".join(pct(row.get(k)) for k in ("precision", "recall", "f1"))
            counts = " / ".join(str(verified.get(k, "未记录")) for k in ("true_positive", "false_positive", "false_negative"))
            lines.append(f"| {cell(run['label'])} | {ARMS.get(key, key)} | {prf(emitted)} | {pct(emitted.get('type_accuracy'))} | {arm.get('candidate_status_counts', {}).get('confirmed_error', 0)} | {counts} | {prf(verified)} |")
    lines += ["", "自动确认仅来自程序支持的确定性验证。未知语义和模型自述不能升级为自动确认；确认比例也不是全文所有应核查事项的覆盖率。零自动确认时程序可能给出 P=0，此值不表示已验证了一批错误结论。", "",
              "## 运行成本与延迟", "",
              "| 运行 | 唯一模型调用 | 输入 token | 输出 token（含服务方计入的思考） | 保守核算费用（元） | 接口或服务校验失败 | 响应解析失败 |",
              "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for run in runs:
        usage = run["score"].get("unique_model_usage", {})
        lines.append(f"| {cell(run['label'])} | {usage.get('calls', '未记录')} | {usage.get('input_tokens', '未记录')} | {usage.get('output_tokens', '未记录')} | {number(usage.get('accounted_cny'), 6)} | {usage.get('transport_or_provider_failed_calls', usage.get('failed_calls', '未记录'))} | {usage.get('response_parse_failed_calls', '未记录')} |")
    lines += ["", "每个运行按唯一 call_id 汇总，直接检测和组合组共用返回的费用不重复相加。接口成功仍可能返回无法解析的 JSON，因此响应解析失败单列，执行失败的文档继续计入计划分母。上述运行可能不涵盖早期失败调用、其他试验或跨运行复用，不能相加推断账户总花费。费用按配置的保守单价核算；未知 usage 或中断保留最高预留额，不是服务商账单实扣。"]
    if ledger_path:
        ledger = read(ledger_path)
        ledger = ledger.get("budget", ledger)
        lines += ["", f"共享账本快照：预算上限 {number(ledger.get('budget_cny'), 2)} 元，累计保守核算 {number(ledger.get('accounted_cny'), 6)} 元，调用 {ledger.get('calls', '未记录')} 次，未成功或仍预留的调用 {ledger.get('uncertain_calls', '未记录')} 次（不等于这些调用的费用均未知）。来源：{link('账本汇总快照', ledger_path, out)}。这是文件快照，不声称为当前余额。"]
    else:
        lines += ["", "本次未提供共享账本汇总快照，累计花费与当前余额不在本报告中推断。"]
    lines += ["", "| 运行 | 流程 | 实测组内墙钟 P50 / P95（秒） | 无缓存端到端估算 P50 / P95（秒） | 无法估算篇数 |",
              "| --- | --- | --- | --- | ---: |"]
    for run in runs:
        for key, arm in run["score"]["detectors"].items():
            latency = arm.get("latency", {})
            measured = latency.get("measured_arm_wall_seconds", {})
            estimated = latency.get("estimated_end_to_end_seconds", {})
            pair = lambda row: number(row.get("p50")) + " / " + number(row.get("p95"))
            lines.append(f"| {cell(run['label'])} | {ARMS.get(key, key)} | {pair(measured)} | {pair(estimated)} | {latency.get('end_to_end_unavailable_documents', '未记录')} |")
    lines += ["", "组合组墙钟包含本地缓存回放，不能拿毫秒级回放与直接模型调用比较速度。端到端估算补回唯一回放调用及其重试的原始耗时，新调用已包含于墙钟而不重复累计；缺少原始耗时留空。该值假设顺序执行，不等于组合流程独立部署的实测延迟。"]

    chosen = set(details) if details else {r["label"] for r in runs if r["scope"] in {"dev_validation", "frozen_eval"}}
    if chosen - {r["label"] for r in runs}:
        raise ValueError("details 指向未提供的运行")
    if not chosen:
        lines += ["", "## 分层验证状态", "", "本版列出的运行均为小样本调参，未提供独立新样本验证或冻结评测。详细分层仍保留在各 score.json 中；新增 --scope LABEL=dev_validation 或 --details LABEL 后生成对应分层表。"]
    for run in runs:
        if run["label"] not in chosen:
            continue
        lines += ["", f"## {cell(run['label'])}：分层诊断", "",
                  "以下分层表采用 score.json 已接受输出口径（不含另存的拒绝提示），与上方全部提示主指标区分；每层样本量小，零分不能当作精确的总体能力估计。"]
        for field, title in (("by_scene", "场景"), ("by_length", "文长（Unicode 字符）"), ("by_type", "错误类型")):
            lines += ["", f"### 按{title}", "", "| 流程 | 分层 | 文档数 | TP | FP | FN | P | R | F1 |", "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
            for key, arm in run["score"]["detectors"].items():
                for group, metrics in sorted(arm.get(field, {}).items()):
                    lines.append(f"| {ARMS.get(key, key)} | {cell(group)} | {len(metrics.get('by_document', []))} | {rates_row(metrics)} |")

    lines += ["", "## 数据质量敏感性与未完成项", "",
              "标准集 973 篇与长文集 24 篇共 997 篇，原始错误 4,178 个，可评分 4,093 个，85 个异常或歧义标注保留审计。无法定位的 gold 不进入可评分召回分母；同一文档中的预测仍可能因对应这些未评分问题被计为 FP。因此精确率存在标注不完整的局限，不能将所有未匹配提示都视为已人工证伪。", "",
              "| 运行 | 流程 | 完全可评分文档 | 排除文档 | 该子集全部提示 P / R / F1 |", "| --- | --- | ---: | ---: | --- |"]
    for run in runs:
        for key, arm in run["score"]["detectors"].items():
            sensitivity = arm.get("fully_scorable_documents_sensitivity", {})
            rates = sensitivity.get("all_review_hints_detection", {})
            lines.append(f"| {cell(run['label'])} | {ARMS.get(key, key)} | {sensitivity.get('documents', '未记录')} | {sensitivity.get('excluded_documents', '未记录')} | {' / '.join(pct(rates.get(k)) for k in ('precision', 'recall', 'f1'))} |")
    lines += ["", "该表仅是预先按 gold 可评分状态划分的敏感性诊断，不替换完整计划队列，也未按预测结果挑除样本。", "",
              "- 三组是冻结旧规则、同一模型的直接候选、同一候选加程序验证与合并；后两组共用完全相同的模型返回，属于后处理对照，不能声称是两次独立模型实验。源码、提示示例、思考设置同时变化的开发轮次不能孤立归因为某一个改动。",
              "- 固定少量开发示例遵循 FinED-Bench 的多类检查思路；证据确认、长文策略和更严格的误报约束属于本项目工程设计。降低无依据提示可能损失论文分类下的召回，须通过新样本检验。",
              "- 16 条正常文本候选仍待人工签核，未作为验证过的正确样本；不能报告正常研报误报率。三份长文无错误记录也不能替代经审核的正常研报分布。",
              "- 证据语义支持度、过度转人工比例、人工复核效率尚需独立配对业务标注与真人计时。公开文本没有配对财报，不能将该实验称为研报—财报外部证据核查。",
              "- 10 月 5 日与 10 月 7 日的团队会议、未来保留集运行和后续修复验收不因报告生成而完成。FinRiskAtlas 的行为指标不等同于本报告的 FinED-Bench 错误检测结果。", "",
              "## 可追溯产物", ""]
    for run in runs:
        path = run["directory"] / "score.json"
        config = run["score"].get("run_config", {})
        lines += [f"- {cell(run['label'])}：{link('评分', path, out)}；{link('运行配置', run['directory'] / 'run_config.json', out)}；{link('计划队列', run['directory'] / 'queue.json', out)}。",
                  f"  评分 SHA256 `{hashlib.sha256(path.read_bytes()).hexdigest()}`；推理源码 `{config.get('source_hash', '未记录')}`；示例 `{config.get('examples_sha256', '未使用')}`。"]
    lines += [f"- {link('离线规则与配对回归报告', ROOT / 'docs/V2_RESULTS.md', out)}；{link('实施与复现说明', ROOT / 'docs/V2_IMPLEMENTATION.md', out)}。", ""]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="append", required=True, help="repeat LABEL=RUN_DIRECTORY")
    parser.add_argument("--scope", action="append", default=[], help="LABEL=dev_tuning|dev_validation|dev_expansion|frozen_eval; default dev_tuning")
    parser.add_argument("--details", action="append", default=[], help="include stratified emitted-output tables for LABEL")
    parser.add_argument("--ledger-summary", help="safe JSON summary snapshot; never infer cumulative spend by summing runs")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        runs = load_runs(args.run, args.scope)
        content = render(runs, args.out, args.ledger_summary, args.details)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8")
    print(str(out.resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
