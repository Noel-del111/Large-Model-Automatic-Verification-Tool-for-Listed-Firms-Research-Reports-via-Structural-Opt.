# R09：可直接使用的 C → D 联调样例

这些是**合成接口样例，不是实际研报测评结果，也不是新增金标**。用内存 `Document` 调用真实 `check_documents` 生成；历史形态由同一结果移除新增字段得到。公司、数字、文件名均为演示数据，原 DOCX 不存在，不能用于测试 PDF 解析、OCR、证据页高亮或模型效果。`created_at` 为固定 fixture 元数据，耗时字段已移除，避免被误读为实测性能。

| JSON | 业务场景 | 版本与预期 |
|---|---|---|
| `01_no_issue.json` | 研报收入 100 万元，对应来源也为 100 万元 | 1.1.0；no_issue / proceed / 空请求；计数 0 / 0 |
| `02_confirmed_error.json` | 研报收入 200 万元，对应来源为 100 万元 | 1.1.0；confirmed_error / proceed / 空请求；计数 0 / 0；proceed 不等于原文正确 |
| `03_needs_review.json` | 研报披露货币资金 300 万元，现有来源仅有收入表 | 1.1.0；needs_review / ask；请求货币资金来源；计数 1 / 1 |
| `04_ask_two_periods.json` | 研报称 2024 年收入同比增长 20%，没有两期收入来源 | 1.1.0；needs_review / ask；请求 2024FY 和 2023FY 收入；计数 **1 / 2** |
| `05_legacy_without_requests.json` | 模拟旧版本完全没有补证字段 | 1.0.0；保留 needs_review；显示“旧版未提供补证信息”，不得补成 proceed 或 0 条 |
| `06_legacy_request_count_only.json` | 模拟已有两期请求，但 summary 只有旧的发现计数 | 1.0.0；原 JSON 只有 evidence_requests=1；可从完整列表算出 2 条并注明来源，不倒填原 JSON |

所有 JSON 都保留了 claim、证据、来源事实和独立 text_review 结构，可直接交给 D 现有的结果读取函数。请求项在 `findings[*].evidence_request`，计数在 `summary`；详细规则见 [D 接入说明](../../docs/R09_D_INTEGRATION.md)。单文本检测未调用模型，其 coverage 中保留离线限制；不能据此声称文本语义审查完整。

从仓库根目录重新生成这些 JSON（无需 API key、原 PDF 或联网）：

```powershell
.\.venv\Scripts\python.exe factcheck/tools/export_contract_examples.py
```

同时生成 C 的 JSON / CSV / Markdown / manifest 四份产物，方便比较 D 的合并导出（目录必须是尚未包含这些 fixture run 的新目录）：

```powershell
.\.venv\Scripts\python.exe factcheck/tools/export_contract_examples.py --artifacts-out data/r09-demo-exports
.\.venv\Scripts\python.exe factcheck/run.py verify data/r09-demo-exports/fixture-r09-04_ask_two_periods
```

不能拿 C 原 CSV / Markdown 中历史缺失字段的空单元格当作 0 条；D 的旧版友好提示及完整复核导出仍按接入说明实施。样例只提供输入，**不代表 D 页面、PDF 和复核导出已经验收通过**。
