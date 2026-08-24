# PR-03 全量编写规划与搜索前移：实施证据

- 分支：`agent/pr-03-work`
- 基线：`438ab7b feat: add chapter writing plan control plane`
- 范围：只实现全量编写的 v2 影子规划；未切换 Writer，未开始 PR-04。

## 已完成主链

1. `ChapterWritingPlanBuilder` 从 legacy WritingPlan 生成稳定有序的 content units。
2. 规划来源覆盖招标要求、评分义务、全局项目事实、章节资料、用户材料块、兄弟章引用和 Web Evidence。
3. source ID/content hash 确定性生成；source binding 严格校验来源和目标内容块。
4. 按内容块执行资料充分性判断：项目事实充分时禁用公开检索，政策/标准/规范缺口才检索，企业事实和项目承诺禁止检索。
5. 规划搜索复用 Writer 的 Provider、重试和 Evidence 发布内核；Evidence 通过不可变 batch snapshot 引用进入规划。
6. `chapter.plan.shadow.generate` 从已晋级的 ChapterBlueprint、RequirementLedger、ScoreModel、ProjectModel、SourceIndex 和章节 Context 自动装配输入。
7. legacy WritingPlan 保存时在双 Flag 开启条件下自动执行影子入口；失败被持久化为 `shadow_failed`，不移动 plan head，不确认规划，不阻断 legacy 写作。
8. workspace snapshot 暴露 shadow status、diff、error 和聚合计数；成功/失败事件记录耗时、来源数、搜索数和错误数。
9. Writer 继续读取 legacy inline plan 并执行原 inline research；本 PR 没有改变正文授权。

## 主要文件

- `src/document_pipeline/chapter_writing_plan_builder.py`
- `src/document_pipeline/chapter_writing_plan.py`
- `src/document_pipeline/writer_research.py`
- `src/document_pipeline/chapter_chat.py`
- `src/document_pipeline/contracts.py`
- `src/document_pipeline/workspace_modes.py`
- `src/document_pipeline/workspace_snapshot.py`
- `src/document_pipeline/execution_controller.py`
- `src/control_plane.py`
- `tests/test_pr03_chapter_writing_plan_builder.py`
- `tests/test_pr02_chapter_writing_plan.py`

## 数据库与 API

- 没有新增规划权威表；继续使用 PR-02 的 append-only plan revision 和 plan event。
- `chapter_plan_events` 新增事件类型：`shadow_succeeded`、`shadow_failed`。
- 新增 Command：`chapter.plan.shadow.generate`。
- 新增环境变量：`BID_AGENT_CHAPTER_PLAN_SHADOW_ENABLED`。
- 旧客户端响应字段保持兼容；新增 shadow 字段均为附加字段。

## 自动化验证

- PR-03/PR-02/Writer research 专项：`31 passed`。
- 章节规划、章节 Agent、研究规划、正文阶段相关回归：`86 passed`。
- 后端全量非 live 回归：`552 passed, 3 skipped, 44 subtests passed`。
- `python -m compileall -q src`：通过。
- `python -m ruff check src tests --quiet`：通过。
- `git diff --check -- src tests`：通过。
- 根据仓库 `AGENTS.md`，未执行前端 build；PR-03 没有前端改动。

全量测试有一条既有 score semantic batch cache 损坏告警，测试仍通过，与 PR-03 修改无关。

## Definition of Done

- [x] v2 plan sources/units/bindings 完整。
- [x] 搜索可在规划阶段完成并绑定 Evidence snapshot。
- [x] legacy Writer 未切换。
- [x] shadow 失败不阻断且可在 snapshot 查看。
- [x] 企业事实和项目承诺禁搜边界通过。
- [x] source ID/hash、顺序和绑定确定性通过。
- [x] 两章并发构建不串来源。
- [x] 后端全量非 live 回归通过。
- [x] 未开始 PR-04。

## 回滚

关闭 `BID_AGENT_CHAPTER_PLAN_SHADOW_ENABLED` 即恢复 PR-02 legacy projection；关闭 `BID_AGENT_CHAPTER_PLAN_V2_ENABLED` 则完全关闭 plan v2 控制面。共享 research executor 保留 legacy adapter，不影响当前 inline research。
