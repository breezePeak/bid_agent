# 标书 Agent 当前逻辑流程

本文档描述 2026-07 当前代码实现下的实际流程，重点覆盖：
- 阶段注册表
- Graph/CLI/Web 如何共用流程定义
- 每阶段输入输出
- 运行时事件与追踪
- 上下文预算
- 质量门禁
- 项目类型化 prompt
- 人工复核覆盖层

## 1. 总体结构

当前系统不再由三套独立流程配置驱动，而是统一收敛到一份阶段注册表：

- `src/pipeline_registry.py`
  定义 `StageSpec`、`RunArtifact`
- `src/main.py`
  基于注册表执行 CLI 串行流水线
- `src/graph/bid_graph.py`
  基于注册表顺序构建 LangGraph 主图
- `src/web_app.py`
  基于注册表展示 Web 流程台

配套运行时能力：

- `src/prompt_registry.py`
  定义 `AgentSpec`，管理 prompt 文件、版本、checksum、输入/输出 contract
- `src/runtime_context.py`
  记录单次 agent 调用的输入摘要、模型、temperature、token 估算和 prompt 元数据
- `src/project_profile_registry.py`
  管理项目类型、profile 选择和 prompt variant 解析
- `src/manual_review.py`
  管理人工复核覆盖层、摘要聚合和局部重跑建议
- `src/graph/state_recorder.py`
  记录运行状态、事件流、阶段指标和 agent artifact
- `src/pipeline_supervisor.py`
  负责后端自动流水线调度、暂停、重试、重启接管和持久化控制状态

## 2. 实际主流程

当前主流程严格按下列顺序执行：

```text
START
  → init_workspace
  → prepare_inputs
  → split_docs
  → parse_score
  → extract_facts
  → build_template_evidence
  → generate_outline
  → plan_chapter_jobs
  → select_contexts
  → write_chapters
  → review_fix_chapters
  → build_source_trace_index
  → build_score_coverage_matrix
  → estimate_final_score
  → summarize_chapters
  → global_review
  → compliance_check
  → build_markdown
  → build_docx
  → check_format
  → END
```

说明：
- `run` 和 `graph-run` 的阶段顺序一致，均以 `pipeline_registry.STAGE_SPECS` 为准
- Graph 进度文案由 registry 动态生成 `[i/n]`，不再手写分母
- `graph-run --resume` 会按事件流和产物校验决定是否复用
- Web 流程台展示的节点顺序与这里一致
- 报价/偏离表同时解析 Markdown 与 `outputs/final.docx`

## 3. 分阶段说明

### 3.1 `init_workspace`

职责：
- 初始化 `sources/`、`inputs/`、`workspace/`、`outputs/`、`prompts/`
- 写入默认提示词占位文件

关键输出：
- 基础目录
- 默认 prompt 文件

### 3.2 `prepare_inputs`

职责：
- 遍历 `sources/tender/`、`sources/company/`、`sources/template/`
- 将 PDF/DOCX/MD 统一转换
- 对招标文件做切块和 AI 分类
- 生成 `inputs/tender.md`、`inputs/score.md`、`inputs/company.md`
- 复制首个模板到 `inputs/template.docx`

关键中间产物：
- `workspace/imported/tender_raw.md`
- `workspace/imported/tender_blocks.json`
- `workspace/imported/tender_classified_blocks.json`
- `workspace/imported/tender_classification_report.json`
- `workspace/imported/tender_other.md`
- `workspace/template_schema.json`

相关代码：
- `src/input_preparer.py`
- `src/tender_extractor.py`
- `src/template_analyzer.py`

### 3.3 `split_docs`

职责：
- 将 `inputs/tender.md`、`inputs/company.md` 切成结构化 chunk

关键输出：
- `workspace/chunks/tender_chunks.json`
- `workspace/chunks/company_chunks.json`

### 3.4 `parse_score`

职责：
- 从 `inputs/score.md` 中先抽取原始评分要求
- 再整理为最终评分点

关键输出：
- `workspace/score_requirements.json`
- `workspace/score_points.json`

agent：
- `score_requirement_extractor`
- `score_point_parser`

### 3.5 `extract_facts`

职责：
- 从招标文件抽取项目需求与约束
- 从公司资料抽取可复用事实
- 合成为 `global_facts`

