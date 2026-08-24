# 给 Codex 的第一次开发提示词

下面整段直接复制给 Codex。当前只执行 PR-00。

```text
你正在 `breezePeak/bid_agent` 仓库中开发。请直接检查代码、修复测试和生产回归并提交一个独立 PR，不要只给方案。

当前计划基线：
`85702e3aa60bd5e2f7b26a130ef7a6048499e020`

当前事实：
- 旧开发计划基于 `74cd1ff12a79a373f9c262d48f61e03caa3cd642`，已经过期。
- 最新 main 合并了 PR #10。
- PR #10 的 static 和 frontend job 通过，但 unit job 失败。
- 已知结果为 29 failed、492 passed、4 skipped、44 subtests passed。
- 当前不具备开始新功能开发的可信基线。

本轮唯一任务：
实施 `PR-00：修复当前主分支并冻结可信基线`。

完成 PR-00 后立即停止。禁止实施 PR-01 或任何标书改写新功能。

第一步必须执行：
1. `git status --short`
2. `git rev-parse HEAD`
3. `git log -5 --oneline`
4. 如果 HEAD 不是 `85702e3aa60bd5e2f7b26a130ef7a6048499e020` 或其可证明无冲突的后续提交，先列出差异并判断是否需要停止。
5. 不得覆盖与本任务无关的用户修改。

必须阅读：
- `docs/unified_writing_pr_plan_v2/00_START_HERE_当前只执行PR-00.md`
- `docs/unified_writing_pr_plan_v2/01_总纲_架构边界_依赖图_统一验收规则.md`
- `docs/unified_writing_pr_plan_v2/PR-00_修复当前主分支并冻结可信基线.md`
- `docs/adr/`
- 当前生产代码和全部相关测试

建议分支：
`agent/pr-00-green-baseline`

PR-00 必须完成：

一、完整复现
1. 本地运行与 CI 相同的全部命令。
2. 输出完整失败列表。
3. 对照 GitHub Actions Run `32453280179`。
4. 建立失败分类表，逐项标记：
   - production_regression
   - test_isolation_defect
   - stale_test_after_accepted_change
5. stale test 修改必须提供代码/ADR证据和替代测试。

二、处理已知失败类别
1. 确定性研究测试不得访问 Tavily 或真实网络。
2. 修复章节批量写作测试注入或生产编排回归。
3. 恢复当前受支持的章节确认开关语义。
4. 修复 Grounding 错误分类。
5. 修复 Python 单测对 frontend/dist 的错误依赖。
6. 修复 stage/generation snapshot 兼容。
7. 修复 planning、outline audit、template/source error 分类。
8. 同步 Artifact Registry 版本冻结。
9. 修复 Requirement/ProjectModel 输入结构。
10. 修复 strict template H1 依赖。
11. 修复 visual/diagram orientation。
12. 不得通过 skip、xfail、删测试或放宽断言掩盖问题。

三、冻结可信基线
1. 新增 `scripts/verify_pr00_baseline.py`。
2. 新增 `docs/unified_writing_pr_plan_v2/evidence/PR-00-baseline.md`。
3. 基线脚本检查：
   - 关键模块导入；
   - ControlStore 旧 Schema 升级；
   - ChapterWritingService 是唯一写作入口；
   - 确定性测试无网络；
   - stage registry/snapshot 一致；
   - 章节确认开关；
   - 当前 API 关键 contract；
   - Word 导出模块。
4. 记录当前全量编写真实调用链。

四、完整回归
必须运行：

python -m compileall -q src
ruff check src tests --quiet
python -m pytest tests -q --tb=line -k "not live_llm"

cd frontend
npm ci
npm test
npm run build

cd ..
python scripts/verify_pr00_baseline.py

完整 Python 测试必须连续通过两次，排除顺序和偶发状态问题。

还必须执行当前业务回归：
1. 创建 full_write 工作空间。
2. 上传招标书/评分文件。
3. 生成目录并 H1。
4. 打开章节。
5. 查看当前内部 WritingPlan。
6. 直接开始写正文。
7. 检查当前 inline research 判断。
8. 正文编辑。
9. H2。
10. 批量编写并模拟服务重启。
11. 当前 Word 导出。
12. strict template 路径。

硬性禁止：
- 不新增 writing_mode。
- 不新增 LEGACY_BID。
- 不新增 plan control tables。
- 不新增 plan approval receipt。
- 不改成“规划确认后才能写”。
- 不增加工作台双页签。
- 不把搜索前移。
- 不开发标书改写。
- 不开始 PR-01。

最终报告必须包含：
1. HEAD 和分支。
2. 29 个失败的逐项分类与处理结果。
3. 修改文件。
4. 每个测试修改的理由。
5. 所有命令真实结果。
6. 业务回归结果。
7. 基线脚本结果。
8. 回滚方法。
9. PR-00 Definition of Done。
10. 明确写出：`PR-01 未开始`。

完成后停止。
```
