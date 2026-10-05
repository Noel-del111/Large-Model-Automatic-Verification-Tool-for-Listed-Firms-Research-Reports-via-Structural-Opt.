# 逐 case 诊断：典型案例会议导航

本导航覆盖 **71 份独立分析：59 个待审诊断实例、12 个匹配成功对照，涉及66份不同文档**。用途是帮助会议先建立共同的判断标准，再逐案讨论。每个链接进入对应实例的预期、原文、请求、原始回复、后处理、评分、未决事项与最小验证；导航不替代分析人的逐案陈述与人工签核。

材料来自 `research442-rerun-20261005` 新批次，不能作为旧报告的逐条复现。FP/FN/TP 是冻结基准的匹配结果；FP 不等于人工确认误报，TP 不等于已经具备业务免审条件。分析均为 AI 初步材料，最终判断留给人工。

## 如何理解“典型”

这里的典型性指**能清楚展示一种失分或转人工机制，并有可检验的边界**，不指它代表全量发生频率。首轮按类别与 FP/FN 配额抽取，优先纳入可用的锚定失败实例，并固定纳入重点案例；补样又定向覆盖零分、空候选与本批次较长文本。不能用某机制在这71例中出现得多，推断它就是全量首要根因。

一份文档可以有多个 case，同一业务问题可能同时形成 FP 与 FN。估算影响与人工成本时须回到独立错误实例、文档及实际复核动作，不能将两端重复计为两份收益。下文的对照是用于辨析机制的现有案例，并非随机实验或完全匹配的因果对照。

抽样依据见 [selection.json](../../artifacts/case-diagnostics-20261005/selection.json)，全部材料见 [案例索引](../../artifacts/case-diagnostics-20261005/index.md)。

## 71 份分析的阅读入口

下表按主要讨论入口组织，每例只列一次以便核对覆盖；同一 case 可以同时有多个机制。这些入口不是已由人工签核的根因标签。

