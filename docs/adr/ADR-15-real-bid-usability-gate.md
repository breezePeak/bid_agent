# ADR-15：真实标书可用性盲测与 Gate U

- 状态：Accepted（V3 主架构基线；实现归 PR-27）
- 日期：2026-07-27

## 背景

Gate K/S/A/P/B 分别证明可信内核、Source、语义、规划和 Writer 入口满足约束，运行时 G6 证明单个工作空间的 Finding 已按规则关闭，Gate M 证明迁移和生产切换安全。这些结论都不能单独证明最终 Word 在真实投标场景中可用。

V2 的主要失败不是缺少生成调用，而是结构或流程跑通后就把输出当成正式标书，没有使用独立真实项目验证整标覆盖、证据可信、跨章一致性、人工改写量和最终页面质量。

## 决策

1. 新增仓库发布验收门 `Gate U：Real-Bid Usability`，顺序固定为 `Gate B → PR-24～PR-27 staging → Gate U → Gate M → production CAS`。
2. Gate U 使用未参与 Prompt、模型、规则、Golden 或阈值调优的匿名真实项目盲测集。Golden-A～D、构造样本、单元测试和开发人员自评不能替代 Gate U。
3. Gate U 只批准证据包中明确声明的 `supported_bid_profiles`。覆盖矩阵必须在看到盲测结果前冻结，并命中每个拟上线的输入类型、评分复杂度、模板模式、附件组合和 OCR 能力分支；未覆盖范围 fail closed。
4. 每次盲测生成内容寻址的 `UsabilityRunManifest`，绑定输入、Evidence、全部 promoted Artifact、Prompt/模型/规则/Renderer 版本、最终 DOCX、逐页渲染、审计 Finding、专家意见和人工编辑记录的 hash。
5. 每份最终 Word 都必须逐页渲染检查目录、标题编号、表格、图片、页眉页脚、交叉引用、分页、裁切、溢出和空白占位。
6. 两名投标专家独立盲审，争议由第三名专家裁决。每份项目单独判定，禁止使用跨项目平均分掩盖 blocking 失败。
7. Gate U 未通过时，产品输出只能标记为 `test_draft`，不得宣称正式标书、不得启用生产唯一写路径。
8. Gate M 的证据清单必须绑定已通过的 exact Gate U `id/version/hash`；Gate M 不得豁免、降级或补偿 Gate U。
9. Gate U 不替代每个工作空间的运行时 G0～G6/H1。影响语义、Evidence policy、Writer、Integration、Audit、Prompt、模型、Renderer 或模板适配器的变化，必须使受影响的 Gate U scope stale 并重新盲测。

## 单项目硬指标

- 强制、资格、废标和 critical Score 漏项：0。
- 无证据企业资质、业绩、能力、产品参数或数字 Claim：0。
- 项目范围、工期、金额、参数、交付物和验收口径的跨章硬冲突：0。
- 未关闭 critical Finding：0。
- 模板未授权结构变化：0。
- blocking 页面缺陷：0。
- Requirement/Score/Duty/Evidence/Claim 到最终内容的关键链路可回溯率：100%。
- 专家可用性平均分：`≥4.2/5`，任一维度：`≥4.0/5`。
- 无需实质性重写的 ContentUnit：`≥80%`；实质性重写 ContentUnit：`≤20%`。
- critical 主责章节整章推倒重写：0。

人工编辑分类必须使用证据包中冻结的版本化 taxonomy；措辞和格式微调不得冒充实质性重写，技术路线、范围、承诺、核心证据或主责响应变化必须计入实质性重写。

## 证据与审批

Gate U 证据包保存在 `artifacts/release_gates/v3/U/<version>/`，至少包含：

- manifest、supported profile 覆盖矩阵和盲测隔离声明；
- 输入、代码、Schema、Prompt、模型、Evidence policy、Renderer 和模板适配器版本；
- 每个 `UsabilityRunManifest` 及其输入/Artifact/DOCX/渲染 hash；
- 自动评测、逐页检查、专家独立评分、裁决和人工编辑分类原始记录；
- 逐项目结果、blocking finding、差异、已知风险和适用范围；
- 投标领域负责人、质量负责人和产品负责人的审批。

## 后果

- “架构正确”“测试通过”“Golden 达标”不再等同于“正式标书可用”。
- 发布能力被限定在有真实盲测证据的 profile 内，系统对未覆盖范围明确阻断。
- 模型或写作链变化需要重做受影响范围的 Gate U，但不无条件使无关 profile 失效。
- 生产切换顺序固定为先证明可用，再证明迁移安全。
