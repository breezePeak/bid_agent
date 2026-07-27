# V3 架构审查清单（PR-14.0 冻结）

任何涉及 Agent、Artifact、Gate、Promotion、中间语言或 Writer 边界的 PR，合入前必须逐项回答。未回答视为 P0 审查失败。

## A. 权限与写入路径

1. 本次 Agent 的只读输入与唯一输出是什么？
2. 新增或消费了哪些 Artifact kind？是否已在 ArtifactKindRegistry 登记？
3. Agent 是否仍无法直接写 canonical Artifact / control.db 业务状态 / 最终 Word？
4. 新增 Agent 如何证明无法绕过 Artifact / Gate / Promotion？（代码权限 + 负向测试，不得只靠 Prompt）

## B. Proposal / Validation / Gate / Promotion

5. Proposal 类型与 payload Schema 是什么？空 `{}`、未知 kind 是否失败？
6. Validator 是否只按 `proposal_id` 从 Store 重载？是否绑定 exact `proposal_hash`？
7. ValidationReport / GateReceipt / PromotionReceipt 是否内容寻址并绑定依赖快照？
8. GatePolicyRegistry 是否声明完整必需 Gate、issuer、verdict？
9. dependency fingerprint 是否由可信内核重算，而非 producer 自证？
10. CAS、Artifact revision、Receipt 与审计事件是否原子提交？

## C. 依赖、陈旧与引用

11. 引用是否解析到同 workspace、正确 kind、明确 promoted revision/hash？
12. 上游 revision/hash、Prompt、模型、policy 变化后 stale 范围是否精确？
13. 相同 operation 不同 proposal_hash 是否冲突？

## D. 规划与写作边界

14. 是否影响 H1 PlanningConfirm？若影响，H1 绑定字段是否完整？
15. 是否影响严格模板结构？未授权结构变化是否为 0？
16. Writer 是否仍只能消费冻结 WriterInputBundle，且无法联网/读整标？

## E. 负向测试最低集

至少覆盖：

- Agent 直接写 canonical Artifact 失败
- 无 GateReceipt 无法晋级
- 陈旧 base_revision 无法晋级
- 同 operation 不同内容冲突
- 验证 A 晋级 B 失败
- Proposal A 复用 B 的 Validation/Gate 失败
- 伪造 / 过期 / 跨 workspace / 错误 issuer / 旧 policy / 缺 Gate 失败
- producer 自证 dependency fingerprint 失败
- 未知 kind / 空 payload / 未知 policy 失败

## F. 发布门

17. 本次变更是否触及 Gate K/S/A/P/B/M？证据包路径？
18. Gate K 未通过时是否仍避免把 candidate 语义 Artifact 当作生产事实？

审查人必须在 PR 描述中粘贴本清单的填写结果，并链接相关 ADR（至少 ADR-01/02/11）。
