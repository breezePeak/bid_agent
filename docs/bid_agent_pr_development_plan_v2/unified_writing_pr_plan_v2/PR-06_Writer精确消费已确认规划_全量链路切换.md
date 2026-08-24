# PR-06：Writer 精确消费已确认规划并切换全量编写链路


- 计划版本：`PR Plan V2.0`
- 仓库：`breezePeak/bid_agent`
- 起始基线：`85702e3aa60bd5e2f7b26a130ef7a6048499e020`
- 开发原则：本 PR 独立可部署、可测试、可回滚


## 1. 开始条件


- PR-05 已合并并全绿。
- 规划编辑、确认和 stale 已稳定。
- 至少 20 个测试章节完成 shadow 对比，没有系统性 coverage 退化。


不满足开始条件时，本 PR 立即停止。不得以“先做一部分不影响”为理由继续。

## 2. 本 PR 的唯一目标


让 `confirmed_plan_v2` 的全量编写章节只能通过已确认规划生成正文。
Writer 精确消费用户确认的来源，正文阶段不再搜索。
legacy_inline 工作空间继续沿用当前行为。


## 3. 当前代码事实


当前 `ChapterWritingService` 的顺序是：

```text
assemble bundle
→ apply request plan
→ inline research
→ ContentWriter
→ content gate
→ draft
```

当前 Bundle 只携带自由形态 plan，没有 receipt。
`WriterInputBundleAssembler._evidence_snapshot()` 会按 chapter/score/requirement 收集相关证据，
而不是按用户确认的 evidence IDs 精确收集。


## 4. 本 PR 允许修改的范围


允许：

- 扩展 WriterInputBundle；
- `ChapterWritingRequest` 增加 plan refs；
- `ChapterWritingService.require_confirmed_plan()`；
- Bundle assembler 精确 source/evidence；
- v2 flow 禁止 inline research；
- structured `chapter.plan.execute`；
- chat “开始写”调用同一 command；
- pilot workspace 切 confirmed_plan_v2；
- legacy adapter 保留。

本 PR 先支持单章全量编写。


## 5. 本 PR 明确不做


禁止：

- 删除 legacy_inline；
- 标书改写；
- legacy sources；
- v2 批量执行；
- Writer 自己搜索；
- Writer 读取整个 SourceIndex；
- Writer 使用未确认来源；
- 自动确认 plan。


## 6. 预计文件、类、接口和表


| 模块 | 变化 |
|---|---|
| `contracts.py` | Bundle plan exact fields |
| `writer_bundle.py` | `assemble_from_confirmed_plan` |
| `chapter_writing_service.py` | plan authority validation |
| `writer_research.py` | legacy adapter 保留，v2 不调用 |
| `content_writer.py` | 只读 frozen plan |
| `content_grounding.py` | source/evidence binding |
| `chapter_chat.py` | structured execute command |
| `execution_controller.py` | `chapter.plan.execute` |
| `v3_app.py` / frontend API | execute action |
| workbench | start button uses command，Pilot Flag |


文件清单是实施导航，不是让开发者不看代码就机械修改。实际文件有变化时，必须在 PR 描述中说明原因。

## 7. 详细实现顺序


### 7.1 Request

新增必需字段（v2 flow）：

```text
plan_revision
plan_hash
plan_approval_receipt_id
expected_chapter_revision
```

legacy flow 可为空。

### 7.2 Authority validation

服务必须重载：

- workspace flow；
- plan revision；
- plan hash；
- receipt；
- current dependencies；
- current confirmed pointer。

任何不一致返回明确 409。

### 7.3 Bundle assembly

Bundle 只包含：

- 当前 Blueprint slice；
- confirmed content units；
- confirmed source refs；
- exact SourceBlock snapshots；
- exact EvidenceItems；
- bindings；
- plan rewrite type；
- receipt。

用户从 plan 删除的 Evidence 不能因 topic 相关再次进入。

### 7.4 Research

v2 path 固定：

```text
run_research = False
```

Service 内明确断言：

- plan 已完成 research；
- required evidence 均可加载；
- 缺资料返回 `CHAPTER_PLAN_REVISION_REQUIRED`；
- 不允许 fallback 到 inline search。

legacy path 保留现有 coordinator 和 fallback。

### 7.5 开始编写按钮

新按钮提交 `chapter.plan.execute`。
不再把“开始编写本章正文”作为唯一控制信号交给 LLM 猜意图。

右侧聊天识别到开始写时，也调用同一 command。

### 7.6 Pilot

只有明确设置 `chapter_plan_flow=confirmed_plan_v2` 的 full_write 工作空间切新路径。
其他工作空间完全不变。

### 7.7 批量

本 PR 中 confirmed_plan_v2 的批量按钮默认禁用，并说明批量执行将在完成计划快照支持后开放。
legacy batch 不变。不得让 batch 绕过 plan gate。


## 8. 自动化测试


1. missing plan；
2. unconfirmed plan；
3. wrong hash；
4. stale dependency；
5. wrong receipt；
6. exact evidence selection；
7. removed evidence absent；
8. no inline research call；
9. insufficient plan returns revision required；
10. chat/button same command；
11. legacy direct write unchanged；
12. v2 single chapter success；
13. draft provenance includes plan refs；
14. content gate；
15. H2；
16. frontend disabled batch；
17. strict template v2 single chapter。


## 9. 人工验收场景


对同一项目复制两个 workspace：

- A：legacy_inline；
- B：confirmed_plan_v2。

执行相同章节：

1. A 按当前直接写作成功；
2. B 未确认 plan 时被阻断；
3. B 确认后成功；
4. B 写作阶段没有 research event；
5. B 正文只引用选中来源；
6. B H2 和 Word 正常；
7. A 与 B 的正文基础覆盖不低于基线。


