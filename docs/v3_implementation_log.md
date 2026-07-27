# V3 开发实施记录

本记录与 [v3_development_plan.md](./v3_development_plan.md) 同步。每个完成的 PR 都必须包含验证证据和独立 Git 提交。

Bid Master Agent、投标中间语言、Evidence Layer、受控写作与全文审计的后续建设见 [v3_semantic_understanding_and_outline_development_plan.md](./v3_semantic_understanding_and_outline_development_plan.md)。

## PR-0：冻结基线与消除半接入状态

- 状态：已完成
- 提交：`9040c68 chore(v3): freeze PR-0 baseline`
- 记录：[v3_pr0_baseline.md](./v3_pr0_baseline.md)
- 验证：`python -m unittest tests.test_v3_pr0_baseline tests.test_pipeline_registry tests.test_main_v2_cli_guard`（10 tests passed）。全量 `unittest discover` 在 120 秒超时，未用于验收结论。

## PR-1：契约、Artifact 和 Schema V3

- 状态：已完成
- 内容：新增 `document_pipeline` Pydantic v2 领域契约、V3 Artifact 命名空间约束和 `control.db` V3 表；要求台账、项目模型、文档契约、全文计划、内容单元、内容块、终稿、质量报告及 ChangeSet 均携带版本与来源哈希。
- 数据库：Schema 版本从 19 升至 20；新增 `document_state`、`evidence_needs`、`content_unit_states`、`dependency_edges`、`change_sets` 和 `content_locks`。
- 验证：`python -m unittest tests.test_v3_contracts tests.test_v3_pr0_baseline tests.test_artifact_manifest`（17 tests passed）。

## PR-2：输入清单与来源规范化

- 状态：已完成
- 内容：新增角色显式的 `InputManifestService`，将本地导入文件复制到独立 V3 只读输入存储；模板替换自动停用旧版本并产生 ChangeSet。
- 规范化：新增 `SourceNormalizer`，生成带稳定 ID、输入角色与段落锚点的 `workspace/v3/source_index.json`，企业资料与外部参考资料按角色隔离。
- 失效：招标、评分或模板替换将结构和内容单元标记为全量受影响；企业资料保留局部影响入口，待 PR-3 的事实依赖边建立后精确定位。
- 验证：`python -m unittest tests.test_v3_input_manifest tests.test_v3_contracts`（10 tests passed）。

## PR-3：要求台账与项目整体模型

- 状态：已完成
- 内容：新增 `RequirementLedgerBuilder`，从招标和评分来源切片原子化抽取强制、评分、资格、交付、验收和合同要求，每项保留原文和稳定来源锚点。
- 项目模型：新增 `ProjectModelBuilder` 与理解门，独立生成目标、范围、工作包、交付物、验收、工期、角色、约束、已确认企业事实、未知项和 EvidenceNeed；外部参考资料不会被写成企业事实。
- 迁移边界：现有 `project_understanding.py` 仅保留为未自动接入的实验实现，待 V3 StageRunner 替代链通过后物理删除。
- 验证：`python -m unittest tests.test_v3_requirement_project_model tests.test_v3_input_manifest tests.test_v3_contracts`（13 tests passed）。

## PR-4：双模式文档契约

- 状态：已完成
- 严格模板：新增 OOXML 模板契约编译器，读取原始模板并识别标题层级、父子关系、文本/表格占位 slot 与结构指纹；未能映射的要求输出 `TEMPLATE_COVERAGE_GAP`，不创建新标题。
- 无模板：新增目录契约编译器，按要求台账分组生成带 `requirement_ids` 来源的节点；台账为空时直接阻断，禁止万能目录。
- 模式：`DocumentContractCompiler` 只由活动 `template` 输入决定 `template_strict`；其他 DOCX 角色不会触发严格模板模式。
- 验证：`python -m unittest tests.test_v3_document_contract tests.test_v3_requirement_project_model tests.test_v3_contracts`（12 tests passed）。

## PR-5：全文责任计划与规划覆盖