关键输出：
- `workspace/tender_requirements.json`
- `workspace/company_facts.json`
- `workspace/global_facts.json`

agent：
- `tender_requirement_extractor`
- `company_facts_extractor`

### 3.6 `build_template_evidence`

职责：
- 将模板 schema 中的标题、writing task、fill slot 映射到事实依据
- 生成模板质量报告

关键输出：
- `workspace/template_evidence_map.json`
- `workspace/template_quality_report.json`

### 3.7 `generate_outline`

职责：
- 结合评分点、招标需求、全局事实、模板结构和模板依据映射生成大纲

关键输出：
- `workspace/outline.json`

质量门禁：
- 所有评分点必须至少绑定到一个章节

### 3.8 `plan_chapter_jobs`

职责：
- 将大纲展开为章节任务包
- 将模板任务附着到章节

关键输出：
- `workspace/jobs/*.json`

每个 job 典型内容：
- `chapter_id`
- `chapter_title`
- `score_point_ids`
- `writing_requirements`
- `sections`
- `template_tasks`

### 3.9 `select_contexts`

职责：
- 先用 `chunk_ranker` 本地粗筛
- 再由 LLM 从候选 catalog 中选择当前章节最相关的 tender/company chunk

关键输出：
- `workspace/contexts/*_context.json`
- `workspace/contexts/*_ranked_chunks.json`

上下文预算：
- 控制候选 catalog 进入模型前的字符数
- 控制每侧最多候选数
- 在结果里写入 `selection_meta`
- 可叠加人工复核偏好 chunk id 和补充说明

### 3.10 `write_chapters`

职责：
- 根据 job、selected context、全局事实、招标需求、模板任务生成章节 Markdown

关键输出：
- `workspace/chapters/*.md`

关键约束：
- 不允许编造未提供的公司事实
- 对 `weak/missing` 模板任务只能谨慎表述
- 进入模型前会将 chunk payload 压缩成 compact context
- 会消费章节级人工复核指令

质量门禁：
- 弱证据内容不得写成“已具备/已提供/已完成”等既成事实

### 3.11 `review_fix_chapters`

职责：
- 审核章节是否覆盖评分点
- 按 severity 分级问题（blocker / major / minor），并生成 `priority_fixes`
- 仅当存在可自动改稿的 blocker/major（`rewrite_status=need_rewrite`）时自动改稿，最多 2 轮
- `need_evidence` / 纯缺材料问题：分流人工补资料，不进入自动改稿
- 改稿优先消费 `priority_fixes`；复审带上轮 fixes 做定向验收
- 同一批 blocker/major 连续 2 轮未收敛：标记 `stuck` 并停止自动改稿

关键输出：
- `workspace/reviews/*_review.json`（含 `priority_fixes` / `max_severity` / `need_evidence` / `rewrite_status`）
- `workspace/rewrites/*_rewrite_log.json`

agent：
- `chapter_reviewer`
- `chapter_rewriter`

### 3.12 `build_source_trace_index`

职责：
- 构建章节来源追溯索引

关键输出：
- `workspace/source_traces/*_sources.json`
- `workspace/source_trace_index.json`

### 3.13 `build_score_coverage_matrix`

职责：
- 汇总评分点 -> 章节任务 -> 审核覆盖结果
- 叠加硬指标：关键词命中率、要求词重叠、`level_hint`
- 硬指标可下调 LLM 过于乐观的覆盖结论

关键输出：
- `workspace/score_coverage_matrix.json`（含 `hard_metrics` / `hard_uncovered_score_points`）

### 3.13b `estimate_final_score`

职责：
- 按「评分点满分 × 覆盖档位系数」估算终稿得分
- 覆盖档位融合 LLM 与硬指标（取更严）
- 输出保守/基准/乐观区间、失分项与等级

关键输出：
- `workspace/final_score_estimate.json`
- `outputs/score_estimate.md`

说明：
- 为系统参考分，不是评标委员会正式打分
- 无分值评分点不计入总分

### 3.14 `summarize_chapters`

职责：
- 为每章生成结构化摘要，供全文审核优先使用

关键输出：
- `workspace/summaries/*_summary.json`

agent：
- `chapter_summarizer`

### 3.15 `global_review`

