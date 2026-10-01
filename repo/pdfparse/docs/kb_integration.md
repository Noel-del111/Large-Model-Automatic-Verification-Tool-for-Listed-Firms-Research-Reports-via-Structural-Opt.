# 知识库接入说明（OpenViking / RAGFlow）

回答两个问题：指导老师提出的 OpenViking 方案能不能用；解析模块要交出什么，才能一次接上知识库。

## 一、结论

能用，而且我们这一侧已经准备好了接口，但有三个前提必须在组会上确认：

1. **OpenViking 是服务端架构**，需要单独部署服务进程（`openviking-server`），CLI 只是客户端；
2. **必须提供 Embedding 与 VLM 模型**，它自己不做本地推理。封闭环境下要用 Ollama、vLLM 或 Xinference
   在本地起 OpenAI 兼容端点（服务端 `doctor` 会专门检测 Ollama）；
3. **安装体积不小**：本机实测 `pip install openviking==0.4.21` 会装 396 个包、占用约 1.0 GB，
   其中大部分是它默认依赖的火山引擎 SDK。如果现场机器磁盘紧张，建议用托管服务或精简安装。

如果现场网络与算力都不允许跑模型服务，退路是我们模块内置的离线检索（BM25 + block_id 回链），
功能上覆盖“检索、多文档横向对比、回链到原文位置”，只是没有语义检索能力。

## 二、解析模块交出的三样东西

`export-kb` 命令会把解析产物转成知识库可直接消费的形态：

```bat
run.cmd export-kb --out data\out_real_v9 --kb data\kb
```

| 产物 | 内容 | 用途 |
| --- | --- | --- |
| `markdown/<文档>.md` | 按页组织，带 `<!-- Page N -->` 与 `<!-- block:ID page:N type:T bbox:[...] -->` 锚点，表格转 Markdown，标题保留层级 | 直接入库；锚点让入库后的内容仍能定位回原文 |
| `kb_index.jsonl` | 每个块一行：文档、页码、坐标、类型、文本、单元格、句子 | 检索命中后回查页码与坐标，渲染高亮 |
| `ingest_manifest.json` | 目标 URI、文件哈希、现成入库命令 | 一键复制执行，可审计 |

锚点用 HTML 注释的原因：Markdown 渲染时不可见，不污染正文与向量化内容；
OpenViking 自己的 PDF 解析也是用 `<!-- Page N -->` 保留页码，属于同一套约定。

## 三、接入 OpenViking 的步骤

### 1. 起服务端（离线环境）

```bash
# 本地模型：先把 embedding 与 VLM 跑起来（示例用 Ollama）
ollama pull bge-m3
ollama pull qwen2.5vl:7b

# 安装并初始化
uv tool install openviking --upgrade
openviking-server init          # 按提示填 embedding 与 VLM；也可直接复用 deploy/ov.conf.example
openviking-server doctor
openviking-server               # 默认监听 127.0.0.1:1933
```

本项目提供了一份指向本地 Ollama 的配置模板：`deploy/ov.conf.example`，
把 `api_base` 指向自己的推理服务即可，`storage.workspace` 建议改到数据盘。

**路径必须是纯 ASCII。** 实测把 `storage.workspace` 指向项目目录（路径含中文）时，
服务端的本地向量库后端会直接启动失败（底层 Rust 绑定读不出中文路径）；
本项目的目录名恰好含中文，所以配置模板里把工作区固定到 `D:/openviking-data` 这类纯英文路径。

### 2. 安装并连接 CLI

```bash
npm install -g @openviking/cli
ov config                        # 自建服务选“自定义”，地址填 http://127.0.0.1:1933
ov health
```

### 3. 入库与检索

```bash
# 命令由 export-kb 生成，直接复制
ov add-resource "data/kb/markdown/华鑫证券_2026年宏观策略.md" \
   --to viking://resources/research-reports/华鑫证券_2026年宏观策略 --wait --timeout 300

ov tree
ov find "归母净利润"             # 语义检索
```

检索命中后，用返回内容里的 `block_id` 在 `kb_index.jsonl` 中回查页码与坐标，
再执行 `run.cmd preview --out <产物目录> --doc <文档> --pages <页> --blocks <block_id>`
即可在页面上高亮出证据位置。

