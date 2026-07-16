# Agent 化阶段完成状态

> 更新日期：2026-07-16  
> 对应计划：[agentization_development_plan.md](./agentization_development_plan.md)

## 结论

| 阶段 | 状态 |
|------|------|
| PR-1 Tool 层 | **完成并验收** |
| PR-2 只读诊断 | **完成并验收** |
| PR-3 Supervisor 最小闭环 | **完成并验收** |
| PR-4 参数化章节 + 失效传播 | **完成并验收** |
| PR-5 GoalState + build_export（stale 强制重建） | **完成并验收** |
| PR-6 覆盖率驱动改稿闭环 + 导出门禁 | **完成并验收** |

默认 `AGENT_SUPERVISOR_ENABLED=false`，不改变现有全量流水线与旧 chat。

## PR-6 本批完成

- [x] `analyze_coverage`：只读分析评分覆盖矩阵，输出缺口与建议章节
- [x] `fix_coverage`：生成定向 rewrite 计划；`confirm_execute=true` 才真正改稿
- [x] Goal 准则 `score_coverage_min`
- [x] 话术推断：覆盖率/补齐评分点 → fix_coverage 目标
- [x] Supervisor 规则：覆盖查询 / 覆盖修复
- [x] `build_export` 遇 `compliance_report.blocking=true` → `gate_blocked`
- [x] 单测：`tests/test_coverage_loop.py`

## 当前 Tool 清单（核心）

| Tool | 类型 |
|------|------|
| run_stage | 执行注册阶段 |
| query_status / query_artifacts / diagnose_failure | 只读 |
| analyze_coverage | 只读覆盖分析 |
| write_chapters / review_chapters / rewrite_chapters | 参数化章节 |
| fix_coverage | 覆盖驱动改稿计划/执行 |
| build_export | 导出（stale 重建 + 合规门禁） |
| + 20 个 stage command 别名 | 兼容旧命令 |

## 仍未做（后续可选）

| 项 | 说明 |
|----|------|
| 前端 goal / 轨迹面板 | UI |
| LangGraph supervisor 图 | Phase 4 |
| fix_coverage 自动多轮直到达标 | 需预算熔断 |
| 合规项自动定向改写 | 可再开 PR |

## 冒烟

```bash
python -m src.main tool --list
python -m src.main tool --name analyze_coverage --args "{\"rebuild\":false}"
python -m src.main tool --name fix_coverage --args "{\"confirm_execute\":false}"
python -m src.main tool --name build_export --args "{\"targets\":[\"md\",\"docx\"],\"dry_run\":true}"
python -m unittest discover -s tests -q
```
