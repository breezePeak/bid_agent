# 章节编写规划、搜索、改写类型与统一 Writer 链路

## 1. 核心目标

章节规划不是聊天里的一段提纲文字，而是用户可以检查、修改、确认，并由 Writer 精确执行的结构化合同。

统一链路：

```text
ChapterBlueprint
  ↓
稳定 content units
  ↓
收集候选来源
  ↓
按需搜索并获取原文
  ↓
生成 bindings
  ↓
计算 rewrite type
  ↓
用户确认
  ↓
冻结 WriterInputBundle
  ↓
统一 Writer
```

---

## 2. 规划输入

每次构建 plan 必须读取精确版本：

```text
workspace writing_mode/capability
ChapterBlueprint revision/hash
chapter workspace revision
chapter context revision/hash
global context revision/hash
RequirementLedger revision/hash
ScoreModel revision/hash
SourceIndex revision/hash
LegacyBidIndex revision/hash（改写模式）
EvidenceCatalog revision/hash
policy/prompt/model fingerprints
```

输入版本作为 dependency snapshot 保存。

---

## 3. 内容块生成

复用 `compile_chapter_writing_outline`，升级稳定 ID。每个 content unit 包含：

```text
本块标题
本块目的
must-answer
评分项
评分条件
招标要求
outcome kind
写作规则
顺序
```

规划 Agent 不能新增超出 Blueprint 的章节职责。若发现缺少内容块：

- 可以在当前章目的范围内提议新增；
- 超出范围则生成 `PlanIssueProposal`；
- 不直接改 Blueprint。

---

## 4. 全量编写来源收集

### 必选约束

- 招标要求；
- 评分义务；
- Blueprint purpose/objectives。

### 可用材料

- ProjectModel facts；
- GlobalProjectContext；
- ChapterContext；
- company/reference/guidance SourceBlocks；
- 已发布 Evidence；
- 用户备注。

### 搜索决策

对每个 content unit 判断：

```text
sufficient
need_project_material
need_web_research
blocked
```

搜索应服务于明确问题，不是为了让页面上显得资料很多。

---

## 5. 标书改写来源收集

在全量来源基础上增加：

```text
LegacyBidSourceProvider
```

流程：

1. 用 content unit 查询 LegacyBidIndex；
2. 获取 section/paragraph candidates；
3. 读取 candidate 原文；
4. 重排；
5. 扩展必要邻接段落；
6. 识别风险；
7. 生成候选 source card；
8. 规划 Agent 绑定到 content unit；
9. 用户确认或删除。

旧目录 lineage 可提高候选权重，但不能强制正文必须复用对应旧章节。

---

## 6. 搜索前移的详细逻辑

### 6.1 Gap 分析

每个 content unit 输出：

```json
{
  "unit_id": "...",
  "required_questions": [...],
  "covered_questions": [...],
  "missing_questions": [...],
  "research_needed": true,
  "reason": "...",
  "query_budget": 3
}
```

### 6.2 查询生成

查询必须包含：

- 业务主题；
- 新项目地域/年份（仅已确认事实）；
- 权威机构；
- 文件类型；
- 时间要求；
- 排除项。

### 6.3 搜索与抓取

```text
search provider
  ↓
候选 URL/title/snippet
  ↓
去重与域名策略
  ↓
抓取原文
  ↓
正文提取
  ↓
保存 immutable evidence snapshot
  ↓
内容摘要与用途建议
```

### 6.4 来源等级

```text
authoritative：招标文件、法律法规、政府/标准官方来源
project：用户确认项目资料
external_primary：原始发布机构、厂商官方技术文档
external_secondary：可靠二次资料
lead_only：只有摘要或无法获取原文
```

`lead_only` 不进入 Writer。

### 6.5 搜索失败

- 已获取来源保留；
- 失败 URL 可重试；
- 查询预算可由用户增加；
- 用户可以显式“仅使用现有资料”；
- blocking gap 不得自动放行。

---

## 7. 规划图数据

不保存坐标，只保存关系：

```text
sources
content_units
bindings
target chapter
```

前端根据节点 DOM 坐标绘线。

例：

```json
{
  "sources": [
    {
      "source_id": "legacy:block:abc",
      "source_type": "legacy_paragraph",
      "title": "旧标书 2.1.1 第3-8段",
      "description": "年度政策与任务变化",
      "fetch_status": "ready"
    },
    {
      "source_id": "evidence:2026-policy",
      "source_type": "web_evidence",
      "title": "2026年度官方政策文件",
      "description": "补充最新年度变化",
      "fetch_status": "ready"
    }
  ],
  "content_units": [
    {
      "unit_id": "cu:chapter-x:condition-y",
      "title": "本年度调查政策与任务变化"
    }
  ],
  "bindings": [
    {
      "source_id": "legacy:block:abc",
      "content_unit_id": "cu:chapter-x:condition-y",
      "usage_type": "base_content",
      "instruction": "保留主体，替换旧年度和项目名"
    },
    {
      "source_id": "evidence:2026-policy",
      "content_unit_id": "cu:chapter-x:condition-y",
      "usage_type": "supplement",
      "instruction": "补充最新政策"
    }
  ]
}
```

---

## 8. 用户可编辑范围

### 来源

- 添加已有项目资料；
- 添加旧段落；
- 删除候选来源；
- 要求继续搜索；
- 查看原文；
- 设置来源用途；
- 标记禁止使用。

### 内容块

- 调整顺序；
- 修改面向用户的标题；
- 修改 must-answer 表达；
- 在 Blueprint 范围内增加/拆分/合并；
- 删除无依据的 Planner 建议；
- 不能改评分主责 ID。

