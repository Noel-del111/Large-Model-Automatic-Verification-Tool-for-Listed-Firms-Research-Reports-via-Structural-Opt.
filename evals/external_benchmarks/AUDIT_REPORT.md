# v2 复检报告（AUDIT_REPORT）

- 报告日期：**2026-10-08**
- 对象：`evals/external_benchmarks/`（PR #5 的数据修订版）
- 上游评审问题来源：对外部候选数据包（v1）的复检意见
- 复检方式：**只读磁盘上的 v2 产物**，独立脚本断言，不依赖构建脚本内部状态
- 复检命令：`python3 scripts/verify_datasets.py` → **20 项检查全部 PASS，退出码 0**

> 本报告只做"核对与修复留痕"，不下模型效果结论。所有数字均可由上述脚本复跑得到。

---

## 0. 结论速览

| 评审问题 | 严重度 | v1 事实 | v2 处理 | 复检结果 |
|---|---|---|---|---|
| F1 标签方向相反 | 高（会训反） | FinanceBench `0=有错`，FinVerBench `1=有错` | 统一 `has_error`，`label` 固定 `1=有错` | PASS：0 违例 |
| F2 120 条无效纠错对 | 高（脏样本） | `corrupted==corrected` 120 对；42 组同正文双标签 | 隔离 120 对 + 162 条检测样本打模糊标记 | PASS：pairs 1822，相同 0 |
| F3 误改年份 | 高（错样本） | ≥3 条年份误改，实为 **9** 条非金额误改 | 严格匹配器 + 排除年份/日期/财号 | PASS：0 违例 |
| F4 ConvFinQA 划分与 ID | 高（错配） | 3 划分混装，12,594 行仅 8,891 唯一 id | 按划分分文件，id 全局唯一 | PASS：12,594/12,594 |
| E1 FOMC 重复 | 中（重复计数） | 两份 496 条逐字相同 | 只留 1 份计分副本 | PASS：1 个文件 |
| E2 ID 泄漏 | 中（答案泄漏） | `__clean` 43、`_wrong/_right` 各 111、含 error_type | 中性 id，原始 id 移入 gold/audit | PASS：0 违例 |

---

## 1. [F1] 统一文件数字标签含义相反

**v1 现象**（复现：按 `source` 分组统计 `label` 与 `label_text`）

```
FinVerBench : label=0 -> consistent    43 条
              label=1 -> inconsistent 1942 条
FinanceBench: label=0 -> inconsistent   111 条     ← 方向相反
              label=1 -> consistent     111 条
```

同一套数字标签下，222 条 FinanceBench 样本的含义与 FinVerBench 完全相反。

**v2 处理**
- 以 `has_error`(bool) 作为唯一权威标签；
- `label` 统一为 `1=has_error / 0=no_error`；
- 构建时对每一条调用 `check_label()`，断言 `has_error / label / label_text` 三者一致，
  不一致直接 `AssertionError` 中断构建（不再可能生成方向相反的标签）。

**复检**：`FinVerBench 检测 gold: label/has_error/label_text 一致 → n=1985 违例=0`；
`FinanceBench 断言核验 → n=204 违例=0`。

---

## 2. [F2] FinVerBench 120 条"错误文本 == 正确文本"

**v1 现象**
- 1942 对纠错样本中，**120 对**的 `corrupted_text` 与 `corrected_text` 完全相同；
- 检测文件里 **42 组**同一 `context` 同时带 `0` 和 `1` 两种标签，受影响 **162 条**（42 clean + 120 注错）；
- 根因：上游把错误注入在 `formatted_statements` 未呈现的结构化字段中（上游论文承认该局限）。

**v2 处理**
- 构建时对每对做字面比较，相同则**不产出纠错对**，同时写入
  `audit/quarantine_candidates.jsonl`（`reason=finverbench_correction_identical_text`）；
- 对可见文本存在"双标签"的检测样本，置 `ambiguous_visible_text=true`（162 条），
  评分时默认排除（`score_detection(exclude_ambiguous=True)`）。

