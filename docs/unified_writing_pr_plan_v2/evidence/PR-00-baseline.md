# PR-00 可信基线证据

## 基线与复现

- 计划基线：`85702e3aa60bd5e2f7b26a130ef7a6048499e020`。
- 开发分支：`agent/pr-00-green-baseline`。
- 本地环境：Python 3.11.15、Node 18.20.8、npm 10.8.2；CI 使用 Python 3.11、Node 20。
- GitHub Actions Run `32453280179`：static 成功、frontend 成功、unit 失败，记录为 29 failed、492 passed、4 skipped、44 subtests passed。
- 基线本地复现：26 failed、496 passed、3 skipped、44 subtests passed。与 CI 相差的 3 项是环境相关的 research published、Grounding 分类和无 `frontend/dist` shell 隔离；三项均纳入下表。

## 29 项失败分类与处理

| # | 失败测试/场景 | 分类 | 处理与替代证据 |
|---:|---|---|---|
| 1 | autonomous research published evidence | test_isolation_defect | 注入确定性 semantic reviewer；增加禁止网络的基线检查，并修复否定式安全说明被误判为禁止检索。 |
| 2 | research tool published status | test_isolation_defect | ResearchTool 接受 reviewer 注入，测试使用离线 provider/reviewer，不再进入 Tavily/真实模型。 |
| 3 | chapter batch worker commit rejection | test_isolation_defect | 恢复 `ChapterWritingService` factory 注入缝隙；测试替换已消失的 `CommandGateway` patch 点。 |
| 4 | chapter batch sequential revision | test_isolation_defect | 使用 fake `ChapterWritingService.iter_events` 验证前章提交后才启动后章。 |
| 5 | AI draft confirmation phase | production_regression | 恢复统一 `_confirmation_required()`，关闭时自动生成正式指针。 |
| 6 | manual edit confirmation phase | production_regression | 编辑路径复用同一确认开关及 finalize 逻辑。 |
| 7 | restore revision confirmation phase | production_regression | 恢复版本路径复用同一确认开关及 finalize 逻辑。 |
| 8 | H2 chapter confirmation phase | production_regression | 四种内容变更使用同一开关语义，开/关均有测试。 |
| 9 | Grounding generic policy classification | production_regression | 明显通用采购政策在无项目锚点时确定性归类为 `PROJECT_SPECIFICITY_MISSING`。 |
| 10 | Python login shell depends on frontend/dist | test_isolation_defect | 无 dist 时仍返回含 `id="app"` 的稳定 shell；新增隔离测试。 |
| 11 | generation snapshot legacy stage | production_regression | 历史 `execute_content_plan` 映射为 `chapter_writing`。 |
| 12 | generation current-writing drawer | stale_test_after_accepted_change | fixture 改用当前 `chapter_writing`；另保留历史 stage 映射测试。 |
| 13 | generation research/evidence drawer | stale_test_after_accepted_change | fixture 改用当前 stage，并继续断言完整检索与证据采用轨迹。 |
| 14 | multipoint node-sharing controlled repair | stale_test_after_accepted_change | ADR/当前 G2 允许相关条件共享同一业务章节；替代测试严格断言两条件落在同一节点且仅调用模型一次。 |
| 15 | Artifact Registry version freeze | stale_test_after_accepted_change | 依赖图已接受变更为 v9；冻结期望同步为 9。 |
| 16 | ProjectModel tender skeleton | production_regression | Project input 恢复有界 `requirements` projection，保留 ID、kind、正文、状态和严重度。 |
| 17 | company fact vs external reference | production_regression | Project source context 纳入 company、继续排除 reference，防止外部案例冒充企业事实。 |
| 18 | direct score/outline provider pipeline | production_regression | 过滤评分档评价句并生成业务标题，防止评价句成为目录。 |
| 19 | invalid outline candidate fallback | stale_test_after_accepted_change | ADR-10 要求模型输出非法时 fail closed；替代测试断言 `V3_OUTLINE_INFERENCE_INVALID` 且不晋级规则替代物。 |
| 20 | final outline audit fallback | stale_test_after_accepted_change | 替代测试断言最终审核失败立即阻断，不发布 needs-review 目录。 |
| 21 | fallback template conflict | stale_test_after_accepted_change | 替代测试直接断言模板 finding 为 `V3_BLUEPRINT_TEMPLATE_BLOCKED` 且不晋级。 |
| 22 | unknown ScoreResponseUnit classification | production_regression | 保留根因链并返回 `V3_OUTLINE_SOURCE_REFERENCE_INVALID`。 |
| 23 | strict template title/order classification | production_regression | 模板根因返回 `V3_OUTLINE_TEMPLATE_INVALID`；最终模板 audit 使用独立阻断码。 |
| 24 | score review warning outline path | production_regression | score、deterministic outline、LLM merge 和 G2 audit 一致过滤纯评分档评价句。 |
| 25 | StageRunner old content chain | stale_test_after_accepted_change | `execute_content_plan` 已退出新运行；替代测试通过唯一 `ChapterWritingService` 写全部计划单元并断言产出正文。 |
| 26 | StageRunner render-before-quality | stale_test_after_accepted_change | 替代测试直接断言未完成正文时 `RENDER_BLOCKED_STALE_CONTENT`，未知 stage 仍 fail closed。 |
| 27 | G2 without H1 old writer stage | stale_test_after_accepted_change | 替代测试从唯一写作入口发起请求，仍严格断言 `PLANNING_CONFIRM_REQUIRED` 且无 bundle。 |
| 28 | strict template H1 writer path | stale_test_after_accepted_change | 按 ADR-13 先产生 ProjectModel，再 H1；写作改走唯一 ChapterWritingService，继续断言模板 slot 定向。 |
| 29 | diagram orientation | production_regression | 恢复 sibling chapter visual/method/overview 角色投影，diagram 章节重新归为 visual。 |

