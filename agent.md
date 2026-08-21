# Agent 工作约定

## V3 主架构基线（强制）

凡涉及 V3 的 Agent、语义解析、章节规划、证据、写作、全文整合、质量审核、Artifact、工作流或权限设计，必须先完整阅读：

- [V3 Bid Master Agent 与投标中间语言详细开发计划](./docs/v3_semantic_understanding_and_outline_development_plan.md)

本节是仓库级架构约束，不是建议。不得以“临时实现”“兼容旧逻辑”“先跑起来”或“开发方便”为由绕过。若任务与本节冲突，必须停止实现，明确指出冲突，并在获得用户明确批准、同步修改本文件和对应 ADR/开发计划后才能继续。

### 1. 核心分工

必须始终保持：

```text
Agent    → Decision / Proposal
Service  → Deterministic Execution
Artifact → Versioned Truth
Gate     → Validation and Promotion Decision
```

**V3 总原则：任何新增 Agent 能力，都必须证明自己不能通过绕过 Artifact/Gate/Promotion 获得额外权限。**

该原则适用于当前和未来的全部 Agent，包括但不限于行业分析、搜索、图表、格式、研究、审查和本地模型 Agent。证明必须来自代码权限、调用契约和负向测试，不能只依靠 Prompt 中的文字承诺。

- Agent 是决策者，只读取冻结快照，只输出 `Proposal`、`Finding` 或 `RepairRequest`。
- Service 是执行者，负责解析、校验、索引、装配、状态投影、CAS 晋级和渲染。
- Artifact 是唯一运行时事实载体；Agent 对话、Prompt 输出、缓存和 Vector Store 都不是事实源。
- Gate 决定 Proposal 是否可以晋级，Agent 和 LLM 无权自行宣告“通过”或“已发布”。

### 2. 唯一投标中间语言

V3 权威语义链固定为：

```text
InputManifest / SourceIndex
→ RequirementLedger
→ ScoreModel
→ ProjectModel（派生摘要与全局约束）
→ ResponseTopicGraph / ResponseDuty
→ ChapterBlueprint
→ EvidenceSnapshot
→ WriterInputBundle
→ ContentBlock
→ IntegratedDocument
→ AuditReport / FinalGateReceipt
→ Renderer
```

必须遵守：

- `RequirementLedger` 保存采购义务；`ScoreModel` 保存评分逻辑，两者通过 ID 引用，不复制互相冲突的事实。
- `ResponseTopicGraph` 是中央响应语义层，统一承载功能、架构、数据、安全、实施、服务、交付、验收、资格、商务和合规主题。
- `FeatureModel` 只能是 `topic_type=function` 子图的派生 View，不得恢复为独立中央权威。
- Topic 不直接粗暴映射多个章节；必须通过 `ResponseDuty` 表达不同响应责任。
- 每个核心 Duty 恰好有一个 primary chapter；其他章节只能是 supporting、mention 或 cross-reference。
- `ProjectModel` 是上游 Artifact 的受控投影，不得复制成另一套可独立修改的事实库。

不得新增一套与上述对象并列、语义重叠的“方便型”中间 JSON。

### 2.1 规划语义链与内部推理 Skill

在上述权威 Artifact 链不变的前提下，规划阶段的目标推理链固定为：

```text
评分语义 LLM
→ 项目整体理解 LLM
→ Topic / Duty 语义规划 LLM
→ BidAgent 内部版本化章节目录拆分 Skill
→ G1 / G2
→ H1 PlanningConfirm
```

这里的“内部版本化章节目录拆分 Skill”是 `Planning Agent` 的受控推理
Provider，不是 Codex `SKILL.md`，也不是面向用户的外部 Bid Master Skill。它必须读取冻结
Artifact 快照，只输出强类型 Candidate/Proposal，并将 Skill ID、Skill 版本、Prompt
checksum、模型、温度、输出 Schema 和 policy version 纳入 fingerprint。

职责边界必须明确：

- LLM 和内部推理 Skill 负责评分语义、项目整体理解、Topic/Duty 聚合以及项目专用目录的
  语义决策。
