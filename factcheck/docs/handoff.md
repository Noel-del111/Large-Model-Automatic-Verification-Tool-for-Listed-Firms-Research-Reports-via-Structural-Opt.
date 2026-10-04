# C 核查模块交接说明

**2026-10-05 R09 更新：** 新配对结果契约为 `1.1.0`，schema 兼容旧 `1.0.0`；补证字段、状态关系、计数含义和旧结果处理见 [R09 补证契约与 D 接入说明](R09_D_INTEGRATION.md)，本次交付和 468 项回归见 [C 交付记录](R09_C_DELIVERY.md)。下文的 2026-09-28 测试数字为历史记录，不代表本次测试。本次不改变单文本 `text-review/1.0` 契约。

交付日期：2026-09-28。本次交付把 B 的解析结果接入业务核查：输入研报和对应财务材料，输出错误位置、原文、依据与修改建议。D 可以直接调用 Python 接口，或读取核查结果 JSON 展示给用户。

## 本次交付内容

| 内容 | 入口 | 接手人 |
| --- | --- | --- |
| 事实抽取、核查规则、建议生成和命令行 | [使用说明](../README.md)、`factcheck/src/yjcheck/` | C、D |
| 结果字段与定位约定，新输出 1.1.0，兼容旧 1.0.0 | [JSON Schema](../schemas/check_result.schema.json)、[给 D 的接口](../README.md#给-d-的接口) | A、D |
| B 接入问题修复及回归测试 | [修复说明](../../pdfparse/docs/C_HANDOFF_FIXES.md) | B |
| 测试结果、样本分歧与适用边界 | [验收记录](acceptance.md)、[原始标注审计](sample_annotation_audit.md) | A、E |
| 本轮测试逐项日志、依赖版本和评测摘要 | [验收归档](validation/2026-09-28/) | 全员 |

C 使用 B 的解析模块，因此请获取包含 `pdfparse/` 和 `factcheck/` 的完整仓库，不要只拷贝 C 目录。本次 B 修复与 C 新增功能分别提交，方便 B 核对解析行为和兼容性。

## 队友如何运行

使用 Python 3.12，在仓库根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r factcheck/requirements.txt
.\.venv\Scripts\python.exe factcheck/run.py check --report "研报.pdf" --source "财报.pdf" --out data/check
```

也支持 DOCX 研报和保留原 PDF 的 B 解析 JSON。每次运行生成 `check_result.json`、`findings.csv`、`report.md` 和 `manifest.json`。默认不需要模型密钥；启用可选模型时按使用说明设置环境变量。

D 接入时读取 `findings`，展示已确认错误、待人工确认、未发现问题三种状态，并保留原文、证据位置、建议和人工复核状态。`summary.complete` 不代表整篇研报全部正确，详细含义见接口说明。

## E 如何复现验收

原始研报、财报和 Excel 沿用 E 提供的文件，按团队约定不进入 Git 仓库。完整自动测试要求如下目录关系：

```text
工作目录/
├─ 团队仓库/
│  ├─ pdfparse/
│  └─ factcheck/
└─ E测试样本最新版/
   └─ E 原有的四组样本目录及文件（保留原名）
```

```powershell
.\.venv\Scripts\python.exe -m pip install -r factcheck/requirements-test.txt
.\.venv\Scripts\python.exe factcheck/tools/run_all_tests.py --out data/test-results
.\.venv\Scripts\python.exe factcheck/tools/evaluate_samples.py --samples "..\E测试样本最新版" --out data/c-acceptance
```

缺少依赖或原始 E 样本时，完整验收可能失败；不会把缺少样本算作跳过后通过。OCR 测试依赖 RapidOCR 对应运行环境与模型可用。

2026-09-28 本机验收为 **155 项通过：B 53 项、C 102 项，0 失败、0 错误、0 跳过**。这是已归档的本机运行结果，未声称 GitHub Actions 已执行。

E 的 20 行原标注包含 1 个重复：是否有错对应 20/20，错误分类对应 16/20，原来源页码对应 18/20。4 项分类分歧和 2 处标注页码问题均保留审计说明；额外 9 项未标注声明不计作已验收成绩。该结果是开发样本回归，不是独立盲测准确率。

## 后续联调

- **B**：审阅解析修复，确认文档与运行标识、页质量、表格和坐标处理可供下游使用。
- **A、D**：按新 schema 1.1.0 对齐结果页、补证清单、证据对照、人工确认和导出，兼容旧 1.0.0；字段调整由 A 协调。
- **E**：用原始文件复现，复核标注分歧，再组织未参与开发的独立盲测与全链路验收。
- **C**：跟进联调发现的抽取、核查和建议问题。

当前首版覆盖支持指标和可确认口径的文本型材料。没有进行付费云模型实测；扫描件全面核查、全网引用检索、复杂估值与投资判断不属于本次自动范围。面向最终用户的完整页面和人工确认流程需由 D 接入。
