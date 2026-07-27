# PR-16.1 canonical Source — 验收记录

- 状态：Implemented（技术主链完成；Gate S 证据待完整矩阵与人工批准）
- 日期：2026-07-27
- 前置：Gate K PASS

## 已交付

1. 强类型 Schema：`SourceIndex`、`SourceNormalizationCoverage`、扩展 `SourceBlock` kinds、`TemplateStructureContract` 节点 `level/numbering`
2. Registry 启用：`InputManifest` / `SourceIndex` / `TemplateStructureContract` + Gate policies `G0_*`
3. Service → Proposal → Validation → Gate → CAS Promotion：
   - 注册输入晋级 `InputManifest`
   - `normalize_sources` 晋级 `SourceIndex`
   - `compile_template_structure` 晋级 `TemplateStructureContract`
4. 磁盘 JSON 仅作 `authority=promoted_artifact_projection` 投影；直接改 JSON 不改变 promoted 事实
5. `by_role` 改为由 promoted SourceIndex 派生的只读 view（`by_role` 投影字段）
6. 下游 Requirement/Score/Planning Stage 只读 **promoted SourceIndex**
7. PDF：按页内位置统一排序 text/table；无文本页产生 `ocr_gap` / StructureGap；全书无文本仍阻断
8. DOCX：document-order 流；table 容器块 + cell；coverage 逐元素
9. 模板表格 Slot 绑定最近上游章节（非全文最后标题）
10. block identity 绑定 file hash + parser version + kind + locator

## 验证

```text
python -m unittest tests.test_v3_source_structure tests.test_v3_input_manifest \
  tests.test_v3_pr14_contracts tests.test_v3_proposal_promotion \
  tests.test_v3_requirement_agent tests.test_v3_score_agent tests.test_v3_stage_runner -v
```

## Gate S 剩余

- 198 节点模板零漂移专项 fixture/报告
- 更大规模 PDF bbox 真实样本矩阵
- Gate S 证据包人工双签（Source 解析负责人 + 架构负责人）

未完成上述项前，不得宣称 Gate S PASS；但 PR-17～20 正式路径已不得把普通 `source_index.json` 当作权威事实（Stage 硬阻断）。
