# 标书系统 Agent 化改造开发计划

> 状态：草案 v1.3（PR-1~8 已落地；PR-9~11 进行中）  
> 日期：2026-07-16  
> 路径：`docs/agentization_development_plan.md`  
> 关联：
> - [current_logic_flow.md](./current_logic_flow.md) — 当前实现真相源（已上线行为）
> - [bid_agent_development_plan.md](./bid_agent_development_plan.md) — 历史演进
>
> 本文职责：目标架构、分期实施、风险预案、测试验收、回滚与开放决策。  
> `current_logic_flow.md` 只描述已上线行为；本文描述目标与计划。实现偏离时先记 ADR 再改版本号。

---

## 0. 一句话结论

**不要推倒重做成自由规划 Agent。**

```text
外层 Supervisor Agent（诊断 / 选工具 / 解释 / 熔断）
  +
内层 Capability Tool 层（现有 StageSpec 产物契约 + 质量门 + resume）
  +
默认 Plan = 今天 STAGE_SPECS 顺序（一键生成仍确定性）
```

**流水线当肌肉，Agent 当大脑。** 默认路径仍是工作流；对话、失败恢复、定向质量修补才走自治。

---

## 1. 背景与问题诊断

### 1.1 业务目标（不变）

招标文件 + 评分标准 + 公司资料 + 模板 → 章节生成 → 审核改稿 → 合规 → Word，并持续降低编造/弱证据风险、人工返工、长流程排障成本。

### 1.2 用户体感问题

| 体感 | 根因（代码层） |
|------|----------------|
| 不像 Agent，像工作流 | 主路径按 `STAGE_SPECS` 固定顺序执行 |
| 对话只会“点下一步” | `session_orchestrator` action 死枚举，常对齐 `next_step` |
| 出错不会自己想办法 | 恢复偏规则重试（429/超时），非读产物后重规划 |
| 定向修补贵 | 缺 `chapter_ids` / focus 等一等公民参数 |
| 有 LangGraph 但不智能 | `bid_graph` 线性边；`routers.continue_to_next` 为 stub |

### 1.3 已具备且必须保留

| 资产 | 路径 | 价值 |
|------|------|------|
| 阶段注册表 | `src/pipeline_registry.py` | CLI/Graph/Web 统一契约 |
| Prompt/Agent 契约 | `src/prompt_registry.py` | 版本、checksum、I/O |
| 子 Agent 蓝图 | `src/subagent_registry.py` | 章节并发模型 |
| 运行时记录 | `src/graph/state_recorder.py` | resume/事件/指标 |
| 后端调度 | `src/pipeline_supervisor.py` | 暂停、心跳、重启接管 |
| 会话编排雏形 | `src/session_orchestrator.py` | coordinator + manifest |
| 质量门 | `src/quality_gates.py` | 弱证据、合规阻塞 |
| 人工复核层 | `src/manual_review.py` | 不污染原始产物 |
| Web 控制台 | `src/web_app.py` + `frontend/` | 可观测操作面 |

### 1.4 Agent 化真正缺口

1. 无 **Tool 运行时**（不能按名+参数调用）
2. 无 **多轮观察循环**（一次 JSON 结束）
3. 无 **GoalState**（知 next_step，不知目标是否达成）
4. **参数化弱**（多为整段重跑）
5. 无 **DecisionTrace**
6. **Policy 未产品化**（跳过合规/删产物等）

### 1.5 反目标（明确不做）

| 不做 | 原因 |
|------|------|
| LLM 自由发明新阶段 | 契约/Word/合规失控 |
| 无边界多 Agent 互聊 | 成本、不可复现、难审计 |
| 取消 registry 与产物落盘 | resume/预览/质检全塌 |
| 每个 stage 都 ReAct | 贵且不稳 |
| 大爆炸重写 | 回归与现网风险过高 |

### 1.6 当前阶段清单（默认顺序，勿随意删）

```text
init_workspace → prepare_inputs → split_docs → parse_score → extract_facts
→ build_template_evidence → generate_outline → plan_chapter_jobs
→ select_contexts → write_chapters → review_fix_chapters
→ build_source_trace_index → build_score_coverage_matrix → estimate_final_score
→ summarize_chapters → global_review → compliance_check
→ build_markdown → build_docx → check_format
```

