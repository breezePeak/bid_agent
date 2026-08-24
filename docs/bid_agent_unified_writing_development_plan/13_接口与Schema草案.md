# 接口与 Schema 草案

> 本文件是开发时的契约草案。实现前应转换为严格 Pydantic/前端归一化契约，并以测试固定。

## 1. 枚举

```python
ProjectWritingMode = Literal["full_write", "bid_rewrite"]
DocumentMode = Literal["auto_outline", "template_strict"]

RewriteType = Literal[
    "copy",
    "light_edit",
    "restructure",
    "new_write",
]

PlanStatus = Literal[
    "draft",
    "confirmed",
    "stale",
    "failed",
]

PlanSourceType = Literal[
    "tender_requirement",
    "score_obligation",
    "project_fact",
    "chapter_context",
    "uploaded_material",
    "legacy_paragraph",
    "web_evidence",
    "user_note",
]

BindingUsageType = Literal[
    "base_content",
    "supplement",
    "fact_only",
    "evidence",
    "structure_reference",
    "contrast",
]
```

UI 聚合：

```text
mixed
```

不进入 RewriteType。

---

## 2. 创建工作空间

### 请求

```http
POST /api/v3/workspaces
Content-Type: application/json
```

```json
{
  "name": "2026年度项目",
  "writing_mode": "bid_rewrite",
  "project_type": "国土调查",
  "expected_pages": 180
}
```

### 响应

```json
{
  "ok": true,
  "workspace": {
    "id": "...",
    "name": "...",
    "writing_mode": "bid_rewrite",
    "project_type": "国土调查",
    "expected_pages": 180,
    "capabilities": {
      "chapter_plan_v1": true,
      "plan_required_for_write": true,
      "bid_rewrite_v1": true,
      "legacy_inline_research": false
    }
  }
}
```

---

## 3. LegacyBidIndex Schema

```python
class LegacyEntityRisk(BaseModel):
    risk_id: str
    block_id: str
    risk_type: Literal[
        "project_name",
        "client_name",
        "bidder_name",
        "year",
        "date",
        "location",
        "duration",
        "person",
        "quantity",
        "amount",
        "platform_version",
        "standard_version",
        "contact",
        "other",
    ]
    text: str
    normalized_value: str
    severity: Literal["blocking", "warning"]
    suggested_action: Literal["replace", "remove", "verify", "retain"]
    reason: str

class LegacyBidDocument(BaseModel):
    source_input_id: str
    filename: str
    root_section_ids: list[str]
    block_count: int
    section_count: int
    paragraph_count: int
    structure_quality: str

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
    lead_block_ids: list[str]
    paragraph_block_ids: list[str]
    table_block_ids: list[str]
    image_block_ids: list[str]
    child_section_ids: list[str]
    description: str
    answers: list[str]
    content_kinds: list[str]
    reusable_conditions: list[str]
    legacy_entity_risks: list[LegacyEntityRisk]

class LegacyParagraphProfile(BaseModel):
    block_id: str
    section_id: str
    order_in_section: int
    description: str
    answers: list[str]
    content_kind: str
    reusable_scope: str
    legacy_entity_risks: list[LegacyEntityRisk]

class LegacyIndexCoverage(BaseModel):
    total_legacy_blocks: int
    described_paragraphs: int
    assigned_blocks: int
    exempt_blocks: int
    structure_gaps: list[dict]

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

---

## 4. Blueprint lineage

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
    outline_lineage: list[OutlineLineageRef] = Field(default_factory=list)
```

---

## 5. ChapterWritingPlan Schema

