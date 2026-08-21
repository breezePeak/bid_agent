# 数据库迁移、Schema 与兼容性设计

## 1. 目标

当前每个工作空间使用独立 SQLite 控制库：

```text
runs/<workspace_id>/workspace/control.db
```

当前 `ControlStore.SCHEMA_VERSION = 29`。本次所有迁移必须满足：

1. 增量、幂等、可重复执行；
2. 不删除旧表、旧列和旧历史；
3. 不在迁移中调用 LLM、网络或读取大文件正文；
4. 迁移失败时事务回滚；
5. 旧工作空间打开即迁移，但新能力默认不开启；
6. 关闭 Feature Flag 后仍可使用旧路径；
7. 所有新增 head 指针都可由历史记录重建；
8. 每个迁移版本有自动化测试和真实工作空间副本演练。

---

## 2. 版本拆分建议

不要一次从 29 跳到一个塞满所有功能的版本。建议：

| Schema | 内容 | 行为影响 |
|---:|---|---|
| 30 | 项目 writing mode、profile、capability 字段 | 无，默认兼容 |
| 31 | 章节 plan proposal/revision/receipt 表和 head 指针 | 无，Flag 关闭 |
| 32 | 正文绑定 plan 字段、索引和 staleness 支持 | 无，旧记录默认 0/空 |
| 33 | 可选：性能索引和诊断字段 | 无 |

每个版本独立合并、独立部署。当前代码虽然在 `_initialize()` 中集中执行 DDL，仍应拆出显式迁移方法，至少做到代码结构和测试按版本隔离。

---

## 3. Schema 30：项目写作模式与能力

### 3.1 `document_state` 新增字段

```sql
ALTER TABLE document_state
ADD COLUMN writing_mode TEXT NOT NULL DEFAULT 'full_write';

ALTER TABLE document_state
ADD COLUMN project_type TEXT NOT NULL DEFAULT '';

ALTER TABLE document_state
ADD COLUMN expected_pages INTEGER;

ALTER TABLE document_state
ADD COLUMN capabilities_json TEXT NOT NULL DEFAULT '{}';
```

SQLite 不支持所有版本上的复杂 `CHECK` 增量约束，业务层必须严格校验：

```text
writing_mode ∈ {full_write, bid_rewrite}
expected_pages IS NULL OR expected_pages > 0
```

### 3.2 为什么不复用 `document_mode`

`document_mode` 当前是：

```text
auto_outline
template_strict
```

`writing_mode` 是：

```text
full_write
bid_rewrite
```

二者必须并存，不能组合成诸如：

```text
bid_rewrite_template_strict
```

否则枚举会像无人管理的抽屉一样迅速塞满。

### 3.3 旧工作空间回填

```text
writing_mode = full_write
capabilities_json = {}
project_type = ''
expected_pages = NULL
```

注意：旧工作空间虽然回填 `full_write`，但仍不自动启用 plan-required。是否启用由 capability 决定：

```json
{
  "chapter_plan_v1": false,
  "plan_required_for_write": false,
  "bid_rewrite_v1": false
}
```

### 3.4 新工作空间初始化

`POST /api/v3/workspaces` 创建目录和 ControlStore 后，必须在同一请求内初始化 `document_state`：

```json
{
  "writing_mode": "full_write",
  "project_type": "",
  "expected_pages": null,
  "capabilities": {
    "chapter_plan_v1": false,
    "plan_required_for_write": false,
    "bid_rewrite_v1": false
  }
}
```

创建失败时删除刚创建但未初始化的工作空间目录，避免半工作空间。

---

## 4. Schema 31：章节规划控制面

### 4.1 `chapter_workspaces` 新增 head 指针

```sql
ALTER TABLE chapter_workspaces
ADD COLUMN head_plan_revision INTEGER NOT NULL DEFAULT 0;

ALTER TABLE chapter_workspaces
ADD COLUMN confirmed_plan_revision INTEGER NOT NULL DEFAULT 0;
```

不建议单独存 `plan_status`，因为它可从 head revision、confirmed revision、计划记录状态和 operation 派生。若为列表性能需要缓存，必须由单一 Service 原子更新，不能由前端或 Agent 写。

### 4.2 计划提案表

