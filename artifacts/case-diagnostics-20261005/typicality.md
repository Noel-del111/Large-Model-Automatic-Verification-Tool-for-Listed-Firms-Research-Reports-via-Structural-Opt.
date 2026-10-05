# 逐 case 样本典型性审计

典型性在此指诊断现象与困难切片覆盖，不指统计代表性。所有机械标签都不是全量根因裁定。

冻结批次：research442-rerun-20261005 / hybrid，442篇，1137个FP/FN。原始失败样本52个，补样后59个；12个TP对照不进入失败样本分母。

## 类别 × FP/FN

|类别|结果|全量|原52|补样后59|原样本占比−全量占比(pp)|
|---|---|---:|---:|---:|---:|
|不一致条款|FP|0|0|0|0.00|
|不一致条款|FN|0|0|0|0.00|
|冗余语句|FP|156|2|2|-9.87|
|冗余语句|FN|83|2|3|-3.45|
|属性值缺失错误|FP|19|2|2|2.18|
|属性值缺失错误|FN|18|2|2|2.26|
|数值不一致错误|FP|47|2|2|-0.29|
|数值不一致错误|FN|21|2|3|2.00|
|数值单位错误|FP|34|2|2|0.86|
|数值单位错误|FN|10|2|3|2.97|
|数值缺失|FP|46|2|2|-0.20|
|数值缺失|FN|16|2|2|2.44|
|时间信息非法|FP|62|2|2|-1.61|
|时间信息非法|FN|12|2|2|2.79|
|时间矛盾|FP|34|2|2|0.86|
|时间矛盾|FN|30|2|3|1.21|
|术语误用|FP|56|2|2|-1.08|
|术语误用|FN|93|2|3|-4.33|
|格式错误|FP|21|2|2|2.00|
|格式错误|FN|34|2|2|0.86|
|模糊语言|FP|6|2|2|3.32|
|模糊语言|FN|0|0|0|0.00|
|法规引用错误|FP|7|2|2|3.23|
|法规引用错误|FN|0|0|0|0.00|
|计算错误|FP|62|2|2|-1.61|
|计算错误|FN|26|2|3|1.56|
|语义逻辑矛盾|FP|69|2|2|-2.22|
|语义逻辑矛盾|FN|37|2|2|0.59|
|金融要素缺失|FP|6|2|2|3.32|
|金融要素缺失|FN|132|2|3|-7.76|

类别×结果总变差距离（0表示分布相同，不是置信度）：baseline=0.3243, expanded=0.3191

## 文档集中度

|范围|实例|文档|单篇最多实例|前5篇占比|HHI|
|---|---:|---:|---:|---:|---:|
|full|1137|392|13|4.05%|0.0034|
|baseline|52|48|3|17.31%|0.0229|
|expanded|59|55|3|15.25%|0.0198|

HHI和前5篇占比会随样本量变化，仅用于描述文档集中程度，不是统计代表性检验。

## scene 切片

失败数/文档数分别计数；全量文档列含没有失败的文档。

|切片|全量文档|全量失败/失败文档|原52失败/文档|补样59失败/文档|
|---|---:|---:|---:|---:|
|个股研报|200|510/181|18/15|20/17|
|行业研报|242|627/211|34/33|39/38|

## length 切片

失败数/文档数分别计数；全量文档列含没有失败的文档。

|切片|全量文档|全量失败/失败文档|原52失败/文档|补样59失败/文档|
|---|---:|---:|---:|---:|
|<2000|391|984/348|47/43|51/47|
|2000–3999|42|119/36|5/5|6/6|
|4000–7999|9|34/8|0/0|2/2|
|>=8000|0|0/0|0/0|0/0|

## document_f1 切片

失败数/文档数分别计数；全量文档列含没有失败的文档。

|切片|全量文档|全量失败/失败文档|原52失败/文档|补样59失败/文档|
|---|---:|---:|---:|---:|
|(0,0.4]|57|285/57|16/13|19/16|
|(0.4,0.6]|97|394/97|18/18|18/18|
|(0.6,0.8]|155|345/155|13/12|13/12|
|(0.8,1]|128|86/78|4/4|4/4|
|0|5|27/5|1/1|5/5|

## 可重叠机械标签

每格为命中数/适用分母；FN专属标签分母是FN，invalid_anchor标签分母是FP，其余分母是全部失败实例。

|标签|全量|原52|补样59|
|---|---:|---:|---:|
|invalid_anchor|46/625|6/28|6/28|
|same_type_partial_overlap|194/1137|8/52|8/59|
|other_type_full_containment|372/1137|17/52|18/59|
|one_to_one_competition|16/1137|3/52|3/59|
|no_anchored_overlap|487/1137|16/52|21/59|
|other_type_partial_overlap|37/1137|4/52|5/59|
|fn_raw_candidates_empty|3/512|0/24|1/31|
|fn_raw_present_no_exact_quote_overlap|211/512|8/24|11/31|
|fn_raw_quote_overlap_without_final_overlap|33/512|0/24|1/31|
|fn_no_raw_candidate_of_gold_type|347/512|14/24|19/31|
|invalid_anchor_exact_quote_absent|4/625|3/28|3/28|
|invalid_anchor_exact_quote_ambiguous|42/625|3/28|3/28|

