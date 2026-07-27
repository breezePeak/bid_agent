# ADR-04：ChapterBlueprint 与 WriterInputBundle 边界

- 状态：Accepted（骨架）
- 日期：2026-07-27

## 决策

- `ChapterBlueprint` 是 Writer 唯一的章节结构、目的与响应责任来源。
- `DocumentContract` / `DocumentPlan` 只能由有效 H1 后的 Blueprint 确定性编译。
- Writer 唯一调用参数是冻结的 `WriterInputBundle`。

## 后果

- 无 Blueprint / 无有效 H1 / Blueprint 外标题不得驱动 Writer。