- 确定性规则负责来源/ID/引用校验、分值守恒、权限、依赖、树结构、覆盖率、模板结构、
  canonical ID/顺序编译和 Gate 判定，不得冒充语义规划器。
- 内部目录 Skill 的输出必须经过 Proposal、exact Validation、G1/G2 和 Promotion；模型或
  Skill 无权自行发布 `ProjectModel`、`ResponseTopicGraph` 或 `ChapterBlueprint`。
- 模型不可用、Skill 调用失败、输出不符合 Schema、引用未知 ID、缺少满分条件标题或无法
  满足 G1/G2 时必须 fail closed。禁止静默回退到规则拼装的项目理解、TopicGraph 或目录并
  将其当作正式规划结果。
- 确定性 baseline 只允许用于单元测试、离线对照或显式标记的 shadow 评测，不得通过
  `document.prepare_outline` 冒充已经执行了上述 LLM/Skill 链。

### 3. Proposal 与 Artifact 晋级

所有 canonical Artifact 必须经过：

```text
Proposal
→ Schema / Reference / Authority Validation
→ Domain Gate / 必要的 Human Gate
→ ArtifactPromotionService
→ Promoted Revision
```

硬约束：

- Agent、LLM 和普通 Service 不得直接写 canonical Artifact、active revision 或 `control.db` 业务状态。
- 下游只能读取已 promoted 的明确 revision，不能读取 draft、rejected、needs_human 或 stale Proposal。
- Artifact 即使物理格式为 JSON，也必须是强类型、带 Schema、ID、revision、producer、source hash、dependency fingerprint 和 Receipt 的版本化对象，不能作为普通可变 JSON 使用。
- Artifact 历史采用 append-only；active revision 只能由 `ArtifactPromotionService` 通过 CAS 原子切换。
- 无有效 `ValidationReport` 和 `GateReceipt` 不得晋级。
- 陈旧 `base_revision`、失效 dependency fingerprint 或重复 operation 不得晋级。
- Promotion、active revision 更新和 `PromotionReceipt` 写入必须原子化。
- 任何失败或进程中断不得留下半有效 revision。
- Snapshot、API、前端和后续 Agent 均不得把未晋级 Proposal 投影为当前事实。
- Validator 只能按 `proposal_id` 从 append-only Store 重新加载 exact Proposal；不得验证调用方另传的同 ID 不同 payload。
- `ValidationReport` 必须绑定 `proposal_id`、`proposal_hash`、canonical payload hash、resolved dependency snapshot/fingerprint 和 validator/schema/policy version。
- `GateReceipt` 必须内容寻址，并绑定 `receipt_id/hash`、workspace、exact Proposal hash、ValidationReport `id/hash`、artifact kind、base revision、resolved dependency snapshot/fingerprint、Gate policy version、合法 issuer、签发时间及过期时间或永久失效策略。
- `PromotionReceipt` 必须绑定 workspace、base/promoted revision、Artifact hash、完整 resolved dependency snapshot，以及每个 GateReceipt 的 `id + immutable hash`；不能只保存可替换的 Receipt ID。
- Promotion 必须从 Store 重算 Proposal hash 和 active 依赖，并按 `GatePolicyRegistry` 校验该 artifact kind 所需的完整 Gate 集合；任意一个名为 `pass` 的 Receipt 不够。
- 未知 finding、未知 policy、`block`、`needs_human`、过期 Receipt 或无权限 reviewer 的降级决定一律 fail closed。
- dependency fingerprint 必须由 Validator/Promotion 从 active promoted Artifact 独立计算；producer 提供的值只能用于比对，不能自证。
- 引用必须解析到同 workspace、正确 artifact kind、明确 promoted revision/hash；draft、悬空、跨 workspace、类型错和无权限引用一律 fail closed。
- `InputManifest`、`SourceIndex` 和 `TemplateStructureContract` 属于 canonical Artifact，必须强类型、append-only 并经过 Validation/Gate/Promotion；普通 JSON 只能是序列化或可重建投影。

### 4. Bid Master 与 Agent 权限

