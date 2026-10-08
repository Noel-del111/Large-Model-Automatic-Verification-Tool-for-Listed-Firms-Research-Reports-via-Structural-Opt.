# 字段规范（SCHEMA）—— v2

所有成品数据均为 **JSONL**（UTF-8，每行一个 JSON 对象）；`.gz` 为 gzip 压缩的同一格式，
用 `gzip.open(path, "rt", encoding="utf-8")` 读取。长文本字段保留原始换行符 `\n`。

> **关于 `.gz`**：为控制仓库体积，**单文件超过 1.5 MB 会自动以 `.gz` 落盘**，
> 因此同一任务的 `inputs.*` 与 `gold.*` 可能一个带 `.gz`、一个不带，属正常。
> `scripts/scorers.py` 的 `_resolve()` 会自动补 `.gz` 再读；用 pandas 亦可直接读压缩包
> （`pd.read_json(path, lines=True)` 对 `.gz` 透明）。

## 0. 两条全局约定

1. **标签约定**：`has_error`(bool) 为唯一权威标签；`label` 固定 `1=有错 / 0=无错`；
   `label_text` 固定 `inconsistent` / `consistent`。三者必须一致（构建时断言）。
2. **输入/答案分离**：`data/inputs/*` 只放模型可见文本；`data/gold/*` 放标签与答案。
   两边以 `sample_id` 对齐，**不得**把 gold 字段拼进提示词。

---

## 1. FinVerBench 一致性判定

`data/inputs/finverbench_detection.jsonl.gz` + `data/gold/finverbench_detection.jsonl`（1985 条）

| 文件 | 字段 | 类型 | 说明 |
|---|---|---|---|
| inputs | `sample_id` | str | 中性 id，`fvb-det-0001` |
| inputs | `source` / `task` / `language` | str | `FinVerBench` / `financial_statement_consistency_detection` / `en` |
| inputs | `instruction` | str | 原文问句（通用指令，无泄漏） |
| inputs | `context` | str | **长文本输入**：格式化后的三张报表 |
| gold | `has_error` | bool | **权威标签** |
| gold | `label` / `label_text` | int / str | `1`/`inconsistent`（有错）、`0`/`consistent`（无错） |
| gold | `error_category` / `error_category_cn` | str | `AE`/`CL`/`YOY`/`MR`/`none` + 中文解释 |
| gold | `error_type` | str | 具体类型，见下 |
| gold | `error_location` | str | 被修改的科目路径，如 `income_statement.net_income` |
| gold | `error_magnitude_pct` | float\|null | 改动幅度（%） |
| gold | `original_value` / `modified_value` | float\|null | 正确值 / 注入值 |
| gold | `error_description` | str\|null | 错误的自然语言描述 |
| gold | `difficulty` | str | `baseline`/`easy` 等 |
| gold | `company` / `period` | str | 公司 / 期间（如 `FY2025`） |
| gold | `group_id` | str | 分组键 `finverbench::<公司>\|<期间>` |
| gold | `ambiguous_visible_text` | bool | **是否模糊样本**（可见文本双标签），共 162 条，评分时默认排除 |
| gold | `src_instance_id` | str | 上游原始 id（含 `__clean`/错误类型，**仅留档，勿入提示词**） |

**`error_type` 取值**：`AE_ROW_SUM`、`AE_COLUMN_SUM`、`CL_NET_INCOME_TO_RE`、
`CL_NET_INCOME_TO_CFS`、`CL_ENDING_CASH`、`YOY_OPENING_BALANCE`、`YOY_COMPUTED_CHANGE`、
`MR_MINOR`(0.5%)、`MR_MODERATE`(2%)、`MR_SIGNIFICANT`(10%)、`MR_EXTREME`(25%)。

---

## 2. FinVerBench 长文本纠错

`data/inputs/finverbench_correction.jsonl` + `data/gold/finverbench_correction.jsonl.gz`（**1822** 条）

| 文件 | 字段 | 说明 |
|---|---|---|
| inputs | `instruction` | 纠错指令 |
| inputs | `corrupted_text` | **输入**：含内部不一致的报表全文 |
| gold | `corrected_text` | **目标**：同一份报表的正确版本（保证与 `corrupted_text` 不相等） |
| gold | `error_type` / `error_location` / `original_value` / `modified_value` | 定位与数值监督 |
| gold | `group_id` / `src_instance_id` / `company` / `period` / `difficulty` | 同上 |

---

## 3. FinanceBench 证据锚定问答

`data/inputs/financebench_qa.jsonl` + `data/gold/financebench_qa.jsonl`（150 条）

| 文件 | 字段 | 说明 |
|---|---|---|
| inputs | `instruction` | 官方问题 |
| inputs | `evidence_text` | 金标证据原文 |
| inputs | `evidence_page_num` | 证据页码 |
| inputs | `context_full_page` | 证据所在整页全文 |
| gold | `answer` | 标准答案（常为金额/比例） |
| gold | `justification` | 官方给出的推理说明 |
| gold | `question_type` / `question_reasoning` | 题型 / 推理类型 |
| gold | `company` / `doc_name` / `doc_type` / `doc_period` / `gics_sector` | 文档元信息 |
| gold | `group_id` / `financebench_id` | 分组键 / 上游 id |

