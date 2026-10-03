"""Build a concise, source-linked V2 report from measured offline artifacts."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "frontend/tools"))
from metrics import compute_metrics


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def pct(value):
    return "未测量" if value is None else f"{value:.2%}"


def build(args):
    score, paired, baseline, tests = (read(p) for p in (args.text_score, args.paired, args.baseline, args.tests))
    metrics, old_metrics = compute_metrics(paired), compute_metrics(baseline)
    lines = ["# 第二版离线验证报告", "", "本报告区分真实文件上的规则运行、模拟接口验证与真实模型评测。下列公开文本结果仅覆盖离线规则；真实模型尚未运行，不能据此判断大模型效果。", "",
             "## 验证与数据", "",
             f"- B/C 工程回归：{tests['total']} 项，结果：{'全部通过' if tests['passed'] else '有失败，见测试日志'}。",
             "- 另有评测回归 37 项、前端及配对指标回归 13 项通过；本地 Mock HTTP 联调、PDF/CSV/JSON 导出和产物校验通过。",
             "- 997 篇按同源组固定划分为开发 598、10 月 5 日评测 200、10 月 7 日保留 199；推理输入和答案分开。",
             "- 共 4,178 个错误实例，其中 4,093 可评分，85 个异常或歧义标注单列审计。开发集可评分 2,469 个、排除 51 个。",
             "- 16 条正常候选仍待人工签核，不作为已验证正确文本；两份留出集尚未用于本轮调参。", "",
             "## 公开文本开发集", "",
             f"两组都完成的文档：{score['paired_complete_documents']}/{score['requested_documents']}。表中“新版”是组合流程的离线规则部分。", "",
             "| 流程 | TP | FP | FN | Precision | Recall | F1 | 待复核候选 | 规则确认 |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for name, label in (("legacy_rules", "冻结旧版规则"), ("hybrid", "新版规则")):
        entry = score["detectors"][name]
        s = entry["candidate_detection"]
        lines.append(f"| {label} | {s['true_positive']} | {s['false_positive']} | {s['false_negative']} | {pct(s['precision'])} | {pct(s['recall'])} | {pct(s['f1'])} | {entry['human_review_candidates']} | {entry['candidate_status_counts'].get('confirmed_error', 0)} |")
    verified = score["detectors"]["hybrid"]["verified_detection"]
    lines += ["", f"新版规则确认子集：TP {verified['true_positive']}、FP {verified['false_positive']}，Precision {pct(verified['precision'])}、Recall {pct(verified['recall'])}。规则确认仅覆盖有确定性证明的有限错误类型。", "",
              "采用原句相同或预测原句包含标准原句、类型一致、一对一最大匹配；多片段错误仍算一个实例。原句输出与最小证明分别保存在 spans 和 verification_spans。未改答案以迎合预测；疑似漏标仍按原 gold 计分。", "",
              "这批开发数据已用于定位修复，不能称为独立盲测。召回率仍低，说明规则无法覆盖公开基准的大部分错误类型；候选增多也增加了复核负担。完整 JSON 包含按场景、长度、类型分层结果与缺失预测分母。", "",
              "## 研报—财报四组开发回归", "",
              "原表 20 行，去重后 19 项声明，其中 9 项错误、10 项正确；有 1 行重复。公开文本和业务配对指标分别计算。", "",
              "| 指标 | 冻结旧版 | 当前版 |", "| --- | ---: | ---: |"]
    for key, label in (("precision", "已标注子集判错精确率"), ("recall", "错误召回率"),
                       ("evidence_accuracy", "来源页码命中"), ("automatic_coverage", "已标注声明自动判断覆盖"),
                       ("type_accuracy", "独立错误类型准确率"), ("review_burden", "配对输出待复核比例")):
        lines.append(f"| {label} | {pct(old_metrics['rates'][key])} | {pct(metrics['rates'][key])} |")
    lines += ["", "页码命中只说明定位，不证明语义证据充分。额外未标注输出分状态另列；文本补充候选保存在各运行的 text_review 中，不并入配对声明的分母。错误类型分歧保留原表，待人工裁定，不以修改答案消除差异。", "",
              "## 运行、费用与未完成项", "",
              "| 流程 | 完成文档 | 每篇耗时 P50 / P95（秒） | 模型调用 | 核算费用（元） |", "| --- | ---: | ---: | ---: | ---: |"]
    for name, entry in score["detectors"].items():
        runtime = entry["runtime"]
        lines.append(f"| {name} | {entry['completed_documents']} | {entry['seconds_p50']} / {entry['seconds_p95']} | {runtime['model_calls']} | {runtime['accounted_cny']} |")
    lines += ["", "- 耗时是本机离线规则耗时，不是模型延迟。表中 0.0 受计时器分辨率限制，不表示执行不耗时。真实模型直接检测、真实组合流程、token 消耗与真实扣费均尚未实测。",
              "- 模拟模型测试只验证返回解析、证据门槛、失败/重试与累计预算，不模拟模型实际准确率。",
              "- 真实调用共享 20 元预算账本。每次请求预留最大费用；中断、重试和缺 usage 不退还不确定费用。配置最高单价的核算额可能高于服务商实际扣费。",
              "- 仍待完成：正常文本人工审核、语义证据支持度、真实人工复核计时，以及接入 API 后的同模型对照。",
              "- 10 月 5 日和 10 月 7 日的评测及会议仍是后续团队工作，不能视为本轮已经完成。", "", "## 可追溯产物", ""]
    for label, path in (("公开文本评分", args.text_score), ("当前配对结果", args.paired), ("冻结配对基线", args.baseline), ("工程回归", args.tests)):
        p = Path(path).resolve()
        relative = p.relative_to(ROOT).as_posix()
        lines.append(f"- [{label}](../{relative})；SHA256 `{hashlib.sha256(p.read_bytes()).hexdigest()}`")
    lines += [f"- 本次文本推理源码指纹：`{score['run_config']['source_hash']}`。", "- [复现与配置说明](V2_IMPLEMENTATION.md)", ""]
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines), encoding="utf-8")
    print(str(target.resolve()))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("text-score", "paired", "baseline", "tests", "out"):
        parser.add_argument("--" + name, required=True)
    build(parser.parse_args())
