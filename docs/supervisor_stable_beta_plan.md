# 标书 Agent 下一轮开发计划

> 状态：**已实施（2026-07-20）**  
> 基线日期：2026-07-20  
> 适用仓库：`bid_agent`  
> 路径：`docs/supervisor_stable_beta_plan.md`  
> 目标版本：Agent First Stable Beta  
> 实施说明：PR-1~PR-10 与 `tests/test_agent_state_machine.py` 已合入开发树；状态机验收通过。  
> 后续目标架构与迁移计划：[current_logic_flow_v2.md](./current_logic_flow_v2.md)；本文保留 Agent First Stable Beta 阶段的实施记录。  

## 一、开发目标

本轮只完成一件事：

> 让 Supervisor 从“可以运行”升级为“状态正确、权限正确、失败可恢复、测试可证明”的稳定执行入口。

本轮完成后，应满足：

1. 用户查询状态不会重复调用直到超限。
2. “一键生成完整标书”能够真正启动剩余流水线。
3. 任何计划步骤都不能绕过依赖执行。
4. Tool 失败后能够按 `max_attempts` 重试。
5. 用户确认一个 Tool，不会授权其他变更操作。
6. 材料仍缺失时，系统能够再次阻断。
7. 新需求不会错误继承旧的失败 Goal。
8. CI 能证明核心闭环通过。

---

## 二、本轮不做的内容

暂时冻结以下工作：

* 不修改 3D 前端。
* 不增加新的 Agent 角色。
* 不增加新的业务功能。
* 不继续美化工作台。
* 不扩展新的项目类型 Prompt。
* 不重构已经稳定的标书流水线阶段。
* 不加入复杂的长期记忆和多用户权限系统。

所有开发资源集中在 Supervisor 状态机和验收测试。

---

# 三、开发任务

## PR-1：收紧 Tool 确认权限

### 当前问题

用户点击“确认执行 build_export”时，系统可能同时设置：

```json
{
  "all_mutations": true
}
```

这会导致后续写作、改稿、修复等变更 Tool 都被自动放行。

### 修改方案

调整 `src/agent/supervisor.py`：

```python
if confirmed_tools:
    goal = grant_confirmation(
        root,
        tools=confirmed_tools,
        all_mutations=False,
    )
elif user_confirmed:
    goal = grant_confirmation(
        root,
        all_mutations=True,
    )
```

确认模式分为两种：

```text
tool_scope
all_mutations
```

普通前端确认按钮只能产生 `tool_scope`。

只有明确的“确认执行全部剩余操作”才能产生 `all_mutations`。

### 前端要求

`ChatPanel.vue` 的确认按钮必须始终携带：

```json
{
  "type": "confirm_tool",
  "tool": "具体 Tool 名",
  "args": {}
}
```

不能只发送模糊的：

```json
{
  "user_confirmed": true
}
```

### 验收标准

用户只确认 `build_export` 后：

```python
confirmation_allows(goal, "build_export") is True
confirmation_allows(goal, "rewrite_chapters") is False
confirmation_allows(goal, "fix_compliance") is False
```

---

## PR-2：新增完整流水线续跑 Tool

### 当前问题

`full_generate` 计划中存在：

```python
run_stage({"command": ""})
```

这是非法参数，不能真正运行完整流水线。

### 修改方案

新增 Tool：

```text
run_pipeline_remaining
```

建议文件：

```text
src/agent/tool_registry.py
src/agent/tool_runtime.py
```

参数：

```json
{
  "start_command": "",
  "workers": 4,
  "max_retries": 1,
  "resume": true
}
```

行为：

1. 读取当前流水线状态。
2. 找到第一个未完成且未失效的阶段。
3. 从该阶段向后运行。
4. 遇到以下情况立即停止：

   * 缺材料
   * 质量门禁
   * 人工确认
   * 阶段失败
   * 用户暂停
   * 流水线完成

返回：

```json
{
  "ok": true,
  "status": "complete|blocked|failed|paused",
  "started_from": "parse-score",
  "completed_stages": [],
  "blocked_reason": "",
  "next_command": ""
}
```

### 修改完整生成计划

