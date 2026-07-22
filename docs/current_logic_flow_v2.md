# 标书 Agent 控制面架构 V2

> 版本：V2  
> 状态：阶段 A/B 改造进行中，V2 尚未完成切换
> 确认日期：2026-07-21  
> 现行实现：[current_logic_flow_v1.md](./current_logic_flow_v1.md)  
> 版本导航：[current_logic_flow.md](./current_logic_flow.md)

V2 采用“Agent 控制面 + 确定性流水线唯一执行内核 + 每工作区 SQLite 控制状态”的目标架构，并按两阶段完成收敛与迁移。阶段 A/B 已进入代码改造，但迁移、回归和切换验收尚未完成；V1 仍是当前实现真相源。

## 1. 背景与问题

当前问题的核心不是功能数量，而是同一工作空间、同一阶段和同一状态存在多个所有者。

### 1.1 工作区根目录分裂

Web 状态读取活动工作区，但部分 Agent/Supervisor 路径仍可能回退到项目根目录。结果是聊天看到 A 工作区的状态，却在项目根或另一个工作区创建 Goal、DecisionTrace 或执行 Tool。

V2 必须让工作区成为所有查询和变更的显式上下文，禁止业务层自行推断活动目录。

### 1.2 多套执行内核并存

当前阶段可能经由前端 fast-path、Web 后台流水线、Agent ToolRuntime、CLI 或 LangGraph 执行。阶段注册表虽然统一了顺序，但没有统一执行语义，不同入口具有不同的锁、确认、门禁、恢复、日志和完成判断。

V2 只保留一个确定性 Pipeline 执行内核。Agent、Web、CLI 和调试适配器只能提交 Command，不得直接调用阶段 runner。

### 1.3 状态来源过多

Goal、Pipeline、Activity、RepairJob、Materials、Issues、stale artifacts、聊天历史和 Web 内存变量分别拥有独立生命周期。现有聚合状态只能发现冲突，不能保证一次状态迁移的原子性。

V2 使用每工作区一个 `workspace/control.db` 保存权威控制状态，并通过同一事务写入当前状态和审计事件。

### 1.4 材料与质量存在重复权威源

材料同时存在响应状态、证据状态和生命周期状态；上传、验证、回填及 Goal 恢复没有形成单一状态机。质量报告、Issue、人工覆盖和多个 `can_proceed` 又分别解释是否阻断，异常路径还可能 fail-open。

V2 将材料履约、质量 Finding、Issue 处理和 Policy Decision 分开建模，由唯一 GateEvaluator 计算是否允许继续或正式交付。

## 2. 目标架构

目标控制链如下：

```text
Chat / 页面按钮 / CLI
  → CommandGateway
  → Workspace revision + Policy + Confirmation + mutation lease
  → CommandDispatcher
      ├─ ExecutionController → ExecutionWorker → 确定性 Pipeline Stage Runner
      ├─ MaterialsService
      ├─ Quality / GateEvaluator
      └─ Artifact / Export Service
  → Artifact / Material / Quality 结果
  → control.db + Workspace Event Stream
  → GoalCoordinator 复核并推进计划
```

只有 Pipeline 阶段执行进入 Stage Runner；材料验证、Policy Decision、Gate 重验、暂停控制和下载等领域 Command 由 Dispatcher 路由到对应应用服务，但仍共享同一 Gateway、revision、Policy、Confirmation、lease/控制事务和审计事件。

### 2.1 核心原则

1. Agent 只负责理解目标、生成计划、提出 Command、读取结果和决定下一步。
2. Pipeline 是唯一可以执行阶段和生成流水线产物的内核。
3. Chat、页面按钮与 CLI 使用同一 CommandGateway、Policy 和门禁。
4. 每个工作区显式携带 `workspace_id`，不存在服务端隐式“当前工作区”。
5. SQLite 保存控制状态；Markdown、JSON 报告、DOCX 和源文件继续保留为文件。
6. 每个工作区同一时刻只允许一个变更 Operation；只读查询可并发。
7. 阶段完成必须有成功执行记录和有效 Artifact manifest，不能只凭文件存在判断。
8. Policy、Gate、确认或状态读取异常一律 fail-closed。

### 2.2 SQLite 控制面

每个工作区创建 `workspace/control.db`，启用 WAL、外键、busy timeout 和 schema version。首版至少包含：

- Goal 与 PlanStep 当前状态；
- Command、Operation、StageRun 和确认记录；
- Artifact manifest、依赖 fingerprint 与 stale 状态；
- MaterialRequirement、Submission、Verification 与 Fulfillment；
- GateEvaluation、Finding、Issue 和 PolicyDecision；
- ChatMessage、ActionProposal；
- 追加式 WorkspaceEvent 审计流。

当前状态表是权威读模型；事件表用于审计、SSE 增量推送和故障诊断。V2 首版不要求仅靠事件回放重建全部业务状态。

## 3. 公共协议

### 3.1 `WorkspaceContext`

```text
WorkspaceContext:
  workspace_id: string
  root: absolute path
```

`WorkspaceContext` 必须在 API/CLI 边界解析并传入应用服务。领域模块不得使用可选 root，也不得回退到 `project_root()`。

### 3.2 `CommandEnvelope`

```text
CommandEnvelope:
  command_id: UUID
  workspace_id: string
  kind: string
  payload: object
  goal_id: string | null
  actor: object
  expected_revision: integer
  idempotency_key: string
  confirmation_id: string | null
```

首批标准 Command：

- `pipeline.start`
- `pipeline.resume`
- `pipeline.run_stage`
- `pipeline.pause`
- `pipeline.cancel`
- `pipeline.skip_stage`
- `repair.execute`
- `gate.revalidate`
- `materials.attach`
- `materials.verify`
- `materials.confirm`
- `materials.apply`
- `document.rewrite`
- `document.apply`
- `document.undo`
- `issue.accept_risk`
- `export.build_draft`
- `export.build_final`

### 3.3 `CommandReceipt`

```text
CommandReceipt:
  command_id: UUID
  operation_id: UUID | null
  status: accepted | requires_confirmation | rejected | duplicate | no_op
  workspace_revision: integer
  confirmation_id: string | null
  error: object | null
```

相同 `idempotency_key` 只能产生一次 Operation。`expected_revision` 过期时返回冲突，不得根据旧动作继续执行。

### 3.4 `ActionProposal`

```text
ActionProposal:
  action_id: UUID
  workspace_id: string
  goal_id: string | null
  label: string
  command: CommandEnvelope
  risk: low | medium | high | critical
  requires_confirmation: boolean
  expected_revision: integer
  expires_at: timestamp
```

前端确认时只提交 `action_id`，不得重新拼接 tool、command 或参数，也不得通过按钮文案推测下一阶段。

### 3.5 `WorkspaceSnapshot`

Snapshot 必须包含单调递增的 `revision`，以及当前 Goal、Operation、Pipeline、Materials、Findings、Artifacts、确认请求和风险摘要。UI 首次加载 Snapshot，之后只订阅同一工作区的事件流；发现事件断档时重新拉取 Snapshot。

### 3.6 `GateReceipt`

```text
GateReceipt:
  receipt_id: UUID
  workspace_id: string
  workspace_revision: integer
  artifact_revisions: object
  gate_input_fingerprint: string
  evaluator_version: string
  verdict: pass | warn | block
  blocking_finding_ids: string[]
  accepted_risk_ids: string[]
  created_at: timestamp
```

`gate_input_fingerprint` 覆盖输入 hash、材料 Fulfillment/Verification revision、正式稿 Artifact revision、当前 Finding 集、适用 PolicyDecision 和门禁规则版本。正式 Word 的生成和下载必须重新计算并匹配该 fingerprint；聊天消息等无关 revision 变化不应误使凭据失效，任一门禁依赖变化则必须使旧凭据失效。

### 3.7 V2 API

```text
POST /api/v2/workspaces/{id}/chat/turn
POST /api/v2/workspaces/{id}/commands
POST /api/v2/workspaces/{id}/actions/{action_id}/confirm
POST /api/v2/workspaces/{id}/actions/{action_id}/decline
POST /api/v2/workspaces/{id}/confirmations/{confirmation_id}/confirm
POST /api/v2/workspaces/{id}/confirmations/{confirmation_id}/decline
GET  /api/v2/workspaces/{id}/operations/{operation_id}
GET  /api/v2/workspaces/{id}/snapshot
GET  /api/v2/workspaces/{id}/events?after_seq={seq}
POST /api/v2/workspaces/{id}/materials/uploads
GET  /api/v2/workspaces/{id}/exports/draft
GET  /api/v2/workspaces/{id}/exports/final?gate_receipt_id={receipt_id}
```

