# 架构设计：Artifact、控制面与数据一致性

## 1. 架构目标

新增能力必须遵守现有 V3 内核：

1. Agent 无 canonical Artifact、数据库和任意文件写权限。
2. 全局 Artifact 必须通过 Proposal → Validation → Gate → Promotion。
3. Promotion 使用 CAS、幂等和追加历史。
4. Writer 只接受冻结的 `WriterInputBundle`。
5. Writer 不联网、不读取全局工作空间、不自行决定来源。
6. `ChapterBlueprint` 是章节结构和评分主责唯一权威。
7. 章节上下文、规划和正文均按章节隔离。
8. 所有确认绑定精确 hash、revision、依赖和 reviewer。

---

## 2. 权威对象分层

### 2.1 全局 canonical Artifact

| Artifact | 职责 |
|---|---|
| `InputManifest` | 输入文件清单和角色 |
| `SourceIndex` | 所有输入的 canonical `SourceBlock` |
| `LegacyBidIndex` | 旧投标书目录、章节和段落语义索引 |
| `RequirementLedger` | 新招标要求 |
| `ScoreModel` | 评分结构和满分条件 |
| `ProjectModel` | 新项目事实 |
| `TemplateStructureContract` | 严格模板结构 |
| `ChapterBlueprint` | 全局章节树、职责、评分归属和旧目录 lineage |

### 2.2 章节级 canonical 控制记录

| 记录 | 职责 |
|---|---|
| `ChapterContextRevision` | 用户和系统为本章维护的上下文 |
| `ChapterWritingPlanRevision` | 本次章节写作要使用的来源、内容块和绑定 |
| `ChapterPlanApprovalReceipt` | 对精确规划版本的确认 |
| `ChapterContentRevision` | 章节正文版本 |
| `ChapterApprovalReceipt` | 对精确正文版本的确认 |

### 2.3 非权威投影

以下只用于展示或诊断：

- 旧投标书 Markdown 分块；
- 规划图布局坐标；
- `_authority.json` 中的权限偏好；
- 聊天历史；
- 搜索结果列表缓存；
- 章节编号显示字符串；
- 前端来源卡片排序。

它们不能反向成为 Writer 的 canonical 输入。

---

## 3. 为什么 ChapterWritingPlan 不直接塞进现有全局 Artifact Registry

当前全局 `v3_active_artifacts` 以 `artifact_kind` 为唯一键。一个工作空间同一时刻只有一个活动 `ChapterBlueprint`，这很合理；但章节规划是“一章一个活动 head”。

如果把所有章节规划都注册成同一个全局 `ChapterWritingPlan`：

```text
artifact_kind = ChapterWritingPlan
```

就只能有一个活动章节规划，显然不行。除非大改全局 Artifact 主键，这会扩大风险。

因此第一版采用章节级 Promotion 内核：

```text
ChapterPlanProposal
  → ChapterPlanValidationReport
  → ChapterPlanGateReceipt
  → ChapterPlanPromotionService
  → ChapterWritingPlanRevision
  → head_plan_revision
```

它复用全局 ADR 的规则，但存储在章节作用域表中。  
未来若全局 Artifact Registry 支持 `(artifact_kind, aggregate_id)`，再统一底层实现，不在本次顺手重写基础设施。

---

## 4. LegacyBidIndex 设计

### 4.1 依赖

```text
InputManifest
  ↓
SourceIndex
  ↓
LegacyBidIndex
```

`LegacyBidIndex` 的所有 section 和 paragraph 必须引用 SourceIndex 的 canonical `block_id`。

### 4.2 核心结构

```python
class LegacyBidIndex(ContractModel):
    index_id: str
    source_index_revision: int
    source_index_hash: str
    documents: list[LegacyBidDocument]
    sections: list[LegacySectionProfile]
    paragraphs: list[LegacyParagraphProfile]
    coverage: LegacyIndexCoverage
    dependency_fingerprint: str
```

### 4.3 章节模型

```python
class LegacySectionProfile(BaseModel):
    section_id: str
    source_input_id: str
    heading_block_id: str
    parent_section_id: str | None
    order: int
    level: int
    number_text: str
    title: str
    heading_path: list[str]
    descendant_section_ids: list[str]
    paragraph_block_ids: list[str]
    table_block_ids: list[str]
    image_block_ids: list[str]
    description: str
    answers: list[str]
    content_kinds: list[str]
    reusable_conditions: list[str]
    legacy_entity_risks: list[LegacyEntityRisk]
```

### 4.4 段落模型

```python
class LegacyParagraphProfile(BaseModel):
    block_id: str
    section_id: str
    order_in_section: int
    description: str
    answers: list[str]
    content_kind: str
    reusable_scope: str
    legacy_entity_risks: list[LegacyEntityRisk]
```

### 4.5 Gate

至少检查：

