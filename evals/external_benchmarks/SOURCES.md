# 数据来源、版本冻结、许可与下载通道（v2）

本目录数据全部来自三个公开基准。本文件记录**来源、固定版本、文件校验、许可、引用方式**，
以及本环境实测可用的**下载通道**（github.com / huggingface.co 主站直连受限）。

> **冻结原则**：`audit/source_hashes.json` 里记录的 **sha256 是唯一权威冻结依据**；
> 下表 revision 用于溯源，便于重新下载时对齐。

---

## 1. 版本锚点（2026-10-08 核对）

| 来源 | 仓库 / 数据集 | 固定 revision | revision 日期 |
|---|---|---|---|
| FinVerBench | `SiluPanda/finverification-bench` | `8aef2f48befdab5c57cc383a521711fe11c2df98` | 2026-03-17 |
| FinanceBench | `patronus-ai/financebench` | `cc39aeb4afdf33909ee1412188bf89035950c2eb` | 2024-12-03 |
| FinBen · finben-finer-ord | `TheFinAI/en-finer-ord` | `1a235081039192371efe56c4bfd340edd25144ea` | — |
| FinBen · flare-convfinqa | `TheFinAI/en-convfinqa` | `a24fb040ac27d8045e4afcdbf3e126299cc731bb` | — |
| FinBen · flare-fomc | `TheFinAI/en-fomc` | `e1f823e0e71556d0a2c1206b71310e564cae8dd4` | — |
| FinBen · finben-fomc | `TheFinAI/en-fomc` | `e1f823e0e71556d0a2c1206b71310e564cae8dd4` | 与 flare-fomc **同一 revision**（内容重复） |
| FinBen · flare-finred | `TheFinAI/en-finred` | `af34b2c8c3cc4bae61eee23cfaf0f0783eb800b2` | — |
| FinBen · flare-fnxl | `TheFinAI/en-fnxl` | `8bea408feb61295e0a31499e31d0291896d0d6ba` | — |
| FinBen · flare-tatqa | `TheFinAI/en-tatqa` | `1cf60f0c2c2c7ef6153b1842a5aa79509da568ba` | — |

> ⚠️ **命名注意**：FinBen 的 `flare-*` 子集在 HuggingFace 上实际挂载名是 **`en-*`**
> （`flare-convfinqa` → `en-convfinqa`）。`SOURCES.md` 中的 `flare-*` 是 FinBen 论文里的任务名。

---

## 2. FinVerBench

| 项 | 内容 |
|---|---|
| 全称 | FinVerBench: Evaluating Large Language Models on Financial Statement Verification |
| 仓库 | https://github.com/SiluPanda/finverification-bench |
| 数据规模 | **1,985** 个实例 = 43 条干净 + 1,942 条注入错误；覆盖 43 家标普 500 公司的 SEC 10-K XBRL 财报 |
| 关键文件 | `data/benchmark/benchmark.json`、`benchmark_stats.json`、`data/converted/*.json`、`paper/` |
| 许可 | **仓库无 SPDX 许可文件**（GitHub API `license=null`），按学术研究用途使用 |

**错误注入类型（本目录的"纠错样本模板"来源）**

| 类别 | 具体类型 | 含义 |
|---|---|---|
| AE | `AE_ROW_SUM` / `AE_COLUMN_SUM` | 行/列合计不等于各项之和 |
| CL | `CL_NET_INCOME_TO_RE` / `CL_NET_INCOME_TO_CFS` / `CL_ENDING_CASH` | 跨表勾稽不一致 |
| YOY | `YOY_OPENING_BALANCE` / `YOY_COMPUTED_CHANGE` | 同比期初期末接不上 |
| MR | `MR_MINOR`(0.5%) / `MR_MODERATE`(2%) / `MR_SIGNIFICANT`(10%) / `MR_EXTREME`(25%) | 量级扰动 |

**已知上游局限**：部分注入错误落在 `formatted_statements` 未呈现的结构化字段上，
导致约 120 条样本的可见文本与正确文本相同（上游论文亦承认）。本目录已隔离这批样本。

---

## 3. FinanceBench

| 项 | 内容 |
|---|---|
| 全称 | FinanceBench: A New Benchmark for Financial Question Answering |
| 仓库 | https://github.com/patronus-ai/financebench |
| 数据规模 | 开源样本 **150** 条问答（完整版 10,231 题）；配套文档元信息 |
| 数据文件 | `data/financebench_open_source.jsonl`（150 条，含 `evidence`）、`data/financebench_document_information.jsonl` |
| 许可 | **仓库无 SPDX 许可文件**（GitHub API `license=null`），README 声明供研究与评估使用 |