## 四、接入 RAGFlow 的步骤

RAGFlow 的深度文档解析自带字符级坐标与切片回高亮，接入更直接：

1. 上传 `markdown/<文档>.md`（RAGFlow 切片时可保留 Markdown 结构）；
2. 把 `kb_index.jsonl` 作为外部元数据表导入，键为 `doc_id + block_id`；
3. 检索命中后按 `block_id` 取页码与坐标，同样用 `preview` 高亮。

RAGFlow 为 Apache-2.0，若团队在意许可证，它比 OpenViking 的 AGPL-3.0 更宽松。

## 五、不装知识库也能演示：离线检索

模块内置 BM25 检索，直接跑在 `kb_index.jsonl` 上：

```bat
run.cmd search --kb data\kb --query "归母净利润 增速" --top 5
run.cmd search --kb data\kb --query "流动性 货币政策" --compare
```

第一条按相关度返回命中块，附页码、坐标与页面状态；第二条按文档分组，用于横向对比。
两者都会打印 `preview` 回链命令，形成“检索 → 定位 → 高亮”的完整闭环。
这条链路不依赖任何服务，比赛现场最稳。

## 六、本机验证记录

在开发机上把整条接入链路实跑了一遍，结果如下：

| 步骤 | 结果 | 证据 |
| --- | --- | --- |
| 安装 OpenViking 0.4.21 | 成功 | 装完 396 个包、约 1.0 GB |
| `openviking-server doctor` | 8 项检查中 4 项 PASS | Python、Native Engine、AGFS、磁盘通过；Embedding 与 VLM 因未配置模型失败；单独检测了 Ollama |
| 配置含中文的工作区路径 | 启动失败 | 本地向量库的 Rust 绑定读不出中文路径，改用 `D:/openviking-data` 后正常 |
| 启动服务端 | 成功 | `/health` 返回 `{"status":"ok","healthy":true,"version":"0.4.21","auth_mode":"dev"}` |
| CLI 连接 | 成功 | `ov health` 显示已连接、开发模式认证 |
| 上传解析成果 | 成功 | 服务端工作区出现同名的 73.8 KB 上传文件，与导出的 Markdown 一致 |
| 语义处理（摘要与向量） | 未完成 | 配置指向的本地模型端点未启动，服务端反复重试 `/embeddings`，CLI 报请求超时 |
| `export-kb` | 成功 | 4 篇研报、7884 个索引块、生成 4 条入库命令 |
| 离线检索与回链 | 成功 | 命中结果带页码、坐标、块编号，可直接生成 `preview` 高亮命令 |

把链路上每一段的完成度说清楚，是为了避免答辩时被问住：**解析产物到服务端的通道是通的，卡点只在模型端点上**。
现场只要有一台能跑 Ollama 或 vLLM 的机器（CPU 也能跑 1B 级 embedding 模型），语义检索就能补齐；
否则用内置离线检索，功能上覆盖“检索、横向对比、回链”三条主线，只是没有语义匹配能力。

下一步取决于现场条件：能跑本地模型服务就上 OpenViking 的语义检索；
不能就先用内置离线检索，把“检索 + 横向对比 + 证据回链”这条主线演示完整。

## 七、有网络时的推荐配置

终评环境有网络，直接把 Embedding 与 VLM 指向云端 OpenAI 兼容端点即可，不必在本机跑模型：

```bash
export OPENVIKING_EMBEDDING_API_KEY=<向量模型密钥>
export OPENVIKING_VLM_API_KEY=<视觉模型密钥>
openviking-server --config deploy/ov.conf.example     # 把 api_base / model 改成实际端点
```

`.env` 与密钥一律不要进仓库；`ov.conf` 里的密钥建议用环境变量占位后再替换。

端点是否可用，先用本项目自带的体检命令确认，避免到现场才发现模型名没开通：

```bat
run.cmd model-check --base-url <端点>/v1 --api-key <密钥> ^
  --embedding-model <向量模型> --chat-model <对话模型> --vision-model <视觉模型>
```

体检输出会逐项给出通过/失败、耗时与返回示例。三类能力的分工：
Embedding 供 OpenViking 语义检索；对话模型供 L0/L1 摘要与记忆提取；
视觉模型供解析模块的第三层抽检。
