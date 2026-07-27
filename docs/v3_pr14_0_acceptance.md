# PR-14.0 最小契约冻结包 — 验收报告

- 状态：Accepted
- 日期：2026-07-27
- 范围：ADR-01/02/11、canonicalization、Proposal/Validation/Gate/Planning/Promotion Schema、Artifact Registry、架构审查清单

## 交付物

| 交付 | 路径 |
|---|---|
| Canonicalization | `src/document_pipeline/canonicalization.py` |
| Proposal/Receipt Schema | `src/document_pipeline/proposals.py` |
| Artifact Registry | `src/document_pipeline/artifact_registry.py` |
| GatePolicyRegistry | `src/document_pipeline/gate_policy_registry.py` |
| ADR-01 | `docs/adr/ADR-01-agent-artifact-service-permissions.md` |
| ADR-02 | `docs/adr/ADR-02-proposal-gate-cas-promotion.md` |
| ADR-11 | `docs/adr/ADR-11-exact-proposal-receipt-binding.md` |
| Review checklist | `docs/architecture_review_checklist.md` |
| Test vectors | `tests/fixtures/v3_kernel/canonicalization_vectors.json` |
| Contract tests | `tests/test_v3_pr14_contracts.py` |

## 当前 enabled/promotable kinds

- RequirementLedger
- ScoreModel
- ProjectModel
- ResponseTopicGraph
- ChapterBlueprint

未启用但已登记（PR-16.1 前拒绝晋级）：InputManifest、TemplateStructureContract。

## DoD 核对

- [x] canonicalization vectors 稳定产出相同 hash（含 unicode / 列表顺序敏感向量）
- [x] ProposalEnvelope / ValidationReport / GateReceipt / PlanningGateReceipt / PromotionReceipt 合法/非法 fixture
- [x] PlanningGateReceipt carry-forward 缺少原 H1 / DAG / scope 失败
- [x] PromotionReceipt/GateReceipt 的 `receipt_hash` 字段与 `compute_receipt_content_hash()` 不冲突
- [x] enabled/promotable kind 均有真实 payload Schema 与 GatePolicy；`{}`、未知 kind、未知 policy 失败
- [x] ADR-01/02/11、checklist、Registry 可被 PR-15.1 测试直接引用
- [x] 本报告作为 PR-14.0 独立验收记录（不得由 PR-15.1 提交隐式代替）
- [x] Gate K 人工双签完成（见 `artifacts/release_gates/v3/K/v1/approvals.json`，result=PASS）

## 验证命令

```text
python -m unittest tests.test_v3_pr14_contracts -v
```

## 后续

PR-15.1 在本冻结契约上完成 exact binding、Store 封死与 Promotion 重算后，方可申请 Gate K 人工批准；批准前不启动 PR-16.1。