`chat/turn` 请求至少包含 `message`、`expected_revision` 和 `idempotency_key`，响应包含持久化的用户/助手消息、`ActionProposal[]`、`CommandReceipt[]` 与最新 snapshot revision。查询只返回答案；用户显式输入“暂停”或“继续已确认 Goal”时，ConversationService 可把强类型 Command 提交给 CommandGateway 并返回 Receipt，但不得直接调用领域 mutation。取消、跳过和其他需要确认的意图只返回 ActionProposal，确认后再由 CommandGateway 执行。前端不得用正则自行提交第二次命令。

直接提交 Command 时，如 Policy 判定需要确认，CommandGateway 持久化 `ConfirmationRequest` 并返回 `requires_confirmation + confirmation_id`；Chat 或按钮产生的 Action 则通过 `action_id` 确认。两条确认入口最终调用同一个 ConfirmationService，且确认请求只能消费一次。确认与拒绝请求均不得重新提交或覆盖原 Command payload。

材料上传接口只把 multipart 文件写入隔离暂存区，返回短期、单次使用的 `upload_token/hash`，不创建 MaterialSubmission，也不改变材料状态。随后由 `materials.attach` Command 经 CommandGateway 校验 token、文件策略和 revision，转存为受控 Artifact 并创建 Submission，再由 `materials.verify` Command 驱动验证；禁止客户端提交任意服务端绝对路径作为材料来源。Operation 查询用于 CLI、刷新恢复和无 SSE 客户端读取终态。final 下载必须同时校验授权、final Artifact manifest 和 GateReceipt fingerprint；不匹配返回 `409 GATE_RECEIPT_STALE`，不得降级为无门禁下载。

### 3.8 Event Stream 与错误协议

```text
WorkspaceEvent:
  seq: integer
  event_id: UUID
  workspace_id: string
  workspace_revision: integer
  kind: string
  aggregate_type: string
  aggregate_id: string
  payload: object
  occurred_at: timestamp
```

SSE 使用 `text/event-stream`，事件 `id` 等于 `seq`；客户端通过 `Last-Event-ID` 或 `after_seq` 断点续传。事件允许重复投递，客户端按 `seq` 去重；发现乱序或缺口时停止应用增量并重新获取 Snapshot。V2 首版不自动删除 WorkspaceEvent；后续如压缩事件，必须保留可验证的 snapshot checkpoint，并对无法续传的游标返回 `410 EVENT_GAP`。

公共错误至少包括 `REVISION_CONFLICT`、`LEASE_CONFLICT`、`CONFIRMATION_REQUIRED`、`ACTION_EXPIRED`、`ACTION_REPLAYED`、`GATE_BLOCKED`、`GATE_RECEIPT_STALE`、`AUTH_FORBIDDEN`、`EVENT_GAP` 和 `STATE_UNAVAILABLE`。错误响应必须带稳定 `code`、当前 revision、可否重试和关联 ID；未知错误不得被解释为成功。

## 4. 领域边界

### 4.1 Workspace / Conversation / Command 控制面

- WorkspaceRegistry 负责将 `workspace_id` 解析为规范化根目录、工作区状态和访问控制；请求中的路径不能替代该映射。
- Conversation 只拥有 ChatMessage 与 ActionProposal，不拥有 Pipeline、材料或 Gate 状态。
- CommandGateway 负责命令规范化、幂等、revision 校验、Policy、Confirmation、workspace lease 申请和 Receipt；它是所有 mutation 的唯一入口。
- ConfirmationService 负责动作过期、防重放、Command hash、actor 与授权范围校验。
- 身份由服务端认证上下文产生，客户端 payload 中的 `actor` 仅作显示信息，不能自行声明角色。
- 控制面不得直接写领域结果；它只能调用对应应用服务并在同一控制事务中记录状态与事件。

### 4.2 Goal / Agent

负责：

- 用户目标、范围、约束和成功条件；
- 计划步骤和确认范围；
- 根据 Operation 结果复核目标并选择下一条 Command。

禁止：

- 直接调用阶段函数或长任务；
- 直接写材料、Issue、Artifact 或 Pipeline 状态；
- 根据聊天历史宣告任务已经完成。

Goal 步骤只能由 `OperationCompleted`、`OperationBlocked`、`OperationFailed` 等执行事件推进。

### 4.3 Execution / Pipeline

负责：

- workspace lease、Operation 队列和阶段 attempt；
- 子进程启动、heartbeat、暂停、取消和重启接管；
- 阶段依赖、重试、输出校验、Artifact manifest 和终态事件。

状态机固定为：

- Command：正常路径为 `received → pending_confirmation → accepted → dispatched → completed`，无需确认时可从 `received` 直接进入 `accepted`；确认前可进入 `rejected/expired`。重复幂等键只关联原 Command，不创建新分支。
- Operation：正常路径为 `queued → running → succeeded`；暂停路径为 `running → pausing → paused → running`；`queued/running/paused` 可进入 `cancelling → cancelled`，执行也可进入 `failed` 或 `blocked` 终态。
- StageRun：`queued → running → succeeded/failed/cancelled`，或在校验既有有效产物后记为 `reused`；`skipped` 仅适用于被声明为可选的阶段。

终态通过带 revision 的 compare-and-set 写入且不可回退。暂停/取消与阶段完成竞态时，先成功提交的终态生效，后到控制命令返回 `no_op` 并携带当前终态；重试必须创建递增 attempt，不得覆盖旧 StageRun。

`pipeline.pause/resume/cancel/skip_stage` 是绑定现有 `operation_id` 的控制 Command，不创建第二个 Operation，也不与目标 Operation 竞争 workspace lease。ExecutionController 使用目标 Operation 当前 fencing token 和 revision 执行状态转换；恢复时续租或事务性取得更大的 token。`skip_stage` 在确认后用同一 token/CAS 记录 PolicyDecision 与 StageRun 终态。只有 `pipeline.start/run_stage`、Repair、材料应用、改稿和导出等执行命令会创建新的变更 Operation。

workspace lease 至少包含 `lease_id`、单调递增 `fencing_token`、owner、heartbeat 和 `expires_at`。Worker 的每次控制状态写入必须校验 fencing token；心跳超时只允许新 owner 通过事务接管并取得更大 token，旧 Worker 随后的写入全部拒绝，从而避免重启或网络抖动导致双跑。

阶段 Ready 的统一条件为：

1. 最新 StageRun 终态为 `succeeded/reused`；仅无必需输出且无强制下游依赖的可选阶段允许 `skipped`；
2. 必需输出存在且校验通过；
3. 输出 hash 与 manifest 一致；
4. producer input fingerprint 等于当前依赖 fingerprint；
5. Artifact 未标记 stale。

阶段子进程可以继续在工作区生成文件，但不得写控制数据库。Worker 持有独占 lease，并在子进程退出后校验文件，再事务性记录 manifest 和终态。

人工跳过必须记录 PolicyDecision，且只适用于无必需输出和无强制下游依赖的可选阶段。当前有效 Artifact 应记录为 `reused`，不属于 skip；缺少必需产物时拒绝 skip，目标 Operation 保持或进入 `blocked`，不能凭 waiver 将缺失产物标记为 ready。

`pipeline.skip_stage` payload 必须包含 `operation_id`、`stage_id` 和非空 `reason`，始终要求确认。目标阶段非可选、存在必需下游输出、当前正在不可安全中断的写入，或 Gate/Policy 不允许跳过时，CommandGateway 即使收到确认也必须拒绝并返回稳定阻断原因。

### 4.4 Materials

统一生命周期：

```text
missing → requested → submitted → verifying → verified → applied → resolved
```

例外状态：`rejected`、`waived`、`not_applicable`。

- `MaterialRequirement` 保存稳定 requirement ID、招标来源、风险级别和受影响章节。
- `MaterialSubmission` 保存上传 Artifact、hash 和提交人。
- `MaterialVerification` 保存自动/人工验证结果、字段、页码、主体、有效期和证据引用。
- `MaterialFulfillment` 是唯一生命周期状态。
- submitted/uploaded 绝不等于 verified。
- 资格或 fatal 材料不能 waiver，也不能通过接受风险关闭。
- 只有 verified 或具有完整审计记录的人工确认才能执行 `materials.apply`。
- 应用材料后，通过依赖图失效受影响的 context、chapter、review、coverage、gate 和 export，再生成最小恢复 Operation；Materials 不直接恢复 Goal。

### 4.5 Quality