**复检**
- `不再存在 corrupted==corrected 的纠错对 → n_pairs=1822 相同=0`
- `被隔离的相同对已留痕 → 120`
- `对应检测样本已打模糊标记 → 162`

**残余风险（务必知悉）**：其余 1822 对**只通过了这一项检查**，不等于全部合格；
且检测集中干净样本仅 43 条，误报率估计方差大。

---

## 3. [F3] FinanceBench 数值扰动误改非金额数值

**v1 现象**：`指令说明`写 ±8%，实际全部为 **+8%**；且数值正则把**尾部标点逗号**当成数字一部分，
`pick_perturbable` 只要求"含逗号或点"，于是以下非金额数值被改：

| v1 改了什么 | 原文语境 | 性质 |
|---|---|---|
| `2022,` → `2,184` | `In 2022, AMD reported...` | 年份 |
| `FY2022,` → `FY2,184` | `As of FY2022, Pepsico...` | 财年 |
| `737,` → `796` | `production rates for the 737, 777X and 787 aircrafts` | 机型编号 |
| `22,` → `24` | `For FY22, JnJ had changes...` | 财年号 |
| `28,` → `30`（3 条） | `...ended on January 28, 2023` | 日期 |
| `30,` → `32` | `...from August 30, 2023 onward` | 日期 |

共 **9 条**（评审原文称"至少 3 条"，实为 9 条）。

**v2 处理**
- 数值匹配改为 `(?:\d{1,3}(?:,\d{3})+|\d+|…)`：逗号**只有后接 3 位数字**才算数字的一部分；
- 候选必须"像金额/比例"：带 `$`、带 `%`、带小数、或合法千分位；裸整数一律不扰动；
- 显式排除：4 位年份、月份名邻近（日期）、前置 `FY`/`fiscal`、后接 `, 20xx`；
- 文档把 `±8%` 更正为 **`+8%`**，并在数据里写入 `perturbation_direction: "upward_only"`。

**复检**
- `所有扰动对象都是金额/比例（无裸整数）→ n_pairs=102 违例=0`
- `不再出现 2022, → 2,184 → 违例=0`
- `v1 误改项已留痕 → 9 条`（3 年份 + 1 机型 + 1 财号 + 4 日期）

**数量变化**：111 → **102**（差 9 条 = 被排除的误改项）。

---

## 4. [F4] ConvFinQA 混装划分 + ID 冲突

**v1 现象**
- 12,594 行混装 train/valid/test；
- `sample_id` 形如 `finben/flare-convfinqa/convfinqa0`，**id 每个划分各自从 0 重新编号**，
  因此 12,594 行只有 **8,891** 个唯一 id，**2,213** 个 id 对应不同输入 → 按 id 存结果会互相覆盖。

**v2 处理**
- 按官方划分**分文件落盘**：`flare-convfinqa.{train,valid,test}.jsonl.gz`；
- `sample_id` 改为 `finben/flare-convfinqa/<split>/<orig_id>`，全局唯一；
- 分组键也带上 `split`（因为 `dialogue_id` 同样是每划分各自编号，608 个对话跨划分重复）。

**复检**
- `按划分分文件 → files=[test, train, valid]`（3 个）
- `sample_id 全局唯一 → n=12594 unique=12594`
- `sample_id 含官方划分 → test 1490 / train 8891 / valid 2213`

---

## 5. [E1] FOMC 重复子集

**v1 现象**：`finben-fomc.jsonl` 与 `flare-fomc.jsonl` 各 496 条，input/output **逐字相同**。

**v2 处理**：按**内容签名**（而非文件哈希——两个 parquet 字节不同、内容相同）识别重复子集，
保留 `flare-fomc` 一份作为唯一计分副本，丢弃的 496 条写入 quarantine。

**复检**：`FOMC 只保留一份计分副本 → files=['flare-fomc.jsonl']`；
`被丢弃的重复子集已留痕 → 496`；上游 revision 也显示 `flare-fomc` 与 `finben-fomc`
指向**同一个 HF revision**（`e1f823e0…`），互为佐证。