```sql
CREATE TABLE IF NOT EXISTS chapter_plan_proposals (
    proposal_id TEXT PRIMARY KEY,
    chapter_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    base_plan_revision INTEGER NOT NULL,
    expected_chapter_revision INTEGER NOT NULL,
    dependency_fingerprint TEXT NOT NULL,
    proposal_hash TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL,
    producer_role TEXT NOT NULL,
    prompt_version TEXT NOT NULL DEFAULT '',
    model_fingerprint TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chapter_plan_proposals_chapter
ON chapter_plan_proposals(chapter_id, created_at DESC);
```

状态：

```text
created
validated
gated
promoted
rejected
superseded
```

### 4.3 验证报告

```sql
CREATE TABLE IF NOT EXISTS chapter_plan_validation_reports (
    report_id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL UNIQUE
        REFERENCES chapter_plan_proposals(proposal_id),
    proposal_hash TEXT NOT NULL,
    report_hash TEXT NOT NULL UNIQUE,
    report_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```

### 4.4 Gate 回执

```sql
CREATE TABLE IF NOT EXISTS chapter_plan_gate_receipts (
    receipt_id TEXT PRIMARY KEY,
    receipt_hash TEXT NOT NULL UNIQUE,
    proposal_id TEXT NOT NULL
        REFERENCES chapter_plan_proposals(proposal_id),
    proposal_hash TEXT NOT NULL,
    validation_report_id TEXT NOT NULL,
    validation_report_hash TEXT NOT NULL,
    chapter_id TEXT NOT NULL,
    verdict TEXT NOT NULL,
    gate_policy_version TEXT NOT NULL,
    dependency_fingerprint TEXT NOT NULL,
    findings_json TEXT NOT NULL,
    issuer TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chapter_plan_gate_proposal
ON chapter_plan_gate_receipts(proposal_id, created_at DESC);
```

### 4.5 规划追加版本

```sql
CREATE TABLE IF NOT EXISTS chapter_writing_plan_revisions (
    chapter_id TEXT NOT NULL,
    plan_revision INTEGER NOT NULL,
    plan_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    dependency_fingerprint TEXT NOT NULL,
    parent_plan_revision INTEGER,
    proposal_id TEXT NOT NULL UNIQUE
        REFERENCES chapter_plan_proposals(proposal_id),
    proposal_hash TEXT NOT NULL,
    gate_receipt_id TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    actor_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    PRIMARY KEY (chapter_id, plan_revision)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_chapter_plan_hash
ON chapter_writing_plan_revisions(chapter_id, plan_hash);

CREATE INDEX IF NOT EXISTS idx_chapter_plan_head
ON chapter_writing_plan_revisions(chapter_id, plan_revision DESC);
```

状态：

```text
draft
confirmed
stale
rejected
```

历史 revision 不 UPDATE payload，只允许追加。状态变化也优先通过新 revision 或单独事件；若对历史记录做 `status=stale`，必须只改状态字段且写审计事件。

### 4.6 Promotion 回执

```sql
CREATE TABLE IF NOT EXISTS chapter_plan_promotion_receipts (
    receipt_id TEXT PRIMARY KEY,
    receipt_hash TEXT NOT NULL UNIQUE,
    chapter_id TEXT NOT NULL,
    proposal_id TEXT NOT NULL UNIQUE
        REFERENCES chapter_plan_proposals(proposal_id),
    proposal_hash TEXT NOT NULL,
    base_plan_revision INTEGER NOT NULL,
    promoted_plan_revision INTEGER NOT NULL,
    plan_hash TEXT NOT NULL,
    expected_chapter_revision INTEGER NOT NULL,
    resulting_chapter_revision INTEGER NOT NULL,
    dependency_fingerprint TEXT NOT NULL,
    gate_receipt_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(chapter_id, idempotency_key)
);
```

### 4.7 规划确认回执

