# 标书 Agent 后续开发计划

> 状态：**已实施（2026-07-17）**  
> 基线日期：2026-07-17  
> 适用仓库：`breezePeak/bid_agent`  
> 建议路径：`docs/next_agent_development_plan.md`  
> 目标版本：Agent 闭环强化版  
> 实施说明：PR-9~PR-14 代码、测试与 Web 工作台已合入主干开发树；全量 `unittest discover` 通过。  
> 后续目标架构与迁移计划：[current_logic_flow_v2.md](./current_logic_flow_v2.md)；本文保留该阶段的实施记录。  

## 1. 当前阶段判断

当前系统已经完成以下基础能力：

- 固定顺序、可恢复的标书生成流水线；
- `StageSpec` / `ToolSpec` / Tool Runtime 统一执行入口；
- Supervisor 根据用户意图选择已注册工具；
- GoalState、DecisionTrace、Policy、质量问题单；
- 评分覆盖、全文审核、合规检查和导出门禁；
- 按章节并发执行的写作、审核和改稿 Worker；
- 问题根因分组、最小修复计划、执行后重新验证；
- Web 端 Agent 工作台、修复任务进度和人工复核入口。

当前系统已经属于“受约束的 Supervisor Agent + 确定性标书流水线”，但仍有四个主要缺口：

1. Supervisor 多数情况下只选择并执行一个 Tool，本轮立即结束；
2. GoalState 能判断目标结果，但还不能稳定驱动后续多步执行；
3. 章节子图仍是固定直线，自检失败后不能在子图内自动改写和复审；
4. 部分高风险操作仍可被过于宽松地人工放行。

后续开发重点不是继续增加 Agent 名称或角色，而是把“目标—观察—执行—复核—完成”闭环做实。

---

## 2. 下一阶段总目标

完成下面这条可验收链路：

```text
用户提出目标
→ Supervisor 解析目标与成功条件
→ 查询当前真实状态
→ 生成受约束的运行计划
→ 选择并执行 Tool
→ 读取 ToolResult 和最新产物
→ 重新评估 GoalState
→ 自动选择下一步
→ 遇到人工材料或高风险操作时暂停
→ 所有成功条件满足后结束
```

核心验收指令：

> 补齐所有可自动补齐的评分点，修复可以自动处理的合规问题，缺少材料的位置保留结构化占位，重新审核并生成最终 Word。

系统应在用户确认一次后自动完成：

```text
查询评分覆盖和合规问题
→ 按根因生成修复计划
→ 定向改写相关章节
→ 章节复审
→ 重算评分覆盖
→ 全文审核
→ 合规复检
→ 出稿前检查
→ 生成 Markdown / Word
→ 格式检查
→ GoalState=succeeded
```

若遇到缺证书、缺签章、缺报价依据等不可自动解决的问题，应进入 `blocked_human`，明确列出所需材料和恢复入口，不得静默跳过。

---

## 3. 开发原则

### 3.1 保留双模执行

系统继续保留两种运行模式：

| 模式 | 用途 | 决策方式 |
|---|---|---|
| Deterministic Pipeline | 全量生成、回归测试、稳定交付 | 严格按照 `STAGE_SPECS` 顺序 |
| Supervised Agent | 对话、诊断、局部修复、质量闭环 | Supervisor 根据 GoalState 选择 Tool |

全量生成流程不依赖 Supervisor 每阶段在线决策，避免流程漂移、成本增长和难以复现。

### 3.2 不允许自由发明流程

Supervisor 只能：

- 调用注册 Tool；
- 跳过已经完成且未失效的阶段；
- 定向执行指定章节；
- 在依赖允许范围内选择重跑阶段；
- 插入只读查询、诊断、人工确认和质量复核。

禁止：

- 发明未注册阶段；
- 绕过依赖执行下游阶段；
- 未经过合规门禁直接生成正式终稿；
- 在没有明确章节目标时执行全量改写；
- 让多个 Agent 无限制互相对话。

### 3.3 真实状态优先

Supervisor 的每次决策必须基于：

