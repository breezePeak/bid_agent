# 标书 Agent 默认入口与生产化开发计划

> 状态：**PR-A0～A7 核心已落地（2026-07-20）**  
> 编制日期：2026-07-20  
> 适用仓库：`bid_agent`  
> 目标版本：Agent First Beta  
> 依据：2026-07-20 项目进度检查与产品定位讨论  
> 后续目标架构与迁移计划：[current_logic_flow_v2.md](./current_logic_flow_v2.md)；本文保留 Agent First Beta 阶段的实施记录。  
>  
> 进度摘要：  
> - A0 并发统一配置 / LLM 信号量 / 429 降载  
> - A1 `tests/test_agent_first_acceptance.py` 15 场景  
> - A2 LangGraph 适配层仅调用 `run_supervisor_turn`  
> - A3 `AGENT_SUPERVISOR_ENABLED` 默认 true + 模式徽章 + 启动一致性检查  
> - A4 `agent/goal_compiler.py` 结构化编译 + 规则回退  
> - A5 `agent/material_verifier.py` uploaded≠verified  
> - A6 `.github/workflows/ci.yml`  
> - A7 工作台计划步骤 / TopBar 模式 / `src/api` 拆分起点 / runs 出版本库

## 1. 结论

当前项目已经从早期 MVP 进入内部试用 / 准 Beta 阶段，并且已经具备真实的 Agent 内核：

- 持久化的 `GoalState`；
- Supervisor 多步决策循环；
- 注册 Tool、权限策略、预算和决策轨迹；
- 章节“写作 → 自检 → 改稿 → 再检查”闭环；
- 材料缺失和合规阻断后的暂停、补料与恢复；
- 确定性流水线、质量门禁和 Word 导出能力。

但当前默认产品路径尚未切换到 Agent：

- `.env` 未设置 `AGENT_SUPERVISOR_ENABLED`；
- `src/agent/flags.py` 的默认值仍为 `false`；
- Web 会话默认仍可能由 `session_orchestrator` 驱动；
- Supervisor 能力虽然存在，但属于可选路径。

这与“标书 Agent”的产品定位不一致。本阶段的核心任务不是继续增加 Agent 角色，而是让 Supervisor 成为默认入口，统一编排内核，并用 CI 和真实标书评测证明它能够稳定交付。

目标产品架构：

```text
用户请求
   ↓
Supervisor Agent（默认入口和唯一目标执行内核）
   ├─ 解析目标、范围和约束
   ├─ 读取工作空间真实状态
   ├─ 生成受约束计划
   ├─ 调用注册 Tool
   ├─ 复核结果并继续执行
   └─ succeeded / blocked_human / blocked_policy / failed
          ↓
确定性 Pipeline（从零生成和稳定执行）
          ↓
Chapter Subgraph（单章写作、自检和改稿闭环）
          ↓
质量门禁 / 材料门禁 / 导出门禁
```

## 2. 当前基线

### 2.1 已完成能力

当前注册表包含 21 个阶段（含初始化），主链路覆盖：

1. 工作空间初始化；
2. 资料导入与文档切分；
3. 招标要求与评分点解析；
4. 公司和项目事实提取；
5. 材料资格清单；
6. 模板依据分析；
7. 大纲与章节任务生成；
8. 上下文选择；
9. 章节写作；
10. 章节审核与改稿；
11. 来源追踪；
12. 评分覆盖和估分；
13. 全文审核；
14. 合规检查；
15. Markdown、Word 和格式检查。

CLI、LangGraph 和 Web 控制台共享 `pipeline_registry`。运行状态、事件、指标、恢复状态和章节集合完整性均已持久化，支持断点续跑。

### 2.2 质量基线

截至本计划编制时，本地验证结果为：

- Python：`222 passed`，`14 subtests passed`；
- `python -m compileall`：通过；
- Vue 主前端生产构建：通过；
- Three.js 前端生产构建：通过，但主包超过 500 KB；
- FastAPI startup 使用方式存在弃用警告；
- 仓库没有持续集成工作流。

### 2.3 当前准确定位

当前系统应定义为：

> 一个已经具备 Agent 内核，但默认产品入口仍未完全切换到 Agent、工程验收体系尚未补齐的受约束标书 Agent。

## 3. 当前核心问题

### 3.1 Agent 已实现但默认未启用

`AGENT_SUPERVISOR_ENABLED` 默认关闭，导致代码能力与用户默认体验不一致。用户可能仍然感受到“聊天控制固定流水线”，而不是由目标驱动的连续 Agent 执行。