```python
class ChapterPlanConstraints(BaseModel):
    chapter_purpose: str
    writing_objectives: list[str]
    requirement_ids: list[str]
    score_point_ids: list[str]
    score_condition_ids: list[str]
    must_answer: list[str]
    forbidden_topics: list[str]

class ChapterPlanContentUnit(BaseModel):
    unit_id: str
    order: int
    title: str
    purpose: str
    must_answer: list[str]
    requirement_ids: list[str]
    score_point_ids: list[str]
    score_condition_ids: list[str]
    outcome_kind: Literal["", "deliverable", "acceptance"]
    writing_instruction: str

class LegacyParagraphSourceRef(BaseModel):
    kind: Literal["legacy_paragraph"]
    source_input_id: str
    legacy_section_id: str
    block_ids: list[str]
    block_hashes: dict[str, str]

class WebEvidenceSourceRef(BaseModel):
    kind: Literal["web_evidence"]
    evidence_id: str
    batch_id: str
    snapshot_id: str
    snapshot_hash: str
    url: str
    domain: str

class ProjectFactSourceRef(BaseModel):
    kind: Literal["project_fact"]
    fact_id: str
    context_revision: int
    fact_hash: str

class UploadedBlockSourceRef(BaseModel):
    kind: Literal["uploaded_material"]
    input_id: str
    block_ids: list[str]
    block_hashes: dict[str, str]

PlanSourceRef = Annotated[
    LegacyParagraphSourceRef
    | WebEvidenceSourceRef
    | ProjectFactSourceRef
    | UploadedBlockSourceRef,
    Field(discriminator="kind"),
]

class ChapterPlanSource(BaseModel):
    source_id: str
    source_type: PlanSourceType
    title: str
    description: str
    source_ref: PlanSourceRef | dict
    source_hash: str
    fetch_status: Literal["ready", "lead_only", "failed", "stale"]
    trust_level: Literal[
        "authoritative",
        "project",
        "external_primary",
        "external_secondary",
    ]
    risk_flags: list[str]

class ChapterPlanBinding(BaseModel):
    binding_id: str
    source_id: str
    content_unit_id: str
    usage_type: BindingUsageType
    usage_scope: Literal["full", "partial"]
    selected_fragment_refs: list[str]
    instruction: str
    forbidden_carryovers: list[str]
    order: int

class ChapterPlanExclusion(BaseModel):
    exclusion_id: str
    source_ref: str
    scope: str
    reason: str

class ChapterPlanResearchDecision(BaseModel):
    mode: Literal["not_needed", "auto", "user_requested", "existing_only"]
    missing_questions: list[str]
    queries: list[str]
    query_budget: int
    unresolved_gaps: list[str]
    accepted_existing_only: bool

class ChapterWritingPlanPayload(BaseModel):
    schema_version: Literal["v3.chapter-writing-plan.v1"]
    workspace_id: str
    chapter_id: str
    project_writing_mode: ProjectWritingMode
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
    rewrite_type: RewriteType
    rewrite_reason_codes: list[str]
    dependency_fingerprint: str
```

---

## 6. Plan revision API View

```json
{
  "chapter_id": "chapter-2.1.1",
  "plan_revision": 3,
  "parent_plan_revision": 2,
  "plan_hash": "...",
  "status": "confirmed",
  "payload": {},
  "approval": {
    "receipt_id": "...",
    "review_mode": "human",
    "principal_id": "...",
    "created_at": "..."
  },
  "stale_reason": "",
  "created_at": "..."
}
```

---

## 7. 读取当前 Plan

```http
GET /api/v3/workspaces/{workspace_id}/chapters/{chapter_id}/writing-plan
```

### 响应：无计划

```json
{
  "ok": true,
  "plan": null,
  "summary": {
    "status": "missing",
    "write_ready": false
  },
  "capabilities": {
    "chapter_plan_v1": true,
    "plan_required_for_write": true
  }
}
```

### 响应：已有

```json
{
  "ok": true,
  "plan": {
    "chapter_id": "...",
    "plan_revision": 3,
    "plan_hash": "...",
    "status": "confirmed",
    "payload": {}
  },
  "summary": {
    "rewrite_type": "light_edit",
    "source_count": 6,
    "legacy_source_count": 4,
    "web_source_count": 1,
    "write_ready": true,
    "stale": false
  }
}
```

---

## 8. 构建 Plan 事件流

```http
POST /api/v3/workspaces/{workspace_id}/chapters/{chapter_id}/writing-plan/stream
Accept: application/x-ndjson
```