```python
if "full_generate" in types:
    add("query_status", "query_status", {"view": "summary"})
    add(
        "run_remaining",
        "run_pipeline_remaining",
        {"resume": True},
        depends_on=["query_status"],
    )
    add(
        "export_preflight",
        "export_preflight",
        {},
        depends_on=["run_remaining"],
    )
    add(
        "export",
        "build_export",
        {"targets": ["md", "docx", "format"]},
        depends_on=["export_preflight"],
    )
```

### 验收标准

以下指令不得出现空命令：

```text
一键生成完整标书
全部跑完
从当前进度继续生成
生成标书并导出 Word
```

完整生成测试中，禁止把 `budget_exceeded` 视为正常结果。

---

## PR-3：统一计划步骤调度入口

### 当前问题

`next_plan_step()` 会检查依赖，但 `_rule_based_decision()` 还会自己遍历计划寻找 `pending` 步骤。

这会造成依赖被绕过。

### 修改方案

规定：

> 所有计划内步骤，只能通过 `next_plan_step()` 获取。

修改 `_rule_based_decision()`：

```python
if prefer_plan and goal:
    plan_step = next_plan_step(root, goal)
```

不要再根据：

```python
current_plan_index
current_step_id
第一个 pending
```

自行选择步骤。

为此需要给 `_rule_based_decision()` 明确传入 `root`：

```python
def _rule_based_decision(
    message,
    snapshot,
    *,
    root,
    goal=None,
    prefer_plan=True,
):
```

### 依赖阻断规则

前置步骤状态为以下任意值时，下游不得执行：

```text
pending
running
blocked
failed
```

只有以下状态满足依赖：

```text
done
skipped
```

### 验收标准

计划：

```text
analyze
→ fix
→ recheck
→ export
```

当 `fix` 失败时：

* `recheck` 不执行。
* `export` 不执行。
* Goal 保持失败或可重试状态。
* 返回明确失败原因。

---

## PR-4：实现真正的步骤重试

### 当前问题

计划中虽然有：

```json
{
  "attempts": 0,
  "max_attempts": 2
}
```

但步骤失败后直接进入 `failed`，不会重新调度。

### 修改方案

增加统一函数：

```python
def handle_plan_step_result(
    root,
    goal,
    step_id,
    *,
    ok,
    error="",
    error_code="",
    retryable=None,
):
```

规则：

```python
if ok:
    status = "done"
elif attempts < max_attempts and is_retryable:
    status = "pending"
else:
    status = "failed"
```

保存：

```json
{
  "attempts": 2,
  "last_error": "...",
  "last_failed_at": "...",
  "status": "pending|failed"
}
```

重试只允许以下错误：

```text
timeout
rate_limit
temporary_network
runner_failed 且 retryable=true
```

以下错误不得自动重试：

```text
invalid_args
unknown_tool
blocked_policy
missing_required_artifact
human_confirmation_required
```

### Goal 终止规则

当某个必需步骤达到最大重试次数后：

```text
Goal.status = failed
```

同时输出：

```json
{
  "failed_step_id": "fix_coverage",
  "attempts": 2,
  "recommended_actions": [
    "查看失败详情",
    "人工重试该步骤",
    "修改配置后恢复"
  ]
}
```

---

## PR-5：增加 Goal 完成模式

### 当前问题

状态查询和诊断目标没有成功条件，执行完 Tool 后可能继续重复查询。

### 修改方案

给 Goal 增加字段：

```json
{
  "completion_mode": "criteria"
}
```

支持三种模式：

```text
criteria
plan_completed
tool_once
```

含义：

### `criteria`

业务型目标使用。

例如：

```text
补齐评分点
修复合规
导出 Word
生成完整标书
```

成功由 `success_criteria` 判断。

### `plan_completed`

只读计划型目标使用。

例如：

```text
查看状态
诊断失败
列出产物
```

所有计划步骤完成即成功。

### `tool_once`

只需调用一次指定 Tool 的简单请求。

### 默认映射

```python
status       -> plan_completed
diagnose     -> plan_completed
chat         -> tool_once 或无 Goal
full_generate -> criteria
fix_coverage  -> criteria
fix_compliance -> criteria
fix_chapter   -> criteria
export        -> criteria
```

### 验收标准

“当前进度怎么样”必须满足：

```text
query_status 调用次数 = 1
terminal_status = succeeded
Goal.status = succeeded
budget_exceeded = false
```

---

## PR-6：修复旧 Goal 污染新需求

### 当前问题

旧 Goal 为以下状态时：

