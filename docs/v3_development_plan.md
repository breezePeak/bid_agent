# 标书 Agent V3 详细开发计划

> 状态：PR-0～PR-13 历史实施基线；后续编号和收口计划已迁移
> 日期：2026-07-26
> 总体方案：[current_logic_flow_v3.md](./current_logic_flow_v3.md)
> 当前基线：[current_logic_flow_v2.md](./current_logic_flow_v2.md)
> 当前权威计划：[v3_semantic_understanding_and_outline_development_plan.md](./v3_semantic_understanding_and_outline_development_plan.md)

本文件保留 PR-0～PR-13 的历史建设背景。原草案中的“PR-14：迁移、真实项目验收与发布”未作为活动 PR 实施，其职责已拆入当前权威计划的 PR-14～PR-20 收口发布门和 PR-27/Gate M。自 PR-13 以后，PR 编号、完成状态和进入条件均以当前权威计划与 [v3_implementation_log.md](./v3_implementation_log.md) 为准，禁止继续沿用本文件的旧 PR-14 编号。

## 1. 计划目标

本计划用于把当前项目从“V2 控制面 + V1/V2 混合内容链”迁移到 V3。

最终结果必须同时满足：

1. 只有一个工作空间控制面、一个 Pipeline 执行内核和一个权威状态源。
2. 内容生产由项目模型、要求台账、文档结构契约和全文责任计划驱动。
3. 有模板严格原位填充，无模板根据标书生成标题。
4. 资料检索可以在任意内容阶段按需触发。
5. 写作单元按语义和依赖组织，不再为每个标题创建独立 Agent。
6. 全文审核能够真实删除、合并和重写重复内容。
7. V1 文件状态、兼容 API、旧 Runner、LangGraph Pipeline 和被替代的 V2 内容模块全部移除。
8. V3 上线后不存在 V1/V2 运行时回退；回滚只能回滚发布版本和数据库备份。

## 2. 当前基线与实施约束

### 2.1 已确认的现状

- 当前工作树存在大量未提交修改，包含用户原有改动和未完成的项目理解/联网检索实验。
- `pipeline_registry.py` 已登记 `analyze_project_understanding` 和 `research_project_materials`。
- `main.py` 正常 Pipeline Runner 未登记上述两个 stage，遇到未知 stage 会静默 `continue`。
- `graph/bid_graph.py` 的 node map 未登记上述两个 stage，构图时会出现缺失节点。
- 当前联网研究是一次性线性阶段，会把结果追加回 `inputs/reference.md` 并重新切分全部参考资料。
- 当前模板生成会清除模板正文并从 Markdown 重建，异常时退回空白 Word。
- 当前真实样本有 198 个章节任务和 1198 次评分点绑定，证明旧 job 模型不能继续沿用。
- 前端主要已使用 `/api/v2`，但后端仍保留大量非版本化兼容路由和 V1 文件状态投影。
- V2 控制面仍包含 V1 状态导入、冲突调和、兼容 projection 和 legacy fallback。

### 2.2 施工约束

- 不得使用 `git reset --hard`、覆盖或删除当前未提交改动。
- PR-0 必须先把现有改动按来源和意图区分，再开始重构。
- 开发期间允许新旧模块短暂共存，但生产执行入口始终只能指向一套内核。
- 每个阶段必须先增加 V3 测试，再删除被替代代码。
- 删除必须满足：
  1. 无活动调用；
  2. 已有 V3 替代；
  3. 替代路径测试通过。
- 历史 V1/V2 文档可以保留，但不得被运行代码或测试当作现行契约。

## 3. 目标阶段链

V3 正常 Pipeline 只保留以下内容阶段：