请求：

```json
{
  "action": "build",
  "expected_chapter_revision": 7,
  "base_plan_revision": 0,
  "research_mode": "auto",
  "idempotency_key": "uuid"
}
```

事件：

```json
{"sequence":1,"type":"plan_started","operation_id":"..."}
{"sequence":2,"type":"constraints_loaded","data":{"unit_count":4}}
{"sequence":3,"type":"legacy_candidates_found","data":{"count":12}}
{"sequence":4,"type":"research_decision","data":{"needed":true}}
{"sequence":5,"type":"research_source_found","data":{"source_id":"..."}}
{"sequence":6,"type":"plan_delta","data":{"source_count":5}}
{"sequence":7,"type":"validation","data":{"passed":true}}
{"sequence":8,"type":"gate","data":{"verdict":"pass"}}
{"sequence":9,"type":"plan_ready","data":{"plan_revision":1,"plan_hash":"..."}}
{"sequence":10,"type":"done","status":"succeeded"}
```

错误：

```json
{
  "sequence": 6,
  "type": "error",
  "error": {
    "code": "CHAPTER_PLAN_SOURCE_NOT_READY",
    "message": "...",
    "retryable": true,
    "details": {}
  }
}
```

---

## 9. 编辑 Plan

```http
POST /api/v3/workspaces/{workspace_id}/chapters/{chapter_id}/writing-plan/edits
```

```json
{
  "expected_chapter_revision": 8,
  "base_plan_revision": 1,
  "base_plan_hash": "...",
  "idempotency_key": "...",
  "operations": [
    {
      "type": "remove_source",
      "source_id": "source-x",
      "reason": "与新项目无关"
    },
    {
      "type": "update_binding_instruction",
      "binding_id": "binding-y",
      "instruction": "保留平台流程，删除旧版本号"
    }
  ]
}
```

响应返回完整新 plan revision。

---

## 10. 确认 Plan

```http
POST /api/v3/workspaces/{workspace_id}/chapters/{chapter_id}/writing-plan/confirm
```

```json
{
  "expected_chapter_revision": 9,
  "plan_revision": 2,
  "plan_hash": "...",
  "dependency_fingerprint": "...",
  "review_mode": "human",
  "idempotency_key": "..."
}
```

响应：

```json
{
  "ok": true,
  "plan": {
    "plan_revision": 2,
    "plan_hash": "...",
    "status": "confirmed"
  },
  "approval_receipt": {
    "receipt_id": "...",
    "receipt_hash": "...",
    "review_mode": "human"
  },
  "write_readiness": {
    "ready": true,
    "reason": ""
  }
}
```

---

## 11. 开始写作

复用：

```http
POST /api/v3/workspaces/{workspace_id}/chapters/{chapter_id}/draft/stream
```

请求：

```json
{
  "action": "execute_confirmed_plan",
  "expected_chapter_revision": 10,
  "plan_revision": 2,
  "plan_hash": "...",
  "plan_approval_receipt_id": "...",
  "run_research": false,
  "idempotency_key": "..."
}
```

新能力下：

```text
run_research=true → CHAPTER_WRITE_RESEARCH_NOT_ALLOWED
```

---

## 12. WriterInputBundle 示例

```json
{
  "bundle_id": "...",
  "chapter_id": "...",
  "blueprint_revision": 4,
  "blueprint_hash": "...",
  "chapter_plan_revision": 2,
  "chapter_plan_hash": "...",
  "chapter_plan_approval_receipt_id": "...",
  "chapter_plan_snapshot": {
    "content_units": [...],
    "bindings": [...]
  },
  "selected_source_blocks": [
    {
      "block_id": "...",
      "content_hash": "...",
      "content": "...",
      "source_type": "legacy_paragraph"
    }
  ],
  "selected_source_bindings": [...],
  "excluded_source_refs": [...],
  "allowed_source_ids": [...],
  "dependency_fingerprint": "..."
}
```

---

## 13. 章节列表 summary

