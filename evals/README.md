# FinED-Bench 评测说明

**当前口径：200 篇是开发阶段实验与阶段性验证，未做模型权重训练；最终独立测试尚未进行。** 已有推理评分与工程回归，不称为“没有任何测试”。历史 `eval_oct05` ID 不改名，199 篇 `holdout_oct07` 留作最终独立测试。完整数据、版本与交付边界见 [范围与交接说明](../docs/V2_SCOPE_AND_HANDOFF.md)。

更新日期：2026-10-03。本轮依据 **《Are Large Language Models Reliable Reviewers? A Benchmark for Error Detection in Financial Documents》**，本地原文为相邻论文目录中的 `2026.findings-acl.1481.pdf`。FinRiskAtlas 是另一篇论文，不再作为本轮主线或误称为该 PDF 的内容。

## 两条评测线

| 评测线 | 输入 | 能证明的内容 | 不能据此证明 |
| --- | --- | --- | --- |
| 公开文本检测 | FinED-Bench 的原文，无外部财报 | 单文档错误检测、类型与原文定位，场景/长度泛化 | PDF 解析、外部证据正确性、全部正确文本的误报率 |
| 配对业务核查 | 研报与对应财报/公告、人工标准答案 | 抽取、数值比对、证据与定位、三态结论、复核流程 | 公开文本全部 15 类错误均已覆盖 |

保留已有四组 E 样本作为开发回归，不将看过的答案重新命名为盲测。标准公开集均为含错文档，必须另补人工核验的正确文本和容易误判的反例。待审核材料不能自动获得“正确”标签。

`prepare_review_pack.py` 已生成 16 条正常候选（10 条公开 SFT 研报原文＋6 条合成难负例），全部等待人工签核；`approve-import` 验证审核人、签核时间和内容哈希后才产生独立 `negative_review` 输入/答案，不与公开盲测混报。命令见实施说明。

## 数据审计与隔离

`dataset_prepare.prepare_dataset()` 是第二版数据准备入口。固定种子 `20261003`，基于源文件哈希和行号生成稳定 ID，通过规范化文本、标题及可识别标题公司标识聚合不可拆分组，再按场景和长度分层分配。

| 范围 | 开发集 | 10 月 5 日评测集 | 10 月 7 日保留集 | 总计 |
| --- | ---: | ---: | ---: | ---: |
| 全部文档 | 598 | 200 | 199 | 997 |
| 个股/行业研报 | 265 | 89 | 88 | 442 |

源文件含标准集 973 篇/4,095 错误和长文集 24 篇/83 错误。第二版严格审计后，4,178 个错误中 **4,093 个可评分，85 个保留排除记录**。旧版本“模糊重新锚定可完全还原”的说法不再用作金标准：只接受有效原偏移、唯一精确匹配或唯一空白归一化匹配；歧义、缺失、异常类型等公开记录。排除数不能悄悄从报告中消失。

`inputs.*.jsonl` 与 `gold.*.jsonl` 分开；`run` 拒绝答案字段，且不打开 gold 文件。`examples.dev.json` 仅来自开发集完整短文，目标文档不能同时作为示例。未知公司不从正文提及对象臆测，因此分组机制不能证明全部公司已隔离；公开留出也不能排除预训练污染。

## 运行

**当前执行范围（用户最新指令）：只完成 200 篇后上传进度 PR，并停止等待进一步指令。** 本机 `data/v2/run_control.json` 阻止 `full-dev` 与 `full-holdout_oct07` 输出批次，检查发生在模型客户端和账本初始化之前。不要通过换输出目录绕过用户暂停范围；后续须由用户明确恢复。原来的 798 篇汇总仍保留为未来工具，不作为当前完成标准。

当前 200 篇队列已结束，模型调用暂停；不启动新的模型进程或独立批次。交接时不重跑本批、不补发失败请求、不另开付费 10 篇试跑；以下评分、汇总与导出均为本地处理。

当前批次完成后的只读汇总命令为：

```powershell
.venv\Scripts\python.exe evals/finalize_full_acceptance.py --scope eval200
.venv\Scripts\python.exe evals/export_progress_snapshot.py
```

