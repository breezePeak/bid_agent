# PR-05：单章改写执行，复用现有统一 Writer

## 1. 唯一目标

用户确认当前章节改写方案后，点击“开始改写”，系统按方案生成正文、流式显示、写入现有章节草稿，并继续使用当前正文编辑、确认和导出能力。

## 2. 禁止项

禁止新增：

```text
RewriteWriter
LegacyWriter
第二套章节写作 API
第二套草稿表
第二套正文编辑器
```

## 3. 执行服务

新增：

```text
BidRewriteExecutionService
bid_rewrite.chapter.execute
```

职责：

1. 校验 workspace 为 `bid_rewrite`；
2. 重载当前已确认 plan；
3. 校验 plan hash、依赖和章节 revision；
4. 加载 exact LegacyBid block snapshots；
5. 将改写方案装配为现有 `ChapterWritingRequest`；
6. 调用现有 `ChapterWritingService`；
7. 返回现有 draft revision。

## 4. 不新增 WriterInputBundle 真相

利用现有：

```text
ChapterWritingRequest.chapter_writing_plan
WriterInputBundle.chapter_writing_plan
```

增加受控 rewrite 字段：

```json
{
  "rewrite_schema": "v1",
  "rewrite_strategy": "light_edit",
  "selected_legacy_sources": [],
  "new_content_items": [],
  "selected_evidence_refs": [],
  "replacement_map": [],
  "pollution_receipt": {}
}
```

`ChapterWritingKernel` 仅在该字段存在时把 rewrite context 放入 prompt。普通全量编写的 payload 和 prompt 不增加空字段。

## 5. 四种执行方式

### copy

```text
exact selected blocks
→ confirmed replacement map
→ 格式标准化
→ LegacyPollutionGate
→ WriterBundleContentGate
→ chapter.generate_draft
```

直接复用不调用模型改写，但仍通过同一个 `ChapterWritingService` 编排和现有草稿提交。

### light_edit

现有 ContentWriter 读取：

- 旧主体；
- 替换映射；
- 新要求缺口；
- 项目事实；
- 已选证据。

### restructure

现有 ContentWriter 按新 WritingPlan blocks 重排多个旧来源。

### new_write

现有 ContentWriter 按当前全量章节写作逻辑编写，但只使用已确认的当前章来源。

## 6. 查询规则

改写方案确认后：

```text
run_research = False
```

正文阶段不再添加新来源。

缺少必要资料时，在 PR-04 阶段解决；否则执行返回明确的 `REWRITE_PLAN_INCOMPLETE`。

原 full_write 继续保持当前 `run_research=True` 行为。

## 7. 前端

确认方案后显示：

```text
开始改写
```

执行流程：

```text
点击
→ 流式进度
→ 正文 delta
→ 完成后自动切换“正文”
→ 现有 ContentBlockEditor
→ 现有版本/H2/Word
```

未确认、stale、污染阻断时按钮不可用并显示具体原因。

## 8. 对现有 ChapterWritingService 的修改约束

只允许增加条件分支：

```text
存在 confirmed rewrite context
→ rewrite path

不存在 rewrite context
→ 原代码路径
```

必须用回归测试证明：

- full_write assembler 调用次数不变；
- research coordinator 调用不变；
- ContentWriter 调用不变；
- draft commit payload 不变；
- batch full_write 不变。

## 9. 自动化测试

- missing/unconfirmed/stale plan；
- wrong plan hash；
- stale legacy block hash；
- copy 不调用 ContentWriter；
- copy 仍通过 Gate/commit；
- light_edit 调用原 ContentWriter；
- restructure 多来源；
- new_write；
- 执行阶段不调用 research；
- unresolved pollution 阻断；
- output provenance；
- stream 顺序；
- 章节 CAS；
- H2；
- Word；
- full_write 单章、聊天、批量全回归。

## 10. 人工验收

分别选择四章：

1. 直接复用；
2. 修改复用；
3. 重组复用；
4. 重新编写。

逐章确认：

- 正文符合新章节目的；
- 没有旧采购人、旧项目名、旧年份；
- 旧内容复用比例符合方案；
- 生成后自动切正文；
- 编辑、确认、导出正常；
- 普通全量编写仍能直接一键编写和批量编写。

## 11. Definition of Done

- [ ] 四种单章策略可执行；
- [ ] 没有第二套 Writer；
- [ ] 确认后正文阶段不再查询；
- [ ] 污染不进入草稿；
- [ ] full_write 调用链不变；
- [ ] CI 全绿；
- [ ] 未开始 PR-06。