```sql
CREATE TABLE IF NOT EXISTS chapter_plan_approval_receipts (
    receipt_id TEXT PRIMARY KEY,
    receipt_hash TEXT NOT NULL UNIQUE,
    chapter_id TEXT NOT NULL,
    plan_revision INTEGER NOT NULL,
    plan_hash TEXT NOT NULL,
    decision TEXT NOT NULL,
    review_mode TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    dependency_fingerprint TEXT NOT NULL,
    dependency_snapshot_json TEXT NOT NULL,
    confirmation_required INTEGER NOT NULL,
    actor_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(chapter_id, plan_revision, plan_hash, decision, review_mode)
);

CREATE INDEX IF NOT EXISTS idx_chapter_plan_approvals
ON chapter_plan_approval_receipts(chapter_id, plan_revision DESC);
```

### 4.8 外键策略

当前 SQLite 开启 `PRAGMA foreign_keys = ON`。新增表应引用 proposal，但不建议对 `chapter_workspaces` 设置强级联删除：

- 工作空间删除是目录整体删除；
- 章节归档不应删除计划历史；
- Blueprint 重建不应删除旧计划；
- 数据修复时保留审计链。

---

## 5. Schema 32：正文与规划绑定

### 5.1 `chapter_content_revisions` 新增字段

```sql
ALTER TABLE chapter_content_revisions
ADD COLUMN plan_revision INTEGER NOT NULL DEFAULT 0;

ALTER TABLE chapter_content_revisions
ADD COLUMN plan_hash TEXT NOT NULL DEFAULT '';

ALTER TABLE chapter_content_revisions
ADD COLUMN plan_approval_receipt_id TEXT NOT NULL DEFAULT '';

ALTER TABLE chapter_content_revisions
ADD COLUMN writer_bundle_hash TEXT NOT NULL DEFAULT '';

ALTER TABLE chapter_content_revisions
ADD COLUMN stale_reason TEXT NOT NULL DEFAULT '';
```

旧正文：

```text
plan_revision = 0
plan_hash = ''
```

表示由兼容链路生成，不伪造规划历史。

### 5.2 批量任务

现有 `chapter_batch_items.context_ref_json` 可以保存：

```json
{
  "plan_revision": 3,
  "plan_hash": "...",
  "plan_approval_receipt_id": "...",
  "chapter_revision_at_enqueue": 9
}
```

第一版不必增加列，但必须：

- 创建任务时冻结；
- 执行每一项前再次校验；
- 恢复任务时拒绝已 stale 的 item；
- 错误码明确为 `CHAPTER_BATCH_PLAN_STALE`。

若查询性能不足，再在 Schema 33 增加实体列，不先为可能的问题堆字段。

---

## 6. 迁移代码结构

在 `ControlStore._initialize()` 中增加：

```python
self._migrate_workspace_profile_v30(connection)
self._migrate_chapter_plan_v31(connection)
self._migrate_chapter_content_plan_refs_v32(connection)
```

每个方法：

1. 查询 `PRAGMA table_info(...)`；
2. 缺列才 `ALTER TABLE`；
3. `CREATE TABLE IF NOT EXISTS`；
4. `CREATE INDEX IF NOT EXISTS`；
5. 回填；
6. 运行一致性检查；
7. 成功后更新 `control_meta.schema_version`。

不要仅靠最终 `SCHEMA_VERSION` 判断是否执行，因为用户可能拥有中断迁移的数据库。

---

## 7. 迁移事务

SQLite DDL 在事务中的行为需要显式验证。建议：

```python
with connection:
    connection.execute("BEGIN IMMEDIATE")
    ...
    connection.execute("COMMIT")
```

但当前连接使用 `isolation_level=None`。实现时应统一一个迁移事务 helper，并测试：

- 正常迁移；
- 第二次重复迁移；
- 中途抛异常；
- 两个线程同时初始化；
- WAL 文件存在；
- 数据库只读；
- SQLite 版本差异。

现有 `_STORE_INIT_LOCK` 只保护同一进程；仍要考虑两个服务进程误启动。`BEGIN IMMEDIATE` 和 `busy_timeout` 必须能给出明确错误，不得产生部分列。

---

## 8. 一致性检查

迁移后运行轻量检查：

```sql
SELECT writing_mode FROM document_state LIMIT 1;
PRAGMA table_info(chapter_workspaces);
PRAGMA table_info(chapter_content_revisions);
PRAGMA foreign_key_check;
PRAGMA quick_check;
```

业务检查：

