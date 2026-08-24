# PR-01 项目写作模式与输入角色脚手架证据

## 范围与基线

- 开发基线：PR-00 合并后的 `64f043b7df6f67c72cb4ddffcb2bc5e51da26490`。
- 开发分支：`agent/pr-01-work`。
- PR-00 合并后 main 的 GitHub Actions CI #29（Run `32677588752`）全绿。
- 本 PR 只增加模式、输入角色、持久化、能力声明和兼容边界；没有实现 planning v2、旧标书改写、目录融合或新的工作台入口。

## 数据与契约脚手架

- `ProjectWritingMode` 冻结为 `full_write`、`bid_rewrite`。
- `ChapterPlanFlowVersion` 冻结为 `legacy_inline`、`confirmed_plan_v2`。
- `InputRole` 新增 `legacy_bid`；PR-01 中该输入明确标记为 `excluded`，不进入 SourceIndex、requirements、目录或写作上下文。
- ControlStore Schema 升至 30，`document_state` 增加 `writing_mode` 和 `chapter_plan_flow`；旧数据库通过幂等加列迁移获得 `full_write`、`legacy_inline` 默认值，现有字段和数据保持不变。
- 新建工作空间默认持久化 `full_write`、`legacy_inline`；创建响应、列表响应和 workspace snapshot 均返回模式、流程版本和 capabilities。
- 旧响应缺少新字段时，前端契约归一化为 `full_write`、`legacy_inline`，保持旧工作空间兼容。

## 能力开关与失败边界

以下环境变量均默认关闭：

- `BID_AGENT_CHAPTER_PLAN_V2_ENABLED=0`
- `BID_AGENT_CHAPTER_PLAN_V2_DEFAULT=0`
- `BID_AGENT_BID_REWRITE_ENABLED=0`
- `BID_AGENT_LEGACY_OUTLINE_FUSION_ENABLED=0`

请求 `bid_rewrite` 或上传 `legacy_bid` 时，默认返回 `CAPABILITY_DISABLED`；即使打开 rewrite flag，PR-01 仍返回 `FEATURE_NOT_RELEASED`，不会创建半成品工作空间或接收文件。前端不展示“标书改写”选项，创建请求显式发送 `full_write`。

## 当前真实调用链

```text
POST /api/v3/runs
  -> 校验 ProjectWritingMode 与 capability
  -> ControlStore.initialize_document_state(full_write, legacy_inline)
  -> InputManifestService / SourceNormalizer
       -> legacy_bid: excluded（PR-01 fail closed）
       -> tender / company / score / reference: 既有链路
  -> RequirementLedger -> ScoreModel -> ProjectModel -> ChapterBlueprint
  -> H1
  -> ChapterWorkspace / internal WritingPlan
  -> ChapterWritingService
       -> inline WriterResearchCoordinator
       -> ContentWriter / ChapterEditingService
  -> H2 / ChapterBatchService / current Word export
```

现有 full_write 写作调用链和章节确认语义未改变；搜索仍在写作过程中执行。

## 测试与回归

- `python -m compileall -q src`：通过。
- `ruff check src tests --quiet`：通过。
- PR-01 定向 Python 回归：26 passed，40.21s。
- `python -m pytest tests -q --tb=line -k "not live_llm"`：528 passed、3 skipped、1 个既有预期 warning、44 subtests passed，429.71s。
- `npm ci`：因已有前端进程占用 `esbuild.exe` 失败；未终止或覆盖该外部进程。随后 `npm install --ignore-scripts` 恢复依赖，成功安装/更新依赖。
- `npm test`：61 passed、0 failed。
- `npm run build`：受仓库 `AGENTS.md`“前端验证不允许 build”约束，未执行。
- `python scripts/verify_pr00_baseline.py`：8 checks passed。

回归覆盖：旧 Schema 升级及数据保留、新工作空间默认模式持久化、旧/新 API response 兼容、非法模式、disabled/unreleased 能力错误、`legacy_bid` 不污染 SourceIndex、full_write 既有完整业务链路、当前 Word 导出和 strict template。没有新增 skip、xfail，没有删除测试或放宽断言。

## 工作区隔离

工作区中同时存在用户的旧计划删除、未跟踪的新计划原件，以及不属于 PR-01 的前端界面修改。PR-01 提交仅纳入本证据列出的后端契约、迁移、API、前端契约和对应测试，不覆盖或夹带其他修改。

## 回滚

按 PR-01 提交逆序执行 `git revert <commit>`。Schema 30 迁移是加列迁移；回滚应用代码不会删除数据库列或用户数据，旧代码会忽略新增列。不要使用 hard reset，也不要回滚 PR-00。

## Definition of Done

- [x] PR-00 合并后 main CI 全绿。
- [x] 模式、流程版本和 `legacy_bid` 枚举已冻结。
- [x] Schema 迁移对旧工作空间幂等且保持数据。
- [x] API、snapshot 和前端契约提供向后兼容默认值。
- [x] rewrite 与 legacy 输入在未发布阶段 fail closed。
- [x] 所有 PR-01 feature flags 默认关闭。
- [x] Python 全量、前端全量和 PR-00 基线脚本通过。
- [x] 未暴露 rewrite UI，未实现改写、plan v2 或目录融合。

**PR-02 未开始。**