```text
budget_exceeded
blocked_policy
blocked_human
```

用户提出新需求，系统可能继续复用旧 Goal。

### 修改方案

增加意图判断：

```python
resume_requested = explicit_resume_intent(message)
```

只有以下明确表达才恢复旧 Goal：

```text
继续上一个任务
恢复刚才的任务
继续执行剩余计划
材料已补，继续
确认执行
```

其他情况下：

```python
if goal.status in GOAL_TERMINAL:
    create_new_goal()
```

特别处理 `blocked_human`：

* 用户上传或确认材料：恢复旧 Goal。
* 用户问其他问题：创建新 Goal。
* 用户只问状态：创建临时只读 Goal，不能覆盖原业务 Goal。

### 建议增加 Goal 历史

保存当前 Goal 前，将旧 Goal归档到：

```text
workspace/agent/goals/<goal_id>.json
```

`goal_state.json` 只保存当前 Goal。

---

## PR-7：修复材料恢复状态

### 当前问题

材料恢复后会永久设置：

```python
block_on_missing_materials = False
```

这可能让其他仍缺失的关键材料失去阻断能力。

### 修改方案

删除永久修改约束的逻辑。

新增一次性字段：

```json
{
  "resume_context": {
    "reason": "material_verified",
    "item_ids": ["MAT-QUAL-001"],
    "skip_same_snapshot_once": true,
    "created_at": "..."
  }
}
```

恢复流程：

1. 材料必须为 `verified` 或人工确认通过。
2. 重建材料清单。
3. 重新计算全部关键材料。
4. 若其他关键材料仍缺失，再次进入 `blocked_human`。
5. 只允许跳过同一份旧快照一次。
6. 完成一次重新评估后删除 `resume_context`。

### 验收标准

同时缺少 A、B 两项材料时：

1. 上传并验证 A。
2. Goal 恢复。
3. 重新评估发现 B 仍缺失。
4. Goal 再次进入 `blocked_human`。
5. 阻断原因只显示 B。

---

## PR-8：统一 Goal Compiler 检查名称

### 当前问题

Goal Compiler 使用：

```text
export_preflight_ok
```

Goal Evaluator 使用：

```text
export_preflight
```

### 修改方案

只保留统一名称：

```text
export_preflight
```

为兼容旧数据，评估器可临时支持别名：

```python
if check in {"export_preflight", "export_preflight_ok"}:
    ...
```

保存新 Goal 时统一写成：

```text
export_preflight
```

### 验收标准

LLM Goal Compiler 生成出稿目标后，不得出现：

```text
unsupported_check
```

---

## PR-9：修复无进展判断

### 当前问题

Tool 执行完成后，Issue 指纹仍然来自执行前的 Snapshot。

### 修改方案

执行完成后重新构建状态：

```python
post_snapshot = build_snapshot(
    root,
    status=status,
    goal=goal,
    last_tool_result=last_tool_result_dict,
    budget=budget.to_dict(),
    for_llm=True,
)
```

然后使用：

```python
issues_fingerprint(post_snapshot)
criteria_fingerprint(goal.criteria_results)
```

判断是否有进展。

建议进展判断同时包含：

```text
成功条件变化
开放问题数量变化
计划步骤状态变化
产物新增或失效状态变化
材料状态变化
```

### 验收标准

修复一个 Block Issue 后：

```text
open_blocks: 1 → 0
no_progress_steps: 归零
Goal 不得触发 budget_exceeded
```

---

## PR-10：清理运行数据和 CI

### Git 清理

虽然 `.gitignore` 已加入 `runs/`，但历史文件仍被追踪。

执行：

```bash
git rm -r --cached runs
```

保留：

```text
runs/.gitkeep
```

不要提交：

```text
runs/.active_run
客户名称
运行日志
材料文件
生成章节
最终标书
```

### CI 调整

CI 分成四组：

#### 1. 静态检查

```bash
python -m compileall -q src
ruff check src tests
```

`ruff` 不得再使用：

```bash
|| true
```

否则检查失败仍会显示成功。

#### 2. 单元测试

```bash
pytest tests -q
```

排除真实 LLM 测试。

#### 3. Agent 状态机验收

单独运行：

```bash
pytest tests/test_agent_state_machine.py -q
pytest tests/test_agent_first_acceptance.py -q
```

#### 4. 普通前端构建

