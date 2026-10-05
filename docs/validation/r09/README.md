# R09 D 接入验收状态（2026-10-05）

本目录记录配对核查补证字段在 D 页面及复核合并导出的接入，不代表模型效果提升、人工签核制度完成或自动补证闭环。

已完成：

- 页面概览分别显示“待补证/待澄清 finding 数”和“请求条目数”。
- finding 详情及人工复核区域展示处理提示和 9 个请求字段；`file` 仅作文本线索，不触发访问。
- 合并 CSV、Markdown、PDF 保留补证状态、条目数和完整请求；JSON 只追加独立 `review`。
- 兼容旧 1.0.0 整组缺失，以及仅缺 `evidence_request_items` 但列表完整的历史形态。
- 对部分字段缺失、状态关联错误、汇总不一致显式报契约问题，不自动修补原结果。
- 异常汇总不会遮蔽仍可读取的请求内容；复核区域与详情共用九字段展示。布尔计数和非法字段类型会被标记为契约错误。
- `text-review/1.0` 不进入配对补证契约。
- 长原因按实际内容分页；验收 PDF 为 `r09-long-evidence-request.pdf`，结果摘要及哈希为 `r09-acceptance.json`。

自动测试覆盖接入说明中的 A-F、H，并检查合并 JSON 不改写原字段。G 中“原件哈希不变”和复核存储隔离已有既有 `ReviewStore`/产物测试覆盖；真人填写复核内容、页面截图和 C/D 双方最终签字仍需人工完成。

运行：

```powershell
python -m unittest discover -s frontend/tests -v
python frontend/tools/build_r09_acceptance.py
```

验收 PDF 是合成契约长文本样例，不是实际业务报告。请求中的不存在路径只用于验证安全展示。