| 主要入口 | 对应独立 case |
| --- | --- |
| 引用变形、拼接与多位置歧义 | [C001](../../artifacts/case-diagnostics-20261005/cases/C001.md)、[C009](../../artifacts/case-diagnostics-20261005/cases/C009.md)、[C013](../../artifacts/case-diagnostics-20261005/cases/C013.md)、[C015](../../artifacts/case-diagnostics-20261005/cases/C015.md)、[C025](../../artifacts/case-diagnostics-20261005/cases/C025.md)、[C029](../../artifacts/case-diagnostics-20261005/cases/C029.md)、[C071](../../artifacts/case-diagnostics-20261005/cases/C071.md) |
| 分类边界与错误解释竞争 | [C005](../../artifacts/case-diagnostics-20261005/cases/C005.md)、[C006](../../artifacts/case-diagnostics-20261005/cases/C006.md)、[C008](../../artifacts/case-diagnostics-20261005/cases/C008.md)、[C010](../../artifacts/case-diagnostics-20261005/cases/C010.md)、[C012](../../artifacts/case-diagnostics-20261005/cases/C012.md)、[C014](../../artifacts/case-diagnostics-20261005/cases/C014.md)、[C017](../../artifacts/case-diagnostics-20261005/cases/C017.md)、[C018](../../artifacts/case-diagnostics-20261005/cases/C018.md)、[C022](../../artifacts/case-diagnostics-20261005/cases/C022.md)、[C023](../../artifacts/case-diagnostics-20261005/cases/C023.md)、[C026](../../artifacts/case-diagnostics-20261005/cases/C026.md)、[C028](../../artifacts/case-diagnostics-20261005/cases/C028.md)、[C036](../../artifacts/case-diagnostics-20261005/cases/C036.md)、[C040](../../artifacts/case-diagnostics-20261005/cases/C040.md)、[C045](../../artifacts/case-diagnostics-20261005/cases/C045.md)、[C051](../../artifacts/case-diagnostics-20261005/cases/C051.md)、[C052](../../artifacts/case-diagnostics-20261005/cases/C052.md)、[C066](../../artifacts/case-diagnostics-20261005/cases/C066.md) |
| 跨度、主体上下文与多span表示 | [C003](../../artifacts/case-diagnostics-20261005/cases/C003.md)、[C004](../../artifacts/case-diagnostics-20261005/cases/C004.md)、[C011](../../artifacts/case-diagnostics-20261005/cases/C011.md)、[C016](../../artifacts/case-diagnostics-20261005/cases/C016.md)、[C019](../../artifacts/case-diagnostics-20261005/cases/C019.md)、[C020](../../artifacts/case-diagnostics-20261005/cases/C020.md)、[C027](../../artifacts/case-diagnostics-20261005/cases/C027.md)、[C047](../../artifacts/case-diagnostics-20261005/cases/C047.md)、[C069](../../artifacts/case-diagnostics-20261005/cases/C069.md) |
| 一对一匹配中的重复、合并与归属 | [C021](../../artifacts/case-diagnostics-20261005/cases/C021.md)、[C039](../../artifacts/case-diagnostics-20261005/cases/C039.md)、[C042](../../artifacts/case-diagnostics-20261005/cases/C042.md) |
| 候选未生成、任务规范或金标待裁定 | [C007](../../artifacts/case-diagnostics-20261005/cases/C007.md)、[C024](../../artifacts/case-diagnostics-20261005/cases/C024.md)、[C031](../../artifacts/case-diagnostics-20261005/cases/C031.md)、[C032](../../artifacts/case-diagnostics-20261005/cases/C032.md)、[C035](../../artifacts/case-diagnostics-20261005/cases/C035.md)、[C043](../../artifacts/case-diagnostics-20261005/cases/C043.md)、[C044](../../artifacts/case-diagnostics-20261005/cases/C044.md)、[C048](../../artifacts/case-diagnostics-20261005/cases/C048.md)、[C065](../../artifacts/case-diagnostics-20261005/cases/C065.md)、[C067](../../artifacts/case-diagnostics-20261005/cases/C067.md)、[C068](../../artifacts/case-diagnostics-20261005/cases/C068.md)、[C070](../../artifacts/case-diagnostics-20261005/cases/C070.md) |
| 当前输入与示例的证据边界 | [C041](../../artifacts/case-diagnostics-20261005/cases/C041.md) |
| 计算中的显示精度 | [C037](../../artifacts/case-diagnostics-20261005/cases/C037.md)、[C038](../../artifacts/case-diagnostics-20261005/cases/C038.md) |
| 基准外候选与业务告警边界 | [C002](../../artifacts/case-diagnostics-20261005/cases/C002.md)、[C030](../../artifacts/case-diagnostics-20261005/cases/C030.md)、[C033](../../artifacts/case-diagnostics-20261005/cases/C033.md)、[C034](../../artifacts/case-diagnostics-20261005/cases/C034.md)、[C046](../../artifacts/case-diagnostics-20261005/cases/C046.md)、[C049](../../artifacts/case-diagnostics-20261005/cases/C049.md)、[C050](../../artifacts/case-diagnostics-20261005/cases/C050.md) |
| 成功检测及确认策略对照 | [C053](../../artifacts/case-diagnostics-20261005/cases/C053.md)、[C054](../../artifacts/case-diagnostics-20261005/cases/C054.md)、[C055](../../artifacts/case-diagnostics-20261005/cases/C055.md)、[C056](../../artifacts/case-diagnostics-20261005/cases/C056.md)、[C057](../../artifacts/case-diagnostics-20261005/cases/C057.md)、[C058](../../artifacts/case-diagnostics-20261005/cases/C058.md)、[C059](../../artifacts/case-diagnostics-20261005/cases/C059.md)、[C060](../../artifacts/case-diagnostics-20261005/cases/C060.md)、[C061](../../artifacts/case-diagnostics-20261005/cases/C061.md)、[C062](../../artifacts/case-diagnostics-20261005/cases/C062.md)、[C063](../../artifacts/case-diagnostics-20261005/cases/C063.md)、[C064](../../artifacts/case-diagnostics-20261005/cases/C064.md) |

## 会前优先阅读的机制对照

### 1. 引用被改写，与字符偏移不准确应分开

**主例：[C029](../../artifacts/case-diagnostics-20261005/cases/C029.md)；成功对照：[C055](../../artifacts/case-diagnostics-20261005/cases/C055.md)、[C057](../../artifacts/case-diagnostics-20261005/cases/C057.md)；边界对照：[C009](../../artifacts/case-diagnostics-20261005/cases/C009.md)。**

C029 已指出“债权空间”术语问题，却把标题与换行改成“风险提示：全球…”，整段不再是原文连续字符串，锚定失败。C055、C057 的初始偏移也不准确，但原句逐字正确且位置唯一，宿主成功重定位并命中。因此“模型偏移错了”不是充分根因。

