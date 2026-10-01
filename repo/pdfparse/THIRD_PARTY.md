# 第三方组件登记表

按竞赛要求登记本模块使用的全部第三方成果。**版本号请在最终打包时用实际安装版本回填**，
运行清单 `manifest.json` 中的 `dependency_versions` 字段会自动记录实际版本。

## 1. 直接依赖

| 名称 | 版本 | 来源 | 许可证 | 使用范围 | 是否提交源码/权重 |
| --- | --- | --- | --- | --- | --- |
| PyMuPDF | 1.27.2.3 | https://github.com/pymupdf/PyMuPDF | AGPL-3.0（另提供商业许可） | 主引擎：文本与表格抽取、坐标获取 | 不提交，通过依赖安装 |
| pdfplumber | 0.11.7 | https://github.com/jsvine/pdfplumber | MIT | 对照引擎：交叉校验文本一致度 | 不提交，通过依赖安装 |
| RapidOCR | 3.9.2 | https://github.com/RapidAI/RapidOCR | Apache-2.0 | OCR 兜底：扫描件与位图图表文字 | 不提交，通过依赖安装 |
| onnxruntime | 1.30.0 | https://github.com/microsoft/onnxruntime | MIT | OCR 推理运行时（CPU） | 不提交，通过依赖安装 |
| PP-OCRv6 检测与识别模型、方向分类模型 | 随 RapidOCR 3.9.2 分发 | 安装包内 `rapidocr/models/` | 随上游模型许可 | OCR 文字检测与识别 | 不提交权重，随依赖安装，离线可用 |
| Streamlit | 1.64.0 | https://github.com/streamlit/streamlit | Apache-2.0 | 本地可视化页面（上传与结果展示） | 不提交，通过依赖安装 |

## 2. 可选引擎（未启用时不构成依赖）

| 名称 | 版本 | 来源 | 许可证 | 使用范围 | 是否提交源码/权重 |
| --- | --- | --- | --- | --- | --- |
| Docling | 待定 | https://github.com/docling-project/docling | MIT | 可选主引擎：版面、阅读顺序、provenance | 不提交；使用需另行登记权重 |
| MinerU | 待定 | https://github.com/opendatalab/MinerU | Apache-2.0 附加条款 | 可选主引擎：读取其离线解析产物 | 不提交；权重提供离线下载脚本 |
| PaddleOCR / PP-StructureV3 | 待定 | https://github.com/PaddlePaddle/PaddleOCR | Apache-2.0 | 可选增强：表格单元格与扫描件 | 不提交；权重提供离线下载脚本 |

## 3. 标准库与工具

本模块的契约、日志、质量判定、报告与命令行入口仅使用 Python 标准库
（`dataclasses`、`json`、`csv`、`difflib`、`hashlib`、`argparse`、`unittest` 等），不引入其他第三方依赖。

## 4. 使用边界说明

1. **PyMuPDF 采用 AGPL-3.0 双许可**。本模块仅将其作为依赖调用，未修改其源码；
   若后续产品化且不希望承担 AGPL 义务，可移除 PyMuPDF，改用 pdfplumber（MIT）作为主引擎，
   代码层面只需替换 `--primary` 参数。
2. **MinerU 的附加条款**要求在对外提供在线服务时于界面或文档显著标注使用了 MinerU，
   并设定了商业许可阈值（月活超过一亿或月收入超过两千万美元需单独取得商业许可）。
3. **本模块不调用任何外部接口**。竞赛要求的封闭环境中，研报数据不出本机；
   火山引擎 Understanding API 等商业解析服务不在使用范围内。
4. **模型权重不进代码仓库**。启用视觉模型时，用 `tools/fetch_models.py` 在联网环境下载，
   记录文件哈希后拷贝到离线环境，并把权重名称、版本、哈希补录到本文件。

## 5. 登记维护

- 责任人：PDF 解析模块负责人
- 更新时机：新增或替换任何第三方组件、升级版本、扩展使用范围时
- 每次运行的实际版本以 `data/out/logs/<run_id>/manifest.json` 为准，两者需保持一致

## 6. 测试数据来源

| 数据 | 来源 | 用途 | 是否随代码提交 |
| --- | --- | --- | --- |
| 合成测试 PDF | 本模块 `tests/sample_pdfs.py` 生成 | 单元测试与回归 | 由脚本现场生成，不提交二进制 |
| 公开研报 PDF | 东方财富公开研报接口（reportapi.eastmoney.com）返回的公开直链 | 本地实跑验证解析质量 | 不提交，`data/real/` 已加入 `.gitignore` |

测试数据仅用于验证解析流程与质量判定，不随代码仓库分发，也不用于再发布；
研报的著作权归原作者与发布机构所有。
