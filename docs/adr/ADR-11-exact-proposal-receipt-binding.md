# ADR-11：Proposal 内容寻址、Validation/Gate 精确绑定与 GatePolicyRegistry

- 状态：Accepted（PR-14.0 冻结）
- 日期：2026-07-27
- 关联：ADR-01、ADR-02、PR-15.1

## 背景

审计复现过“Store 保存无效 payload，再校验同 ID 的另一份有效 payload，最终把原无效内容成功 Promotion”的漏洞。根因是 Validation/Gate 只绑定 `proposal_id`，未绑定 exact content hash。

## 决策

1. **内容寻址**  
   - `proposal_hash` 覆盖 workspace、kind、producer、operation、base revision、payload、声明依赖、schema/prompt/model 版本与 canonicalization version。  
   - 排除 `proposal_id`、`created_at` 等显示/身份字段。  
   - 使用冻结 `CANONICALIZATION_VERSION = v3-canon-2`。

2. **Validator 只按 proposal_id 重载**  
   - 禁止调用方同时传入另一份 payload 参与验证。  
   - ValidationReport 绑定 `proposal_id + proposal_hash + canonical_payload_hash + resolved dependency snapshot/fingerprint + validator/schema/policy version`。

3. **GateReceipt 内容寻址**  
   - 绑定 exact Proposal hash、ValidationReport id/hash、artifact kind、base revision、依赖快照、Gate policy version、issuer、issued/expires。  
   - `block` / `needs_human` / 未知 finding / 未知 policy 一律 fail closed。  
   - 非授权角色不得降级为 `pass`。

4. **GatePolicyRegistry**  
   - 每种 enabled/promotable Artifact 声明必需 Gate 集合、合法 issuer、允许 verdict 与 validator id。  
   - Promotion 必须校验完整 Gate 集合；任意一个名为 pass 的 Receipt 不够。

5. **依赖由内核重算**  
   - producer 只能声明 dependency fingerprint / DependencyRef。  
   - Validator 与 Promotion 事务内从 active promoted Artifact 独立计算 fingerprint 并比对。

6. **PromotionReceipt**  
   - 绑定 workspace、base/promoted revision、Artifact hash、完整 dependency snapshot，以及每个 GateReceipt 的 `id + immutable hash`。

7. **受控推理 InferenceReceipt**
   - Score、Project、Topic 和 ChapterBlueprint Proposal 必须各引用唯一、append-only 的
     InferenceReceipt。
   - Receipt 内容寻址覆盖 invocation、Capability/Skill 及版本、Prompt 及 hash、Provider
     指纹、模型指纹、温度、输出 Schema、exact input snapshot 及 hash、原始输出、
     normalized Candidate 和 compiled Proposal payload hash。
   - Validator 必须从 Store 重载当前 active dependency，并按 Capability 独立重建 exact
     Provider 输入；仅校验调用方提交的 snapshot hash 不构成证明。
   - 所有必需 dependency 必须在 `declared_dependencies` 中同时锁定 revision 与 hash；
     Provider 运行时元数据缺失、Receipt 被连同 hash 一起伪造、输入无法重建或任一指纹
     不一致均 fail closed。

## 后果

- “验证 A、晋级 B”必须稳定失败。  
- Proposal A 不得复用 Proposal B 的 Validation/Gate。  
- 伪造、过期、跨 workspace、错误 issuer、旧 policy、缺 Gate 全部失败。
- Prompt、Provider、模型、温度、Schema、Skill 或上游 Artifact 变化会使旧推理 Artifact
  与 H1 自动失效。

## 引用

- `src/document_pipeline/canonicalization.py`
- `src/document_pipeline/proposals.py`
- `src/document_pipeline/artifact_promotion.py`
- `src/document_pipeline/inference_receipts.py`
- `src/document_pipeline/inference_inputs.py`
- `src/document_pipeline/inference_runtime.py`
- `src/document_pipeline/gate_policy_registry.py`