**证据边界：** C029 可证引用协议损失，恢复引用不能单独证明金融判断成立。C009 即使恢复标点，仍需核实两处价格是否同日、同规格、同口径。

**最小验证：** 固定类别、理由和输入，在离线副本只恢复原文字符后重放锚定与评分，再独立裁定业务真伪。分别报告“能定位”和“理由有充分证据”，不能只看锚定率。

### 2. 原句存在多次，不等于引用是虚构的

**主例：[C001](../../artifacts/case-diagnostics-20261005/cases/C001.md)、[C071](../../artifacts/case-diagnostics-20261005/cases/C071.md)；成功对照：[C053](../../artifacts/case-diagnostics-20261005/cases/C053.md)；边界对照：[C025](../../artifacts/case-diagnostics-20261005/cases/C025.md)、[C013](../../artifacts/case-diagnostics-20261005/cases/C013.md)。**

C001 已描述相邻句重复，但两个相同引文都能落在两个位置，未给消歧信息而被拒绝。C053 引用包含重复双方的完整证据并成功匹配。C071 也在raw理由中明确发现重复小标题，却因相同引文多位置而被拒绝，最终没有与金标重叠的有效span。这说明重复检查自身容易产生定位多解。

**证据边界：** C071 已用冻结函数离线核实：原始引文无法锚定；只补单处偏移能锚定但仍未包含完整gold；返回连续两行才同时满足锚定和包含条件。C001 恢复两个位置后，同样还需检查双span是否满足单个整段金标。C025 的量产时间冲突、C013 的收益率单位判断消除歧义后，仍有业务前提未决。

**最小验证：** 人工指定真实位置，分别重放原双span、消歧双span及连续原文引用。不能自动选第一次出现，也不能把任意两次出现都当作无价值重复。

### 3. 已发现异常，但类别与金标边界不同

**主例：[C052](../../artifacts/case-diagnostics-20261005/cases/C052.md)、[C028](../../artifacts/case-diagnostics-20261005/cases/C028.md)；成功对照：[C054](../../artifacts/case-diagnostics-20261005/cases/C054.md)、[C059](../../artifacts/case-diagnostics-20261005/cases/C059.md)；解释差异对照：[C014](../../artifacts/case-diagnostics-20261005/cases/C014.md)。**

C052 已发现法规书名号为空，模型归“法规引用错误”，金标为“属性值缺失”；C028 已指出“4-1月”倒序，模型归“时间信息非法”，金标为“时间矛盾”。两例引用都足以覆盖目标。C054 的空APP名称、C059 的事故与通报先后关系提供能成功匹配的对照。

**证据边界：** 改类别带来的匹配改善不是新增业务发现。C014 还把“在129年抵达”解释成旅程持续129年，不能把全部标签分歧看成只有评分类别问题。

**最小验证：** 人工统一空字段、日期本身非法、时间关系冲突的边界；仅在诊断副本改类别，固定原文、跨度和理由，分开统计类型改善与业务解释错误。

### 4. 跨度失配既可能是表示差异，也可能漏了必要证据

**主例：[C003](../../artifacts/case-diagnostics-20261005/cases/C003.md)、[C047](../../artifacts/case-diagnostics-20261005/cases/C047.md)；成功对照：[C063](../../artifacts/case-diagnostics-20261005/cases/C063.md)；边界对照：[C020](../../artifacts/case-diagnostics-20261005/cases/C020.md)、[C027](../../artifacts/case-diagnostics-20261005/cases/C027.md)。**

C003 的两段引文及中间换行覆盖重复内容，但冻结匹配要求每条金标span被某一条预测span完整包含，不能以预测span并集代替。C063 用一个完整段落覆盖金标两条证据，能正常匹配。C047 则只识别复合财务段中“加权”残缺的局部，扩大引用不等于业务识别已完整。

**证据边界：** C027 的额外前句可能只是边界规范；C020 缺少公司与产品主体则可能影响复核。不能机械使用同一种扩span策略。

**最小验证：** 人工指出最小必要事实和主体，离线只改变引用表示，保留内容、类别及主分。复合金标另报实际识别的子问题，不对任意远距离span取包络来增加命中。

### 5. 一对一匹配：重复、合并和归属是不同问题

**主例：[C021](../../artifacts/case-diagnostics-20261005/cases/C021.md)、[C039](../../artifacts/case-diagnostics-20261005/cases/C039.md)、[C042](../../artifacts/case-diagnostics-20261005/cases/C042.md)；成功对照：[C058](../../artifacts/case-diagnostics-20261005/cases/C058.md)、[C062](../../artifacts/case-diagnostics-20261005/cases/C062.md)。**