### 3.2 存在三套相近的编排逻辑

当前主要编排入口包括：

- `src/session_orchestrator.py`；
- `src/agent/supervisor.py`；
- `src/graph/supervisor_graph.py`。

三套逻辑之间存在职责重叠，容易造成：

- Web 与 CLI 行为不一致；
- 预算、确认和熔断规则分叉；
- 材料阻断和 Goal 状态更新不一致；
- 修复一处后其他入口仍保留旧行为。

### 3.3 工程保障不足

现有测试能够支撑本地开发，但尚未形成生产级保障：

- 没有 CI；
- 没有合并前强制测试；
- 前端缺少完整的组件与契约测试入口；
- 缺少固定 Demo 工作空间端到端测试；
- 缺少真实模型定时评测；
- 依赖只声明最低版本，缺少稳定锁定策略。

### 3.4 Goal 解析仍偏规则化

`infer_goal_from_message` 主要依赖“导出、改稿、评分点、合规”等关键词。它可以覆盖简单目标，但对复合目标支持不足，例如：

> 补齐评分点并修复合规问题，但不要修改报价章节；缺材料时保留结构化占位，最后重新生成 Word。

### 3.5 材料验证链路仍偏弱

材料生命周期已经建立：

```text
missing → requested → uploaded → verified → injected → resolved
```

但上传材料的真实性与适用性仍主要依靠关键词、正则和人工覆盖，尚不能稳定完成：

- 材料类型识别；
- 公司主体匹配；
- 证书编号和有效期提取；
- 招标要求逐项匹配；
- 过期、主体不一致或范围不符识别；
- 验证后精准注入和局部重跑。

### 3.6 并发配置正在硬编码放大

当前开发树正在把多个入口的默认 `workers` 调整为 10。若直接合入，会带来：

- 模型接口瞬时限流；
- Token 和费用峰值；
- 多工作空间并发倍增；
- 状态文件和日志竞争；
- 不同模型供应商吞吐能力不一致。

并发必须统一配置并受限，不能散落在多个函数中硬编码。

### 3.7 3D 展示投入早于核心稳定性

3D 指挥台具有演示价值，但已经出现包体和渲染成本问题。当前更重要的是让用户清楚看到：

- 当前目标；
- 已完成与待执行步骤；
- 正在运行的 Tool；
- 被修改的章节；
- 阻断原因；
- 缺失材料；
- 出稿风险。

因此 3D 模式应保留为可选演示能力，暂不作为核心开发主线。

### 3.8 模块体积和仓库边界需要治理

`web_app.py`、`tool_runtime.py`、`compliance_checker.py` 和 `materials_checklist.py` 已经形成大型模块，后续多人修改容易产生冲突。

此外，仓库跟踪了部分 `runs/` 运行文件。真实运行数据、客户名称、Prompt 副本和活动工作空间指针不应长期与源码一同版本化。

## 4. 本阶段目标与非目标

### 4.1 必须达成的目标

1. Supervisor 成为 Web 产品默认入口；
2. 只保留一个 Supervisor 目标执行内核；
3. 确定性 Pipeline 作为 Agent 可调用的稳定执行器保留；
4. Chapter Subgraph 继续负责单章局部闭环；
5. Agent 可连续执行到成功或明确阻断，不要求用户反复点击下一步；
6. 所有变更类 Tool 继续经过 Policy、确认和质量门禁；
7. 建立自动 CI、端到端验收和真实项目评测；
8. 保留一键关闭 Supervisor 的紧急回退能力。

### 4.2 本阶段非目标

- 不新增更多“商务 Agent”“技术 Agent”“审核 Agent”等角色；
- 不允许 Supervisor 自由发明阶段或绕过 Tool Registry；
- 不移除确定性 Pipeline；
- 不用多个 Agent 无限制互相对话；
- 不把 3D 动画作为发布阻断项；
- 不在缺少来源时自动生成资质、金额、业绩或承诺事实。

## 5. 实施里程碑

## PR-A0：基线冻结与并发治理

### 目标

先稳定当前开发树，避免在切换默认入口时叠加并发和 UI 风险。

### 实施内容

1. 梳理并提交当前未提交修改；
2. 新增统一配置：
   - `BID_AGENT_WORKERS_DEFAULT`；
   - `BID_AGENT_WORKERS_MAX`；
   - `BID_AGENT_LLM_CONCURRENCY`；
