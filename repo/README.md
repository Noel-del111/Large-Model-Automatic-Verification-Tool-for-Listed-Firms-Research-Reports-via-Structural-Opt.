# 研报纠错助手：基于大模型与结构化比对的上市公司研究报告自动核查工具

> 第二版交接先读 [本次范围与边界](../docs/V2_SCOPE_AND_HANDOFF.md) 和 [根目录说明](../README.md)。本目录是旧入口兼容层，B/C 唯一实现位于根目录；本次完成 200 篇开发阶段实验，未进行模型权重训练，最终独立测试尚未进行。原始分数、版本关系及 PR 合并冲突以交接说明为准。

输入一份研报草稿 PDF 和对应的财报 PDF，输出错误位置、错误类型、原文、依据与修改建议。

本仓库用于 2026 年北京市金融人工智能比赛的参赛作品开发，五人并行推进，各模块独立可跑、
按统一字段对接。当前进度以 `docs/` 下的记录与实际代码为准。

## C 核查模块（2026-09-28）

已新增 `factcheck/`，支持研报/财报事实抽取、数值/单位/期间/口径/引用比对、修改建议和可追溯结果。
可读取 PDF、DOCX 研报及 B 解析 JSON；输出 JSON、CSV 和 Markdown。使用、D 接口、测试及边界见
[`factcheck/README.md`](factcheck/README.md)，本轮实测见 [`factcheck/docs/acceptance.md`](factcheck/docs/acceptance.md)。
同时修复了此前 B→C 接入审查的问题，说明见 [`pdfparse/docs/C_HANDOFF_FIXES.md`](pdfparse/docs/C_HANDOFF_FIXES.md)。
团队接手顺序、交付清单和复现前提见 [`C 模块交接说明`](factcheck/docs/handoff.md)。

## 最小可交付范围

输入研报草稿 PDF 与对应财报 PDF，输出：

1. **错误位置**：文件 → 页码 → 区块坐标，可高亮回原文；
2. **错误类型**：数值、单位、期间、口径、引用等分类；
3. **原文与依据**：研报原句 + 财报/公告中的对应证据；
4. **修改建议**：建议替换内容与修改原因；
5. **结果分级**：已确认错误 / 待人工确认 / 未发现问题。

## 团队分工

| 成员 | 角色 | 具体职责 | 主要交付物 |
| --- | --- | --- | --- |
| A | 产品与统筹 | 定义页面流程与错误分类、冻结统一字段、协调联调 | 流程原型、任务看板、字段表 |
| **B** | **PDF 解析** | **提取文本与表格，保留页码与坐标，识别解析失败** | **解析模块（本仓库 `pdfparse/`）、字段表、定位能力** |
| C | 核查逻辑 | 数值、单位、期间、口径核对，生成修改建议 | 核查模块、建议生成 |
| D | 前端与导出 | 上传、结果筛选、证据对照、报告导出 | 可交互页面、纠错报告 |
| E | 测试与交付 | 整理样本、验证操作流、盲测与复核 | 测试记录、启动说明、交付包 |

### 时间线（2026 年 9 月 22 日至 10 月 19 日）

| 日期 | 主责 | 关键任务 | 阶段交付 |
| --- | --- | --- | --- |
| 09.22–09.23 | 全员 | A 定义最小功能与字段建议，各模块可跑通 | 字段建议稿 |
| 09.24–09.26 | A 主责 | **B 完成 PDF 解析首版** | **字段冻结、各模块首版** |
| 09.27–10.03 | B C D | **B 解析真实 PDF**，C 接入真实核查，D 做证据页 | **首个端到端可演示流程** |
| 10.04–10.09 | C D | 补齐单位、期间、口径核对与报告导出 | 完整操作流程 |
| 10.10–10.13 | A 统筹 | 全链路联调、修复阻断 | 功能完整工程版 |
| 10.14–10.17 | E 主责 | 盲测、异常文件测试、误报逐条处理 | 稳定版本与测试报告 |
| 10.18 | A | 正式提交交付包 | 交付包提交 |

### 评测指标

