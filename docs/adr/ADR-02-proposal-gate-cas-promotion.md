# ADR-02：Proposal、Gate 与 CAS Promotion

- 状态：Accepted（PR-14.0 冻结）
- 日期：2026-07-27
- 关联：ADR-11 exact binding、PR-15.1

## 背景

若任意服务可直接写 active Artifact，则验证、审计和回滚均不可信。需要单一晋级事务。

## 决策

所有 canonical Artifact 必须经过：

```text
Proposal
→ Schema / Reference / Authority Validation
→ Domain Gate / 必要的 Human Gate
→ ArtifactPromotionService
→ Promoted Revision
```

规则：

1. Proposal 进入 append-only Store，不可原地替换 decision content。
2. ValidationReport 与 GateReceipt 是晋级前置条件，不是装饰字段。
3. Promotion 在单事务中完成：校验 Receipt 集合、CAS active pointer、写入 Artifact revision、写入 PromotionReceipt、写审计事件。
4. 陈旧 `base_revision`、失效 dependency fingerprint、未知 policy、非法 issuer 一律拒绝。
5. 相同 `operation_id` 绑定 exact `proposal_hash`；不同内容不得静默返回另一 Receipt。
6. 进程中断不得留下半有效 revision。

## 非本 ADR 范围

唯一中间语言对象链与依赖方向是独立架构契约，不占用 ADR-02 编号。

## 后果

- 下游只读 promoted revision。
- Snapshot / API / 前端不得把未晋级 Proposal 投影为当前事实。
- Gate K 未通过前，candidate 语义 Artifact 不得作为生产事实。

## 引用

- `src/control_plane.py` `promote_v3_proposal`
- `src/document_pipeline/gate_policy_registry.py`