| 顺序 | V3 stage | 主要输入 | 主要输出 |
|---:|---|---|---|
| 1 | `ingest_inputs` | 上传文件、输入角色 | `InputManifest`、规范化源文件 |
| 2 | `normalize_sources` | 招标、评分、企业和参考资料 | 可追溯 chunks、来源索引 |
| 3 | `build_requirement_ledger` | 招标和评分 chunks | `RequirementLedger` |
| 4 | `build_project_model` | 要求台账、企业事实、参考资料 | `ProjectModel`、初始 EvidenceNeed |
| 5 | `sync_material_requirements` | 要求台账、企业事实 | `control.db` 材料状态 |
| 6 | `compile_document_contract` | 项目模型、要求台账、可选模板 | `TemplateContract` 或 `OutlineContract` |
| 7 | `plan_document` | 文档契约、项目模型、要求台账 | `DocumentPlan`、规划覆盖矩阵 |
| 8 | `execute_content_plan` | 内容单元、证据、依赖图 | 结构化 `ContentBlock` |
| 9 | `integrate_document` | 全部内容块、主题和一致性账本 | `IntegratedDocument` |
| 10 | `verify_document` | 终稿、要求、事实、证据 | 覆盖/Claim/合规/重复/一致性报告 |
| 11 | `render_document` | 通过内容门禁的终稿 | Markdown 预览、DOCX |
| 12 | `verify_delivery` | DOCX、模板契约、渲染页面 | 格式报告、GateReceipt、交付状态 |

`research_on_demand` 不是上述线性阶段之一。它是 3、4、6、7、8、9、10 均可调用的受控服务。

工作空间初始化不再属于内容 Pipeline，由 `workspace.create` Command 完成。

## 4. 当前阶段迁移映射

| 当前阶段 | V3 处理 |
|---|---|
| `init_workspace` | 移出内容链，归入 WorkspaceService |
| `prepare_inputs` | 重构为 `ingest_inputs`，增加明确输入角色和版本 |
| `split_docs` | 保留能力，归入 `normalize_sources` |
| `parse_score` | 与强制要求、资格和成果解析合并为 `build_requirement_ledger` |
| `extract_facts` | 拆分为来源事实抽取和 `build_project_model` |
| `analyze_project_understanding` | 删除当前实验实现，由 V3 `build_project_model` 替代 |
| `research_project_materials` | 从主链删除，由按需 ResearchService 替代 |
| `build_materials_checklist` | 保留确定性派生能力，改为 `sync_material_requirements` |
| `build_template_evidence` | 删除，由严格 TemplateContract 编译和要求映射替代 |
| `generate_outline` | 删除旧逻辑，由双模式 `compile_document_contract` 替代 |
| `plan_chapter_jobs` | 删除，由 `plan_document` 和 ContentUnit 切分替代 |
| `select_contexts` | 降为 ContentUnit Scheduler 内部上下文装配能力 |
| `write_chapters` | 改为 `execute_content_plan` |
| `review_fix_chapters` | 改为内容单元内部审核循环 |
| `build_source_trace_index` | 并入 `verify_document`，从规划到终稿持续维护 |
| `build_score_coverage_matrix` | 规划和终稿各执行一次 |
| `estimate_final_score` | 保留为非阻断诊断报告，不进入正式主链 |
| `summarize_chapters` | 删除独立阶段，摘要改为内容单元内部 Artifact |
| `global_review` | 拆成 `integrate_document` 和只读终稿审计 |
| `compliance_check` | 保留规则能力，并入 `verify_document` |
| `build_markdown` | 仅无模板模式和预览使用 |
| `build_docx` | 替换为双 Renderer |
| `check_format` | 扩展为 `verify_delivery` |

## 5. V3 代码组织

新增无版本号的内容领域包，避免将来继续复制 V4/V5：

```text
src/document_pipeline/
  contracts.py
  input_manifest.py
  requirement_ledger.py
  project_model.py
  document_contract.py
  template_contract.py
  outline_contract.py
  document_planner.py
  evidence.py
  research_service.py
  content_units.py
  content_scheduler.py
  content_writer.py
  integrator.py
  traceability.py
  quality.py
  invalidation.py
  renderers/
    template_renderer.py
    standard_renderer.py
    render_verifier.py
```

要求：