- `Bid Master Agent` 是唯一顶层编排者，但必须复用现有 `CommandGateway`、`StageRunner`、`ControlStore`、Goal 和 Budget。
- 禁止新增 LangGraph、第二个 Supervisor、第二套 Runner 或另一套 observe/plan/tool 状态机。
- 专业 Agent 不直接互相调用；协作只通过 Bid Master、Command 和 promoted Artifact。
- Agent 默认无 canonical Artifact 写权限、数据库写权限、任意文件读取权限和未声明的外部工具权限。
- Agent 必须是无状态或短生命周期角色；必要状态必须写入 Proposal/Artifact，不能依赖隐藏会话记忆。
- 不得按 Requirement、ScorePoint、Topic、章节或模板节点数量线性创建 Agent。
- 未经架构评测、ADR 和用户明确批准，不增加新的长期 Agent 角色。
- 新 Agent 即使使用不同模型、工具或运行环境，也只能替换受控推理模块，不能获得新的权威写入路径。
- `Planning Agent` 可以通过静态注册、版本化、强类型的内部推理 Skill 获得专业语义能力；
  该 Skill 仍属于 `Planning Agent` 的能力实现，不构成新的长期 Agent 角色，也不得建立
  第二套编排器、状态机、Artifact 或 Gate。

V3 目标逻辑角色固定为：

```text
Bid Master
├─ Requirement Agent
├─ Score Agent
├─ Planning Agent
├─ Writer Agent
├─ Integration Agent
└─ Quality Audit Agent
```

这些是能力边界，不要求全部同时常驻。必须按 Phase 分步实现。

### 5. ChapterBlueprint 与 Writer 边界

- `ChapterBlueprint` 是 Writer 唯一的章节结构、章节目的和响应责任来源。
- `DocumentContract` 和 `DocumentPlan` 只能在有效 H1 后由 ChapterBlueprint 确定性编译，必须携带 `source_blueprint_revision/hash`；它们不是第二套可编辑规划。
- Writer 的唯一调用参数是冻结、最小充分并带 hash 的 `WriterInputBundle`。
- `WriterInputBundleAssembler` 是 Service；它从已确认 Blueprint、Topic/Duty、Requirement、Score、EvidenceSnapshot、术语和全局约束编译 Bundle。
- Writer 不得读取整份招标文件、SourceIndex、整个 ProjectModel、整个 EvidenceRepository、任意工作区文件或其他 Agent 的自由文本。
- Writer 不得绕过受控研究服务自行联网、搜索、调用外部 Provider、上传文件、创建标题、移动主责、增加 Topic 或扩大响应范围。
- Writer 只能引用 Bundle 中存在的 target、Requirement、Score、Topic、Duty、Fact 和 Evidence ID。
- 缺证据时只能输出 `EvidenceNeedProposal`；发现规划问题时只能输出 `PlanIssueProposal`。
- Writer 只输出 `ContentProposal`，通过内容 Gate 后才能晋级为 `ContentBlock`。
- 任一上游 revision、Prompt、模型配置或 dependency fingerprint 变化后，对应 Bundle 和 ContentProposal 必须 stale。

### 6. Evidence Layer

- `EvidenceRepository` 是统一证据治理层，必须保存证据版本、来源、authority class、claim scope、有效期、访问策略、Binding 和验证状态。
- 企业资质、企业案例、产品资料、招标原文、官方标准、公开网页和历史标书必须使用不同 authority class。
- 企业能力只能由允许的 company/product Evidence 支持。
- 官方标准、Tavily 网页研究结果和历史优秀标书不得证明本企业资质、案例、人员、产品实绩或关键承诺。
- 历史优秀标书默认只能作为结构、表达和方法参考。
- Vector Store 只索引 promoted EvidenceRecord 和 Artifact 引用，可随时重建，不能作为事实源。
- Writer 不得自行查询 EvidenceRepository；只能读取 Bundle 内冻结的最小 EvidenceSnapshot。
- Tavily 是唯一允许的公开资料 Research Provider，只能由显式 EvidenceNeed 或章节 WritingPlan 触发，不是 Agent 或招标权威解析器。
- Tavily 研究不支持附件；任何 `attachment_input_ids` 或外部文件上传请求必须拒绝。
- Evidence 变化应只使受影响的 Snapshot、Bundle 和 ContentUnit stale；除非改变规划责任，不得无条件要求重审 Blueprint。

