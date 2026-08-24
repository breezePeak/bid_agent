# PR-02 章节编写规划控制面与版本确认内核证据

## 范围

- 基线：`afe12d661b0f57a5406ac7e780472fbb6b366d7d`。
- 分支：`agent/pr-02-work`。
- 只实现 Plan v2 权威控制面、影子导入和只读投影。
- 未切换 Writer，未实现搜索前移、前端双页签、旧投标书解析、目录融合或标书改写。

## 数据库与契约

- ControlStore Schema 升至 31。
- `chapter_workspaces` 增加 head/confirmed Plan 指针和确定性状态。
- 新增 append-only Plan revision、exact approval receipt、chapter plan event 三张表。
- Plan candidate、payload、binding、content unit、revision、receipt、status 均为
  `extra=forbid` 强类型模型。
- Plan hash 使用 canonical JSON；dependency fingerprint 覆盖 Blueprint、全局上下文、
  章节上下文、SourceIndex 和 evidence snapshot。

## 调用链

```text
Agent/UI candidate
  → POST workspace commands
  → CommandGateway
  → ChapterWritingPlanService
  → ControlStore transaction
       strict schema
       leaf check
       chapter CAS
       authoritative dependency reload
       append revision / exact receipt / event

GET chapter plan
  → ControlStore reload current dependencies
  → deterministic current/confirmed/stale status
  → plan + receipt + current dependency snapshot
```

`legacy_inline` 的正文链路保持：

```text
ChapterAgentService
  → compatibility _writing_plans.json
  → ChapterWritingService
  → WriterInputBundle
  → ContentWriter
```

Flag 开启时，JSON 保存会 best-effort 影子追加 `legacy_projection`；失败被隔离，不影响
上述旧链路。

## 自动化覆盖

专项测试覆盖：

1. Schema extra forbid；
2. canonical plan hash；
3. append-only 与 parent revision；
4. stale chapter CAS；
5. duplicate append 幂等；
6. exact receipt binding 和重试幂等；
7. 全局/章节/SourceIndex/evidence 依赖 stale；
8. 依赖变化后确认失败；
9. 认证用户确认；
10. legacy JSON seed 幂等和失败隔离；
11. 旧 legacy_inline 行为；
12. snapshot summary 与只读 API；
13. 服务重启 pointer/receipt 恢复；
14. SQLite 中断事务无半 revision。

## 验证结果

- 修改文件语法检查：通过。
- 修改文件 Ruff：通过。
- PR-02 专项：13 passed。
- 章节/上下文/正文/聊天/PR-01 必要回归：72 passed、6 subtests passed。
- PR-00 基线：8 checks passed。
- 全量确定性 Python：542 passed、3 skipped、44 subtests passed；1 条既有 batch cache
  损坏模拟 RuntimeWarning。
- 前端测试：61 passed。
- 前端 build：按仓库 `AGENTS.md` 约束不执行。

## 回滚

关闭 `BID_AGENT_CHAPTER_PLAN_V2_ENABLED`。数据库新增表列保留；旧工作空间继续使用
`full_write + legacy_inline`，Writer 不读取 Plan receipt。JSON 文件不删除。

## Definition of Done

- [x] 规划权威状态进入 control.db。
- [x] JSON 只作为 legacy_inline 兼容状态和可导入投影，正式 Plan 不以 JSON 为权威。
- [x] exact receipt binding 测试通过。
- [x] legacy_inline 行为未切换。
- [x] ADR-16 已冻结。
- [x] 全量门禁完成（前端 build 按仓库约束豁免）。
- [x] PR-03 未开始。