C021 的规则和模型对同一非法日期分别产出候选，未合并后多出一个FP；C058 展示两种证据能汇合为确认状态的路径。C039 把两个独立市场CAGR错误放在一个候选，一对一评分只能匹配一个；C062 的单个CAGR错误能正确匹配。C042 则有两条理由不同的候选覆盖同一金标，确定性最大匹配将金标分给宽跨度候选，直接讨论比较号的候选成为FP。

**证据边界：** 匹配先按canonical排序，重排原始列表不是有效实验。C042 有不同的最大基数分配，不说明总TP/FP会改变。仅按相同span去重可能吞掉同句多个异常。

**最小验证：** 分别构造同异常合并、不同异常拆分、固定兼容关系的归属展示副本。保留主分、来源与错误身份；人工确认问题粒度之后才讨论告警数与复核数量变化。

### 6. 文本被引用，不代表其中所有问题已被识别

**主例：[C024](../../artifacts/case-diagnostics-20261005/cases/C024.md)；日期检测对照：[C058](../../artifacts/case-diagnostics-20261005/cases/C058.md)；候选空集对照：[C065](../../artifacts/case-diagnostics-20261005/cases/C065.md)；本批较长文本对照：[C070](../../artifacts/case-diagnostics-20261005/cases/C070.md)。**

C024 的raw引用了含“129年”的整句，理由却只讨论“超套利线性增长”的术语问题，年份没有对应理由或候选。C065 则是调用成功且合法回复为 `errors=[]`，整篇没有任何候选。C070 的融资率段未产生候选，但同篇其他7项提示正常产出，全文4029字符进入了请求。这是三种不同的候选生成差异。另见[C071](../../artifacts/case-diagnostics-20261005/cases/C071.md)：它的raw理由已经明确发现目标重复，最终无有效span来自锚定拒绝，不能归入“未生成候选”。

**证据边界：** 可以定位到候选生成阶段，不能据一次回放区分prompt、注意分配、知识或随机性。C065 金标具体缺失要素仍需裁定；C070 是否应改为财务费用率也需人工确认。不能仅凭较长文本就宣称长度致因。

**最小验证：** 先人工确认独立异常。对C024准备仅修复已识别术语、保持年份不变的对照；对C070准备完整费用段与原全文的对照，知识提示与上下文长度须分别改变。C065 另核查空结果的稳定性。后续模型实验需要固定配置与重复观察，本导航不执行新调用。

### 7. 当前上下文与示例的证据来源越界

**主例：[C041](../../artifacts/case-diagnostics-20261005/cases/C041.md)；同文档反例：[C029](../../artifacts/case-diagnostics-20261005/cases/C029.md)；当前证据完整对照：[C063](../../artifacts/case-diagnostics-20261005/cases/C063.md)。**

C041 在亚马逊报告里引用贵金属投资建议并指控主题冲突；该整句不在当前文档，却逐字出现在few-shot示例。C029 在同一份输出中锚定失败的原因是当前原句被改写，两者来源不同，不能用同一种宽松匹配修复。

**证据边界：** 已确认输出引用示例内容，尚未证明内部注意机制或某条提示的因果贡献，也不能外推所有幻觉来自示例。

**最小验证：** 离线标出引文属于当前contexts、示例、其他文档或均不存在；之后仅改变该示例做配对比较，检查真实问题是否保留。禁止把示例句强行定位到当前文档。

### 8. 计算点值不同，未必超过显示精度可解释范围

**主例：[C037](../../artifacts/case-diagnostics-20261005/cases/C037.md)、[C038](../../artifacts/case-diagnostics-20261005/cases/C038.md)；计算冲突对照：[C062](../../artifacts/case-diagnostics-20261005/cases/C062.md)；同条理由混合问题对照：[C066](../../artifacts/case-diagnostics-20261005/cases/C066.md)。**

C037 的22.99/1.58约14.55，但14.59隐含EPS约1.57574仍可显示为1.58。C038 的4.62/33.38约13.84%，在两金额合理舍入范围内，报告13.86%仍可能成立。C062 的四年CAGR约24.7973%而报告35%，提供明确关系冲突对照。C066 一条理由中“三年对应四个YOY”有直接依据，但附加的两个点值增长率质疑可同时由金额舍入解释。