汇总命令生成 [200 篇交接报告](../docs/V2_EVAL200_RESULTS.md) 与 `data/v2/eval200-finalization-status.json`，保留完整 200 篇分母，明确 598 篇未运行。退出码 0 表示本批全部执行及评分完成；退出码 1 表示报告已生成但存在执行失败，状态为 `reports_ready_with_execution_failures`；退出码 2 表示缺少评分或完整性异常，不能发布为已处理完毕。`scope_execution_and_scoring_complete` 只在本批所有组成功完成时为 true，`full_model_execution_and_scoring_complete` 仍为 false。运行途中用户将累计额度从 50 元提高到 100 元，原运行配置中的 50 元保留为启动快照；共享账本授权及 `release/budget-amendment-100.json` 记录后续变更，不修改本批历史预测。

汇总完成后，`evals/export_progress_snapshot.py` 生成可提交的 [公开汇总计数](../docs/validation/v2-eval200/aggregate.json)。它要求每组均已尝试 200 篇、没有缺失预测，且评分哈希和汇总状态一致；允许发布明确标记了执行失败的阶段结果。尝试数、成功完成数和失败数分别保留，例如 200 篇已尝试、199 篇完成、1 篇失败不能写成 200 篇全部成功，失败文档仍在评分分母中。导出仅包含汇总计数、分层、配置版本、耗时及费用，不包含逐文档原文、答案或提示。该文件供 PR 查看；本地原始产物仍是逐条复核依据，哈希不能替代未提供的数据。

新环境可参考 [无密钥配置示例](model_config.example.json) 创建本机的 `data/v2/local_model_config.json`；不要覆盖已填写的配置。示例默认预算 20 元，价格是 2026-10-03 核验的周末快照，过期后必须重新核验。若接手同一账户和同一累计预算，应由原执行者提供原账本与授权记录，不能新建账本清零费用；密钥通过私下安全方式配置，不提交 Git。

**接手当前同一账本时保留已授权的 `YJCHECK_BUDGET_CNY=100`，不要照抄示例中的默认 20。** 普通打开账本会采用较低上限，错误地配置 20 会将原 100 元上限下调，历史费用不会消失；重放旧授权 ID 不能自动恢复较高上限。保留 100 元配置不等于恢复调用，当前停止要求继续有效。

从仓库根目录执行，完整配置与预算说明见 [第二版复现说明](../docs/V2_IMPLEMENTATION.md)。

```powershell
New-Item -ItemType Directory -Force data/v2/baseline | Out-Null
git archive d71f8c7 --format=zip -o data/v2/baseline/source-d71f8c7.zip
.venv\Scripts\python.exe evals/dataset_prepare.py --data "../Are Large Language Models Reliable Reviewers A Benchmark for Error Detection in Financial Documents/测评集/FinED-Bench-main" --out data/v2/dataset
.venv\Scripts\python.exe evals/run_v2.py run --inputs data/v2/dataset/inputs.dev.jsonl --examples data/v2/dataset/examples.dev.json --mode offline --max-documents 10 --out data/v2/runs/dev-offline-10
.venv\Scripts\python.exe evals/run_v2.py score --inputs data/v2/dataset/inputs.dev.jsonl --gold data/v2/dataset/gold.dev.jsonl --runs data/v2/runs/dev-offline-10 --out data/v2/runs/dev-offline-10/score.json
```

真实模型小样本迭代使用的 `examples.dev.v2.json` 可通过下列独立步骤复现；随后将运行命令的 `--examples` 指向该文件。仓库中的 `dev_example_reason_patch.v2.json` 只保存 3 篇已选开发示例的 9 条简短理由、文档 ID 和结构哈希，不保存额外答案或保留集内容。

```powershell
.venv\Scripts\python.exe evals/curate_dev_examples.py --examples data/v2/dataset/examples.dev.json --dev-inputs data/v2/dataset/inputs.dev.jsonl --out data/v2/dataset/examples.dev.v2.json --audit data/v2/dataset/examples.dev.v2.audit.json
```

此步骤只读取开发集输入、原示例和明确指定的理由补丁，不读取任何 gold、10 月 5 日评测集或 10 月 7 日保留集。它逐一核验开发集成员及正文，并用结构哈希锁定 ID、顺序、正文、类型、偏移及其他元数据；只允许替换 `reason`，数据或种子改变后不再匹配时会拒绝套用补丁。输出审计包含输入、补丁、输出的 SHA-256 和版本号。理由是 AI 辅助的开发提示编写，不是人工金标准审核；`complete_annotation` 仅表示保留了原示例的全部标注，不保证公开数据穷尽所有真实问题。运行器仍须排除正在测试的同一文档/正文，示例修改须使用新的运行输出目录。

