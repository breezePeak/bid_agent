# PR-17.1 Requirement 语义校准 — 验收记录

- 状态：**工程增强已合入；Golden-A1 阈值未达标，不得作为正式 PR-18 输入**
- 日期：2026-07-27
- 前置：Gate K PASS、Gate S PASS、PR-14.1 基础设施

## 已完成

1. 受控推理接口 `RequirementInferenceProvider`；默认 `DeterministicRequirementExtractor`（版本化 prompt/model fingerprint）
2. SourceBlock **分批抽取** + 跨批去重/冲突记录
3. **稳定 clause_id / parent_clause_id**（内容寻址，不再用标题自由文本当父条款 ID）
4. 补遗 **作用域覆盖**（文本重叠/条款命中才 waive，禁止整文件无差别作废）
5. 反向覆盖审计改为 **语句级义务漏检**（一块内漏掉某个义务会失败）
6. 低置信度 → `needs_human` / `blocked`；非义务叙述 → abstain
7. Proposal coverage_audit 记录 SourceIndex revision/hash、batch audit、prompt/model/policy 版本

## 未完成 / 阻断

- 未接入真实 LLM 抽取路径（接口已预留，默认仍为确定性规则）
- Golden-A1 **无 expert_accepted 样本**，critical 召回率阈值未验证
- 正式 PR-18 reconcile/Validation/Promotion 仍不得依赖本阶段输出作为“语义完成”

## 验证

```text
python -m unittest tests.test_v3_requirement_agent tests.test_v3_stage_runner -v
```

## 后续

1. 用专家标注 A1 样本评测并迭代 Prompt/规则
2. 达标后再向 PR-18 提供正式 RequirementLedger 输入
