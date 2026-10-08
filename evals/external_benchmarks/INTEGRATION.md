# 接入说明（INTEGRATION）—— 给队友

本目录把 FinVerBench / FinanceBench / FinBen 三个公开基准整理成**外部专项评测集**。
本文说明：怎么接、会影响什么、怎么跑第一次、以及本次 PR 的内容。

---

## 1. 一句话：怎么接

```bash
cd evals/external_benchmarks
pip install -r requirements.txt

python3 scripts/verify_datasets.py   # 20 项数据复检，应全 PASS
python3 scripts/smoke_test.py        # 小批联调：输入→预测→评分 全链路
```

```python
import sys; sys.path.insert(0, "evals/external_benchmarks/scripts")
from scorers import load_task, evaluate

recs = load_task("finverbench_detection", split="test")   # 1521 条
preds = my_model(recs)                                     # 只喂 rec["input"]
print(evaluate("finverbench_detection", recs, preds))
```

---

## 2. 对现有代码/数据的影响

**完全追加式，不改动主仓任何既有内容**：

| 动作 | 说明 |
|---|---|
| 新增 `evals/external_benchmarks/` | 独立目录，自包含 |
| `.gitignore` 新增 3 行 | 忽略 `sources/`（原始下载缓存）、`__pycache__/`，并豁免 `evals/external_benchmarks/data/`（否则会被全局 `data/` 规则挡掉） |
| 现有 `data/v2/` 划分、hash、`eval_oct05` 命名 | **一行未动** |
| 主仓依赖 | 未新增；本目录自带 `requirements.txt`（仅 pandas/pyarrow，且只有 FinBen 的 parquet 解析需要） |

`evals/datasets.md` 顶部加了一行外部基准交叉引用（更新日期 2026-10-08），其余内容未动。

---

## 3. 与主线字段约定的对接

主线（`evals/dataset_prepare.py`）用 `doc_id` + `content`，答案单独放 `gold.*.jsonl`。
本目录**沿用同样的文件命名** `inputs.<split>.jsonl` / `gold.<split>.jsonl`，
只需经 `scripts/adapters.py` 转一次字段名：

```python
from adapters import to_project_inputs, to_project_gold

ins  = to_project_inputs("finverbench_detection", "test")   # [{"doc_id","content","meta"}]
gold = to_project_gold("finverbench_detection", "test")     # [{"doc_id","has_error",...}]
```

`CONTENT_FIELD` 定义了每个任务的"正文"取哪个字段（例如 `finverbench_detection` 取 `context`、
`financebench_claim_verification` 取 `evidence_text`）。

---

## 4. 建议的接入顺序（先小批，再正式）

1. **冻结构建**：`python3 scripts/build_datasets.py` 可幂等重建；产物的 sha256 在 `MANIFEST.json`。
2. **只读 dev**：用 `splits/dev.jsonl` 里的样本调通输入、输出、定位、计分四件事。
3. **接评分器**：`scorers.py` 已按任务给出指标；**不要**把不同任务合成一个"整体纠错准确率"。
4. **跑冻结的 test**：`splits/test.jsonl`，官方 test 划分（FinBen）只作评测。
5. **中文样本另建**：英文公开基准不能替代团队真实交付能力，仍需人工确认的中文研报样本。

---

## 5. 评测时必须注意的三条口径

1. **模型只能看 inputs**。`gold` 里有 `error_type` / `original_value` / `modified_value` /
   `corrected_text` / `src_instance_id`（含 `__clean`、错误类型），**任何一个进入提示词都算泄漏**。
   `verify_datasets.py` 会断言 inputs 不含这些字段。
2. **检测任务别只看准确率**。FinVerBench 1,985 条里 1,942 条"有错"，全答"有错"准确率 **97.83%**。
   必须同时报 `error_recall` / `error_precision` / `false_positive_rate` / 各 `error_type` 召回。
3. **`financebench_qa` 测的是"给定证据后的核验"**。证据页随输入给出，
   不能据此证明全文检索能力；全文检索要用不带证据的设定单独测。

---

## 6. 已知局限（务必知悉）

- FinVerBench 上游有缺陷：120 条"错误文本==正确文本"已隔离，但**其余 1822 条也只过了这一项检查**。
- 干净样本只有 43 条 → 误报率方差大，**须在 test 上评估**（dev 里只有 10 条干净样本）。
- `financebench_correction` 的错误是规则扰动（**+8% 上偏，单数字**），不代表真人错误形态。
- 检测/纠错/索引之间存在派生关系，**行数相加 ≠ 独立样本数**；FOMC 重复已去除。
- 5 个 FinBen 子集（finqa/fpb/fiqasa/ectsum/multifin-en）需授权，未纳入。
- 许可：两个 GitHub 来源**未声明 SPDX 许可**，FinBen 部分子集为 `cc-by-nc-4.0` →
  整体**仅限学术研究与非商业评测**，详见 `LICENSE_NOTES.md`。

---

## 7. 本次 PR 的内容

- 版本：**v2（2026-10-08 数据修订版）**，v1 的 4 个数据缺陷 + 2 个放大结果的问题已修复，
  证据见 `AUDIT_REPORT.md`。
- 新增文件：`evals/external_benchmarks/` 下
  `README.md`、`SCHEMA.md`、`SOURCES.md`、`LICENSE_NOTES.md`、`INTEGRATION.md`、`AUDIT_REPORT.md`、
  `MANIFEST.json`、`requirements.txt`、`scripts/`（6 个脚本）、`data/`、`splits/`、`audit/`。
- 现有文件改动：`.gitignore`（+3 行）、`evals/datasets.md`（+1 行交叉引用）。

**合并方式**：仓库 → Pull requests → 对应 PR → Squash and merge。
若 `data/` 下的大文件在 diff 里显示异常，属正常（JSONL 以"每行一条"存储，行数 = 样本数）。
