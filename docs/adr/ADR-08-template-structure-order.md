# ADR-08：TemplateStructureContract 的前置顺序

- 状态：Accepted（骨架）
- 日期：2026-07-27

## 决策

- 严格模板模式必须先生成只读 TemplateStructureContract，再进行 Blueprint 映射。
- 模板结构未授权不得变化；无承载位置必须 TEMPLATE_MAPPING_GAP 阻断。