只有未来用户明确恢复后，才可在重新核验模型、价格、上下文及授权范围后，将 `--mode offline` 换为 `--mode model`，使用 `examples.dev.v2.json` 和新输出目录试跑。当前不得执行该付费步骤。模型模式依次运行冻结旧规则、同一模型直接检测、组合流程，每篇各组完成后才进入下一篇。离线仅有旧规则与组合流程的规则部分，不生成伪模型分数。

默认归档为 `data/v2/baseline/source-d71f8c7.zip`，可由 `--baseline-archive` 显式指定；冻结基线在独立命名空间载入，不能偷偷回退成当前规则。代码/配置指纹变化时须换输出目录。同一配置可扩大 `--max-documents` 续跑，0 代表全部。用户已明确授权累计 100 元，原账本已通过 `evals/authorize_budget.py` 按 20→50→100 元迁移并保留授权审计；此前 83 次调用的高峰保守费用 4.662582 元仍在账本内。默认预算仍为 20 元，可降低；最高 100 元需显式授权迁移，不能删账本、换目录或仅改配置重置费用或提高上限。

当前后续调用配置为 `deepseek-flash`、思考 `enabled/low`、最大输出 65,536 token、超时 480 秒；按官方周末费率输入 1 元、输出 4 元/百万 token，价格有效至 `2026-10-04T16:00:00Z`（北京时间 10 月 5 日 00:00）。每次请求和重试前检查 `YJCHECK_PRICE_VALID_UNTIL`，过期停止，待重新核验费率。不同历史轮次仍按各自配置及原价记账。

仅修改规则或解析后处理时，可通过 `--reuse-model-responses-from` 复用严格兼容的原始成功回复；提示、模型、请求、单价及账本身份必须匹配；仅累计花费上限可随明确授权改变，复用来源记录原/新上限，不能复制历史预测冒充新结果。当前 20 篇及完成长文 2 篇的修复验收均为 0 次新模型调用。窄范围 JSON 恢复只插入唯一缺失的末尾对象 `}`，保留原始回复并记录双哈希、位置和规则；截断响应不恢复。规则层保留段落/表格结构及原文全局偏移；复杂或不能完整解释的枚举列表直接弃权。

## 评分口径

`score_paper_detection()` 实现的是**依据论文描述的项目评分口径，不是作者官方评分程序**：

- 以错误实例为单位，预测原句与标准原句匹配或包含，类型一致才计为正确；多片段错误的全部片段需满足定位条件。
- 使用确定性的最大一对一匹配；预测顺序不影响结果，重复预测不能重复命中同一个标准错误。
- 未产出任何预测的标准文档仍进入分母并贡献漏检；缺失结果与未完成执行单列。
- 类型准确率先按不考虑类型的定位匹配独立计算，避免类型严格匹配后“准确率必然为 100%”。
- 不可评分标注保留在审计计数；无效预测锚点不获得定位得分。旧 `score_detection()` 的重叠口径仅作定位诊断，不作为论文主评分。

`score` 以 `all_review_hints_detection` 披露全部提示 P/R/F1，拒绝定位的提示仍计入未命中预测，直接模型中已输出的无效候选不重复计数；另列已接受输出、确定性确认及场景/长度/类型分层、状态和复核负担。API/服务校验失败、响应解析失败、执行失败或缺失文档分开统计，主报告仍保留完整计划队列分母。缓存回放墙钟与补回原调用耗时的端到端估算分列，不能用回放时间宣称组合更快。

`coverage.execution_complete` 表示请求、解析、全文范围及跨段任务执行完毕；`candidate_quality_complete` 另记类型与原文锚点是否全部有效，`coverage.complete` 要求两者同时成立，均不是识别准确率；离线 hybrid 的模型部分未运行，会公开标记不完整。规则处理完成、模型处理完成和跨段补查完成应分别看待。

## 项目验收指标

