# C 核查模块

C 接收研报和对应原始财务材料，识别数值、单位、期间、口径和显式引用问题，输出可追溯的修改建议。原文件不会被改写。D 可直接读取 JSON 展示结果，E 可独立运行测试和样本评测。

## 本次交付

- `docs/handoff.md`：团队交接入口，说明 B/D/E 如何接手、运行前提和剩余工作。
- `docs/R09_C_DELIVERY.md`：R09 补证契约本次交付及 468 项回归记录；D 待接入内容另见 `docs/R09_D_INTEGRATION.md`。
- `src/yjcheck/`：输入准入、正文/表格事实抽取、Decimal 比对、可选大模型候选抽取、结果输出。
- `schemas/check_result.schema.json`：D 使用的版本化结果契约。
- `tests/`、`tools/run_all_tests.py`：规则、输入边界、真实 PDF、模型协议、CLI、B 接入回归；完整验收不允许跳过。
- `tools/evaluate_samples.py`：先独立运行系统，再读取 E 的原 Excel 对答案，保留重复项和分歧。
- `docs/sample_annotation_audit.md`：E 样本的原始标注审计。
- `docs/acceptance.md`：本次实测结果、复现命令和边界。

## 启动

在仓库根目录执行。已为当前工作区配置 `.venv`；新机器使用 Python 3.12 创建环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r factcheck/requirements.txt
.\.venv\Scripts\python.exe factcheck/run.py check --report "研报.pdf" --source "财务材料.pdf" --out data/check
```

`--source` 可重复提供多份材料。同指标、期间和口径的来源冲突会转人工，程序不会选择最接近研报的来源。

支持文本型 PDF 研报、DOCX 研报和 B 的 `parse_result.json`。DOCX 按原 XML 段落定位，保留标题段、制表符和换行，不伪造 PDF 页码；带表格的 DOCX 需先转换为 PDF。PDF 默认使用 B 的 pdfplumber 引擎，可选 `--engine pymupdf`。

导入 B JSON 时必须保留原 PDF：C 校验 SHA-256 和完整页号，并重新读取原 PDF 的正文，防止手改 JSON 数字仍借用正确文件哈希。API `adapters.from_parse_result` 是面向可信进程内 B 结果的底层转换，外部文件请走 `load_document` 或 CLI。

通常可从研报标题的公司简称/股票代码识别主体；也可加 `--company "公司简称"`。公司身份仍须由原材料首页公司标题或公司全称对应的明确简称定义确认，竞争对手段落中的提及、文件名均不能独立证明身份。

每次运行输出到 `data/check/<run_id>/`：

| 文件 | 用途 |
| --- | --- |
| `check_result.json` | 完整结果：双方事实、证据、规则和计算过程，以及输入质量问题 |
| `findings.csv` | 便于人工查看和交接的清单，使用 UTF-8 BOM |
| `report.md` | 可阅读的纠错报告 |
| `manifest.json` | 三份产物的 SHA-256 和运行标识 |

校验结果文件完整性：

```powershell
.\.venv\Scripts\python.exe factcheck/run.py verify "data/check/<run_id>"
```

`check` 退出码 0 表示运行完成，**不表示研报正确**；输入错误退出 2。`verify` 成功为 0，文件修改/缺失/运行标识不一致为 1。

## 给 D 的接口

Python 入口（将 `factcheck/src` 和 `pdfparse/src` 加入 Python 搜索路径）：

```python
from yjcheck.pipeline import run_check
result, output_dir = run_check("研报.pdf", ["财报.pdf"], "data/check")
for finding in result["findings"]:
    print(finding["status_label"], finding["message"])