- 状态：已完成
- 内容：新增 `DocumentPlanner`，在正文生成前为每项要求和评分项分配唯一 primary owner，并写入 V3 `DocumentPlan` 与规划覆盖报告；未覆盖或重复主责会阻断。
- ContentUnit：自动将有父子关系的完整父章节归为写作单元；无模板扁平目录先合并为一个语义文档单元，避免“一标题一 Writer”。
- 验证：`python -m unittest tests.test_v3_document_planner tests.test_v3_document_contract tests.test_v3_requirement_project_model`（6 tests passed）。

## PR-6：按需研究服务

- 状态：已完成（真实 DeepSeek 搜索与附件上传已人工冒烟）
- 已完成：新增 `ResearchService` 与 Provider 接口；研究仅由 `EvidenceNeed` 触发，发布到不可变的 `workspace/v3/evidence/batches` 快照，不会修改 `inputs/reference.md` 或触发全量切分。
- 工具注册：`research.resolve` 已接入 `V3ExecutionController`，只允许解析 `ProjectModel` 中已声明的 EvidenceNeed。
- 实现路线：网页研究采用 Provider Adapter，而非绑定 DeepSeek；`deepseek_web` 是首个适配器，使用 Playwright 持久化浏览器会话。首次由用户在本机可见浏览器中登录，系统不保存账号或密码。每次问答均由明确的 EvidenceNeed 触发，回答作为外部二级证据并保留网页 URL、提问、回答和检索时间；不允许证明企业能力，也不绕过 V3 质量门。
- Agent 工具：新增 `V3ResearchTool`，只允许按 `need_id` 解析 `ProjectModel` 中已声明的 EvidenceNeed，并调用 Provider Adapter 发布证据批次；前端按钮只能触发已声明的 EvidenceNeed，发送文件还必须显式选择活动 `attachment_input_ids`，不允许任意网页问答直接写入标书。
- 默认 Provider：未设置 `BID_AGENT_RESEARCH_PROVIDER` 时使用 `deepseek_web`；适配器会强制启用“联网搜索”，提取并去重公开来源 URL，无法取得来源时拒绝发布。
- 控制状态：`control.db.evidence_needs` 记录预算、状态与活动证据批次；失败批次保留错误信息且不可覆盖，后续调用以新 revision 重试。
- 安全边界：外部 Provider 不能发布企业能力 Claim；企业事实仍必须来自 `company` 输入角色。
- 已验证：Provider 契约、来源 URL 解析、预算限制、失败重试和证据批次均有自动化测试；已使用 Playwright 持久化登录态完成真实联网搜索及无敏感测试附件上传，公开来源 URL 可正常进入候选证据。

## PR-7：ContentUnit Scheduler 与 Writer

- 状态：已完成
- 内容：新增 ContentUnit 初始化和调度器；控制库记录单元状态、尝试次数、证据快照与输出 Artifact。
- Writer：新增受契约约束的 ContentWriter，只能向 DocumentPlan 既有节点写 `ContentBlock`；严格模板模式要求目标节点已声明 slot，禁止新标题和共享状态写入。
- 验证：`python -m unittest tests.test_v3_content_units tests.test_v3_research_service tests.test_v3_document_planner`（5 tests passed）。

## PR-8：三级全文整合

- 状态：已完成
- 内容：新增 `DocumentIntegrator`，读取 ContentBlock Artifact、保留人工锁定内容，并以主责 Requirement 和规范化正文为键实际删除重复块；输出 `IntegratedDocument` 与不可变 rewrite trace。
- 验证：`python -m unittest tests.test_v3_integrator tests.test_v3_content_units tests.test_v3_contracts`（9 tests passed）。

## PR-9：追溯与质量门禁

- 状态：已完成
- 内容：新增只读 `QualityGate`，输出终稿覆盖与内容质量报告；任何未响应强制要求或无来源关键 Claim 会阻断 `verify_document` GateEvaluation。
- 验证：`python -m unittest tests.test_v3_quality tests.test_v3_integrator tests.test_v3_contracts`（9 tests passed）。

## PR-10：严格模板 Renderer

- 状态：已完成
- 内容：新增 StrictTemplateRenderer，复制 V3 原模板并只按声明 text/cell slot 写入已通过整合的 ContentBlock；模板模式不允许重建 Word、清空正文或异常时生成空白文档。
- 验证：`python -m unittest tests.test_v3_template_renderer`（1 test passed）。

