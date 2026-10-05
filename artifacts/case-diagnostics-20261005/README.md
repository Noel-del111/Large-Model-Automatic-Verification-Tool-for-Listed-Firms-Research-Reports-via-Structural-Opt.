# 逐 case 深度诊断与典型性审计

从 [典型案例导航](../../docs/case-diagnostics/typical-case-guide.md)、[逐案索引](index.md) 或 [抽样覆盖审计](typicality.md) 开始。本包共 **71 条独立分析：59 条失败实例和 12 条 TP 对照，涉及 66 篇文档**。每条结论均有证据、已排除解释、未知项和最小验证办法，全部等待人工裁定。

## 范围与典型性

- 原始分层抽样保持不变：12 个有正例类型每类 2 FP＋2 FN，另法规引用错误、模糊语言各 2 FP，共 52 条失败实例（28 FP＋24 FN）；另附 12 条 TP 对照。
- 分层内尽量选不同文档，采用稳定 SHA-256 排序；有定位失败的 FP 分层优先保留一例。金融缺失 000368 FN、000334 TP、日历确认 000322 TP 固定入选。原 C001—C064 编号与身份不变。
- 对照本批全量 **625 FP＋512 FN＝1137 条失败记录**，追加 C065—C071 七条 FN：补齐原始候选为空的文档、全部 5 篇零分文档、行业与个股两种场景的 ≥4000 字失败例，以及原始引文在原文中有精确出现位置并触及金标、但最终提示无有效重叠的 FN 侧案例。最后一类此前已有相关 FP 侧案例，补样让 FN 本身也有独立结论。
- `supplemental_selection.json` 保存每项身份和理由；`typicality.json/md` 对比全量、原 52 条和扩展后 59 条，并将 12 个 TP 对照排除出失败分母。
- **典型性指诊断机制和边界的覆盖，不代表统计代表性。** 这是有目的的分层诊断抽样，不能用样本中的原因比例推算全量收益；一个业务问题也可能产生 FP/FN 两端。机械特征是筛查线索，不能代替人工业务判断。
- 本批最长 5023 字，≥4000 字样本不能代表真正长上下文压力场景。没有本轮 PDF/OCR、多文件关联或真实执行失败样本，不一致条款也无样本；这些能力仍需新增数据检验。

## 可追溯性与结论边界

使用已入库的 `research442-rerun-20261005` 新批次与其冻结源码，校验 1880 个源文件及派生 verification 清单。hybrid 全提示口径的 442 篇逐例评分复算为 **1235 TP／625 FP／512 FN**，与冻结结果一致；旧 PDF 报告批次尚未恢复，不能冒充其逐条复现。

442 个请求由冻结输入、示例、代码和配置离线重建，消息哈希同时匹配缓存和 trace，不读取 gold 来构造请求，不调用模型。**这是经过指纹核验的重建消息，不是原始 HTTP 留存。** 完整上游 `eval_data.json` 不在此 PR 中；提供 `--original-source` 才会重新核验它，未提供时页面明确标记 `not_available`，不能继承作者机器上的核验结论。

`FP` 表示未匹配预测，可能来自定位、类型、跨度、重复竞争或金标覆盖问题，不等同于人工认定的业务误报；`FN` 也不等于模型完全没有发现。TP 仅表示机械匹配成功，业务事实与理由仍须核查。原 88 篇研报 holdout 已曝光，本包不能当作独立泛化测试。

## 每一条怎样复核

1. 打开 `cases/Cxxx.md`，先读具体金标、正文与逐案问题，独立判断预期是否合理。
2. 沿文档中的链接检查冻结输入、金标、原始回复、最终候选和源码。GitHub 可直接阅读这些已入库文件。
3. 运行下方重建命令后，在本地 `requests/000xxx.json` 对照完整 system prompt、few-shot 与当前任务，在 `documents/000xxx.json` 查看全部候选和拒绝原因。
4. `cases/Cxxx.evidence.json` 的 `scoring_checks` 给出焦点实例与相关对象的逐对匹配检查。相关候选只是线索，不自动表示同一业务问题。
5. 核对独立结论、已排除解释、未知项和最小验证，将真人裁定写入本地 `human_review.jsonl`。

