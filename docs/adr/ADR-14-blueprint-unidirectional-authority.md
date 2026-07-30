# ADR-14：Blueprint 到 DocumentContract/DocumentPlan/WriterInputBundle 的单向权威

- 状态：Accepted（骨架）
- 日期：2026-07-27

## 决策

- DocumentContract / DocumentPlan / WriterInputBundle 只能由 promoted 且 H1-confirmed Blueprint 派生。
- 旧 DocumentPlan 不得与 Blueprint 双写 canonical。