- 业务报告作为不可变证据 Artifact 保存。
- `GateEvaluator` 基于当前 revision 产生不可变 `GateEvaluation` 和强类型 `Finding`。
- Finding 必须包含稳定 rule/target key、风险类型、来源 revision 和证据引用，禁止根据标题文字推断 fatal/major。
- `Issue` 是 Finding 的人工处理投影，负责 `open/in_progress/resolved/accepted` 生命周期。
- `PolicyDecision` 单独记录接受风险、人工确认、actor、理由和适用范围，不得删除或修改 Finding。
- `GateReceipt` 是 GateEvaluator 在指定输入 fingerprint 上应用 PolicyDecision 后的不可变凭据，引用 Finding 与决策集合；Issue 状态变化只触发重新评估，不能自行改写既有 Finding 或 GateReceipt。
- 所有继续、导出和下载判断只调用一个 GateEvaluator，不允许各模块自行过滤报告或重复实现 `can_proceed`。
- 结构化材料占位节点必须从文本命中扫描中排除，不能凭“授权委托书待补”等占位文字判定材料已存在。

### 4.6 Artifact

Artifact 负责路径、hash、producer、输入 fingerprint、revision 和 `ready/stale/missing` 状态。任何手工改写、材料应用或上游重跑都必须通过统一依赖图传播失效，目录型与 glob 型产物使用同一种规范化 key。

### 4.7 Export

- `draft.docx` 可在存在未解决风险时生成，但必须包含草稿标识和风险登记。
- `final.docx` 必须持有当前有效且 verdict 非 block 的 GateReceipt。
- final 下载接口重新验证 GateReceipt；历史遗留但凭据无效的 `final.docx` 只能作为草稿提供或先重验。
- accepted major/minor 风险以及按管理员例外接受的 critical 风险持续显示并进入风险登记；fatal 与资格缺失永远阻断正式交付。

## 5. 两阶段迁移

### 5.1 阶段 A：止血并收敛控制链

1. 冻结新增流程功能，为工作区隔离、聊天控制、材料、门禁和恢复建立基线用例。
2. 引入强制 `WorkspaceContext`，修复 Agent、Goal Compiler、Tool 和 Chat 的隐式根目录回退。
3. 建立最小 `control.db`、Workspace revision、Command、Operation、Confirmation、Event 和 lease 表。
4. 由 CommandGateway 暂时包装现有后台 PipelineSupervisor，使阶段仍可复用当前 CLI 子进程实现。
5. 将 Chat、按钮、CLI、旧 `start-pipeline/run-command`、Repair 和 mutation Tool 全部转入 CommandGateway。
6. 删除前端和后端对“继续/确认”的执行 fast-path；不得在请求线程同步运行 mutation Tool。
7. 增加 `pipeline.pause/resume/cancel`，让聊天输入“暂停/继续/取消”真正控制当前 Operation。
8. 提供单一 Snapshot 和 workspace-scoped SSE，停止各面板独立轮询并维护本地运行真相。

阶段 A 验收门槛：同一动作从 Chat、按钮或 CLI 发起时，必须得到相同的 Operation、Policy、Gate、日志和恢复行为；任何工作区都不能读写另一工作区或仓库根控制状态。

### 5.2 阶段 B：迁移领域状态并删除旧边界

1. 将 StageRun、Artifact manifest、stale 和依赖 fingerprint 全部迁入控制面，移除 artifact-only resume。
2. 将材料 Requirement、Submission、Verification 和 Fulfillment 迁入统一仓储，主 UI 接入真实上传和验证链路。
3. 将报告适配为 Finding，统一 GateEvaluator、Issue 投影、风险决策和 GateReceipt。
4. Mutation Tool 只生成 Command；删除 ToolRuntime 直接运行阶段的能力。
5. 前后端以 V2 API schema 和生成的契约类型为边界：ChatPanel 只管理消息和 ActionProposal，计划、执行、材料和问题由统一 Workspace Store 渲染；前端不得导入后端状态实现或直连文件/旧端点。
6. Web 作为主产品入口；CLI 调用同一应用服务；LangGraph 只保留调试适配；3D 只读订阅 Snapshot/Event；旧静态前端下线。
7. 兼容期结束后删除旧 orchestrator、前后端 fast-path、重复 Graph/CLI 编排和 JSON 控制状态双写。

### 5.3 旧工作区导入

- 首次打开 V1 工作区时，创建 `control.db` 并在单一事务中导入聊天、Goal、运行记录、材料、Issue 和 stale 状态。
- 对现有 Artifact 重新计算 hash，并结合成功事件判断 readiness。
- 导入过程记录 schema version、源文件 checksum 和迁移报告，可重复执行且不会重复创建记录。
- 多个状态源发生冲突时，创建 `MigrationConflict`，将工作区迁移状态标记为 `needs_reconciliation`，把相关导入 Operation 归一为 `blocked`，Goal 保持 `in_progress/blocked`，禁止猜测成功。
- 仓库根目录中的遗留 Goal 或 DecisionTrace 视为 orphan，不自动绑定任何 run。
- 旧 API 保留一个版本，内部翻译为 V2 Command 并返回弃用头；兼容期内不得继续维护第二套执行逻辑。

### 5.4 发布、兼容与回滚

1. 导入前对 V1 控制文件、关键 Artifact manifest 和已有 `control.db` 做只读清单与可恢复备份；迁移器先执行 dry-run，输出将导入、冲突、orphan 和不可识别记录。
2. V2 按工作区 feature flag 灰度开启。阶段 A 期间允许 V1 只读结果与 V2 Snapshot 做 shadow compare，但禁止两个控制面同时写同一工作区。
3. dry-run 无未解释的 fatal 冲突、迁移契约测试通过且管理员确认报告后，才允许单工作区切换；切换事务写入 schema version、迁移 checksum 和 cutover event。
4. `needs_reconciliation` 必须在受控的管理界面或 CLI 中逐项选择“绑定、标记失败、保留为 orphan”，保留 actor、理由和原始证据；未处理前禁止正式导出。
5. 阶段 A 回滚通过工作区 flag 停止 V2 Worker，并恢复备份或让 V1 兼容适配器读取未发生 V2 领域写入的工作区；阶段 B 开始后不得直接降级，必须进入维护模式并执行经过验证的反向迁移或备份恢复。
6. 兼容适配器至少保留一个已发布版本；只有迁移覆盖率、兼容 API 回归、监控窗口和回滚演练均通过后才能移除，移除条件写入阶段 B 验收记录。

## 6. 确认与安全规则

### 6.1 可直接执行

- 状态、材料、问题和产物查询；
- 暂停当前 Operation；
- 用户明确说“继续”时，恢复已经确认过范围的 Goal；
- Policy 允许的只读诊断。

### 6.2 必须确认

- 新建全量生成任务；
- 正文写作、改稿、材料应用和问题修复；
- 取消运行、跳过阶段；
- 接受风险；
- 生成或覆盖正式终稿。

确认必须绑定精确 Command hash、workspace revision、风险级别、actor 和有效期。Goal 范围确认只能列出允许的 Command kind 和作用范围，禁止默认授予 `all_mutations`。

critical 风险仅在不是 fatal/废标、不是资格材料缺口、Policy 明确允许且可信 admin 提供理由并完成二次确认时可接受；否则保持阻断。fatal 与资格材料风险对任何角色都不可接受。

### 6.3 身份、接口与文件安全

- 最小角色为 viewer、operator、reviewer、admin：viewer 只读；operator 可执行已确认 Goal 范围内的普通命令；reviewer 可完成人工材料验证和允许范围内的风险决策；admin 才可执行迁移 reconciliation 与受控关键操作。
- 每个请求都校验工作区 ACL，任何角色都不能跨工作区复用 Action、Confirmation、Operation 或 GateReceipt；服务端不得信任客户端传入的 actor/role。
- Action 与 Confirmation 绑定 workspace、Command hash、revision、actor、过期时间和一次性 nonce；确认后立即作废，篡改、过期、重复或跨工作区重放均拒绝并记审计事件。
- Cookie 会话的 mutation 接口必须启用 CSRF 防护；SSE、Snapshot、下载和 CLI token 使用相同工作区授权策略。
- 上传限制大小、扩展名和 MIME allowlist，净化文件名，按服务端生成 ID 落盘并计算 hash；压缩包防目录穿越和解压炸弹，可疑文件先隔离，不得进入解析或 Pipeline。下载同样校验工作区授权。
- 凭证、原始敏感材料和绝对路径不得进入聊天文本、普通日志或 WorkspaceEvent payload；审计记录保留标识与受控引用。

### 6.4 不可放行

- fatal/废标 Finding；
- 缺失或未验证的资格材料；
- 无可信管理员身份时的 critical 风险；
- 状态 revision 冲突、GateEvaluator 异常或来源不明的 Artifact；
- 已失效或与当前输入 revision 不一致的 GateReceipt。

认证、授权、Policy、Confirmation、Gate、lease 或状态读取任一异常时拒绝 mutation；不得以超时、解析失败或兼容适配器异常作为继续执行的理由。