| 指标 | 统计方法 | 目标 |
| --- | --- | --- |
| 判错精确率 | 正确识别的错误数 ÷ 系统报出的错误数 | ≥ 90% |
| 错误召回率 | 正确识别的错误数 ÷ 测试集已知错误数 | ≥ 80% |
| 正确内容误报率 | 误判的正确项数 ÷ 正确内容总数 | ≤ 10% |
| 证据定位准确率 | 正确定位来源及页码的比例 | ≥ 90% |
| 建议完整性 | 已确认错误均包含定位、原文、依据、建议 | 逐条检查 |

## 仓库结构

```
.
├─ README.md                      本文件
├─ docs/                          项目级文档
│  ├─ 开源方案调研报告.docx         B：解析与知识库选型调研（含 OpenViking 评估）
│  └─ PDF解析模块推进方案.docx      B：对齐竞赛交付要求的推进方案
└─ pdfparse/                      B：PDF 解析模块
   ├─ README.md                   模块用法与指标口径
   ├─ REPRODUCE.md                离线复现与提交前自检
   ├─ THIRD_PARTY.md              第三方组件登记（含许可证与使用范围）
   ├─ src/yjparse/                解析、质检、日志、导出、检索
   ├─ configs/thresholds.json     质量判定阈值
   ├─ schemas/                    结果 JSON Schema
   ├─ tools/                      模型端点 Mock、阅读顺序评估、权重准备
   ├─ tests/                      37 项单元测试与合成样本
   ├─ docs/                       真实研报试跑记录、知识库接入说明
   └─ deploy/ov.conf.example      OpenViking 配置模板
```

队友的模块（核查逻辑、前端与导出、测试与交付）各自独立目录，接入约定见下节“给下游的接口约定”。

## 快速开始（B：解析模块）

```bat
cd pdfparse
run.cmd doctor                                       :: 检查环境与引擎可用性
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pip install -r requirements-engines.txt   :: 可选：对照引擎与 OCR
```

详细的离线复现步骤见 `pdfparse/REPRODUCE.md`。

## 拿到一份研报后，怎么检索并输出

下面这条链路是"从一份 PDF 到可检索、可回溯的结构化结果"的完整操作，
每一步都会产出文件，最后一步能定位到具体页面和坐标。

### 第 1 步：解析

```bat
cd pdfparse
run.cmd parse --input "D:\研报\某某公司2025年半年报点评.pdf" --out data\out
```

输出（控制台会直接打印状态）：

```
[warn] 某某公司2025年半年报点评  页数=21  块=371  表=1  引擎=pymupdf/1.27.2.3  对照=pdfplumber  run=20260926T185404_905be5
```

`ok` 表示可用，`warn` 表示有可解释的疑点，`fail` 表示解析不可用并进入人工复核队列。
扫描件会走 OCR 兜底自动补文字。

### 第 2 步：看质量报告，确认哪些页可信

```bat
run.cmd report --out data\out
```

产物：`data/out/quality_summary.json`、`quality_summary.csv`（逐页状态与原因），
以及每篇文档目录下的 `quality_table.csv`。**下游核查只引用 `ok` 与 `warn` 页**，`fail` 页只提示异常。

### 第 3 步：导出检索索引

```bat
run.cmd export-kb --out data\out --kb data\kb
```

产物三样：

| 文件 | 内容 |
| --- | --- |
| `data/kb/markdown/<文档>.md` | 按页组织、带页码与块坐标锚点的 Markdown，可入库知识库 |
| `data/kb/kb_index.jsonl` | 每个区块一行：页码、坐标、类型、文本、单元格、句子 |
| `data/kb/ingest_manifest.json` | 目标 URI 与现成的 OpenViking 入库命令 |

### 第 4 步：检索

```bat
run.cmd search --kb data\kb --query "归母净利润 增速" --top 5
```

输出示例（命中结果自带页码、坐标、块编号与页面状态）：

```
[ 58.661] 某某公司2025年半年报点评 第117页 p117_t13 [text] bbox=[200,483,305,493] 状态=warn
          25Q3归母净利润TTM增速
回链命令（把命中的块高亮出来）：
  run.cmd preview --out <产物目录> --doc "某某公司2025年半年报点评" --pages 117 --blocks p117_t13
```