```

新配对结果使用 `schema_version=1.1.0`，补证信息成为明确的输出契约；[JSON Schema](schemas/check_result.schema.json) 同时接受历史 `1.0.0`。单文本的 `text-review/1.0` 独立契约不变。每个 finding 包含：

| 字段 | 含义 |
| --- | --- |
| `id` / `claim` | 原声明及其公司、指标、原值、单位、币种、期间和口径 |
| `status` | `confirmed_error` 已确认错误、`needs_review` 待人工确认、`no_issue` 未发现问题 |
| `error_type` | `number` 数值、`unit` 单位、`period` 期间、`basis` 调整前后、`scope` 归属/合并范围、`citation` 引用；输入/覆盖问题另列 |
| `rule_id` / `calculation` | 触发规则、规范化数值、精度、来源和复算过程 |
| `evidence` | 支撑判断的来源事实；内部的 evidence 同时保留数值行和表头上下文 |
| `suggestion` / `suggested_value` | 修改建议和建议数值；不能确定时不编造数值 |
| `review_status` | 初始固定 `unreviewed`，由 D 的人工复核流程管理后续状态 |
| `decision` | `ask` 需要补材料或人工澄清；`proceed` 无额外补证请求，不表示“原文正确”，也不代表已执行检索 |
| `evidence_request` | 补证请求数组，含材料角色、字段、公司、期间、口径、请求类型、原因和已有文件标识 |

`1.1.0` 中，`needs_review` 必须对应 `ask` 和非空请求数组；`confirmed_error` / `no_issue` 必须对应 `proceed` 和空数组。`summary.evidence_requests` 是需要补证的 finding 数，`summary.evidence_request_items` 是请求项数：一条结论要补两期材料，分别计 1 和 2。JSON Schema 校验字段类型与状态关系；汇总与实际数组的数量一致性由生成流程及契约测试验证。

旧 `1.0.0` 可能完全没有这组字段，也可能已有请求、只有 `summary.evidence_requests` 而没有请求项总数。消费者须保留旧原始结果，不补写为 `proceed`，不把缺失计数当零；页面可显示“旧版未提供补证信息”。旧 finding 若提供补证字段，则 `decision` 与 `evidence_request` 必须成对且满足状态关系。

九个请求字段、四种请求类型、合成联调样例及 D 的页面/复核导出待办，见 [R09 补证契约与 D 接入说明](docs/R09_D_INTEGRATION.md)。C 生成的 JSON、CSV、Markdown 已包含补证内容；D 的含人工复核导出需要单独接入该契约。

金额 `value` 是十进制字符串，不能先转二进制浮点再做金额比对。期间格式为 `2024FY`、`2025H1`、`2025Q1` 或资产时点 `2024-12-31`；`publication_year` 特指专项报告冠名年度，期间为 `publication`，不是签署/实际发布年份。`basis` 为 `before/after/change/reported/unknown`，`scope` 为 `consolidated/parent/unknown`。

PDF evidence 包含原文件 SHA-256、解析 run_id、block_id、从 1 开始的页码和 bbox（左上角原点、点单位）；DOCX 使用 paragraph。正文字符范围为原块的半开区间 `[char_start, char_end)`，可直接回切。跨行句子分别保留各原始块，`claim.attributes.value_locations` 给出数值所在块内范围。

来源事实的 `attributes.value_locations` 专门列出实际数值所在位置；引用核对仅认可这些位置，不能用单位/期间所在的表头页替代数值页。表格若没有精确单元格框，保留近似行框用于查看，不支持据此直接替换 PDF 文本。

`summary.complete` 只表示提取到的支持范围内事实均已给出确定结论且输入无已知质量问题；即使为 true，也可能有已确认错误，且不代表审查了整篇文章所有论断。零抽取、证据不足或模型调用失败不会显示完整通过。

## 规则与适用范围

- 支持收入、利润（归母/总利润/扣非归母）、货币资金、存货、资本公积、未分配利润、资产、归母权益、经营现金流、基本 EPS、明确股价、毛利率等指标的已声明数据。
- 财报提取读取真实表头的列顺序；续页继承期间、单位和范围，并保留这些上下文证据。失败页不能提供数字或偷偷提供期间。
- 先匹配公司、期间、币种和口径，再换算单位。金额采用 Decimal，按研报显示的小数位使用 ROUND_HALF_UP。不会为贴合参考答案扩大误差容忍度。
- “另一年度恰好有同一数字”只是人工核查线索。只有声明条件下已有可靠来源且明确矛盾时才确认错误。
- 同比由两期可靠金额复算，零或负基数转人工；百分比与百分点不同。PE 仅在同口径股价、EPS 都有证据且为正时复算。
- 明确的数字照搬并换了单位可判单位错误；只有金额差若干倍，通常只能确认数值不符，不能断言具体原因。
- 缺页、身份不匹配阻断自动结论；坏坐标、OCR/结构异常的页或证据转人工。其他可信页可以单独核查，但整份输入仍明确标为不完整。
- 自由形式引用、全网来源检索、复杂估值、扫描件全面核查、预测判断、复杂百分比过渡和未支持指标不在本版自动判错范围。不会自动覆盖原研报或生成投资建议。

## 大模型接入

默认离线运行。启用时使用兼容 Chat Completions 的端点：

```powershell
$env:YJCHECK_BASE_URL = "https://你的服务/v1"
$env:YJCHECK_MODEL = "你的模型名"
$env:YJCHECK_API_KEY = "你的密钥"
.\.venv\Scripts\python.exe factcheck/run.py check --report "研报.pdf" --source "财报.pdf" --model
```

模型只负责补充事实候选，程序校验原文摘录、指标与数值绑定、单位和定位。规则已识别的项目仍走确定性核查；模型新增且无法独立确认语义的项目标为待人工确认。请求提示词、模型、输入、输出和拒绝原因保留在 `model_traces`，不记录认证密钥。只有显式使用 `--model` 才发送研报内容。

本次验证了模型协议、伪造证据拦截、失败行为和留痕；没有调用付费云模型，不宣称真实模型抽取准确率。

## E 样本与完整测试

```powershell
.\.venv\Scripts\python.exe -m pip install -r factcheck/requirements-test.txt
.\.venv\Scripts\python.exe factcheck/tools/run_all_tests.py
.\.venv\Scripts\python.exe factcheck/tools/evaluate_samples.py --samples "..\E测试样本最新版" --out data/c-acceptance
```

完整测试使用工作区原始 E 样本目录；未随代码复制财报/研报。缺少样本会明确失败，不通过跳过伪装验收成功。运行日志和依赖版本写在 `data/test-results/`。
新机器需要将 E 的原始目录放在仓库同级，目录名保持 `E测试样本最新版`，再运行完整测试。已提交的本轮测试摘要和逐项日志见 [`docs/validation/2026-09-28/`](docs/validation/2026-09-28/)。

E 的 Excel 只在核查程序运行后用于评测，不能进入事实抽取或比对。20 行原标注含 1 个重复，分开记录是否有错、错误分类、来源页码和未标注输出；本轮是公开开发样本回归，不是独立盲测，也不是分工中规划的 100 项完整评测。