- **invalid_anchor**：FP的最终提示明确标记invalid_anchor；不是所有FP，也不推断OCR失败。
- **same_type_partial_overlap**：存在同类型预测/gold空间重叠，但该预测不能严格完整包含该gold全部span。
- **other_type_full_containment**：存在不同类型预测/gold，预测严格完整包含gold；不证明reason识别了同一个异常。
- **one_to_one_competition**：存在同类型完整包含的兼容边，但该边另一端已被确定性一对一匹配分配给其他实例。
- **no_anchored_overlap**：FN与所有最终有效提示均无空间重叠；FP须自身有有效span且与所有可评gold均无重叠。
- **other_type_partial_overlap**：不同类型仅部分空间重叠，不满足完整包含；只作几何关系描述。
- **fn_raw_candidates_empty**：FN所在整篇report.raw_candidates为空；需另看执行状态，不等于已证实的生成根因。
- **fn_raw_present_no_exact_quote_overlap**：整篇raw候选非空，但其所有引文在原文的精确出现位置均不与该FN重叠；改写引文可能造成零命中。
- **fn_raw_quote_overlap_without_final_overlap**：FN无最终提示重叠，但至少一个raw引文的某个精确出现位置重叠；不说明其reason识别该错误。
- **fn_no_raw_candidate_of_gold_type**：整篇raw候选中没有gold类型；可能与分类差异重叠，不称该类型没被检查。
- **invalid_anchor_exact_quote_absent**：invalid_anchor FP至少有一个原始引文在原文精确零命中；不做空白或标点模糊恢复。
- **invalid_anchor_exact_quote_ambiguous**：invalid_anchor FP至少有一个原始引文有多个精确出现位置；标签可与零命中并存。

## 零分文档逐篇覆盖

|文档|长度|场景|raw候选|原样本|补样后|
|---|---:|---|---:|---|---|
|000160|1092|行业研报|0|未覆盖|C065|
|000411|1768|个股研报|3|未覆盖|C066|
|000595|1493|行业研报|2|未覆盖|C067|
|000696|1396|行业研报|5|未覆盖|C068|
|000705|811|行业研报|1|C007|C007|

## 缺口及最小补样

原样本未覆盖的机械标签或困难切片：

- length_scene:4000–7999:个股研报
- length_scene:4000–7999:行业研报
- mechanical:fn_raw_candidates_empty
- mechanical:fn_raw_quote_overlap_without_final_overlap
- zero_f1_document:fined:eval_data:5ffe80ef7c529d19:000160
- zero_f1_document:fined:eval_data:5ffe80ef7c529d19:000411
- zero_f1_document:fined:eval_data:5ffe80ef7c529d19:000595
- zero_f1_document:fined:eval_data:5ffe80ef7c529d19:000696

- C065 / `["fined:eval_data:5ffe80ef7c529d19:000160", "FN", null, 2]`：已补；覆盖 mechanical:fn_raw_candidates_empty, zero_f1_document:fined:eval_data:5ffe80ef7c529d19:000160。
- C066 / `["fined:eval_data:5ffe80ef7c529d19:000411", "FN", null, 0]`：已补；覆盖 zero_f1_document:fined:eval_data:5ffe80ef7c529d19:000411。
- C067 / `["fined:eval_data:5ffe80ef7c529d19:000595", "FN", null, 0]`：已补；覆盖 zero_f1_document:fined:eval_data:5ffe80ef7c529d19:000595。
- C068 / `["fined:eval_data:5ffe80ef7c529d19:000696", "FN", null, 1]`：已补；覆盖 zero_f1_document:fined:eval_data:5ffe80ef7c529d19:000696。
- C069 / `["fined:eval_data:5ffe80ef7c529d19:000646", "FN", null, 2]`：已补；覆盖 length_scene:4000–7999:行业研报。
- C070 / `["fined:eval_data:5ffe80ef7c529d19:000381", "FN", null, 1]`：已补；覆盖 length_scene:4000–7999:个股研报。
- C071 / `["fined:eval_data:5ffe80ef7c529d19:000589", "FN", null, 0]`：已补；覆盖 mechanical:fn_raw_quote_overlap_without_final_overlap。

补样后未覆盖目标：无。已覆盖定义的机械现象、全部零分文档以及有失败的4000字以上各场景；这不证明所有业务根因齐备。

## 解释边界

- 目的性诊断样本；类别配额、锚点优先和尾部补样改变入选概率，不能估计总体根因频率或总体业务准确率。
- 机械标签允许重叠，同一业务问题也可能同时产生一个FP和一个FN；各标签计数不能相加当作独立根因。
- 覆盖计数只统计每份case的焦点实例，不将其related_predictions/related_golds附带讨论算作另一个已选实例。因此焦点FN切片为0不表示既有FP分析从未涉及相似链路。
- 原文包含、引文重叠和提示中列出某类型不等于模型实际检查或理解了该异常；需逐case人工签核。
- 本批是文本研报，不能评价PDF/OCR、跨文件检索、无错误真阴性或竞赛端到端能力；全量最长仍不足8000字。
- 全量指research442-rerun-20261005的hybrid冻结批次，不与截图旧批或当前源码重跑成绩混合。
- 本地manifest提供文件一致性检查，不是独立签名的来源保证；审计不改冻结输入、评分或人工结论。

新模型/API调用：0。人工结论不由本工具生成。
