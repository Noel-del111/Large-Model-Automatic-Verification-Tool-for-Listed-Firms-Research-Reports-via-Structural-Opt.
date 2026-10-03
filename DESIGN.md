# 研报纠错助手 · 系统设计方案

**基于大模型与结构化比对的上市公司研究报告自动核查工具**

> 2026-10-03 集成说明：前端、问答与导出已有实现；新增结构化补证清单和操作级评测。后文保留原设计目标，实际范围与验证以 `README.md` 和 `docs/UPSTREAM_UPDATE_2026-10-03.md` 为准。

- 依据材料：[参考仓库](https://github.com/lomooc-kk/Large-Model-Automatic-Verification-Tool-for-Listed-Firms-Research-Reports-via-Structural-Opt.)（已下载至 `repo/`，含 README、docs/*.docx、`pdfparse/`、`factcheck/`）
- 状态约定：本文中「已实现」= 参考仓库已有可运行代码；「待建」= 本设计新增或需补齐的部分
- 目标：输入一份研报草稿 PDF 和对应的财报/公告 PDF，输出错误位置、错误类型、原文、依据与修改建议；并支持用户以「助手」方式交互式追问、复核与导出

---

## 1. 产品定义

### 1.1 输入与输出

| 项 | 内容 |
| --- | --- |
| 输入 | 研报草稿 PDF/DOCX + 对应财报/公告 PDF（可多份），可选：公司简称、B 解析 JSON |
| 输出 | ① 错误位置：文件 → 页码 → 区块坐标（可高亮回原文）；② 错误类型；③ 原文与依据（研报原句 + 财报证据）；④ 修改建议（建议值 + 原因）；⑤ 结果分级：已确认错误 / 待人工确认 / 未发现问题 |
| 约束 | 不修改原文件；不生成投资建议；原始数据与密钥不进仓库 |

### 1.2 评测指标（竞赛口径）

| 指标 | 口径 | 目标 |
| --- | --- | --- |
| 判错精确率 | 正确识别的错误数 ÷ 系统报出的错误数 | ≥ 90% |
| 错误召回率 | 正确识别的错误数 ÷ 测试集已知错误数 | ≥ 80% |
| 正确内容误报率 | 误判的正确项数 ÷ 正确内容总数 | ≤ 10% |
| 证据定位准确率 | 正确定位来源及页码的比例 | ≥ 90% |
| 建议完整性 | 已确认错误均包含定位、原文、依据、建议 | 逐条检查 |

### 1.3 设计原则

1. **事实层与推论层分离**：解析输出与财报证据是事实层（精确指回原文位置）；模型判断、解释与建议是推论层（单独标注），二者不可混存。
2. **确定性规则优先，大模型补充**：数值核对一律走 Decimal 确定性计算；大模型只贡献事实候选、解释与建议措辞，判定结论由规则引擎负责。
3. **风险审慎 / 宁缺毋滥**：证据不足、口径不明、来源冲突一律转「待人工确认」，不猜、不编造数值。
4. **全链路可追溯**：文件访问、工具调用、计算过程、结果生成四类 JSONL 日志 + 运行清单（manifest），产物哈希可校验。
5. **封闭环境可运行**：默认全离线；远程模型仅显式开启（DeepSeek 等 OpenAI 兼容端点），密钥走环境变量，请求留痕去密钥。
6. **接口先行**：模块间以 A 冻结的统一字段与 JSON Schema 对接，任何一方可独立替换实现。

---

## 2. 总体架构

五层 + 一个可追溯底座：

| 层 | 模块 | 职责 | 状态 |
| --- | --- | --- | --- |
| L1 接入层 | 前端上传 / 任务管理 | 多文件上传、参数选择 | 上传与同步核查已实现，独立任务队列未实现 |
| L2 解析层 | `pdfparse/`（yjparse） | PDF → 带页码与坐标的结构化结果 + 质量分级（ok/warn/fail） | 已实现（B） |
| L3 核查层 | `factcheck/`（yjcheck） | 事实抽取（规则 + LLM 候选）→ 确定性规则比对 → 三级结论 + 修改建议 | 已实现（C） |
| L4 助手层 | 大模型纠错助手 | 单条追问、发现检索问答、离线解释、补证说明 | 已实现 |
| L5 展示导出层 | 结果页 / 证据对照 / 报告导出 | 双向高亮、人工复核、PDF/CSV/MD/JSON 与补证清单 | 已实现；Excel 使用 CSV，非原生 XLSX |
| 底座 | 四类日志 + manifest + 第三方登记 + 评测(E) | 可追溯、可复现、合规、指标统计 | 已实现/进行中 |

关键数据流：

```
报表PDF ─▶ L2 解析 ──parse_result.json──▶ L3 核查 ──check_result.json──▶ L5 展示导出
  │              │ ok/warn 页才准入          ▲        │          ▲
研报PDF ────────┘                        │        └── L4 助手(对话/解释) ─┘
```

### 2.1 分层原则

- **L2 只做事实层**：不做单位换算、不做口径判定、不改写数值文本（见 [B 侧字段映射表](repo/docs/B侧字段映射表.docx.txt)）。
- **L3 消费事实层，产出结论层**：结论必须携带证据链（sha256 → run_id → block_id → page/bbox/句子/单元格）。
- **L4 只消费 L3 结果，不新增判定**：助手的一切回答以 findings + evidence 为上下文，不得改变 status，不得私自给出未经复算的"正确数值"。
- **L5 负责人的介入**：`review_status` 状态机（unreviewed → confirmed / dismissed / contested）沉淀人工结论，回流评测(E)。

---

## 3. 数据契约

### 3.1 A 冻结的统一字段

公司、指标、数值、单位、期间、口径、文件及页码（映射关系见 `repo/docs/B侧字段映射表.docx`）：

| 统一字段 | 来源层 | 说明 |
| --- | --- | --- |
| 公司 | doc.doc_id / 首页标题块 | 取不到留空，由上游补；不臆造 |
| 指标 | 表格首列 cells[col=0] / 正文句子 | 正文由 C 按指标词表匹配 |
| 数值 | cells[].text / sentences[].text | 原样保留千分位、百分号、括号负数、单位后缀 |
| 单位 | 表头行 / 单元格文本 / source_note | 表头"单位：百万元"与格内"8,420 万元"都查 |
| 期间 | 表头行 / 时间词 | 2024A/2025E 原样保留，C 侧归一到 2024FY/2025H1 等 |
| 口径 | 不判定，提供原文与位置 | 归母/扣非、合并/母公司、调整前后属 C 职责 |
| 文件 | doc.source_path + sha256 | sha256 用于确认引用同一份文件 |
| 页码 | pages[].page、blocks[].block_id | 页码从 1 开始；block_id 形如 p117_t13 |

### 3.2 parse_result（B→全部，schema 见 `repo/pdfparse/schemas/parse_result.schema.json`）

- 必填：`run_id`、`doc{sha256,total_pages}`、`engine{name,version,params}`、每页 `pages[]{page,page_size,status,blocks[]}`、`quality_report`。
- 每个块必填 `block_id/type/bbox/order`；表格块含 `cells[](row,col,text,bbox)`，文本块含 `sentences[](text,bbox,char_start,char_end)`，图表块含 `caption/source_note`。
- 页面状态 `ok/warn/fail`：**下游核查只引用 ok/warn 页**，fail 页只提示异常。

### 3.3 check_result（C→D，schema 1.0.0 见 `repo/factcheck/schemas/check_result.schema.json`）

- 结果三级：`confirmed_error`（已确认错误）/ `needs_review`（待人工确认）/ `no_issue`（未发现问题）。
- 错误类型：`number` 数值、`unit` 单位、`period` 期间、`basis` 调整前后、`scope` 归属/合并范围、`citation` 引用；另有 `input_quality`（输入质量问题）、`coverage`（无抽取）。
- 每条 finding：`claim`（含公司/指标/原值/单位/币种/期间/口径）+ `evidence`（来源事实，含数值行与表头上下文）+ `rule_id` + `calculation`（规范化、精度、复算过程）+ `suggestion/suggested_value` + `review_status`（初始 `unreviewed`，由 D 维护）。
- 金额 `value` 为**十进制字符串**，禁用二进制浮点；期间格式 `2024FY/2025H1/2025Q1/2024-12-31`；`basis∈{before,after,change,reported,unknown}`，`scope∈{consolidated,parent,unknown}`。
- 定位链：PDF evidence 含 sha256、run_id、block_id、页码（从 1）、bbox（左上原点、点单位）、字符半开区间 `[char_start,char_end)`；DOCX 用 paragraph。

---

## 4. 解析层设计（L2，已实现，设计要点）

### 4.1 引擎适配层

| 引擎 | 定位 | 状态 |
| --- | --- | --- |
| pymupdf | 主引擎之一，原生精度坐标、find_tables | 端到端验证 |
| pdfplumber | 对照引擎 / 默认引擎，行级坐标 | 端到端验证 |
| docling | 严谨阅读顺序 + provenance | 适配器已写，未实测 |
| mineru | 版面主引擎候选（离线产物接入） | 适配器已写，未实测 |

切换主引擎不改变下游契约；`--engine-params` 支持 `table_strategy`（lines/hybrid/text）与 `reading_order`（naive/band/xycut）。

### 4.2 三层质检机制

1. **确定性规则**：页码/坐标完整性（缺失即失败）、页数一致、乱码率 >1% 失败、文字密度、表格结构（`configs/thresholds.json` 集中调参并写入 manifest）。
2. **双引擎交叉校验**：主引擎 vs 对照引擎归一化文本一致度，<0.85 告警、<0.60 失败；顺带完成电子版/扫描件路由。
3. **视觉模型抽检（第三层，可选）**：按 `vlm_sample_ratio` 抽页比对页面图像与解析文本，抓漏段、数字错位、图表标注混入正文；只升级 warn，不改 fail 判定；端点不可用不阻断主流程。

### 4.3 OCR 兜底与产物

- 无文本层/位图密集页自动走 RapidOCR（纯 CPU 离线），OCR 块 `level=ocr`、`confidence` 平均置信度，页备注 `ocr_used`。
- 产物：`parse_result.json`、`pages.jsonl`、`blocks.jsonl`、`quality_*.csv`、`failure_list.csv`；`preview` 生成红框(文本)/蓝框(表格)/灰框(图片)/橙框(页眉页脚)高亮图；`export-kb` 输出带页码锚点的 Markdown 与检索索引。
- 已知边界：坐标法阅读顺序接近上限（band 方案已是最优近似）；无框财务表需 hybrid 策略；严谨顺序需 Docling/MinerU 主引擎。

---

## 5. 核查层设计（L3，已实现，设计要点）

### 5.1 事实抽取（两条通道）

- **规则抽取（默认离线）**：正文/跨行句子/指标表/跨页财报，读取真实表头列顺序；续页继承期间、单位、范围并保留上下文证据；失败页不提供数字。
- **LLM 候选抽取（可选 `--model`）**：模型只提交候选；程序侧校验——摘录必须在原文块内（`unanchored_quote_or_metric` 拒绝）、数值与指标必须同句绑定（`value_or_metric_not_in_quote`、`numeric_value_belongs_to_other_metric` 拒绝）、维度枚举校验（`invalid_dimensions`）。规则已识别的声明保留确定性解释；模型新增且无法独立确认语义的标为待人工确认。请求留痕进 `model_traces`，不含密钥。

### 5.2 错误分类学

| error_type | 含义 | 判错示例（来自 E 开发样本） |
| --- | --- | --- |
| number | 数值不符 | 佛燃"调整前归母净利润 8.5325 亿"（正确 8.5312） |
| unit | 单位倍数错误（数字照搬仅换单位才可判） | 金额沿用原数却改了单位倍数 |
| period | 期间错位 | 兰石"2023 年度披露"（冠名年度 2025） |
| basis | 调整前后口径错误 | 大地"重述后营收 9.47 亿"（9.47 是重述前） |
| scope | 归属/合并范围错误 | 归母/总利润、合并/母公司混用 |
| citation | 显式引用页码与数值实际位置不符 | 引用写 P8 而数值在 P6 |
| input_quality | 文件身份/完整性未通过 | 缺页、坏坐标、失败页证据 |

### 5.3 规则引擎关键机制（`repo/factcheck/src/yjcheck/rules.py`）

1. **归一化**：金额一律 Decimal，单位表（元/千元/万元/亿元…%、百分点、元/股、股…）；千分位校验；括号负数转负。
2. **匹配顺序**：先公司→期间→币种→口径（调整前后/合并范围）完全同上下文，再比较数值。
3. **显示精度对齐**：按研报显示小数位 ROUND_HALF_UP 复算期望值；不为贴合参考答案扩大容差（佛燃 8.5325 vs 8.5326 判数值错误）。
4. **来源冲突转人工**：同指标/期间/口径多个可靠来源值不一致 → needs_review，程序不挑有利候选。
5. **派生指标复算**：同比 = 两期可靠金额复算（零/负基数转人工）；PE = 同口径股价/EPS 均为正时复算；百分比与百分点不可互换。
6. **引用核验**：只认可 `value_locations`（实际数值所在行），表头页不能替代数值页。
7. **金额差异的表述纪律**：只判"数值不符"，不凭数字差异断言具体成因（佛燃货币资金差 100 倍、兰石资本公积差 10 倍均保守标数值错误而非单位错误）。

### 5.4 输出与验证

- 产物：`check_result.json`（完整审计）、`findings.csv`（UTF-8 BOM，防 Excel 公式注入）、`report.md`、`manifest.json`（三产物 SHA-256 + run_id）。
- 退出码语义：check 返回 0 = 运行完成（≠ 研报正确），2 = 输入错误；`verify` 校验产物完整性。
- `summary.complete=true` 仅表示「已提取支持范围内事实均有确定结论且输入无已知质量问题」，不表示整篇无错、不覆盖全文论断。

---

## 6. 大模型助手层设计（L4，本设计重点）

### 6.1 定位

把"批量核查器"升级为"纠错助手"：用户既可一键跑完全量核查，也可以对每一条发现对话式追问。助手是**证据的翻译器**，不是**新的裁判**。

### 6.2 模型角色分工（全部兼容 OpenAI Chat Completions 端点）

| 角色 | 承担环节 | 模型建议 | 结论权 |
| --- | --- | --- | --- |
| 事实候选抽取 | L3 可选通道（已有 `model.py`） | DeepSeek-V3 级文本模型 | 无（程序校验后才进入 claims，且标 needs_review） |
| 证据解释（RAG） | L4 对话问答 | DeepSeek-R1 级推理模型 | 无（只读 findings+evidence 作答） |
| 建议措辞 | L4 修改建议润色/说明 | 同上 | 无（suggested_value 必须来自规则引擎复算） |
| 视觉抽检（可选） | L2 第三层质检 | DeepSeek-OCR / Qwen-VL 类 | 无（只升级 warn） |

- 配置方式：`YJCHECK_BASE_URL / YJCHECK_MODEL / YJCHECK_API_KEY`（DeepSeek 用 `https://api.deepseek.com/v1`）；远程端点强制 HTTPS；密钥不进命令行与仓库。
- 国产模型满足竞赛"鼓励国产大模型"导向；默认离线运行，模型不启用时助手退化为"证据浏览器"（按 finding 原文摘录作答）。

### 6.3 助手交互设计

| 交互 | 触发 | 助手行为 | 数据来源 |
| --- | --- | --- | --- |
| 全量核查 | 上传双 PDF | 跑 L2→L3 流水线，输出概览卡片（已确认/待确认/无问题计数） | check_result.summary |
| 单条追问 | 点击 finding，问"为什么判错" | 用 rule_id + calculation 复算过程 + 双方证据摘录组织回答 | finding.calculation / evidence |
| 证据对照 | 问"依据在哪" | 返回证据页码/坐标并触发 L5 高亮 | evidence.page/bbox |
| 建议确认 | 问"改成什么" | 给出 suggested_value + 重新核验 | finding.suggestion（值必须来自复算） |
| 报告问答 | 导出前"这份报告有什么问题" | RAG：以 findings 列表为上下文做总结式问答 | check_result.json 全文 |

### 6.4 助手层护栏（必须实现）

1. **状态不可越权**：助手输出绝不能把 needs_review 说成"确定错误"，也不能反向洗白 confirmed_error；回答里区分"系统判定"与"模型解释"。
2. **数值不可编造**：任何"建议值"必须出现在 `suggested_value` 或 `calculation.expected_in_claim_unit` 中，否则助手只能回答"需人工复核"。
3. **上下文仅限本次运行**：会话上下文 = 当次 check_result + 用户选定 range；不混入其它公司数据；提问原文进 `model_traces`（去密钥）。
4. **引用必须可回链**：助手每句涉及证据的回答携带 `[第 N 页 / block_id]` 标记，供 L5 高亮。
5. **降级可用**：无模型时，助手以模板化说法输出 rule_id 的解释卡片（规则 → 原因 → 证据位置 → 建议）。

### 6.5 检索增强方案

- 内置：B 的 `export-kb`（Markdown 分页 + `kb_index.jsonl` 块级索引）+ BM25 离线检索（已实现）；L4 优先按 evidence 定位直接取块文本，检索只用于开放式追问。
- 可叠加（可选）：OpenViking / RAGFlow 向量检索——结论：二者只承担"检索与横向对比"半层，**不能替代解析层**（页码仅 HTML 注释、无块级坐标）；解析层与知识库层以同一份 JSON 契约解耦（见 `repo/docs/开源方案调研报告.docx`）。

---

## 7. 展示与导出层设计（L5，已有实现，细节以代码为准）

### 7.1 页面流程

上传（研报/财报/公告，多文件）→ 解析参数（主引擎/OCR/视觉抽检）→ 运行 → 五个视图：

1. **概览**：文档数、页状态分布、发现计数（三态）、运行 id、`summary.complete` 口径提醒。
2. **结果列表**：按状态/错误类型/公司过滤；每条 = 状态徽章 + 研报原文 + 建议值 + 依据摘要。
3. **证据对照**：左右分栏（研报侧 / 财报侧），点击 finding → 双侧高亮到具体区块（点坐标 × 渲染缩放换算）；高亮不稳定时退化为"页码 + 原文摘录"。
4. **人工复核**：`review_status` 状态机 `unreviewed → confirmed / dismissed / contested`，复核结论写回本地结果目录，回流 E 评测。
5. **导出**：纠错报告（PDF/MD/Excel），含评测口径说明与"不支持范围"声明页。

### 7.2 关键约定

- 高亮参数：页码、坐标、页面尺寸（左上原点、点单位）来自 B，缩放出 D 换算。
- CSV 导出防公式注入（`=/+/-/@` 前缀处理）沿用 C 现有实现。
- 复核工作流职责：C 只产出 `unreviewed`，D 管理后续状态。

---

## 8. 评测层设计（E）

- **样本管理**：沿用来源目录登记模板（编号/类型/公司/期间/渠道/可公开性/sha256/用途），开发集与盲测集按公司隔离封存。
- **计分口径**：是否有错、错误分类、来源页码分维度统计，重复标注去重；额外未标注产出单列（`additional_unannotated_predictions`），不计入分母。
- **现状**：四组开发样本回归 20 行原标注——是否有错 20/20 一致（去重 19/19）；分类 16/20 一致；页码 18/20 一致（2 处标注页码错误保留审计）。**该结果为开发样本回归，非独立盲测**。
- **盲测规划**：≥100 项未参与开发的新公司/新报告；先统一错误分类粒度、统一段落/页码标注口径、去重后再封存盲测集。

---

## 9. 工程、合规与可追溯

1. **封闭环境**：默认离线可跑；远程模型显式开启；权重离线下载 + 哈希校验（`tools/fetch_models.py`）。
2. **四类 JSONL 日志 + manifest**：access/tools/compute/results，运行清单记录代码提交号、依赖哈希、权重哈希、阈值、机器规格。
3. **复现命令**：`parse` → `verify`（产物哈希比对）→ `report`，现场演示同一命令即证明"展示版本 = 提交源码"。
4. **第三方登记**（`THIRD_PARTY.md`）：组件名/版本/来源/许可证/使用范围逐项登记；许可证注意——PyMuPDF/OpenViking 为 AGPL 系，产品化时换 pdfplumber/MIT 组件；MinerU 附加商业与署名条款。
5. **推送约定**：本机网络无法 push，用 `tools/push_via_api.py` 按本地 HEAD 重建远端（注意覆盖语义，先合并队友提交）。
6. **保密**：原始 PDF 只进本地 `data/`，密钥走环境变量，模型留痕去密钥。

---

## 10. 关键设计决策记录

| # | 决策 | 理由 |
| --- | --- | --- |
| D1 | 金额用十进制字符串，禁用二进制浮点 | 金额比对是核心业务，浮点误差不可接受 |
| D2 | 显示精度对齐用 ROUND_HALF_UP，不为标签放宽 | 保持判定可辩护（佛燃 8.5325/8.5326 案例） |
| D3 | 确定性规则优先，LLM 只做候选与解释 | 保证 fps 90% 判错精确率目标与可追溯 |
| D4 | 冲突来源/证据不足一律转人工 | 宁缺毋滥，控制误报率 ≤10% |
| D5 | 页级 ok/warn 才准入，fail 只提示 | 避免解析错误放大成业务误判 |
| D6 | 解析与知识库解耦（统一 JSON 契约） | OpenViking 无块级坐标，不能承担解析层 |
| D7 | 助手层不改判定、不编数值 | 事实/推论分层是竞赛设计要求的硬约束 |
| D8 | 报告声明覆盖边界（`supported_claims_only`） | 防止"系统通过 = 全文正确"的误读 |

---

## 11. 落地路线（建议顺序）

| 阶段 | 内容 | 交付 |
| --- | --- | --- |
| P0 基线 | 本地跑通 repo：`pdfparse run.cmd parse/report` + `factcheck run.py check/verify`（155 项测试） | 端到端命令行链路 |
| P1 展示层 | D 前端六视图 + 证据双向高亮 + 复核状态机 + 导出 | 可演示 Web 应用（已完成：`frontend/`，2026-09-29） |
| P2 助手层 | DeepSeek 接入：候选抽取实测、RAG 对话、护栏与留痕 | 对话式纠错助手（已完成：`frontend/assistant.py` + 助手问答视图；deepseek-chat 实测通过，2026-09-29） |
| P3 测评 | E 盲测集封存 + 100 项盲测 + 五指标统计 | 评测报告（已交付：`frontend/tools/metrics.py` + 开发集 3/4 组实测；待补：大地海洋答案表、独立盲测集） |
| P4 加固 | 异常 PDF（加密/损坏/超大）、扫描件全核查、无框表格、严谨阅读顺序 | 稳定性提升 |

## 12. 参考仓库文件索引

| 主题 | 位置 |
| --- | --- |
| 总需求与分工 | `repo/README.md` |
| 统一字段映射 | `repo/docs/B侧字段映射表.docx.txt` |
| B 交付与验收 | `repo/docs/B模块交付物清单.docx.txt` |
| B 推进方案 | `repo/docs/PDF解析模块推进方案.docx.txt` |
| 开源选型调研 | `repo/docs/开源方案调研报告.docx.txt` |
| 解析模块设计 | `repo/pdfparse/README.md`、`schemas/parse_result.schema.json` |
| 核查模块设计 | `repo/factcheck/README.md`、`schemas/check_result.schema.json` |
| 核查规则实现 | `repo/factcheck/src/yjcheck/rules.py` |
| LLM 候选抽取 | `repo/factcheck/src/yjcheck/model.py` |
| 交接与验收 | `repo/factcheck/docs/handoff.md`、`acceptance.md` |