### 7. Integration 与 Quality Audit

全文整合必须分层：

- `DocumentIntegrationService` 负责顺序装配、Slot 校验、锁定块保护、显式交叉引用和完全相同内容 hash 的确定性处理。
- `Integration Agent` 只判断跨章语义重复、术语/数字/产品/承诺冲突，并输出 IntegrationProposal 或受限 RepairRequest。
- 禁止因两个 ContentBlock 共享 `requirement_id` 就自动删除其中一个；primary 和 supporting 内容可能引用同一 Requirement。
- 任何语义移动、合并、删除或重写必须有 Proposal 和 rewrite trace。
- `Quality Audit Agent` 必须使用独立、只读、冻结上下文，只输出 `AuditFinding`。
- Audit Agent 不得直接修改 ContentBlock、IntegratedDocument 或最终 Word。
- Finding 必须按类型路由回 Requirement、Score、Planning、Evidence、Writer 或 Integration 责任方。
- 未关闭的 critical Finding 必须阻断 Renderer 和正式交付。

### 8. Gate 与人工确认

- 正文前只新增一个必经人工 Gate：`H1 PlanningConfirm`。
- H1 一次确认项目摘要、关键 Requirement/Score 异常、Topic/Duty、ChapterBlueprint、主责映射和模板缺口。
- H1 是暂停点，不是自动 Stage。运行时 Receipt verdict 为 `needs_human`，`run_pipeline`/StageRun 对外状态为 `blocked_human`；StageRunner、Agent、Service 和脚本不得代签。
- 只有已认证用户通过显式 `ConfirmPlanning` Command 对冻结规划快照作出决定，HumanGateService 才能签发 H1。
- H1 Receipt 必须绑定用户 principal、workspace、promoted ChapterBlueprint `artifact_id/revision/hash`、G2 Receipt `id/hash`、完整规划审计快照 hash、`planning_confirmation_scope_hash`、完整 Artifact DAG/root、policy version、nonce、decision 和时间；完整依赖快照至少包含 InputManifest、SourceIndex、RequirementLedger、ScoreModel、ProjectModel、ResponseTopicGraph、ChapterBlueprint，以及适用的 TemplateStructureContract 的 `artifact_id/revision/hash`。源 Proposal hash 只能作为追踪信息，不能代替 promoted Artifact 身份；`reviewer="user"` 字符串不是身份。
- 任何上述依赖 revision/hash 变化都必须触发 H1 适用性重评。H1ApplicabilityService 必须先证明整个下游 DAG 对当前 InputManifest/SourceIndex 仍 current；若新 G2 有效且重新计算的 `planning_confirmation_scope_hash` 相同，才可签发绑定新 exact snapshot 的确定性 carry-forward Receipt。carry-forward 必须引用原始人工 H1 `id/hash`、原确认用户、旧/新 DAG root 和算法/policy version，不得伪装成新的人工决定；scope hash 变化时旧 H1 必须 stale 并重新人工确认。旧 Receipt 自身不可改写。
- Evidence、DocumentPlan、Bundle 和 Writer 每次消费时必须验证当前 exact snapshot 对应的 H1 或 carry-forward Receipt，不得只检查同名 Gate 是否曾存在。
- 不得分别增加 Requirement、ProjectModel、Topic、Blueprint、Evidence、ContentUnit 等重复固定确认门。
- OCR 不可用、缺企业材料、外部附件授权、证据冲突和审核不收敛属于条件性阻断，不等于增加固定 Gate。
- 只有 blocking Requirement/Score、核心 Topic/Duty、标题层级、primary owner、模板目标或全局范围/工期/交付/验收变化才使 H1 失效。

### 9. 模板与 Renderer