职责：
- 优先基于章节摘要进行全文一致性审核
- 若无摘要则回退到完整章节正文

关键输出：
- `workspace/global_review.json`

关键状态：
- 如果 `need_manual_review=true`，整体运行状态标记为 `warn`
- 已接受/已解决的全文风险会在结果层被过滤，避免重复报出

agent：
- `global_reviewer`

### 3.16 `compliance_check`

职责：
- 规则优先的专项合规检查（9 类）
  - 资格条件（默认 warn/人工，禁止关键词假 pass）
  - 废标条款（默认 warn/人工，禁止空 pass）
  - ★▲强制参数
  - 签字盖章（文本无法验真，禁止 pass）
  - 保证金
  - 投标有效期
  - 文档完整性
  - 全文数据一致性
  - 报价/商务最小检查（限价、零报价、报价表存在性）
- 统一输出检查项结构（check_id / severity / requirement_source / bid_evidence）
- 不改写正文，只发现废标/扣分/缺失风险

关键输出：
- `workspace/compliance_report.json`

关键状态：
- `blocking=true`（存在 fatal/critical 失败）时阶段状态为 `error`
- `need_manual_review=true` 时阶段状态为 `warn`
- `pre_build` 阶段不硬停后续出稿，便于先生成终稿
- `check_format` 阶段会基于 `final.md` 做 `final` 复检；`blocking` 时阻断交付成功

说明：
- 本阶段为确定性规则检查，不依赖 LLM
- 与 `global_review`（LLM 全文一致性）互补，不替换
- 完成后自动：
  - 生成 `workspace/claim_validation_report.json`（金额/资质/业绩 claim 防编造 + chunk 对齐）
  - 回灌 `workspace/compliance_rewrite_hints.json` 与 `manual_review/compliance_actions.json`
  - 将可改稿合规项注入对应章节 `reviews/*_review.json` 的 `priority_fixes`
  - 报价表确定性验算：`workspace/price_table_report.json`（数量×单价）
  - 偏离表逐行：`workspace/deviation_table_report.json`

### 3.17 `build_markdown`

职责：
- 按大纲顺序拼接章节为 `final.md`

关键输出：
- `outputs/final.md`

### 3.18 `build_docx`

职责：
- 尽量继承模板样式、封面、表格和页眉页脚
- 生成模板填充报告

关键输出：
- `outputs/final.docx`
- `workspace/template_fill_report.json`

质量门禁：
- 若模板填充报告显示未处理结构或残留占位，阶段失败

### 3.19 `check_format`

职责：
- 对 Markdown、DOCX、模板 contract、填充报告做最终检查
- 触发专项合规终稿复检（`phase=final`，优先 `outputs/final.md`）
- `compliance blocking` 记入格式门禁 fail，并阻止流程成功完成

关键输出：
- `workspace/format_check_report.json`
- 更新 `workspace/compliance_report.json`（终稿复检结果）

说明：
- 如果存在 `fail` 项（含合规阻断），会抛异常并阻止流程被视为成功

## 4. 运行时状态与事件流

当前系统会写 4 类运行时文件：

### 4.1 `workspace/run_state.json`

保存当前状态快照：
- `run_id`
- `stage`
- `status`
- `message`
- `updated_at`
- `summary`
- `metrics`

### 4.2 `workspace/run_state_history.jsonl`

保存每次状态快照历史，便于回看阶段推进过程。

### 4.3 `workspace/run_events.jsonl`

保存阶段事件流。当前事件类型包括：
- `start`
- `success`
- `reuse`
- `skip`
- `fail`
- `agent_artifact`

典型字段：
- `ts`
- `run_id`
- `stage`
- `event_type`
- `status`
- `message`
- `chapter_id`
- `artifact_path`
- `metrics`

### 4.4 `workspace/run_metrics.json`

保存阶段级指标：
- `attempts`
- `duration_ms`
- `llm_calls`
- `input_tokens_est`
- `output_tokens_est`
- `agent_runs`

### 4.5 `workspace/pipeline_control.json`

保存后端监督器的控制面状态，典型字段包括：
- `status`
- `current_stage`
- `worker_pid`
- `started_at`
- `updated_at`
- `error`

这个文件让 Web 流程从“页面内存状态”变成“工作空间持久状态”，因此：
- 刷新页面不会丢失自动推进状态
- Web 服务重启后可重新接管仍在运行或待恢复的工作空间