---

## 2. 目标架构

### 2.1 分层

```text
L0 交互层     Web Chat / CLI / API
L1 Supervisor  意图、GoalState、选 Tool、Policy、DecisionTrace
L2 Tool 层     ToolSpec = Stage + 参数 schema + 风险 + 幂等
L3 Worker 层   现有 writer/reviewer/gates（基本不动）
L4 运行时      workspace、events、goal/decision 落盘
```

### 2.2 双模执行（必须并存）

| 模式 | 入口 | 决策者 | 场景 |
|------|------|--------|------|
| Deterministic Pipeline | run / graph-run / auto_run | 固定 STAGE 顺序 | 全量生成、回归、演示 |
| Supervised Agent | Chat、失败诊断、定向修补 | Supervisor tool loop | 对话、局部修、质量驱动 |

**硬规则：Deterministic 不得依赖 Supervisor 在线决策**（否则 final.docx 不可复现）。

### 2.3 默认 Plan vs Agent Plan

- `default_plan` = registry 顺序，确定性。
- Agent 在约束内生成运行时计划（跳过已完成、参数化重跑、插入 query/diagnose/human）。
- **P3 前不允许**发明新阶段或任意重排未完成依赖。

示例：`补齐评分点 SP-03，只改相关章后出 Word`

```text
query coverage → (可选) 重 plan jobs → write/rewrite 指定章
→ review 指定章 → 重算 coverage/estimate → gates OK 则 md/docx/format
→ 否则 request_human_review
```

---

## 3. 核心数据模型

### 3.1 ToolSpec（StageSpec 超集）

字段：id, command, label, description, kind, requires, produces, runner, params_schema, risk_level, idempotent, side_effects, default_timeout_sec, max_concurrency, allowed_modes, human_confirm_required, prompt_agents, tags。

兼容：StageSpec 继续驱动 CLI/Web；Tool 从 STAGE_SPECS + 参数化包装器生成。不要立刻删 StageSpec。

### 3.2 第一批参数化 Tool

| Tool | 关键参数 | 说明 |
|------|----------|------|
| run_stage | command, force? | 兼容旧阶段 |
| write_chapters | chapter_ids, mode, focus_problems | 定向写作 |
| review_chapters | chapter_ids, max_rounds? | 定向审核 |
| rewrite_chapters | targets[] | 定向改稿 |
| query_status | view | 只读 |
| query_artifacts | path_glob, max_chars | 只读沙箱 |
| diagnose_failure | command? | 聚合错误 |
| retry_stage | command, reset_artifacts? | 受控重试 |
| rollback_to | command/checkpoint | 高风险确认 |
| request_human_review | category, item_ids, reason | 人工门禁 |
| auto_run_remaining | from_command?, stop_on_gate? | 确定性剩余 |
| build_export | targets, skip_if_gate_fail | 出稿 |

### 3.3 GoalState

落盘：`workspace/agent/goal_state.json`  
含：goal_id, raw_user_goal, objectives, constraints（allow_skip_compliance 默认 false）, success_criteria, status, blocked_reason。

### 3.4 DecisionTrace

落盘：`workspace/agent/decisions.jsonl`  
每轮：thought_summary（用户可见短理由，非 CoT 原文）, selected_tool, args, policy_checks, observation_summary, next_action, tokens, model, prompt_version。

### 3.5 ToolResult envelope

ok, tool, args, error{code,message,retryable,suggested_tools}, artifacts_written, metrics, summary_for_llm(限长<=2k), raw_refs, gate_results。

---

## 4. Supervisor 运行时

### 4.1 主循环

```text
init goal → loop(max_steps):
  snapshot → decide(JSON schema) → policy → (human confirm?)
  → invoke tool → record → reevaluate criteria
  → continue | ask_human | finish | abort
```

### 4.2 预算熔断

| 项 | 默认 | 超限 |
|----|------|------|
| max_steps | 12 / 复杂 40 | 停止汇报 |
| max_llm_calls | 可配 | 同上 |
| max_retries_per_tool | 2 | 换策略/人工 |
| max_same_tool_streak | 3 | 防死循环 |
| max_wall_time | 按规模 | 暂停 resume |
| max_chapters_per_invoke | 对齐并发 5 | 分批 |

