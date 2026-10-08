# 外部金融一致性 / 纠错评测集（external_benchmarks）

面向"上市公司研报 / 财报**长文本自身矛盾**的检测与纠错"这一任务，把目前最接近的三个公开基准
（**FinVerBench / FinanceBench / FinBen**）下载、校验、统一整理成可直接接入评测的数据集。

原始基准大多是"金融长文本 → 问答"，需要二次加工才能用于一致性检测/纠错。本目录把它们
统一成一套字段规范，并额外派生出**检测样本**与**纠错样本**。

> **版本：v2（2026-10-08 数据修订版）**
> v1 曾作为外部候选数据包提交（PR #5 首个版本）。经外部评审复检，v1 存在 4 个必须修复的
> 数据缺陷 + 2 个会放大结果的问题，v2 已逐条修复，并在 `AUDIT_REPORT.md` 中给出复检证据。
> **用途定位：外部专项评测集**（已知局限见第 6 节），不建议不加区分地并入主训练集。

---

## 1. v1 → v2 修了什么

| 编号 | 问题 | v2 的处理 |
|---|---|---|
| F1 | 统一文件数字标签方向相反（FinVerBench `1=有错`，FinanceBench `0=有错`） | 以 `has_error`(bool) 为权威字段，`label` 固定 `1=有错/0=无错`，构建时逐条断言三者一致 |
| F2 | FinVerBench 有 **120** 条纠错对"错误文本 == 正确文本" | 隔离并留痕；对应检测样本打 `ambiguous_visible_text=true`（共 162 条） |
| F3 | FinanceBench 数值扰动误改年份/日期/财年号/机型编号 | 换严格匹配器（须带 `$`/`%`/小数/合法千分位，排除年份与日期语境），误改 0 条 |
| F4 | ConvFinQA 混装 train/valid/test，12,594 行仅 8,891 个唯一 id | 按划分分文件；`sample_id` 全局唯一（12,594/12,594） |
| E1 | 两份 FOMC 内容逐字相同，会重复计数 | 只保留 `flare-fomc` 一份计分副本 |
| E2 | `sample_id` 泄漏答案（`__clean` / `_wrong` / `_right` / 含 `error_type`） | 改为中性顺序 id，原始 id 只留在 `gold/` 与 `audit/` |

---

## 2. 目录结构

```
evals/external_benchmarks/
├── MANIFEST.json           # 产物哈希/行数 + 上游版本锚点（冻结依据）
├── AUDIT_REPORT.md         # v2 复检报告：每个问题的证据与修复前后对比
├── README.md / SCHEMA.md / SOURCES.md / LICENSE_NOTES.md / INTEGRATION.md
├── requirements.txt
├── data/
│   ├── finverbench_detection/        inputs.{dev,test}.jsonl.gz  gold.{dev,test}.jsonl
│   ├── finverbench_correction/       inputs.{dev,test}.jsonl     gold.{dev,test}.jsonl.gz
│   ├── financebench_qa/              inputs.{dev,test}.jsonl     gold.{dev,test}.jsonl
│   ├── financebench_claim_verification/  同上
│   ├── financebench_correction/      同上
│   ├── finben/                       # 辅助评测子集（input/gold_output 同记录内分离）
│   │   ├── flare-convfinqa.{train,valid,test}.jsonl.gz
│   │   ├── flare-fomc.jsonl / flare-finred.jsonl / flare-fnxl.jsonl
│   │   ├── flare-tatqa.jsonl / finben-finer-ord.jsonl
│   └── index.jsonl.gz                # 全量索引（sample_id/task/group_id/split/has_error）
├── splits/                 # 开发/评测划分（按原始文档分组，同组不跨划分）
│   ├── dev.jsonl  test.jsonl  groups.jsonl
├── audit/                  # 来源冻结与排除留痕
│   ├── source_hashes.json          # 上游每个原始文件的 sha256
│   ├── quarantine_candidates.jsonl # 每条被排除样本 + 原因（625 条）
│   └── audit_summary.json
└── scripts/
    ├── build_datasets.py   # 从 sources/ 重建全部产物（幂等）
    ├── verify_datasets.py  # 独立复检（20 项断言，全过退出码 0）
    ├── scorers.py          # 各任务输入装配 + 评分器
    ├── adapters.py         # 对接主线 doc_id/content 约定的适配层
    ├── smoke_test.py       # 小批联调：输入→预测→评分 全链路
    └── download_all.py     # 重新下载原始数据（支持固定 revisions）
```

> **文件命名对齐主线**：`inputs.<split>.jsonl` / `gold.<split>.jsonl` 与主线
> `evals/dataset_prepare.py` 的产物命名一致，接主线只需经 `scripts/adapters.py` 转一次字段。

`data/` 与 `scripts/` 下的数据均**可复现**：`python3 scripts/build_datasets.py` 从 `sources/`
重新生成，结果与 `MANIFEST.json` 中的 sha256 一致。

---

## 3. 数据规模（v2）