## 7. 测试与验收

### 7.1 工作区与命令

- 工作区 A 的 Chat、Goal、Command 和产物绝不写入工作区 B 或仓库根目录。
- 多标签页切换不会改变服务端请求的 workspace 目标。
- Chat“暂停/继续/取消”、页面按钮和 CLI 对同一操作产生一致 Command 与 Operation。
- 暂停/恢复/取消只控制既有 operation_id，不创建竞争 Operation；取消仍在确认后执行。
- 过期 Action、错误 revision 返回冲突；重复点击返回 duplicate，且只执行一次。
- Action/Confirmation 的篡改、过期、重复消费和跨工作区重放全部拒绝并留下审计记录。

### 7.2 执行与恢复

- 一个工作区无法同时启动流水线、改稿、材料回填或文档编辑。
- 服务重启后 lease 能安全过期、接管或恢复，不会双重运行。
- 旧 Worker 持有过期 fencing token 时不能写入；SQLite WAL 在进程崩溃后可恢复到一致事务边界。
- pause/cancel 与子进程完成同时发生时只产生一个合法终态，重复控制命令返回 no-op。
- 非可选阶段、缺少 skip 理由或缺少必需产物时，即使已确认也必须拒绝 skip 并保持/进入 blocked；已有有效产物按 reused 验收，不能记为 skipped。
- 文件存在但无成功 StageRun、hash 不符或已 stale 时不得复用。
- 阶段进程崩溃留下的半成品不得成为 ready Artifact。

### 7.3 材料

- submitted/uploaded 状态不能关闭缺口或恢复 Goal。
- 自动与人工验证结果持久化到同一材料聚合，并保留 actor 和证据。
- 结构化待补占位符不得满足资格或必交材料门禁。
- 材料应用只失效并重跑受影响分支，不默认全量重跑。

### 7.4 质量与导出

- GateEvaluator 异常、关键材料 deferred、开放 fatal/critical Finding 均阻止 final 构建和下载。
- accepted major/minor 风险及符合管理员例外规则的 accepted critical 风险持续显示并进入风险登记。
- fatal 与资格缺失无法通过 API、Chat、CLI 或前端按钮接受。
- critical 仅在 Policy 允许、可信 admin 二次确认且不属于 fatal/资格缺口时可接受，其他 critical 保持阻断。
- 任一输入、材料或正文 revision 变化都会使旧 GateReceipt 失效。
- 与 Gate 无关的聊天 revision 变化不会单独使 GateReceipt 失效；门禁输入 fingerprint、规则版本或正式稿 manifest 任一变化必须阻断下载。

### 7.5 迁移与架构约束

- V1 工作区迁移可重复执行且结果一致，冲突和 orphan 不污染活动项目。
- V1 兼容 API 与 V2 API 产生相同执行语义。
- 架构测试禁止领域模块隐式调用 `project_root()`。
- 非 ExecutionWorker 不得调用 mutation runner。
- 前端不得直接调用旧执行端点或解释 Action label。
- SSE 重复、乱序、断线和事件缺口均能通过 seq 去重或 Snapshot 恢复，不会回退 UI revision。
- 迁移 dry-run、冲突 reconciliation、灰度切换和备份恢复演练通过；失败迁移不会修改 V1 真相源。
- Python 测试、前端契约测试、生产构建和端到端场景全部通过后，才允许删除兼容适配器。

## 8. 状态与变更记录

### 8.1 实施状态

| 范围 | 状态 | 完成标准 |
|---|---|---|
| V2 架构计划 | 已确认 | 目标边界、协议、迁移和验收规则形成本文档 |
| 阶段 A | 进行中 | 唯一 Command 控制链与工作区隔离通过验收 |
| 阶段 B | 进行中 | 领域状态迁移完成并删除旧执行旁路 |
| V2 正式切换 | 未开始 | 全量回归、旧工作区迁移和发布验收通过 |

状态只能按“未开始 → 进行中 → 已验收”推进。不得仅因代码合入就标记完成；代码已合入但尚未完成迁移、回归或发布验证时，只能标记为“进行中”。

### 8.2 变更记录

