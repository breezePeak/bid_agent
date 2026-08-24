# ADR-16：章节编写规划版本与执行授权边界

- 状态：Accepted（PR-02）
- 日期：2026-08-24
- 关联：ADR-07、ADR-11、ADR-14

## 背景

历史 `_writing_plans.json` 是 `legacy_inline` 写作链路的内部状态，没有追加版本、
章节 CAS、精确哈希、依赖快照或认证用户确认回执。它可以继续作为旧链路兼容投影，
但不能证明 Writer 最终消费的是用户确认的哪一版规划。

## 决策

1. **H1 仍是唯一全局规划 Gate**

   `ChapterBlueprint` 仍只能通过 H1 晋级。章节 Plan 不得改变章节树、标题、父子关系、
   章节目的或评分责任，也不得重新签发或替代 H1。

2. **章节 Plan 是追加版本控制面对象**

   正式版本写入 `chapter_writing_plan_revisions`，章节工作空间只保存 head/confirmed
   指针和状态。禁止原地更新 `plan_json`。Agent、聊天或前端只能提交严格 Schema 的
   candidate，权威 binding 由服务从 control.db 和已晋级 Artifact 重建。

3. **依赖由内核重算**

   dependency fingerprint 精确覆盖当前章节、Blueprint、ProjectModel 全局上下文、
   章节上下文、SourceIndex 和证据调度快照。append 与 confirm 都在控制面重新读取并
   比较当前依赖；调用方不能提交自定义 fingerprint 或 binding 覆盖权威状态。

4. **确认回执是章节级执行授权**

   `ChapterPlanApprovalReceipt` 精确绑定 chapter、plan revision/hash、dependency
   fingerprint、完整 binding 和认证 principal。只有当前 head 且依赖未 stale 的规划
   可以确认；相同 exact 请求幂等返回同一 receipt，不同 hash 不得复用。

5. **PR-02 不改变 Writer 授权**

   所有工作空间继续使用 `legacy_inline`。当前正文按钮、章节聊天、批量写作和
   `WriterInputBundle.chapter_writing_plan` 行为不变。PR-02 的 receipt 暂不成为 Writer
   必需输入；该切换只能在后续独立 PR 完成。

6. **JSON 是兼容投影，不是正式权威状态**

   Feature Flag 开启时，现有 JSON 当前计划可 best-effort、幂等导入为
   `legacy_projection`，失败不得阻断旧写作链路。JSON 文件不删除；正式 revision、
   hash、stale 和 receipt 只从 control.db 读取。

7. **Agent 不得直接写权威状态**

   Plan proposal/append/confirm/invalidate 写路径必须经过 CommandGateway 注册的受控
   Service。确认只接受 API 注入的认证用户身份。Writer 仍只能经
   `ChapterWritingService → WriterInputBundle → ContentWriter` 运行，不新增第二套 Writer。

## 状态语义

- `not_started`：没有 Plan head。
- `current`：head 与当前依赖一致，但没有有效确认指针。
- `confirmed`：head 与当前依赖一致，confirmed 指针精确指向 head。
- `stale_blueprint`、`stale_global_context`、`stale_chapter_context`、
  `stale_source`、`stale_evidence`：对应依赖与 Plan binding 不一致。

stale 状态由当前依赖与存储 binding 确定性计算。读取和 snapshot 可以展示 stale，
但 PR-02 不阻断 `legacy_inline` Writer。

## 回滚

关闭 `BID_AGENT_CHAPTER_PLAN_V2_ENABLED` 即停止命令写入和 JSON 影子导入。新表、新列
和历史 receipt 保留；旧 Writer 不读取 confirmed 指针，因此无需删表或逆向迁移。
