# BidAgent 评分拆解与章节目录领域策略

## 定位

本策略属于 BidAgent V3 产品运行时，不是 Codex `SKILL.md`，也不建立第二套中间
JSON。它既约束评分语义 LLM，也约束 `Planning Agent` 内部版本化章节目录拆分 Skill。
唯一权威链保持为：

`SourceIndex / RequirementLedger → 分批 ScoreSemantic（ScoreCondition / ScoreResponseUnit）→ ChapterBlueprint → G2`

`ScoreModel` 只承载确定性解析出的评分组、物理评分点、完整评分档次和来源锚点，是
`ScoreSemantic` 的结构输入，不构成一个额外的大模型语义阶段。当前自动主链不调用
`ProjectModel`、`ProjectUnderstandingProvider`、`ResponseTopicGraph`、
`ResponseTopic`、`ResponseDuty`，也不执行 `scope` 归纳文本的字符相似度校验。上述对象
只允许由显式 legacy/兼容入口使用，不能被 `document.prepare_outline` 自动读取，也不能
在新链失败时作为回退。

产品入口仍为 `document.prepare_outline`。外部 Bid Master/Codex Skill 仍然只是可选
Command 入口；没有任何外部 Skill 时，Web/API 也必须完整执行 BidAgent 内部推理链并得到
相同的受控结果。

## 目标生成链

评分驱动目录的目标链为：

```text
确定性评分结构 + 轻量 DocumentMap
→ 按自然评分大项构建 ScoreSemantic 批次
→ 评分语义 LLM 拆解满分 ScoreCondition / ScoreResponseUnit
→ 汇总全部已校验评分理解
→ BidAgent 内部版本化章节目录拆分 Skill
→ ChapterBlueprint Candidate
→ exact Proposal Validation
→ G2
→ H1 PlanningConfirm
```

内部目录 Skill 是 `Planning Agent` 的受控 Provider，不是外部插件或新的 Agent。它只读取
冻结的评分理解汇总、关联采购需求原文、可选模板结构，只输出强类型章节
Candidate/Proposal，并将 Skill ID、版本、Prompt checksum、模型、温度、输出 Schema
和 policy version 纳入 fingerprint。它不读取整份标书正文。

轻量 `DocumentMap` 只提供标题层级、评分组顺序、采购需求位置、模板目录位置、来源 ID
和每个标题的边界块，不枚举标题下全部正文。任何一次模型调用都不得以“项目整体理解”为
由发送全部 `SourceBlock`。

## 评分拆解规则

1. 只从正式评分文件，或招标文件中边界明确的评分章节提取分值。
2. 资格审查、符合性审查、保证金、密封和废标条件属于合规义务；没有明确赋分时不生成
   普通 `ScorePoint`。
3. 一个来源可定位的物理评分规则对应一个 `ScorePoint`；同一规则中的优秀、良好、一般
   等得分档次保存在 `scoring_levels`，不得拆成多个评分点。若一个物理规则实际包含多个
   可独立组织响应或独立得分的任务，则必须拆成多个 `ScoreResponseUnit`，每个 Unit
   精确绑定自己的满分 `condition_id`。
4. 保存评分组、评分因素层级、分值、最高档原文、`ScoreResponseUnit`、最高档原子
   条件、证明材料要求和来源锚点。每个原子条件必须绑定实际 `SourceBlock` 的精确字符
   区间；评分语义确定性校验对最高档原文做无损覆盖复核，不能用剩余条件的闭合 ID 集合
   掩盖原文遗漏。
   `ScoreResponseUnit.source_excerpt` 仅是可读的语义说明，不是权威来源字段；不得把它
   误当作逐字原文而阻断。Unit 的权威可追溯性来自 `source_level_ids` 及其
   `condition_id` 的精确来源锚点。
5. 所有小计和总分必须守恒；任何丢分、重复计分或来源不明均阻断评分语义晋级。

## 从评分点反推目录