- 所有 `block_id` 存在于依赖 SourceIndex；
- 所有 section parent 存在或为空；
- 无环；
- heading block 角色为 `legacy_bid`；
- paragraph coverage 达到阈值；
- 描述不为空；
- 描述没有伪造新的 source ID；
- `source_index_revision/hash` 精确匹配；
- 被替换旧文件不会继续作为 active source；
- section ID 稳定且由 canonical input/block 派生。

---

## 5. ChapterBlueprint 扩展

增加可选字段：

```python
class OutlineLineageRef(BaseModel):
    source_input_id: str
    legacy_section_id: str
    source_heading_block_id: str
    source_number: str
    source_title: str
    source_path: list[str]
    decision: Literal["reused", "adapted", "merged", "split"]
    rationale: str

class BlueprintNode(...):
    outline_lineage: list[OutlineLineageRef] = []
```

注意：

- 旧 Blueprint 读取时默认空数组；
- 不自动重写旧 Artifact；
- 只有新生成 Blueprint 才写 lineage；
- rewrite type 不放 Blueprint；
- `ignored` 旧节点记录在 planning report，而不是挂到新节点；
- 严格模板节点可记录 lineage，但不能改变模板 topology。

---

## 6. ChapterWritingPlan 领域模型

### 6.1 计划头

```python
class ChapterWritingPlanPayload(BaseModel):
    schema_version: Literal["v3.chapter-writing-plan.v1"]
    workspace_id: str
    chapter_id: str
    project_writing_mode: Literal["full_write", "bid_rewrite"]
    blueprint_revision: int
    blueprint_hash: str
    chapter_context_revision: int
    chapter_context_hash: str
    global_context_revision: int
    global_context_hash: str
    constraints: ChapterPlanConstraints
    content_units: list[ChapterPlanContentUnit]
    sources: list[ChapterPlanSource]
    bindings: list[ChapterPlanBinding]
    exclusions: list[ChapterPlanExclusion]
    research_decision: ChapterPlanResearchDecision
    rewrite_type: Literal["copy", "light_edit", "restructure", "new_write"]
    dependency_fingerprint: str
```

### 6.2 内容块

内容块基于现有 `compile_chapter_writing_outline`，但需要稳定 ID：

```python
class ChapterPlanContentUnit(BaseModel):
    unit_id: str
    order: int
    title: str
    purpose: str
    must_answer: list[str]
    score_point_ids: list[str]
    score_condition_ids: list[str]
    requirement_ids: list[str]
    outcome_kind: Literal["", "deliverable", "acceptance"]
    writing_instruction: str
```

`unit_id` 不能再只依赖数组位置 `WO-1`。建议由：

```text
chapter_id + condition_id / objective stable key
```

派生，避免用户增加前一个块后所有 ID 全变。

### 6.3 来源

```python
class ChapterPlanSource(BaseModel):
    source_id: str
    source_type: Literal[
        "tender_requirement",
        "score_obligation",
        "project_fact",
        "chapter_context",
        "uploaded_material",
        "legacy_paragraph",
        "web_evidence",
        "user_note",
    ]
    title: str
    description: str
    source_ref: dict
    source_hash: str
    fetch_status: Literal["ready", "lead_only", "failed", "stale"]
    trust_level: Literal["authoritative", "project", "external_primary", "external_secondary"]
    risk_flags: list[str]
```

### 6.4 绑定

```python
class ChapterPlanBinding(BaseModel):
    binding_id: str
    source_id: str
    content_unit_id: str
    usage_type: Literal[
        "base_content",
        "supplement",
        "fact_only",
        "evidence",
        "structure_reference",
        "contrast",
    ]
    usage_scope: Literal["full", "partial"]
    selected_fragment_refs: list[str]
    instruction: str
    forbidden_carryovers: list[str]
    order: int
```

### 6.5 排除项

```python
class ChapterPlanExclusion(BaseModel):
    source_ref: str
    reason: str
    scope: str
```

排除项用于明确：

- 用户删除的候选来源；
- 旧项目名称；
- 过期标准；
- 不允许使用的段落；
- 与新招标冲突的信息。

---

## 7. 章节计划提案与晋级

### 7.1 Proposal

Agent/Planner 只产生：

```python
ChapterPlanProposal(
    proposal_id,
    chapter_id,
    base_plan_revision,
    expected_chapter_revision,
    dependency_fingerprint,
    payload,
    proposal_hash,
    producer,
    prompt_version,
    model_fingerprint,
)
```

### 7.2 Validation

确定性验证：

- Schema；
- chapter 存在且 active；
- Blueprint revision/hash；
- 所有 requirement/score ID 属于当前章；
- content unit ID 唯一；
- source ID 唯一；
- binding 两端存在；
- legacy `block_id` 存在；
- evidence snapshot ready；
- 排除来源不在 active bindings；
- rewrite type 与 bindings 重新计算一致；
- blocking gap 已解决或显式接受；
- dependency fingerprint 可重算。

### 7.3 Gate

Gate 关注：