## PR-11：无模板 Renderer

- 状态：已完成（编辑闭环将在 PR-12 API Command 中接入）
- 内容：新增 StandardRenderer，仅从 OutlineContract 与 IntegratedDocument 输出 Markdown/DOCX，禁止临时标题和章节文件拼接。
- 验证：`python -m unittest tests.test_v3_standard_renderer tests.test_v3_template_renderer`（2 tests passed）。

## PR-12：V3 API、前端和唯一执行入口

- 状态：已完成
- 已完成的后端内容：新增 `V3StageRunner`，覆盖 12 个正常内容阶段；`ingest_inputs` 只接受已登记的 V3 输入，材料清单由 V3 台账/项目模型派生，`verify_delivery` 只检查 `outputs/v3` 产物。
- 执行入口：新增 `V3ExecutionController`，由现有 `CommandGateway` 驱动 `document.run_stage` 或 `document.run_pipeline`；每个 V3 stage 都记录到同一 Command 的控制面 StageRun，拒绝未登记 stage。
- 状态投影：新增只读 `V3WorkspaceSnapshotBuilder`，统一展示 V3 输入、文档模式/契约/计划、项目模型、EvidenceNeed、材料缺口、ContentUnit、质量门禁和交付状态；不读取 V1 投影。
- 工作空间：V3 创建与列表 API 只识别 `workspace/v3` 布局，旧 V1/V2 工作空间不再出现在 V3 控制台；新 V3 工作空间不创建旧 pipeline 目录。
- API：新增 `/api/v3/workspaces/{id}` 下的 uploads、commands、snapshot、events、evidence、document、gate 和 export 端点；上传端点强制输入角色并写入 V3 不可变输入存储，Command 只经 `V3ExecutionController`，快照和读取端点只返回 V3 状态，导出必须有 `verify_delivery=ready`。CLI 已切换至 V3。
- 前端：主工作区视图替换为 V3 输入、执行、质量、证据缺口、材料和下载界面；生产构建不再包含 `/api/v2/workspaces` 或 `/v2/workspaces` 调用。
- 门禁：`render_document` 仅在 `verify_document=pass` 后执行；`verify_delivery` 校验 V3 DOCX 可读性、可见内容和哈希，并将结果写入 V3 渲染报告与控制面 GateEvaluation。
- 后续：PR-13 物理删除旧 V1/V2 控制面、前端组件、运行入口及相关依赖。
- 验证：`python -m unittest tests.test_v3_execution_controller tests.test_control_cli`（8 tests passed）；`npm run build` 与 `npm test` 通过。
- 汇总验证：`python -m unittest discover -s tests -p "test_v3_*.py"`（34 tests passed）。

## PR-13：删除 V1/V2 废弃逻辑

- 状态：进行中
- 已删除：V2 前端工作区视图、聊天、终稿编辑、问题单、材料清单、文件浏览、旧状态适配及相关前端测试；`frontend/src/api/index.js` 不再保留 V2 工作区 API 封装。
- 安全修正：认证中间件现在从 `/api/v3/workspaces/{id}` 正确提取工作区 ID，并在所有 V3 工作区读写接口上执行同一 ACL 校验。
- 公开服务面：所有 V1/V2 HTTP 路由已从 FastAPI 路由表移除，旧路径统一返回 `410 LEGACY_API_RETIRED` 并指向 `/api/v3/workspaces/`；物理实现和 Legacy Pipeline 正在继续收缩。
- 隔离：V3 Web 入口不再导入旧 `graph.state_recorder`；V3 请求只使用 `V3ExecutionController`、V3 输入清单、V3 快照与控制库事件，旧 V2 Web 集成测试已删除。
- Web 拆分：新增独立 `api.v3_app`，V3 服务启动命令已切换为 `python -m uvicorn api.v3_app:app --app-dir src --host 127.0.0.1 --port 7860`。
- 物理收缩：历史 `web_app.py` 及其 V1/V2 Web 测试已删除，运行代码和测试直接使用 `api.v3_app`。
- 数据清理：两个旧 V2 项目目录及其残留的 `workspace/chat.db` 已彻底删除。
- 运行入口：已移除 `graph-run` 与 `agent-graph-run` 两个 LangGraph CLI 公共命令，主图、Supervisor 图、节点和路由实现均已物理删除，章节循环已改为直接函数流；项目不再依赖 LangGraph/LangChain Core。V3 工作区仅通过 CommandGateway 执行阶段或全流程。