### 4.6 `workspace/recovery_state.json`

当阶段失败后进入自动恢复，会写入：
- `reason`
- `action`
- `attempt`
- `max_attempts`
- `updated_at`

前端用它展示“正在自主修复 / 自动重试”的状态。

### 4.7 `workspace/run_error.json`

最近一次失败阶段的诊断缓存，主要包含：
- `command`
- `exit_code`
- `lines`

前端和聊天面板可以直接基于它给出失败摘要、重试和跳过动作。

## 5. Agent 追踪

每次 agent 调用结束后，会生成：

- `workspace/agent_runs/{stage}__{agent}__{chapter}.json`
- `workspace/agent_runs/{stage}__{agent}__{chapter}__timestamp.json`

内容包括：
- `agent_name`
- `prompt_file`
- `prompt_version`
- `prompt_checksum`
- `project_type`
- `model`
- `temperature`
- `input_contract`
- `output_contract`
- `input_summary`
- `context_budget`
- `llm_calls`
- `input_tokens_est`
- `output_tokens_est`

这部分主要用于回答：
- 这次到底用了哪个 prompt
- prompt 有没有变
- 这章用了什么模型
- 上下文预算是多少

## 6. Resume 逻辑

当前 resume / 阶段完成判断 不再只看“文件是否存在”，而是同时要求：

1. 阶段输出产物有效
2. `run_events.jsonl` 中该阶段最后事件为 `success/reuse/skip`

因此：
- 只有产物存在但没有成功事件，不会被安全复用
- 事件存在但产物被删了，也不会被复用
- 对 `select_contexts / write_chapters / review_fix_chapters / summarize_chapters` 这类集合阶段，会按章节 ID 集合精确校验是否补齐，不再因为“目录里已有部分文件”误判为完成

## 7. 自动流水线与恢复

当前 Web 不再依赖前端逐步触发每个命令，而是由后端监督器统一推进自动阶段：

- 前端调用 `/api/start-pipeline`
- `PipelineSupervisor` 持久化当前工作空间的调度状态
- 每个自动阶段最终仍复用同一套 `_run_sync` 执行逻辑

恢复策略分为三层：

1. 阶段失败自动恢复
   目前会针对 `429 / 超时 / 网络异常 / LLM 暂时不可用` 等问题记录恢复状态并自动重试。
2. 阶段卡死看门狗
   如果一个阶段长时间没有日志心跳，会终止子进程并进入恢复流程。
3. 服务重启接管
   FastAPI 启动时会读取 `pipeline_control.json` 和当前活动工作空间，尝试重新接管未完成的流水线。

另外，前端已经做了一个纠偏：
- 如果恢复提示仍显示“重试中”，但新的 `agent_artifact` 或 `success` 事件已经出现，状态接口会把这一阶段归一为正常运行，前端同步清掉陈旧提示，避免页面一直挂在“2/2 重试中”。

## 8. Web 控制台当前逻辑

Web 控制台不是独立实现的另一套流程，而是读取统一元数据：

- 流程节点：来自 `pipeline_registry`
- 运行状态：来自 `run_state.json`
- 事件流：来自 `run_events.jsonl`
- 阶段指标：来自 `run_metrics.json`
- agent run：来自 `workspace/agent_runs/*.json`
- 项目类型：来自 `workspace/project_profile.json`
- 人工复核：来自 `workspace/manual_review/*.json`

当前支持：
- 独立 run workspace
- Vue 单页控制台与 FastAPI API/静态资源一体部署
- 节点详情查看
- 产物预览
- 运行记录查看
- 实时日志 + 实时事件推送
- 自动流水线启动、暂停、重试、跳过失败阶段
- 恢复中状态、失败诊断和阶段日志折叠展示
- 项目类型选择与当前 profile 展示
- 最新 agent run、prompt version/checksum、budget 命中查看
- 人工复核总览和分类项操作
- 工作空间列表、删除工作空间
- `final.md` 在线改写、块级流式改写、撤销上次改写、文档预览

## 9. LLM 配置热更新

当前 Web 管理模式下，LLM 配置的读取规则与独立 CLI 不同：