- 最新产物状态；
- stale / invalidation 状态；
- 当前 open issues；
- 当前 GoalState；
- 最近一次 ToolResult；
- 人工复核状态；
- pipeline / repair job 运行状态。

不得仅根据历史聊天内容判断“已经完成”。

---

## 4. 里程碑规划

## PR-9：Supervisor 多步闭环

### 目标

将当前“每轮通常只执行一个 Tool”升级为真正的短循环：

```text
observe → decide → policy → invoke → observe → reevaluate goal → continue
```

### 主要改动

建议涉及：

- `src/agent/supervisor.py`
- `src/agent/goal.py`
- `src/agent/trace.py`
- `src/agent/budgets.py`
- `src/session_orchestrator.py`
- `src/web_app.py`

### 实现要求

1. 每次 Tool 执行后重新构建 snapshot；
2. 每次 Tool 执行后重新计算 GoalState；
3. `done` 不再完全由模型自由决定；
4. Goal 未完成且未阻断时，Supervisor 可继续下一步；
5. 变更类 Tool 只在本 Goal 已获得相应确认时执行；
6. 只读 Tool 可自动执行；
7. 达到最大步数、最大模型调用数或无进展阈值时停止；
8. 每步写入 DecisionTrace；
9. 终止状态统一为：
   - `succeeded`
   - `blocked_human`
   - `blocked_policy`
   - `budget_exceeded`
   - `failed`

### 建议循环结构

```python
while budget.allow_next_step():
    snapshot = build_snapshot(root)
    goal = reevaluate_goal(root)

    if goal_succeeded(goal):
        return finish("succeeded")

    if human_blocking(snapshot, goal):
        return finish("blocked_human")

    decision = decide(snapshot, goal, history)
    policy = evaluate_tool_call(decision.tool, decision.args)

    if policy.ask_human:
        return suspend_for_confirmation(decision)

    result = invoke(decision.tool, decision.args)
    append_decision(decision, result)

    if no_progress_detected():
        return finish("budget_exceeded")
```

### 验收标准

- 一次目标至少可连续执行 3 个只读或已确认 Tool；
- Tool 执行结果会影响下一步选择；
- Goal 成功后不会继续执行多余阶段；
- 同一 `tool + args` 连续重复达到阈值时自动熔断；
- Supervisor 重启后可从 GoalState 和 DecisionTrace 恢复。

---

## PR-10：GoalState 2.0 与计划执行

### 目标

让 GoalState 从“结果检查器”升级为“运行驱动器”。

### 新增字段建议

```json
{
  "goal_id": "...",
  "raw_user_goal": "...",
  "normalized_objectives": [],
  "constraints": {},
  "success_criteria": [],
  "plan": [],
  "current_plan_index": 0,
  "status": "in_progress",
  "blocked_reason": "",
  "confirmation_scope": {},
  "progress": {},
  "criteria_results": [],
  "created_at": "",
  "updated_at": ""
}
```

### 重点能力

1. 目标拆成结构化 objectives；
2. 每个 objective 映射到受控 Tool 序列；
3. 支持计划中的条件步骤；
4. 支持跳过已完成、未失效的步骤；
5. 支持局部目标，例如只修改指定章节；
6. 支持目标约束：
   - 禁止修改报价章节；
   - 只处理技术方案；
   - 缺材料时保留结构化占位；
   - 必须通过合规门禁后才能导出；
7. 支持 `blocked_human` 后补料恢复。

### 计划步骤结构建议

```json
{
  "step_id": "repair_coverage",
  "tool": "fix_coverage",
  "args": {
    "max_chapters": 5
  },
  "depends_on": ["analyze_coverage"],
  "run_if": {
    "open_issue_codes": ["SCORE_UNCOVERED"]
  },
  "status": "pending",
  "attempts": 0,
  "max_attempts": 2
}
```

### 验收标准

- “补评分点并出 Word”能生成明确计划；
- 改写章节后，旧 `final.md` / `final.docx` 自动标记 stale；
- 覆盖率未达到目标时不能提前标记 succeeded；
- 合规阻断时导出步骤保持 pending 或 blocked；
- 用户补充材料后可从阻断步骤继续，而不是从头重跑。

