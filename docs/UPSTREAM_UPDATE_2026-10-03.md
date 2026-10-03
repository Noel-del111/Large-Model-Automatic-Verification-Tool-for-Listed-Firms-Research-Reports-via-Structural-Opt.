# 上游集成与本地改进记录

核对日期：2026-10-03（北京时间）。上游仓库默认分支 HEAD：
`d71f8c752138da3898208f82bb6c525a82f7d325`，提交时间 2026-10-01 23:41:59 +08:00。

来源：[上游提交](https://github.com/lomooc-kk/Large-Model-Automatic-Verification-Tool-for-Listed-Firms-Research-Reports-via-Structural-Opt./commit/d71f8c752138da3898208f82bb6c525a82f7d325)。本地参考副本：`.upstream-latest/`。

## 对比结论

本地为文件副本，没有 Git 历史，采用逐文件内容对比并忽略 CRLF/LF 差异。上游同时包含根目录 factcheck/pdfparse 和嵌套 repo/，其中根目录核心缺少本地已有的 OCR 自动兜底、解析引擎重试、部分抽取增强、错误类型扩展与研报内部一致性检查。前端与本地内容一致。

因此没有覆盖整个目录；新增集成上游 `evals/` 的 contracts、operation_eval、fined_bench_eval 及研究文档。已有核心保留，再以增量方式实现上游评测合同建议的“证据不足时提出取证请求”。

## 实际改进

1. `repo/factcheck/src/yjcheck/evidence_requests.py`：在最终结论（包括输入异常覆盖）之后生成 decision 与 evidence_request。系统状态仍使用 confirmed_error / needs_review / no_issue；Ask 包括补证与人工澄清，不新增第四种错误状态。
2. 清单包含材料角色、指标/待澄清字段、期间、公司、口径、文件（已知时）、请求类型与原因。缺少同比或 PE 复算输入时，从计算规则传出缺失输入，避免由模型猜测所需指标和期间。
3. schema 1.0.0 添加可选字段校验，兼容旧结果；新结果的 ask 必须有清单且没有建议正确值，proceed 清单为空。summary.evidence_requests 统计有补证请求的发现条数，不是请求项数量。
4. 页面概览、列表、证据对照及助手解释展示清单；后端 CSV/Markdown、前端 CSV/Markdown/JSON/PDF 导出均包含清单。同步修正前端 Markdown 单元格中竖线未转义的问题。
5. 上游操作评测器修复：所有金标准样本都进入分母，缺失预测计入错误；重复 ID 与非法动作拒绝评分；不同条目的同名文档不会互相匹配。无金标准的额外预测单独计数。ERA/CRA 仍沿用上游的证据覆盖率，不惩罚过量取证。
6. `evals/check_result_adapter.py` 导出真实运行的证据决策供独立标注评测；不把块内字符偏移转换成伪造的全文偏移，不自动制造金标准。
7. 新增根目录启动说明，更新 DESIGN.md 状态与 project-map.html 学习图解。

## 验证

| 项目 | 本次结果 |
| --- | --- |
| 解析模块完整回归 | 53/53 通过 |
| 核查模块完整回归（含 6 个新增测试） | 117/119 通过，1 失败、1 跳过 |
| 新增补证逻辑、契约、导出与离线解释测试 | 6/6 通过，包含在上面的 119 项中 |
| 新增操作评测边界与适配器测试 | 5/5 通过 |
| Streamlit 页面与历史结果兼容性测试 | 1/1 通过 |
| 上游两套评测工具内置自测 | 均通过 |
| 演示 PDF 端到端冒烟 | 通过：1 条确认错误、1 条无问题、1 条待确认并带补证清单 |
| 复核、四类导出、原文高亮与产物哈希 | 冒烟通过 |
| 模型抽取与助手问答 | 本地 Mock 冒烟通过，无真实远程模型调用 |
| 本地公开集标注检查 | 973 篇标准集与 24 篇长文档集读取、检查、输出报告成功 |

完整回归日志与环境版本在 `docs/validation/2026-10-03/tests.log` 和 `summary.json`。

**完整回归未全绿的原因**（更新前基线即存在）：

- `LocalESampleClaimTests.test_all_four_delivered_docx_have_expected_extraction_coverage`：缺少项目根目录下的 `E测试样本最新版/` 及四份验收 DOCX，测试按原约定失败，没有隐藏或改为通过。
- `LocalPdfRegressionTests.setUpClass`：所需本地公开 PDF 夹具未随代码分发，按原测试逻辑跳过。

这些不是本次新增逻辑的失败，但完整交付验收仍需补齐对应材料。工具自测与标注检查不证明核查精确率、召回率达到竞赛目标；尚未使用独立金标准测量新取证功能的 BAcc/ERA/CRA。

公开集报告在 `docs/validation/2026-10-03/dataset-audit/fined_bench_integrity.json`。注意 errors 计数按错误标注，offset_ok/offset_bad 按标注中的 span 计数，多 span 标注会导致两者分母不同。

## 运行与复现

```powershell
.venv/Scripts/python.exe -m streamlit run frontend/app.py
.venv/Scripts/python.exe -X utf8 repo/factcheck/tools/run_all_tests.py --out docs/validation/recheck
.venv/Scripts/python.exe -X utf8 -m unittest discover -s frontend/tests -v
.venv/Scripts/python.exe -X utf8 -m unittest discover -s evals/tests -v
.venv/Scripts/python.exe -X utf8 evals/operation_eval.py --selftest
.venv/Scripts/python.exe -X utf8 evals/fined_bench_eval.py --selftest
```

取证预测导出和金标准格式见 `evals/README.md`。

## 原文件备份

本次改动前的已有文件保存在 `.local-backups/20261003-upstream/`，目录结构与项目一致。原有上传数据、人工复核记录及核查历史均保留；冒烟测试新增了独立运行目录。`.upstream-latest/` 是仅用于对比的上游副本，不在应用导入路径中。
