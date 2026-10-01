# 研报 PDF 解析模块（yjparse）

把研报 PDF 转成**带页码与坐标**的结构化结果，给出可解释的解析质量判定，并把全过程留痕，
满足竞赛对「数据可核验、过程可追溯、结果可复现」的要求。

## 这个模块解决什么

| 需求 | 实现方式 |
| --- | --- |
| 提取正文文本 | 引擎适配层统一输出块级文本，保留块顺序 |
| 提取表格 | PyMuPDF `find_tables()` / pdfplumber 抽取，保留单元格坐标与行列位置 |
| 保留页码与坐标 | 契约强制要求 `page` 与 `bbox`，缺失即判失败；坐标系为左上原点、单位为点 |
| 识别解析失败 | 三层机制：确定性指标、双引擎交叉校验、视觉模型抽检（抽检接口预留） |
| 人工核对与演示 | `preview` 命令把每个块与单元格的坐标画回页面图像 |
| 扫描件与位图文字 | 内置 OCR 兜底（RapidOCR，纯 CPU、离线），对无文本层或位图密集页面自动触发 |
| 解析失败第三层识别 | 视觉模型抽检：把页面图像与解析文本交给视觉模型比对，抓漏段、数字错位、图表标注混入正文 |
| 过程可追溯 | 四类 JSONL 日志（文件访问 / 工具调用 / 计算过程 / 结果生成）+ 运行清单 |
| 结果可复现 | 产物哈希写入运行清单，`verify` 命令逐项校验 |

## 快速开始（Windows）

```bat
cd pdfparse
run.cmd doctor                                   :: 查看环境与引擎可用性
run.cmd parse --input data\raw --out data\out     :: 解析目录下全部 PDF
run.cmd verify --out data\out                     :: 校验产物与运行清单一致
run.cmd report --out data\out                     :: 汇总质量报告
run.cmd preview --out data\out --pages 1,2        :: 把坐标画回页面图像，人工核对
run.cmd export-kb --out data\out --kb data\kb      :: 导出知识库可入库的 Markdown 与检索索引
run.cmd search --kb data\kb --query "归母净利润"   :: 离线检索，命中结果带页码与坐标
run.cmd search --kb data\kb --query "流动性" --compare   :: 多文档横向对比
run.cmd model-check --base-url https://<endpoint>/v1 --vision-model <模型名>   :: 提交前体检模型端点
run_app.cmd                                        :: 打开可视化页面：上传研报 → 看结果
```

## 可视化页面（上传一份研报看结果）

```bat
cd pdfparse
run_app.cmd
```

浏览器打开 `http://127.0.0.1:8501`，页面做的事：

1. **上传研报 PDF**（可多份，用于横向对比），或点“生成一份示例研报试跑”用合成样本；
2. 左侧选解析参数：主引擎、对照引擎、OCR 兜底、视觉模型抽检、表格策略；
3. 点“开始解析”，页面分五个标签页展示：

   - **概览**：文档数、页数、正常/警告/失败页、表格与句子数、双引擎一致度、解析耗时；
   - **逐页质量**：每页字符数、区块数、文字密度、乱码率、一致度与判定原因，可只看问题页并导出 CSV；
   - **证据定位**：选文档与页面，生成带框的页面图像（红框文本、蓝框表格与单元格、灰框图片、橙框页眉页脚），可展开看该页解析文本；
   - **检索与对比**：关键词检索（BM25），命中结果带页码、坐标、块编号与页面状态，可一键生成命中位置高亮图；支持多文档横向对比；
   - **下载产物**：结构化 JSON、逐页质量表 CSV、区块明细 JSONL、检索索引 JSONL。

页面定位是解析模块的可视化验证与演示工具；面向最终用户的交互页面由 D 负责。
它复用与命令行完全相同的解析链路，因此页面里看到的结果与 `parse` 命令一致。

Linux / macOS 用 `make` 或直接设置 `PYTHONPATH`：