## 10. 兼容性要求

1. 旧工作空间必须可以在本 PR 代码上直接打开。
2. 新字段必须有确定性默认值。
3. Feature Flag 关闭时现有行为保持不变。
4. 旧 API 客户端不能因为新增响应字段崩溃。
5. 新 API 在缺少能力时返回明确错误，不得静默降级。
6. 当前正式 Word、章节 H2、批量恢复和严格模板行为必须继续通过回归。

## 11. 失败处理

- Schema 或迁移失败：事务回滚，工作空间保持旧版本可读。
- CAS 冲突：返回 409 和实际 revision，不得覆盖。
- 模型输出非法：记录失败，不发布半成品。
- 搜索失败：按本 PR 明确策略处理，不得临场随意降级。
- 前端请求失败：保留本地未保存正文，展示可恢复错误。
- 服务中断：重启后从权威状态恢复，不能依赖浏览器内存。

## 12. 回滚方案


将 Pilot workspace 的 `chapter_plan_flow` 切回 `legacy_inline`。

保留 plan revisions 和 receipts。
WriterInputBundle 新字段有兼容默认，旧 bundle 可读取。
不要删除 v2 数据。


## 13. Definition of Done


- [ ] v2 Writer 只消费 confirmed plan；
- [ ] v2 写作不联网；
- [ ] legacy path 不变；
- [ ] chat/button 共用结构化命令；
- [ ] 用户删除来源不会回来；
- [ ] v2 单章完整闭环；
- [ ] 全 CI 通过；
- [ ] PR-07 未开始。


并且必须满足总纲中的统一 Definition of Done。

## 14. 推荐提交拆分

建议本 PR 内部提交顺序：

1. `test:` 锁定本 PR 当前行为和目标契约；
2. `feat/refactor:` 实现后端契约与控制面；
3. `feat:` 接入服务和 API；
4. `feat:` 接入前端（如本 PR 包含）；
5. `test:` 增加集成与回归；
6. `docs:` 更新真实逻辑和回滚说明。

不得把所有修改压成一个无法审查的 “update all changes”。

## 15. 可直接复制给 Codex 的本 PR 提示词

```text
你正在 `breezePeak/bid_agent` 仓库中实施 **PR-06：Writer 精确消费已确认规划并切换全量编写链路**。请直接检查代码、补测试并完成实现，不要只输出方案。

计划起始基线：`85702e3aa60bd5e2f7b26a130ef7a6048499e020`

开始前必须执行：
1. `git status --short`
2. `git rev-parse HEAD`
3. `git log -5 --oneline`
4. 如果 HEAD 已晚于计划基线，先审查新提交对本 PR 的影响。
5. 不得覆盖与本任务无关的用户修改。

建议分支：`agent/pr-06-writer`

开始条件：

- PR-05 已合并并全绿。
- 规划编辑、确认和 stale 已稳定。
- 至少 20 个测试章节完成 shadow 对比，没有系统性 coverage 退化。


必须阅读：
- `docs/unified_writing_pr_plan_v2/00_START_HERE_当前只执行PR-00.md`
- `docs/unified_writing_pr_plan_v2/01_总纲_架构边界_依赖图_统一验收规则.md`
- `docs/unified_writing_pr_plan_v2/PR-06_Writer 精确消费已确认规划并切换全量编写链路.md`
- 与本 PR 有关的 ADR、生产代码和现有测试

本 PR 唯一目标：

让 `confirmed_plan_v2` 的全量编写章节只能通过已确认规划生成正文。
Writer 精确消费用户确认的来源，正文阶段不再搜索。
legacy_inline 工作空间继续沿用当前行为。


允许范围：

允许：

- 扩展 WriterInputBundle；
- `ChapterWritingRequest` 增加 plan refs；
- `ChapterWritingService.require_confirmed_plan()`；
- Bundle assembler 精确 source/evidence；
- v2 flow 禁止 inline research；
- structured `chapter.plan.execute`；
- chat “开始写”调用同一 command；
- pilot workspace 切 confirmed_plan_v2；
- legacy adapter 保留。

本 PR 先支持单章全量编写。


硬性禁止：

禁止：

- 删除 legacy_inline；
- 标书改写；
- legacy sources；
- v2 批量执行；
- Writer 自己搜索；
- Writer 读取整个 SourceIndex；
- Writer 使用未确认来源；
- 自动确认 plan。


执行要求：
1. 先列出当前真实调用链、将修改的文件和风险点。
2. 先补能证明当前行为和目标行为的测试，再改实现。
3. 每完成一个小步骤立即运行对应局部测试。
4. Feature Flag 关闭时必须保持现有行为。
5. 旧测试与当前受支持行为冲突时，不得直接删除；必须给出证据并补替代测试。
6. 数据库迁移必须可重复、可升级旧工作空间、不可破坏原表。
7. 完成后运行全部门禁命令。
8. 最终提交本 PR 报告，然后停止，不得开始 PR-07。

必须运行：
python -m compileall -q src
ruff check src tests --quiet
python -m pytest tests -q --tb=line -k "not live_llm"
cd frontend
npm ci
npm test
npm run build

本 PR 特别要求：

- 必须证明 v2 path 没有调用 research coordinator。
- 必须保留 legacy_inline 回归。
- 不得在本 PR 开启 v2 batch。


最终报告必须包含：
- 修改文件清单；
- 数据库和 API 变化；
- 新增/修改测试清单；
- 每条命令的真实结果；
- 兼容性验证；
- 人工验收结果；
- 回滚方法；
- Definition of Done 逐项结果；
- 明确写出“未开始下一 PR”。
```
