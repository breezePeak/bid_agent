# PR-15.1 可信内核阻断修复 — 验收报告

- 状态：Automated suite green；Gate K **未批准**（`PENDING_HUMAN_APPROVAL`）
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
8. **P0 封死**：`record_v3_validation_report` / `issue_v3_gate_receipt` 需要 `KERNEL_SEAL`；无 seal 的 Store 直写一律 403。
9. **P0 封死**：写入与 Promotion 独立重验 Store payload Schema，并重算 Validation/Gate `receipt_hash`；伪造 hash 不能晋级。
10. cited_source_ids 默认解析 InputManifest；不存在的 Source ID fail closed。
11. PlanningGateReceipt carry-forward 缺少原 H1 / DAG / scope 时 Schema 失败。
12. `receipt_hash` 字段与 `compute_receipt_content_hash()` 分离。

## 负向矩阵（自动化）

见 `tests/test_v3_proposal_promotion.py` 与 `tests/test_v3_pr14_contracts.py`：

- validate A / promote B blocked
- 伪造全通过 ValidationReport 直写被 seal / schema 重验阻断
- 伪造 gate_service issuer / receipt_hash 直写被 seal 阻断
- Proposal A 不能复用 B 的 Gate
- producer 自证 fingerprint
- 同 operation 不同 hash
- 跨 workspace 提交
- 空 payload / 未知 Source ID
- 人工 Gate 拒绝 system 代签
- 半 revision 不残留
- 非法 Receipt fixture / carry-forward 缺字段

## 验证命令

```text
python -m unittest tests.test_v3_pr14_contracts tests.test_v3_proposal_promotion -v
python -m unittest tests.test_v3_requirement_agent tests.test_v3_score_agent tests.test_v3_planning_agent tests.test_v3_stage_runner -v
```

## Gate K

证据包：`artifacts/release_gates/v3/K/v1/manifest.json`

- 证据哈希：先将文本规范为 **LF + UTF-8** 再算 SHA-256（见 manifest.`hash_normalization`）
- `automated_result=PASS`，`result=PENDING_HUMAN_APPROVAL`，`approvals=[]`
- 人工批准角色：架构负责人 + 可信内核/安全复核人
- **未写入 approvals 前不得宣布 Gate K 通过，不得启动 PR-16.1**
