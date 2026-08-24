# PR-07：旧投标书解析、LegacyBidIndex 与 Markdown 投影


- 计划版本：`PR Plan V2.0`
- 仓库：`breezePeak/bid_agent`
- 起始基线：`85702e3aa60bd5e2f7b26a130ef7a6048499e020`
- 开发原则：本 PR 独立可部署、可测试、可回滚


## 1. 开始条件


- PR-06 已合并并全绿。
- full_write confirmed_plan_v2 单章 Pilot 稳定。
- legacy_inline 仍可回滚。


不满足开始条件时，本 PR 立即停止。不得以“先做一部分不影响”为理由继续。

## 2. 本 PR 的唯一目标


正式启用旧投标书输入，恢复完整目录树、章节边界和段落语义索引，形成可追溯的
`LegacyBidIndex` promoted Artifact 和只读 Markdown 投影。尚不生成融合目录或改写计划。


## 3. 当前代码事实


`SourceIndex.SourceBlock` 已具备稳定 block_id、heading_path、page、paragraph_index、
source_anchor 和 content_hash。

缺少的是旧标书专用目录树、父子章节关系、标题编号、章节正文边界、段落描述、
可复用条件、旧项目实体风险和 Artifact registry 里的旧标书语义索引。


## 4. 本 PR 允许修改的范围


允许：

- bid_rewrite workspace 开放 legacy_bid 上传；
- deterministic structure recovery；
- semantic description inference；
- inference receipt；
- LegacyBidIndex Artifact；
- Registry/Gate/Promotion；
- Markdown projection；
- 旧实体风险识别；
- API 只读预览。

不接入目录或 Writer。


## 5. 本 PR 明确不做


禁止：

- 旧标书进入 ProjectModel；
- 旧标书进入 RequirementLedger/ScoreModel；
- 自动复制正文；
- 新旧目录融合；
- 改写类型；
- 工作台显示旧段落来源；
- Writer 读取 LegacyBidIndex。


## 6. 预计文件、类、接口和表


| 模块 | 变化 |
|---|---|
| `contracts.py` | LegacyBidDocument/Section/Paragraph/Index |
| `artifact_registry.py` | 注册 LegacyBidIndex，升级版本 |
| `capability_registry.py` | 合法 producer |
| `gate_policy_registry.py` | Schema/source/entity gates |
| 新增 `legacy_bid_index.py` | deterministic compiler |
| 新增 `legacy_bid_inference.py` | 语义 candidate |
| `source_normalizer.py` | heading/tree recovery 边界测试 |
| `stage_runner.py` | bid_rewrite preprocessing stage |
| `v3_app.py` | 只读 legacy index/section/paragraph |
| projection renderer | Markdown |


文件清单是实施导航，不是让开发者不看代码就机械修改。实际文件有变化时，必须在 PR 描述中说明原因。

## 7. 详细实现顺序


### 7.1 输入约束

bid_rewrite 至少需要一份新招标书和一份旧投标书，允许多份旧投标书。
full_write 上传 legacy_bid 必须拒绝或要求切换模式，不能悄悄当 reference。

### 7.2 确定性结构树

根据 SourceBlock 顺序恢复：

```text
document
section
subsection
paragraph/list/table/image refs
```

章节 ID 来源于 input_id + heading block_id，不使用标题文本作为唯一 ID。

处理正常标题、无编号标题、同名标题、跳级标题、父标题前置正文、表格、图片说明、
OCR gap 和 orphan paragraphs。结构不确定时标记 needs_review，不伪造层级。

### 7.3 语义描述

模型每次只接收受限章节/段落快照，输出：

- description；
- answers；
- content_kind；
- reusable_scope；
- legacy_entity_risks；
- confidence；
- review_status。

所有引用必须是输入中的 section_id/block_id。

### 7.4 旧项目污染识别

至少识别：

- 项目名称；
- 采购人/建设单位；
- 地区；
- 年份和日期；
- 工期；
- 人员；
- 数量；
- 型号和版本；
- 特定标准版本；
- 原系统名称；
- 原合同承诺。

风险只是标签，不自动删除原文。

### 7.5 Artifact

`LegacyBidIndex` 依赖 exact `SourceIndex`。
必须经过 Proposal、Validation、InferenceReceipt、Gate、CAS Promotion、active revision 和 staleness。

Artifact Registry version 冻结测试同步更新，不能重演 PR-00 的版本漂移。

### 7.6 Markdown 投影

路径示例：

```text
workspace/v3/legacy_bids/<input_id>/sections/
```

每个 md 顶部带 section_id、source heading block_id、content block ids 和 source hash。
投影可删除重建，不参与权威判断。


## 8. 自动化测试


1. legacy role isolation；
2. heading tree；
3. same title；
4. jump level；
5. orphan paragraphs；
6. table/image refs；
7. stable IDs；
8. semantic refs only；
9. fabricated ref blocked；
10. entity risks；
11. Artifact promotion；
12. stale SourceIndex blocks promotion；
13. registry version；
14. Markdown rebuild；
15. legacy never enters ProjectModel/Writer；
16. multiple old bids。


## 9. 人工验收场景


- 创建 bid_rewrite Pilot。
- 上传新标书和一份多级旧投标书。
- 查看目录树和具体段落。
- 对照原 Word/PDF 随机抽查 20 个段落定位。
- 检查 Markdown 与 SourceBlock 内容一致。
- 替换旧投标书，确认旧 LegacyBidIndex stale。
- full_write 项目上传 legacy_bid 被拒绝。


