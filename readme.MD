# 标书写作 Agent

本项目是一个面向标书生成场景的本地 Agent。它基于 LangGraph 编排完整流水线，支持招标文件/公司资料/Word 模板导入，自动完成评分点解析、事实提取、大纲生成、章节写作、审核改稿、来源追溯、评分覆盖分析、全文审核和 Word 输出。

当前版本已经从“脚本串流程”升级为“可观测流程系统”：
- `CLI`、`LangGraph`、`Web` 共用同一份阶段注册表
- 每个阶段会写运行状态、事件流和阶段指标
- 每次 agent 调用会固化 prompt 版本、checksum、模型和输入摘要
- 支持按项目类型解析 prompt profile
- 支持人工复核覆盖层和局部重跑建议
- 上下文选择和章节写作带预算约束
- 大纲覆盖、弱证据表述、模板填充和最终审核都有质量门禁
- Web 端改为后端监督器驱动的自动流水线，页面刷新或服务重启后可继续接管
- 大模型配置支持热切换，后续请求自动使用新的模型和 API Key

## 快速开始

```bash
conda create -n bid_agent python=3.11 -y
conda activate bid_agent
pip install -r requirements.txt

python src/main.py init
python src/main.py init-demo

# 配置 .env
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=your-api-key
OPENAI_MODEL=gpt-4.1-mini

python src/main.py prepare-inputs
python src/main.py validate
python src/main.py graph-run --workers 2
```

如果你使用真实项目资料：
- 招标文件放入 `sources/tender/`
- 公司资料放入 `sources/company/`
- Word 模板放入 `sources/template/`

## 当前流程

主流程现在与代码保持一致，按下面 18 个阶段运行：

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

对应关系：
- 阶段注册表：`src/pipeline_registry.py`
- Graph 编排：`src/graph/bid_graph.py`
- 节点执行：`src/graph/nodes.py`
- Web 流程台：`src/web_app.py`

更详细的逻辑说明见 [docs/current_logic_flow.md](/D:/my_project/bid_agent/docs/current_logic_flow.md:1)。

## 关键目录

```text
sources/                 用户原始资料
inputs/                  标准化输入
workspace/imported/      招标文件切块与分类中间产物
workspace/chunks/        文档 chunk
workspace/jobs/          章节任务包
workspace/contexts/      章节上下文与 ranker 结果
workspace/chapters/      章节 Markdown
workspace/reviews/       章节审核结果
workspace/rewrites/      自动改稿日志
workspace/summaries/     章节摘要
workspace/agent_runs/    每次 agent 调用的追踪记录
workspace/project_profile.json 当前项目类型
workspace/manual_review/ 人工复核覆盖层与重跑建议
workspace/run_state.json 当前运行状态
workspace/run_state_history.jsonl 运行历史
workspace/run_events.jsonl        事件流
workspace/run_metrics.json        阶段指标
workspace/pipeline_control.json   后端流水线监督状态
workspace/recovery_state.json     自动恢复状态
workspace/run_error.json          最近一次失败阶段诊断
outputs/                 final.md / final.docx
tests/                   回归测试
```

## 可观测性与追踪

新增的运行时产物：

- `workspace/run_state.json`
  当前阶段、状态、摘要、阶段指标
- `workspace/run_state_history.jsonl`
  每次状态快照历史
- `workspace/run_events.jsonl`
  阶段级事件流，包含 `start/success/reuse/skip/fail/agent_artifact`
- `workspace/run_metrics.json`
  阶段 attempts、duration、llm_calls、token 估算
- `workspace/agent_runs/*.json`
  每次 agent 调用的 prompt 文件、版本、checksum、模型、temperature、输入摘要

这使得下面几类问题更容易排查：
- 为什么这次大纲结构变了
- 为什么某章上下文被截断
- 为什么 resume 跳过了某一步
- 为什么最终结果被标记为 `warn`

## 上下文预算与质量门禁

当前默认规则：

- `select-contexts`
  先走 `chunk_ranker` 本地粗筛，再按预算压缩候选目录
- `write-chapters`
  只向模型发送 compact chunk payload，不直接塞全量片段
- `review-chapter` / `summarize-chapter` / `global-review`
  默认使用裁剪后的正文或摘要，降低长上下文失控风险

质量门禁：
- `generate-outline`
  所有评分点必须至少被一个章节绑定
- `write/rewrite chapter`
  弱证据模板任务不能写成“已具备/已提供/已完成”
- `build-docx`
  模板填充报告若存在未处理结构或残留占位，会触发门禁
- `global-review`
  若 `need_manual_review=true`，整体运行状态会落为 `warn`