---

## 6. [E2] sample_id 泄漏答案

**v1 现象**：`finverbench/apple_inc__clean`（43）、`...__AE_ROW_SUM_0.5pct_0`（错误类型进 id）、
`financebench_corr/financebench_id_03029_wrong` / `_right`（各 111）。

**v2 处理**：`sample_id` 改为中性顺序 id（`fvb-det-0001` / `fvb-corr-0001` / `fb-cv-0001A` …），
原始 id 放入 `gold/` 的 `src_instance_id` / `financebench_id` 字段与 `audit/`，**不进入 inputs**。

**复检**：`data/ 下 sample_id 不含答案泄漏标记 → 违例=[]`；
`data/inputs/* 不含标签/答案字段 → 违例=[]`。

---

## 7. [评审第 3 条] 按原始文档分组后再划分

- 分组规则：FinVerBench 按 `公司 + 期间`（43 组）；FinanceBench 按 `doc_name`（84 个文档）；
  ConvFinQA 按 `官方划分 + dialogue_id`。
- 同一份报表的 clean / 注错 / 纠错版本，以及同一条断言的"正/反"两版，**必然落在同一组**。
- 官方 test 划分**不做 dev 抽样**。

**复检**
- `dev / test 的文档组零重叠 → dev组=647 test组=2943 交=0`
- `split 索引样本数自洽 → dev=3242 test=18240`
- 各任务划分分布：

| 任务 | dev | test |
|---|---|---|
| financial_statement_consistency_detection | 464 | 1521 |
| financial_statement_correction | 429 | 1393 |
| claim_consistency_verification | 40 | 164 |
| evidence_grounded_financial_qa | 38 | 112 |
| numeric_claim_correction | 20 | 82 |
| finben/flare-convfinqa | 2251 | 10343 |
| 其余 FinBen（官方 test-only） | 0 | 4625 |

> 注意：FinVerBench 检测在 dev 中只有 **10 条**干净样本（test 中 33 条），
> 因此 dev 上的误报率基本不可用，**误报率必须在 test 上评估**。

---

## 8. [评审第 4/5 条] 输入与答案分离 + 任务级评分

- `data/inputs/*` 与 `data/gold/*` 按 `sample_id` 对齐；`scorers.to_prompt()` 是唯一入口，
  且 `verify_datasets.py` 断言 inputs 不含标签/答案字段。
- 任务级评分器（`scripts/scorers.py`）：
  `detection`（含 recall/precision/FPR/各错误类型召回）、`correction`、`numcorr`、`qa`、
  `token_f1`、`stance`、`num_em`。
- 小批联调（`scripts/smoke_test.py`）已用最笨基线跑通 **418 条**样本的
  输入→预测→评分 全链路，无字段缺失、无提示词泄漏。

**虚高基线（必须警惕）**：FinVerBench 检测集 1985 条中 1942 条为"有错"，
**全答"有错"准确率即 97.83%**。因此该任务的验收指标应以
`error_recall / false_positive_rate / 各错误类型召回` 为准。

---

## 9. 仍未解决 / 需要团队决策

1. **FinVerBench 上游局限无法绕过**：120 条已隔离，但剩余 1822 条的"可见信息是否充分"
   未逐条验证；建议抽样人工复核，或补全可见字段后重新验标。
2. **`financebench_correction` 的错误形态是规则扰动**（+8%，单数字），
   与真人错误（换行、口径混用、单位错、同比基期错）差异大；
   若要贴近业务，建议改由大模型改写并做一轮质量校验。
3. **中文样本缺失**：英文公开基准不能替代团队真实要交付的能力，
   仍需保留人工确认的中文正常研报、业务错误、证据不足样本。
4. **许可**：FinVerBench / FinanceBench 上游**未声明 SPDX 许可**，
   仅可用于学术研究，商用前需与作者确认（见 `LICENSE_NOTES.md`）。
