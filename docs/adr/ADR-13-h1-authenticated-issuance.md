# ADR-13：H1 认证签发、快照绑定和失效规则

- 状态：Accepted（骨架）
- 日期：2026-07-27

## 决策

- H1 绑定用户 principal、workspace、Blueprint 与完整规划依赖 DAG。
- 依赖 revision/hash 变化触发适用性重评；scope 不变可 carry-forward。