- 独立 CLI：环境变量仍然覆盖 `.env`
- Web 管理工作空间：中央配置文件会覆盖 Web 进程启动时继承的旧环境变量

这样做的目的，是保证你在设置页里修改“当前使用中的模型 / API Key / Base URL”后：
- Web 进程内的后续请求立即生效
- 已存在的多个工作空间在下一次 LLM 请求时自动读取新配置
- 不需要人工重启工作空间，也不会继续拿着旧子进程环境跑下去

## 10. 项目类型化 Prompt

当前项目类型默认来自 `workspace/project_profile.json`，由 CLI/Web 手动选择，默认值为 `general`。

已内建类型：
- `general`
- `government_procurement`
- `software_project`
- `ops_service`
- `system_integration`

首轮覆盖核心 agent：
- `tender_requirement_extractor`
- `outline_generator`
- `chapter_context_selector`
- `chapter_writer`
- `global_reviewer`

解析策略：
- 若存在 `write_chapter.software_project.md` 这类 variant 文件，则直接使用
- 若不存在 variant 文件，则在默认 prompt 后拼接 profile guidance

## 11. 人工复核覆盖层

人工复核不直接改写原始运行产物，而是单独写入：

- `workspace/manual_review/summary.json`
- `workspace/manual_review/template_evidence_overrides.json`
- `workspace/manual_review/score_coverage_overrides.json`
- `workspace/manual_review/chapter_actions.json`
- `workspace/manual_review/global_review_actions.json`
- `workspace/manual_review/replay_requests.json`

当前覆盖层已经接入：
- `plan_chapter_jobs`
  可叠加评分点 -> 章节归属覆盖
- `select_contexts`
  可叠加 preferred chunk ids 和人工说明
- `write_chapters`
  可叠加章节级人工修订指令
- `global_review`
  可消费 accepted/resolved 风险，避免重复告警

## 12. 测试与回归点

当前测试覆盖了最容易回归的结构层能力：

- `tests/test_pipeline_registry.py`
  确保 registry 与 Web 映射一致
- `tests/test_state_recorder.py`
  确保 resume 依赖“事件 + 产物”
- `tests/test_prompt_registry.py`
  确保 agent run 追踪 prompt 元数据
- `tests/test_quality_gates.py`
  确保评分点覆盖门禁生效
- `tests/test_context_selector_contract.py`
  确保上下文选择结构契约和 budget 元数据稳定
- `tests/test_chapter_writer_contract.py`
  确保章节标题、agent run artifact 和弱证据门禁稳定
- `tests/test_web_status_contract.py`
  确保 Web 状态接口和节点详情结构稳定
- `tests/test_pipeline_supervisor.py`
  确保后端自动流水线调度、暂停与恢复接管逻辑稳定
- `tests/test_incremental_pipeline.py`
  确保集合阶段按缺失章节增量补齐，而不是整阶段误判完成
- `tests/test_auto_recovery.py`
  确保自动恢复、恢复失败和前端恢复态契约稳定
- `tests/test_live_llm_config.py`
  确保多工作空间会在后续请求中热切换到新模型/API Key
- `tests/test_project_profile_prompt_resolution.py`
  确保项目类型 prompt 解析正确
- `tests/test_manual_review.py`
  确保人工复核覆盖层独立持久化且不污染原始产物

建议每次改流程骨架后至少执行：

```bash
python -m unittest discover -s tests -v
python -m compileall src tests
```

---

## 质量门禁与问题单（2026-07 起）

### 行为摘要

1. 关键阶段结束后可写入 `workspace/issues/open.json`（问题单）。
2. **block** 级 open 问题会阻止：
   - 自动流水线进入下一阶段（`pipeline_supervisor`）
   - Web `start-pipeline` / `run-command`（除 init/validate 等）
   - `build-docx` 出正式稿
3. 已实现硬门禁的阶段示例：
   - `global-review`：不一致/未覆盖评分点/冲突/编造风险等
   - `compliance-check`：fatal/critical 等 blocking
   - `review-fix-all`：未通过章（`CHAPTER_REVIEW_GATE=1` 时）
   - `write-all`：存在失败章节
   - `parse-score`：评分点为空
   - `generate-outline`：评分点未绑定章节
