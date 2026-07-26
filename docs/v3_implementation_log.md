# V3 开发实施记录

本记录与 [v3_development_plan.md](./v3_development_plan.md) 同步。每个完成的 PR 都必须包含验证证据和独立 Git 提交。

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

- 状态：已完成
- 内容：新增 `ResearchService` 与 Provider 接口；研究仅由 `EvidenceNeed` 触发，发布到不可变的 `workspace/v3/evidence/batches` 快照，不会修改 `inputs/reference.md` 或触发全量切分。
- 控制状态：`control.db.evidence_needs` 记录预算、状态与活动证据批次；预算为零或检索失败会显式形成 gap。
- 安全边界：外部 Provider 不能发布企业能力 Claim；企业事实仍必须来自 `company` 输入角色。
- 验证：`python -m unittest tests.test_v3_research_service tests.test_v3_contracts tests.test_v3_document_planner`（11 tests passed）。

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