| 数据集 | 条数 | 任务 | 备注 |
|---|---|---|---|
| `finverbench_detection/` | **1985** | 一致性判定 | 43 家公司 43 个组；有错 1942 / 干净 43 |
| `finverbench_correction/` | **1822** | 长文本纠错 | v1 为 1942，剔除 120 条无效对 |
| `financebench_qa/` | **150** | 证据锚定问答 | 官方开源 QA，含证据页 |
| `financebench_claim_verification/` | **204** | 断言一致性核验 | 102 对"正/反"断言，类别均衡 |
| `financebench_correction/` | **102** | 数值断言纠错 | v1 为 111，剔除 9 条误改 |
| `finben/flare-convfinqa.*` | **12594** | 多轮数值推理 | train 8891 / valid 2213 / test 1490 |
| `finben/flare-tatqa` | **1668** | 表格数值推理 | 辅助评测 |
| `finben/finben-finer-ord` | **1075** | 命名实体识别 | 辅助评测 |
| `finben/flare-finred` | **1068** | 关系抽取 | 辅助评测 |
| `finben/flare-fnxl` | **318** | 财报数字/XBRL 标签 | 与数值一致性最相关 |
| `finben/flare-fomc` | **496** | 央行立场分类 | 单一计分副本 |

合计 **21,482** 条索引记录。注意：**行数相加 ≠ 独立样本数**（检测/纠错/统一视图之间存在派生关系，
FOMC 重复已去除）。

---

## 4. 快速开始

```bash
cd evals/external_benchmarks
pip install -r requirements.txt        # 只有 FinBen 的 parquet 解析需要 pandas/pyarrow

# 1) 复检数据（20 项断言）
python3 scripts/verify_datasets.py

# 2) 小批联调：输入→预测→评分 全链路
python3 scripts/smoke_test.py

# 3) 从 sources/ 重建（可选，幂等）
python3 scripts/build_datasets.py
```

```python
import sys; sys.path.insert(0, "scripts")
from scorers import load_task, evaluate, to_prompt

recs = load_task("finverbench_detection", split="test")
print(len(recs), sorted(to_prompt(recs[0])))        # 模型只应看到 input
preds = [True] * len(recs)                          # 最笨基线：全判"有错"
print(evaluate("finverbench_detection", recs, preds))
# -> accuracy 0.978 但 error_recall 1.0 / false_positive_rate 1.0  ← 准确率虚高，必须看召回/FPR
```

---

## 5. 评测约定（重要）

1. **模型只能看到 `data/<task>/inputs.<split>.jsonl*`**。`data/<task>/gold.<split>.jsonl*` 含标签、原始正确值、错误位置，**禁止进入提示词**。
   `scorers.to_prompt()` 是唯一入口；`scripts/verify_datasets.py` 会断言 inputs 不含标签字段。
2. **每个任务独立评分**，不合成"整体纠错准确率"——各任务输入/答案/指标不同，
   混在一个数字里没有意义（对应评审意见）。
3. **检测类必须报告** `error_recall` / `error_precision` / `false_positive_rate` +
   各 `error_type` 召回。FinVerBench 有 97.8% 的"有错"占比，全答"有错"就有 97.83% 准确率。
4. **划分**：`splits/dev.jsonl` 用于开发与联调，`splits/test.jsonl` 用于正式评测。
   划分按**原始文档分组**（FinVerBench 按公司+期间、FinanceBench 按文档、
   ConvFinQA 按"划分+对话"），同一份报表的 clean/注错/纠错版本永远在同一组。
   官方 test 划分**不做 dev 抽样**，仅作评测。
5. **`financebench_qa` 是"给定证据后的核验"**：证据页随输入给出，因此它测的是
   "已有证据后能否核实数值/结论"，**不能**用来证明全文检索能力。

---

## 6. 已知限制

- **FinVerBench 上游本身有瑕疵**：部分错误注入在 `formatted_statements` 未呈现的结构化字段里，
  导致 v1 出现 120 条"错误文本 == 正确文本"。v2 已隔离这 120 条，但**其余 1822 条也只通过了这一项检查**，
  上游论文亦承认该局限。
- **检测样本类别严重不均衡**（干净 43 / 有错 1942），干净样本仅 43 条，
  误报率估计方差大，建议同时报告置信区间，或只把干净样本作为"误报抽查"。
- **`financebench_correction` 由规则生成**：固定 **+8%**（上偏，非 ±8%）数值扰动，
  只改一个数字、其余逐字保留，因此错误定位精确但**不代表真人错误形态**。
- **FinBen 子任务与研报事实纠错目标距离较远**（信息抽取/立场分类），仅作辅助评测。
- **5 个 FinBen 子集未包含**：`flare-finqa` / `flare-fpb` / `flare-fiqasa` / `flare-ectsum` /
  `flare-multifin-en`，需自行申请授权，见 `SOURCES.md`。
- **未包含 PDF 原文**：FinanceBench 官方 `pdfs/` 约 500 MB，本目录未纳入；证据原文已在字段中。

---

## 7. 许可与引用

三个来源均为公开学术基准。**上游许可已逐个核实**（见 `LICENSE_NOTES.md`），使用时请遵守各自条款并引用原论文。