- 公共契约使用 Pydantic v2，并生成 JSON Schema。
- 领域模块不读取隐式项目根目录，只接收 `WorkspaceContext`。
- 所有写操作通过 StageRunner 或应用服务执行。
- LLM 只输出契约定义的数据，模型结果必须规范化和校验。
- 模块之间传 Artifact ID 和 revision，不传可变全局对象。

V3 Artifact 使用独立命名空间：

```text
workspace/v3/input_manifest.json
workspace/v3/source_index.json
workspace/v3/requirement_ledger.json
workspace/v3/project_model.json
workspace/v3/contracts/document_contract.json
workspace/v3/document_plan.json
workspace/v3/evidence/batches/*.json
workspace/v3/content_units/*.json
workspace/v3/integrated_document.json
workspace/v3/reports/planning_coverage.json
workspace/v3/reports/final_coverage.json
workspace/v3/reports/content_quality.json
workspace/v3/reports/render_quality.json
```

旧 Artifact 不得与 V3 使用相同路径，防止 resume 错误复用。

## 6. 控制数据库 V3

### 6.1 保留的表

继续保留并升级：

- `commands`
- `operations`
- `stage_runs`
- `workspace_lease`
- `workspace_events`
- `artifact_states`
- `gate_receipts`
- `gate_evaluations`
- `material_*`
- `issue_states`
- `policy_decisions`
- `goal_state`
- `repair_job_state`
- `agent_activity_state`
- `confirmations`
- `workspace_acl`

### 6.2 新增表

```text
document_state
  workspace_id
  document_mode
  project_model_revision
  document_contract_revision
  document_plan_revision
  integration_revision
  delivery_status
  updated_at

evidence_needs
  need_id
  question
  topic_id
  priority
  blocking_scope
  deadline_stage
  query_budget
  status
  active_batch_id
  created_at
  updated_at

content_unit_states
  unit_id
  contract_revision
  state
  attempt
  evidence_snapshot_hash
  output_artifact_id
  invalidation_reason
  updated_at

dependency_edges
  upstream_type
  upstream_id
  downstream_type
  downstream_id
  edge_kind

change_sets
  change_id
  source
  payload_json
  impact_json
  status
  created_at
  applied_at

content_locks
  block_id
  lock_owner
  reason
  content_hash
  created_at
```

### 6.3 删除的数据库兼容结构

- `migration_conflicts` 只服务 V1/V2 状态调和，V3 不再保留。
- 迁移前将未解决记录导出到 `workspace/v2_archive/migration_conflicts.json`。
- Schema V3 不再提供 V1 文件状态导入标志和 projection revision。

## 7. 公共契约

### 7.1 `DocumentMode`

```text
template_strict | auto_outline
```

模式由活动 `template` 输入是否存在决定。模板失败不会改变模式，只会进入 `blocked`。

### 7.2 `ProjectModel`

必须包含：

- 项目身份、背景、目标；
- 范围、边界、对象；
- 工作包和依赖；
- 输入、处理、成果、验收；
- 时间、角色、风险和约束；
- 统一术语和事实；
- 推断、冲突和未知项；
- 对 Requirement 和 EvidenceNeed 的引用。

### 7.3 `DocumentContract`

联合类型：

```text
DocumentContract =
  TemplateContract(mode=template_strict)
  | OutlineContract(mode=auto_outline)
```

两种合同必须提供统一的 `node_id`、父子关系、顺序和可写目标，供 DocumentPlan 和 ContentBlock 使用。

### 7.4 `DocumentPlan`

每个要求、评分点和主题只能有一个 `primary_owner`。允许多处出现时使用 `required_mentions`，不能创建多个主责。

### 7.5 `ContentBlock`

必须携带：

- 目标节点或槽位；
- 内容类型和正文；
- requirement/score/topic/evidence/fact 引用；
- 置信度和人工锁定状态。

缺少来源标识的关键数字、标准、人员、业绩和承诺不能通过内容门禁。

## 8. 分阶段开发计划

### PR-0：冻结基线与消除半接入状态

#### 目标

