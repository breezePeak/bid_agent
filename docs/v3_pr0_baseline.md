# V3 PR-0 基线冻结记录

> 日期：2026-07-26  
> 对应计划：[v3_development_plan.md](./v3_development_plan.md) 的 PR-0

## 工作树归类（只读盘点）

本记录创建时工作树已有 53 个已跟踪修改和 9 个未跟踪文件；它们均在 V3 开发开始前存在，未被回滚或覆盖。完成分类和回归验证后，本 PR 将它们与 PR-0 保护性修复一起提交为可回放基线。

| 分类 | 涉及范围 | 处理原则 |
|---|---|---|
| V2 控制面与界面切换 | `src/control_plane.py`、`src/web_app.py`、`src/agent/*`、`frontend/src/**`、V2 控制面测试 | 保留，后续 V3 在其唯一控制面上演进 |
| V2 内容链与质量改动 | `src/chapter_*`、`src/*checker.py`、`src/pipeline_*`、`prompts/**` 及对应测试 | 保留，待相应 V3 替代通过测试后删除 |
| 实验性项目理解与联网研究 | `src/project_understanding.py`、`src/reference_extractor.py`、`src/web_research.py`、相关 prompts/tests | 保留作迁移参考；禁止进入自动生产链 |
| V3 设计资料 | `docs/current_logic_flow_v3.md`、`docs/v3_development_plan.md`、本记录 | 作为后续 PR 的实施契约；不代表现行运行时 |
| 其他文档说明 | `readme.MD`、`docs/research_and_writing_brief.md`、`docs/current_logic_flow.md` | 保留，后续按 V3 切换节奏更新 |

## 冻结的回归场景

以下场景是后续 V3 测试与验收必须保留的最小复现夹具，不把当前错误行为当作可接受结果：

1. 深层模板：7 个一级标题、198 个结构节点时，旧 `plan_chapter_jobs` 会创建 198 个独立 Writer 任务。
2. 评分点重复：92 个评分点累计 1198 次绑定；其中 `S036` 曾被绑定 131 次。
3. 模板正文：旧 Renderer 通过 `_clear_document_body_after_cover` 或 `_clear_document_body` 清除原模板正文，再由 Markdown 重新追加。
4. 模板异常：旧 Renderer 读取模板失败时会新建空白 `Document()` 继续输出。

## PR-0 已实施的保护

- `analyze_project_understanding` 与 `research_project_materials` 标记为非自动阶段，不能被正常 Pipeline 或 LangGraph 自动链调度。
- 正常 Pipeline 在发现已注册但缺少实现的 stage 时立即抛错，不再静默 `continue`。
- `tests/test_v3_pr0_baseline.py` 固定自动阶段与 Main Runner 的一对一完整性检查。

## 验收证据

- 测试命令：`python -m unittest tests.test_v3_pr0_baseline tests.test_pipeline_registry tests.test_main_v2_cli_guard`
- 后续状态：PR-0 完成后进入 PR-1；当前旧 Graph 仍为迁移期间的历史路径，不作为 V3 基线验收入口。