**本目录派生的两类样本**
- 断言核验 204 条：把答案数值按 **+8%** 上偏改写得到"错误断言"，与原断言配对（均衡）。
- 数值纠错 102 条：给出被改坏的断言，要求还原。

---

## 4. FinBen

| 项 | 内容 |
|---|---|
| 全称 | FinBen: A Holistic Financial Benchmark for Large Language Models |
| 组织 | https://huggingface.co/TheFinAI （原始项目：https://github.com/The-FinAI/FinBen） |
| 概览页 | https://ssawant.github.io/posts/FinBen/FinBen.html |
| 覆盖 | 23 类任务 / 35 个数据集 |

**本目录选取的子集**（按"文本分析 / 事实抽取"以及数值推理相关性挑选）

| 子集 | 条数 | 任务 | 上游许可（已核实） |
|---|---|---|---|
| `flare-fomc`（=`en-fomc`） | 496 | 央行立场分类 | `cc-by-nc-4.0` |
| `flare-tatqa`（=`en-tatqa`） | 1668 | 表格+文本数值推理 | `cc-by-4.0` |
| `flare-convfinqa`（=`en-convfinqa`） | 12594 | 多轮数值问答 | `mit` |
| `finben-finer-ord`（=`en-finer-ord`） | 1075 | 命名实体识别 | `cc-by-nc-4.0` |
| `flare-finred`（=`en-finred`） | 1068 | 关系抽取 | `other`（自定义） |
| `flare-fnxl`（=`en-fnxl`） | 318 | 财报数字/XBRL 标签 | `other`（自定义） |

> `finben-fomc` 与 `flare-fomc` 内容逐字相同（同一 HF revision），v2 只保留后者。

**受限 / 未包含的子集**（需自行申请授权，未纳入本目录）

| 子集 | 原因 | 替代来源 |
|---|---|---|
| `flare-finqa` | 403，需 HF 授权 | FinQA 原始仓库 `casmls/FinQA` |
| `flare-fpb` | 403，需 HF 授权 | Financial PhraseBank（Malofeeva 等） |
| `flare-fiqasa` | 403，需 HF 授权 | FIQASA 论文附页 |
| `flare-ectsum` | 403，需 HF 授权 | ECTSum 论文附页 |
| `flare-multifin-en` | 403，需 HF 授权 | MultiFin 论文附页 |

带 token 的下载示例（本环境需走镜像）：

```bash
export HF_ENDPOINT=https://hf-mirror.com
export HF_TOKEN=<你的 HuggingFace token>
python3 -c "
from huggingface_hub import snapshot_download
snapshot_download('TheFinAI/en-finqa', repo_type='dataset',
                  revision='main', local_dir='sources/FinBen/flare-finqa')
"
```

---

## 5. 本环境实测可用的下载通道

| 目标 | 直连 | 可用替代 |
|---|---|---|
| GitHub raw / release | ❌ | `https://cdn.jsdelivr.net/gh/<owner>/<repo>@<rev>/<path>`、`https://gh-proxy.com/https://github.com/...` |
| GitHub codeload（仓库 zip） | ⚠️ 不稳定 | `https://codeload.github.com/<owner>/<repo>/zip/refs/heads/main` 重试 |
| GitHub API | ✅ | 直接可用 |
| HuggingFace | ❌ | `https://hf-mirror.com`（hf CLI 设 `HF_ENDPOINT`） |

`scripts/download_all.py` 已封装上述镜像与重试逻辑，并支持按 revision 固定下载。

---

## 6. 引用

```bibtex
@misc{finverbench2026,
  title={FinVerBench: Evaluating Large Language Models on Financial Statement Verification},
  year={2026}, note={https://github.com/SiluPanda/finverification-bench}
}

@article{islam2023financebench,
  title={FinanceBench: A New Benchmark for Financial Question Answering},
  author={Islam, Pranab and Kannappan, Anand and Kiela, Douwe and others},
  year={2023}, eprint={2311.11944}, archivePrefix={arXiv}, primaryClass={cs.CL}
}

@inproceedings{xie2024finben,
  title={FinBen: A Holistic Financial Benchmark for Large Language Models},
  author={Qianqian Xie and Weiguang Han and Zhengyu Chen and others},
  year={2024}, eprint={2402.12659}, archivePrefix={arXiv}, primaryClass={cs.CL}
}
```

许可细节与逐来源核实结论见 `LICENSE_NOTES.md`。