索引从 0 开始。`all_hints_index` 指冻结 `validate_predictions` 与 `all_review_hints` 转换后的统一数组，不能直接索引原始 `report.errors`；`gold_index` 指包括被排除项在内的原 gold 数组。证据 JSON 中的 `documents/`、`requests/` 指针在本地重建后可用。

## 入库材料与本地派生材料

| 材料 | 用途 |
|---|---|
| `selection.json`、`supplemental_selection.json` | 不变的实例身份、抽样规则与补样理由 |
| `typicality.json/md` | 全量与抽样覆盖对照、缺口和统计限制 |
| `index.md`、`cases/Cxxx.md` | 71 份可独立审阅的分析及源文件链接 |
| `cases/Cxxx.analysis.json` | 人工逐案复核前的 AI 分析、证据、未知与最小验证 |
| `cases/Cxxx.evidence.json` | 焦点预测或金标、相关对象、逐对评分检查 |
| `meeting.md` | 先逐案后归纳的会议顺序与优化准入 |
| `provenance.json`、`request_verification_summary.json` | 冻结版本、评分复算和请求重建摘要 |
| 本地 `index.html`、`documents/`、`requests/` | 离线交互页、完整文档证据、66 篇选中文档的重建消息 |
| 本地 `human_review.jsonl` | 真人分析人、复核人、最终结论、证据与实际耗时 |
| 本地 `manifest.json`、`validation.json` 等 | 完整性校验；不等于业务结论获得真人认可 |

派生的大文件和可变人工记录已由 `.gitignore` 排除，可以从已跟踪的冻结批次和分析材料生成，不重复提交文档、模型回复、完整请求、HTML 或压缩包。

## 离线复现

Python 3.10+，在仓库根目录运行；无需 API 密钥，不访问模型，目标目录必须尚不存在：

```powershell
python tools/rebuild_case_diagnostics.py --package artifacts/research442-rerun-20261005 --notes artifacts/case-diagnostics-20261005 --out data/case-diagnostics-review
```

命令自动应用补样清单，核对所有实例身份后复制分析，重建完整请求和证据，生成 Markdown、HTML、人工记录和哈希清单。可选 `--original-source <eval_data.json路径>` 用来额外核验上游数据文件。打开输出目录的 `index.html` 可离线搜索和筛选案例。

单独复算典型性审计可运行 `python tools/audit_case_typicality.py --package artifacts/research442-rerun-20261005 --pack data/case-diagnostics-review`。重新渲染本地材料时运行 `python tools/render_case_diagnostics.py <输出目录>`；已有人工记录保留，仅补充缺少的 case，页面状态按实际记录统计。

```powershell
python -m unittest discover -s evals/tests -p test_case_diagnostics.py -v
python -m unittest discover -s evals/tests -p test_reconstruct_research442_requests.py -v
python -m unittest discover -s evals/tests -p test_case_typicality.py -v
python -m unittest discover -s evals/tests -p test_case_pack_portability.py -v
```

不得将诊断输出写入冻结包，或用冻结源码覆盖当前生产目录。

## 人工记录与验收

分析人和复核人填写本人姓名；证据不足可保留 `unresolved`。只有真人完成裁定、填写最终结论与证据后才标记 `reviewed`，AI 不代签。实际 `review_minutes` 未测量时保持 null，不能用 0 冒充无成本；收益统计需去重同一业务问题的 FP/FN 两端及同文准备时间。

本轮交付是可复核的逐案材料、机制覆盖审计与最小验证办法。人工最终裁定、会议共识、实际复核耗时、优化对照实验仍需团队完成。业务流程、生产 prompt、评分和成本权重均未修改；OCR、RAG、插件或新增决策节点的优先级由案例证据和后续验证决定。