> ⚠️ 证据页已随输入给出，因此该任务测的是"**给定证据后的核验能力**"，**不能**用来证明全文检索能力。

---

## 4. FinanceBench 断言一致性核验

`data/inputs/financebench_claim_verification.jsonl` + `data/gold/…`（**204** 条 = 102 对正/反）

| 文件 | 字段 | 说明 |
|---|---|---|
| inputs | `instruction` / `evidence_text` | 问题 + 证据 |
| inputs | `claim` | **待核验断言**（由正确断言按 +8% 改写而来，或为正确断言本身） |
| gold | `has_error` / `label` / `label_text` | 断言是否与证据不符（`1`=不符） |
| gold | `correct_statement` | 正确的断言原文 |
| gold | `error_category` | 有错时为 `MR`，无错为 `none` |
| gold | `company` / `doc_name` / `group_id` / `financebench_id` | 同上 |

> 数量 204 = 102（有错）+ 102（无错），**类别均衡**，可直接算 FPR。

---

## 5. FinanceBench 数值断言纠错

`data/inputs/financebench_correction.jsonl` + `data/gold/financebench_correction.jsonl`（**102** 条）

| 文件 | 字段 | 说明 |
|---|---|---|
| inputs | `instruction` / `wrong_statement` | 问题 + **被改坏的断言** |
| gold | `correct_statement` | 正确断言 |
| gold | `edit` | `{"from": "…", "to": "…"}`，被改的数值 token |
| gold | `perturbation_rule` | `rule_based_factor=+0.08` |
| gold | `perturbation_direction` | `upward_only`（**只上偏 +8%，不是 ±8%**） |
| gold | `has_error` / `label` / `label_text` | 恒为 `True`/`1`/`inconsistent` |

---

## 6. FinBen 辅助子集（`data/finben/`）

统一字段：

| 字段 | 说明 |
|---|---|
| `sample_id` | `finben/<subset>/<split>/<orig_id>`，**全局唯一** |
| `source` / `subset` / `split` | `FinBen` / 子集名 / 官方划分 |
| `task` / `task_cn` | 任务标识（见下）+ 中文说明 |
| `instruction` / `input` | 模型可见输入（`input` 优先取 `text` 字段，否则取 `query`） |
| `gold_output` | **标准答案**，不得进入提示词 |
| `input_extra` | 上游附带的其他可见字段（如 `choices`/`token`/`label` 的原始形式） |
| `group_id` | 分组键（convfinqa 为 `finben::flare-convfinqa::<split>::<dialogue_id>`） |
| `dialogue_id` / `turn` | 仅 convfinqa 有 |

| 文件 | 条数 | `task` |
|---|---|---|
| `flare-convfinqa.train.jsonl.gz` | 8891 | `conversational_numeric_reasoning` |
| `flare-convfinqa.valid.jsonl.gz` | 2213 | 同上 |
| `flare-convfinqa.test.jsonl.gz` | 1490 | 同上（官方 test，评测专用） |
| `flare-tatqa.jsonl` | 1668 | `table_text_numeric_reasoning` |
| `finben-finer-ord.jsonl` | 1075 | `named_entity_recognition` |
| `flare-finred.jsonl` | 1068 | `relation_extraction` |
| `flare-fomc.jsonl` | 496 | `central_bank_stance_classification` |
| `flare-fnxl.jsonl` | 318 | `xbrl_numeric_tagging` |

> `finben-fomc` 与 `flare-fomc` 逐字重复，v2 **只保留 `flare-fomc`**。

---

## 7. 索引与划分

`data/index.jsonl.gz`（21,482 条）—— 全量索引，便于按任务/划分筛样本：

| 字段 | 说明 |
|---|---|
| `sample_id` / `source` / `task` / `group_id` | 标识与分组 |
| `has_error` / `label_text` / `error_category` | 标签（无监督任务为 `null`） |
| `ambiguous_visible_text` | 模糊样本标记 |
| `orig_split` | FinBen 的官方划分；其他来源为 `n/a` |
| `split` | **本目录划分结果**：`dev` / `test` |

`splits/dev.jsonl` / `splits/test.jsonl`：与 index 同结构，按划分切分。
`splits/groups.jsonl`：`{group_id, splits, n_samples}`，`splits` 长度恒为 1（同组不跨划分）。

---

## 8. 审计文件

`audit/quarantine_candidates.jsonl`（625 条）—— 每条被排除样本及原因：

| `reason` | 条数 | 含义 |
|---|---|---|
| `finverbench_correction_identical_text` | 120 | v1 中错误文本与正确文本完全相同的纠错对 |
| `financebench_nonmonetary_edit_v1` | 9 | v1 误改年份/日期/财号/机型的扰动 |
| `finben_duplicate_subset` | 496 | 与保留副本逐字重复的 FOMC |

其它：`audit/source_hashes.json`（上游 175 个文件的 sha256）、
`audit/audit_summary.json`（构建汇总）、`MANIFEST.json`（产物哈希 + 上游版本锚点）。