- `head_plan_revision >= confirmed_plan_revision >= 0`；
- head 指针存在对应 revision；
- confirmed revision 有 approval receipt；
- content `plan_revision > 0` 时 plan hash 不为空；
- plan proposal promoted 后有 promotion receipt；
- proposal hash、payload canonical hash 可重算。

检查失败：

```text
STATE_UNAVAILABLE / CONTROL_SCHEMA_INVALID
```

工作空间进入只读诊断，不继续自动写作。

---

## 9. 旧工作空间兼容矩阵

| 工作空间 | writing_mode | capability | 行为 |
|---|---|---|---|
| 旧库迁移后 | full_write | 无/false | 完整走当前旧写作链路 |
| 新建 full_write，Flag 关闭 | full_write | false | 当前旧链路 |
| 新建 full_write，影子模式 | full_write | plan shadow | 生成规划但不阻止旧写作 |
| 新建 full_write，新链路 | full_write | plan required | 确认规划后写作 |
| 新建 bid_rewrite，功能未开 | bid_rewrite | bid false | 创建或进入时明确提示功能未启用，不隐式降级 |
| 新建 bid_rewrite，功能已开 | bid_rewrite | plan+bid true | 完整新链路 |

`bid_rewrite` 不能悄悄降级到全量编写。用户上传旧标书就是要用，不是要看系统装作没看见。

---

## 10. API 向后兼容

### 创建请求

旧请求：

```json
{"name": "demo"}
```

继续成功，默认：

```text
writing_mode=full_write
```

新请求：

```json
{
  "name": "demo",
  "writing_mode": "bid_rewrite",
  "project_type": "国土调查",
  "expected_pages": 180
}
```

服务端严格校验。

### 章节详情

新增字段必须是可选扩展：

```json
{
  "plan_summary": null
}
```

旧前端忽略即可。

### 写作接口

旧调用没有 plan 引用：

- 旧 capability：允许；
- 新 capability：返回 `CHAPTER_PLAN_REQUIRED`。

不能在同一工作空间内随机判断。

---

## 11. 备份与恢复

### 11.1 迁移前

对每个工作空间：

1. 等待写操作完成或获得维护锁；
2. 执行 SQLite backup API，不直接复制正在写的 db；
3. 保存：
   - `control.db`
   - `-wal/-shm` 处理状态；
   - `workspace/v3` Artifact 目录清单；
   - 当前 schema version；
   - 当前 commit；
4. 计算 SHA-256。

### 11.2 恢复

1. 停止 API；
2. 移走当前损坏库，不覆盖；
3. 从 backup 恢复；
4. 启动旧代码或关闭新 Feature Flag；
5. 运行 `PRAGMA quick_check`；
6. 打开快照、章节、正文和导出验证。

新增列和表不要求删除。回滚代码只要忽略它们即可，这是增量迁移的主要价值。

---

## 12. 迁移测试清单

### 单元

- 空数据库从 0 初始化到最终版本；
- 29 → 30；
- 30 → 31；
- 31 → 32；
- 29 → 32；
- 每条路径执行两次；
- 每个 `ALTER` 前后模拟异常；
- 列已存在但 meta version 落后；
- meta version 领先但表缺失；
- 非法 writing_mode；
- head 指针孤儿。

### 集成

- 复制真实旧工作空间；
- 迁移后打开列表和快照；
- 打开所有章节；
- 使用旧路径写一章；
- 确认正文；
- 批量写两章；
- 导出 Word；
- 关闭新代码后用旧兼容路径读取。

### 并发

- 两线程同时创建 ControlStore；
- API 启动 reconciliation 与用户请求同时发生；
- 规划确认与章节上下文修改竞争；
- 规划确认重复提交；
- 数据库锁 5 秒以上的错误信息。

---

## 13. 禁止事项

- 禁止迁移时删除 `_authority.json`。
- 禁止把旧 content revision 回填成虚假 plan revision。
- 禁止因新增 plan 表而自动把旧工作空间 capability 打开。
- 禁止在前端根据“表是否存在”判断能力。
- 禁止迁移脚本直接编辑 Artifact JSON。
- 禁止先更新 head 指针再插入 revision。
- 禁止失败时吞异常后继续启动写作。
