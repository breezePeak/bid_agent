# ADR-06：Integration Service / Agent 双层职责

- 状态：Accepted（骨架）
- 日期：2026-07-27

## 决策

- DocumentIntegrationService 做确定性装配与 Slot 校验。
- Integration Agent 只判断跨章语义冲突并输出 Proposal/RepairRequest。
- 不得因共享 requirement_id 自动删除 supporting 内容。
