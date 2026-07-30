# ADR-09：Bid Master 复用 V3 唯一状态机

- 状态：Accepted（骨架）
- 日期：2026-07-27

## 决策

- Bid Master 是唯一顶层编排者，复用 CommandGateway / StageRunner / ControlStore。
- 禁止新增第二套 Supervisor/Runner/状态机。