3. 删除各模块中散落的 `workers=10` 默认值；
4. 对用户输入的 workers 做上下界校验；
5. 对 429、超时和连接错误执行指数退避；
6. 多工作空间共享全局 LLM 并发限制；
7. 记录实际并发、排队时间、429 次数和降载次数。

### 验收标准

- 并发默认值只有一个配置来源；
- 任意 API/CLI 参数都不能绕过最大并发；
- 429 后能够自动降载并恢复；
- 两个工作空间同时运行时不会各自无限扩张线程；
- 单并发、默认并发、最大并发测试均通过。

## PR-A1：Agent 验收任务集

### 目标

在切换默认入口之前，以固定任务证明 Supervisor 能够稳定完成目标。

### 首批验收任务

1. 从零生成完整标书并导出 Word；
2. 补齐所有可自动补齐的评分点；
3. 修复所有可自动处理的合规问题；
4. 修改指定章节但禁止修改报价章节；
5. 缺少证书时暂停并列出所需材料；
6. 上传材料后只重跑受影响章节；
7. 服务重启后恢复原目标；
8. 存在 fatal 问题时禁止正式导出；
9. 连续改稿不收敛时停止并标记 `stuck`；
10. 目标完成后不继续执行多余 Tool；
11. 同一 Tool 和参数重复无进展时熔断；
12. 用户拒绝确认后不执行变更；
13. Pipeline 阶段失败后保留可恢复状态；
14. 只读诊断不要求人工确认；
15. accepted risk 在出稿前持续披露。

### 通用验收标准

- 用户确认一次后可连续执行多个步骤；
- 最终进入 `succeeded`、`blocked_human`、`blocked_policy`、`budget_exceeded` 或 `failed`；
- 不以“等待用户继续点击下一步”作为正常终止；
- Goal、Plan、DecisionTrace、Issue 和实际产物状态一致；
- 任何正式导出均通过出稿前门禁。

## PR-A2：统一 Supervisor 执行内核

### 目标

消除三套相近编排逻辑，只保留一个目标执行内核。

### 职责划分

#### `agent/supervisor.py`

作为唯一内核，负责：

- observe；
- Goal 创建和重评估；
- 计划推进；
- Tool 选择；
- Policy 与确认；
- Tool 调用；
- Budget 和无进展检测；
- DecisionTrace；
- 统一终止状态。

#### `graph/supervisor_graph.py`

只作为 LangGraph 适配层：

- 调用统一 Supervisor 内核；
- 映射图状态；
- 提供可视化和调试入口；
- 不重复实现业务循环、预算和确认规则；
- 不引用 Supervisor 私有函数。

#### `session_orchestrator.py`

缩减为：

- 普通聊天兼容层；
- 旧 API 参数适配；
- 非目标型问答；
- 向统一 Supervisor 委托目标执行。

### 验收标准

- Web、CLI、LangGraph 对同一快照和目标生成一致计划；
- 预算和熔断只有一套实现；
- Goal 状态只能由统一内核推进；
- LangGraph 适配层不直接执行业务 Tool；
- 旧入口兼容测试通过。

## PR-A3：Agent 成为默认产品入口

### 目标

让产品实际使用路径与“标书 Agent”定位一致。

### 实施内容

1. Web 聊天和目标操作默认调用 Supervisor；
2. 从零生成时由 Supervisor 调用确定性 Pipeline；
3. 局部修改、问题修复和补料恢复由 Supervisor 编排；
4. 默认配置将 `AGENT_SUPERVISOR_ENABLED` 设为 `true`；
5. 保留 `false` 作为紧急回退开关；
6. 页面明确显示当前运行模式；
7. 回退时保留 Goal、Issue 和产物，不破坏工作空间；
8. 启动时检查 GoalState 与 PipelineState 一致性。

### 发布策略

1. 开发环境默认开启；
2. 固定 Demo 工作空间开启；
3. 内部工作空间灰度开启；
4. PR-A1 验收全部通过后正式改默认值；
5. 出现严重异常时使用开关回退，不回滚工作空间数据。

### 验收标准

- 新工作空间默认显示 Agent 模式；
- 用户提出目标后自动创建 Goal 和 Plan；
- 全量生成仍严格遵循 Pipeline Registry；
- 禁用开关后旧入口仍可用；
- 两种模式切换不会丢失产物和问题单。

## PR-A4：结构化 Goal Compiler

### 目标

将关键词推断升级为“LLM 结构化解析 + 确定性校验与计划编译”。

### 目标结构

