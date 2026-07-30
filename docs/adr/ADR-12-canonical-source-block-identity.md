# ADR-12：canonical Source、解析器版本与 SourceBlock 稳定身份

- 状态：Accepted（随 PR-16.1 / Gate S）
- 日期：2026-07-27

## 决策

- InputManifest / SourceIndex / TemplateStructureContract 为 canonical Artifact，经 Validation/Gate/CAS 晋级。
- block identity = 文件 hash + 确定性 locator + block kind + parser version。
- 磁盘 JSON 仅为可重建投影；`by_role` 只读派生。

## 后果

- 下游只读 promoted Source revision/hash。
- 直接修改 source_index.json 不改变权威事实。