- 严格模板模式必须先生成只读 `TemplateStructureContract`，再进行 Blueprint 映射。
- 模板标题、级别、顺序、编号、父子关系和 Slot 未经用户明确授权不得变化。
- 模板无承载位置时必须产生 `TEMPLATE_MAPPING_GAP` 并阻断，禁止自动追加章节。
- Renderer 只能消费 promoted `DocumentContract`、`IntegratedDocument` 和有效 FinalGateReceipt；运行时 FinalGateReceipt 只授权生成候选 DOCX，不证明该产品版本已经通过真实标书可用性验收。
- Gate U 的候选 DOCX 必须在冻结的 Renderer、字体和操作系统环境中逐页渲染，绑定 DOCX hash、页数、每页图像 hash 和逐页 Finding；DOCX 或渲染环境变化后，旧页面审核自动失效。
- Agent 不得直接生成、编辑或修补最终 Word。

### 10. 开发顺序

必须按价值和依赖顺序推进：

```text
PR-14：冻结中间语言、架构约束和 Golden Set
PR-15：建立 Proposal/Validation/Gate/Promotion 可信运行内核

可信内核完成后，才进入 Agent 能力建设：
Phase 1：Requirement + Score + Topic/Duty + ChapterBlueprint
Phase 2：EvidenceRepository + EvidenceSnapshot
Phase 3：WriterInputBundle + ContentBlock Writer
Phase 4：Integration + Quality Audit
Phase 5：真实标书盲测 + Gate U + Gate M 灰度切换；外部 Bid Master/Codex Skill 仅作为可选入口
```

- 当前 PR-14～PR-20 的收口顺序固定为：首批 PR-14.0 最小冻结包 + PR-15.1 exact binding/Gate policy → PR-16.1 canonical Source → PR-14.1 Golden 正式 baseline → PR-17.1～PR-19.1 语义校准 → PR-20.1 真实 H1/Blueprint 派生契约。
- `Gate K/S/A/P/B/U/M` 是仓库发布验收门，不是工作空间运行时 GateReceipt。Gate P 通过前，不进入 PR-21 及后续生产实现；不消费未验收 Artifact 的接口、Schema 和测试设计可以并行准备。PR-23 实现实际 Bundle 编译后还必须通过 Gate B，才能启动 PR-24 正式 ContentBlock 生成。
- Gate U 是真实标书可用性硬门，必须在 PR-24～PR-27 的完整 staging 链、独立盲测和最终 Word 逐页验收后通过。Gate U 未通过时，任何输出只能标记为 `test_draft`，不得宣称为正式标书、不得启用生产写路径。
- Gate M 是 PR-27 内生产切换的最后硬门，其证据清单必须依赖已通过的 exact Gate U `id/version/hash`；只有 Gate U 与 Gate M 均获批后，Release Service 才能 CAS 切换 production active revision，不能先切换再补门。
- 任何历史运行统计或合成压力参数都不得升级为业务规模、Golden 数量或发布阈值。历史 `92` 个评分点、`1198` 次绑定、`S036` 绑定 `131` 次只描述旧链路异常；历史 `198` 只描述旧链路创建的独立章节任务数，不能据此断言真实模板具有 `198` 个 canonical 节点。
- 语义验收必须以逐项目、双人复核的人工 Golden 为准；模板结构与 Writer 扇出必须使用独立、明确标注为 synthetic 的多档压力夹具验证。评分语义样本和模板结构压力夹具不得合并成固定的“92/198 样本”。
- 不得先做复杂 Writer，再补 Requirement/Topic/Blueprint。
- 不得先创建外部 Bid Master/Codex Skill，再把其中的 Prompt 和状态复制回产品。
- 外部 Bid Master/Codex Skill 不在核心生产链上，只能调用 Bid Master 公开 Command。
- Web/API 在没有任何外部 Skill 时必须具备完整产品能力。
- 上述限制不排斥 `Planning Agent` 使用 BidAgent 内部版本化推理 Skill；内部 Skill 属于
  产品运行时 Provider，必须遵守本文件的 Proposal/Artifact/Gate/Promotion、权限和
  fingerprint 约束。