```json
{
  "objectives": [],
  "scope": {
    "chapter_ids": [],
    "include_sections": [],
    "exclude_sections": []
  },
  "constraints": {
    "forbid_price_changes": true,
    "allow_placeholders_for_missing_materials": true,
    "require_compliance_pass_before_export": true
  },
  "success_criteria": [],
  "human_confirmation": {
    "required_for": []
  }
}
```

### 编译流程

```text
用户原始目标
→ LLM 输出结构化 Goal 草案
→ JSON Schema 校验
→ 范围、约束和权限确定性校验
→ 映射到注册 Tool
→ 生成 Plan
→ 用户确认高风险变更
→ 执行
```

### 安全要求

- LLM 不能直接生成可执行函数名之外的代码；
- Tool 必须存在于注册表；
- 参数必须通过 Tool Schema；
- 目标中的禁止修改范围必须传播到每个变更 Tool；
- 解析失败时回退到规则解析或要求用户澄清；
- Goal 编译结果写入审计记录。

### 验收标准

- 支持包含多个 objective 和约束的复合目标；
- 禁止修改报价章节的约束不会在后续步骤丢失；
- 未注册动作不会进入计划；
- 同一目标在 Web、CLI 下得到相同规范化结果；
- 关键词解析继续作为离线和故障回退路径。

## PR-A5：材料真实性验证闭环

### 目标

让 `uploaded → verified → injected → resolved` 成为有证据的状态迁移。

### 实施内容

1. 上传文件解析与分页；
2. 材料类型分类；
3. 提取公司名称、证书编号、签发单位、有效期和适用范围；
4. 与招标要求逐项匹配；
5. 识别过期、主体不符、范围不足和内容缺页；
6. 保存证据页码、原文片段和提取置信度；
7. 中低置信度进入人工确认；
8. 验证通过后只注入相关章节；
9. 精准失效 context、chapter、review、coverage 和 export；
10. 重跑后重新验证对应 Issue。

### 状态约束

- `uploaded` 不等于 `verified`；
- 仅关键词命中不能自动变成 `resolved`；
- 低置信度必须保留原始材料证据；
- 人工确认必须记录操作者、时间和依据；
- 过期材料不得自动通过资格门禁。

### 验收标准

- 上传一份有效证书后只重跑受影响章节；
- 上传无关文件不会关闭材料缺口；
- 过期或主体不符材料会生成明确问题；
- 所有自动验证结果均可追溯到文件和页码；
- 人工确认后能够从 `blocked_human` 恢复。

## PR-A6：CI、端到端测试与真实项目评测

### 目标

把“开发者本地通过”升级为可持续的工程保障。

### PR 必跑 CI

1. Python 单元测试；
2. `compileall`；
3. Ruff 格式和静态检查；
4. 主前端构建；
5. 前端组件与 API 契约测试；
6. 3D 前端构建；
7. 使用模拟 LLM 的 Demo 工作空间完整流水线；
8. Supervisor 多步 E2E；
9. Prompt JSON contract 测试；
10. 测试失败日志和关键产物上传。

### 真实模型评测

真实 LLM 测试不作为每个 PR 的同步阻断项，改为：

- 每日定时运行；
- 发布前手动运行；
- 模型或 Prompt 变更时强制运行；
- 结果进入历史趋势报表。

### 评测集

至少覆盖：

- 软件项目；
- 系统集成；
- 运维服务；
- 政府采购；
- 大型综合项目。

每类准备 3～5 套脱敏招标文件，并包含至少一套 500 页以上压力样本。

### 核心指标

- 评分点遗漏率；
- 错误事实数量；
- Claim 无来源比例；
- 合规漏检率；
- 合规误报率；
- 改稿收敛率；
- 人工补料次数；
- LLM 调用次数和成本；
- 总运行时间；
- 章节失败率；
- 429 和自动恢复次数；
- Word 结构和格式问题数。

### 验收标准

- 所有合并请求必须通过确定性 CI；
- 受保护分支不能绕过必需检查；
- Prompt 或模型调整可以比较前后质量变化；
- Demo 流水线失败时可下载完整诊断信息；
- 真实项目评测拥有可追踪的版本和趋势。

## PR-A7：工作台聚焦与工程治理

### 目标

让用户首先看懂 Agent 的目标、行动和风险，同时降低维护成本。

### 二维工作台优先项

- 当前目标和成功条件；
- 当前计划步骤；
- 正在运行的 Tool；
- Tool 选择原因摘要；
- 本轮修改章节；
- 材料阻断及上传入口；
- open issue 和 accepted risk；
- 出稿前检查；
- 成本、耗时、调用次数和恢复次数。

### 3D 模式处理