---

## PR-11：章节子图自检改写闭环

### 目标

将章节子图从固定直线：

```text
load job → load context → write → self-check → save
```

升级为受限条件图：

```text
load job
→ load context
→ write
→ self-check
→ pass → save
→ need_rewrite → rewrite → self-check
→ need_evidence → save deferred + request human
→ max_rounds → save blocked result
```

### 主要改动

建议涉及：

- `src/graph/chapter_subgraph.py`
- `src/graph/state.py`
- `src/chapter_reviewer.py`
- `src/chapter_rewriter.py`
- `src/subagent_runner.py`
- `src/agent/activity.py`

### 关键规则

1. 最大自动改写轮数默认 2；
2. `need_evidence=true` 不进入无意义改写；
3. blocker / major 才触发自动改写；
4. 连续两次问题指纹不变化时判定 stuck；
5. 每轮保存 review 和 rewrite log；
6. 最终章节状态必须包含：
   - `passed`
   - `deferred_material`
   - `stuck`
   - `failed`
7. 子图结果同步为 Issue，供 Supervisor 决定后续动作。

### 验收标准

- 可自动修复的问题在子图内完成改写和复审；
- 缺材料问题不会浪费模型调用反复改写；
- 章节失败时不会被误判为写作阶段完成；
- 章节结果可在 Agent 工作台中看到每轮状态。

---

## PR-12：质量门禁与风险放行收紧

### 目标

防止用户为了继续出稿而无意绕过真正的废标或资格风险。

### 必须修改

1. `ISSUE_ACCEPT_RISK_ENABLED` 默认恢复为 `0`；
2. 接受风险必须填写原因；
3. 原因最少 8 个有效字符；
4. fatal 类问题禁止通过接受风险关闭；
5. critical 类问题仅管理员可接受，并二次确认；
6. 接受风险不能删除原始证据；
7. 导出前必须展示所有 accepted risks；
8. 存在 accepted risk 时，终稿状态不得显示为“全部通过”；
9. 可选生成 `outputs/risk_register.md`；
10. 正式 Word 与内部草稿分离：

```text
outputs/draft.docx     允许带未解决风险
outputs/final.docx     必须通过正式导出门禁
```

### 风险分类建议

| 风险类型 | 是否允许接受风险 |
|---|---|
| fatal 废标项 | 不允许 |
| 资格材料缺失 | 不允许直接关闭，只能 deferred / 补料 |
| critical 合规冲突 | 管理员二次确认 |
| major 评分风险 | 可接受，必须填写理由 |
| minor 表述问题 | 可接受 |

### 验收标准

- 默认配置不能绕过 block issue；
- 空原因不能接受风险；
- fatal 风险无法通过 API 或前端放行；
- accepted risk 在审计记录和导出前检查中持续可见。

---

## PR-13：人工补料与恢复闭环

### 目标

让缺材料问题从“提醒用户”升级为可恢复任务。

### 实现内容

1. 统一材料缺口数据结构；
2. 每个缺口关联：
   - 招标要求来源；
   - 影响章节；
   - 风险等级；
   - 建议文件类型；
   - 当前状态；
3. 用户上传或确认材料后，自动识别受影响章节；
4. 只失效相关 context、chapter、review、coverage 和 export；
5. 自动生成最小恢复计划；
6. Supervisor 从 `blocked_human` 恢复到 `in_progress`；
7. 重新执行相关章节，不得全量重跑。

### 状态建议

```text
missing
→ requested
→ uploaded
→ verified
→ injected
→ resolved
```

例外状态：

```text
waived
rejected
not_applicable
```

### 验收标准

- 上传一份资格证书后，只重跑引用该材料的章节；
- 材料补齐后对应 Issue 自动重新验证；
- 重新生成 Word 前，所有受影响的旧产物已标记 stale；
- 恢复过程在 Web 中可观察。

---

## PR-14：Agent 工作台与用户体验强化

### 目标