多文档横向对比（按文档分组，每条取最相关的片段）：

```bat
run.cmd search --kb data\kb --query "毛利率 变化" --compare --top 2
```

### 第 5 步：回链到原文位置（证据展示）

```bat
run.cmd preview --out data\out --doc "某某公司2025年半年报点评" --pages 117 --blocks p117_t13
```

在 `data/out/<文档>/preview/page-117.png` 生成页面图像，命中的区块用红框（表格蓝框）标出，
可直接放进演示与报告，满足"证据定位准确率"这一指标。

### 可选：开启视觉模型抽检（第三层质检）

有网络时可用云端视觉模型核对"解析文本是否忠实反映页面"，专抓漏段、数字错位、图表标注混入正文：

```bat
set YJPARSE_VLM_BASE_URL=https://<OpenAI 兼容端点>/v1
set YJPARSE_VLM_API_KEY=<密钥>
set YJPARSE_VLM_MODEL=<视觉模型名>
run.cmd parse --input "D:\研报\某某研报.pdf" --out data\out --vlm auto

:: 提交前先体检端点
run.cmd model-check --base-url https://<端点>/v1 --vision-model <视觉模型名>
```

没有密钥时用 `py -3 tools\mock_model_server.py --port 8899` 彩排整条链路。

## 给下游的接口约定

| 对接方 | 我提供什么 | 格式 | 约定 |
| --- | --- | --- | --- |
| C 核查逻辑 | 页级块级解析结果 + 质量报告 | `parse_result.json`、`blocks.jsonl` | 只引用 `ok`/`warn` 页；引用必须带 `page` 与 `bbox` |
| D 前端与导出 | 原文高亮参数 | 页码、坐标、页面尺寸 | 左上角原点、单位为点，按渲染缩放换算 |
| E 测试与交付 | 质量表与失败清单 | `quality_summary.csv` | 逐页状态与原因可直接作为测试记录 |

字段要点：每个区块包含 `block_id`、`type`、`bbox`、`order`、`text`；
表格额外含 `cells`（行列号 + 单元格坐标），文本块含 `sentences`（句级坐标），
图片与表格块含 `caption` 与 `source_note`（图表标题与资料来源）。

## B 初次交付记录（历史）

以下保留 B 初次交付的实跑记录；最新 B/C 合计 155 项测试结果见 [C 模块验收记录](factcheck/docs/acceptance.md)。

已在本机用四份公开研报（合计 195 页，含 134 页图表密集的宏观策略报告）实跑：

- 解析失败页 0，平均双引擎一致度 0.94–0.9997；
- 37 项单元测试通过，产物哈希 20/20 校验通过；
- OCR 兜底在 6 页生效（章节页、封底、位图表格）；
- 视觉模型抽检链路已跑通（用本地 Mock 端点验证协议与流程）。

待办与边界写在 `pdfparse/README.md` 与 `pdfparse/docs/real_report_test.md`，
阅读顺序、无框表格列对齐是已知薄弱点，需要版面引擎或后续优化。

## 协作约定

1. **原始数据不进仓库**：研报 PDF、财报 PDF 只放本地 `data/`，仓库只保留代码与文档。
2. **密钥不进仓库**：模型端点密钥一律用环境变量，配置模板见 `pdfparse/deploy/ov.conf.example`。
3. **第三方成果如实登记**：新增依赖或模型时同步更新 `pdfparse/THIRD_PARTY.md`。
4. **接口字段改动要同步 A**：字段表由 A 冻结，改动先在任务看板提出。
5. **推送方式（重要）**：本机网络连不上 `github.com:443`，`git push` 会失败，改用
   `py -3 tools/push_via_api.py`。它按本地 HEAD 重建远端分支，所以**新上传的内容会覆盖之前的**；
   推送前请先确认队友的提交已经合并到本地，避免覆盖别人的工作。

```bat
git add -A
git commit -m "说明这次改了什么"
py -3 tools/push_via_api.py
```
