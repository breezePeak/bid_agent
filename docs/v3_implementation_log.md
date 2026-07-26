# V3 开发实施记录

本记录与 [v3_development_plan.md](./v3_development_plan.md) 同步。每个完成的 PR 都必须包含验证证据和独立 Git 提交。

## PR-0：冻结基线与消除半接入状态

- 状态：已完成
- 提交：`9040c68 chore(v3): freeze PR-0 baseline`
- 记录：[v3_pr0_baseline.md](./v3_pr0_baseline.md)
- 验证：`python -m unittest tests.test_v3_pr0_baseline tests.test_pipeline_registry tests.test_main_v2_cli_guard`（10 tests passed）。全量 `unittest discover` 在 120 秒超时，未用于验收结论。

## PR-1：契约、Artifact 和 Schema V3

- 状态：已完成
- 内容：新增 `document_pipeline` Pydantic v2 领域契约、V3 Artifact 命名空间约束和 `control.db` V3 表；要求台账、项目模型、文档契约、全文计划、内容单元、内容块、终稿、质量报告及 ChangeSet 均携带版本与来源哈希。
- 数据库：Schema 版本从 19 升至 20；新增 `document_state`、`evidence_needs`、`content_unit_states`、`dependency_edges`、`change_sets` 和 `content_locks`。
- 验证：`python -m unittest tests.test_v3_contracts tests.test_v3_pr0_baseline tests.test_artifact_manifest`（17 tests passed）。