```json
{
  "chapter_id": "...",
  "title": "现有业务平台与技术支撑基础",
  "plan_summary": {
    "status": "confirmed",
    "head_revision": 3,
    "confirmed_revision": 3,
    "rewrite_type": "light_edit",
    "rewrite_reason_codes": ["ONE_DOMINANT_LEGACY_SECTION"],
    "source_count": 5,
    "legacy_source_count": 3,
    "web_source_count": 1,
    "stale": false,
    "write_ready": true
  }
}
```

父节点：

```json
{
  "rewrite_type": "mixed",
  "is_aggregate": true
}
```

---

## 14. Source detail

```http
GET /api/v3/workspaces/{workspace_id}/chapters/{chapter_id}/writing-plan/sources/{source_id}
```

响应旧段落：

```json
{
  "source_id": "...",
  "source_type": "legacy_paragraph",
  "document": {
    "input_id": "...",
    "filename": "旧投标书.docx"
  },
  "section": {
    "section_id": "...",
    "number": "2.1.2",
    "title": "国土调查云平台支撑",
    "path": ["2 项目理解", "2.1 项目任务背景", "2.1.2 国土调查云平台支撑"]
  },
  "blocks": [
    {
      "block_id": "...",
      "page": 12,
      "paragraph_index": 4,
      "content": "...",
      "content_hash": "..."
    }
  ],
  "risks": [...]
}
```

ACL 和 chapter scope 必须校验。

---

## 15. 错误响应标准

```json
{
  "ok": false,
  "error": {
    "code": "CHAPTER_PLAN_REVISION_CONFLICT",
    "message": "编写规划已被更新，请加载最新版本后再提交。",
    "retryable": true,
    "details": {
      "latest_plan_revision": 4,
      "latest_plan_hash": "...",
      "remediation": "reload_and_reapply"
    }
  },
  "message": "编写规划已被更新，请加载最新版本后再提交。"
}
```

---

## 16. 错误码表

| code | HTTP | retryable | 前端处理 |
|---|---:|---:|---|
| WRITING_MODE_INVALID | 400 | 否 | 返回创建页 |
| LEGACY_BID_REQUIRED | 409 | 是 | 上传旧投标书 |
| LEGACY_BID_INDEX_NOT_READY | 409/202 | 是 | 显示解析进度 |
| CHAPTER_PLAN_REQUIRED | 409 | 是 | 打开编写逻辑 |
| CHAPTER_PLAN_BUILDING | 409 | 是 | 连接 operation |
| CHAPTER_PLAN_NOT_CONFIRMED | 409 | 是 | 确认 |
| CHAPTER_PLAN_STALE | 409 | 是 | 重建 |
| CHAPTER_PLAN_HASH_MISMATCH | 409 | 是 | reload |
| CHAPTER_PLAN_REVISION_CONFLICT | 409 | 是 | 合并 |
| CHAPTER_PLAN_SOURCE_NOT_READY | 422 | 是 | 抓取/移除 |
| CHAPTER_PLAN_GATE_REJECTED | 422 | 是 | 展示 findings |
| CHAPTER_PLAN_APPROVAL_INVALID | 409 | 是 | 重新确认 |
| CHAPTER_WRITE_RESEARCH_NOT_ALLOWED | 400 | 否 | 修正客户端 |
| CHAPTER_PLAN_REVISION_REQUIRED | 422 | 是 | 返回规划补资料 |
| LEGACY_CONTENT_CONTAMINATION | 422 | 是 | 查看命中并修订 |
| STATE_UNAVAILABLE | 503 | 是 | 诊断/重试 |

---

## 17. SQL 核心草案

详细迁移见 `05_数据库迁移_Schema_兼容性设计.md`。最关键关系：

```text
chapter_workspaces
  head_plan_revision
  confirmed_plan_revision

chapter_plan_proposals
  ↓
chapter_plan_validation_reports
  ↓
chapter_plan_gate_receipts
  ↓
chapter_writing_plan_revisions
  ↓
chapter_plan_approval_receipts

chapter_content_revisions
  plan_revision
  plan_hash
  plan_approval_receipt_id
  writer_bundle_hash
```