### Binding

- 改 `usage_type`；
- 改 `instruction`；
- 选整段或部分段落；
- 设置旧实体处理；
- 调整来源优先级。

每次编辑生成新 plan revision，不原地覆盖已确认历史。

---

## 9. Rewrite type 计算器

新增纯确定性：

```python
class ChapterRewriteClassifier:
    def classify(plan) -> RewriteType
```

### copy

```text
legacy body sources = 1 logical complete section
usage_scope = full
all content units covered by same section
no substantive external supplement
structure unchanged
only safe replacements
```

### light_edit

```text
one dominant legacy section
legacy base coverage above threshold
limited supplement/replacement
content unit structure substantially preserved
```

### restructure

```text
multiple legacy sections
or one section split/reordered
or multiple base sources
or major content-unit reorganization
```

### new_write

```text
no legacy body source with base_content
```

`structure_reference` 不算 legacy body source。

分类输出理由：

```json
{
  "type": "restructure",
  "reason_codes": [
    "MULTIPLE_LEGACY_BASE_SECTIONS",
    "CONTENT_UNIT_REORDERED"
  ]
}
```

Gate 重算，前端不能自己决定。

---

## 10. 规划确认

确认按钮请求必须带：

```text
expected_chapter_revision
plan_revision
plan_hash
dependency_fingerprint
idempotency_key
```

服务端流程：

1. 读取当前 plan；
2. exact hash；
3. 重跑 validation/gate；
4. 重算 rewrite type；
5. 验证 source ready；
6. 验证 dependencies；
7. 生成 approval receipt；
8. 更新 confirmed pointer；
9. 写事件；
10. 返回 write readiness。

确认后不自动写，除非独立配置明确允许当前章节自动开始。

---

## 11. Writer Bundle 冻结

### 11.1 精确来源

Bundle 中每个来源保存：

```text
source_id
canonical ref
content snapshot
content hash
display metadata
binding instruction
target content unit
risk/exclusion rules
```

### 11.2 顺序

Writer prompt 按 content unit 顺序组织：

```text
Unit 1
  constraints
  selected sources
  bindings
  forbidden carryovers

Unit 2
  ...
```

不把所有来源混成一个大 evidence pool。

### 11.3 旧段落

只传 selected block 原文和必要邻接上下文。不要传整本旧标书。

### 11.4 Web

只传抓取后的 snapshot 片段，不传搜索 snippet 作为事实。

### 11.5 排除项

明确放入：

```text
不得出现的旧项目名
不得使用的旧参数
用户删除的来源 ID
过期标准
不允许新增的事实
```

---

## 12. Writer Prompt 规则

统一 Writer 需要增加：

1. 按 content unit 顺序写；
2. 每块只使用绑定来源；
3. 不得引用未提供来源；
4. 不得把 source description 当原文事实；
5. `copy` 尽量保持原文内容结构，但执行污染规则；
6. `light_edit` 保留主体，严格执行替换和补充；
7. `restructure` 重组而非简单拼接；
8. `new_write` 不偷用旧标书；
9. 缺资料返回 gap，不编造；
10. 不输出内部 ID、评分术语、改写标签和来源卡说明。

---

## 13. 写作后 Gate

### 13.1 规划覆盖

- 每个 required content unit 有输出；
- 不多写越界主题；
- outcome kind 合规。

### 13.2 来源约束

- 引用/事实可归因于 allowed sources；
- 未选来源不应泄漏；
- 删除来源特征文本检查。

### 13.3 旧污染

- 旧项目实体；
- 旧年份；
- 旧地点；
- 旧人员；
- 旧平台/标准冲突；
- 旧甲方/旧投标人。

### 13.4 类型一致性

- `copy` 不应大幅扩写；
- `new_write` 不应出现长段旧文复刻；
- `restructure` 应去重；
- `light_edit` 应完成指定替换。

Gate 不通过：

```text
blocked draft preview
or no content revision
```

具体沿用当前 content gate 事务语义，不能让半成功状态含糊。

---

## 14. 重新生成

### 同一规划重写

允许：

```text
same confirmed plan revision
new writer operation
new content revision
```

### 修改规划后重写

流程：

```text
edit plan
→ new plan revision
→ confirm
→ old draft stale
→ write new draft
```

### 局部重写

局部重写也必须：

- 使用同一 plan；
- 只处理目标 content unit/block；
- 不搜索；
- 不跨章节；
- 生成新的 content revision；
- 保持未修改内容。

---

## 15. 批量写作

批量写作不是自动规划整本。用户可先确认多个章节规划，再选择：

```text
批量编写已确认章节
```

队列每项保存 plan exact refs。  
未确认/陈旧章节显示原因，不自动帮用户确认。

---

## 16. Plan staleness 与正文

### plan stale

- 禁止开始写；
- 已有正文保留；
- UI 显示上游变化；
- 可查看新旧差异；
- 重建计划。

### draft stale

- 不删除；
- 不能确认成正式版本；
- 可复制人工编辑到新草稿，需明确操作；
- 新 plan 生成后可重写。

---

## 17. 质量指标

| 指标 | 目标 |
|---|---:|
| Writer 收到未确认来源 | 0 |
| 确认后新增 Web 来源 | 0 |
| 旧段落 source ID 可追溯率 | 100% |
| plan binding 两端合法率 | 100% |
| rewrite type Gate 重算一致率 | 100% |
| 新项目旧实体 blocking 漏检 | 黄金样本控制 |
| 删除来源仍进入 Bundle | 0 |
| 跨章节 source/context 泄漏 | 0 |