| 指标 | 口径/边界 |
| --- | --- |
| 检测能力 | 候选 P/R/F1 与已确认 P/R/F1 分开；按场景、15 类和长度报告 |
| 抽取覆盖 | 正确抽取的唯一声明 / 人工标注的应核查声明，不能用告警条数代替 |
| 自动核查覆盖 | 有明确自动结论的标准声明 / 应核查声明，并列结论正确率 |
| 不确定性 | 可自动判断却转人工、证据不足却强判分别统计，需任务级标准答案 |
| 证据支持 | 定位正确率和语义支持率分别审核；公开字符锚点不替代外部证据 |
| 复核负担 | 候选数、每条及每篇实际复核耗时；未计时不能当 0 秒 |
| 可靠性与速度 | 计划/尝试/完成文档数，失败原因，耗时 P50/P95；重复一致性需另行复跑 |
| 成本 | 调用及重试的 token、价格来源/日期、费用、共享账本保守预留；API 费与人工成本分列 |

截至本次更新，20 篇新开发样本缓存重放为 20/20 完成：直接模型 TP39/FP51/FN37、F1 46.99%，组合 TP39/FP53/FN37、F1 46.43%（全部提示口径）。JSON 语法恢复增加 9 条 FP，不能称作检测能力提升。完成长文 2 篇的规则额外 FP 清零，直接与组合均为 TP2/FP7/FN2；119,714 字符最长文档另用 65,536 输出上限重试，现已完成，无接口/解析失败，输出 28,164 token、保守费用 0.189605 元，但两组均 TP0/FP5/FN4。两批长文的输出预算不同，须分开呈现，不能用执行成功代替效果达标。冻结前 B/C 293 项（无跳过）、评测 61 项、前端 15 项通过，工程通路可以进入全量测量。当前 200 篇已处理，进度 PR #2 已创建，模型调用暂停；598 篇后续批次已在创建模型客户端之前阻止，199 篇保留集继续封存。最终效果需引用对应配置和完整队列，人工负例、语义证据及复核效率仍待验证。

## 旧工具的定位

`fined_bench_eval.py --selftest` 保留基础评分自检；`operation_eval.py` 和 `contracts.py` 为原有 FinRiskAtlas 操作级分析工具，仅作可选补充。Ask/Proceed 的证据请求决策不能直接等同于人工复核效果。公开数据及其派生产物保持本地，引用论文与数据来源；许可状态以实际分发文件和发布方说明为准。

更多数据范围见 [datasets.md](datasets.md)。
# 未来 798 篇总验收与配对报告复现

以下命令仅说明未来用户明确恢复后的 798 篇总验收，不能作为当前 200 篇进度 PR 的完成门槛。运行中的 `status.json` 仅供进度显示；未来两批均处理结束后，可按实际逐文档结果核对 200 篇评测集、598 篇开发集、冻结配置和累计预算。缺失、重复、源码漂移或缩小评分分母均不能通过，199 篇保留集不进入该总验收范围。当前使用上文的 `--scope eval200` 汇总，不为满足未来门槛启动 598 篇或补发失败调用。

```powershell
.venv\Scripts\python.exe evals/audit_full_acceptance.py
.venv\Scripts\python.exe evals/audit_full_acceptance.py --require-complete
.venv\Scripts\python.exe evals/build_paired_report.py
.venv\Scripts\python.exe evals/finalize_full_acceptance.py
```

审计产物为 `data/v2/full-acceptance-audit.json`；默认允许生成运行中快照，`--require-complete` 在未完成时返回非零退出码。`full_model_execution_and_scoring_complete` 只说明全部计划文档执行及评分完整，不代表模型质量达到未约定的阈值，也不代替人审签核。报告来源指定的 E 配对四组见 [业务配对回归报告](../docs/V2_PAIRED_RESULTS.md)，旧回放目录不重复计入。人工正常样本、语义证据支持和真人效率保留为独立待验证项。

`finalize_full_acceptance.py` 要求两批评分均已生成，再核验完整性并生成 `docs/V2_FULL_RESULTS.md`、账本快照及带文件哈希的汇总状态；有执行失败时明确标记验收未通过。它不调用模型、不修改预算。在 Windows 上可加 `--wait-for-worker <现有全量进程PID>`，通过持有该进程的观察句柄等待结果；原任务退出却缺少评分时退出报错，不自动重启推理。默认每 30 秒观察一次，仅进度变化时输出。