```bash
cd frontend
npm ci
npm run build
```

本轮可以从 CI 中暂时移除 3D 构建，避免无关代码影响 Agent 主线。

---

# 四、严格验收场景

新增文件：

```text
tests/test_agent_state_machine.py
```

至少包含以下测试。

## 场景 1：状态查询只执行一次

输入：

```text
当前进度怎么样
```

断言：

```text
query_status = 1 次
terminal_status = succeeded
Goal.status = succeeded
```

## 场景 2：诊断只执行一次

输入：

```text
为什么失败了
```

断言：

```text
diagnose_failure = 1 次
不触发 mutation Tool
```

## 场景 3：完整生成不存在空参数

输入：

```text
一键生成完整标书并导出 Word
```

断言：

```text
不存在 run_stage(command="")
存在 run_pipeline_remaining
最终不得因 invalid_args 失败
```

## 场景 4：依赖失败后禁止执行下游

计划：

```text
A → B → C
```

B 失败后：

```text
C 调用次数 = 0
```

## 场景 5：步骤按最大次数重试

配置：

```text
max_attempts = 2
```

第一次失败，第二次成功：

```text
attempts = 2
status = done
```

连续两次失败：

```text
status = failed
Goal.status = failed
```

## 场景 6：确认权限不扩散

确认：

```text
build_export
```

断言：

```text
build_export = allowed
rewrite_chapters = denied
fix_compliance = denied
```

## 场景 7：材料部分补齐后再次阻断

缺 A、B，验证 A 后：

```text
Goal.status = blocked_human
blocked_reason 只包含 B
```

## 场景 8：预算超限后的新需求创建新 Goal

前一个 Goal：

```text
budget_exceeded
```

用户输入：

```text
当前进度怎么样
```

断言：

```text
new_goal_id != old_goal_id
objective = status
```

## 场景 9：明确恢复才复用旧 Goal

用户输入：

```text
继续上一个任务
```

断言：

```text
goal_id 保持不变
```

## 场景 10：Issue 修复后识别为有进展

修复前：

```text
open_blocks = 1
```

修复后：

```text
open_blocks = 0
no_progress_steps = 0
```

## 场景 11：Goal Compiler 检查名称正确

所有编译结果中的 `check` 必须属于：

```text
artifact_exists
stage_ready
no_stale
score_coverage_min
no_open_blocks
chapters_written
export_preflight
```

## 场景 12：验收测试不得宽松放行

禁止出现：

```python
or True
```

完整生成场景不得接受：

```text
budget_exceeded
blocked_policy
failed
```

作为测试通过结果。

---

# 五、建议提交顺序

## Commit 1

```text
fix: scope supervisor confirmations to selected tools
```

## Commit 2

```text
feat: add run_pipeline_remaining agent tool
```

## Commit 3

```text
refactor: route all plan execution through next_plan_step
```

## Commit 4

```text
fix: implement plan retries and terminal failure handling
```

## Commit 5

```text
feat: add goal completion modes and terminal goal rollover
```

## Commit 6

```text
fix: revalidate all materials after goal resume
```

## Commit 7

```text
fix: refresh post-tool progress fingerprints
```

## Commit 8

```text
test: harden agent state machine acceptance
```

## Commit 9

```text
chore: clean runtime data and enforce CI checks
```

---

# 六、完成标准

只有同时满足以下条件，本轮才算完成：

```text
[x] Supervisor 默认开启
[x] 状态查询只执行一次
[x] 完整生成无空 command
[x] 计划步骤无法绕过依赖
[x] max_attempts 真正生效
[x] Tool 确认权限不扩散
[x] 材料未齐可再次阻断
[x] 终止 Goal 不污染新需求
[x] Goal Compiler 检查名称统一
[x] Tool 执行后使用新 Snapshot
[x] runs 运行数据不再被 Git 追踪
[x] Ruff 和测试失败会让 CI 失败
[x] 所有严格验收测试通过
```

---

# 七、本轮完成后的项目定位

本轮之前：

```text
Agent First Beta
具备 Agent 内核，但状态机存在边界漏洞
```

本轮之后：

```text
Agent First Stable Beta
可以在人工监督下运行真实标书项目
```

此时再进入下一阶段：

```text
真实标书评测集
效果指标统计
提示词优化
跨项目稳定性测试
```

而不是继续增加界面和 Agent 数量。