### 10.1 真实标书可用性与 Gate U

- Gate U 使用未参与 Prompt、模型、规则、Golden 或阈值调优的匿名真实项目盲测集；Golden-A～D 通过不能替代 Gate U。
- Gate U 是离线仓库发布验收门，不增加第二个运行时人工确认点，不改变“正文生成前只有一个 H1 PlanningConfirm”的产品约束。
- Gate U 只批准证据包中明确声明的 `supported_bid_profiles`。每个拟上线的输入类型、评分复杂度、模板模式、附件组合和 OCR 能力分支都必须由预先冻结的覆盖矩阵命中；未覆盖或超出能力边界的项目必须显式阻断。
- 每次盲测必须生成内容寻址的 `UsabilityRunManifest`，绑定输入 hash、Evidence 快照、全部 promoted Artifact revision/hash、Prompt/模型/规则/Renderer 版本、最终 DOCX hash、逐页渲染 hash、审计 Finding、专家意见和人工编辑记录。
- 每份盲测标书分别验收，禁止用跨样本平均分掩盖单份 blocking 失败。强制/资格/废标/critical Score 漏项、无证据企业 Claim、跨章硬事实冲突、未关闭 critical Finding、模板未授权变化和 blocking 页面缺陷必须全部为 0。
- 两名投标专家必须独立盲审；争议由第三名专家裁决。单份标书专家可用性平均分必须 `≥4.2/5` 且任一维度 `≥4.0/5`，无需实质性重写的 ContentUnit 比例必须 `≥80%`，实质性重写比例必须 `≤20%`，critical 主责章节整章推倒重写必须为 0。
- 最终 Word 必须逐页渲染检查目录、标题编号、表格、图片、页眉页脚、交叉引用、分页、裁切、溢出和空白占位；JSON、单元测试、Golden-C/D 或运行时 G6 均不能替代页面验收。
- 发布候选、覆盖矩阵、人工编辑 taxonomy 和阈值 policy 必须在 holdout 揭盲前冻结并绑定 hash，满足 `policy.frozen_at < holdout.unsealed_at < first_run_at`。原始机器输出先归档，禁止揭盲后调阈值、改 Prompt、挑选最佳重跑或手工修补后冒充原始结果；失败必须形成新候选并使用尚未消费的 holdout。
- Gate U 不替代每个工作空间的运行时 G0～G6/H1；它只证明特定版本在声明的生产范围内可发布。影响语义、Evidence policy、Writer、Integration、Audit、Prompt、模型、Renderer 或模板适配器的变化必须使对应 Gate U scope stale 并重新盲测。
- Gate U 证据包固定保存于 `artifacts/release_gates/v3/U/<version>/`，批准角色为投标领域负责人、质量负责人和产品负责人，三者必须是不同的认证 principal。Gate M 不得豁免、降级或补偿 Gate U。

### 11. 强制测试与架构审查

任何涉及上述架构的 PR，至少必须证明：

