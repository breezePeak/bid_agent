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

## Gate S

- 自动化矩阵：`tests/test_v3_gate_s_source.py`（晋级权威、JSON 非权威、确定性、PDF gap、合成深层模板冻结结构校验、真实模板稳定）
- 证据包：`artifacts/release_gates/v3/S/v1/`（`automated_result=PASS`，`result=PENDING_HUMAN_APPROVAL`）
- 合成深层模板冻结快照：`tests/fixtures/v3_source/template_deep_structure_freeze.json`

### 仍需人工

- Source 解析负责人 + 架构负责人写入 `approvals[]` 后，`result` 方可改为 PASS

### 已知风险

- 历史 `198` 只确认旧链路创建的独立任务数，不能证明真实模板节点数；Gate S 使用独立合成深层模板压力参数，并明确不作为业务规模或发布阈值
- PDF 使用可解析合成件证明 StructureGap/bbox 路径；生产扫描件仍需现场样本
- 旧工作空间迁移 inventory 仍归 Gate M

未人工批准前，不得宣称 Gate S PASS；但 PR-17～20 正式路径已不得把普通 `source_index.json` 当作权威事实（Stage 硬阻断）。