**证据边界：** 可行精确值是数学反例，不是查到的真实财报值。只能说相应告警证据不足，不能据此宣布报告已算对。一条候选理由包含正确部分，也不代表所有指控都正确。

**最小验证：** 在固定候选上加入期间、口径及显示精度区间复算，以超出合理区间的错误作对照。对混合理由逐项核查，不能统一放宽一个百分比阈值。

### 9. 金标未说明缺什么，或合计对象不同，先核查预期

**主例：[C048](../../artifacts/case-diagnostics-20261005/cases/C048.md)、[C007](../../artifacts/case-diagnostics-20261005/cases/C007.md)、[C067](../../artifacts/case-diagnostics-20261005/cases/C067.md)；明确空字段对照：[C054](../../artifacts/case-diagnostics-20261005/cases/C054.md)；语义未决：[C031](../../artifacts/case-diagnostics-20261005/cases/C031.md)、[C032](../../artifacts/case-diagnostics-20261005/cases/C032.md)。**

C048 风险提示以顿号结尾但已有两项风险，金标未说还必须有何项；C007 的整段产品描述被标属性缺失，却没有具体缺失字段。C067 的金标把16款机器人与6+4+4家企业作计算问题，14家展示16款不天然矛盾，不能忽略“款”和“家”的对象差异。C054 的空名称则有直接可见的字段空槽。

**证据边界：** raw未报告目标不等于业务漏检成立；金标歧义、输入残缺、行业省略与真实错误必须区分。C031 的现金流因果、C032 的成本端铁水也要人工给出具体错误命题。

**最小验证：** 人工补足缺失槽位、必要性、合计对象与关系。C067 用“16家企业”与“16款机器人”对照检查同单位约束；无法判定则保留未决，不猜字段，也不训练模型强行相加不同对象。

### 10. 基准外提示，与任务规范冲突

**主例：[C034](../../artifacts/case-diagnostics-20261005/cases/C034.md)、[C035](../../artifacts/case-diagnostics-20261005/cases/C035.md)；格式检测对照：[C061](../../artifacts/case-diagnostics-20261005/cases/C061.md)；其他边界：[C030](../../artifacts/case-diagnostics-20261005/cases/C030.md)、[C049](../../artifacts/case-diagnostics-20261005/cases/C049.md)。**

C034 双百分号有可见依据但没有金标；C035 同层编号混用被金标收录，冻结prompt却排除一般排版偏好。这分别涉及标注覆盖和任务边界。C061 平均价格中的连字符是已有格式TP，但正确修复值仍未知。

**证据边界：** 不能因FP屏蔽双百分号，也不能因FN全面开放排版提示。C030 的上下文可继承指标、C049 的相对变化与百分点差异，还需判断是否产生实质业务歧义。

**最小验证：** 人工按比赛交付标准裁定需不需要修改、严重程度和类别，单独补充裁定，不回写冻结主金标。正常标点、标题省略、层级编号、约数和预测措辞作为误报对照。

### 11. 检测正确仍转人工，与是否可免审分开

**主例：[C064](../../artifacts/case-diagnostics-20261005/cases/C064.md)、[C062](../../artifacts/case-diagnostics-20261005/cases/C062.md)；程序确认对照：[C058](../../artifacts/case-diagnostics-20261005/cases/C058.md)；修复值未知对照：[C060](../../artifacts/case-diagnostics-20261005/cases/C060.md)、[C061](../../artifacts/case-diagnostics-20261005/cases/C061.md)。**

C064 的缺指标问题类型和范围都匹配，仍needs_review；C062 的CAGR推导正确且匹配，也待人工。冻结协议没有模型决策字段，宿主默认needs_review，再由有限确定性规则升级；C058 展示日历规则成功升级。因此这些例子的转人工不能简单解释为模型不敢。

**证据边界：** TP不是人工批准免审。发现问题不等于知道正确改法：C060 的真实交易方式、C061 的原始价格都未确认。C064 一例也不能解释整个类别低召回。

**最小验证：** 人工签核明确免审条件，固定候选比较原策略和仅覆盖该条件的新策略；用正常共享指标、不同期间、舍入和合法日期作反例，记录实际减少的复核动作及新增错误，不能直接奖励确认条数。

### 12. 外部来源必要性，要由具体case证明