```bash
make doctor
make parse INPUT=data/raw OUT=data/out
PYTHONPATH=src python -m yjparse parse --input data/raw --out data/out
```

## 目录结构

```
pdfparse/
├─ configs/thresholds.json      质量判定阈值（可调，写入运行清单）
├─ schemas/parse_result.schema.json  结果 JSON Schema
├─ src/yjparse/
│  ├─ contract.py               数据契约与校验（页码、坐标必填）
│  ├─ ledger.py                 四类日志 + 运行清单
│  ├─ quality.py                指标计算与失败判定
│  ├─ pipeline.py               主引擎 + 对照引擎 + 质量判定
│  ├─ artifacts.py              产物落盘与复现校验
│  ├─ report.py                 质量报告汇总
│  ├─ cli.py                    命令行入口
│  └─ engines/                  引擎适配层
├─ tests/                       单元测试与合成 PDF 用例
├─ tools/fetch_models.py        离线权重下载与哈希校验（待接入实际模型）
├─ REPRODUCE.md                 离线复现步骤
└─ THIRD_PARTY.md               第三方组件登记表
```

## 引擎适配层

| 引擎 | 依赖 | 坐标 | 表格 | 状态 |
| --- | --- | --- | --- | --- |
| `pymupdf` | PyMuPDF | 原生精度 | 支持（`find_tables`） | 已在本机端到端验证 |
| `pdfplumber` | pdfplumber | 行级坐标 | 支持 | 已在本机端到端验证 |
| `docling` | docling | provenance（页码 + 边界框 + 字符跨度） | 支持 | 适配器已写好，本机未安装，未做端到端验证 |
| `mineru` | MinerU 离线产物 | 块级坐标 | 支持（HTML 结构，无单元格坐标） | 适配器已写好，本机未安装，未做端到端验证 |

切换主引擎：

```bat
run.cmd parse --input data\raw --out data\out --primary pymupdf --reference pdfplumber
run.cmd parse --input data\raw --out data\out --primary mineru --mineru-output path\to\content_list.json
run.cmd parse --input data\raw --out data\out --primary docling --reference pymupdf
```

无框财务预测表（券商研报最后的财务表通常没有竖线）：

```bat
run.cmd parse --input data\real --out data\out_hybrid --engine-params "{\"pymupdf\": {\"table_strategy\": \"hybrid\"}}"
```

OCR 兜底：

```bat
run.cmd parse --input data\real --out data\out --ocr auto      :: 默认，仅无文本层或位图密集页面
run.cmd parse --input data\real --out data\out_all --ocr always :: 全部页面都走 OCR
run.cmd parse --input data\real --out data\out --ocr off        :: 关闭 OCR
```

OCR 结果作为普通文本块进入同一份契约，`level` 为 `ocr`、`confidence` 为平均置信度，
页面备注里会出现 `ocr_used`。未安装 OCR 依赖时不会报错，只在日志里记录一次失败。

视觉模型抽检（第三层质检）：

```bat
:: 环境变量方式（推荐，避免密钥进命令行）
set YJPARSE_VLM_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
set YJPARSE_VLM_API_KEY=<密钥>
set YJPARSE_VLM_MODEL=<视觉模型名>
run.cmd parse --input data\real --out data\out --vlm auto      :: 只抽检 warn / fail 页
run.cmd parse --input data\real --out data\out --vlm always --vlm-sample-ratio 0.2

:: 没有密钥时用本地 Mock 服务彩排整条链路
py -3 tools\mock_model_server.py --port 8899 --verdict mismatch
run.cmd parse --input data\real --out data\demo --vlm always ^
  --vlm-base-url http://127.0.0.1:8899/v1 --vlm-model mock-vl --vlm-max-pages 3
```

抽检结果写进页面备注（`vlm_check:ok` / `vlm_check:mismatch:<问题类型>`）与质量报告
（`vlm_checked_pages`、`vlm_mismatch_pages`），命中的页面自动升级为 `warn`，不改变 `fail` 判定。
模型端点不可用时只记 `vlm_check:error`，不影响主流程。