在不丢失当前改动的前提下建立可验证基线，先消除“注册了但没有完整 Runner”的不一致。

#### 工作

1. 记录当前 `git status`，按用户改动、V2 切换改动、实验性研究改动分类。
2. 不回滚任何未知来源修改。
3. 为当前 Pipeline Registry、Runner 和 Graph 一致性增加失败测试。
4. 增加最小回归夹具，复现：
   - 深层模板生成大量 job；
   - 评分点重复绑定；
   - 模板正文被清除；
   - 模板异常后空白 Word 回退。
5. 在 V3 StageRunner 接入前，实验性项目理解和研究 stage 不得进入生产自动链。
6. 锁定 V2 控制面测试基线，确保后续重构没有第二权威源。

#### 验收

- 所有活动 stage 在唯一 Runner 中有且只有一个实现。
- 未知 stage 必须报错，禁止 `continue` 静默跳过。
- Graph 旧路径不再作为基线验收入口。
- 当前未提交改动均有归类记录。

### PR-1：契约、Artifact 和 Schema V3

#### 目标

建立后续所有模块共同使用的强类型契约和版本规则。

#### 工作

1. 新建 `document_pipeline/contracts.py`。
2. 显式依赖 Pydantic v2。
3. 定义 InputManifest、RequirementLedger、ProjectModel、EvidenceNeed、EvidenceItem、DocumentContract、DocumentPlan、ContentUnit、ContentBlock、IntegratedDocument、QualityReport 和 ChangeSet。
4. 每种契约增加 `schema_version`、`revision`、`source_hashes`。
5. 增加 Artifact manifest 类型和依赖 fingerprint。
6. 新增控制数据库表和 schema migration。
7. 增加 JSON Schema 快照测试。

#### 验收

- 非法枚举、空 ID、重复主键和悬空引用无法写入。
- Artifact schema 变化会使下游 stale。
- V2 内容 Artifact 无法被 V3 resume 复用。

### PR-2：输入清单与来源规范化

#### 目标

明确文件角色和版本，解决“DOCX 是模板还是参考资料”的歧义。

#### 工作

1. 上传和本地导入都必须声明 role。
2. 同一工作空间只允许一个活动模板。
3. 原始文件只读保存，转换结果带 source anchor。
4. chunks 增加稳定 ID、页码、表格/段落位置、来源角色和版本。
5. 参考资料和企业资料严格分库。
6. 文件替换创建 ChangeSet，并计算下游影响。

#### 验收

- 普通 DOCX 参考资料不会触发模板模式。
- 替换模板会使契约及全部正文 stale。
- 替换一份企业证书只使依赖对应企业事实的内容 stale。

### PR-3：要求台账与项目整体模型

#### 目标

在写目录前形成最低充分、可追溯的整体项目理解。

#### 工作

1. 将评分点、强制项、资格项、废标项、交付物、验收和合同要求统一抽取到 RequirementLedger。
2. 为每项要求保留原文和 source anchor。
3. 从要求台账、招标事实和企业事实构建 ProjectModel。
4. 分离事实、推断、冲突和未知项。
5. 生成局部 EvidenceNeed，不生成“必须一次查完”的研究任务。
6. 增加理解门禁和人工确认项。
7. 将当前 `project_understanding.py` 中可用字段迁移到新契约后删除旧实现。

#### 验收

- 标书目标、范围、任务、成果、验收和工期均可追溯。
- 不依赖外部检索也能形成标书自身的项目骨架。
- 未确认企业能力不会出现在 ProjectModel 的已确认事实中。

### PR-4：双模式文档契约

#### 目标

一次性解决模板严格性和无模板标题生成。

#### 工作：严格模板

1. 直接分析 DOCX OOXML package。
2. 综合识别 outline level、样式 ID、本地化名称、编号、书签、内容控件和段落 ID。
3. 生成不可变结构指纹。
4. 识别 `text_slot`、`cell_slot`、`flow_slot` 和 `repeat_slot`。
5. 将 Requirement 映射到已有节点和槽位。
6. 无可靠映射时生成 `TEMPLATE_COVERAGE_GAP`。
7. 模板契约预览作为写作前硬门禁。