让用户能看懂 Agent 正在做什么、为什么暂停、下一步会做什么。

### 页面建议

#### 目标卡片

展示：

- 用户原始目标；
- 当前状态；
- 成功条件；
- 当前完成度；
- 阻断原因；
- 是否等待确认。

#### 计划卡片

展示：

```text
✓ 查询评分覆盖
✓ 生成修复计划
● 正在改写 3 个章节
○ 重新审核
○ 合规复检
○ 生成 Word
```

#### 决策轨迹

默认只显示用户可理解的摘要：

```text
发现 SP-03、SP-07 覆盖不足，选择定向改写 2.1、3.2 章。
```

调试模式才显示完整 Tool、args 和 ToolResult 摘要。

#### 人工处理卡片

必须明确：

- 缺什么；
- 为什么需要；
- 影响什么；
- 用户应该上传什么；
- 补充后从哪里继续。

### 验收标准

- 用户无需阅读日志即可知道当前阶段；
- 每次暂停都有明确原因和操作按钮；
- 目标完成后给出最终摘要、风险和产物入口；
- Agent 工作台与真实 GoalState、RepairJob、PipelineState 一致。

---

## 5. Snapshot 统一设计

Supervisor 不应分别读取多个零散文件，应新增统一快照构建器，例如：

```text
src/agent/snapshot.py
```

建议返回：

```json
{
  "pipeline": {
    "status": "idle",
    "current_stage": "",
    "next_step": {}
  },
  "goal": {},
  "artifacts": {
    "ready": [],
    "missing": [],
    "stale": []
  },
  "issues": {
    "open_blocks": [],
    "open_warnings": [],
    "accepted": []
  },
  "materials": {
    "missing": [],
    "uploaded": []
  },
  "repair_job": {},
  "manual_review": {},
  "last_tool_result": {},
  "budget": {}
}
```

### 约束

- 传给 LLM 的快照必须限长；
- 不传整章正文；
- 不传 API Key；
- 产物内容只传摘要和引用路径；
- 所有状态字段必须来自真实文件或运行时，而不是聊天推测。

---

## 6. Budget 与防死循环

新增或强化以下限制：

| 项目 | 建议默认值 |
|---|---:|
| `AGENT_MAX_STEPS` | 12 |
| `AGENT_MAX_LLM_CALLS` | 20 |
| `AGENT_MAX_SAME_TOOL_STREAK` | 2 |
| `AGENT_MAX_NO_PROGRESS_STEPS` | 2 |
| `AGENT_MAX_REPAIR_ROUNDS` | 2 |
| `AGENT_MAX_CHAPTERS_PER_INVOKE` | 5 |
| `AGENT_OBSERVATION_MAX_CHARS` | 2000 |
| `AGENT_SNAPSHOT_MAX_CHARS` | 12000 |

### 无进展判定

满足任意条件则停止自动循环：

- 同一 `tool + args` 连续执行且结果摘要未变化；
- open issue 指纹连续两轮未变化；
- Goal criteria 连续两轮无变化；
- 同一章节连续两轮 review 问题不收敛；
- ToolResult 连续失败且错误类型相同；
- 预算耗尽。

停止后必须给出：

- 已完成步骤；
- 未完成目标；
- 最近错误；
- 推荐人工动作；
- 可恢复入口。

---

## 7. 测试计划

### 7.1 单元测试

新增或强化：

```text
tests/test_supervisor_multistep.py
tests/test_goal_plan.py
tests/test_goal_resume.py
tests/test_supervisor_budget.py
tests/test_no_progress_detection.py
tests/test_chapter_subgraph_loop.py
tests/test_chapter_stuck_detection.py
tests/test_accept_risk_policy.py
tests/test_material_resume.py
tests/test_snapshot_contract.py
```

### 7.2 E2E 场景

#### 场景 1：全量生成

```text
上传完整资料
→ 一键生成
→ final.md / final.docx / format report 成功
```

#### 场景 2：补齐评分点后出稿

```text
发现未覆盖评分点
→ 定向改写
→ 复审
→ coverage 达标
→ 导出 Word
```

