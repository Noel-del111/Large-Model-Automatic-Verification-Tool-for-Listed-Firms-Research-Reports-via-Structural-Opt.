# R09：C 交付与验收记录

日期：2026-10-05。基线：`61725a2b69af5a1855f18db6d67d6bd1af38f1e8`；工作分支：`codex/r9-evidence-contract`。

**C 的 schema / 契约补齐已经完成；D 的页面与人工复核合并导出已于 2026-10-05 接入并增加自动回归。** 代码侧交付已补齐；真人页面截图和 C/D 双方最终联调签字仍待人工完成，因此尚不把整项 R09 标为已人工验收关闭。D 的当前状态见 `docs/validation/r09/README.md`。

## R09 要解决的具体问题

C 在基线中已经通过 `attach_evidence_requests` 为配对核查结果输出补证请求，但结果仍标为 1.0.0，公共 JSON Schema 未声明这些字段及关联约束。D 的详情页和自行生成的复核导出仍按旧字段消费，用户可能看不到应该补哪份材料、哪个期间以及为什么补。

本次把 C 输出固定为可检查的 1.1.0 契约，明确兼容旧版的规则，并提供 D 可直接使用的联调输入。补证任务由已有 Python 规则根据 finding、原声明及输入问题生成；本次没有新增大模型判定、自动检索或模型训练。

## C 已交付

| 内容 | 文件与说明 |
|---|---|
| 版本化 JSON Schema | `schemas/check_result.schema.json`：新 1.1.0 强制 decision、请求数组及两个汇总计数，定义 9 个请求字段、4 种请求类型和状态关联；继续接受 1.0.0 |
| 新结果版本号 | `src/yjcheck/models.py`：配对结果升级到 1.1.0；独立 `text-review/1.0` 不变 |
| 可读补证文本 | `src/yjcheck/evidence_requests.py`：CSV / Markdown 清单补全公司、期间、调整口径、合并范围及请求类型，继续保留原因和文件线索 |
| 兼容和输出回归 | `tests/test_evidence_requests_contract.py`：新增 14 项，覆盖真实三态输出、两期请求、历史形态、非法字段/状态、输入质量降级、导出转义和产物哈希 |
| 离线测试环境隔离 | `tests/test_boundaries.py`：仅修复 mock 测试夹具，避免读取本机真实模型价格配置与累计账本；生产价格门禁及其测试不变 |
| 联调样例与生成器 | `examples/r09/` 六份完整 JSON；`tools/export_contract_examples.py` 可离线复现，并生成 C 的 JSON / CSV / Markdown / manifest |
| D 交接说明 | [R09_D_INTEGRATION.md](R09_D_INTEGRATION.md)：明确入口函数、9 字段、两个计数、旧版提示、复核原件隔离以及 8 组验收案例 |

新契约的状态约束：`needs_review → ask + 非空请求`；`confirmed_error / no_issue → proceed + 空请求`。`proceed` 仅表示本次没有额外补证请求，不表示原文正确或已人工复核。

两个汇总数分别是“有请求的 finding 数”和“请求条目总数”。例如一条收入同比声明缺少 2024FY、2023FY 两期来源，计数为 **1 / 2**。JSON Schema 校验字段类型、必需性和状态关联；跨数组求和相等由生成流程与契约回归保证，不能声称 Schema 自身执行了求和校验。

历史兼容覆盖两种已知形态：旧结果完全没有补证字段；旧结果已有请求但 summary 只有 evidence_requests、没有 evidence_request_items。旧数据保持其原版本与字段缺失状态，不能补造 proceed 或把缺失统计当作零。

## 本次实测

以下命令从仓库根目录以 `.venv/Scripts/python.exe` 执行，不调用真实模型。每套均为 unittest discovery 全量执行，无失败、无错误、无跳过。

| 测试范围 | 命令参数 | 通过数 |
|---|---|---:|
| C 核查模块 | `-m unittest discover -s factcheck/tests -v` | 278 / 278 |
| B 解析模块 | `-m unittest discover -s pdfparse/tests -v` | 54 / 54 |
| 评测与评分工具 | `-m unittest discover -s evals/tests -v` | 119 / 119 |
| 既有前端回归 | `-m unittest discover -s frontend/tests -v` | 17 / 17 |
| 合计 | 分套执行上述四条命令 | **468 / 468** |

另对六份联调样例逐个验证：符合 schema；重新生成内容一致；C 四份产物的 manifest 校验成功；D 现有 `full_json_bytes` 追加人工复核后仍完整保留原结果。三态案例实际分别产生 no_issue、confirmed_error、needs_review；两期案例确为 1 个 finding / 2 条请求。

本机完整日志在仓库同级的 `tmp/r09-contract/`，文件为 `factcheck-full-tests.log`、`pdfparse-tests.log`、`evals-tests.log`、`frontend-tests.log` 和 `fixtures-verification.log`。测试日志不是随仓库自动分发的原始测评实验包；本页仅记录本次接口改动回归，不宣称对 442 篇重跑、提高准确率/召回率或补齐原模型响应。

## D 接入后的关闭条件

D 代码现已实现结果详情及复核区域的补证展示，补齐 CSV / Markdown / PDF 的复核合并导出，保留原 JSON 透传，并处理旧版缺失、长文本、两期请求和契约异常。自动检查和长清单 PDF 已交付；页面实机截图及 C、D 双方联调签字仍需人工补交，完成后 R09 才能标记为已人工验收关闭。