#### 工作：无模板

1. 根据标书规定结构、评分点、工作包、实施阶段、成果和验收生成标题。
2. 每个标题保存来源 Requirement。
3. 合并语义相近评分点。
4. 禁止通用兜底章节和把遗漏项塞入最后一章。

#### 验收

- 模板标题文字、级别、顺序和编号准确。
- 中文自定义样式和复杂编号可以识别。
- 严格模式不生成任何新标题。
- 无模板模式所有标题都有标书来源。

### PR-5：全文责任计划与规划覆盖

#### 目标

在写作前解决重复、越权和评分点多重绑定。

#### 工作

1. 为所有 Requirement、score point 和 topic 分配唯一主责。
2. 生成 supporting、forbidden 和 required_mentions。
3. 生成工作包依赖图、交叉引用和图表计划。
4. 按完整父章节或语义工作包划分 ContentUnit。
5. 超长父章节拆为共享同一 contract 的连续片段。
6. 生成规划覆盖矩阵。
7. 未覆盖、重复主责或循环依赖时阻断。

#### 验收

- 每个评分点恰好一个 `primary_owner`。
- 受控重复有 Requirement 明确依据。
- 7 个一级标题、198 个模板节点的样本不再产生 198 个独立 Writer。
- S036 等评分点不再被重复绑定 131 次。

### PR-6：按需研究服务

#### 目标

将当前一次性前置检索改造成可在任意阶段调用的共享证据服务。

#### 工作

1. 定义 ResearchProvider 接口，搜索和页面获取不写入业务文件。
2. 保留 SSRF、防内网访问、响应大小和超时限制。
3. 每个 EvidenceNeed 设置来源策略、预算、截止阶段和停止条件。
4. 支持官方站点、标准来源、人工上传资料和可配置搜索 Provider。
5. 搜索结果先进入候选证据，经过来源和 Claim 验证后才能发布为 EvidenceItem。
6. 证据批次不可变；Writer 固定读取某一批次快照。
7. 删除直接追加 `inputs/reference.md` 和全量重新切分的逻辑。
8. 网络失败只阻塞依赖该证据的内容单元。

#### 验收

- 项目理解、规划、写作和审核均可提出 EvidenceNeed。
- 新证据只使相关单元 stale。
- 外部资料不能证明企业能力。
- 达到查询预算后会形成明确缺口，不会无限搜索。

### PR-7：ContentUnit Scheduler 与 Writer

#### 目标

用依赖化内容单元替换独立章节 Agent。

#### 工作

1. ContentUnit Scheduler 从 DocumentPlan 生成 DAG。
2. 上下文装配同时包含全局模型、全篇计划和局部证据。
3. 并行执行只允许无依赖、无主题冲突单元。
4. Writer 输出 ContentBlock。
5. Writer 只能提交 EvidenceNeed、FactProposal 或 PlanIssue，不能修改共享状态。
6. 每个单元内执行生成、证据检查、越权检查和定向重写。
7. 单元完成记录证据快照 hash 和 contract revision。
8. 严格模式输出 slot 内容；无模板模式输出 node 内容。

#### 验收

- Writer 无法生成未登记标题。
- Writer 无法完整展开 forbidden topic。
- 不同并发 Writer 读取相同不可变全局快照。
- 局部失败和重试不影响已完成无关单元。

### PR-8：三级全文整合

#### 目标

让全文审核能够真实解决重复和矛盾。

#### 工作

1. 建立 TopicLedger、FactLedger、TerminologyLedger 和 CommitmentLedger。
2. 内容单元内部合并重复块。
3. 相邻章节检查承接和交叉引用。
4. 全文检测主责冲突、语义重复、术语差异、时间和数字冲突。
5. 对问题块执行移动、删除、合并和重写。
6. 保护用户锁定内容块。
7. 综合章节在详细内容完成后反向生成。
8. 输出 `IntegratedDocument` 和 rewrite trace。