- 保留为可选演示模式；
- 按需加载 Three.js；
- 与主工作台拆包；
- 降低标签、灯光和动画更新成本；
- 3D 故障不得影响核心工作台和流水线。

### 后端拆分建议

将 `web_app.py` 逐步拆为：

```text
src/api/workspaces.py
src/api/pipeline.py
src/api/agent.py
src/api/issues.py
src/api/materials.py
src/api/export.py
src/api/settings.py
```

将 Tool Runtime 按只读、执行、修复和导出职责拆分，同时保持统一注册入口。

### 其他治理项

- FastAPI startup 迁移到 lifespan；
- 锁定 Python 和前端依赖；
- 修复 README 链接和输出路径说明；
- 将真实 `runs/` 数据移出源码版本控制；
- 只保留脱敏测试 fixture；
- 对工作空间日志和上传资料增加隐私清理策略。

## 6. 统一状态与安全约束

### 6.1 真实状态优先

Supervisor 每步决策必须读取：

- PipelineState；
- GoalState；
- Plan 当前步骤；
- 最新 ToolResult；
- open / accepted issues；
- materials 状态；
- stale artifacts；
- RepairJob；
- ManualReview；
- Budget。

不能仅根据聊天历史推断任务已经完成。

### 6.2 终止状态统一

所有入口统一使用：

```text
succeeded
blocked_human
blocked_policy
awaiting_confirmation
budget_exceeded
failed
```

每个终止结果必须包含：

- 已完成步骤；
- 未完成目标；
- 阻断或失败原因；
- 风险摘要；
- 下一步人工动作；
- 可恢复入口；
- 当前产物链接。

### 6.3 正式导出约束

- fatal 问题禁止正式导出；
- 资格材料缺失不得通过接受风险直接关闭；
- critical 风险继续要求管理员和二次确认；
- accepted risk 必须在出稿前披露；
- `draft.docx` 与 `final.docx` 保持语义区分；
- Goal succeeded 不得绕过导出门禁。

## 7. 推荐实施顺序

```text
PR-A0 基线冻结与并发治理
→ PR-A1 Agent 验收任务集
→ PR-A2 统一 Supervisor 内核
→ PR-A3 Agent 默认入口
→ PR-A4 结构化 Goal Compiler
→ PR-A5 材料真实性验证
→ PR-A6 CI 与真实项目评测
→ PR-A7 工作台和工程治理
```

其中首个可对外称为 “Agent First Beta” 的发布门槛为：

- PR-A0～PR-A3 全部完成；
- PR-A1 验收任务全部通过；
- PR 必跑 CI 已启用；
- 至少五套脱敏真实项目完成回归；
- Supervisor 默认开启且保留紧急回退；
- fatal、材料和导出门禁无绕过路径。

## 8. 最终验收场景

核心验收指令：

> 补齐所有可自动补齐的评分点，修复可以自动处理的合规问题；不要修改报价章节，缺少材料的位置保留结构化占位；重新审核并生成最终 Word。

预期执行链路：

```text
解析结构化目标与约束
→ 查询评分覆盖、合规问题和材料缺口
→ 生成受约束计划
→ 请求一次必要确认
→ 按根因定向改写相关章节
→ 章节复审
→ 重算评分覆盖
→ 全文审核
→ 合规复检
→ 出稿前检查
→ 生成 Markdown / Word
→ 格式检查
→ GoalState=succeeded
```

若缺少证书、签章、报价依据等不可自动解决的问题，预期行为为：

```text
GoalState=blocked_human
→ 明确列出所需材料、对应招标要求和受影响章节
→ 用户上传材料
→ 验证材料
→ 只失效和重跑受影响范围
→ 恢复原 Goal
→ 继续执行直至成功或其他明确终止状态
```

## 9. 完成定义

只有同时满足以下条件，本计划才可标记完成：

1. Supervisor 已成为默认产品入口；
2. 三套相近编排逻辑已收敛为一个执行内核；
3. 确定性 Pipeline 和 Chapter Subgraph 职责清晰且未被破坏；
4. 复合 Goal 可以结构化解析并保持约束；
5. 材料验证拥有文件、页码和字段级证据；
6. PR CI、Supervisor E2E 和真实项目评测持续运行；
7. 默认并发受控，不会因多工作空间导致无限放大；
8. Agent 能够自动完成目标或明确进入可恢复阻断；
9. 正式 Word 无法绕过 fatal、材料和合规门禁；
10. 用户无需理解内部流水线即可看懂当前目标、行动、阻断和结果。
