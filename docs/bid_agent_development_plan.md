# 标书写作 Agent 开发演进记录

本文档保留项目从 MVP 到当前版本的演进脉络。它不再承担“当前流程说明”的职责，当前实际逻辑请优先查看 [current_logic_flow.md](/D:/my_project/bid_agent/docs/current_logic_flow.md:1)。

## 1. 项目目标

目标一直比较稳定：

> 跑通“招标文件 / 评分标准 / 公司资料 / 标书模板 → 生成章节 → 审核章节 → 拼接 Word”的主流程，并持续降低编造风险、人工返工成本和长流程排障成本。

当前目标已经从“能跑通”扩展为：
- 可观测：每一步可追踪
- 可恢复：支持安全 resume
- 可解释：能回看上下文、prompt、事件和质量门禁
- 可扩展：CLI / Graph / Web 共用一套阶段定义

## 2. 演进概览

### 阶段 1：基础 CLI 和文件读写

已完成：
- `init` 初始化目录和默认提示词
- `utils.py` 统一文件读写
- `config.py` 读取 `.env`
- `main.py` 建立 CLI 框架

### 阶段 2：LLM 调用与评分解析

已完成：
- `llm_client.py` 统一 OpenAI-compatible 调用
- `score_parser.py` 解析评分点
- `fact_extractor.py` 提取全局事实
- `outline_generator.py` 生成大纲

### 阶段 3：资料导入

已完成：
- `document_converter.py`
- `tender_extractor.py`
- `company_extractor.py`
- `input_preparer.py`

核心效果：
- `sources/` → `inputs/`
- 招标文件自动分类为 `score.md / tender.md / other.md`

### 阶段 4：LangGraph 主流程

已完成：
- `graph/state.py`
- `graph/nodes.py`
- `graph/bid_graph.py`
- `graph-run`

### 阶段 5：job/context 章节 SubAgent

已完成：
- `document_splitter.py`
- `job_planner.py`
- `context_selector.py`
- `chapter_writer.py`
- `chapter_reviewer.py`
- `subagent_runner.py`
- `graph/chapter_subgraph.py`

### 阶段 6：validate 项目检查

已完成：
- 文件、环境变量、中间产物、输出结果静态检查

### 阶段 7：init-demo

已完成：
- 生成最小演示数据，降低首次体验门槛

### 阶段 8：chunk-ranker

已完成：
- `select-context` 前做本地粗筛
- 降低大项目 prompt 长度

### 阶段 9：AI tender block classifier

已完成：
- 招标文件切块 + AI 分类 + 规则兜底

### 阶段 10：chapter-summary + global-review

已完成：
- 章节摘要
- 全文审核优先使用摘要

### 阶段 11：review-fix-all 自动改稿

已完成：
- 审核 → 自动改稿 → 再审核

### 阶段 12：retry / resume

已完成：
- `graph-run --resume`
- 失败章节重试
- `run_state.json` / `run_state_history.jsonl`

### 阶段 13：评分点覆盖矩阵

已完成：
- `workspace/score_coverage_matrix.json`

### 阶段 14：来源可追溯引用

已完成：
- `workspace/source_trace_index.json`
- `workspace/source_traces/*`

### 阶段 15：DOCX 模板保真

已完成（基础版）：
- 尽量继承模板样式、封面、表格、页眉页脚

## 3. 本轮升级：借鉴 `claude-code` 的结构能力

这一轮不是新增业务功能，而是升级基础架构。

### 3.1 统一阶段注册表

新增：
- `src/pipeline_registry.py`

落地效果：
- `main.py`、`graph/bid_graph.py`、`web_app.py` 共用同一份 `StageSpec`
- 阶段定义不再三处硬编码

### 3.2 运行时状态事件化

升级：
- `src/graph/state_recorder.py`

新增产物：
- `workspace/run_events.jsonl`
- `workspace/run_metrics.json`

落地效果：
- 每阶段有 `start/success/reuse/skip/fail`
- 可以记录阶段 attempts、duration、llm_calls、token 估算

### 3.3 Prompt / Agent 治理

升级：
- `src/prompt_registry.py`
- `src/runtime_context.py`

落地效果：
- 每个 agent 有 `AgentSpec`
- prompt 带 `version`、`checksum`
- 每次 agent 调用都会生成追踪 artifact

### 3.4 上下文预算控制

新增：
- `src/context_budget.py`

落地效果：
- `select-contexts`、`write-chapters`、`review/summarize/global-review` 都会压缩上下文
- 避免大项目下 prompt 无限膨胀

### 3.5 质量门禁

新增：
- `src/quality_gates.py`

落地效果：
- 大纲必须覆盖所有评分点
- 弱证据不能写成既成事实
- 模板填充异常会阻止 `build-docx` 通过
- `need_manual_review=true` 时整体流程状态为 `warn`

## 4. 当前状态总结

当前项目已经从“能跑通的多步骤生成器”升级为：

1. 有统一阶段模型的流程系统
2. 有事件流和指标的可观测系统
3. 有 prompt/version/checksum 的 agent 追踪系统
4. 有上下文预算和质量门禁的受控生成系统

## 5. 下一阶段落地结果

这一轮已经把上一版“下一步建议”中的 4 个方向落成第一版：

1. 更细的结构契约测试
   已补 `context_selector / chapter_writer / web status / project profile / manual review`

2. Web 可视化补强
   已展示项目类型、prompt version/checksum、最新 agent runs、阶段指标和 budget 命中

3. 项目类型化 prompt 策略
   已增加 `general / government_procurement / software_project / ops_service / system_integration`

4. 人工复核工作台
   已增加独立覆盖层、摘要聚合、分类查看与状态写入，以及局部重跑建议

后续更值得继续推进的是：

1. 把人工复核项和章节/产物预览做更强联动
2. 为 5 个核心 agent 补齐真实 variant prompt 文件
3. 为 replay 建立显式“从某阶段重跑受影响章节”的 UI 命令
4. 扩展 Web 视图测试到前端渲染层