#### 验收

- 审核发现的问题在正文中已经被修订。
- 非主责章节只保留必要摘要或引用。
- 统一事实和承诺无法被下游章节覆盖。
- 长文整合不依赖一次性把全文塞给单个模型。

### PR-9：追溯和质量门禁

#### 目标

将现有分散的审核器统一为 V3 交付门禁。

#### 工作

1. 规划覆盖和终稿覆盖分开。
2. Claim 与 EvidenceItem 对齐。
3. 继续复用资格、废标、签章、价格表、偏离表等确定性检查。
4. 新增主题主责、语义重复、统一术语、事实冲突和模板覆盖检查。
5. `global_review` 改为只读终稿审计。
6. 统一 Finding、Issue 和 GateEvaluation。
7. 正式 GateReceipt 包含输入、契约、计划、证据、终稿和 Renderer 指纹。

#### 验收

- 任一强制项未响应时不得 `ready`。
- 未知企业事实和无来源关键数字不得 `ready`。
- 审计失败返回整合或相关 ContentUnit。
- 旧 GateReceipt 在任何依赖变化后失效。

### PR-10：严格模板 Renderer

#### 目标

真正按照上传模板原位填充。

#### 工作

1. 复制原模板，禁止重新创建 Document。
2. 使用稳定 OOXML 锚点写入 slot。
3. 保留 styles、numbering、relationships、headers、footers、sections、fields 和固定正文。
4. `flow_slot` 复制相邻正文样式，不创建标题。
5. `repeat_slot` 只复制契约允许结构。
6. 填充后重新计算结构指纹并与白名单比较。
7. DOCX 转 PDF/页面图，执行视觉检查。
8. 模板异常直接阻断，删除空白 Word fallback。

#### 验收

- 模板固定结构零未授权变化。
- 页面增长不破坏节、页眉页脚和编号。
- 模板内固定说明仍存在。
- 损坏、加密或不支持模板不会生成替代 Word。

### PR-11：无模板 Renderer 与编辑闭环

#### 目标

从 IntegratedDocument 生成普通标书，并让人工编辑进入全局失效模型。

#### 工作

1. 根据 OutlineContract 生成 Markdown 和 DOCX。
2. 统一标题编号、正文、表格、列表、目录和分页样式。
3. 编辑器由“直接修改 final.md 行”改为编辑 ContentBlock。
4. 用户编辑形成 ChangeSet 和 content lock。
5. 编辑后重新执行整合、覆盖、质量和渲染门禁。
6. 撤销恢复块 revision，不直接覆盖文件。

#### 验收

- 用户编辑不会绕过质量门禁。
- 无模板标题和结构与 OutlineContract 一致。
- 正式 Word 可从内容块稳定重建。

### PR-12：V3 API、前端和唯一执行入口

#### 目标

完成 `/api/v3` 切换，消除 V2 API 和多执行入口。

#### 工作

1. 提供 `/api/v3/workspaces/{id}` 下的 snapshot、events、commands、uploads、evidence、document、gate 和 export API。
2. WorkspaceSnapshot 展示文档模式、项目模型、契约、计划、EvidenceNeed、ContentUnit 和交付状态。
3. 前端上传明确选择文件角色。
4. 增加模板预检、目录预览、全文责任和证据缺口视图。
5. Pipeline 只由 CommandGateway → ExecutionController → StageRunner 执行。
6. CLI 仅保留 V3 控制客户端和内部 worker 入口。
7. 删除前端对 `/api/v2` 和旧非版本化工作空间 API 的调用。

#### 验收

- Web、CLI 和 Agent 对同一 Command 得到相同状态迁移。
- 不存在页面 fast-path 或直接 stage mutation。
- 前端全部工作空间请求使用 `/api/v3`。

### PR-13：删除 V1/V2 废弃逻辑

#### 目标

在 V3 替代测试通过后物理删除旧路径，不保留运行时兼容层。

#### 删除组 A：旧状态权威和投影