评分点名称、父级评分因素、`ScoreResponseUnit` 和满分 `ScoreCondition` 共同决定上层
响应标题；最高得分档中的并列实质要求决定必须覆盖的下级标题。具体标题层级和语义表达
由内部目录 Skill 在看到全部已校验评分理解后统一生成，不得由固定字符串模板或
`RequirementKind` 规则替代。下级标题不是凭常识补写，而是对最高档要求进行有来源、
可追溯、无损的语义压缩。

例如：

- 评分因素：`目标任务（4分）`
- 满分要求：`项目任务背景描述清楚，工作必要性和可行性理由充分、逻辑清晰；工作目标明确、可行，工作内容具体、翔实`

应生成：

1. 目标任务
   1. 项目任务背景
   2. 工作必要性与可行性
   3. 工作目标
   4. 工作内容

四个下级标题的写作目标分别保留对应评分原句，从而保证整个 4 分评分点完整覆盖。不得只
生成“目标任务”一个空泛标题，也不得把整段评分标准仅塞进提示词而不体现在目录中。

若评分表具有“技术方法 → 核查准备 → 数据接收检查”之类父子因素，则评分语义 LLM 必须
在 `ScorePoint.outline_path` 保留该层级，内部目录 Skill 必须在
`ChapterBlueprint` 中复用共同父节点，避免把所有评分叶子机械铺平成同级章节。

目录深度由“评分因素父链 + 满分原子条件”递归决定。评分表已给出的末级因素就是对应
`ScoreResponseUnit` 的主责章节，不得在末级因素与满分条件之间再插入一个由模型改写
出来的“独立任务”层。例如评分表为“技术方法（43分）→ 核查准备工作（6分）”，应生成：

1. 技术方法（43分）
   1. 核查准备工作（6分）
      1. 核查准备工作
      2. 数据接收内容
      3. 检查方法
   2. 年度全国国土变更调查成果国家级内、外业核查质量控制检查和成果复核（31分）
      1. 变更图斑正确性检查分析
      2. 核查样本影像分类方法
      3. 使用说明
      4. 易混淆类型实例

因此，上例中“技术方法”只能出现一次；“核查准备工作”和“年度全国国土变更调查……”
是它的同级子标题，各自的满分原子条件直接位于对应子标题下。如果评分表本身还有更深
父级，则继续按来源层级展开，不能用固定最大标题级数截断。

## 语义生成与规则校验的职责边界

- 确定性解析器负责恢复评分组、物理评分点、同一点全部评分档、合并单元格继承关系、
  分值、来源位置和轻量 `DocumentMap`；同一评分点的不同档次不得拆到不同批次。
- 批次第一层边界使用价格、商务、技术等自然评分大项。技术大项超限时，先按技术评分表
  内部小标题切分；仍超限时只能在完整 `ScorePoint` 边界切分。
- 每批输入最多使用模型上下文预算的 45%，至少为结构化输出预留 35%，系统提示词和安全
  余量保留 20%。超限时先删除低相关检索内容，再按完整评分点切批；单个完整评分点仍
  超限则 fail closed。
- 上下文补充依次使用评分点明确引用的章节/条款、已绑定采购需求、同标题要求、关键词
  命中要求和少量高相关检索块。每条补充必须携带真实 `requirement_id`、来源位置和原文；
  通用合同、保证金等噪声不得仅凭相似度进入，明确引用除外。
- 评分语义 LLM 负责为每个独立得分任务生成 `ScoreResponseUnit`，并把最高得分档拆成
  `content`、`evidence`、`constraint`、`quality`、`document` 五类
  `ScoreCondition`。`quality` 是写作目标，不能机械变成“完整性”“合理性”等空洞章节。
- 内部目录 Skill 只接收全部已校验的评分理解、关联需求和可选模板，负责跨评分点合并
  重复响应主题、按评审阅读顺序生成项目专用多层章节树，并保留 Unit、Condition、
  Requirement 到 `ChapterNode` 的映射。
- 规则只负责来源/ID/引用、分值守恒、权限、依赖、父子树、覆盖、模板结构、
  canonical ID/顺序和 Gate 校验；不得用字符相似度判断模型归纳文字是否“像原文”，也
  不得继续主导目录语义。
- 规则不得通过自动追加“补充评分响应”、把缺项塞入最后章节、生成空泛兜底章节或复制
  RequirementKind 目录来掩盖 Skill 输出缺口。