1. Agent 直接写 canonical Artifact 会失败。
2. 无 GateReceipt 的 Proposal 无法晋级。
3. 陈旧 base revision 无法晋级。
4. 相同 operation 不会重复发布。
5. Writer 无法读取 Bundle 外对象或调用未授权工具。
6. 外部/历史证据无法支持企业能力 Claim。
7. Audit Agent 无法修改正文。
8. supporting 内容不会因共享 Requirement ID 被误删。
9. 模板结构未授权变化为 0。
10. 进程中断不会留下半有效 revision。
11. 下游只读取 promoted revision。
12. 输入、Prompt、模型、Evidence 和 Blueprint 变化能够精准 stale。
13. 新增 Agent 无法获得 Artifact/Gate/Promotion 之外的权威写入路径。
14. 同一 `proposal_id` 替换 payload、kind、revision 或依赖后，旧 Validation/Gate 不能晋级新内容。
15. Proposal A 不能复用 Proposal B 的 ValidationReport 或 GateReceipt。
16. 未注册 Gate、错误 issuer、旧 policy、错误 artifact kind 或缺少必需 Gate 时 Promotion 失败。
17. producer 伪造或自证 dependency fingerprint 时验证失败，上游变化后旧 Proposal 在 Promotion 前再次失败。
18. `run_pipeline` 必须在 H1 暂停；自动、匿名、伪造 reviewer、跨用户/workspace 和 stale snapshot 确认全部失败。
19. 直接修改 InputManifest/SourceIndex/TemplateStructure JSON 不改变权威事实，下游只读取 promoted Source revision。
20. 无 Blueprint、无有效 H1、旧 DocumentPlan 或 Blueprint 外标题不能驱动 Writer。
21. Gate U 未通过时只能生成 `test_draft`，不能切换生产写路径或宣称正式标书。
22. Golden、训练/调优样本或开发人员自评不能冒充 Gate U 独立盲测。
23. 任一盲测项目出现 blocking 失败时 Gate U 必须失败，不能被总平均分掩盖。
24. Prompt、模型、Writer、Integration、Audit 或 Renderer 等决策敏感版本变化后，旧 Gate U scope 必须 stale。
25. Gate M 缺少已通过的 exact Gate U `id/version/hash` 依赖时不得批准生产 CAS。
26. Gate U 的 threshold policy 必须早于 holdout 揭盲冻结，盲测样本不得与 Golden、调优集或已消费 holdout 重叠。
27. DOCX 可打开、文件存在、段落非空或运行时 G6 PASS 不能冒充逐页审核覆盖率 100%。

提交实现前必须在设计说明或 PR 描述中明确：

- 本次 Agent 的只读输入和唯一输出；
- 新增或消费的 Artifact；
- Proposal 类型；
- Validator 和 Gate；
- Promotion 路径；
- dependency fingerprint；
- stale 传播范围；
- 权限边界及负向测试；
- 是否影响 H1 PlanningConfirm；
- 是否影响严格模板结构；
- 是否影响 Gate U 的 `supported_bid_profiles`、盲测结果、人工改写率、最终 Word 页面质量或失效范围；
- 新增 Agent 如何证明无法绕过 Artifact/Gate/Promotion。

以下情况一律按 P0 架构违规处理：

- 绕过 Promotion 直接写 active Artifact；
- Validation/Gate 只绑定 `proposal_id` 而未绑定 exact proposal hash；
- 接受任意 pass Receipt，而未按 artifact kind 验证 Gate policy 和必需 Gate 集合；
- 使用 producer 自己提交的 dependency fingerprint 作为唯一真实性依据；
- StageRunner、Agent、Service 或脚本冒充用户自动签发 H1；
- SourceIndex、TemplateStructureContract 或 DocumentPlan 作为普通可变 JSON 进入跨阶段权威链；
- Writer、DocumentContract 或 DocumentPlan 绕过有效 H1/Blueprint 设计章节结构；
- Agent 直接写数据库或最终 Word；
- Writer 读取整标或自行联网；
- 未授权外发文件；
- 外部资料冒充企业能力；
- 未关闭 critical Finding 仍交付；
- Gate U 未通过却宣称正式标书、复用旧 Gate U 证据或执行生产切换；
- Gate M 未绑定已通过的 exact Gate U 仍批准 production CAS；
- 使用 Golden/开发样本冒充 holdout、揭盲后调低阈值、挑选最佳重跑或手工修补后冒充原始盲测输出；
- 用 DOCX 存在、可打开、非空段落或 JSON 正确代替最终 Word 逐页验收；
- 引入第二套权威状态或隐藏 fallback；
- 为方便重新引入普通可变 JSON 作为跨阶段事实。

每次修改代码后，需要完成以下收尾动作：

1. 运行必要的检查或测试，确认改动可用。
2. 使用 `git status` 确认待提交文件。
3. 将本次相关改动提交为一个清晰的 git commit。
4. 将提交 push 到远端仓库。

如果某一步无法完成，需要在最终回复中说明原因和剩余状态。

## 注意事项

- 修改 `frontend/` 下的 Vue 源代码后，**不需要**运行 `npm run build`。由开发者自行构建。
