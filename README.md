# 研报纠错助手（本地集成版）

输入研报 PDF/DOCX 与财报/公告 PDF，获得数值、单位、期间、口径及引用核查结果，可查看原文证据、人工复核、问答和导出。

## 启动

Windows 双击 `frontend/run_app.cmd`，或在项目目录运行：

```powershell
.venv/Scripts/python.exe -m streamlit run frontend/app.py
```

核查结果仍为“已确认错误 / 待人工确认 / 未发现问题”。新版对待确认项增加具体的补证或澄清清单，包括材料角色、指标、期间、口径、原因，输入异常时还会指定文件。补齐后重新核查。

## 本次上游集成

2026-10-03 核对上游默认分支，最新提交为 [d71f8c7](https://github.com/lomooc-kk/Large-Model-Automatic-Verification-Tool-for-Listed-Firms-Research-Reports-via-Structural-Opt./commit/d71f8c752138da3898208f82bb6c525a82f7d325)。

- 整合 `evals/`：公开集标注定位检查、操作级评分、Ask/Proceed 证据决策评分。
- 将上游建议的取证清单落地到核心结果、页面、助手及 CSV/Markdown/JSON/PDF 导出。
- 修正评测器漏交预测不扣分、重复样本 ID 和跨条目误匹配的问题。
- 保留本地已有的 OCR、解析引擎兜底、研报内部一致性检查及前端；上游根目录与嵌套 `repo/` 同时存在，根目录核心有较旧实现，未直接覆盖。

详细范围与验证见 `docs/UPSTREAM_UPDATE_2026-10-03.md`。原文件备份在 `.local-backups/20261003-upstream/`，上游参考副本在 `.upstream-latest/`，均不参与运行。

## 学习与验证

- 浏览器打开 `project-map.html` 查看交互式项目图解。
- 页面在 `frontend/`，核查在 `repo/factcheck/`，解析在 `repo/pdfparse/`。
- 新字段保持 schema 1.0.0 的可选扩展；旧结果仍可展示，新生成结果包含取证字段。

```powershell
.venv/Scripts/python.exe -X utf8 -m unittest discover -s repo/factcheck/tests -p test_evidence_requests.py -v
.venv/Scripts/python.exe -X utf8 -m unittest discover -s frontend/tests -v
.venv/Scripts/python.exe -X utf8 -m unittest discover -s evals/tests -v
.venv/Scripts/python.exe -X utf8 evals/operation_eval.py --selftest
.venv/Scripts/python.exe -X utf8 evals/fined_bench_eval.py --selftest
```

评测工具自测不等于核查精确率、召回率已达标。完整核心测试还依赖本地交付样本，详见验证记录。

## 全量研报实测（2026-10-03）

已运行 FinED-Bench 中全部 442 篇研报、1,792 个标注错误。内部一致性检查提出 55 条待人工确认疑点，53 条严格匹配：疑点精确率 96.36%，召回率 2.96%，F1 5.74%。这些疑点不是自动确认错误；纯文本基准缺少配对财报，完整核查流程没有产生确定结论。

- [全部指标与合格线对照](docs/benchmarks/2026-10-03-all-research/REPORT.md)
- [HTML 评测报告](docs/benchmarks/2026-10-03-all-research/REPORT.html)
- [机器可读指标](docs/benchmarks/2026-10-03-all-research/metrics.json)
- 竞赛指标定义见 [项目指标说明](repo/README.md)。

公开仓库仅保存评测汇总，原始测试集、逐篇正文和本地运行产物不随代码上传。复测前将 FinED-Bench 放到 `测评集/测评集/FinED-Bench-main/`，运行：

```powershell
.venv/Scripts/python.exe -X utf8 evals/run_research_benchmark.py --out evals/reports/new-run
```

当前应用使用 `repo/factcheck` 与 `repo/pdfparse`。仓库根目录保留同名模块以兼容原有使用方式，发布时同步核心代码。
