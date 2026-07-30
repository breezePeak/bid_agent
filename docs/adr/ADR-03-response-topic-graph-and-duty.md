# ADR-03：ResponseTopicGraph 与 ResponseDuty

- 状态：Accepted（PR-14.1 骨架冻结）
- 日期：2026-07-27

## 决策

- `ResponseTopicGraph` 是中央响应语义层，覆盖功能/架构/数据/安全/实施/服务/交付/验收/资格/商务/合规。
- `FeatureModel` 只能是 `topic_type=function` 子图派生 View。
- Topic 通过 `ResponseDuty` 表达不同章节响应责任；每个核心 Duty 恰好一个 primary chapter。

## 后果

- 禁止一 Requirement/Score 默认一个根 Topic。
- Planning/Writer 不得绕过 Topic/Duty 直接从 Requirement 平铺章节。