## 10. 兼容性要求

1. 旧工作空间必须可以在本 PR 代码上直接打开。
2. 新字段必须有确定性默认值。
3. Feature Flag 关闭时现有行为保持不变。
4. 旧 API 客户端不能因为新增响应字段崩溃。
5. 新 API 在缺少能力时返回明确错误，不得静默降级。
6. 当前正式 Word、章节 H2、批量恢复和严格模板行为必须继续通过回归。

## 11. 失败处理

- Schema 或迁移失败：事务回滚，工作空间保持旧版本可读。
- CAS 冲突：返回 409 和实际 revision，不得覆盖。
- 模型输出非法：记录失败，不发布半成品。
- 搜索失败：按本 PR 明确策略处理，不得临场随意降级。
- 前端请求失败：保留本地未保存正文，展示可恢复错误。
- 服务中断：重启后从权威状态恢复，不能依赖浏览器内存。

## 12. 回滚方案


关闭 `BID_AGENT_BID_REWRITE_ENABLED` 和 legacy index stage。

Artifact 和 Markdown 保留只读。
full_write 完全不读取该 Artifact。


## 13. Definition of Done


- [ ] 旧标书结构可追溯；
- [ ] 段落 block_id 稳定；
- [ ] 语义描述有 receipt；
- [ ] 污染风险可见；
- [ ] Markdown 只是投影；
- [ ] ProjectModel 零污染；
- [ ] 全 CI 通过；
- [ ] PR-08 未开始。


并且必须满足总纲中的统一 Definition of Done。

## 14. 推荐提交拆分

建议本 PR 内部提交顺序：

1. `test:` 锁定本 PR 当前行为和目标契约；
2. `feat/refactor:` 实现后端契约与控制面；
3. `feat:` 接入服务和 API；
4. `feat:` 接入前端（如本 PR 包含）；
5. `test:` 增加集成与回归；
6. `docs:` 更新真实逻辑和回滚说明。

不得把所有修改压成一个无法审查的 “update all changes”。

## 15. 可直接复制给 Codex 的本 PR 提示词

```text
你正在 `breezePeak/bid_agent` 仓库中实施 **PR-07：旧投标书解析、LegacyBidIndex 与 Markdown 投影**。请直接检查代码、补测试并完成实现，不要只输出方案。

计划起始基线：`85702e3aa60bd5e2f7b26a130ef7a6048499e020`

开始前必须执行：
1. `git status --short`
2. `git rev-parse HEAD`
3. `git log -5 --oneline`
4. 如果 HEAD 已晚于计划基线，先审查新提交对本 PR 的影响。
5. 不得覆盖与本任务无关的用户修改。

建议分支：`agent/pr-07-legacybidindex-markdown`

开始条件：

- PR-06 已合并并全绿。
- full_write confirmed_plan_v2 单章 Pilot 稳定。
- legacy_inline 仍可回滚。


必须阅读：
- `docs/unified_writing_pr_plan_v2/00_START_HERE_当前只执行PR-00.md`
- `docs/unified_writing_pr_plan_v2/01_总纲_架构边界_依赖图_统一验收规则.md`
- `docs/unified_writing_pr_plan_v2/PR-07_旧投标书解析、LegacyBidIndex 与 Markdown 投影.md`
- 与本 PR 有关的 ADR、生产代码和现有测试

本 PR 唯一目标：

正式启用旧投标书输入，恢复完整目录树、章节边界和段落语义索引，形成可追溯的
`LegacyBidIndex` promoted Artifact 和只读 Markdown 投影。尚不生成融合目录或改写计划。


允许范围：

允许：

- bid_rewrite workspace 开放 legacy_bid 上传；
- deterministic structure recovery；
- semantic description inference；
- inference receipt；
- LegacyBidIndex Artifact；
- Registry/Gate/Promotion；
- Markdown projection；
- 旧实体风险识别；
- API 只读预览。

不接入目录或 Writer。


硬性禁止：

禁止：

- 旧标书进入 ProjectModel；
- 旧标书进入 RequirementLedger/ScoreModel；
- 自动复制正文；
- 新旧目录融合；
- 改写类型；
- 工作台显示旧段落来源；
- Writer 读取 LegacyBidIndex。


执行要求：
1. 先列出当前真实调用链、将修改的文件和风险点。
2. 先补能证明当前行为和目标行为的测试，再改实现。
3. 每完成一个小步骤立即运行对应局部测试。
4. Feature Flag 关闭时必须保持现有行为。
5. 旧测试与当前受支持行为冲突时，不得直接删除；必须给出证据并补替代测试。
6. 数据库迁移必须可重复、可升级旧工作空间、不可破坏原表。
7. 完成后运行全部门禁命令。
8. 最终提交本 PR 报告，然后停止，不得开始 PR-08。

必须运行：
python -m compileall -q src
ruff check src tests --quiet
python -m pytest tests -q --tb=line -k "not live_llm"
cd frontend
npm ci
npm test
npm run build

本 PR 特别要求：

- 重点验证旧标书绝不进入新项目事实。
- Artifact Registry/Gate/Capability 必须一起更新。
- Markdown 不得成为权威源。


最终报告必须包含：
- 修改文件清单；
- 数据库和 API 变化；
- 新增/修改测试清单；
- 每条命令的真实结果；
- 兼容性验证；
- 人工验收结果；
- 回滚方法；
- Definition of Done 逐项结果；
- 明确写出“未开始下一 PR”。
```