死循环检测：同 tool+args 连续；observation 哈希不变；单章改稿超 max 2 rounds；token 涨但目标无进展。

### 4.3 Policy（规则优先于 LLM）

- 合规 blocking 禁止正式出 docx（可 draft 标记）
- 默认禁止跳过合规；话术要求也要二次确认+审计
- 大范围删产物 critical 需确认
- force 重跑昂贵阶段需说明影响
- 只读永远允许
- 同章并发写禁止

### 4.4 与 session_orchestrator 兼容映射

query→query_*；run_command→run_stage；dispatch_chapters→write_chapters；dispatch_review→review_chapters；dispatch_rewrite→rewrite_chapters；global_review→run_stage；auto_run→auto_run_remaining；chat→纯回复。

保留 `plan()` 对外形状，内部委托 Supervisor；失败 fallback 旧逻辑。

### 4.5 LLM

temperature 0-0.2；严格 JSON/schema；失败 fallback 规则 planner；**禁止**全量章节正文进 Supervisor；建议编排模型与写作模型分离。

---

## 5. Tool Runtime

### 5.1 统一入口

`invoke(tool_name, args, root, dry_run=False, actor=...) -> ToolResult`

步骤：查 spec → 校验 args → policy → requires → 幂等 skip? → runner → produces → events → envelope。  
失败返回结构化 error，禁止无上下文 500。

### 5.2 与 PipelineSupervisor

| 场景 | 后端 |
|------|------|
| 短 tool（query/diagnose） | 进程内 |
| 长 stage（write-all/review/auto_run） | PipelineSupervisor |
| Web 已 running | 拒绝/排队，禁止双 auto_run |

auto_run **仍确定性按 STAGE 顺序**；Supervisor 只决定何时启动、从哪段开始、是否在 gate 停下做局部 tool。

### 5.3 失效传播表（必须维护）

| 变更 | 必须失效的下游 |
|------|----------------|
| 重解析 score | outline 归属、jobs、coverage、estimate |
| 改某章正文 | 该章 review/summary/trace 局部、coverage 行、final md/docx |
| 改 template evidence | 相关章 context 与写作约束 |
| 合规报告变化 | 导出门禁 |

导出前兜底：chapter content hash vs final 构建输入清单。无失效表 → 局部修好但 Word 仍旧。

### 5.4 锁

章节锁 `workspace/locks/chapter_{id}.lock`；阶段锁复用 pipeline running；`workspace/agent/supervisor.lock` 不可重入。

### 5.5 dry_run

高成本 tool 可 dry_run：返回触达章节、预估 LLM 次数、将失效产物；UI 先展示再确认。

### 5.6 错误码契约

unknown_tool / invalid_args / policy_denied / missing_requires / runner_failed / gate_blocked / locked / budget_exceeded / skipped。

---

## 6. Graph / CLI / Web

### 6.1 CLI

新增：`agent-run`、`tool`、`agent-status`、`agent-decisions`。  
保留：`run`/`graph-run`=Deterministic；单 stage 命令=run_stage 薄封装。

### 6.2 LangGraph

A：线性 graph 继续作 deterministic 执行器。  
B：`build_supervisor_graph()`：supervisor ↔ tool_node/human_node → END。  
不要把 20 个 stage 都改 conditional edge；state 只存引用摘要。

### 6.3 Web

流程台仍展示 STAGE；Chat 展示 decision/确认卡片；旧按钮底层转 tool；高风险模态确认；模式区分「确定性流水线」vs「Agent 目标」。

API：`POST /api/agent/run|confirm|cancel`，`GET /api/agent/goal|decisions`，`POST /api/tools/invoke`；`/api/chat/orchestrate` 兼容增强。

### 6.4 兼容红线

flag 关=改造前行为（契约测试锁死）；禁止 Web/CLI 私有跑 stage 分叉。

---

## 7. 分阶段实施

总原则：每期 unittest 绿 + E2E + 文档；`AGENT_SUPERVISOR_ENABLED` 默认关；先只读再写入。

### Phase 0 基线（3–5 天）

冻结契约测试；demo 全量/失败/单章 rewrite 基线指标；flag 位。  
退出：基线可一键跑。

### Phase 1 Tool 层（1–2 周）