没有新增 skip、xfail，没有删除测试，也没有放宽 G2、模板、来源或 H1 硬门禁。

## 当前真实调用链

```text
full_write workspace
  -> InputManifestService / SourceNormalizer
  -> RequirementLedger -> ScoreModel -> ProjectModel -> ChapterBlueprint
  -> H1（当前规划确认）
  -> ChapterWorkspace / internal WritingPlan
  -> ChapterWritingService（唯一公共写作入口）
       -> WriterInputBundleAssembler
       -> inline WriterResearchCoordinator（写作过程中判断与检索）
       -> ContentWriter
       -> WriterBundleContentGate
       -> ChapterEditingService draft revision
  -> manual edit / H2
  -> ChapterBatchService -> ChapterWritingService（逐章、可恢复）
  -> current_word_export.build_current_word
```

直接要求写正文不等待内部 WritingPlan 确认；搜索仍在写作过程中执行。本 PR 没有新增 planning control table、plan receipt、writing mode、双页签或改写入口。

## 基线脚本

`python scripts/verify_pr00_baseline.py` 检查：关键导入、ControlStore v1 到当前 Schema 升级、统一写作入口、离线确定性研究、历史 stage 映射、章节确认开关、关键 API route、Word 导出模块。失败时非零退出。

## 回归记录

最终命令结果在 PR 提交前更新：

- `python -m compileall -q src`：通过。
- `ruff check src tests --quiet`：通过。
- `python -m pytest tests -q --tb=line -k "not live_llm"` 第一次：523 passed、3 skipped、1 warning、44 subtests passed，334.47s。
- 同一 Python 全量第二次：523 passed、3 skipped、1 warning、44 subtests passed，317.44s。
- `npm ci`：通过，安装 63 packages。首次因本仓库既有 `npm run dev` 锁定 esbuild 失败；确认命令行归属后停止该开发服务器并重跑成功。
- `npm test`：59 passed、0 failed。
- `npm run build`：受仓库 `AGENTS.md` 的“前端验证不允许 build”约束，未执行并在交付中明确披露。
- `python scripts/verify_pr00_baseline.py`：8 checks passed。

业务回归集合 `test_writing_plan_flow.py`、chapter workspace/chat/content phases、chapter batch、current Word export、strict template：58 passed、6 subtests passed。全量测试同时覆盖 full_write 的上传/目录/H1、打开章节、内部 WritingPlan、直接写正文、inline research、编辑、H2、批量恢复、Word 导出和 strict template contract。

## 回滚

仅按 PR-00 提交逆序执行 `git revert <commit>`；不得 reset 到 `74cd1ff...`，也不得覆盖用户现有文档修改。PR-00 不新增数据库表或产品模式，回滚后数据库仍由旧代码按既有 Schema 读取，但会回到红色测试基线。

## Definition of Done

- [x] 29 个已知失败均已分类并有代码修复或更强替代测试。
- [ ] GitHub Actions 三个 job 全绿（推送 PR 后确认）。
- [x] 本地 Python 全量连续通过两次。
- [x] 当前 full_write、WritingPlan、直接写作、inline research、编辑、H2、批量恢复、Word 导出和 strict template 回归通过。
- [x] 基线脚本已新增并可独立失败退出。
- [x] 没有新增后续功能。
- [ ] PR 合并后 main 全绿（合并后条件，不在本分支内伪造）。

**PR-01 未开始。**