- V1 run state、goal、materials、repair job 和 issue 文件读写。
- V1 → V2 自动导入、冲突调和和 migration conflict 处理。
- V1 状态文件存在性检查和 compatibility projection。
- `legacy_json`、legacy actor/source 和旧 idempotency 命名。

#### 删除组 B：旧执行内核

- LangGraph 完整 Pipeline 和 `graph-run`。
- LangGraph chapter subgraph。
- LangGraph supervisor CLI。
- `session_orchestrator` legacy fallback。
- `AGENT_SUPERVISOR_ENABLED` 回退开关。
- 旧公开阶段 CLI 和 `BID_AGENT_EXECUTION_WORKER` 兼容分支。
- Graph state recorder 中作为权威的 `run_state.json`；需要的事件追踪迁入 control plane/Artifact telemetry。

#### 删除组 C：旧内容生产

- 旧 outline 生成和兜底追加逻辑。
- template evidence map 作为文档结构依据。
- job planner、独立 chapter context、独立 chapter writer/reviewer/rewriter/summarizer。
- subagent runner 和旧 agents wrapper。
- 旧 source trace/coverage 的章节文件耦合。
- 旧 global review 和旧 Markdown 拼接路径。
- 当前实验性前置项目研究和 `web_research.py`。

#### 删除组 D：旧模板路径

- `_clear_document_body`
- `_clear_document_body_after_cover`
- 模板 Markdown 重建
- 模板异常空白 Word fallback
- 只按 Heading 名称识别标题的旧 schema

#### 删除组 E：旧 API 和前端适配

删除所有工作空间相关的：

- 非版本化 `/api/...` 兼容路由；
- `/api/v2/...` 路由；
- compatibility warning/link/deprecation header；
- 旧 active workspace 回退；
- 旧 mutation adapter；
- 前端 legacy action 和测试。

继续保留或迁移到明确全局 API 的只有：

- 登录、登出和当前用户；
- 静态页面；
- 全局 LLM 配置；
- 其他确实不属于工作空间的管理配置。

#### 删除组 F：依赖和文档

- 删除不再使用的 `langgraph`、`langchain-core` 依赖。
- V1/V2 文档保留为历史，但导航标记为非运行时依据。
- README、CLI 帮助和环境变量示例全部改成 V3。

#### 验收

代码搜索必须满足：

- 无 `/api/v2/workspaces`；
- 无 V1 工作空间 mutation 路由；
- 无 `graph-run`、`run_bid_graph` 和 LangGraph Pipeline；
- 无 `run_state.json` 作为状态源；
- 无模板清正文和空白 Word 回退；
- 无 legacy orchestrator fallback；
- 无一标题一 job 的执行路径；
- 无生产代码读取 V2 内容 Artifact。

### 历史发布草案：迁移、真实项目验收与发布（原草案 PR-14，已废止编号）

#### V1 工作空间

- 不迁移。
- 提供只读归档或删除工具。
- 明确提示重新创建 V3 工作空间并重新上传源文件。

#### V2 工作空间

1. 创建完整备份。
2. 升级 `control.db` schema。
3. 保留：
   - 原始上传文件；
   - 企业材料提交和验证记录；
   - 工作空间 ACL；
   - 历史 Command、Operation 和审计事件。
4. 重新验证已接受风险，不直接继承旧 GateReceipt。
5. 归档旧 outline、jobs、contexts、chapters、summaries、final.md 和 final.docx。
6. 将旧内容阶段 Artifact 全部标记 stale。
7. 从 `ingest_inputs` 或 `build_requirement_ledger` 重新执行 V3。

#### 发布方式

- 开发期只在测试和独立 V3 工作空间运行，不在生产暴露双内核选择。
- V3 API、前端和 StageRunner 验收后原子切换。
- 切换后立即执行 PR-13 的物理删除。
- 回滚使用上一发布包和数据库备份，不保留 V1/V2 运行时分支。

#### 发布验收