4. 最小修复：`POST /api/issues/{id}/actions/preview|execute`，修复后按根因表重验。
5. Tool 层：`list_issues` / `repair_issue` / `export_preflight`；`run_stage` 受门禁约束。
6. 出稿前检查：`GET /api/export-preflight` 或前端「出稿前检查」。
7. 配置：
   - `QUALITY_GATE_MODE=strict|soft`
   - `GLOBAL_REVIEW_GATE=1`
   - `CHAPTER_REVIEW_GATE=1`
   - `ISSUE_ACCEPT_RISK_ENABLED=0`（默认关）
   - `ISSUE_LLM_CAUSE_ENABLED=0`（默认关）
   - 批量修复 / 智能归因 / 接受风险见 `/api/issues/*`

### 相关代码

- `src/agent/issues.py` / `root_cause.py` / `repair.py`
- `src/quality_gates.py`
- 前端 `StepDetailView` 问题修复按钮；计划区质量门禁阻断条

## 12. Agent 闭环强化（PR-9 ~ PR-14，2026-07-17）

系统在确定性流水线之外，提供受约束的 Supervisor 多步闭环：

### 12.1 多步循环（PR-9）

```text
observe(snapshot) → reevaluate GoalState → decide(tool)
→ policy → invoke → reevaluate → continue / terminal
```

实现：

- `src/agent/supervisor.py`：`run_supervisor_turn` 真正的 while-budget 循环
- `src/agent/snapshot.py`：统一快照（pipeline / goal / artifacts / issues / materials / budget）
- `src/agent/budgets.py`：步数、LLM 调用、同 tool 连击、无进展熔断
- `src/graph/supervisor_graph.py`：LangGraph 版可继续多步（readonly / 已确认变更）

终止状态：

- `succeeded` / `blocked_human` / `blocked_policy` / `budget_exceeded` / `failed` / `awaiting_confirmation`

### 12.2 GoalState 2.0（PR-10）

`workspace/agent/goal_state.json` 增加：

- `plan` / `current_plan_index` / `confirmation_scope` / `progress`
- 条件步骤 `run_if`、依赖 `depends_on`、attempts
- `resume_goal_after_materials` 补料恢复

目标可驱动计划执行；覆盖率未达标时不能提前 `succeeded`。

### 12.3 章节子图闭环（PR-11）

`src/graph/chapter_subgraph.py`：

```text
write → self-check → (need_rewrite → rewrite → self-check)* → save
```

最终章节状态：`passed` / `deferred_material` / `stuck` / `failed`。

### 12.4 风险门禁（PR-12）

- `ISSUE_ACCEPT_RISK_ENABLED` **默认 0**
- 接受风险原因 ≥ 8 有效字符
- fatal / 资格材料禁止直接 accept
- critical 需管理员 + 二次确认
- `export_preflight` 披露 accepted risks；`all_passed=false` 当存在接受风险
- 可选 `outputs/risk_register.md`

### 12.5 材料恢复（PR-13）

- 材料生命周期：`missing → requested → uploaded → verified → injected → resolved`
- `POST /api/materials-checklist/upload` 标记上传并生成最小恢复计划
- 局部失效 + 局部回填，不全量重跑
- Goal 从 `blocked_human` 恢复

### 12.6 Agent 工作台（PR-14）

Web：

- 目标卡 / 计划卡 / 决策轨迹 / 人工补料卡
- API：`/api/agent/goal`、`/api/agent/snapshot`、`/api/agent/decisions`、`/api/agent/goal/resume`

### 12.7 配置

```text
AGENT_MAX_STEPS=12
AGENT_MAX_LLM_CALLS=20
AGENT_MAX_SAME_TOOL_STREAK=2
AGENT_MAX_NO_PROGRESS_STEPS=2
AGENT_MAX_REPAIR_ROUNDS=2
AGENT_SNAPSHOT_MAX_CHARS=12000
ISSUE_ACCEPT_RISK_ENABLED=0
```

### 12.8 相关测试

- `tests/test_supervisor_multistep.py`
- `tests/test_goal_plan.py` / `test_goal_resume.py`
- `tests/test_supervisor_budget.py` / `test_no_progress_detection.py`
- `tests/test_chapter_subgraph_loop.py` / `test_chapter_stuck_detection.py`
- `tests/test_accept_risk_policy.py`
- `tests/test_material_resume.py`
- `tests/test_snapshot_contract.py`
