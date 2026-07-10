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
  → summarize_chapters
  → global_review
  → build_markdown
  → build_docx
  → check_format
  → END
```

说明：
- `run` 和 `graph-run` 的阶段顺序一致
- `graph-run --resume` 会按事件流和产物校验决定是否复用
- Web 流程台展示的节点顺序与这里一致

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
- 对 `need_rewrite=true` 的章节执行自动改稿，最多 2 轮

关键输出：
- `workspace/reviews/*_review.json`
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

关键输出：
- `workspace/score_coverage_matrix.json`

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

### 3.16 `build_markdown`

职责：
- 按大纲顺序拼接章节为 `final.md`

关键输出：
- `outputs/final.md`

### 3.17 `build_docx`

职责：
- 尽量继承模板样式、封面、表格和页眉页脚
- 生成模板填充报告

关键输出：
- `outputs/final.docx`
- `workspace/template_fill_report.json`

质量门禁：
- 若模板填充报告显示未处理结构或残留占位，阶段失败

### 3.18 `check_format`

职责：
- 对 Markdown、DOCX、模板 contract、填充报告做最终检查

关键输出：
- `workspace/format_check_report.json`

说明：
- 如果存在 `fail` 项，会抛异常并阻止流程被视为成功

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

当前 resume 不再只看“文件是否存在”，而是同时要求：

1. 阶段输出产物有效
2. `run_events.jsonl` 中该阶段最后事件为 `success/reuse/skip`

因此：
- 只有产物存在但没有成功事件，不会被安全复用
- 事件存在但产物被删了，也不会被复用

## 7. Web 控制台当前逻辑

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
- 节点详情查看
- 产物预览
- 运行记录查看
- 实时日志 + 实时事件推送
- 项目类型选择与当前 profile 展示
- 最新 agent run、prompt version/checksum、budget 命中查看
- 人工复核总览和分类项操作

## 8. 项目类型化 Prompt

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

## 9. 人工复核覆盖层

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

## 10. 测试与回归点

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
- `tests/test_project_profile_prompt_resolution.py`
  确保项目类型 prompt 解析正确
- `tests/test_manual_review.py`
  确保人工复核覆盖层独立持久化且不污染原始产物

建议每次改流程骨架后至少执行：

```bash
python -m unittest discover -s tests -v
python -m compileall src tests
```