### 评分语义的受控修复边界

每个自然评分大项或技术子批次独立调用、独立校验并立即按 exact 输入 fingerprint
持久化缓存。价格或商务批已通过后，技术批失败不得重跑已通过批次；目录生成失败也不得
重新理解评分点。缓存只允许在批次内容、模型、Prompt、Schema、policy 和上游依赖完全
一致时复用，损坏或过期缓存必须表现为可观测 miss。

每批必须按单个物理 `rule_id` 独立执行来源、档次和最高档条件校验。若只有部分规则
不合格，唯一一次受控修复只携带失败规则、必要评分组和精简失败片段；已通过规则直接
复用。修复后仍为空、引用未知 ID、伪造原文区间或跨规则唯一性冲突时 fail closed，不得
由程序臆造缺失的满分条件。

## 全文评价项

“投标文件整体评价”“响应文件编制质量”等全文作用域的 `ScoreResponseUnit` 保留
分值、来源、condition 和唯一质量门责任，但不生成名为“整体评价”的空洞正文章节。
它们进入 `ChapterBlueprint.document_quality_gates`，由后续写作、整合和质量检查消费，
用于全文的：

- 内容完整性；
- 逻辑与层次；
- 格式、目录和排版规范；
- 图表与正文一致性；
- 数据、术语和承诺前后一致性；
- 项目针对性和可操作性。

## 评分语义校验与 G2 阻断条件

评分语义确定性校验至少阻断：评分来源非法、评分点无来源、分值不守恒、非否决评分点
未完成理解、最高得分档没有满分条件、`source_excerpt/source_span` 不能在真实
`SourceBlock` 中复核、Requirement 异常绑定，以及未知、重复或遗漏 ID。

G2 至少阻断：

- 每个 section 型 `ScoreResponseUnit` 没有且仅有一个 primary `ChapterNode`，或同一
  Unit 被多个主责章节重复绑定；
- 每个 `content/evidence/constraint` 满分 `condition_id` 没有落入所属 Unit 的
  primary 章节子树；
- `quality` 条件没有进入对应章节写作要求；
- `document` 条件没有进入全文质量门；
- 同一物理 `ScorePoint` 中的全文 Unit 与章节 Unit 被错误地整体归为全文质量门；
- 全文评价项被错误生成独立章节，或没有全文质量门承接；
- 评分组分值没有保留在章节路径；
- Blueprint 与评分语义批次汇总的 revision、fingerprint 或来源 hash 不一致；
- 悬空父节点、父子环、未知 Unit/Condition/Requirement，或重复/遗漏映射。

领域校验必须由 `ProposalValidator` 对 Store 中的 exact Proposal 重新执行；只在
`PlanningAgent` 或 `StageRunner` 中预检不构成可信门禁。

模型不可用、内部 Skill 调用失败、输出不符合 Schema、引用未知 ID、评分语义不完整或
G2 不通过时必须 fail closed。禁止静默回退到规则拼装目录、`ProjectModel`、
`ResponseTopicGraph/ResponseDuty` 或 legacy `ChapterBlueprint` 并将其作为正式目录
返回。legacy 只能通过显式兼容入口运行，且必须使用独立 fingerprint/Receipt，不能冒充
当前自动主链。确定性 baseline 只能用于测试、显式 shadow 或离线对照；部署环境变量
无权开启测试规则链，测试必须显式注入专用 deterministic harness。

## 项目隔离

历史技术标只允许提供结构、方法和表达参考，不得成为当前评分事实来源。项目名、年度、
包号、区域、数量、工期、人员和业绩必须来自当前工作空间的正式输入或经核验企业证据。
更换招标文件或评分文件后，旧 `ScoreModel`、各 `ScoreSemantic` 批次缓存、评分理解汇总
和 `ChapterBlueprint` 必须因依赖 hash 变化而失效，不能静默复用。

内部 Skill、Prompt、模型、温度、Schema 或 policy version 变化时，也必须通过
fingerprint 使受影响 Proposal/Artifact stale；若变化影响 H1 确认范围，则必须重新人工
确认，不能复用旧目录或旧确认票据。