#### 场景 3：合规自动修复

```text
发现可自动修复的表述冲突
→ 改写相关章节
→ 合规复检通过
```

#### 场景 4：缺材料阻断

```text
发现资格证书缺失
→ GoalState=blocked_human
→ 上传材料
→ 局部重跑
→ 恢复目标
```

#### 场景 5：禁止绕过 fatal 风险

```text
存在 fatal issue
→ 尝试接受风险
→ Policy 拒绝
→ final.docx 不生成
```

#### 场景 6：Supervisor 重启恢复

```text
执行到第 3 步时服务重启
→ 读取 GoalState / decisions / job state
→ 从未完成步骤继续
```

#### 场景 7：死循环熔断

```text
同一章节两轮改写无进展
→ 停止自动改写
→ blocked_human
```

#### 场景 8：局部修改不全量重跑

```text
仅修改 2.1 章
→ 只失效相关 review / summary / trace / coverage / final
→ 不重跑其他章节写作
```

---

## 8. 监控指标

建议在 `workspace/agent/metrics.jsonl` 或现有指标系统增加：

| 指标 | 含义 |
|---|---|
| goal_success_rate | Agent 目标完成率 |
| blocked_human_rate | 人工阻断比例 |
| invalid_tool_rate | 无效 Tool 选择率 |
| policy_denied_rate | Policy 拒绝率 |
| average_steps_per_goal | 每目标平均步骤数 |
| no_progress_abort_rate | 无进展熔断率 |
| repair_success_rate | 自动修复成功率 |
| average_rewrite_rounds | 平均章节改写轮数 |
| unnecessary_stage_rate | 不必要阶段执行率 |
| stale_export_incidents | 旧终稿错误导出次数 |

关键目标：

```text
invalid_tool_rate < 1%
stale_export_incidents = 0
fatal_risk_bypass = 0
```

---

## 9. 推荐实施顺序

严格按照以下顺序推进：

```text
PR-9  Supervisor 多步闭环
→ PR-10 GoalState 2.0
→ PR-11 章节子图闭环
→ PR-12 风险门禁收紧
→ PR-13 人工补料恢复
→ PR-14 Web 工作台强化
```

原因：

- 没有 PR-9，多步自动执行无法成立；
- 没有 PR-10，系统不知道计划走到哪里；
- PR-11 和 PR-13 依赖目标恢复及失效传播；
- PR-12 应在扩大自动执行范围前完成；
- UI 应最后对齐稳定的数据结构，避免反复返工。

---

## 10. 每个 PR 的 Definition of Done

每个 PR 合入前必须满足：

- [ ] `python -m unittest discover -s tests -v` 全绿；
- [ ] `python -m compileall src tests` 通过；
- [ ] 新行为有单元测试；
- [ ] 至少一个对应 E2E 场景通过；
- [ ] 不泄露 API Key、完整 prompt 或章节全文到 DecisionTrace；
- [ ] 关闭 Agent flag 后原确定性流程仍可运行；
- [ ] 失败后有明确恢复方式；
- [ ] 高风险动作经过 Policy；
- [ ] 修改产物后失效传播正确；
- [ ] `docs/current_logic_flow_v1.md` 同步更新；
- [ ] 当前文档中的实施状态已更新。

---

## 11. 完成后的系统定位

完成本计划后，系统应达到以下定位：

> 一个以确定性标书流水线为执行基础、由 Supervisor Agent 负责目标理解、状态观察、工具选择、局部修复、质量复核和人工协同的标书生成系统。

它不追求无限自由规划，而应做到：

- 全量生成稳定；
- 局部修改准确；
- 质量问题可闭环；
- 缺材料能暂停和恢复；
- 高风险操作不可静默绕过；
- 所有决策和产物可追踪；
- 最终 Word 与当前最新章节、审核和合规状态一致。

最终核心验收标准只有一句话：

> 用户给出一个明确标书目标并确认执行后，系统能够在安全边界内持续工作，直到目标完成，或者明确告诉用户还缺什么材料、为什么无法继续，以及补充后如何恢复。