`src/agent/`：types, tool_registry, tool_runtime；STAGE→Tool；第一批参数化 tool；CLI tool；单测。不改 Web 主路径。  
退出：run_stage 可跑全部旧阶段；定向 write 只触达指定章。

### Phase 2 Supervisor 闭环（1–2 周）

supervisor 短循环；orchestrator 委托+fallback；DecisionTrace；诊断场景；UI steps。  
退出：能诊断失败并给可执行 tool；critical 无确认不执行。

### Phase 3 目标局部修补（2–3 周）

GoalState+criteria；失效传播；覆盖率/合规驱动 rewrite；manual_review；预算熔断。  
退出：「补评分点并出 Word」可自动完成或 blocked_human；无静默旧 docx。

### Phase 4 双模硬化（1–2 周）

supervisor graph；导出门禁 checklist；UI 区分模式；成本面板；更新 current_logic_flow.md。

### Phase 5 评测运营（持续）

10–20 评测脚本；mock fixture；无效 tool 率/死循环率/人工介入率；snapshot 性能。

### 7.1 每期 Definition of Done

- [ ] `python -m unittest discover -s tests -v` 全绿
- [ ] `python -m compileall src tests` 通过
- [ ] 本 Phase 单测已合入
- [ ] flag 默认安全
- [ ] 无密钥进仓库/decisions/日志
- [ ] 本文变更记录已追加
- [ ] 若改已上线行为：同步 current_logic_flow.md
- [ ] PR 写明回滚方式

### 7.2 依赖与并行

```text
P0 ──────────────────────────────┐
P1 Tool ─────────────────────────┼→ P2 Supervisor
    └→ 参数化 write/rewrite ─────┼→ P3 → P4 → P5
文档/前端轨迹 UI 可于 P2 后半并行 ─┘
```

硬依赖：P2←P1；P3←P2；P4←P3。

### 7.3 人力（2 人）

| 角色 | 焦点 |
|------|------|
| A 后端运行时 | tool_runtime / supervisor / policy / 测试 |
| B 产品链路 | Web API/UI、CLI、文档、E2E |

---

## 8. 模块改动清单

### 新增

```text
src/agent/
  __init__.py types.py tool_registry.py tool_runtime.py
  supervisor.py policy.py goal.py trace.py snapshot.py
  invalidation.py budgets.py prompts/supervisor_system.md
```

### 改造