| 版本 | 日期 | 说明 |
|---|---|---|
| V2.0-plan | 2026-07-21 | 确认 Agent 控制面、唯一 Pipeline、SQLite 控制状态和两阶段迁移方案 |
| V2.0-A1 | 2026-07-21 | 开始阶段 A：引入 WorkspaceContext、control.db、CommandGateway、revision/idempotency/lease/fencing、Action 确认、Snapshot/SSE；聊天与页面的启动、继续、暂停和取消已进入统一控制链。Repair、改稿、材料、正式导出、旧工作区导入与旧旁路删除仍未完成，不构成 V2 切换。 |
| V2.0-A2 | 2026-07-21 | 将聊天最小修复迁入 `repair.start` Command 和 V2 Action 确认；修复 Worker 沿用同一 Operation/fencing token 回写进度与终态，partial 结果保持 blocked 且不再隐式恢复 Pipeline。定向改稿、材料、导出和其余 mutation Tool 仍待迁移。 |
| V2.0-A3 | 2026-07-21 | 将聊天定向改稿迁入 `rewrite.chapters` Command 和持久化 Action 确认；改稿 Worker 使用显式 WorkspaceContext，并将运行、成功、部分失败或异常回写同一 Operation/fencing token。聊天不再直接启动改稿线程。材料、正式导出和其余 mutation Tool 仍待迁移。 |
| V2.0-A4 | 2026-07-21 | 将材料状态更新与回填迁入 `materials.update` / `materials.refill` Command，页面与旧 API 均先创建持久化确认。禁止把 uploaded/submitted 直接标记为 ready，禁止放弃资格、必交和阻断材料，且未验证 ready 材料不得进入正文回填。材料领域表迁入 `control.db`、人工验证身份授权和上传文件清单仍待完成。 |
| V2.0-A5 | 2026-07-21 | 将材料登记、自动验证和人工核验迁入 `materials.upload` / `materials.verify` / `materials.confirm_verification` Command；登记与人工结论使用持久化确认，上传路径限制在当前工作区，人工核验操作者不再读取业务 payload。身份仍来自兼容 actor，服务端认证主体绑定和材料领域表迁移尚未完成。 |
| V2.0-A6 | 2026-07-21 | 建立首版正式稿 GateReceipt：`gate.revalidate` 对质量门禁、材料验证、出稿预检和 final Artifact 执行 fail-closed 复核，将凭据持久化到 `control.db`；V2 正式稿下载会重算输入与 Artifact fingerprint，变化后返回 `GATE_RECEIPT_STALE`。旧下载入口和现有页面尚待完成切换，因此本阶段仍未验收。 |
| V2.0-A7 | 2026-07-21 | Vue 与兼容页面的 Word 下载改为先执行 `gate.revalidate`、取得最新 GateReceipt，再访问 V2 正式稿接口；旧 `/api/download/final-docx` 也强制校验凭据，不再存在无门禁正式稿下载旁路。Markdown 下载仍按草稿能力保留。身份授权、领域状态迁移和发布验收仍未完成。 |
| V2.0-A8 | 2026-07-21 | 材料清单重建迁入 `materials.rebuild` Command，Vue 与兼容端点不再直接调用材料 mutation runner。至此材料更新、重建、登记、验证、人工核验和回填均已有 CommandGateway 入口；材料领域表仍是 V1 文件投影，尚未迁入 SQLite。 |
| V2.0-A9 | 2026-07-21 | V2 Command API 不再信任 JSON 中的 `actor`，统一从服务端请求上下文绑定主体；在认证中间件接入前使用明确的 `v2_api/anonymous` 兼容主体，避免客户端伪造操作者。正式身份认证、角色授权和工作区 ACL 仍未实施。 |
| V2.0-B1 | 2026-07-21 | 开始阶段 B：新增工作区隔离的材料暂存接口和 `control.db` 一次性 upload token；`materials.upload` 只消费当前工作区 token 并复核文件 hash，不再接受客户端提供的服务器路径，Vue 材料清单支持逐项“上传并核验”。同时收紧旧源文件上传的文件名、路径、可执行类型和大小边界。材料生命周期领域表与正式 ACL 仍待迁移。 |
| V2.0-B2 | 2026-07-21 | 新增 `control.db.material_states` 作为材料响应、生命周期和证据状态的 V2 权威表；旧工作区首次访问时单向导入，后续 V1 文件变化不覆盖 V2 状态。材料 Command 更新 SQLite 并同步 V1 文件投影，Snapshot、材料列表、回填门禁和正式稿 fingerprint 优先读取控制库。当前仍保留一个版本的 V1 双写适配，Finding/Issue/Policy 领域表与 ACL 尚待迁移。 |
| V2.0-B3 | 2026-07-21 | 单条/批量问题修复与风险接受迁入 `repair.issues` / `issues.accept_risk` Command，并强制持久化 Action 确认；旧 API 只生成提案，不再直接执行 mutation。风险接受忽略客户端 actor、admin 和二次确认标志，fatal、资格材料及未经服务端授权的 critical 风险均 fail-closed。Issue/Policy 权威表和正式角色授权仍待迁移。 |
| V2.0-B4 | 2026-07-21 | 质量门禁重验迁入 `quality.revalidate` Command，并使用显式 WorkspaceContext；高级 Tool 调试接口仅允许只读 analysis 工具或 dry-run，mutation/export 调用返回 `POLICY_DENIED`，关闭绕过 CommandGateway 的通用执行入口。Goal 兼容接口、Issue/Policy 权威表和 ACL 仍待处理。 |
| V2.0-B5 | 2026-07-21 | Goal 恢复迁入 `goal.resume` Command；旧 Goal 批量 mutation 授权接口返回 `POLICY_DENIED`，V2 只接受逐个持久化 Action 的风险确认，不再保留 `all_mutations` 全局放行能力。Goal 权威状态仍为兼容文件，后续需迁入控制库。 |
| V2.0-B6 | 2026-07-21 | Web 登录改为服务端校验和 HttpOnly/SameSite 会话 cookie，未配置密码时 fail-closed；所有 `/api` 请求经过认证中间件。`control.db.workspace_acl` 保存工作区 owner/editor/viewer，V2 路径按主体和读写类型校验，旧工作区仅允许管理员首次认领。当前为单管理员账号模型，后续可接入组织身份源和多用户授权管理。 |
| V2.0-B7 | 2026-07-21 | 人工复核更新迁入 `review.update` Command，并强制使用持久化 Action 确认；旧 `/api/manual-review/update` 只生成提案，Vue 在确认成功后刷新状态，不再直接写人工复核覆盖文件。人工复核文件仍作为一个版本的兼容投影，后续需迁入权威领域表。 |
| V2.0-B8 | 2026-07-21 | final.md 的单行、块、选区、全文和撤销写入统一迁入 `document.apply_edit` Command；旧编辑接口仅生成 Action，确认时校验 Artifact hash 防止覆盖并发修改，在同一 Operation 内同步重建 Word，重建失败自动恢复 final.md。AI 生成预览仍保持只读，只有确认操作能够写入终稿。 |
| V2.0-B9 | 2026-07-21 | 项目类型切换迁入 `workspace.set_profile` Command 和持久化 Action 确认；兼容页面的人工复核与项目类型操作均完成提案确认适配，不再把 202 提案响应误当成已写入结果。工作区删除/清理和非流水线 utility command 仍待迁移。 |
| V2.0-B10 | 2026-07-21 | 工作区内不属于标准阶段的维护命令迁入 `workspace.run_utility` Command；旧 `/api/run-command` 对此类命令只生成高风险 Action，Vue 与兼容页面确认后才由同一 Operation 同步执行。仅没有工作区上下文的根级 `validate/init-demo` 继续保留 V1 适配。 |
| V2.0-B11 | 2026-07-21 | 补齐工作区目录边界的 ACL：新建工作区立即绑定创建者为 owner，非管理员的工作区列表按 ACL 过滤，切换工作区必须具备读权限，删除工作区必须具备写权限且仅 owner/admin 可执行。删除/清理仍待迁入可恢复的持久化 Action 流程。 |
| V2.0-B12 | 2026-07-21 | 工作区删除和清理分别迁入 `workspace.archive` / `workspace.clean` 持久化 Action；Command 状态提交后才执行受边界校验的文件移动。删除改为移入 `runs/.trash`，清理改为保留 `control.db` 并将 V1 兼容状态与 outputs 移入工作区内归档，不再使用不可恢复的直接 `rmtree`。 |
| V2.0-B13 | 2026-07-21 | `control.db.issue_states` 与 `policy_decisions` 成为 Issue/Policy Decision 权威状态；V1 `workspace/issues/open.json` 首次单向导入后只作为兼容投影，后续文件变化不能覆盖 SQLite。风险接受追加不可变 Policy Decision，旧工作区已接受风险会生成确定性迁移记录，正式 GateReceipt 改为引用权威 Policy Decision。 |
| V2.0-B14 | 2026-07-21 | `control.db.goal_state` 成为 Goal 权威状态；V1 `workspace/agent/goal_state.json` 首次单向导入后只作为兼容投影，所有 Goal 状态机写入先提交 SQLite。运行状态说明同步标注 Goal/Materials/Issues/Policy 的新权威源，避免页面和诊断继续把兼容文件误认为真相源。 |
| V2.0-B15 | 2026-07-21 | `control.db.repair_job_state` 成为 RepairJob 权威状态；V1 `workspace/repair_job.json` 首次单向导入后只作为兼容投影，确认、claim、进度、重验和终态均先提交 SQLite。跨进程文件锁暂保留一个兼容版本，用于保护旧 Worker 的 read-modify-write 临界区。 |
| V2.0-B16 | 2026-07-21 | `control.db.agent_activity_state` 成为 Agent 工位/活动权威状态；V1 `workspace/agent/activity.json` 首次单向导入后只作为兼容投影，阶段开始、工位进度、结束和重启清理均先提交 SQLite。至此 Goal、AgentActivity、RepairJob、Materials、Issues 与 Policy Decision 已统一进入工作区控制库。 |
| V2.0-B17 | 2026-07-21 | 重启恢复改为以 `control.db.operations` 的 Operation/fencing token 为权威，`pipeline_control.json` 仅提供 Worker PID 和当前阶段 checkpoint；身份不一致时将 Operation fail-closed 为 blocked，暂停/取消在重启时完成终态收敛。Pipeline 状态同步失败不再被静默吞掉，避免流水线脱离控制库继续执行。 |
| V2.0-B18 | 2026-07-21 | V2 `repair.start` 在 Action 经 CommandGateway 确认后直接以 Operation ID 授权 claim RepairJob，不再二次校验 V1 `confirmation_id`。V1 聊天确认令牌继续保留一个兼容版本，但不再成为 V2 修复执行的隐藏第二道权威。 |
| V2.0-B19 | 2026-07-21 | Chat 与全部 V1 兼容命令适配器改为从服务端认证 Session 绑定 Command actor，移除 `current-user`、`run-command` 等伪主体常量；客户端 payload 继续不能覆盖审计主体，使 Chat、按钮和兼容 API 的权限与审计语义一致。 |
| V2.0-B20 | 2026-07-21 | 修复 blocked Operation 的补救命令分发表不一致问题：所有声明可在 blocked 状态执行的修复、材料、复核、文档和工作区命令统一复用同一判定集合，并刷新 fencing token；避免部分已确认命令因映射缺失触发异常或绕开 lease。 |
| V2.0-B21 | 2026-07-21 | 定向改稿与最小修复 Worker 在开始内容变更前必须成功同步权威 Operation；控制库写入失败会中止执行，不再静默继续。异步终态发布统一先清理进程内运行标志，再写 Operation，避免观察到 succeeded 与 RUNNING 并存。 |
| V2.0-B22 | 2026-07-21 | 主 Vue 控制台的共享状态总线从 V1 `/api/status` 切换到工作区 V2 Snapshot；Goal、AgentActivity、RepairJob、Materials、Issues 和 Operation 由 Snapshot 的 SQLite 权威字段提供，workflow/合规展示暂放在显式 `presentation` 兼容区。材料导入增加一次性空集合标记，SQLite 空状态也不再回退读取后来出现的 V1 项，避免兼容投影重新成为隐性权威。 |
| V2.0-B23 | 2026-07-21 | ChatPanel 的流程状态与 RepairJob 跟踪统一复用工作区 V2 Snapshot 状态总线，删除 `/api/status` fallback、`/repair-jobs/current` 和 AgentActivity 独立轮询入口；聊天、Goal 面板和 Agent 工位不再各自选择不同控制状态源。状态总线在工作区切换期间丢弃迟到响应并补发新工作区刷新，避免跨工作区 UI 污染。 |
| V2.0-B24 | 2026-07-21 | 主控制台接入工作区 WorkspaceEvent SSE，在 Command、Operation 和领域状态事件后合并刷新 V2 Snapshot，并保留定时轮询作为断线兜底。SSE 统一使用 `WorkspaceEvent` 类型、payload 内 `kind` 区分领域事件，支持 `Last-Event-ID`/`after_seq` 续传和客户端 seq 去重。 |
| V2.0-B25 | 2026-07-21 | ChatPanel 的非流水线维护命令改为直接提交 V2 `workspace.run_utility`，不再调用 `/api/run-command` 兼容适配器；高风险确认仍由统一 Action 接口完成。主 Vue 控制面的执行入口至此不再依赖旧命令分发 API。 |
| V2.0-B26 | 2026-07-21 | DocEditor 与 ChatPanel 的手工块修改、选区/流式 AI 预览应用和全文 AI 预览应用改为直接提交 `document.apply_edit`，并携带服务端返回的 final.md SHA 做乐观并发校验；旧文档端点只继续承担只读渲染、AI 内容提案、预览放弃和兼容撤销，不再负责这些正式写入。 |
| V2.0-B27 | 2026-07-21 | 终稿渲染、选区/全文 AI 提案、流式块改写、预览放弃与撤销提案新增显式 workspace_id 的 V2 路径，主 Vue 控制台停止依赖进程级 ACTIVE_RUN 选择文档工作区；旧路径继续作为一个版本的兼容别名。 |
| V2.0-B28 | 2026-07-21 | 工作区文件树和安全预览新增显式 workspace_id 的 V2 只读接口，FileExplorer 切换到新路径；文件浏览不再受全局 ACTIVE_RUN 切换影响，ACL 由 V2 路径中间件统一校验，旧接口保留为兼容别名。 |
| V2.0-B29 | 2026-07-21 | 材料清单、合规报告和正式出稿预检新增显式 workspace_id 的 V2 只读接口，主 Vue 控制台按当前工作区读取；材料与质量展示不再依赖进程级 ACTIVE_RUN，旧接口保留为兼容别名。 |
| V2.0-B30 | 2026-07-21 | 质量问题明细新增显式 workspace_id 的 V2 读取接口，步骤详情页按当前工作区获取 Issue 列表；旧接口保留为兼容别名，后续将移除读取时同步旧报告的副作用。 |
| V2.0-B31 | 2026-07-21 | 工作流步骤详情、人工复核摘要/条目、问题修复预览、批量预览和原因分析新增显式 workspace_id 的 V2 接口，步骤详情页不再借用进程级 ACTIVE_RUN；旧接口保留为兼容别名。 |
| V2.0-B32 | 2026-07-21 | V2 Issue 列表改为直接读取当前工作区 `control.db`，查询不再隐式同步或改写旧合规/全文复核报告；旧 `/api/issues` 继续承担一个版本的兼容同步行为，V2 响应显式标注 `source=control.db`。 |
| V2.0-B33 | 2026-07-21 | V2 单项/批量修复预览和原因分析改用 `control.db` 中的权威 Issue 快照；修复规划器支持显式 Issue snapshot，避免 V2 提案重新读取旧 `workspace/issues` 文件。旧接口仍按 V1 文件模型兼容。 |
| V2.0-B34 | 2026-07-21 | 聊天历史读取、追加和清空新增显式 workspace_id 的 V2 路径，主聊天窗口不再依赖进程级 ACTIVE_RUN 选择消息文件；消息内容仍作为展示型文件数据保存，旧接口保留为兼容别名。 |
| V2.0-B35 | 2026-07-21 | 招标文件、公司资料和模板源文件上传新增显式 workspace_id 的 V2 路径，聊天窗口与材料面板统一按当前工作区写入 `sources/`；路径 ACL、文件名、类型、大小及越界校验继续生效，旧上传接口保留为兼容别名。 |
| V2.0-B36 | 2026-07-21 | Agent 决策轨迹新增显式 workspace_id 的 V2 读取路径，目标面板与 SQLite Snapshot 的 Goal/Activity 使用同一工作区上下文；决策轨迹继续作为审计型文件数据保存，旧接口保留为兼容别名。 |
| V2.0-B37 | 2026-07-21 | Pipeline 运行日志新增每工作区 `runtime_logs.jsonl` 持久化与显式 workspace_id 的查询/SSE 路径，主聊天窗口不再订阅进程级混合日志；运行事件也按路径工作区读取，旧全局日志接口保留为兼容视图。 |
| V2.0-B38 | 2026-07-21 | 主工作区列表改用不含进程级 active workspace 的 V2 Catalog，页面切换仅改变客户端上下文，不再调用 `/api/select-run`；归档改为直接提交 V2 `workspace.archive` Command，并清理未使用的旧 Agent 工具 API 封装。 |
| V2.0-B39 | 2026-07-21 | 工作区创建新增 V2 Catalog POST，创建与 ACL owner 分配不再改写进程级 ACTIVE_RUN 或 `.active_run`；主创建对话框切换到新接口，旧 `/api/start-run` 继续保留选择 active workspace 的 V1 兼容语义。 |
| V2.0-B40 | 2026-07-21 | 新建工作区所需的项目类型选项拆为无工作区状态的 V2 Catalog 接口，创建对话框不再通过 `/api/project-profile` 隐式读取 ACTIVE_RUN 的当前项目配置；旧接口继续服务 V1 当前工作区视图。 |
| V2.0-B41 | 2026-07-21 | V2 Pipeline/Utility 前置质量门禁改为直接读取 `control.db` Issue 状态，不再在门禁查询中同步或改写旧报告文件；保留发现门禁阶段重验语义，fatal 与资格/废标风险即使 soft 模式也不可绕过，状态读取异常继续 fail-closed。 |
| V2.0-B42 | 2026-07-21 | 增加 V1 Issue 到 `control.db` 的显式一次性导入边界：首次 V2 Snapshot、Issue 查询/提案或门禁读取前导入 `workspace/issues/open.json`，之后永不由文件隐式覆盖 SQLite；旧文件缺失按空快照迁移，格式损坏则 fail-closed。 |
| V2.0-B43 | 2026-07-21 | PipelineSupervisor 支持由 V2 注入 SQLite Gate evaluator，启动、逐阶段推进和重启恢复统一使用同一门禁语义；V2 evaluator 异常时 Supervisor 写入失败状态并停止，消除内部旧门禁异常后 fail-open 的旁路。 |
| V2.0-B44 | 2026-07-21 | 终稿选区、全文和流式块改写提案的忙碌判断改为读取路径工作区的 SQLite Operation 与 Supervisor 状态，不再因其他工作区的进程级 RUNNING 被误阻断；控制状态异常仍按忙碌处理并拒绝提案。 |
| V2.0-B45 | 2026-07-21 | 正式 GateReceipt 指纹升级为直接纳入 `control.db` 的 Material、Issue 和 PolicyDecision 权威状态，不再把材料清单、Issue 或政策决定的 V1 文件投影视为第二权威源；旧投影变化不会误使凭据失效，SQLite 控制状态变化必定使凭据 stale。 |
| V2.0-B46 | 2026-07-21 | 修复 Windows 下 Pipeline checkpoint 原子替换偶发被短暂文件占用而令流水线失败的问题；临时文件替换增加有界退避重试，最终仍失败时保持 fail-closed 并由 Supervisor 记录失败状态。 |
| V2.0-B47 | 2026-07-21 | 建立首版 SQLite Artifact manifest：记录规范化 key、kind、hash、producer、输入 fingerprint、`ready/missing` 和 produced/reused disposition；V2 Snapshot 开始暴露 Artifact 列表，阶段成功但必需产物缺失或 manifest 写入失败时按执行失败处理。stale 依赖传播仍待下一切片完成。 |
| V2.0-B48 | 2026-07-21 | V2 Pipeline 阶段复用开始同时校验磁盘产物、SQLite manifest 状态、内容 hash 与输入 fingerprint；上游重跑导致产物或输入变化时，按 StageSpec 依赖图传递标记已有下游 Artifact 为 stale，Supervisor 不再仅因旧文件仍存在就跳过重建。旧 V1 产物允许在一个兼容版本内首次复用时补建 manifest。 |
| V2.0-B49 | 2026-07-21 | 正式 GateReceipt 签发开始检查 SQLite Artifact readiness：正式依赖存在 stale/missing、磁盘 hash 与 manifest 不一致或 final.docx 输入 fingerprint 过期时一律阻断；Artifact 权威状态同时纳入 GateReceipt 指纹，签发后状态变化会令凭据失效。无 manifest 的 V1 旧产物仅在一个兼容版本内继续允许验收。 |
| V2.0-B50 | 2026-07-21 | 修复 V2 Snapshot 将 SQLite Artifact manifest 被旧文件摘要覆盖的问题：`artifacts` 固定返回权威 manifest 数组，V1 文件展示摘要迁至 `artifact_files` 兼容字段；主前端适配器同步采用稳定数组契约，避免 UI 把兼容投影视为 Artifact 权威状态。 |
| V2.0-B51 | 2026-07-21 | V2 Snapshot 的工作流展示状态开始由 SQLite Artifact manifest 覆盖旧文件存在性判断：已记录阶段只有全部 manifest ready 才显示完成，stale/missing 阶段回到可执行并提示重建；尚未迁移 manifest 的 V1 阶段继续沿用一个版本的兼容展示。 |
| V2.0-B52 | 2026-07-21 | 草稿 Markdown 下载新增显式 workspace_id 的 V2 路径，主工作区页面停止通过 ACTIVE_RUN 下载其他工作区草稿；遗留 FloatingPreview 的步骤详情与文件预览也切换到显式工作区 V2 接口。正式 Word 仍必须携带有效 GateReceipt，草稿能力继续与正式导出门禁分离。 |
| V2.0-B53 | 2026-07-21 | 修复 Windows 测试中 Repair Worker 已提交 Operation 终态但线程尚未完全退出时 `control.db` 被临时目录清理抢占的竞态；内部启动结果暴露仅供生命周期同步的 Worker handle，回归测试在释放工作区前等待线程收尾，并连续五轮验证修复/控制状态用例。 |
| V2.0-B54 | 2026-07-21 | V2 Snapshot 的 Pipeline 状态改为以 `control.db.operations` 为权威，`pipeline_control.json` 只在 Operation ID 与 fencing token 同时匹配时提供当前阶段和 Worker PID；旧 checkpoint 与 SQLite 冲突时忽略其 running 状态并显式标记不一致，避免 UI 被过期文件伪装成仍在运行。 |
| V2.0-B55 | 2026-07-21 | 启动恢复从只检查 ACTIVE_RUN 扩展为扫描全部工作区：每个工作区都会收敛中断的 RepairJob 与 AgentActivity；单进程 Worker 无法自动接管的非活动 Pipeline Operation fail-closed 为 blocked（暂停/取消请求分别收敛为 paused/cancelled），关联运行中 Goal 标记为 blocked_human 并保留孤立 Operation ID，避免后台状态永久伪装为 running。 |
| V2.0-B56 | 2026-07-21 | V1 兼容 API 响应统一增加 `Deprecation`、HTTP 299 Warning 和 V2 successor Link；V2、认证及全局模型配置接口不标记弃用。兼容调用方现在可以在一个版本的迁移窗口内被机器可读地识别和监控，为后续删除旧路由提供调用量依据。 |
| V2.0-B57 | 2026-07-21 | 修复 V1 只读兼容接口的 ACL 绑定旁路：当请求携带 `workspace_id`/`run_id` 查询参数时，中间件对该精确工作区授权，不再只检查 ACTIVE_RUN 后由端点读取另一工作区；无显式参数时才回退到旧活动工作区语义。 |
| V2.0-B58 | 2026-07-21 | 修复 blocked Pipeline 的补救命令复用并终结原 Operation、导致后续无法继续的生命周期缺陷：Repair/Rewrite/Materials/Quality 等补救现在创建带 `parent_operation_id` 的独立子 Operation，原 Pipeline 保持 blocked；子 Operation 完成后 Snapshot 重新暴露活动父 Operation，用户可通过独立 `pipeline.resume` Command 增加 fencing token 并继续。control.db schema 升级为 12。 |
| V2.0-B59 | 2026-07-21 | `document.apply_edit` 重建 Word 后同步刷新 final.md/final.docx 的 SQLite manifest；final.md 以受审计 manual_override 保留为 build-md 当前产物，合规与格式报告标记 stale。正式 GateReceipt 输入新增 format_check_report，使人工改稿后必须重新完成质量/格式检查，不能因旧报告文件仍存在而直接正式出稿。 |
| V2.0-B60 | 2026-07-21 | 定向改稿、Issue 最小修复和材料回填等非 Pipeline 章节写入接入 SQLite Artifact 图：章节集合成功存在时刷新 write-all manifest，并按 StageSpec 传递 stale 到审核、摘要、来源追溯、评分覆盖、合规及最终文档链；外部 Worker 不再只写 V1 `stale_artifacts.json` 而让 V2 误判旧终稿 ready。 |
| V2.0-B61 | 2026-07-21 | `review.update` 除写入一个版本的人工复核兼容投影外，同时追加不可变 SQLite PolicyDecision，记录 category、item、结论、操作说明与服务端 actor；人工复核变化因此进入 GateReceipt 的权威 Policy 指纹，既有正式凭据会失效，不再因只改了复核文件而继续有效。 |
| V2.0-B62 | 2026-07-21 | `workspace.set_profile` 在项目类型实际变化后将当前 SQLite Artifact manifest 全部标记 stale；项目类型影响提示词、结构、篇幅和生成策略，后续 Pipeline 必须按新配置重建，不能继续复用旧项目类型下仅因文件存在而显示 ready 的产物。相同类型的幂等设置不触发失效。 |
| V2.0-B63 | 2026-07-21 | V2 人工复核列表与摘要改为以 SQLite `manual_review` PolicyDecision 覆盖兼容文件结论；同一 category/item 的最新不可变决定控制 pending/closed 展示，响应标注 `control.db` 或 `v1_projection` 来源。V2 摘要读取不再调用会写 `summary.json` 的 V1 聚合函数，消除查询时修改状态的副作用。 |
| V2.0-B64 | 2026-07-21 | 人工复核的执行期消费者（章节上下文、评分点分配、全文复核过滤和清单生成）在 control.db 存在时也以最新 SQLite PolicyDecision 覆盖 V1 override 文件；兼容投影被后续篡改为 pending/open 不能逆转已审计决定。无 control.db 的 V1 工作区继续沿用旧文件语义。 |
| V2.0-B65 | 2026-07-21 | V2 风险接受改为直接读取 SQLite 权威 Issue，并在同一事务内更新 Issue 与追加 PolicyDecision；critical 只接受服务端认证的 admin 角色和已消费的持久化 Action 确认，客户端 admin/confirm 标志继续无效，fatal 与资格材料风险始终 fail-closed。V1 open.json 仅在权威提交后作为兼容投影刷新。 |
| V2.0-B66 | 2026-07-21 | 修复 V2 Chat 转发层丢失认证上下文的问题：路径工作区仍覆盖客户端 run_id，同时完整保留服务端 principal，使聊天生成的 Command/Action 与按钮和 CLI/API 使用同一 actor、role 与审计语义，不再退化为 anonymous。 |
| V2.0-B67 | 2026-07-21 | V2 Snapshot 与工作流详情读取停止调用会写入 `manual_review/summary.json` 的 V1 聚合路径；人工复核摘要直接由 SQLite PolicyDecision 和只读兼容输入计算并标注 `source=control.db`。V2 查询不再因刷新页面修改工作区文件状态。 |
| V2.0-B68 | 2026-07-21 | 工作区招标、公司和模板源文件上传后立即将 `prepare-inputs` 及其下游 SQLite Artifact manifest 标记 stale；旧导入结果、章节、审核和终稿不能在源材料变化后继续显示 ready 或被 Pipeline 复用。V1 上传别名沿用同一失效语义。 |
| V2.0-B69 | 2026-07-21 | V2 正式出稿预检与 GateReceipt 签发改用只读 SQLite Issue 快照，不再调用会从旧报告同步 Issue、写风险登记文件的 V1 `export_preflight`；查询和签发期间旧报告只能作为门禁输入，不能反向覆盖权威 Issue/Policy 状态。V1 预检接口继续保留原兼容行为。 |
| V2.0-B70 | 2026-07-21 | V2 正式出稿预检对全文审核和专项合规报告执行严格 schema 检查；JSON 损坏、对象类型错误或缺少明确 `blocking` 布尔值均返回 `STATE_UNAVAILABLE` 并 fail-closed，不再把无法解析的质量状态当成“未阻断”。 |
| V2.0-B71 | 2026-07-21 | 持久化 Action 的确认/拒绝绑定提案创建主体：HTTP 确认接口重新读取当前服务端 principal，主体 ID 不一致返回 `CONFIRMATION_FORBIDDEN` 且不消费 Action；执行时角色使用确认瞬间的服务端角色，避免其他 editor 代确认管理员 Action 或利用提案时的过期权限。 |
| V2.0-B72 | 2026-07-21 | Cookie 会话的所有非只读 API 启用双提交 CSRF 防护：登录签发独立随机 token，服务端同时校验 SameSite cookie 与 `X-CSRF-Token`，Vue axios、原生 fetch 和 V1 静态页面统一自动附加请求头；缺失或不匹配返回 `CSRF_REQUIRED`，不进入 ACL 或 mutation。 |
| V2.0-B73 | 2026-07-21 | 新增 V2 控制面 CLI：通过正在运行的 HTTP 应用完成登录、CSRF、Snapshot、Command 提交和持久化 Action 确认/拒绝，服务端绑定 actor 并执行同一 ACL、revision、Policy、Gate、lease 与审计逻辑；CLI 不再需要为 V2 操作直接导入阶段 runner。旧阶段 CLI 暂保留为 V1 兼容入口。 |
| V2.0-B74 | 2026-07-21 | 关闭受管 `runs/<workspace>` 的旧阶段 CLI mutation 旁路：交互式 CLI 只能使用 V2 `control` 客户端，直接阶段命令返回拒绝；PipelineSupervisor 启动的受 fencing/lease 控制 ExecutionWorker 使用专用环境标记才可调用 runner。只读 `validate` 和仓库根 V1 兼容工作流暂不受影响。 |
| V2.0-B75 | 2026-07-21 | 删除已无调用方的 Web 内联阶段启动 fast-path，并要求定向改稿 Worker 必须携带有效 Operation ID 与 fencing token 才能启动；内部函数或未来适配器不能脱离 CommandGateway/lease 直接创建章节 mutation。 |
| V2.0-B76 | 2026-07-21 | Goal 成功判定改为对 Issue、AgentActivity、RepairJob 和人工阻断状态读取异常 fail-closed；任一权威控制域不可用都会返回明确阻断原因，禁止因旧代码吞掉异常而把 criteria 已满足误判为 Goal succeeded。 |
| V2.0-B77 | 2026-07-21 | `control.db` schema 升级为 13，新增不可覆盖原始证据的 MigrationConflict、`needs_reconciliation` Snapshot 状态和不可变协调审计；V1 Goal/Materials/Issues 在首次导入时若与既有 SQLite 权威状态冲突，不再猜测或覆盖。未解决冲突阻止所有普通 mutation 与正式 GateReceipt，只有服务端认证 admin 经持久化 Action 可选择绑定、标记失败或保留 orphan。迁移 dry-run、各选择的领域状态应用和备份恢复验收仍待后续切片。 |
| V2.0-B78 | 2026-07-21 | 新增只读迁移 dry-run API 与 CLI，可在不写入 control.db、不提升 revision 的前提下盘点可导入、已对齐、冲突、根目录 orphan 和无法识别状态；根目录旧 Goal/DecisionTrace 明确只列入 orphan，不自动绑定。CLI 新增 reconciliation 提案入口，仍必须经过服务端 Action 确认。各协调选择的领域状态应用和备份恢复验收仍待后续切片。 |
| V2.0-B79 | 2026-07-21 | 迁移协调在修改权威状态前创建逐冲突 SQLite 备份并把相对路径与 SHA-256 写入不可变决策；`bind_legacy` 可显式绑定 Goal、Materials 或 Issues，旧 Goal 的 succeeded/completed 必须降为 blocked_human 后重新验收，不能直接继承成功结论。`mark_failed` 对 Goal 写入失败终态，其他领域与 `keep_orphan` 保留 SQLite 权威状态。主动扫描并持久化 dry-run orphan/冲突仍待下一切片。 |
| V2.0-B80 | 2026-07-22 | 新增管理员确认后的 `migration.scan` Command：扫描会导入无冲突的 V1 Goal/Materials/Issues，并将冲突、根目录 orphan 和无法识别状态持久化为 MigrationConflict；普通 mutation 与正式 GateReceipt 因此会被未协调的扫描结果 fail-closed。Web、Chat/按钮与 CLI 均可通过同一 Action 链创建扫描提案。已处理证据的扫描去重与备份恢复命令验收仍待下一切片。 |
| V2.0-B81 | 2026-07-22 | 迁移 dry-run 会识别已处理的原始证据并归入 `acknowledged`，后续扫描不再重复登记同一已协调 orphan/冲突；尚未完成一次性导入的旧文件内容变化仍产生新的待处理证据，已完成导入的 V1 投影继续不能反向覆盖 SQLite。正式出稿预检新增只读迁移预检，发现未经管理员扫描登记的冲突、orphan 或无法识别状态时返回 `MIGRATION_SCAN_REQUIRED`，避免终稿门禁绕过迁移治理。备份恢复命令与端到端恢复验收仍待下一切片。 |
| V2.0-B82 | 2026-07-22 | 迁移 dry-run 与管理员扫描现在生成源文件 manifest（相对路径、SHA-256、大小、领域与导入状态）及稳定 fingerprint；扫描结果连同服务端 actor 写入 control.db、WorkspaceEvent 和 Snapshot `migration.last_scan`。这为灰度切换前的可重复扫描、变更检测和管理员审计提供权威证据，备份恢复命令与端到端恢复验收仍待后续切片。 |
| V2.0-B83 | 2026-07-22 | 迁移冲突检测和扫描范围扩展至 RepairJob 与 AgentActivity；已有 SQLite 状态而 V1 import marker 尚未建立时，旧 `repair_job.json` 或 `agent/activity.json` 不一致会创建 MigrationConflict，绝不覆盖 V2 权威状态。管理员 `bind_legacy` 现可显式绑定这两个领域，扫描 manifest 同步覆盖其源文件。Pipeline/运行记录的历史 Operation 导入与备份恢复演练仍待后续切片。 |
| V2.0-B84 | 2026-07-22 | 新发现 MigrationConflict 会在同一 SQLite 事务内将当前工作区所有活动 Operation 归一为 `blocked`，并把受影响数量写入事件；已在运行的 Pipeline/Repair 不会在状态冲突出现后继续作为可变更执行链运行，必须经管理员协调后再由显式 Command 恢复。历史 Operation 导入与备份恢复演练仍待后续切片。 |
| V2.0-B85 | 2026-07-22 | 新增迁移 SQLite 备份只读校验入口（V2 API/CLI）：逐份校验路径边界、SHA-256、SQLite `integrity_check`、必要控制表和 schema version，再将可用于恢复的备份明确标记为 verified。该切片只提供恢复前验证，不在运行中的 Command 执行链内直接替换 control.db；受控恢复演练仍待后续切片。 |
| V2.0-B86 | 2026-07-22 | 新增管理员确认后的 `migration.cutover` Command：仅当当前源 fingerprint 已完成扫描、没有 open MigrationConflict 时才能把工作区标记为 V2 控制面 active；切换记录服务端 actor、时间、fingerprint 和 WorkspaceEvent，并暴露于 Snapshot。源文件在扫描后变化或存在未协调冲突均拒绝切换。灰度开关的 V1 适配器下线和受控恢复演练仍待后续切片。 |
| V2.0-B87 | 2026-07-22 | dry-run/扫描新增旧 `pipeline_control.json` 与 `stale_artifacts.json` 清单项；二者被明确隔离为 orphan，不自动绑定为 V2 Operation 或覆盖 SQLite Artifact manifest。管理员必须保留为 orphan 或作出明确处理后才能完成切换，防止旧运行 checkpoint 或 stale 文件重新成为控制真相源。历史 Operation 的结构化导入与受控恢复演练仍待后续切片。 |
| V2.0-B88 | 2026-07-22 | `migration.scan` 现在在工作区写入原子 `workspace/migration_report.json` 兼容审计报告，包含 source fingerprint/manifest、导入清单、冲突/orphan/unrecognized 盘点、导入与发现数量和当前 Migration 状态。SQLite 仍是控制真相源；该报告只作为可审阅的迁移证据，不参与反向状态同步。历史 Operation 的结构化导入与受控恢复演练仍待后续切片。 |
| V2.0-B89 | 2026-07-22 | 新增显式 workspace_id 的 V2 迁移报告读取 API/CLI；报告必须存在、可解析且 workspace_id 匹配，否则 fail-closed 返回 404/503。迁移审计不再需要通过全局 active workspace 或直接文件访问获取。历史 Operation 的结构化导入与受控恢复演练仍待后续切片。 |
| V2.0-B90 | 2026-07-22 | MigrationConflict 归一 active Operation 时增加发起扫描的 Operation 排除项：管理员 `migration.scan` 可登记冲突并完成自身审计 Operation，不会被自己的 fail-closed 规则误标记 blocked；同工作区其他活动变更 Operation 仍会被阻断。 |
| V2.0-B91 | 2026-07-22 | 主 Vue 控制台新增“迁移”面板：管理员可查看 V2 Snapshot 中的 Migration 状态与 open conflict，提出扫描、cutover 或逐项协调 Action，并在界面上明确点击确认后调用同一 V2 Confirmation API。前端不直接修改迁移状态，也不自动确认高风险 Action。 |
| V2.0-B92 | 2026-07-22 | 新增无破坏性迁移备份恢复演练 API/CLI：管理员只能对已完整性校验的工作区内 backup 发起演练，系统把其复制到临时 SQLite、再次执行 integrity/table contract 校验后返回 `recovery_drill=passed`；演练不替换运行中的 control.db。 |
| V2.0-B93 | 2026-07-22 | Vue 迁移面板新增已校验备份列表与“演练”入口；前端仅调用管理员受限的 V2 API，展示无破坏性演练结果，不能触发运行中 control.db 替换。 |
