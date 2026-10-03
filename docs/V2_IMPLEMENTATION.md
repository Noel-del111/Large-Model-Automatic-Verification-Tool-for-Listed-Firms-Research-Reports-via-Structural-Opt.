# 第二版实施与复现说明

**当前口径：本次完成 200 篇开发阶段实验与阶段性验证，未训练/微调模型权重，最终独立测试尚未进行。** 已有真实推理、离线评分和工程测试；历史 `eval_oct05` 划分名保留，199 篇最终保留集未运行。完整边界与接手顺序见 [范围与交接说明](V2_SCOPE_AND_HANDOFF.md)，群内文案见 [微信群说明](TEAM_HANDOFF_WECHAT.md)。

更新时间：2026-10-04。本文说明实际接口、离线复现路径和仍待实测的边界；不将工程测试等同于模型效果验证。

**当前交接状态：200 篇队列已处理完毕，199 篇成功、1 篇超时；模型调用已停止，本次仅上传进度 PR，等待用户进一步指令。** 后续 598 篇与最终 199 篇不在当前自动运行范围。累计预算已获授权提高至 100 元，原账本与 20→50→100 元两次授权记录均保留；额度提高不意味着必须花完。本批 200 篇使用启动时的模型、提示及加载的源码，后续预算与停止控制的源码差异另存审计补丁。