**主例：[C068](../../artifacts/case-diagnostics-20261005/cases/C068.md)；无需外部知识即可发现空槽的对照：[C052](../../artifacts/case-diagnostics-20261005/cases/C052.md)；法规证据边界：[C051](../../artifacts/case-diagnostics-20261005/cases/C051.md)。**

C068 的金标认为“净利润39万元”单位有误，但没有提供正确单位或原始募集说明书；raw没有报告它。与C052直接可见的空名称不同，C068需要具体来源、主体和期间才能裁定。C051 中模型对法规“可/不得”的主张也没有附适用版本的权威条文。

**证据边界：** 这些例子支持提出证据核查需求，尚未证明现有低分总体由专业知识不足造成，更未证明引入完整RAG即可修复。

**最小验证：** 人工先找到适用的权威原文并裁定预期；之后分开比较无补充、人工选定正确证据和系统检索所得证据。保持候选规则不变，区分证据缺失、检索不到和模型不会用证据。

## 零分与较长文本补样如何使用

- [C065](../../artifacts/case-diagnostics-20261005/cases/C065.md)：000160，执行成功但全篇合法空候选，补上候选空集路径。
- [C066](../../artifacts/case-diagnostics-20261005/cases/C066.md)：000411，三个金标相关问题都有候选，却因类型或span表示全部未匹配；与C065不能共享“没工作”的解释。
- [C067](../../artifacts/case-diagnostics-20261005/cases/C067.md)：000595，目标合计对象“款/家”不一致，金标业务前提未决。
- [C068](../../artifacts/case-diagnostics-20261005/cases/C068.md)：000696，目标正确单位依赖原始文件，目前证据不足。
- [C069](../../artifacts/case-diagnostics-20261005/cases/C069.md)：000646，4184字符，目标日期已发现，失分为类别和跨度共同条件。
- [C070](../../artifacts/case-diagnostics-20261005/cases/C070.md)：000381，4029字符，目标融资率术语未生成候选，其余多项提示已生成。

- [C071](../../artifacts/case-diagnostics-20261005/cases/C071.md)：000589，raw明确报告重复小标题，但重复引文无法唯一定位，补足“最终无有效重叠不代表raw没发现”的FN阅读路径；它不是此前FP分析完全未涉及的新根因，同文其他计算理由也不能全部归因锚定。

与原有[C007](../../artifacts/case-diagnostics-20261005/cases/C007.md)的000705一起，补齐本批已发现五份零分文档各至少一个独立错误实例的阅读入口，**不等于五篇文档的每条金标都已完成人工分析**。C069/C070仅覆盖本批中相对较长的文本，不能当作多万字文档或跨文件能力验证。

## 仍未覆盖的范围

- **真实输入漏传、请求失败及逐字网络报文。** 重建messages与历史指纹相符，不等于留存了完整HTTP报文；当前没有可据此认定的漏传或超时典型例。C065的空结果已经确认是成功执行，不能混入执行失败。
- **OCR与版面解析。** 这批输入是文本；C046/C048/C050的残缺文字不能直接归因OCR。真实PDF和业务输入需要另外取样。
- **类别正例和正常实例。** 模糊语言、法规引用错误只纳入未匹配候选，不一致条款没有可用实例。TP对照是检测到错误的成功例，不是真阴性；当前不能据此估计正常文档误报率或确认阈值。
- **专业知识和检索的因果贡献。** 外部来源待核对并非已证实的RAG收益，需要上面的成对证据实验。
- **人工成本与免审风险。** 真人签核和实际复核计时未完成；须衡量独立问题、复核动作、错误代价，不能只把needs_review条数当人工成本。

出现新机制、执行失败、人工推翻初判或新的长度/版面范围时，应追加独立case并保留选择理由，不能替换不利案例来改善样本表现。

## 会议产出与本次补样记录

每个负责人先讲预期、实际行为、第一次发生差异的阶段、关键证据和未决点，再与本导航比较。会后结论写回独立人工记录，同组其他case仍需逐条确认。优化项必须有相关case、已证实机制、未覆盖范围、最小改动、对照和成功标准；形式评分改善不能直接记成新检错能力，自动确认率提高也不能直接记成业务收益。

本次在**初始64例（52诊断+12成功对照）**基础上，保持原例不变，追加C065–C071七个目的性诊断实例，现为**71例（59诊断+12成功对照）**。追加依据是零分文档、合法空候选、本批较长文本及raw/最终候选不同阶段的机制覆盖缺口；这提高机制代表性，**没有形成可用于频率外推的概率抽样**。