## CLI 命令

| 命令 | 说明 |
|---|---|
| `init` | 初始化目录和默认提示词 |
| `init-demo` | 生成演示数据 |
| `prepare-inputs` | 导入原始资料并生成标准输入 |
| `analyze-template` | 仅解析模板 schema |
| `split-docs` | 切分招标文件和公司资料 |
| `parse-score` | 解析评分要求和评分点 |
| `extract-facts` | 提取招标需求、公司事实和全局事实 |
| `build-template-evidence` | 生成模板依据映射和质量报告 |
| `generate-outline` | 生成标书大纲 |
| `plan-jobs` | 生成章节任务包 |
| `select-context --chapter 01` | 为单章选择上下文 |
| `select-context-all` | 为所有章节选择上下文 |
| `write-chapter --chapter 01` | 生成单个章节 |
| `write-all --workers 2 --max-retries 1` | 并发生成所有章节 |
| `review-chapter --chapter 01` | 审核单个章节 |
| `review-all` | 审核所有章节 |
| `rewrite-chapter --chapter 01` | 根据审核意见重写单章 |
| `rewrite-all` | 重写所有需改稿章节 |
| `review-fix-all` | 审核并自动改稿 |
| `build-source-trace` | 生成来源追溯索引 |
| `build-score-coverage` | 生成评分覆盖矩阵 |
| `summarize-chapter --chapter 01` | 生成单章摘要 |
| `summarize-all` | 生成所有章节摘要 |
| `global-review` | 全文一致性审核 |
| `build-md` | 拼接最终 Markdown |
| `build-docx` | 生成最终 Word |
| `check-format` | 执行格式检查与门禁 |
| `run --workers 2` | CLI 串行完整流水线 |
| `graph-run --workers 2 --resume --max-retries 1` | LangGraph 完整流水线，支持断点续跑 |
| `validate` | 静态检查项目状态，不调用模型 |

## Web 控制台

启动：

```bash
python src/web_app.py
```

访问 [http://127.0.0.1:7860](http://127.0.0.1:7860)

当前 Web 控制台支持：
- 上传资料并创建独立运行工作空间
- 由后端持久化监督器自动推进完整流程，刷新/关闭页面不会中断阶段衔接
- 按章节 ID 校验和补齐 context、正文、审核及摘要，不因“部分文件存在”误判完成
- 记录流水线心跳并在阶段长期无进展时终止、重试；服务重启后自动接管或断点恢复
- `run_state.json` 只保存紧凑指标；状态历史和事件日志超过 5MB 后自动轮转，保留 3 份归档
- 修改或切换“使用中”的大模型后，Web 进程和所有工作空间会在下一次 LLM 请求时动态读取新模型/API Key，无需重启任务
- 当前阶段如果经历过 `429/超时/网络抖动`，前端会在检测到新成功事件后自动清除“仍在重试”的陈旧提示
- 按统一阶段注册表展示流程
- 基于 `run_events.jsonl` 推送实时事件
- 查看阶段详情、前置条件、输出产物和运行历史
- 查看项目类型、prompt version/checksum、agent run 和 budget 命中
- 查看并操作人工复核项
- 流式查看阶段日志、失败诊断和恢复进度
- 在线编辑 `final.md`，支持块级改写、撤销上次改写和文档预览
- 删除历史工作空间
- 下载 `final.md`、`final.docx`
- 查看全文审核 JSON

可通过 `BID_AGENT_STAGE_STALL_TIMEOUT` 调整阶段无日志进展超时，默认 `900` 秒，最低 `60` 秒。

## 测试

```bash
python -m unittest discover -s tests -v
python -m compileall src tests
```

当前测试覆盖重点：
- 阶段注册表与 Web 映射一致性
- resume 必须同时满足“产物存在 + 事件完整”
- 工作空间级自动流水线、暂停/恢复与服务重启接管
- 成功进度到达后会清理前端陈旧恢复提示
- LLM 配置热切换后，多工作空间后续请求会使用新配置
- agent prompt 元数据追踪
- 大纲评分点覆盖门禁
- context selector / chapter writer / web status 结构契约
- 项目类型 prompt 解析
- 人工复核覆盖层

## 文档

- [docs/current_logic_flow.md](/D:/my_project/bid_agent/docs/current_logic_flow.md:1)
  当前实际运行逻辑、关键输入输出、事件流和质量门禁
- [docs/bid_agent_development_plan.md](/D:/my_project/bid_agent/docs/bid_agent_development_plan.md:1)
  开发演进记录和阶段性设计说明