用户最新交付截止日为 **10 月 8 日**。10 月 5 日形成可运行第二版及完整对照，10 月 7 日使用封存集验收候选版本。当前使用现成模型进行提示和流程优化，没有微调模型权重；598 篇开发池扩跑用于检查泛用性，200 篇阶段验证与最终 199 篇保留集分别报告。某批数据一旦用于决定修改，之后只作为验证和回归依据。具体安排见 [项目时间线](../README.md#第二版交付时间线10-月-8-日提交)。

## 1. 本轮交付

主线参考 **FinED-Bench《Are Large Language Models Reliable Reviewers? A Benchmark for Error Detection in Financial Documents》**。本地正确原文位于仓库相邻目录：

`../Are Large Language Models Reliable Reviewers A Benchmark for Error Detection in Financial Documents/2026.findings-acl.1481.pdf`

根目录 `pdfparse/`、`factcheck/` 是唯一实现，旧 `repo/` 启动入口转发到根目录。既有配对核查继续输出三种状态；新增独立文本检测，保留原文偏移、多片段错误、候选来源和验证状态，不为纯文本虚构 PDF 页码。

- 输入能放入上下文时整篇处理；超限按段落/章节窗口切分并保留邻接文本。
- 跨窗口补查使用已知指标及其原始句子构建索引；超大关联组、未处理范围和失败原因公开记录。该索引不是已完成任意实体、任意指标的全文推理。
- 15 类是候选检测的分类空间，不表示规则已覆盖所有类别。模型结论只有锚点及确定性验证满足时才可升级为已确认错误。
- 配对结果附加独立的 `text_review` 字段；其候选不混入传统配对 `findings` 的评分分母。

## 2. 数据准备与基线冻结

以下命令在仓库根目录的 PowerShell 执行，Python 运行环境需已安装项目依赖。原始材料与生成数据保留本地。

```powershell
New-Item -ItemType Directory -Force data/v2/baseline | Out-Null
git archive d71f8c7 --format=zip -o data/v2/baseline/source-d71f8c7.zip
.venv\Scripts\python.exe evals/dataset_prepare.py --data "../Are Large Language Models Reliable Reviewers A Benchmark for Error Detection in Financial Documents/测评集/FinED-Bench-main" --out data/v2/dataset --seed 20261003
```

当前本地已保存上述第一版归档。新克隆需拥有 `d71f8c7` 提交；无法解析该提交时应先获取该版本，不能用当前修改后的代码冒充第一版。

| 范围 | 开发 dev | 10 月 5 日 eval_oct05 | 10 月 7 日 holdout_oct07 | 总计 |
| --- | ---: | ---: | ---: | ---: |
| 全部文档 | 598 | 200 | 199 | 997 |
| 个股＋行业研报 | 265 | 89 | 88 | 442 |

输入源为标准集 973 篇和长文集 24 篇；原始错误 4,178 个，可评分 4,093 个，排除审计 85 个。以生成的 `manifest.json` 中源文件哈希、分组、错误记录和划分数量为最终依据。无原文唯一对应位置的模糊修复不得自动成为精确答案。

产物包括：

- `inputs.{split}.jsonl`：原文、稳定 ID、来源哈希、场景、长度与分组信息，无答案字段。
- `gold.{split}.jsonl`：错误实例与原始/修复定位、可评分状态；多片段仍是一个错误。
- `examples.dev.json`：仅来自开发集的完整标注短文示例；当前被测文档不能作为自己的示例。
- `manifest.json`：来源哈希、划分、排除理由、文件哈希和统计。

按规范化文本、标题和可识别的标题公司标识构造关联组；分组不可跨集合。无法识别公司身份的材料明确保留未知，不能声称已完成所有公司的严格隔离。公开留出集也不能证明模型训练阶段从未见过材料。

## 3. 离线运行、真实模型试跑与续跑

先用开发集做 10 篇离线试跑，再单独评分：

```powershell
.venv\Scripts\python.exe evals/run_v2.py run --inputs data/v2/dataset/inputs.dev.jsonl --examples data/v2/dataset/examples.dev.json --mode offline --max-documents 10 --out data/v2/runs/dev-offline-10
.venv\Scripts\python.exe evals/run_v2.py score --inputs data/v2/dataset/inputs.dev.jsonl --gold data/v2/dataset/gold.dev.jsonl --runs data/v2/runs/dev-offline-10 --out data/v2/runs/dev-offline-10/score.json
.venv\Scripts\python.exe evals/run_v2.py status
```

离线只运行冻结旧规则与当前组合流程的规则部分，未运行模型组。当前组合流程会明确标记模型未运行，因此 `coverage.complete=false` 不等于程序崩溃；应结合 `rules_complete`、处理范围和原因解释结果。

真实模型沿用 OpenAI 兼容接口。配置支持进程环境变量，或仅在本机保存的 `data/v2/local_model_config.json`；环境变量优先，读取配置不会修改进程环境。`data/` 已被 Git 忽略，密钥不进入提交。状态命令仅显示字段是否已配置，不打印值。

本机已接入 `deepseek-flash`（DeepSeek-V4.1-Flash）。当前后续运行配置为 `thinking=enabled`、`reasoning_effort=low`、输出上限 65,536 token、超时 480 秒；同一次对照的直接检测与组合流程使用相同参数。早期各轮参数以其 `run_config.json` 为准，不能将当前配置追溯套用到历史结果。

当前按已核验的官方周末费率预留：输入 1 元/百万 token、输出 4 元/百万 token，`YJCHECK_PRICE_VALID_UNTIL=2026-10-04T16:00:00Z`，即北京时间 10 月 5 日 00:00（周日结束）。初始化及每次请求/重试前均检查有效期，过期自动阻止新请求，需重新核验费率。此前 83 次调用按高峰保守单价核算的 **4.662582 元原样保留**，不按新费率重算或清零；这也是历史快照，不是当前账户余额。费用字段不是服务商实际扣费。价格来源：[DeepSeek 官方价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)。API 参数来源：[官方 Chat Completions](https://api-docs.deepseek.com/zh-cn/api/create-chat-completion/)。

| 变量 | 内容 |
| --- | --- |
| `YJCHECK_BASE_URL`、`YJCHECK_MODEL`、`YJCHECK_API_KEY` | 已授权端点、准确模型 ID 与密钥 |
| `YJCHECK_INPUT_CNY_PER_MTOK`、`YJCHECK_OUTPUT_CNY_PER_MTOK` | 经核验的人民币/百万 token 单价 |
| `YJCHECK_PRICE_SOURCE`、`YJCHECK_PRICE_DATE` | 官方价格 HTTPS 页面及 YYYY-MM-DD 采集日期 |
| `YJCHECK_PRICE_VALID_UNTIL` | 带 UTC 时区的价格有效期；当前周末配置为 `2026-10-04T16:00:00Z`，到期拒绝新请求 |
| `YJCHECK_CONTEXT_TOKENS` | 对应端点、对应模型的实际上下文上限 |
| `YJCHECK_MAX_OUTPUT_TOKENS` | 单次输出上限，默认 4096，需落在模型支持范围 |
| `YJCHECK_BUDGET_CNY` | 默认 20，可降低；当前用户已明确授权累计 100 元。提高账本上限需显式授权迁移，最高 100，仅改配置不会提高授权 |
| `YJCHECK_MAX_RETRIES` | 默认 0，可设为 1；重试同样计费 |
| `YJCHECK_REASONING_EFFORT` | 可选空值、`low` / `high` / `max`；与 `thinking=disabled` 同时非空时拒绝启动 |
| `YJCHECK_TIMEOUT`、`YJCHECK_THINKING` | 超时秒数；可选 `enabled` / `disabled`，空值不向端点传思考参数 |

缺少价格、上下文或有效端点时真实运行拒绝启动。不得填猜测价格、把人民币和美元混用，或把在线调用默认按零价计算。价格核验应在实际选定端点后进行。

**当前 200 篇队列已结束，模型调用暂停。不要重跑该批、补发失败请求或另开小样本批次。** 如需重新生成本地报告，可执行下面的本地评分、汇总和公开汇总导出；这些命令不调用 API：

```powershell
.venv\Scripts\python.exe evals/run_v2.py score --inputs data/v2/dataset/inputs.eval_oct05.jsonl --gold data/v2/dataset/gold.eval_oct05.jsonl --runs data/v2/full-eval_oct05 --out data/v2/full-eval_oct05/score.json
.venv\Scripts\python.exe evals/finalize_full_acceptance.py --scope eval200
.venv\Scripts\python.exe evals/export_progress_snapshot.py
```

汇总退出码 0 表示本批所有组执行及评分完成；退出码 1 表示报告已生成但存在执行失败，状态为 `reports_ready_with_execution_failures`；退出码 2 表示缺少评分或完整性异常，不能当作可发布结果。只有每组均已尝试 200 篇、没有缺失预测且评分哈希通过核验时，导出器才允许发布阶段汇总。尝试数、成功完成数和失败数必须按实际记录分别披露；例如 200 篇已尝试、199 篇完成、1 篇失败时，不得写成 200 篇全部成功，评分仍保留 200 篇分母。生成后查阅 [200 篇交接报告](V2_EVAL200_RESULTS.md) 和 [公开汇总计数](validation/v2-eval200/aggregate.json)。

模型模式为每篇依次完成 `legacy_rules`、`model_direct`、`hybrid` 三组，冻结规则不调用模型；后两组使用同一端点与模型。队列只按场景、长度与稳定 ID 排列，不利用答案选样。只有用户明确恢复后，才可按新的授权范围做付费小样本试跑或扩跑，使用已核验的参数和 `examples.dev.v2.json`；`--max-documents 0` 表示全部输入，不是当前交接命令。

相同原文及提示的模型返回在直接检测组与组合组之间共用，属于同一候选的后处理对照。调用费用按唯一 `call_id` 汇总。组合组的缓存回放墙钟不能当作实际模型速度；评分另列 `latency.measured_arm_wall_seconds` 和 `latency.estimated_end_to_end_seconds`，后者在回放墙钟上补回每个唯一模型调用及重试的原始耗时。新调用已包含在墙钟内，不重复计入；缺少原始耗时则该文档端到端估算留空。该估算未替代组合流程独立部署的实测延迟。

`data/v2/model_usage.sqlite3` 是共享累计账本，修改输出目录不重置预算。每个请求先保守预留最大费用，再依据服务方 usage 结算；没有 usage、中断或不确定失败保留预留金额。Mock 使用独立账本。输入 token 是保守估算，实测 token 必须取服务方记录。

本轮已依据用户直接授权，通过 `evals/authorize_budget.py` 将同一账本上限按 20→50→100 元迁移；历史调用、费用及授权前后上限保留在审计中。该 CLI 接收 `--limit-cny`、稳定的 `--authorization-id` 和对应人类授权原文 `--authorization-basis`，重试复用同一授权 ID。默认仍为 20 元，降低上限无需迁移；不能通过删账本、换目录或仅将配置写成 100 来替代授权记录。

队友接手同一账户和同一累计账本时，应保留当前已授权的 `YJCHECK_BUDGET_CNY=100`、原账本及其授权记录；不要照抄新环境示例的默认 20。普通打开账本会采用较低上限，误设 20 会把原 100 元上限下调，历史费用仍保留；重放旧授权 ID 也不会自动恢复被降低的上限。保留预算设置不表示允许恢复调用，当前仍执行上述停止要求。

运行配置记录输入、源码、示例、基线归档和端点指纹；配置或代码变化须换输出目录，禁止混用旧预测。仅修正规则时，可在新目录通过重复的 `--reuse-model-responses-from` 指定历史目录；严格核对模型、端点、请求、单价与账本身份后（累计上限可随明确授权改变，并记录原/新值），按原消息和用途哈希复用成功模型回复，不复制预测或答案。任何提示变化都会造成缓存未命中；最大输出、思考强度等请求设置不同则拒绝导入。历史调用与本次新增费用分开记账。完成的文档组可缓存；不完整结果保留重试历史。达到预算时保存进度并停止。输出的 `queue.json`、`run_config.json`、`status.json`、`predictions/` 分别记录计划、配置、完成情况和逐文档结果。

文本规则保留段落及 Markdown 表格边界，并将块内位置映射回原文全局偏移，避免跨表头绑定单位。枚举规则仅比较能完整解释的对象和值列表；范围、逐项括注、复杂单位或不明确对象关系会弃权，不拿截短列表推断数量冲突。JSON 恢复只允许正常结束响应中唯一缺失的末尾对象闭合 `}`，逐字保留语义内容，并记录原始/修复哈希、插入位置及规则；原始回复缓存不变，截断响应不恢复。API 成功但解析失败单列，失败文档仍计入评测分母。

未来用户明确恢复封存评测后，才可按授权范围选择 `eval_oct05` 或 `holdout_oct07` 的输入及 gold，仍只使用开发集示例；当前不得通过替换输入或输出目录重跑 200 篇或启动 199 篇保留集。10 月 5 日已看答案并用于修改的样本，之后只能作为回归材料。

## 4. 前端和接口

```powershell
frontend/run_app.cmd
```

- “单份文本检查”接收 UTF-8 TXT/Markdown 或粘贴原文；默认离线，可显式启用模型。
- 配对核查保留原有视图，并增加“文本补充检查”页。CLI 使用 `--review-text` 或接口 `ModelConfig.review_text=True` 时启用对应模型路径；页面开启模型时一并启用。
- 概览区分已抽取声明、自动结论、待人工确认及文本候选；候选增加不能解释为自动覆盖提升。
- 复核先点“开始计时复核”，再保存人工结论；耗时记录在独立 `review.json`。未开始计时的记录为未测量，不作零秒样本。
- 独立接口为 `yjcheck.text_review.detect_text(content, document_id=..., scene=..., detector=..., chat=..., examples=..., max_input_tokens=...)`；返回 `text-review/1.0`，包括 `errors`、`coverage`、`traces`、`raw_candidates`、`rejected_candidates`。
- 单个错误含类型、一个或多个 `spans[{start,end,text}]`、理由、检测来源、验证状态与证据；字符区间采用半开区间。不生成虚构页码，不把空候选列表解释为完整的 `no_issue` 证明。

## 5. 验证范围和仍需完成的工作

最新冻结前工程回归为 B/C 共 293 项（B 54、C 239），全部通过、无跳过；评测 61 项、前端及配对指标 15 项通过。早期离线报告中的较小测试数是历史快照。另通过本地 Mock HTTP、证据高亮、PDF/CSV/JSON 导出和产物校验。598 篇开发集与四组 E 配对材料已完成冻结旧版离线对照，结果见 [第二版离线验证报告](V2_RESULTS.md)。这些验证不能替代真实模型评测。真实调用的阶段对照见 [真实模型阶段报告](V2_MODEL_RESULTS.md)。

全量运行期间另完成交付核验：四组真实模型配对回放结果加载到前端全部七个视图，抽取声明及来源事实数量与原始结果一致，点击产物完整性校验均通过；禁止创建付费模型客户端，新增调用为 0，原始结果哈希未改变，也未保存任何人工复核意见。证据见 [前端真实结果联调记录](../data/v2/frontend-cached-real-smoke.json)。这验证已有结果能展示和导出，不代替新文档推理效果或真人计时。

源码冻结、当前提示示例、数据准备清单和归档的 77 项文件检查未发现哈希差异，运行源码指纹与冻结值一致，见 [运行中资产核验](../data/v2/release/current-asset-audit.json)。答案只按字节核对文件哈希，未交给模型；答案的对照依据是原始数据准备清单，不声称该清单经外部签名。核验时间保留在产物中，不能把文件快照当成持续监测保证。 该快照早于随后 100 元预算及停止控制的修改；两份相关源码的新旧哈希和差异另存于 `data/v2/release/budget-amendment-100.json` 与 `budget100-inference.patch`，原 200 篇运行的源码和提示记录保留不变。

预算上限与停止控制改动后，交接前重新验证 B/C 共 **294 项**（B 54、C 240，无跳过）和评测工具 **89 项**，全部通过。公开证据见 [交接验证摘要](validation/v2-eval200/README.md)，原始日志保留本地。本次汇总程序使用 `--scope eval200`，已为这 200 篇生成交接报告，598 篇未执行明确披露；缺少评分或完整性异常不会被包装成完成。未来的 798 篇完整模式仍保留。命令及失败退出码见 [评测运行说明](../evals/README.md#运行)。

公开文本报告包含候选 P/R/F1、规则确认结果 P/R/F1、分类准确率、按场景/长度/错误类型分层、文档完成数、候选复核负担和耗时中位数/P95；共享账本及调用 trace 提供费用与 token。报告同时列出完整计划队列与所有组都完成的配对交集，避免只报告成功子集。

此前开发阶段的历史快照：20 篇新开发样本使用原始模型回复缓存重放，**0 次新调用、20/20 执行完成**；全部提示口径直接模型为 TP39/FP51/FN37、F1 46.99%，组合为 TP39/FP53/FN37、F1 46.43%。JSON 恢复带回 9 条未命中提示，增加 FP，没有形成能力提升。已完成的 2 篇长文也以 0 次新调用重放，规则额外 FP 已清零，两组均为 TP2/FP7/FN2。119,714 字符的最长文档另以 65,536 输出上限重试，现已 1/1 执行完成，无接口或解析失败，实际输出 28,164 token、保守费用 0.189605 元；直接及组合均为 TP0/FP5/FN4，4 个 gold 全部漏检。两个批次配置不同，不能把完成子集 2/2 当作同一配置下 3/3 的效果验收。该阶段工程通路进入后续测量，长文效果仍有不足。来源：[20 篇重放评分](../data/v2/model-dev-validation20-fixed/score.json)、[完成长文 2 篇评分](../data/v2/model-dev-longcomplete2-fixed/score.json)、[最长文档重试](../data/v2/model-dev-longretry64k/score.json)。

`eval_oct05` 的 200 篇已全部尝试，199 篇成功、1 篇超时（未自动重试）；按用户指令停止并上传进度 PR。`dev` 598 篇已通过持久化停止记录阻止启动，`holdout_oct07` 199 篇继续封存。本批结果见 [200 篇交接报告](V2_EVAL200_RESULTS.md) 与 [公开证据](validation/v2-eval200/README.md)，累计保守费用 13.571324 元。

2026-10-03，用户确认尚无人审及真人复核计时，要求先完成模型全量评测。这两项保留为后续工作；本轮模型验收不将其写为已测量。独立业务结果见 [配对回归报告](V2_PAIRED_RESULTS.md)，本次 200 篇队列使用上述限定范围的汇总交接；未来 798 篇总验收才使用 `--require-complete`。

正常样本已准备为 16 条待审核记录（10 条公开 SFT 研报原文＋6 条合成难负例），全部为 `pending`，尚未生成已验证 gold。复现与签核导入命令：

```powershell
.venv\Scripts\python.exe evals/prepare_review_pack.py prepare --source "../Are Large Language Models Reliable Reviewers A Benchmark for Error Detection in Financial Documents/测评集/FinED-Bench-main/sft_data/sft_data_wo_errors.json" --out data/v2/negative_review.jsonl
.venv\Scripts\python.exe evals/prepare_review_pack.py approve-import --review-file data/v2/negative_review.approved.jsonl --out data/v2/negative-approved
```

第二条命令只能在人工选出并签核相应记录之后执行；示例中的 `.approved.jsonl` 需由审核过程产生。每条需有 `human_review_status=approved`、非空 `reviewer`、带时区的 ISO `approved_at`，且 `content_sha256` 与审核内容一致；任一记录不满足则整批拒绝。输出目录必须为空。导入产物为 `inputs.negative_review.jsonl`、空错误数组的 `gold.negative_review.jsonl` 和独立 `approvals.jsonl`。这批材料固定属于 `negative_review` 单独评测线，不混入公开盲测。

以下不能由离线工程验证替代：

1. 真实模型直接检测和模型组合流程的效果、费用及延迟——已完成开发小样本和本批 200 篇冻结对照，后续开发扩展及最终保留集仍待指令。
2. 正确文本的误报率——正常研报片段需人工审核；待审核包不能作为已验证负例。
3. 外部证据是否在语义上支持结论、PDF 定位是否正确——需独立配对业务集验收。
4. 可自动判断却转人工、证据不足却强判的比例，以及与纯人工相比的复核耗时——需任务级标准答案和实际复核记录。
5. 10 月 5 日、10 月 7 日的盲测与会议——属于团队后续执行时间点，不因工具完成而视为已经发生。

FinRiskAtlas 的 `contracts.py`、`operation_eval.py` 仍可作为旧的可选分析工具；其中 Ask/Proceed 不能直接等同于人工复核效率，也不能归为 FinED-Bench 的实验结论。

## 6. 原计划与验收证据对应

以下对应原计划中的交付要求，并已按 200 篇阶段性交接更新。工程通过、开发回归通过、模型全量评测完成和人工验证是不同状态；不得仅凭本表或某个测试数宣布整项验收完成。

| 原计划要求 | 当前证据入口 | 状态与后续验收条件 |
| --- | --- | --- |
| 使用正确论文及 15 类错误 | `text_taxonomy.py`、本地原文、`test_fifteen_types_match_release_labels` | 分类与提示已实现；不是已证明 15 类全部有效 |
| 973 篇标准文本、24 篇长文的数据审计 | `data/v2/dataset/manifest.json`、`dataset_prepare.py` | 997 篇、原始 4,178 个错误、85 个隔离标注有记录；哈希复核通过 |
| 固定 60/20/20、同源分组、答案隔离 | `inputs.*.jsonl`、`gold.*.jsonl`、数据准备及 runner 隔离回归 | 598/200/199；推理拒绝答案字段，示例仅来自开发集；最终仍需核对完整队列与来源 |
| 补人工确认的正常样本 | `data/v2/negative_review.jsonl`、`prepare_review_pack.py` | 16 条全部待审核；按用户指示暂缓，正常文本误报率未测量 |
| 公开文本和研报—财报分别测试 | 公开各运行的 `score.json`、[业务配对报告](V2_PAIRED_RESULTS.md) | 四组配对是开发回归；不能将公开文本结果当作外部证据核查结果 |
| 冻结基线、直接模型、组合流程三组对照 | `data/v2/release/freeze_manifest.json`、`baseline/`、全量 `run_config.json` | 源码与参数冻结；直接和组合共用同一候选返回，属于后处理对照 |
| 小样本优化后预算内扩跑、可恢复 | [阶段报告](V2_MODEL_RESULTS.md)、共享账本、`queue.json`、预算与重试回归 | 小样本工程修复已验证；200 篇已尝试、199 成功、1 超时，未按成功子集缩小分母；598 篇待指令，累计上限 100 元 |
| 全文结构、原始偏移、长文及跨段处理 | `text_context.py`、`text_review.py`、窗口中部与跨段回归 | 超限才切窗，未处理范围公开；跨段索引限已知指标，长文检测效果仍需看真实分层结果 |
| 开放文本候选与确定性验证分离 | `text_review.py`、模型强判/无证据/空结果回归 | 模型自述不能形成自动确认，合法空结果不等于全文正确 |
| 正文和表格统一指标、保留原始名称 | `metric_catalog.py`、`test_generic_metrics.py`、配对回放审计 | 受支持指标归一化及跨段/跨表对齐已测；未知语义不自动升级 |
| 根目录 B/C 唯一实现及旧入口兼容 | B/C 工程日志、旧 Python 路径和相对路径 CLI 回归 | 交接 B/C 294 项通过；本批运行源码哈希与冻结值一致 |
| 论文式一对一评分、完整分母、类型独立 | `evals/tests/test_scoring.py`、`test_run_v2.py`、`test_acceptance_audit.py` | 空预测、重复、类型错、顺序变化、多片段、异常标注和缺失结果均有回归；项目实现不冒充作者官方脚本 |
| 模型、提示、token、时间、费用、失败记录 | `run_config.json`、逐文档 traces、SQLite 累计账本 | 已记录真实调用与完整 200 篇评分；累计保守费用 13.571324 元。保守核算不等同服务商实扣 |
| 抽取量、自动判断、复核原因及原始/人工意见分开 | `frontend/app.py`、`review_store.py`、[真实结果前端联调](../data/v2/frontend-cached-real-smoke.json) | 四组结果七个视图联调通过，未新增模型调用；未伪造真人复核 |
| 解析、定位、高亮、导出、产物校验继续通过 | B/C 工程日志、Mock 联调、前端真实结果产物校验 | 工程路径有验证；页码命中不证明语义证据充分，输入质量警告仍保留 |
| 最终全量效果与费用报告 | `audit_full_acceptance.py`、`finalize_full_acceptance.py` | 已生成 `docs/V2_EVAL200_RESULTS.md`，披露 199 成功及 1 超时后交接；798 篇总验收仍未完成 |
| 正常误报率、证据语义支持、过度转人工及真人效率 | 人工签核与复核计时入口 | 仍需独立人工证据；本轮按用户指示先完成模型全量评测 |
| 10 月 5 日汇总、10 月 7 日保留集、10 月 8 日提交 | [最新交付时间线](../README.md#第二版交付时间线10-月-8-日提交) | 属于后续团队安排；会议、保留集验收与提交不因工具完成而自动算作已发生 |

当前进度 PR 使用上文的 `finalize_full_acceptance.py --scope eval200` 与公开汇总导出，按实际尝试数、完成数和失败数交接；有失败的已处理队列可发布明确标记的阶段结果，不为追求“全部成功”补发调用。`evals/audit_full_acceptance.py --require-complete` 仅用于未来用户明确恢复后的 798 篇总验收，目前会因 598 篇暂缓而返回非零，不是本次进度 PR 的完成门槛，也不是补跑授权。任何执行及评分完整性检查都不替代检测质量判断或尚缺失的人工验证。