| 文件 | 幅度 | 说明 |
|------|------|------|
| pipeline_registry.py | 小 | 导出给 tool |
| subagent_runner.py | 中 | chapter_ids/锁 |
| session_orchestrator.py | 中大 | 委托 Supervisor |
| llm_client.py | 中 | schema/tool call |
| pipeline_supervisor.py | 中 | tool 调度/事件 |
| web_app.py | 中 | API/确认 |
| graph/* | 后置 | 双模 |
| main.py | 小中 | CLI |
| frontend | 中 | 轨迹/确认 |
| tests | 大增 | 见第 10 章 |
| docs/current_logic_flow.md | 同步 | Phase4 |

不优先改：各业务 writer/parser/docx 细节。

---

## 9. 风险清单与应对

### 9.1 产品

| ID | 风险 | 应对 |
|----|------|------|
| R01 | 过度 Agent 化路径漂移 | 默认 deterministic；Agent 仅对话/修补 |
| R02 | 跳过合规出稿 | Policy 硬拦+审计+默认拒绝 |
| R03 | 局部修未刷新终稿 | 失效表+export 前 hash/mtime |
| R04 | 目标含糊乱跑 | 低置信只 query；先澄清 |
| R05 | 与人工复核抢跑 | blocked 时禁 mutate |
| R06 | 一键变慢变贵 | auto_run 不逐步 decide |
| R07 | UI 复杂 | 轨迹默认折叠 |

### 9.2 架构

| ID | 风险 | 应对 |
|----|------|------|
| R10 | Stage/Tool 双真相 | Tool 从 Stage 生成+一致性单测 |
| R11 | CLI/Web/Graph 分叉 | 共用 tool_runtime |
| R12 | resume 与 goal 不一致 | 双向引用+reconcile |
| R13 | 长任务+loop 超时 | 异步 tool、可挂起、重启接管 |
| R14 | 章节写冲突 | 章节锁 |
| R15 | Windows 锁/路径 | win 单测 |
| R16 | Graph 状态过大 | 只存引用摘要 |

### 9.3 LLM

| ID | 风险 | 应对 |
|----|------|------|
| R20 | JSON 失败 | schema+重试+规则 fallback |
| R21 | 幻觉 command | 白名单 |
| R22 | 编造状态 | snapshot 必须真实构建 |
| R23 | context 爆炸 | 压缩+限长 |
| R24 | 提示词注入跳过合规 | Policy 后置 |
| R25 | 模型变弱 | 编排模型可独立配置 |
| R26 | 中英名混乱 | 对内英文 id |

### 9.4 质量

| ID | 风险 | 应对 |
|----|------|------|
| R30 | 审改死循环 | max 2 rounds→human |
| R31 | 假覆盖刷分 | 保留 claim/弱证据门禁 |
| R32 | 溯源断裂 | rewrite 后局部 trace |
| R33 | 忽略模板证据 | write 强制带 evidence 约束 |
| R34 | 多 workspace 串配置 | 热更新隔离测试 |

### 9.5 安全

| ID | 风险 | 应对 |
|----|------|------|
| R40 | 路径穿越 | root 白名单 |
| R41 | shell 注入 | 禁止 shell tool |
| R42 | Key 泄露 | 红染；trace 不记 secret |
| R43 | 任意命令 | 仅注册 tools |
| R44 | 多用户误操作 | run 级锁/权限（未来） |

### 9.6 工程

| ID | 风险 | 应对 |
|----|------|------|
| R50 | 大 PR | 按 Phase 拆；flag 关 |
| R51 | 回归不足 | 契约+E2E+fixture |
| R52 | 文档漂移 | Phase 退出强制改 current_logic |
| R53 | 膨胀成多 Agent 框架 | 反目标清单；新角色单独立项 |

### 9.7 Phase 特有

- P1：参数化与全量 write 微差→集合对比；runner import 启动校验
- P2：多轮延迟→先回“分析中”；recovering 时只解释不双侧重试
- P3：失效表漏项→export 全量一致性兜底；goal 与手动并行→dirty 检测
- P4：双模字段冲突→分离 pipeline_run_id 与 goal_id

---

## 10. 测试计划

### 单测新增

test_tool_registry, test_tool_runtime, test_tool_args_schema, test_policy_engine, test_supervisor_loop, test_goal_state, test_invalidation, test_decision_trace, test_path_sandbox, test_orchestrator_compat。

### E2E

1. demo 确定性出 Word  
2. 只读状态  
3. 失败诊断→重试  
4. 只重写第 2 章→final 重建  
5. 覆盖率修补  
6. 拒绝跳过合规  
7. 人工 pending 不抢跑  
8. 重启 goal resume  
9. max_steps 熔断  
10. flag 关=旧行为  

### 指标

无效 tool 率↓；deterministic 耗时 ≤ 基线+10%；导出一致性事故=0。

```bash
python -m unittest discover -s tests -v
python -m compileall src tests
```

---

## 11. 可观测与审计

事件：agent_goal_*、agent_decision、agent_policy_denied、agent_human_*、agent_budget_exceeded、tool_*、agent_invalidation。

落盘：`workspace/agent/{goal_state.json,decisions.jsonl,last_plan.json,supervisor.lock}`

UI：默认一句话；可展开工具序列；调试看原始 JSON；禁止 Key/全量正文进聊天。

审计最低集：actor、时间、goal_id、policy、tool、args 摘要、产物路径、human 确认、模型与 prompt 版本。

---

## 12. 配置

```text
AGENT_SUPERVISOR_ENABLED=false
AGENT_USE_TOOL_RUNTIME=true
AGENT_MAX_STEPS=12
AGENT_MAX_LLM_CALLS=30
AGENT_MAX_SAME_TOOL_STREAK=3
AGENT_ALLOW_SKIP_COMPLIANCE=false
AGENT_REQUIRE_CONFIRM_RISK_LEVEL=high
AGENT_SNAPSHOT_MAX_CHARS=12000
AGENT_OBSERVATION_MAX_CHARS=2000
AGENT_MODEL=
AGENT_DETERMINISTIC_AUTORUN=true
```

---

## 13. 回滚

1. 即时：关 `AGENT_SUPERVISOR_ENABLED` → 旧路径；agent 目录可忽略  
2. 代码：小 PR；`AGENT_USE_TOOL_RUNTIME=false` shim 旧 runner  
3. 数据：仅新增 `workspace/agent/`；无历史迁移  
4. 事故：死循环→熔断+关 flag；错出 docx→draft 作废+修 policy；未刷 Word→一致性校验+补失效表；与 auto_recovery 冲突→recovering 时 agent 只读  

---

## 14. 工期（参考）

| 阶段 | 工期 | 产出 |
|------|------|------|
| P0 | 3–5 天 | 基线+flag |
| P1 | 1–2 周 | Tool 层 |
| P2 | 1–2 周 | 对话像 Agent |
| P3 | 2–3 周 | 局部修补价值 |
| P4 | 1–2 周 | 双模硬化 |
| P5 | 持续 | 评测运营 |

合计约 **6–10 周** 到可生产体感。

---

## 15. 验收成功标准

1. 自然语言完成 查问题→局部修→出稿  
2. 一键全量仍确定性可 resume  
3. 无审计无法跳过合规出正式稿  
4. 每次自动动作有 decision trace  
5. mock+demo E2E 绿  
6. flag 可回退  

---

## 16. 第一批 PR

**PR-1：** types + tool_registry + run_stage runtime + 一致性单测 + 本文档；不接 Web。  
**PR-2：** query/diagnose + CLI tool。  
**PR-3：** Supervisor 单步接 chat（flag）+ DecisionTrace。  
再进参数化写作与失效传播。

---

## 17. 待产品拍板

| 决策 | 建议 |
|------|------|
| 编排模型与写作模型分离 | 独立 |
| 高风险确认 | 聊天+模态框 |
| Agent 重排 registry 顺序 | P3 前不允许 |
| 失败是否直接 auto_run | 否，先诊断 |
| 多 Goal 并行 | 否，单 active goal |

---

## 18. 附录

### 18.1 关键模块

pipeline_registry, session_orchestrator, subagent_*, pipeline_supervisor, graph/bid_graph, llm_client, quality_gates, manual_review, web_app。

### 18.2 文档约定

current_logic_flow.md = 已上线行为；本文 = 目标计划。每 Phase 更新变更记录。

### 18.3 变更记录

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.0 | 2026-07-16 | 首版草案 |
| v1.2 | 2026-07-16 | 落地 PR-1 Tool 层 + PR-2 只读诊断 + PR-3 Supervisor（flag 默认关） |
| v1.1 | 2026-07-16 | 补强 Runtime/失效表/错误码/DoD/回滚/风险全量 |

---



---

## 20. PR-9~11 实施计划（LangGraph Supervisor 图 / 合规定向改写 / Goal 侧栏）

> 立项日期：2026-07-16  
> 前置：PR-1~8 已完成（Tool 层、Supervisor 短循环、参数化章节、失效传播、GoalState、覆盖闭环、Chat 轨迹、Agent API）

### 20.1 目标一句话

在**不破坏确定性流水线**前提下：

1. 提供可选的 **LangGraph Supervisor 图**入口（agent-graph-run）
2. 提供 **合规失败 → 定向改稿** tool（fix_compliance）
3. 前端 **Goal 侧栏**定时轮询 `/api/agent/goal` 与 decisions

### 20.2 PR-9：LangGraph Supervisor 图

**形态：**

```text
START → supervisor_node → (conditional)
           ├─ tool_node → supervisor_node
           ├─ human_node → END（blocked_human）
           └─ END（done / budget）
```

**设计约束：**

| 项 | 约定 |
|----|------|
| 默认流水线 | 仍用线性 `build_bid_graph()` / `run` / `auto_run` |
| 新入口 | CLI `agent-graph-run --goal "..."`；可选 Web 后续接 |
| State | 只存 root_dir、goal 文本、step、last_tool、last_observation、done、need_confirm、messages 摘要 |
| 决策 | 复用 `agent.supervisor` 规则/LLM，不重写大脑 |
| 执行 | 复用 `tool_runtime.invoke` |
| 预算 | max_steps 默认 5，与 AGENT_MAX_STEPS 对齐 |
| 变更 tool | 默认 need_confirm，图进入 human/END，不自动 mutate |

**交付文件：**

- `src/graph/supervisor_graph.py`
- `src/graph/supervisor_state.py`（或放在 state.py）
- `main.py` 增加 `agent-graph-run`
- `tests/test_supervisor_graph.py`

**验收：**

- [ ] 只读 goal（状态/覆盖）能跑完并 END
- [ ] 变更类 goal 停在 need_confirm，不自动写盘
- [ ] flag/默认路径不受影响
- [ ] 单测 mock tool invoke

### 20.3 PR-10：合规项自动定向改写

**形态：**

```text
compliance_report (blocking/fail)
  → sync_compliance_findings（已有）
  → analyze: 提取 rewriteable chapters + hints
  → plan: fix_compliance(confirm_execute=false)
  → execute: rewrite_chapters(chapter_ids) + 可选再跑 compliance-check
```

**设计约束：**

| 项 | 约定 |
|----|------|
| 可改类型 | 复用 `REWRITEABLE_TYPES`；`MANUAL_ONLY_TYPES` 只进人工 |
| 默认 | 只出计划，不自动 execute |
| confirm_execute=true | 调 rewrite_chapters；可选 `rerun_check=true` 重跑 compliance-check |
| 与 export | blocking 时 build_export 仍 gate_blocked（已有） |
| 轨迹 | 写入 decisions / ToolResult metrics |

**交付文件：**

- `tool_registry`: `analyze_compliance` / `fix_compliance`
- `tool_runtime`: 实现
- `supervisor` 规则：合规/废标/blocking → 分析或计划
- `goal`: 可选 compliance_clear 准则
- `tests/test_compliance_loop.py`

**验收：**

- [ ] 无 report → missing_requires
- [ ] 有 fail 项 → 给出 chapter_ids 计划
- [ ] confirm_execute 默认 false
- [ ] MANUAL_ONLY 不进入自动 rewrite 列表（可进 manual 提示）

### 20.4 PR-11：前端 Goal 侧栏轮询

**形态：**

- `WorkspaceView` 右侧（chat 模式）在 FileExplorer 上方或下方增加 `AgentGoalPanel`
- 轮询 `GET /api/agent/goal` + `GET /api/agent/decisions?tail=8`（2~3s，仅当前 run 可见时）
- 展示：goal_id、status、criteria 列表、最近 decision steps
- Supervisor 关闭时显示「未启用 Agent 目标（flag off / 无 goal）」

**交付文件：**

- `frontend/src/components/AgentGoalPanel.vue`
- `WorkspaceView.vue` 接入
- `main.css` 样式
- 复用 `api/index.js` 已有 fetchAgentGoal / fetchAgentDecisions

**验收：**

- [ ] 无 goal 时友好空态
- [ ] 有 goal 时显示 status 与未达成 criteria
- [ ] 组件卸载清除 timer

### 20.5 风险与不做

| 风险 | 应对 |
|------|------|
| Graph 与 chat supervisor 双实现漂移 | Graph 只编排，决策/执行复用 agent.* |
| 合规自动改写误伤 | 默认仅计划；manual-only 类型排除 |
| 侧栏轮询干扰 | 仅 chat 模式、页面可见时 poll |
| 推倒线性 graph | **不做**；deterministic 入口保留 |

### 20.6 实施顺序

1. 写本节计划（本文）  
2. PR-9 supervisor graph + 测试  
3. PR-10 compliance tools + 测试  
4. PR-11 Goal 侧栏  
5. 更新 `agentization_phase_status.md` + 回归 unittest  

### 20.7 变更记录追加

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.3 | 2026-07-16 | 增加 PR-9~11 计划：Supervisor 图、合规定向改写、Goal 侧栏 |


## 19. 总结

当前已有生产级工作流肌肉。用户觉得不像 Agent，不是因为没注册表，而是：注册表被顺序执行器消费、Orchestrator 是单次意图映射、缺 Goal/Trace/Policy/参数化修补。

正确改法：Stage→Tool；Orchestrator→Supervisor 循环；默认全量仍确定性；对话/失败/局部目标走 Agent。按 Phase 推进并以第 9 章风险为 checklist，可把体感从流程台推进到标书 Agent，且不丢可靠性。

**下一步：开 PR-1（types + tool_registry + run_stage + 单测 + flag），零用户体验风险。**
