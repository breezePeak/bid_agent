# PR-15.1 可信内核阻断修复 — 验收报告

- 状态：Technically complete（待 Gate K 人工批准）
- 日期：2026-07-27
- 前置：PR-14.0 最小契约冻结包

## 已修复

1. Validator 只接受 `proposal_id`，从 Store 重载 Proposal；调用方 payload 不得参与权威验证。
2. ValidationReport 绑定 `proposal_id + proposal_hash + canonical_payload_hash + dependency snapshot/fingerprint + validator/schema/policy version`。
3. GateReceipt 内容寻址，绑定 exact Proposal/ValidationReport hash、issuer、policy version、workspace。
4. GatePolicyRegistry 按 artifact kind 强制完整必需 Gate 集合与合法 issuer。
5. Promotion 事务内重算 hash 与 active 依赖，校验完整 Receipt 集合后 CAS 提交。
6. operation 幂等键绑定 proposal hash；不同内容冲突。
7. “验证 A、晋级 B”稳定失败。

## 负向矩阵（自动化）

见 `tests/test_v3_proposal_promotion.py`：

- validate A / promote B blocked
- Proposal A 不能复用 B 的 Gate
- 伪造 issuer
- 错误 / 缺失必需 Gate
- producer 自证 fingerprint
- 同 operation 不同 hash
- 跨 workspace 提交
- 空 payload
- 人工 Gate 拒绝 system 代签
- 半 revision 不残留

## 验证命令

```text
python -m unittest tests.test_v3_pr14_contracts tests.test_v3_proposal_promotion -v
python -m unittest tests.test_v3_requirement_agent tests.test_v3_score_agent tests.test_v3_planning_agent tests.test_v3_stage_runner tests.test_v3_execution_controller -v
```

## Gate K

证据包：`artifacts/release_gates/v3/K/v1/manifest.json`

人工批准角色：架构负责人 + 可信内核/安全复核人。未获批前，candidate 语义 Artifact 不得切换生产 active chain。
