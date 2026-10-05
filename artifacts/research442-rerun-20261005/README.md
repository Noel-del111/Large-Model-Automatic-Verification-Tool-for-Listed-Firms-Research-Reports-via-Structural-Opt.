# R01：442 篇重跑公开交付包（2026-10-05）

用户已明确授权公开本批原文、金标、预测和模型响应。上传的是新推理批次，不是原 2026-10-04 批次的恢复产物，不证明当前 main 提示/规则的效果。

## 验证结果

同一队列 442 个唯一 document_id；三组各 442 份预测；442 份原始响应的 trace.status 均为 ok。输入、实际 few-shot、冻结源代码和旧规则 ZIP 的 SHA-256 均与运行配置一致。冻结评分器离线复算与随运行保存的 score 完整 JSON 一致，无新模型调用。

| 全部提示口径 | TP | FP | FN |
|---|---:|---:|---:|
| legacy_rules | 52 | 14 | 1695 |
| model_direct | 1211 | 604 | 536 |
| hybrid | 1235 | 625 | 512 |

442 篇均保留在分母；可评分金标 1747，排除标注 45。人工根因、证据语义正确性、正常文本 FPR 和真人复核计时未完成，不因本包发布而标记通过。

## 文件与 R01 对照

- `inputs/inputs.research442.jsonl`、`run/queue.json`：同批输入及执行清单。
- `inputs/gold.research442.jsonl`：离线复算金标，保留 scorable 排除标记。
- `run/predictions/{legacy_rules,model_direct,hybrid}/`：1326 份逐篇预测。
- `run/model_responses/`：442 份原返回内容、调用 trace 和缓存请求指纹。
- `inputs/examples.dev.v2.json`：实际 few-shot 字节；`source/`：本次运行的冻结源码及提示实现。
- `baseline/source-d71f8c7.zip`：本次使用的冻结旧规则归档。
- `run/run_config.json`、`run/status.json`：参数、模型、推理强度、指纹及执行状态。
- `run/score.json`：本次重跑当时保存的原评分（不是已删除批次的旧 score）。仅脱敏账本本机路径。
- `manifest.json`：1880 个交付源文件的发布哈希、原始哈希、大小和脱敏标记。派生复算文件另由 `verification/manifest.json` 覆盖。
- `verification/score.json`、`verification/summary.json`：独立离线复算结果。
- `verification/cases.jsonl`：全部提示口径下每个 TP/FP/FN，包含文档 ID、预测文件、统一提示序号、原金标序号和对应对象。被拒绝提示须按统一 all_review_hints 转换后的序号定位，不能直接用它索引原 errors。

响应缓存仅保存原内容和 trace，不包含完整 HTTP 报文。输入、实际示例及冻结提示实现可追溯请求构造；不声称本包额外恢复了不存在的传输日志。费用账本不上传，status 中账本 calls=443 与主批 442 次的差异不应当作多一篇样本；smoke 目录已排除，账本不是供应商账单。

## 在另一台电脑复核

需要 Python 3.10+；以下 score/verify 路径不访问模型，无需密钥。于仓库根目录执行，输出目录必须尚不存在：

```powershell
python tools/verify_research442.py --package artifacts/research442-rerun-20261005 --out data/research442-verification
```

程序先验证全部源文件哈希，再调用包内冻结评分器，比较原 score，最后导出每个 TP/FP/FN 并核对总数。`source/` 是证据快照，不是主程序替换件；运行时不要将其覆盖到当前源码。

## 旧批次失件记录

旧批次位于已删除的 project3/data/model_runs/research442（中间含长仓库目录名）。用户确认 project3 已删除并清空回收站；此前检查未找到完整旧预测。旧 PDF 的 direct=1181/610/566、hybrid=1218/627/529 只能作为历史汇总，不能由新批次替代或反造逐条证据。状态：`blocked_missing_artifacts`。本次补齐的是新批次 R01 交付，旧批次复现仍未完成。

原 88 篇保留研报已在旧批和此次重跑曝光，不再属于未使用的独立测试集。未对其余非研报样本的曝光状态作推断。

## 发布安全边界

排除 local_model_config.json、API 密钥、SQLite 账本、smoke 运行和环境缓存。已检查已知配置密钥在交付文本中出现次数为 0，另进行了常见凭证模式扫描。run_config 和 score 的账本绝对路径被替换；原始文件哈希保留用于本地比对。业务原文和模型正文按用户授权保留，不对其内容正确性作人工签核。