`table_strategy` 三个取值：`lines` 默认，只用线框检测，段落结构最完整；
`hybrid` 额外接受满足财务表特征的无框表格；`text` 只用文本策略。
默认模式下遇到疑似无框表格，会在页面备注里给出 `info:borderless_tables_detected` 提示。

`--reference` 指定对照引擎，默认 `auto`：优先用 pdfplumber，缺失时退回主引擎的文本层。
两个引擎的逐页文本一致度低于阈值时，该页会被标记，用来发现版面识别错误。

## 产物说明

```
data/out/
├─ <文档名>/
│  ├─ parse_result.json    完整结构化结果（含 pages、blocks、cells）
│  ├─ pages.jsonl          每行一页
│  ├─ blocks.jsonl         每行一个块，带页码与坐标，供下游检索与比对
│  ├─ quality_report.json  质量判定与原因
│  └─ quality_table.csv    逐页质量表（Excel 可直接打开）
├─ quality_summary.json    多文档汇总与复核队列
├─ quality_summary.csv
├─ failure_list.csv        需要人工处理的页面单表（含建议动作）
└─ logs/<run_id>/
   ├─ access.jsonl         文件访问
   ├─ tools.jsonl          工具与模型调用
   ├─ compute.jsonl        指标计算与判定
   ├─ results.jsonl        产物生成
   └─ manifest.json        环境、依赖、参数、阈值、权重与产物哈希
```

## 给下游模块的接口约定

1. 核查与比对模块只引用状态为 `ok` 或 `warn` 的页面，`fail` 页只提示解析异常。
2. 任何引用都必须带 `page` 与 `bbox`，用于回跳到原文位置。
3. 表格引用到单元格时，使用 `block_id` + `row` + `col` 定位。
4. 坐标单位为点、原点在左上角；前端高亮按渲染缩放比换算。

## 已知边界

- PyMuPDF 的阅读顺序为坐标近似排序，严谨的阅读顺序需要版面引擎（Docling 或 MinerU）作为主引擎。
- 扫描件没有文本层，会被判定为失败并提示需要 OCR，本模块不内置 OCR 模型。
- 视觉模型抽检只保留接口与阈值（`vlm_sample_ratio`），接入本地视觉模型后启用。
- 第三方组件与许可证义务见 `THIRD_PARTY.md`。

## 样例结果解读

仓库自带两个合成样本（`tests/sample_pdfs.py` 生成，可随时重建），用它们跑一遍的预期结果是：

| 样本 | 结果 | 说明 |
| --- | --- | --- |
| `样例研报_2025H1.pdf` | `warn` | 2 页、14 个块、1 张表（16 个单元格带坐标）；因合成页面文字稀疏，触发 `low_text_coverage` 警告，双引擎一致度为 1.0 |
| `扫描件样例.pdf` | `fail` | 无文本层，原因 `no_text_layer:chars=0`，进入复核队列 |

合成样本页面故意留白，所以文本密度远低于真实研报；接入真实研报后这一项通常不会触发。
如果实际语料普遍偏稀疏，调整 `configs/thresholds.json` 中的 `text_coverage_warn` 即可，
阈值会随运行清单一起记录，改动可追溯。

## 升级路径

1. **接入版面引擎**：安装 Docling 或配置 MinerU 离线产物，用 `--primary` 切换，坐标与阅读顺序质量会明显提升，本模块其余部分无需改动。
2. **提高 PyMuPDF 版面质量**：官方建议安装 `pymupdf_layout` 包以启用更强的版面分析（注意其许可证与 PyMuPDF 一致）。
3. **启用视觉抽检**：接入本地视觉模型，按 `vlm_sample_ratio` 抽页核对解析文本与页面图像是否一致。
4. **替换主引擎以规避 AGPL**：把 `--primary` 换成 `pdfplumber`，即可脱离 PyMuPDF 依赖。

## 真实研报验证结果

