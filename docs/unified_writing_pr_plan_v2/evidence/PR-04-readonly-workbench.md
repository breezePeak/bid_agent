# PR-04 只读编写逻辑工作台开发记录

## 当前状态

PR-04 已启动，分支为 `codex/pr-04-work`。本阶段仅实现“编写逻辑 / 正文”双页签与只读规划展示，未开始 PR-05。

## 已完成

- 增加严格的章节规划前端读取 contract，校验章节、状态、来源、内容块和绑定引用。
- 接入现有 `GET /api/v3/workspaces/{workspace_id}/chapters/{chapter_id}/plan` 只读接口。
- 增加来源材料、计划内容块、当前章节三列关系图。
- 使用 CSS Grid 与 SVG 贝塞尔线展示绑定关系，使用 `ResizeObserver` 在容器变化后重绘。
- 增加来源分类折叠、来源/内容块/绑定详情抽屉，所有交互均为只读。
- 增加 loading、error、legacy unavailable、current、confirmed、stale 状态。
- 章节切换会取消上一章规划请求，并用 token 防止旧响应串入新章节。
- 正文编辑器使用 `v-show` 保持挂载；切换页签不会销毁未保存编辑状态。
- 默认页签规则：有正文默认正文；无正文且有规划默认编写逻辑。
- 增加独立回滚开关 `BID_AGENT_CHAPTER_PLAN_WORKBENCH_ENABLED`，关闭时保持原工作台行为。

## 验证

- 前端 `npm test`：65 passed。
- PR-01/PR-02/PR-03/章节工作台后端专项：40 passed，6 subtests passed。
- `python -m compileall -q src`：通过。
- `ruff check src/document_pipeline/workspace_modes.py --quiet`：通过。
- `git diff --check`：通过。
- 遵照仓库要求，未执行前端 build。

## 尚未完成

- 真实浏览器中对三栏拖拽、窗口缩放和长数据的人工视觉验收。
- 对真实 shadow plan 工作空间执行端到端验收。
- 完整 CI 回归与 PR-04 最终提交报告。

## 回滚

将 `BID_AGENT_CHAPTER_PLAN_WORKBENCH_ENABLED` 设为 `0` 或移除。只读接口、规划数据和新增组件可以保留，不影响正文编辑、一键编写、批量编写或聊天。