## PR-14：冻结 Bid Master 与投标中间语言架构

- 状态：已完成
- 提交：`2d775a7 docs(v3): freeze trusted Bid Master architecture`
- 内容：冻结唯一中间语言、Agent/Service/Artifact/Gate 权限边界、分阶段顺序和 Golden Set 验收指标；仓库级 `agent.md` 将 Proposal → Validation → Gate → Promotion 以及“新增 Agent 不得获得额外权威写入路径”设为强制架构约束。

## PR-15：Proposal / Validation / Gate / Promotion 可信运行内核

- 状态：已完成
- 输入与输出：Agent 只可通过 `AgentProposalSandbox` 读取其冻结任务输入并追加 `ProposalEnvelope`；唯一输出是候选 Proposal。`BidMaster` 仅协调既有 `ControlStore` 上的校验、Gate 和 Promotion，不新增状态机。
- Artifact 与晋级：新增 append-only Proposal、ValidationReport、GateReceipt、ArtifactRevision、active pointer 与 PromotionReceipt 表。`ArtifactPromotionService` 在单个 SQLite 事务中写入 revision、CAS active pointer 和 Receipt；同一 `(artifact_kind, operation_id)` 返回原 Receipt，避免重复发布。
- Validator / Gate：`ProposalValidator` 校验角色、引用、dependency fingerprint 与 base revision；`GateService` 仅对通过验证且绑定同一 Proposal hash/revision 的候选签发 Receipt。无 pass Receipt、陈旧 base revision 或非法角色均不能晋级。
- 权限与 stale：角色能力表只允许各 Agent 提议其声明的 Artifact kind，未登记工具一律拒绝；Promotion 与 `control.db` 只由 Service 持有。上游 active revision 或 dependency fingerprint 改变会使候选在 Gate/Promotion 时拒绝，不会覆盖当前事实。
- Snapshot：`V3WorkspaceSnapshotBuilder` 仅投影 promoted Artifact；旧磁盘文件及 draft Proposal 不再被投影为运行时事实。
- H1 / 模板：本次不改变 H1 PlanningConfirm，也不修改严格模板结构；后续 PR-16—PR-20 将消费该可信内核。
- 验证：`python -m ruff check src tests`；`python -m pytest -q --basetemp C:\tmp\bid_agent_pytest_v3_pr15`（437 passed, 9 subtests）。新增负向测试覆盖 Agent 越权、无 GateReceipt、陈旧 revision、幂等 operation、失败无半 revision 与 Snapshot 隔离。

## 后续架构基线：Bid Master 与投标中间语言

- 状态：PR-14 与 PR-15 已完成；后续按 PR-16 继续。
- 核心分工：Agent 只产生 Proposal，Artifact 承载权威事实，Service 执行确定性动作，Gate 与 CAS Promotion 决定 Artifact 晋级。
- 中间语言：`RequirementLedger → ScoreModel → ResponseTopicGraph/ResponseDuty → ChapterBlueprint → EvidenceSnapshot → WriterInputBundle → ContentBlock`。
- Agent：Bid Master 复用现有 CommandGateway/StageRunner；Requirement、Score、Planning、Writer、Integration 和 Quality Audit 均读取冻结快照，不直接修改 `control.db` 或 canonical Artifact。
- Evidence：新增统一 EvidenceRepository；DeepSeek 继续只是显式 EvidenceNeed 的 Research Provider，附件上传仍需逐次选择和授权。
- 规划门：正文前只增加一个 `PlanningConfirm`，统一确认项目摘要、异常要求、Topic/Duty、章节树和主责映射。
- Writer 边界：ChapterBlueprint 是唯一结构和响应责任来源，Writer 的唯一调用参数是冻结的 `WriterInputBundle`。
- 已知待修：现有 Integrator 不能再以共享 Requirement ID 作为删除 supporting 内容的依据；现有 QualityGate 需升级为独立只读 Audit 与最终语义门。