四份公开研报（合计 195 页，含一份 134 页、图表密集、带横向图表页的宏观策略报告）的试跑结果：
修复后**无失败页**，平均双引擎一致度 0.94 到 0.9997，剩余警告集中在文字密度、
无框表格与阅读顺序三类可解释情况。逐项问题定位与修复记录见
[docs/real_report_test.md](docs/real_report_test.md)。

## 质量指标口径

| 指标 | 含义 | 默认阈值 |
| --- | --- | --- |
| `text_coverage` | 每千平方点的有效字符数，衡量版面文字密度 | 低于 0.8 只记录（图表页、标注页、分隔页天然字少） |
| `char_count` | 页面有效字符数 | 等于 0 判失败；大于 0 且低于 30 记为信息 |
| `garbled_ratio` | 替换字符、CID 残留、私用区字符占比 | 高于 1% 判失败 |
| `engine_agreement_bag` | 与对照引擎的内容一致度（字符多重集合，抗空格差异） | 低于 0.85 告警，低于 0.6 判失败 |
| `engine_agreement_token` | 词元口径一致度（诊断用，受空格影响） | 不参与判定 |
| `engine_agreement` | 顺序敏感一致度，低于 0.75 记为阅读顺序提示 | 仅提示，不升级状态 |
| `image_area_ratio` | 图片块覆盖页面的面积比例 | 高于 30% 时，无文字页判为整页图表 |
| `table_empty_cell_ratio` | 表格空单元格比例 | 高于 75% 且单元格数不少于 8 才告警 |
| `table_min_fill_ratio` | 表格填充率下限 | 低于 20% 判定为图表伪表格并丢弃 |

所有阈值集中在 `configs/thresholds.json`，修改后会自动写入运行清单，改动可追溯。

**告警口径的原则**：`warn` 只用于“需要人工看一眼”的情况——OCR 兜底页、跨引擎内容不一致、
表格结构可疑、整页图表需要 OCR。文字密度、坐标收拢、对照引擎自身残缺、章节分隔页等属于
正常版面特征或对照侧限制，统一记为 `info:` 前缀，只进报告不升级状态。
本轮按此口径调整后，四份公开研报 195 页的告警从 65 页降到 6 页（3%），失败页保持 0。

## 阅读顺序的现状与结论

模块提供三种排序方案，可用 `--engine-params "{\"pymupdf\": {\"reading_order\": \"band\"}}"` 切换：

| 方案 | 做法 | 适用 |
| --- | --- | --- |
| `naive` | 按坐标 (y, x) 排序 | 单栏页面 |
| `band` | 按跨栏块分带，带内先左栏后右栏（默认） | 明确的双栏页面，只在判据成立时改变顺序 |
| `xycut` | 递归投影切割 | 复杂多栏，但对图表密集页更激进 |

用 `tools/eval_reading_order.py` 在四份公开研报上做过对比：`band` 在 24 页的国金研报里
改变了 11 页的顺序、且不会引入同栏向上回跳；三种方案与两个参照物（PDF 内容流顺序、
对照引擎顺序）的一致度差距都在 2 个百分点以内，`xycut` 反而更容易打乱顺序。

结论是坐标法已经接近上限，**阅读顺序是当前最弱的一环**，需要严谨顺序时应把 Docling 或 MinerU
作为主引擎。评估脚本已入库，换引擎后可直接复测。

## 结构化产出

除了解析质量判定，结果里还包含下游核查需要的结构信息：

| 字段 | 位置 | 用途 |
| --- | --- | --- |
| `type=heading` + `level` | 块级 | 按字号与形态判定标题层级，用于还原章节结构 |
| `sentences[]` | 块级 | 句级文本、字符区间与近似坐标，支撑“引用到某页某句” |
| `caption` / `source_note` | 表格与图片块 | 归并“图 1 …”与“资料来源：…”，形成完整证据链 |
| `cells[]` | 表格块 | 单元格文本、行列号与坐标，支撑数值级核对 |

预览图默认输出到 `data/out_*/<文档名>/preview/page-XXX.png`，
红框文本、蓝框表格与单元格、灰框图片、橙框页眉页脚。
