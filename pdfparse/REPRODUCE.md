# 离线复现步骤

本模块的设计前提是**全本地运行**：核心链路不访问网络，不调用任何外部接口。

## 1. 环境要求

| 项目 | 要求 |
| --- | --- |
| 操作系统 | Windows / Linux / macOS |
| Python | 3.10 及以上（开发与验证使用 3.13.12） |
| 依赖 | 仅 PyMuPDF；对照引擎可选安装 pdfplumber |
| GPU | 不需要 |
| 网络 | 安装依赖时需要，运行与验证时不需要 |

## 2. 安装

```bat
cd pdfparse
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
:: 可选：安装对照引擎，启用双引擎交叉校验
.venv\Scripts\python.exe -m pip install -r requirements-engines.txt
:: 可选：安装 OCR 兜底，处理扫描件与位图图表
.venv\Scripts\python.exe -m pip install rapidocr==3.9.2 onnxruntime==1.30.0
:: 可选：安装可视化页面
.venv\Scripts\python.exe -m pip install streamlit==1.64.0
```

OCR 模型随 wheel 一起安装，位于 `site-packages/rapidocr/models/`，首次运行不会联网下载。

依赖版本固定见 `requirements.txt`，运行清单中的 `dependency_versions` 会记录实际版本。

## 3. 一条命令跑通

```bat
run.cmd parse --input data\raw --out data\out
```

或指定解释器：

```bat
set PYTHONPATH=src
.venv\Scripts\python.exe -m yjparse parse --input data\raw --out data\out
```

## 4. 断网验证（对应封闭环境要求）

1. 断开网络连接（或关闭无线网卡）。
2. 重复第 3 步命令，确认仍能完整产出。
3. 检查 `data/out/logs/<run_id>/tools.jsonl`，确认没有外部接口调用记录。
4. 运行清单中的 `offline` 字段固定为 `true`，用于自证。

## 5. 复现校验

```bat
run.cmd verify --out data\out
```

校验逻辑：读取 `logs/<run_id>/manifest.json` 中记录的产物哈希，重新计算并逐项比对。
全部一致时退出码为 0，任何一项不一致时退出码为 1，可直接作为评审脚本的判定依据。

## 6. 回归测试

```bat
set PYTHONPATH=src
py -3 -m unittest discover -s tests -v
```

测试用例覆盖：坐标契约校验、页码与坐标完整性、表格抽取、产物与哈希校验、
扫描件与空白页识别、双栏阅读顺序、坐标收拢、预览渲染，以及安装了 pdfplumber 时的双引擎交叉校验。
合成 PDF 由 `tests/sample_pdfs.py` 现场生成，不需要外部数据文件。

## 6.1 真实研报试跑

公开研报可从东方财富研报接口按需下载，放入 `data/real`（该目录不纳入版本控制）：

```bat
set PYTHONPATH=src
.venv\Scripts\python.exe -m yjparse parse --input data\real --out data\out_real
.venv\Scripts\python.exe -m yjparse preview --out data\out_real --doc "<文档名>" --pages 1,2
```

试跑结果与问题定位见 `docs/real_report_test.md`。

## 7. 生成自测样本

```bat
set PYTHONPATH=src
py -3 -c "from pathlib import Path; import sys; sys.path.insert(0,'tests'); from sample_pdfs import make_all; print(make_all(Path('data/raw')))"
```

## 8. 接入真实研报后的检查清单

- [ ] 把真实研报放入 `data/raw`，确认文件本身可以公开使用或已获得授权
- [ ] 运行 `run.cmd parse`，检查 `quality_summary.csv` 中的失败页比例是否可接受
- [ ] 抽取 3 到 5 个失败页，人工核对判定原因是否合理，必要时调整 `configs/thresholds.json`
- [ ] 记录本次运行的 `run_id` 与产物哈希，作为交付版本
- [ ] 更新 `THIRD_PARTY.md` 中实际使用的引擎与版本

## 9. 待接入项

| 项目 | 当前状态 | 接入方式 |
| --- | --- | --- |
| Docling 主引擎 | 适配器已写好，未做端到端验证 | `pip install docling` 后按 README 切换 |
| MinerU 主引擎 | 适配器读取离线产物，未做端到端验证 | 离线运行 MinerU 后指定 `--mineru-output` |
| 本地视觉模型抽检 | 阈值与接口已预留 | 接入本地视觉模型，按 `vlm_sample_ratio` 抽样 |
| 无框表格默认展开 | 需显式开启 `table_strategy=hybrid` | 默认给出提示，选定表格引擎后可改为默认开启 |
| OCR 引擎 | 未接入 | 扫描件判失败并提示，接入 PaddleOCR 或视觉模型后启用 |
| 视觉模型抽检 | 已接入，需要模型端点 | 配置 `YJPARSE_VLM_*` 环境变量或用 `tools/mock_model_server.py` 彩排 |

## 10. 提交前环境自检

```bat
:: 1) 解析链路自检（不依赖网络）
run.cmd doctor

:: 2) 模型端点自检（有网络时执行，检查 Embedding / 对话 / 视觉三类能力）
run.cmd model-check --base-url <OpenAI 兼容端点>/v1 --api-key <密钥> ^
  --embedding-model <向量模型> --chat-model <对话模型> --vision-model <视觉模型>

:: 3) 彩排：没有密钥时用本地 Mock 服务跑通全流程
py -3 tools\mock_model_server.py --port 8899
```