- 全量单元、契约、集成和端到端测试通过。
- 两个现有真实项目进行本地回归。
- 至少一个复杂模板和一个无模板标书通过人工验收。
- 正式导出的 GateReceipt、Artifact 和页面渲染证据完整。

## 9. 测试计划

### 9.1 单元测试

- 所有 Pydantic 契约和引用完整性。
- Requirement 原子化和来源定位。
- ProjectModel 事实/推断/未知分离。
- 模板标题、表格、内容控件、slot 和结构指纹。
- 自动目录来源优先级。
- 主责分配和循环依赖检测。
- EvidenceNeed 预算、停止条件和来源策略。
- ContentBlock Claim/evidence 校验。
- 主题重复、术语和数字冲突检测。
- ChangeSet 影响分析。

### 9.2 契约测试

- StageRegistry、StageRunner、Artifact manifest 一致。
- API request/response 与 WorkspaceSnapshot。
- Command 幂等、revision 冲突和 lease。
- DB migration 前后数据一致。
- Renderer 输入只接受通过门禁的 IntegratedDocument。

### 9.3 集成测试

- 招标文件 → 项目模型 → 自动目录 → 内容计划。
- 模板 → TemplateContract → slot 内容 → 原位渲染。
- 写作中 EvidenceNeed → 证据批次 → 局部恢复。
- 新企业材料 → 局部失效 → 重写 → 再整合。
- 用户锁定内容 → 全文整合不覆盖锁定块。
- GateReceipt 在输入变化后失效。

### 9.4 端到端场景

1. 无模板、无企业资料。
2. 无模板、完整企业资料。
3. 严格模板、中文自定义样式。
4. 严格模板、复杂编号和合并表格。
5. 模板损坏。
6. 模板覆盖缺口。
7. 中途上传参考方案。
8. 中途修改分类体系。
9. 网络不可用。
10. 外部标准与招标标准冲突。
11. 长文断点恢复。
12. V2 工作空间迁移。
13. 正式出稿和 GateReceipt 下载。

### 9.5 真实样本验收

对当前 92 评分点、198 模板节点样本检查：

- 92 个评分点全部有且只有一个主责；
- 不再产生 198 个独立 Writer；
- S036 不再有 131 次主责绑定；
- 模板固定标题和结构不变；
- 同一主题不在多章重复完整论述；
- 全文数字、范围、工期和交付物一致。

### 9.6 删除验收

PR-13 后运行静态搜索和 import 测试，证明：

- 删除模块无引用；
- 删除路由无前端调用；
- 删除 Artifact 无 resume 依赖；
- requirements 中无未使用旧框架；
- 文档、CLI 和环境变量无旧运行说明。

## 10. 完成定义

只有以下条件全部满足，V3 才能标记完成：

- [ ] V3 12 个正常内容阶段使用唯一 StageRunner。
- [ ] 研究服务不在线性主链中。
- [ ] 项目模型、要求台账、文档契约和全文计划可追溯。
- [ ] 模板模式严格原位填充且无空白回退。
- [ ] 无模板标题全部来自标书。
- [ ] 每个评分点和主题唯一主责。
- [ ] Writer 按 ContentUnit 和依赖图执行。
- [ ] 全文整合实际修订重复和冲突。
- [ ] 内容、结构和页面门禁均可阻断正式交付。
- [ ] `/api/v3` 和前端完成切换。
- [ ] V1/V2 工作空间兼容 API、文件状态和旧执行内核已物理删除。
- [ ] 旧内容模块和 LangGraph Pipeline 已删除。
- [ ] V2 工作空间迁移和 V1 拒绝策略通过测试。
- [ ] 真实模板和无模板项目均通过人工验收。
- [ ] README、架构导航、CLI 和部署配置全部指向 V3。

历史 PR-0～PR-13 已按本文件推进；后续必须转入当前权威计划的 PR-14～PR-28 和仓库发布验收门 Gate K/S/A/P/B/M。不得为了短期兼容在 V3 正式版保留第二套状态机、第二套 Runner 或静默 fallback。