- 规划覆盖本章 must-answer；
- 无越界 requirement；
- 无不可信 lead 进入 Writer；
- 旧项目污染风险有处理指令；
- `copy` 真的满足完整搬用条件；
- `new_write` 没有 legacy body binding；
- 搜索预算和来源策略合规；
- 人工确认模式要求是否满足。

### 7.4 Promotion

原子事务：

1. 锁定 chapter row；
2. 检查 `chapter_revision`；
3. 检查 `base_plan_revision`；
4. 检查 proposal hash；
5. 检查 gate receipt；
6. 插入 plan revision；
7. 更新 `head_plan_revision`；
8. 若为确认 promotion，更新 `confirmed_plan_revision`；
9. 增加 `chapter_revision`；
10. 写 workspace event；
11. 返回 promotion receipt。

同一幂等键重复调用返回相同结果。

---

## 8. 计划确认回执

```python
class ChapterPlanApprovalReceipt(BaseModel):
    receipt_id: str
    receipt_hash: str
    chapter_id: str
    plan_revision: int
    plan_hash: str
    decision: Literal["confirmed", "rejected"]
    review_mode: Literal["human", "delegated", "auto"]
    principal_id: str
    dependency_fingerprint: str
    dependency_snapshot: dict
    created_at: str
```

确认不能只看 `plan_revision`，因为理论上错误代码可能复用 revision。必须同时校验 hash。

---

## 9. WriterInputBundle 扩展

增加：

```python
chapter_plan_revision: int
chapter_plan_hash: str
chapter_plan_approval_receipt_id: str
chapter_plan_snapshot: dict
selected_source_blocks: list[dict]
selected_source_bindings: list[dict]
excluded_source_refs: list[str]
```

`allowed_source_ids` 由确认 plan 精确编译，不再按“与本章相关”宽泛收集。

### 9.1 新能力工作空间

```text
confirmed plan required
run_research must be false
bundle sources exactly equal confirmed plan whitelist
```

### 9.2 旧兼容工作空间

```text
if capability.chapter_plan_v1 is false:
    retain current outline authority + inline research path
```

过渡期由能力位判断，不用猜数据库有没有某张表。

---

## 10. 依赖指纹

建议 canonical 输入：

```json
{
  "chapter_id": "...",
  "blueprint": {"revision": 4, "hash": "..."},
  "chapter_context": {"revision": 2, "hash": "..."},
  "global_context": {"revision": 3, "hash": "..."},
  "source_blocks": [
    {"block_id": "...", "content_hash": "..."}
  ],
  "evidence": [
    {"evidence_id": "...", "snapshot_hash": "..."}
  ],
  "content_units_hash": "...",
  "bindings_hash": "...",
  "exclusions_hash": "...",
  "policy_versions": {
    "plan_gate": "...",
    "rewrite_classifier": "...",
    "research_policy": "..."
  }
}
```

排序、空值、数字、Unicode 归一化必须复用现有 canonicalization 工具，不能每个服务自己 `json.dumps` 出一个风格。

---

## 11. 陈旧判定服务

新增：

```text
ChapterPlanStalenessService
```

输入上游事件，查依赖边，原子更新：

```text
confirmed plan → stale
head draft → draft_stale
formal content → formal_stale 或 issue
```

不能删除旧 plan/draft，必须保留历史和对比。

建议事件：

```text
chapter.plan.stale
chapter.draft.stale
chapter.plan.confirmed
chapter.plan.promoted
chapter.plan.rejected
```

---

## 12. 权限模式迁移

当前 `_authority.json`：

```text
mode
review_status
outline_hash
outline_snapshot
```

目标：

```text
mode
```

迁移步骤：

1. 新能力关闭：完整读取旧字段。
2. 新能力影子运行：mode 从旧文件读取，正式 plan 状态来自控制库。
3. 新能力正式启用：忽略旧 review_status，仍保留文件不删。
4. 两个发布周期后：提供一次性清理工具，只删旧 review 状态，不删聊天。
5. 回滚时旧文件仍在，兼容链路可继续使用。

---

## 13. 全局 H1 与章节确认的关系

现有 ADR 要求单一全局 H1。新设计保持：

```text
全局 H1：
确认整个 ChapterBlueprint

章节 plan confirmation：
确认某个已存在章节本次写作使用的材料与方法
```

章节计划确认不能：

- 改变目录；
- 改变评分主责；
- 新增跨章职责；
- 替代全局 H1；
- 让章节 Agent 重写 Blueprint。

若规划发现 Blueprint 不合理，只能产生：

```text
PlanIssueProposal
```

由用户回到全局规划修改目录。

---

## 14. 安全与审计

所有计划相关日志至少包含：

```text
workspace_id
chapter_id
operation_id
proposal_id
plan_revision
plan_hash
base_plan_revision
chapter_revision
review_mode
principal_id
source counts by type
dependency_fingerprint
duration
result/error code
```

不得记录：

- 完整敏感正文到普通日志；
- API Key；
- 整份旧投标书；
- 未脱敏用户身份信息。

诊断包可以在用户主动导出时包含受控快照摘要和 ID。
