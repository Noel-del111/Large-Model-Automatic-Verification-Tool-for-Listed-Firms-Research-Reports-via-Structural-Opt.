# 许可说明（LICENSE_NOTES）—— v2，逐来源核实

> **核实方式**：2026-10-08 通过 GitHub API（`/repos/{owner}/{repo}`）与 HuggingFace API
> （`/api/datasets/{repo}` 的 `license:` 标签）**逐个查询上游**，不沿用二手转述。
> v1 的许可说明存在若干与上游不符之处，本版已按查询结果更正（见"v1 更正"）。

---

## 1. 核实结果总表

| 来源 / 子集 | 上游实际标识 | 上游 `license` 字段 | gated | 可商用 |
|---|---|---|---|---|
| FinVerBench | `SiluPanda/finverification-bench` | **无 SPDX 许可文件**（`null`） | — | ⚠️ 未声明，需联系作者 |
| FinanceBench | `patronus-ai/financebench` | **无 SPDX 许可文件**（`null`） | — | ⚠️ 未声明，需联系作者 |
| FinBen · finben-finer-ord | `TheFinAI/en-finer-ord` | `cc-by-nc-4.0` | false | ❌ NC 限制 |
| FinBen · flare-fomc | `TheFinAI/en-fomc` | `cc-by-nc-4.0` | false | ❌ NC 限制 |
| FinBen · finben-fomc | `TheFinAI/en-fomc` | `cc-by-nc-4.0` | false | ❌ NC 限制（且与上者重复） |
| FinBen · flare-convfinqa | `TheFinAI/en-convfinqa` | `mit` | auto | ✅ |
| FinBen · flare-tatqa | `TheFinAI/en-tatqa` | `cc-by-4.0` | auto | ✅（需署名） |
| FinBen · flare-finred | `TheFinAI/en-finred` | **`other`**（自定义） | auto | ⚠️ 需查自定义条款 |
| FinBen · flare-fnxl | `TheFinAI/en-fnxl` | **`other`**（自定义） | auto | ⚠️ 需查自定义条款 |

结论：**本目录整体只可用于学术研究与非商业评测**。其中 `cc-by-nc-4.0` 的四项
（finer-ord / fomc）明确禁止商业使用；`other` 两项需查上游自定义条款；
两个 GitHub 仓库未声明许可，商用前必须联系作者取得书面许可。

---

## 2. v1 更正记录

| v1 的写法 | 核实后的事实 | 处理 |
|---|---|---|
| "FinBen 的 `flare-*` 部分标注 MIT，部分未标注" | 实际各不相同：`en-convfinqa`=MIT、`en-tatqa`=CC-BY-4.0、`en-fomc`/`en-finer-ord`=CC-BY-NC-4.0、`en-finred`/`en-fnxl`=other | 逐个列出（本表） |
| 未区分 `finben-*` 与 `flare-*` 的许可差异 | 两者许可并不统一（如 `en-fomc` 为 NC，而同族的 `en-tatqa` 为 BY） | 按子集逐个标注 |
| 未提及 `flare-*` 实际挂载名为 `en-*` | 上游真实仓库名是 `en-*` | 在 `SOURCES.md` 与本表注明映射 |
| 未记录上游 revision | 各来源 revision 已取到 | 写入 `SOURCES.md` 与 `MANIFEST.json` |

---

## 3. 派生数据的许可约束

本目录中以下文件是**派生数据**，由 `scripts/build_datasets.py` 可复现生成：

- `financebench_claim_verification`（204 条）与 `financebench_correction`（102 条）：
  源自 FinanceBench，按 **+8%** 规则扰动生成 → 沿用 FinanceBench 的许可约束。
- `finverbench_correction`（1822 条）：源自 FinVerBench 的注错/干净对 → 沿用其约束。
- `index.jsonl.gz`、`splits/*`、`audit/*`、`MANIFEST.json`：本目录自建的索引与审计，
  不引入新的第三方权利，可与上述数据同条件分发。

`finben/*`、`financebench_qa`、`finverbench_detection` 为**格式转换**，语义与上游一致，
沿用上游许可。

---

## 4. 其它合规提示

- 上游 FinanceBench 的 PDF 原件（约 500 MB）**未纳入**本目录，仅保留证据原文文本。
- 五个需授权的 FinBen 子集**未纳入**（`flare-finqa`/`flare-fpb`/`flare-fiqasa`/`flare-ectsum`/`flare-multifin-en`）。
- 若后续需要对外发布或商用，请以本表为清单逐个与上游确认，并在仓库 `NOTICE` 中列出引用。
