# 标书 Agent 统一写作与标书改写：按 PR 开发计划合订版

- 基线：`85702e3aa60bd5e2f7b26a130ef7a6048499e020`
- 版本：`PR Plan V2.0`
- 使用规则：当前只执行 PR-00；每次只执行一个 PR。



---

<!-- FILE: 00_START_HERE_当前只执行PR-00.md -->

# 统一章节编写规划与标书改写开发计划：START HERE

- 计划版本：`PR Plan V2.0`
- 仓库：`breezePeak/bid_agent`
- 当前基线分支：`main`
- 当前基线提交：`85702e3aa60bd5e2f7b26a130ef7a6048499e020`
- 旧计划基线：`74cd1ff12a79a373f9c262d48f61e03caa3cd642`，已失效
- 生成日期：`2026-08-21`
- 当前允许执行的开发任务：**仅 PR-00**
- 当前禁止执行：PR-01 及之后的任何新功能

## 1. 为什么必须重做计划

旧开发计划基于提交：

```text
74cd1ff12a79a373f9c262d48f61e03caa3cd642
```

随后仓库合并了 PR #10，并把 `main` 推进到：

```text
85702e3aa60bd5e2f7b26a130ef7a6048499e020
```

这次合并重写了章节对话、内部 WritingPlan、章节搜索、Tavily 适配、Writer 研究编排、
前端工作台和大量测试。旧计划中关于以下内容的判断已经过期：

- `ChapterChatService` 的提纲审核方式；
- 计划是否确认后才能写正文；
- `ChapterWritingPlan` 当前是否存在；
- 搜索发生在什么阶段；
- 章节 Writer 如何收到计划；
- 批量写作如何调用统一 Writer；
- 现有测试基线是否健康。

更关键的是，PR #10 的 GitHub Actions 中：

```text
static   通过
frontend 通过
unit     失败
```

单元测试结果为：

```text
29 failed
492 passed
4 skipped
44 subtests passed
```

因此，本计划把 **PR-00：修复当前主分支并冻结可信基线** 设为绝对前置条件。
PR-00 未完成前，任何功能开发都会把已有回归和新功能缺陷混在一起，随后开发者开始
围着日志猜是旧错还是新错，软件工程再次退化成民俗活动。

## 2. 现在怎么给 Codex

现在只给 Codex 一个文件：

```text
CODEX_PR-00_启动提示词.md
```

不要同时把 PR-01 至 PR-12 发给它，也不要说“顺便把后续都做了”。

Codex 完成 PR-00 后，必须满足：

1. 当前提交基于 `85702e3aa60bd5e2f7b26a130ef7a6048499e020`；
2. 所有确定性 Python 测试通过；
3. 前端测试和构建通过；
4. 当前全量编写主流程完成回归；
5. 当前批量编写、章节确认、Word 导出完成回归；
6. 没有实现 `writing_mode`、`LEGACY_BID`、正式章节规划确认或标书改写；
7. PR-00 已合并到 `main`；
8. 合并后的 `main` GitHub Actions 全绿。

只有以上八项全部成立，才打开：

```text
PR-01_项目写作模式与输入角色脚手架.md
```

并复制其中最后一节的 Codex 提示词。

## 3. 开发计划的使用方式

本计划严格按 PR 排列：

```text
PR-00 当前主分支修复与可信基线
  ↓
PR-01 写作模式、能力开关和输入角色脚手架
  ↓
PR-02 章节编写规划控制面、版本、哈希和确认回执
  ↓
PR-03 全量编写规划与搜索前移的影子运行
  ↓
PR-04 工作台“编写逻辑 / 正文”双页签，只读展示
  ↓
PR-05 规划编辑、来源选择、搜索补充、确认和失效
  ↓
PR-06 Writer 精确消费已确认规划，全量编写链路切换
  ↓
PR-07 旧投标书输入、结构恢复、LegacyBidIndex 和 Markdown 投影
  ↓
PR-08 新招标要求与旧投标书目录融合
  ↓
PR-09 旧段落匹配、四类改写、污染检测
  ↓
PR-10 标书改写完整工作台闭环
  ↓
PR-11 批量编写、陈旧传播、恢复和导出隔离
  ↓
PR-12 真实项目验收、迁移演练、灰度和正式切换
```

每一份 PR 文档都包含：

- 本 PR 的唯一目标；
- 开始条件；
- 当前代码事实；
- 允许修改与禁止修改；
- 精确文件、类、接口和数据库变化；
- 实现顺序；
- 失败处理；
- 自动化测试；
- 人工验收；
- 回滚方案；
- Definition of Done；
- 可直接复制给 Codex 的提示词。

## 4. 全局硬规则

### 4.1 每个 PR 合并后必须能正常运行

禁止出现：

```text
这个 PR 先加字段，下个 PR 再补读取
这个 PR 先改前端，下个 PR 再补接口
这个 PR 先切新链路，下个 PR 再补回滚
```

每个 PR 必须满足：

```text
数据库可升级
旧工作空间可打开
现有功能可使用
新代码默认关闭或完整可用
完整 CI 通过
可单独回滚
```

### 4.2 当前行为先锁定，再有意修改

当前代码已经形成一套新的内部 WritingPlan 行为：

- WritingPlan 保存在 `_writing_plans.json`；
- 用户可以直接要求写正文；
- 当前正文写作不等待 WritingPlan 确认；
- 搜索仍在 `ChapterWritingService` 中执行；
- `WriterInputBundle` 已携带 `chapter_writing_plan`；
- `ChapterWritingService → ContentWriter` 仍是统一正文链路。

PR-00 必须先证明这套当前行为本身完整、可测试、可运行。
后续 PR 对行为的改变必须：

1. 有明确产品目标；
2. 有 Feature Flag 或工作空间能力版本隔离；
3. 有旧行为回归测试；
4. 有新行为测试；
5. 有迁移与回滚路径；
6. 不得把“改了测试期望”当成实现完成。

### 4.3 不新增第二套 Writer

最终仍然只有：

```text
ChapterWritingService
    → WriterInputBundle
    → ContentWriter
    → Content Gate
    → ChapterEditingService
```

标书改写只能改变规划素材和执行策略，不能出现：

```text
RewriteWriter
LegacyWriter
FullWriter
CopyWriter
RewriteWorkbench
```

### 4.4 权威状态只能进入 control.db 或已晋级 Artifact

- Agent 只能生成候选计划、Proposal、Finding 或建议。
- 章节规划的正式版本和确认回执进入 `control.db`。
- `_writing_plans.json` 后续只能是兼容投影，不能继续作为权威源。
- 旧投标书原始段落以 promoted `SourceIndex.SourceBlock` 为权威。
- `LegacyBidIndex` 必须经过 Registry、Validation、Gate 和 Promotion。
- 前端规划图只是数据投影，不能另存一份“图数据真相”。

### 4.5 搜索结果必须先展示、后写作

新链路中：

```text
生成规划
→ 判断资料缺口
→ 执行搜索
→ 保存原文证据
→ 显示来源和用途
→ 用户确认规划
→ Writer 仅消费确认内容
```

Writer 生成正文时不得继续联网，也不得把用户删除的来源重新塞回 Bundle。

## 5. 全局进入下一 PR 的门禁

每个 PR 合并前必须运行：

```bash
python -m compileall -q src
ruff check src tests --quiet
python -m pytest tests -q --tb=line -k "not live_llm"

cd frontend
npm ci
npm test
npm run build
```

除此之外，每个 PR 文档还列出了本阶段专项测试。

任何一条失败：

```text
不得合并
不得开始下一 PR
不得用“与本次修改无关”略过
不得删除测试让 CI 变绿
```

如果确认是旧测试过期，必须同时提交：

- 过期证据；
- 当前受支持行为说明；
- 替代测试；
- 变更记录。

## 6. 用户最终看到的目标流程

### 全量编写

```text
创建全量编写项目
→ 上传新招标书、评分文件、项目资料
→ 生成并确认目录
→ 进入章节工作台
→ 点开一个叶子章节
→ 中间默认显示“编写逻辑”
→ 展示招标约束、项目资料、搜索来源及用途
→ 用户调整并确认
→ 点击“开始编写”
→ 统一 Writer 生成当前章节
→ 切换到“正文”
→ 编辑并确认章节
```

### 标书改写

```text
创建标书改写项目
→ 上传新招标书和旧投标书
→ 解析旧投标书完整目录树和段落
→ 根据新要求并参考旧目录生成新目录
→ 用户修改并确认目录
→ 进入章节工作台
→ 点开一个叶子章节
→ “编写逻辑”展示旧投标书具体段落、补充资料和搜索来源
→ 用户调整并确认
→ 点击“开始编写”
→ 统一 Writer 按全部搬用、简单修改、理解重组或重新编写执行
→ 切换到“正文”
→ 编辑并确认章节
```

## 7. 当前唯一行动

```text
打开 CODEX_PR-00_启动提示词.md
完整复制给 Codex
完成 PR-00
合并并确认 main CI 全绿
然后停止
```


---

<!-- FILE: 01_总纲_架构边界_依赖图_统一验收规则.md -->

# 总纲：架构边界、PR 依赖图与统一验收规则

- 计划版本：`PR Plan V2.0`
- 基线：`85702e3aa60bd5e2f7b26a130ef7a6048499e020`
- 本文用途：所有 PR 共同遵守的架构与验收契约
- 本文不能代替单个 PR 文档

## 1. 本次开发的唯一产品目标

在不破坏现有 V3 功能的前提下，将“全量编写”和“标书改写”统一为：

```text
ChapterBlueprint
→ ChapterWritingPlan
→ 资料补充与来源绑定
→ 用户确认精确规划版本
→ WriterInputBundle
→ ChapterWritingService
→ ContentWriter
→ 正文编辑
→ H2 章节确认
```

两种项目模式的区别只在规划阶段：

| 项目 | 全量编写 | 标书改写 |
|---|---|---|
| 新招标要求 | 使用 | 使用 |
| 评分要求 | 使用 | 使用 |
| 全局项目事实 | 使用 | 使用 |
| 章节上下文 | 使用 | 使用 |
| 用户上传资料 | 使用 | 使用 |
| 公开搜索资料 | 按需 | 按需 |
| 旧投标书目录 | 不使用 | 生成目录时参考 |
| 旧投标书段落 | 不使用 | 章节规划时选择 |
| Writer | 同一套 | 同一套 |
| 工作台 | 同一套 | 同一套 |

## 2. 权威对象边界

### 2.1 ChapterBlueprint

继续负责：

- 正式章节树；
- 章节标题；
- 父子关系；
- 章节目的；
- 写作目标；
- 招标要求归属；
- 评分点和评分条件归属；
- 可写与结构节点边界。

它不负责：

- 当前章节具体使用哪些资料；
- 当前章节是否需要搜索；
- 旧投标书具体采用哪些段落；
- 当前章节的改写类型；
- 用户最终确认了哪份资料规划。

### 2.2 ChapterWritingPlanRevisionRecord

新增的章节级追加版本，负责：

- 当前章节计划写哪些内容块；
- 每个内容块要回答什么；
- 每个来源支持哪个内容块；
- 来源使用方式；
- 是否需要搜索；
- 公开来源证据；
- 旧标书段落引用；
- 动态计算的改写类型；
- 依赖快照和 fingerprint；
- 规划状态。

它不能：

- 修改 Blueprint；
- 创建新章节；
- 把旧投标书写入 ProjectModel；
- 直接写正文；
- 直接切换正式章节指针。

### 2.3 ChapterPlanApprovalReceipt

章节规划的本地执行授权，精确绑定：

```text
chapter_id
plan_revision
plan_hash
dependency_fingerprint
blueprint revision/hash
global context revision/hash
chapter context revision/hash
SourceIndex revision/hash
LegacyBidIndex revision/hash（改写模式）
selected SourceBlock IDs/content_hash
selected Evidence IDs/snapshot_hash
principal_id
issued_at
```

它不是第二个全局 H1，也不重新确认整份 ChapterBlueprint。

需要新增 ADR 明确：

- H1 仍是全局文档规划唯一晋级 Gate；
- ChapterPlanApprovalReceipt 是章节级执行授权；
- 它只允许 Writer 消费一份冻结规划；
- 它不能修改 canonical planning Artifact。

### 2.4 WriterInputBundle

Writer 唯一输入，新增后必须包含：

- `chapter_plan_revision`
- `chapter_plan_hash`
- `chapter_plan_approval_receipt_id`
- `chapter_plan_snapshot`
- `selected_source_blocks`
- `selected_source_bindings`
- `selected_evidence_items`
- `rewrite_type`

Writer 不能自行从工作空间查找更多旧段落或公开资料。

### 2.5 LegacyBidIndex

旧投标书的语义索引 Artifact，依赖 promoted `SourceIndex`。

它包含：

- 旧投标书目录树；
- 章节和段落描述；
- 内容类型；
- 回答的问题；
- 可复用范围；
- 旧项目实体风险；
- 原始 SourceBlock 引用。

旧投标书正文的权威内容仍然是 `SourceIndex.SourceBlock`。
Markdown 只是投影。

## 3. 写作模式与流程版本分离

建议字段：

```text
writing_mode:
  full_write
  bid_rewrite

chapter_plan_flow:
  legacy_inline
  confirmed_plan_v2
```

含义：

- 旧工作空间：`full_write + legacy_inline`
- 新功能影子阶段：`full_write + legacy_inline`，后台生成 v2 plan 对比
- 全量编写新链路：`full_write + confirmed_plan_v2`
- 标书改写：`bid_rewrite + confirmed_plan_v2`

`DocumentMode` 继续只表示：

```text
template_strict
auto_outline
```

四个维度不能混在一起，否则枚举值早晚长成
`bid_rewrite_template_strict_confirmed_plan_v2`，然后所有人开始怀念纸和笔。

## 4. Feature Flag

建议统一：

```text
BID_AGENT_CHAPTER_PLAN_V2_ENABLED=0
BID_AGENT_CHAPTER_PLAN_V2_DEFAULT=0
BID_AGENT_BID_REWRITE_ENABLED=0
BID_AGENT_LEGACY_OUTLINE_FUSION_ENABLED=0
```

规则：

1. Flag 关闭时，旧工作空间行为不变。
2. Flag 开启但 workspace flow 为 `legacy_inline` 时，只允许影子运行。
3. 只有 workspace flow 为 `confirmed_plan_v2` 时，正文写作要求规划确认。
4. Flag 关闭不得删除新表或降级数据。
5. 回滚优先通过能力开关和 workspace flow，不做逆向删表。

## 5. PR 依赖关系

```text
PR-00 → PR-01 → PR-02 → PR-03 → PR-04 → PR-05 → PR-06
                                                   ↓
PR-12 ← PR-11 ← PR-10 ← PR-09 ← PR-08 ← PR-07
```

禁止跳过 PR-00、PR-02、PR-05 或 PR-06。

## 6. 每个 PR 的标准开发顺序

```text
1. 确认 HEAD 和工作区状态
2. 读取本 PR 文档和相关 ADR
3. 读取当前生产代码与现有测试
4. 写出本 PR 真实调用链
5. 补充或调整特征测试
6. 先实现后端契约和控制面
7. 再接服务编排
8. 最后接 API 和前端
9. 运行局部测试
10. 运行全量测试
11. 执行人工场景
12. 输出门禁报告
13. 停止，不开始下一 PR
```

## 7. 测试分层

### 7.1 单元测试

覆盖 Schema、哈希、策略计算、fingerprint、状态转换、来源绑定、污染检测和目录融合。

### 7.2 控制面测试

覆盖 append-only、CAS、幂等、exact receipt binding、stale、重启、SQLite 锁和旧 Schema 升级。

### 7.3 契约测试

覆盖 API、snapshot、NDJSON、前端 parser、未知字段 fail closed 和旧客户端兼容。

### 7.4 集成测试

至少覆盖：

```text
创建工作空间
→ 上传
→ 解析
→ 目录
→ H1
→ 章节规划
→ 规划确认
→ 正文
→ 编辑
→ H2
→ Word 导出
```

### 7.5 端到端真实场景

最终至少两套：

1. 全量编写真实项目；
2. 新招标书 + 旧投标书改写项目。

## 8. 现有逻辑保护矩阵

所有 PR 都必须确认以下功能未被破坏：

- 登录与 CSRF；
- 新建、列出、删除工作空间；
- 文件上传和角色校验；
- RequirementLedger；
- ScoreModel；
- ProjectModel；
- ChapterBlueprint；
- H1；
- 章节工作空间物化；
- 章节上下文；
- 右侧章节聊天；
- 当前直接写作兼容路径；
- 章节编辑和锁定；
- H2；
- 批量任务恢复；
- 当前 Word 导出；
- 正式文档组装；
- 严格模板模式；
- 无模板模式；
- Tavily Provider 配置；
- 确定性测试不联网。

## 9. 统一 Definition of Done

单个 PR 完成必须同时满足：

- [ ] 只实现本 PR 范围；
- [ ] 所有新 Schema 为 `extra=forbid`；
- [ ] 写路径经过 CommandGateway 或受控 Service；
- [ ] 没有 Agent 直接写权威状态；
- [ ] 没有第二套 Writer；
- [ ] 旧工作空间可打开；
- [ ] Feature Flag 关闭时行为不变；
- [ ] 数据库迁移可重复执行；
- [ ] 所有确定性测试通过；
- [ ] 前端测试和构建通过；
- [ ] 本 PR 专项场景通过；
- [ ] 记录迁移和回滚方法；
- [ ] 更新真实逻辑文档；
- [ ] 合并后的 main CI 全绿；
- [ ] 未开始下一 PR。


---

<!-- FILE: 02_PR索引_每次只打开一份.md -->

# PR 索引：每次只打开一份

| 顺序 | 文件 | 何时打开 |
|---:|---|---|
| 0 | `PR-00_修复当前主分支并冻结可信基线.md` | 现在 |
| 1 | `PR-01_项目写作模式与输入角色脚手架.md` | PR-00 合并且 main 全绿 |
| 2 | `PR-02_章节编写规划控制面与版本确认内核.md` | PR-01 合并且全绿 |
| 3 | `PR-03_全量编写规划与搜索前移_影子运行.md` | PR-02 合并且全绿 |
| 4 | `PR-04_工作台编写逻辑与正文双页签_只读展示.md` | PR-03 合并且全绿 |
| 5 | `PR-05_规划编辑_来源选择_确认与失效机制.md` | PR-04 合并且全绿 |
| 6 | `PR-06_Writer精确消费已确认规划_全量链路切换.md` | PR-05 合并且全绿 |
| 7 | `PR-07_旧投标书解析_LegacyBidIndex与Markdown投影.md` | PR-06 合并且全绿 |
| 8 | `PR-08_新旧目录融合与目录来源追踪.md` | PR-07 合并且全绿 |
| 9 | `PR-09_旧段落匹配_改写类型_污染检测.md` | PR-08 合并且全绿 |
| 10 | `PR-10_标书改写工作台完整闭环.md` | PR-09 合并且全绿 |
| 11 | `PR-11_批量编写_陈旧传播_恢复与导出隔离.md` | PR-10 合并且全绿 |
| 12 | `PR-12_真实项目验收_迁移_灰度与正式切换.md` | PR-11 合并且全绿 |

使用规则：

```text
只把当前 PR 文档最后的 Codex 提示词发给 Codex。
Codex 完成并停止。
人工审查。
合并。
确认 main CI 全绿。
再打开下一份。
```

不要一次把 13 个 PR 都交给同一个 Codex 会话。
长会话会让模型把“后续目标”误当成“本轮顺手实现”，随后范围像发面一样膨胀。


---

<!-- FILE: PR-00_修复当前主分支并冻结可信基线.md -->

# PR-00：修复当前主分支并冻结可信基线


- 计划版本：`PR Plan V2.0`
- 仓库：`breezePeak/bid_agent`
- 起始基线：`85702e3aa60bd5e2f7b26a130ef7a6048499e020`
- 开发原则：本 PR 独立可部署、可测试、可回滚


## 1. 开始条件


- 当前 `main` 必须是 `85702e3aa60bd5e2f7b26a130ef7a6048499e020`，或只包含经过审查、与本 PR 无冲突的后续提交。
- 当前工作区不得有未识别的用户修改。
- 不要求当前测试通过，因为本 PR 的任务就是修复当前红色基线。
- 必须能够访问 PR #10 的 CI 失败记录或在本地完整复现。


不满足开始条件时，本 PR 立即停止。不得以“先做一部分不影响”为理由继续。

## 2. 本 PR 的唯一目标


把当前主分支恢复到“确定性 CI 全绿、现有业务主流程可复现”的可信状态，并建立后续
功能开发不能绕过的基线验证脚本与证据。**本 PR 不实现任何标书改写或正式规划确认新功能。**


## 3. 当前代码事实


最新合并提交为 `85702e3aa60bd5e2f7b26a130ef7a6048499e020`。PR #10 改动 67 个文件，当前代码已形成新的内部
WritingPlan 和搜索链路：

- `chapter_chat.py` 使用 `_writing_plans.json` 保存内部计划；
- 用户直接要求写正文时，不等待计划确认；
- `ChapterWritingRequest` 已包含 `chapter_writing_plan`；
- `ChapterWritingService` 仍在写作过程中执行搜索；
- `WriterInputBundle` 已携带计划，但没有 plan revision/hash/receipt；
- `ChapterBatchService` 直接调用 `ChapterWritingService`；
- 中间区域仍只有正文编辑器。

PR #10 的 CI Run `32453280179` 中 static 和 frontend 成功，unit 失败。已知失败共 29 项，
分为以下九类：

1. **确定性研究测试意外进入 Tavily/真实 Provider**
   - autonomous research published evidence；
   - research tool published 状态。
2. **批量写作测试注入接口漂移**
   - `chapter_batch.CommandGateway` patch 入口消失。
3. **章节确认开关/辅助方法回归**
   - `_confirmation_required` 不存在，四个内容阶段测试失败。
4. **Grounding 错误分类漂移**
   - 预期 `PROJECT_SPECIFICITY_MISSING`，实际返回 review unavailable。
5. **前端 shell 与 Python 测试隔离问题**
   - 未构建 frontend 时只返回“请先构建”，测试期望 `id="app"`。
6. **阶段名、生成进度和兼容映射回归**
   - `execute_content_plan` 被报告 unknown；
   - generation stage 缺失、StopIteration。
7. **规划、目录校验和版本冻结回归**
   - controlled repair 调用次数；
   - Artifact Registry 版本期望；
   - outline path、质量标题、模板和来源错误分级。
8. **ProjectModel/Requirement 输入结构漂移**
   - `KeyError: requirements`；
   - strict template H1 缺 ProjectModel。
9. **写作方向角色识别回归**
   - diagram 章节预期 visual，实际 general。

这些失败不能一律“改测试”。每一项必须先判断：

```text
A. 生产代码回归
B. 测试隔离缺陷
C. 经过接受决策后的旧测试过期
```

C 类测试只有在提交替代测试和决策证据后才能修改。


## 4. 本 PR 允许修改的范围


允许：

- 修复当前生产代码回归；
- 恢复或重建必要的依赖注入测试缝隙；
- 修复确定性测试的 Provider 隔离；
- 修复阶段兼容映射；
- 修复确认开关；
- 修复测试 fixture；
- 更新确实过期的冻结版本测试，并记录理由；
- 新增基线验证脚本和基线报告；
- 更新 `docs/current_logic_flow_v3.md` 中已经与代码不符的事实。

所有修改只能让当前已存在的功能恢复正确。


## 5. 本 PR 明确不做


禁止：

- 新增 `writing_mode`；
- 新增 `LEGACY_BID`；
- 新增 plan control tables；
- 新增章节计划确认回执；
- 修改工作台为双页签；
- 把搜索前移；
- 修改目录生成以读取旧投标书；
- 新增标书改写入口；
- 修改当前产品行为以迎合后续计划；
- 删除失败测试而不提供替代测试；
- 通过跳过、xfail、放宽断言把 CI 变绿。


## 6. 预计文件、类、接口和表


| 范围 | 可能涉及 |
|---|---|
| CI 基线 | `.github/workflows/ci.yml`，仅在确有隔离缺陷时修改 |
| 研究隔离 | `autonomous_research.py`、`research_service.py`、`research_tool.py`、相关测试 |
| 批量写作 | `chapter_batch.py`、`test_v3_chapter_batch.py` |
| 章节确认 | `chapter_editing.py`、flow settings、内容阶段测试 |
| 阶段兼容 | `stage_runner.py`、`workspace_snapshot.py`、`pipeline_registry.py` |
| 目录规划 | `planning_inference.py`、`planning_agent.py`、`scoring_outline_policy.py` |
| H1/模板 | `artifact_promotion.py`、strict template 测试 |
| 前端 shell | `v3_app.py`、测试 fixture，不改真实构建产物 |
| 基线工具 | 新增 `scripts/verify_pr00_baseline.py` |
| 证据 | 新增 `docs/unified_writing_pr_plan_v2/evidence/PR-00-baseline.md` |


文件清单是实施导航，不是让开发者不看代码就机械修改。实际文件有变化时，必须在 PR 描述中说明原因。

## 7. 详细实现顺序


### 7.1 冻结复现环境

1. 记录 Python、Node、依赖版本。
2. 执行完整 CI 命令。
3. 保存失败列表和顺序。
4. 对比 GitHub Actions 失败，确认本地没有少跑。
5. 禁止在开发机已有 `frontend/dist` 的情况下掩盖 shell 测试问题。

### 7.2 建立失败分类表

为每个失败记录：

```text
test_id
failure_category
current_behavior
expected_behavior
source_of_truth
decision: fix_code | fix_test_isolation | replace_stale_test
replacement_test
```

分类表写入 PR 证据文档。

### 7.3 先修测试隔离

优先处理：

- 确定性测试不能访问 Tavily；
- Provider 必须通过注入或 mock；
- Python 单测不得依赖前端已经构建；
- 时间、随机 ID 和环境变量必须冻结；
- 测试不能共享全局 Provider 状态。

完成后先跑相关测试文件，再跑全量。

### 7.4 恢复章节确认语义

结合 flow settings 和现有产品要求，确认 `confirmation_required` 的来源。
如果设置开关仍属于当前受支持功能：

- 恢复统一 helper；
- AI draft、用户编辑、恢复 revision、H2 全部使用同一冻结策略；
- 默认行为保持开启；
- 测试覆盖开/关两种模式；
- 不能只为测试补一个永远返回 True 的假方法。

### 7.5 修复阶段和快照兼容

建立当前受支持阶段表。
若 `execute_content_plan` 已被新阶段替代：

- 对历史 operation/snapshot 提供明确兼容映射；
- 新运行只写新阶段名；
- 历史工作空间不能因此打不开；
- 未知阶段仍须 fail closed。

### 7.6 修复规划和目录校验

逐一核对：

- 目录候选的 controlled repair 是否仍是受支持行为；
- review-only finding 与 blocking finding 的边界；
- strict template 标题/顺序冲突必须阻断；
- unknown Source/Unit 必须阻断；
- `outline_path` 中纯评价句是否应形成层级；
- Artifact Registry 版本变更是否有正式原因。

禁止为了让测试通过而降低 G2 或模板硬门禁。

### 7.7 修复 ProjectModel、H1 和 orientation

- ProjectModel 输入 projection 必须稳定；
- strict template H1 依赖必须与当前 ADR 一致；
- diagram/visual 章节角色识别恢复；
- 补边界测试。

### 7.8 建立基线验证脚本

`scripts/verify_pr00_baseline.py` 至少检查：

- 关键模块可导入；
- ControlStore 可从旧 Schema 升级；
- 统一 Writer 入口存在；
- 禁止的 Writer 类不存在；
- 章节确认 helper 行为；
- stage registry 与 snapshot 一致；
- 确定性 Provider 不联网；
- 当前 API contract 的关键字段；
- Word 导出模块可运行。

脚本失败必须非零退出。

### 7.9 完整回归

最后连续两次运行完整门禁，排除偶发顺序依赖。


## 8. 自动化测试


1. 29 个失败测试全部恢复；
2. 每个被替换的旧测试有等价或更强的新测试；
3. 研究测试明确断言没有真实网络调用；
4. 旧 control.db 升级测试；
5. 当前直接写作链路测试；
6. 当前 WritingPlan 展示/修改/直接写正文测试；
7. 章节确认开关测试；
8. 批量任务恢复测试；
9. 严格模板 H1 到 Writer 测试；
10. Word 导出测试；
11. `verify_pr00_baseline.py` 自测试。

最终要求：

```text
0 failed
允许既有明确标记的 skipped
不得新增 xfail
```


## 9. 人工验收场景


用一个脱敏项目或测试 fixture 执行：

1. 登录；
2. 创建工作空间；
3. 上传招标书和评分文件；
4. 生成目录；
5. H1 确认；
6. 打开叶子章节；
7. 查看内部 WritingPlan；
8. 直接说“开始编写本章正文”；
9. 观察搜索判断；
10. 正文写入中间区域；
11. 手工编辑；
12. H2 确认；
13. 批量写两章并模拟服务重启；
14. 导出当前 Word；
15. 严格模板项目执行一次目录和写作。

记录每一步结果，不接受只看单元测试。


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


PR-00 回滚只允许回滚本 PR 的修复提交。

由于本 PR 不改变数据库 Schema 和产品功能，回滚后会回到当前红色基线。
若某个修复引入新回归，应逐提交回滚，而不是恢复到 `74cd1ff12a79a373f9c262d48f61e03caa3cd642`，
因为那会连同用户刚提交的有效改动一起抹掉。


## 13. Definition of Done


- [ ] 29 个已知失败全部处理；
- [ ] GitHub Actions 三个 job 全绿；
- [ ] 本地完整门禁连续通过两次；
- [ ] 当前全量编写闭环通过；
- [ ] 当前批量恢复通过；
- [ ] 当前确认开关通过；
- [ ] 当前 Word 导出通过；
- [ ] strict template 路径通过；
- [ ] 基线脚本存在并通过；
- [ ] 失败分类表完整；
- [ ] 未新增任何后续功能；
- [ ] PR 合并后的 `main` 仍全绿。


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
你正在 `breezePeak/bid_agent` 仓库中实施 **PR-00：修复当前主分支并冻结可信基线**。请直接检查代码、补测试并完成实现，不要只输出方案。

计划起始基线：`85702e3aa60bd5e2f7b26a130ef7a6048499e020`

开始前必须执行：
1. `git status --short`
2. `git rev-parse HEAD`
3. `git log -5 --oneline`
4. 如果 HEAD 已晚于计划基线，先审查新提交对本 PR 的影响。
5. 不得覆盖与本任务无关的用户修改。

建议分支：`agent/pr-00-work`

开始条件：

- 当前 `main` 必须是 `85702e3aa60bd5e2f7b26a130ef7a6048499e020`，或只包含经过审查、与本 PR 无冲突的后续提交。
- 当前工作区不得有未识别的用户修改。
- 不要求当前测试通过，因为本 PR 的任务就是修复当前红色基线。
- 必须能够访问 PR #10 的 CI 失败记录或在本地完整复现。


必须阅读：
- `docs/unified_writing_pr_plan_v2/00_START_HERE_当前只执行PR-00.md`
- `docs/unified_writing_pr_plan_v2/01_总纲_架构边界_依赖图_统一验收规则.md`
- `docs/unified_writing_pr_plan_v2/PR-00_修复当前主分支并冻结可信基线.md`
- 与本 PR 有关的 ADR、生产代码和现有测试

本 PR 唯一目标：

把当前主分支恢复到“确定性 CI 全绿、现有业务主流程可复现”的可信状态，并建立后续
功能开发不能绕过的基线验证脚本与证据。**本 PR 不实现任何标书改写或正式规划确认新功能。**


允许范围：

允许：

- 修复当前生产代码回归；
- 恢复或重建必要的依赖注入测试缝隙；
- 修复确定性测试的 Provider 隔离；
- 修复阶段兼容映射；
- 修复确认开关；
- 修复测试 fixture；
- 更新确实过期的冻结版本测试，并记录理由；
- 新增基线验证脚本和基线报告；
- 更新 `docs/current_logic_flow_v3.md` 中已经与代码不符的事实。

所有修改只能让当前已存在的功能恢复正确。


硬性禁止：

禁止：

- 新增 `writing_mode`；
- 新增 `LEGACY_BID`；
- 新增 plan control tables；
- 新增章节计划确认回执；
- 修改工作台为双页签；
- 把搜索前移；
- 修改目录生成以读取旧投标书；
- 新增标书改写入口；
- 修改当前产品行为以迎合后续计划；
- 删除失败测试而不提供替代测试；
- 通过跳过、xfail、放宽断言把 CI 变绿。


执行要求：
1. 先列出当前真实调用链、将修改的文件和风险点。
2. 先补能证明当前行为和目标行为的测试，再改实现。
3. 每完成一个小步骤立即运行对应局部测试。
4. Feature Flag 关闭时必须保持现有行为。
5. 旧测试与当前受支持行为冲突时，不得直接删除；必须给出证据并补替代测试。
6. 数据库迁移必须可重复、可升级旧工作空间、不可破坏原表。
7. 完成后运行全部门禁命令。
8. 最终提交本 PR 报告，然后停止，不得开始 PR-01。

必须运行：
python -m compileall -q src
ruff check src tests --quiet
python -m pytest tests -q --tb=line -k "not live_llm"
cd frontend
npm ci
npm test
npm run build

本 PR 特别要求：

- 必须先复现当前 GitHub Actions unit 失败。
- 所有 29 个失败都要进入分类表，不能只修自己碰到的几个。
- 确定性测试禁止访问 Tavily 或其他外部网络。
- 每个旧测试修改都要说明为何不是掩盖生产回归。
- 创建 `scripts/verify_pr00_baseline.py` 和基线证据文档。
- 完成后明确写出“PR-01 未开始”。


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


---

<!-- FILE: PR-01_项目写作模式与输入角色脚手架.md -->

# PR-01：项目写作模式与输入角色脚手架


- 计划版本：`PR Plan V2.0`
- 仓库：`breezePeak/bid_agent`
- 起始基线：`85702e3aa60bd5e2f7b26a130ef7a6048499e020`
- 开发原则：本 PR 独立可部署、可测试、可回滚


## 1. 开始条件


- PR-00 已合并。
- 合并后的 `main` GitHub Actions 全绿。
- `scripts/verify_pr00_baseline.py` 通过。
- 当前全量编写、批量、H2 和 Word 导出基线已冻结。


不满足开始条件时，本 PR 立即停止。不得以“先做一部分不影响”为理由继续。

## 2. 本 PR 的唯一目标


增加可持久化的项目写作模式、章节规划流程版本和旧投标书输入角色脚手架，
但所有新行为默认关闭，现有工作空间和现有全量编写路径完全不变。


## 3. 当前代码事实


当前前端创建工作空间只提交项目名称；即使 API helper 曾包含 `project_type`，
创建接口也没有把写作模式作为权威状态保存。

当前 `DocumentMode` 仅表示 `template_strict / auto_outline`，不能承担
`full_write / bid_rewrite`。

当前 `InputRole` 没有 `LEGACY_BID`。直接使用 `REFERENCE` 会导致旧投标书和普通
公开参考资料混在一起，也无法执行目录树恢复、段落复用和旧项目污染检查。


## 4. 本 PR 允许修改的范围


允许：

- 新增 `ProjectWritingMode`；
- 新增 `ChapterPlanFlowVersion`；
- 增量迁移 `document_state`；
- 创建工作空间 API 接受并保存模式；
- snapshot/list API 返回模式和能力；
- 增加 `InputRole.LEGACY_BID`；
- 增加 Feature Flag 和 capability 判定；
- 上传层识别但默认禁止 legacy_bid；
- 增加前后端契约解析；
- 补完整迁移与兼容测试。

新功能只能作为脚手架，不改变写作。


## 5. 本 PR 明确不做


禁止：

- 生成 ChapterWritingPlan v2；
- 新增 plan revision 表；
- 改变当前直接写正文行为；
- 移动搜索阶段；
- 解析旧投标书语义；
- 修改目录生成；
- 显示新工作台页签；
- 对用户开放 bid_rewrite 创建入口；
- 让 legacy_bid 参与 RequirementLedger、ProjectModel 或 Writer。


## 6. 预计文件、类、接口和表


| 文件/模块 | 变化 |
|---|---|
| `contracts.py` | `ProjectWritingMode`、`ChapterPlanFlowVersion`、`InputRole.LEGACY_BID` |
| `control_plane.py` | Schema version + `document_state.writing_mode/chapter_plan_flow` |
| `v3_app.py` | 创建工作空间校验、列表和 snapshot 投影 |
| `workspace_snapshot.py` | 返回写作模式和 capabilities |
| `input_manifest.py` | role 注册兼容 |
| 上传校验 | legacy_bid 受 Feature Flag 和 mode 限制 |
| `frontend/src/api/*Contracts.js` | 新字段兼容解析 |
| `CreateWorkspaceDialog.vue` | 本 PR 只保留默认 full_write，不公开选项 |
| `.env.example` | 新 Feature Flag |


文件清单是实施导航，不是让开发者不看代码就机械修改。实际文件有变化时，必须在 PR 描述中说明原因。

## 7. 详细实现顺序


### 7.1 契约定义

```python
class ProjectWritingMode(str, Enum):
    FULL_WRITE = "full_write"
    BID_REWRITE = "bid_rewrite"

class ChapterPlanFlowVersion(str, Enum):
    LEGACY_INLINE = "legacy_inline"
    CONFIRMED_PLAN_V2 = "confirmed_plan_v2"
```

旧工作空间默认：

```text
writing_mode = full_write
chapter_plan_flow = legacy_inline
```

### 7.2 数据库迁移

向 `document_state` 增量增加：

```sql
writing_mode TEXT NOT NULL DEFAULT 'full_write'
chapter_plan_flow TEXT NOT NULL DEFAULT 'legacy_inline'
```

迁移要求：

- `PRAGMA table_info` 判断；
- 可重复执行；
- 不重建旧表；
- 不覆盖已有值；
- 写入 Schema version；
- 提供旧 DB fixture 升级测试。

### 7.3 创建接口

请求允许：

```json
{{
  "name": "项目名称",
  "writing_mode": "full_write"
}}
```

未提供时使用 `full_write`。

当请求 `bid_rewrite` 时：

- Feature Flag 关闭：返回明确 capability disabled；
- Flag 开启但功能未完成：仍返回 feature not released；
- 不能创建半可用项目。

### 7.4 InputRole

增加 `legacy_bid` 后，所有 role switch 必须 fail closed。
特别检查：

- 上传扩展名；
- SourceNormalizer；
- manifest 展示；
- material role labels；
- Requirement Agent role filter；
- Score Agent role filter；
- ProjectModel source projection；
- global context；
- research；
- WriterBundle。

本 PR 中 legacy_bid 不能进入任何语义下游。

### 7.5 前端兼容

API contract 允许读取新字段，但创建对话框仍只显示项目名。
这是为了先证明后端和迁移稳定，不让一个尚不可用的入口出现在用户眼前。


## 8. 自动化测试


1. 旧 control.db 自动增加列并保持原值；
2. 新工作空间默认 full_write/legacy_inline；
3. 非法 writing_mode 400；
4. Feature Flag 关闭时 bid_rewrite 被明确拒绝；
5. legacy_bid role 可被 Schema 识别；
6. legacy_bid 上传在功能关闭时被拒绝；
7. legacy_bid 不进入 Requirement/Score/ProjectModel；
8. snapshot/list 返回稳定字段；
9. 旧前端 contract 能忽略新增字段；
10. PR-00 全套回归继续通过。


## 9. 人工验收场景


- 用旧工作空间副本启动新代码，确认可打开、写章、H2、导出。
- 创建新 full_write 工作空间，确认行为与 PR-00 完全相同。
- 手工构造 bid_rewrite 请求，确认得到明确“功能未开放”，而不是创建空目录。
- 上传 role=legacy_bid，确认功能关闭时不会进入 Source/ProjectModel。


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


关闭全部新 Feature Flag。

新增列保留，不逆向删列。服务读取默认 `full_write/legacy_inline`。
若 API 兼容有问题，只回滚创建接口和 snapshot 投影，不回滚数据库迁移。


## 13. Definition of Done


- [ ] 旧工作空间零人工迁移；
- [ ] 新工作空间默认行为不变；
- [ ] bid_rewrite 不会提前开放；
- [ ] legacy_bid 不污染语义链；
- [ ] 所有 switch 对新枚举有显式处理；
- [ ] 完整 CI 全绿；
- [ ] PR-02 未开始。


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
你正在 `breezePeak/bid_agent` 仓库中实施 **PR-01：项目写作模式与输入角色脚手架**。请直接检查代码、补测试并完成实现，不要只输出方案。

计划起始基线：`85702e3aa60bd5e2f7b26a130ef7a6048499e020`

开始前必须执行：
1. `git status --short`
2. `git rev-parse HEAD`
3. `git log -5 --oneline`
4. 如果 HEAD 已晚于计划基线，先审查新提交对本 PR 的影响。
5. 不得覆盖与本任务无关的用户修改。

建议分支：`agent/pr-01-work`

开始条件：

- PR-00 已合并。
- 合并后的 `main` GitHub Actions 全绿。
- `scripts/verify_pr00_baseline.py` 通过。
- 当前全量编写、批量、H2 和 Word 导出基线已冻结。


必须阅读：
- `docs/unified_writing_pr_plan_v2/00_START_HERE_当前只执行PR-00.md`
- `docs/unified_writing_pr_plan_v2/01_总纲_架构边界_依赖图_统一验收规则.md`
- `docs/unified_writing_pr_plan_v2/PR-01_项目写作模式与输入角色脚手架.md`
- 与本 PR 有关的 ADR、生产代码和现有测试

本 PR 唯一目标：

增加可持久化的项目写作模式、章节规划流程版本和旧投标书输入角色脚手架，
但所有新行为默认关闭，现有工作空间和现有全量编写路径完全不变。


允许范围：

允许：

- 新增 `ProjectWritingMode`；
- 新增 `ChapterPlanFlowVersion`；
- 增量迁移 `document_state`；
- 创建工作空间 API 接受并保存模式；
- snapshot/list API 返回模式和能力；
- 增加 `InputRole.LEGACY_BID`；
- 增加 Feature Flag 和 capability 判定；
- 上传层识别但默认禁止 legacy_bid；
- 增加前后端契约解析；
- 补完整迁移与兼容测试。

新功能只能作为脚手架，不改变写作。


硬性禁止：

禁止：

- 生成 ChapterWritingPlan v2；
- 新增 plan revision 表；
- 改变当前直接写正文行为；
- 移动搜索阶段；
- 解析旧投标书语义；
- 修改目录生成；
- 显示新工作台页签；
- 对用户开放 bid_rewrite 创建入口；
- 让 legacy_bid 参与 RequirementLedger、ProjectModel 或 Writer。


执行要求：
1. 先列出当前真实调用链、将修改的文件和风险点。
2. 先补能证明当前行为和目标行为的测试，再改实现。
3. 每完成一个小步骤立即运行对应局部测试。
4. Feature Flag 关闭时必须保持现有行为。
5. 旧测试与当前受支持行为冲突时，不得直接删除；必须给出证据并补替代测试。
6. 数据库迁移必须可重复、可升级旧工作空间、不可破坏原表。
7. 完成后运行全部门禁命令。
8. 最终提交本 PR 报告，然后停止，不得开始 PR-02。

必须运行：
python -m compileall -q src
ruff check src tests --quiet
python -m pytest tests -q --tb=line -k "not live_llm"
cd frontend
npm ci
npm test
npm run build

本 PR 特别要求：

- 只做模式和角色脚手架。
- 创建 UI 不得提前显示“标书改写”。
- 证明 legacy_bid 在本 PR 中不会进入任何语义下游。
- 所有旧工作空间测试必须使用真实旧 Schema fixture。


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


---

<!-- FILE: PR-02_章节编写规划控制面与版本确认内核.md -->

# PR-02：章节编写规划控制面与版本确认内核


- 计划版本：`PR Plan V2.0`
- 仓库：`breezePeak/bid_agent`
- 起始基线：`85702e3aa60bd5e2f7b26a130ef7a6048499e020`
- 开发原则：本 PR 独立可部署、可测试、可回滚


## 1. 开始条件


- PR-01 已合并并全绿。
- 旧工作空间迁移测试通过。
- `chapter_plan_flow=legacy_inline` 的现有写作行为与 PR-00 一致。


不满足开始条件时，本 PR 立即停止。不得以“先做一部分不影响”为理由继续。

## 2. 本 PR 的唯一目标


建立正式的章节编写规划追加版本、精确哈希、依赖 fingerprint、确认回执和 CAS 控制面。
本 PR 只建立权威内核和影子写入能力，不改变当前正文写作入口。


## 3. 当前代码事实


当前内部 WritingPlan 保存在：

```text
workspace/v3/chapter_chats/_writing_plans.json
```

它没有 append-only revision、CAS、plan hash、dependency fingerprint、确认回执、
stale 判定、数据库事务和 API 权威读取。

当前 `WriterInputBundle` 只有一个自由形态 `chapter_writing_plan: dict`。
这足以向 Writer 传计划，但不能证明用户确认的是哪一版。


## 4. 本 PR 允许修改的范围


允许：

- 新增强类型 plan contracts；
- 新增 control.db 表和 chapter pointer；
- 新增 `ChapterWritingPlanService`；
- 新增 plan proposal/append/confirm/read/invalidate 命令；
- 新增精确 approval receipt；
- 新增 ADR；
- `_writing_plans.json` 兼容读取与投影；
- 影子保存现有内部计划。

所有工作空间仍保持 `legacy_inline`。


## 5. 本 PR 明确不做


禁止：

- Writer 强制要求 plan receipt；
- 搜索前移；
- 前端双页签；
- 用户编辑正式 plan；
- 旧投标书解析；
- 改写类型；
- 修改当前 `writeCurrentChapter`；
- 禁用批量写作。


## 6. 预计文件、类、接口和表


| 范围 | 变化 |
|---|---|
| `contracts.py` | PlanSource、PlanContentUnit、PlanBinding、PlanRevision、ApprovalReceipt |
| `control_plane.py` | 3 张表、chapter pointer、CAS 方法 |
| 新增 `chapter_writing_plan.py` | 规划控制服务 |
| `execution_controller.py` | plan command handlers |
| `v3_app.py` | GET plan 只读接口，写入仍走 commands |
| `workspace_snapshot.py` | plan summary |
| `chapter_chat.py` | 兼容投影，不再把 JSON 当权威 |
| `docs/adr/ADR-16-*` | 章节规划执行授权边界 |


文件清单是实施导航，不是让开发者不看代码就机械修改。实际文件有变化时，必须在 PR 描述中说明原因。

## 7. 详细实现顺序


### 7.1 强类型模型

至少定义：

```text
ChapterPlanSource
ChapterPlanContentUnit
ChapterPlanBinding
ChapterWritingPlanRevisionRecord
ChapterPlanApprovalReceiptRecord
```

所有模型使用 `extra=forbid`。

### 7.2 表结构

```sql
chapter_writing_plan_revisions
chapter_plan_approval_receipts
chapter_plan_events
```

`chapter_workspaces` 增加：

```text
head_plan_revision
confirmed_plan_revision
plan_status
```

这些字段进入 chapter workspace state hash。

### 7.3 追加版本

`append_plan_revision()` 必须：

1. 校验 leaf chapter；
2. 校验 expected chapter revision；
3. 解析严格 Schema；
4. 重载 Blueprint、global context、chapter context；
5. 计算 dependency fingerprint；
6. 计算 plan hash；
7. 单事务追加 revision；
8. CAS 更新 head pointer；
9. 增加 chapter revision；
10. 写 workspace event。

禁止原地 update `plan_json`。

### 7.4 确认回执

`confirm_plan()` 必须校验：

- 已认证用户；
- exact plan revision/hash；
- exact dependency fingerprint；
- 当前 head pointer；
- 当前依赖重算一致；
- 单事务写 receipt；
- 更新 confirmed pointer；
- 幂等确认返回同一 receipt；
- 不同 hash 不得复用 receipt。

### 7.5 stale 判定

本 PR 提供确定性状态：

```text
current
stale_blueprint
stale_global_context
stale_chapter_context
stale_source
stale_evidence
```

不主动改变 Writer，只在 read API 和 snapshot 中展示。

### 7.6 JSON 兼容

启动时可以读取 `_writing_plans.json` 作为 seed：

- 只导入当前计划为 `source=legacy_projection`；
- 不自动确认；
- 不删除文件；
- 导入幂等；
- 导入失败不阻断 legacy_inline 工作空间。

后续 JSON 只作为可重建投影输出。

### 7.7 ADR

新增 ADR 明确：

- H1 仍是全局 Blueprint 唯一规划 Gate；
- 章节 Plan receipt 是执行授权；
- 不允许修改 Blueprint；
- 不允许绕过 WriterInputBundle；
- Agent 只提交候选。


## 8. 自动化测试


1. Schema extra forbid；
2. plan hash canonical stability；
3. append-only；
4. stale base revision 冲突；
5. duplicate idempotency；
6. exact receipt binding；
7. 依赖变化后确认失败；
8. 用户身份校验；
9. JSON seed 幂等；
10. 旧 workspace 无 plan 表数据仍可写；
11. snapshot summary；
12. 服务重启后 pointer/receipt 恢复；
13. SQLite 中断事务不留半 revision；
14. PR-00/01 全回归。


## 9. 人工验收场景


- 在 legacy_inline 工作空间打开章节，观察后台影子 plan revision。
- 当前“开始编写正文”仍直接工作。
- 修改章节上下文后，旧 plan API 显示 stale，但当前 legacy 写作不被阻断。
- 重启服务，plan revision 和 receipt 保持。
- 尝试伪造 plan hash，确认返回 409。


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


关闭影子写入 Feature Flag。

新表和新列保留；legacy_inline 不读取 confirmed pointer。
若 seed 导入异常，关闭 import flag，不删除用户 JSON。


## 13. Definition of Done


- [ ] 规划权威状态进入 control.db；
- [ ] JSON 不再是新状态权威；
- [ ] exact receipt binding 测试通过；
- [ ] legacy_inline 行为完全不变；
- [ ] 新 ADR 已冻结；
- [ ] 全 CI 通过；
- [ ] PR-03 未开始。


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
你正在 `breezePeak/bid_agent` 仓库中实施 **PR-02：章节编写规划控制面与版本确认内核**。请直接检查代码、补测试并完成实现，不要只输出方案。

计划起始基线：`85702e3aa60bd5e2f7b26a130ef7a6048499e020`

开始前必须执行：
1. `git status --short`
2. `git rev-parse HEAD`
3. `git log -5 --oneline`
4. 如果 HEAD 已晚于计划基线，先审查新提交对本 PR 的影响。
5. 不得覆盖与本任务无关的用户修改。

建议分支：`agent/pr-02-work`

开始条件：

- PR-01 已合并并全绿。
- 旧工作空间迁移测试通过。
- `chapter_plan_flow=legacy_inline` 的现有写作行为与 PR-00 一致。


必须阅读：
- `docs/unified_writing_pr_plan_v2/00_START_HERE_当前只执行PR-00.md`
- `docs/unified_writing_pr_plan_v2/01_总纲_架构边界_依赖图_统一验收规则.md`
- `docs/unified_writing_pr_plan_v2/PR-02_章节编写规划控制面与版本确认内核.md`
- 与本 PR 有关的 ADR、生产代码和现有测试

本 PR 唯一目标：

建立正式的章节编写规划追加版本、精确哈希、依赖 fingerprint、确认回执和 CAS 控制面。
本 PR 只建立权威内核和影子写入能力，不改变当前正文写作入口。


允许范围：

允许：

- 新增强类型 plan contracts；
- 新增 control.db 表和 chapter pointer；
- 新增 `ChapterWritingPlanService`；
- 新增 plan proposal/append/confirm/read/invalidate 命令；
- 新增精确 approval receipt；
- 新增 ADR；
- `_writing_plans.json` 兼容读取与投影；
- 影子保存现有内部计划。

所有工作空间仍保持 `legacy_inline`。


硬性禁止：

禁止：

- Writer 强制要求 plan receipt；
- 搜索前移；
- 前端双页签；
- 用户编辑正式 plan；
- 旧投标书解析；
- 改写类型；
- 修改当前 `writeCurrentChapter`；
- 禁用批量写作。


执行要求：
1. 先列出当前真实调用链、将修改的文件和风险点。
2. 先补能证明当前行为和目标行为的测试，再改实现。
3. 每完成一个小步骤立即运行对应局部测试。
4. Feature Flag 关闭时必须保持现有行为。
5. 旧测试与当前受支持行为冲突时，不得直接删除；必须给出证据并补替代测试。
6. 数据库迁移必须可重复、可升级旧工作空间、不可破坏原表。
7. 完成后运行全部门禁命令。
8. 最终提交本 PR 报告，然后停止，不得开始 PR-03。

必须运行：
python -m compileall -q src
ruff check src tests --quiet
python -m pytest tests -q --tb=line -k "not live_llm"
cd frontend
npm ci
npm test
npm run build

本 PR 特别要求：

- 不得让 Writer 在本 PR 开始要求 plan receipt。
- 重点审查事务边界、hash、fingerprint 和 CAS。
- 必须用旧 `_writing_plans.json` fixture 做幂等导入测试。


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


---

<!-- FILE: PR-03_全量编写规划与搜索前移_影子运行.md -->

# PR-03：全量编写规划与搜索前移的影子运行


- 计划版本：`PR Plan V2.0`
- 仓库：`breezePeak/bid_agent`
- 起始基线：`85702e3aa60bd5e2f7b26a130ef7a6048499e020`
- 开发原则：本 PR 独立可部署、可测试、可回滚


## 1. 开始条件


- PR-02 已合并且全绿。
- Plan revision/receipt 内核通过并发和重启测试。
- legacy_inline 正文路径未改变。


不满足开始条件时，本 PR 立即停止。不得以“先做一部分不影响”为理由继续。

## 2. 本 PR 的唯一目标


在全量编写项目中生成完整的 v2 章节规划候选，包含“来源 → 内容块 → 目标章节”绑定，
并在规划阶段执行公开资料搜索。结果只做影子记录和差异评估，暂不控制 Writer。


## 3. 当前代码事实


当前 `compile_chapter_writing_plan()` 已能生成 blocks，并支持 `project_fact_refs`。
当前 `WriterResearchCoordinator` 在 `ChapterWritingService` 内先判断、再搜索、随后立即写正文。

当前计划缺少统一来源模型：

- 招标要求只是约束；
- 项目事实通过零散字段传入；
- 搜索来源只有写作时才可见；
- 来源未绑定具体内容块；
- 用户无法在写前查看最终素材。


## 4. 本 PR 允许修改的范围


允许：

- 扩展 v2 plan candidate；
- 新增 `ChapterWritingPlanBuilder`；
- 新增 source/content_unit/binding；
- 抽取可复用 research execution 内核；
- 在规划阶段执行搜索并保存 Evidence；
- 影子写入 plan revision；
- 新旧 plan 差异报告；
- telemetry 和耗时指标。

不改变 Writer 当前读取和 inline search。


## 5. 本 PR 明确不做


禁止：

- 用户确认 plan；
- 前端编辑；
- Writer 只读 v2 plan；
- 关闭 inline research；
- 旧投标书来源；
- 标书改写；
- 改变当前写作按钮或批量行为。


## 6. 预计文件、类、接口和表


| 模块 | 变化 |
|---|---|
| `chapter_writing_outline.py` | 保留现有 compiler，增加 v2 adapter |
| 新增 `chapter_writing_plan_builder.py` | 统一规划候选 |
| `chapter_research_planner.py` | 输出结构化 gap/query |
| `writer_research.py` | 抽取共享执行器，保留兼容 adapter |
| `research_service.py` | 证据快照引用 |
| `chapter_writing_plan.py` | 保存 shadow revision |
| `workspace_snapshot.py` | shadow status/metrics |
| plan shadow report | 可重建投影，不是权威状态 |


文件清单是实施导航，不是让开发者不看代码就机械修改。实际文件有变化时，必须在 PR 描述中说明原因。

## 7. 详细实现顺序


### 7.1 规划内容块

以当前 blocks 为基础，统一为：

```text
content_unit_id
title
purpose
must_answer
order
requirement_ids
score_point_ids
condition_ids
```

不得创建 Blueprint 之外的新章节。

### 7.2 来源

全量编写至少支持：

```text
TENDER_REQUIREMENT
SCORE_OBLIGATION
GLOBAL_PROJECT_FACT
CHAPTER_CONTEXT_ITEM
USER_MATERIAL_BLOCK
SIBLING_REFERENCE
WEB_EVIDENCE
```

每个来源保存稳定引用和 hash，UI preview 与 Writer full snapshot 分离。

### 7.3 绑定

每条 binding 包含：

```text
source_id
content_unit_id
usage_type
instruction
required
```

`usage_type` 固定为 constraint、base_fact、support、supplement、evidence 或 cross_reference。

### 7.4 资料充分性

先按内容块判断：

- 已有项目事实是否足够；
- 招标要求是否需要公开背景；
- 是否涉及现行政策、标准、规范；
- 是否是企业事实禁搜范围；
- 是否是项目承诺禁搜范围。

只有明确 gap 才搜索。

### 7.5 搜索执行抽取

将 Provider 执行、重试、EvidenceBatch 发布、原文快照从
`WriterResearchCoordinator` 中抽取为共享执行器。

要求：

- legacy inline adapter 行为不变；
- deterministic tests 不联网；
- v2 plan 使用同一 Evidence 权威；
- 搜索摘要不直接成为正文材料；
- 原文/支持片段进入 EvidenceItem。

### 7.6 影子模式

对于 legacy_inline：

```text
现有写作继续
并行或预先生成 shadow plan
记录新旧计划差异
不阻断用户
```

差异指标：

- 内容块数量；
- search decision；
- evidence source；
- project fact coverage；
- 计划生成耗时；
- token；
- 错误率。

### 7.7 失败策略

shadow 失败：

- 记录错误；
- 不影响现有写作；
- 不创建 confirmed plan；
- snapshot 显示 shadow_failed；
- 不静默假装成功。


## 8. 自动化测试


1. content unit 顺序稳定；
2. source ID/hash 稳定；
3. binding 引用完整；
4. 禁搜企业事实；
5. 项目事实充分时不搜索；
6. 现行标准缺口时搜索；
7. 搜索发布 Evidence；
8. 原文快照存在；
9. shadow 失败不影响 legacy write；
10. legacy inline research 输出不变；
11. provider retry deterministic；
12. shadow diff report；
13. 并发两章不串来源；
14. 全量回归。


## 9. 人工验收场景


- 全量编写项目点开三个不同章节。
- 触发 shadow plan。
- 项目任务章应主要使用 ProjectModel，不应乱搜。
- 政策背景章应按缺口检索公开来源。
- 企业资质章必须拒绝公开搜索替代企业资料。
- 当前正文仍按旧链路成功生成。
- 对比报告可读且不含敏感完整正文。


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


关闭 `BID_AGENT_CHAPTER_PLAN_V2_ENABLED` 或 shadow flag。

共享研究执行器必须保留 legacy adapter，因此回滚 v2 builder 不影响当前 inline research。


## 13. Definition of Done


- [ ] v2 plan sources/units/bindings 完整；
- [ ] 搜索可在规划阶段完成；
- [ ] legacy writer 未切换；
- [ ] shadow 失败不阻断；
- [ ] 禁搜边界通过；
- [ ] Evidence 原文快照通过；
- [ ] 完整 CI 全绿；
- [ ] PR-04 未开始。


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
你正在 `breezePeak/bid_agent` 仓库中实施 **PR-03：全量编写规划与搜索前移的影子运行**。请直接检查代码、补测试并完成实现，不要只输出方案。

计划起始基线：`85702e3aa60bd5e2f7b26a130ef7a6048499e020`

开始前必须执行：
1. `git status --short`
2. `git rev-parse HEAD`
3. `git log -5 --oneline`
4. 如果 HEAD 已晚于计划基线，先审查新提交对本 PR 的影响。
5. 不得覆盖与本任务无关的用户修改。

建议分支：`agent/pr-03-work`

开始条件：

- PR-02 已合并且全绿。
- Plan revision/receipt 内核通过并发和重启测试。
- legacy_inline 正文路径未改变。


必须阅读：
- `docs/unified_writing_pr_plan_v2/00_START_HERE_当前只执行PR-00.md`
- `docs/unified_writing_pr_plan_v2/01_总纲_架构边界_依赖图_统一验收规则.md`
- `docs/unified_writing_pr_plan_v2/PR-03_全量编写规划与搜索前移的影子运行.md`
- 与本 PR 有关的 ADR、生产代码和现有测试

本 PR 唯一目标：

在全量编写项目中生成完整的 v2 章节规划候选，包含“来源 → 内容块 → 目标章节”绑定，
并在规划阶段执行公开资料搜索。结果只做影子记录和差异评估，暂不控制 Writer。


允许范围：

允许：

- 扩展 v2 plan candidate；
- 新增 `ChapterWritingPlanBuilder`；
- 新增 source/content_unit/binding；
- 抽取可复用 research execution 内核；
- 在规划阶段执行搜索并保存 Evidence；
- 影子写入 plan revision；
- 新旧 plan 差异报告；
- telemetry 和耗时指标。

不改变 Writer 当前读取和 inline search。


硬性禁止：

禁止：

- 用户确认 plan；
- 前端编辑；
- Writer 只读 v2 plan；
- 关闭 inline research；
- 旧投标书来源；
- 标书改写；
- 改变当前写作按钮或批量行为。


执行要求：
1. 先列出当前真实调用链、将修改的文件和风险点。
2. 先补能证明当前行为和目标行为的测试，再改实现。
3. 每完成一个小步骤立即运行对应局部测试。
4. Feature Flag 关闭时必须保持现有行为。
5. 旧测试与当前受支持行为冲突时，不得直接删除；必须给出证据并补替代测试。
6. 数据库迁移必须可重复、可升级旧工作空间、不可破坏原表。
7. 完成后运行全部门禁命令。
8. 最终提交本 PR 报告，然后停止，不得开始 PR-04。

必须运行：
python -m compileall -q src
ruff check src tests --quiet
python -m pytest tests -q --tb=line -k "not live_llm"
cd frontend
npm ci
npm test
npm run build

本 PR 特别要求：

- 重点是影子运行，不得切换 Writer。
- 抽取 research executor 时必须用回归测试证明 legacy adapter 兼容。
- 规划来源必须有稳定 ID 和 hash。


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


---

<!-- FILE: PR-04_工作台编写逻辑与正文双页签_只读展示.md -->

# PR-04：工作台编写逻辑与正文双页签只读展示


- 计划版本：`PR Plan V2.0`
- 仓库：`breezePeak/bid_agent`
- 起始基线：`85702e3aa60bd5e2f7b26a130ef7a6048499e020`
- 开发原则：本 PR 独立可部署、可测试、可回滚


## 1. 开始条件


- PR-03 已合并并全绿。
- shadow plan 在测试项目中稳定生成。
- plan read API 可返回严格 contract。


不满足开始条件时，本 PR 立即停止。不得以“先做一部分不影响”为理由继续。

## 2. 本 PR 的唯一目标


在当前章节工作台中增加“编写逻辑 / 正文”双页签和只读规划关系图。
本 PR 不允许用户修改或确认规划，也不改变当前写正文行为。


## 3. 当前代码事实


当前 `ChapterWorkbenchView.vue` 的中间区域只渲染 `ContentBlockEditor`。
当前章节的 WritingPlan 只能在右侧聊天中通过自然语言请求显示。

用户无法直观看到计划内容块、项目资料来源、搜索来源、来源用途和陈旧原因。


## 4. 本 PR 允许修改的范围


允许：

- 新增 plan read API contract；
- 新增前端 plan components；
- 增加双页签；
- 使用 CSS Grid + SVG 绘线；
- 显示 sources/content_units/bindings；
- 显示 shadow/current/stale/read-only 状态；
- 切换章节时加载 plan；
- 保留正文编辑器状态。

所有交互只读。


## 5. 本 PR 明确不做


禁止：

- 规划编辑；
- 删除来源；
- 继续搜索；
- 确认规划；
- Writer gating；
- 改变“一键编写”；
- 改变批量；
- 旧投标书节点；
- 改写类型标签。


## 6. 预计文件、类、接口和表


| 前端文件 | 变化 |
|---|---|
| `ChapterWorkbenchView.vue` | 中间页签、plan loading/state |
| 新增 `ChapterWritingPlanPanel.vue` | 规划主容器 |
| 新增 `ChapterPlanGraph.vue` | 三列关系图 |
| 新增 `ChapterPlanSourceCard.vue` | 来源卡 |
| 新增 `ChapterPlanUnitCard.vue` | 内容块卡 |
| 新增 `ChapterPlanDetailDrawer.vue` | 详情 |
| `api/index.js` | `fetchChapterWritingPlan` |
| `api/chapterContracts.js` | plan contract validator |
| `main.css` | 响应式布局 |
| 前端 tests | SFC、contract、状态切换 |


文件清单是实施导航，不是让开发者不看代码就机械修改。实际文件有变化时，必须在 PR 描述中说明原因。

## 7. 详细实现顺序


### 7.1 页签规则

```text
无正文 + 有 plan：默认编写逻辑
有正文：默认正文
plan stale：显示红色提示，但不自动覆盖正文
plan loading：骨架屏
plan unavailable：清晰说明当前为 legacy flow
```

用户切换页签不得销毁编辑器未保存状态。

### 7.2 图结构

固定三列：

```text
来源材料 → 计划内容块 → 当前章节
```

- 左列来源卡；
- 中列内容块；
- 右列目标章；
- SVG 连接线；
- `ResizeObserver` 计算坐标；
- 节点折叠时重新绘制；
- 不引入重型图框架。

### 7.3 来源显示

只读卡显示：

- 来源类型；
- 标题；
- 简要描述；
- 文件/证据位置；
- 状态；
- 使用用途；
- hash 短值。

不得在列表直接展开完整网页或整份项目材料。

### 7.4 详情抽屉

点击 source/binding/unit：

- source：原始定位、摘要、证据；
- binding：用途和 instruction；
- unit：must_answer 和约束。

### 7.5 兼容 legacy

无 v2 plan 时显示：

```text
当前章节仍使用现有内部 WritingPlan。
新规划视图尚未对本工作空间启用。
```

正文、一键编写和右侧聊天完全可用。


## 8. 自动化测试


1. API contract 解析；
2. no plan 状态；
3. loading/error/stale；
4. tab 默认规则；
5. 切换 tab 不丢 editor dirty；
6. 切换章节取消旧请求；
7. SVG line recalculation；
8. source collapse；
9. 只读无 edit action；
10. legacy workbench regression；
11. batch banner regression；
12. frontend build。


## 9. 人工验收场景


- 打开旧工作空间，确认只读提示，正文功能不变。
- 打开 shadow plan 项目，查看三列图。
- 缩放浏览器和拖动左右栏，连接线保持正确。
- 正文做未保存编辑后切到编写逻辑再返回，内容不丢。
- 快速切换章节，不显示上一章 plan。
- 关闭 plan API，页面仍可编辑正文。


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


通过 Feature Flag 隐藏“编写逻辑”页签。

新增组件可保留，不影响旧 `ContentBlockEditor`。
不需要回滚后端表或 plan 数据。


## 13. Definition of Done


- [ ] 双页签可用；
- [ ] 图只读；
- [ ] legacy 行为不变；
- [ ] 未保存正文不丢；
- [ ] 快速切章不串数据；
- [ ] 所有前端测试通过；
- [ ] 完整 CI 全绿；
- [ ] PR-05 未开始。


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
你正在 `breezePeak/bid_agent` 仓库中实施 **PR-04：工作台编写逻辑与正文双页签只读展示**。请直接检查代码、补测试并完成实现，不要只输出方案。

计划起始基线：`85702e3aa60bd5e2f7b26a130ef7a6048499e020`

开始前必须执行：
1. `git status --short`
2. `git rev-parse HEAD`
3. `git log -5 --oneline`
4. 如果 HEAD 已晚于计划基线，先审查新提交对本 PR 的影响。
5. 不得覆盖与本任务无关的用户修改。

建议分支：`agent/pr-04-work`

开始条件：

- PR-03 已合并并全绿。
- shadow plan 在测试项目中稳定生成。
- plan read API 可返回严格 contract。


必须阅读：
- `docs/unified_writing_pr_plan_v2/00_START_HERE_当前只执行PR-00.md`
- `docs/unified_writing_pr_plan_v2/01_总纲_架构边界_依赖图_统一验收规则.md`
- `docs/unified_writing_pr_plan_v2/PR-04_工作台编写逻辑与正文双页签只读展示.md`
- 与本 PR 有关的 ADR、生产代码和现有测试

本 PR 唯一目标：

在当前章节工作台中增加“编写逻辑 / 正文”双页签和只读规划关系图。
本 PR 不允许用户修改或确认规划，也不改变当前写正文行为。


允许范围：

允许：

- 新增 plan read API contract；
- 新增前端 plan components；
- 增加双页签；
- 使用 CSS Grid + SVG 绘线；
- 显示 sources/content_units/bindings；
- 显示 shadow/current/stale/read-only 状态；
- 切换章节时加载 plan；
- 保留正文编辑器状态。

所有交互只读。


硬性禁止：

禁止：

- 规划编辑；
- 删除来源；
- 继续搜索；
- 确认规划；
- Writer gating；
- 改变“一键编写”；
- 改变批量；
- 旧投标书节点；
- 改写类型标签。


执行要求：
1. 先列出当前真实调用链、将修改的文件和风险点。
2. 先补能证明当前行为和目标行为的测试，再改实现。
3. 每完成一个小步骤立即运行对应局部测试。
4. Feature Flag 关闭时必须保持现有行为。
5. 旧测试与当前受支持行为冲突时，不得直接删除；必须给出证据并补替代测试。
6. 数据库迁移必须可重复、可升级旧工作空间、不可破坏原表。
7. 完成后运行全部门禁命令。
8. 最终提交本 PR 报告，然后停止，不得开始 PR-05。

必须运行：
python -m compileall -q src
ruff check src tests --quiet
python -m pytest tests -q --tb=line -k "not live_llm"
cd frontend
npm ci
npm test
npm run build

本 PR 特别要求：

- 本 PR 绝不增加确认按钮或修改动作。
- 必须测试 editor dirty 状态。
- 不引入第三方流程图库。


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


---

<!-- FILE: PR-05_规划编辑_来源选择_确认与失效机制.md -->

# PR-05：规划编辑、来源选择、搜索补充、确认与失效机制


- 计划版本：`PR Plan V2.0`
- 仓库：`breezePeak/bid_agent`
- 起始基线：`85702e3aa60bd5e2f7b26a130ef7a6048499e020`
- 开发原则：本 PR 独立可部署、可测试、可回滚


## 1. 开始条件


- PR-04 已合并并全绿。
- 只读规划图在 legacy 和 shadow 状态均稳定。
- Plan control-plane CAS/receipt 已通过。


不满足开始条件时，本 PR 立即停止。不得以“先做一部分不影响”为理由继续。

## 2. 本 PR 的唯一目标


开放结构化规划编辑、来源增删、继续搜索、内容块调整和用户确认。
确认只产生精确执行授权，仍不切换 Writer。


## 3. 当前代码事实


当前 shadow plan 可以展示，但用户无法改变。
当前聊天中的“修改 WritingPlan”是文本/JSON 投影修改，不具备 plan revision、
expected revision、来源级操作、exact confirmation、stale receipt 和服务重启一致性。


## 4. 本 PR 允许修改的范围


允许：

- 新增 plan edit commands；
- 新增 source search command；
- 新增 confirm command；
- 前端编辑来源、binding、unit；
- 状态与错误恢复；
- 规划版本历史；
- 依赖变化 stale；
- chat intent 调用同一命令服务。

Writer 仍按旧路径写。


## 5. 本 PR 明确不做


禁止：

- Writer 强制读取 confirmed plan；
- 关闭 inline research；
- bid_rewrite；
- legacy paragraph；
- 批量 v2 执行；
- 删除旧正文；
- 修改 Blueprint。


## 6. 预计文件、类、接口和表


| 命令 | 用途 |
|---|---|
| `chapter.plan.generate` | 生成新 draft revision |
| `chapter.plan.update` | 结构化修改 |
| `chapter.plan.search` | 补充搜索并生成新 revision |
| `chapter.plan.confirm` | 精确确认 |
| `chapter.plan.reopen` | 从 confirmed 派生新 draft |
| `chapter.plan.discard` | 放弃未确认 head |
| GET plan/history | 读取 |

前端增加 edit mode、source selector、continue search、content unit editor、
confirm button、revision drawer 和 conflict dialog。


文件清单是实施导航，不是让开发者不看代码就机械修改。实际文件有变化时，必须在 PR 描述中说明原因。

## 7. 详细实现顺序


### 7.1 结构化操作

`chapter.plan.update` 只接受操作列表：

```text
add_source
remove_source
bind_source
unbind_source
update_binding
add_content_unit
update_content_unit
move_content_unit
remove_content_unit
```

不得接受整份任意 JSON 覆盖。

### 7.2 校验

每次更新必须检查：

- source 是否存在；
- content unit 是否存在；
- binding 不悬空；
- required source 不得无故删除；
- requirement/score coverage 不降低；
- Blueprint 目的不被改写；
- 旧 plan expected revision 匹配；
- hash 重算。

### 7.3 继续搜索

用户输入搜索说明后：

1. 生成 research gap；
2. 校验不属于禁搜范围；
3. 执行 Provider；
4. 发布 Evidence；
5. 新建 source；
6. 绑定指定 content unit；
7. 追加 plan revision；
8. 不自动确认。

搜索失败保留旧 plan，不创建空 revision。

### 7.4 确认

确认按钮必须提交：

```text
expected_chapter_revision
plan_revision
plan_hash
dependency_fingerprint
```

成功后按钮变为“开始编写”，但本 PR 暂不切执行路径，Pilot Flag 仍关闭。

### 7.5 失效

以下变化使 plan stale：

- Blueprint；
- global context；
- chapter context；
- selected material block；
- selected evidence；
- SourceIndex；
- plan dependency schema/version。

未选择的无关资料变化不得让全部章节失效。

### 7.6 聊天兼容

用户在右侧说“把第二个要点拆开”“再查一下最新标准”“不要使用这个来源”时，
Agent 只能调用同一 plan command service，不能直接改 `_writing_plans.json`。


## 8. 自动化测试


1. 每种 edit op；
2. invalid source/unit；
3. coverage 降低阻断；
4. CAS conflict；
5. search success/failure；
6. prohibited search；
7. confirm exact hash；
8. stale receipt；
9. unrelated change 不 stale；
10. chat uses command service；
11. refresh/restart state；
12. revision history；
13. legacy direct write unchanged；
14. frontend conflict handling。


## 9. 人工验收场景


- 在 shadow plan 中删除一个可选来源。
- 把一个内容块拆成两个。
- 为一个块继续搜索。
- 刷新页面确认 revision 保留。
- 两个浏览器同时修改，后提交者收到冲突。
- 确认计划。
- 修改章节上下文，确认计划变 stale。
- 旧正文仍可按 legacy 路径生成。


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


关闭 plan edit UI 和 command capability。

已保存 revisions 保留只读。
legacy_inline Writer 不读取 receipt，不影响用户继续写。


## 13. Definition of Done


- [ ] 所有修改为结构化 command；
- [ ] 无悬空 binding；
- [ ] exact confirm；
- [ ] stale 精确传播；
- [ ] 搜索失败不损坏 plan；
- [ ] legacy Writer 未切换；
- [ ] 全 CI 通过；
- [ ] PR-06 未开始。


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
你正在 `breezePeak/bid_agent` 仓库中实施 **PR-05：规划编辑、来源选择、搜索补充、确认与失效机制**。请直接检查代码、补测试并完成实现，不要只输出方案。

计划起始基线：`85702e3aa60bd5e2f7b26a130ef7a6048499e020`

开始前必须执行：
1. `git status --short`
2. `git rev-parse HEAD`
3. `git log -5 --oneline`
4. 如果 HEAD 已晚于计划基线，先审查新提交对本 PR 的影响。
5. 不得覆盖与本任务无关的用户修改。

建议分支：`agent/pr-05-work`

开始条件：

- PR-04 已合并并全绿。
- 只读规划图在 legacy 和 shadow 状态均稳定。
- Plan control-plane CAS/receipt 已通过。


必须阅读：
- `docs/unified_writing_pr_plan_v2/00_START_HERE_当前只执行PR-00.md`
- `docs/unified_writing_pr_plan_v2/01_总纲_架构边界_依赖图_统一验收规则.md`
- `docs/unified_writing_pr_plan_v2/PR-05_规划编辑、来源选择、搜索补充、确认与失效机制.md`
- 与本 PR 有关的 ADR、生产代码和现有测试

本 PR 唯一目标：

开放结构化规划编辑、来源增删、继续搜索、内容块调整和用户确认。
确认只产生精确执行授权，仍不切换 Writer。


允许范围：

允许：

- 新增 plan edit commands；
- 新增 source search command；
- 新增 confirm command；
- 前端编辑来源、binding、unit；
- 状态与错误恢复；
- 规划版本历史；
- 依赖变化 stale；
- chat intent 调用同一命令服务。

Writer 仍按旧路径写。


硬性禁止：

禁止：

- Writer 强制读取 confirmed plan；
- 关闭 inline research；
- bid_rewrite；
- legacy paragraph；
- 批量 v2 执行；
- 删除旧正文；
- 修改 Blueprint。


执行要求：
1. 先列出当前真实调用链、将修改的文件和风险点。
2. 先补能证明当前行为和目标行为的测试，再改实现。
3. 每完成一个小步骤立即运行对应局部测试。
4. Feature Flag 关闭时必须保持现有行为。
5. 旧测试与当前受支持行为冲突时，不得直接删除；必须给出证据并补替代测试。
6. 数据库迁移必须可重复、可升级旧工作空间、不可破坏原表。
7. 完成后运行全部门禁命令。
8. 最终提交本 PR 报告，然后停止，不得开始 PR-06。

必须运行：
python -m compileall -q src
ruff check src tests --quiet
python -m pytest tests -q --tb=line -k "not live_llm"
cd frontend
npm ci
npm test
npm run build

本 PR 特别要求：

- 禁止直接 PUT 整份 plan JSON。
- 用户确认必须绑定 exact plan hash。
- 聊天和按钮必须共用同一 command service。


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


---

<!-- FILE: PR-06_Writer精确消费已确认规划_全量链路切换.md -->

# PR-06：Writer 精确消费已确认规划并切换全量编写链路


- 计划版本：`PR Plan V2.0`
- 仓库：`breezePeak/bid_agent`
- 起始基线：`85702e3aa60bd5e2f7b26a130ef7a6048499e020`
- 开发原则：本 PR 独立可部署、可测试、可回滚


## 1. 开始条件


- PR-05 已合并并全绿。
- 规划编辑、确认和 stale 已稳定。
- 至少 20 个测试章节完成 shadow 对比，没有系统性 coverage 退化。


不满足开始条件时，本 PR 立即停止。不得以“先做一部分不影响”为理由继续。

## 2. 本 PR 的唯一目标


让 `confirmed_plan_v2` 的全量编写章节只能通过已确认规划生成正文。
Writer 精确消费用户确认的来源，正文阶段不再搜索。
legacy_inline 工作空间继续沿用当前行为。


## 3. 当前代码事实


当前 `ChapterWritingService` 的顺序是：

```text
assemble bundle
→ apply request plan
→ inline research
→ ContentWriter
→ content gate
→ draft
```

当前 Bundle 只携带自由形态 plan，没有 receipt。
`WriterInputBundleAssembler._evidence_snapshot()` 会按 chapter/score/requirement 收集相关证据，
而不是按用户确认的 evidence IDs 精确收集。


## 4. 本 PR 允许修改的范围


允许：

- 扩展 WriterInputBundle；
- `ChapterWritingRequest` 增加 plan refs；
- `ChapterWritingService.require_confirmed_plan()`；
- Bundle assembler 精确 source/evidence；
- v2 flow 禁止 inline research；
- structured `chapter.plan.execute`；
- chat “开始写”调用同一 command；
- pilot workspace 切 confirmed_plan_v2；
- legacy adapter 保留。

本 PR 先支持单章全量编写。


## 5. 本 PR 明确不做


禁止：

- 删除 legacy_inline；
- 标书改写；
- legacy sources；
- v2 批量执行；
- Writer 自己搜索；
- Writer 读取整个 SourceIndex；
- Writer 使用未确认来源；
- 自动确认 plan。


## 6. 预计文件、类、接口和表


| 模块 | 变化 |
|---|---|
| `contracts.py` | Bundle plan exact fields |
| `writer_bundle.py` | `assemble_from_confirmed_plan` |
| `chapter_writing_service.py` | plan authority validation |
| `writer_research.py` | legacy adapter 保留，v2 不调用 |
| `content_writer.py` | 只读 frozen plan |
| `content_grounding.py` | source/evidence binding |
| `chapter_chat.py` | structured execute command |
| `execution_controller.py` | `chapter.plan.execute` |
| `v3_app.py` / frontend API | execute action |
| workbench | start button uses command，Pilot Flag |


文件清单是实施导航，不是让开发者不看代码就机械修改。实际文件有变化时，必须在 PR 描述中说明原因。

## 7. 详细实现顺序


### 7.1 Request

新增必需字段（v2 flow）：

```text
plan_revision
plan_hash
plan_approval_receipt_id
expected_chapter_revision
```

legacy flow 可为空。

### 7.2 Authority validation

服务必须重载：

- workspace flow；
- plan revision；
- plan hash；
- receipt；
- current dependencies；
- current confirmed pointer。

任何不一致返回明确 409。

### 7.3 Bundle assembly

Bundle 只包含：

- 当前 Blueprint slice；
- confirmed content units；
- confirmed source refs；
- exact SourceBlock snapshots；
- exact EvidenceItems；
- bindings；
- plan rewrite type；
- receipt。

用户从 plan 删除的 Evidence 不能因 topic 相关再次进入。

### 7.4 Research

v2 path 固定：

```text
run_research = False
```

Service 内明确断言：

- plan 已完成 research；
- required evidence 均可加载；
- 缺资料返回 `CHAPTER_PLAN_REVISION_REQUIRED`；
- 不允许 fallback 到 inline search。

legacy path 保留现有 coordinator 和 fallback。

### 7.5 开始编写按钮

新按钮提交 `chapter.plan.execute`。
不再把“开始编写本章正文”作为唯一控制信号交给 LLM 猜意图。

右侧聊天识别到开始写时，也调用同一 command。

### 7.6 Pilot

只有明确设置 `chapter_plan_flow=confirmed_plan_v2` 的 full_write 工作空间切新路径。
其他工作空间完全不变。

### 7.7 批量

本 PR 中 confirmed_plan_v2 的批量按钮默认禁用，并说明批量执行将在完成计划快照支持后开放。
legacy batch 不变。不得让 batch 绕过 plan gate。


## 8. 自动化测试


1. missing plan；
2. unconfirmed plan；
3. wrong hash；
4. stale dependency；
5. wrong receipt；
6. exact evidence selection；
7. removed evidence absent；
8. no inline research call；
9. insufficient plan returns revision required；
10. chat/button same command；
11. legacy direct write unchanged；
12. v2 single chapter success；
13. draft provenance includes plan refs；
14. content gate；
15. H2；
16. frontend disabled batch；
17. strict template v2 single chapter。


## 9. 人工验收场景


对同一项目复制两个 workspace：

- A：legacy_inline；
- B：confirmed_plan_v2。

执行相同章节：

1. A 按当前直接写作成功；
2. B 未确认 plan 时被阻断；
3. B 确认后成功；
4. B 写作阶段没有 research event；
5. B 正文只引用选中来源；
6. B H2 和 Word 正常；
7. A 与 B 的正文基础覆盖不低于基线。


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


将 Pilot workspace 的 `chapter_plan_flow` 切回 `legacy_inline`。

保留 plan revisions 和 receipts。
WriterInputBundle 新字段有兼容默认，旧 bundle 可读取。
不要删除 v2 数据。


## 13. Definition of Done


- [ ] v2 Writer 只消费 confirmed plan；
- [ ] v2 写作不联网；
- [ ] legacy path 不变；
- [ ] chat/button 共用结构化命令；
- [ ] 用户删除来源不会回来；
- [ ] v2 单章完整闭环；
- [ ] 全 CI 通过；
- [ ] PR-07 未开始。


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
你正在 `breezePeak/bid_agent` 仓库中实施 **PR-06：Writer 精确消费已确认规划并切换全量编写链路**。请直接检查代码、补测试并完成实现，不要只输出方案。

计划起始基线：`85702e3aa60bd5e2f7b26a130ef7a6048499e020`

开始前必须执行：
1. `git status --short`
2. `git rev-parse HEAD`
3. `git log -5 --oneline`
4. 如果 HEAD 已晚于计划基线，先审查新提交对本 PR 的影响。
5. 不得覆盖与本任务无关的用户修改。

建议分支：`agent/pr-06-writer`

开始条件：

- PR-05 已合并并全绿。
- 规划编辑、确认和 stale 已稳定。
- 至少 20 个测试章节完成 shadow 对比，没有系统性 coverage 退化。


必须阅读：
- `docs/unified_writing_pr_plan_v2/00_START_HERE_当前只执行PR-00.md`
- `docs/unified_writing_pr_plan_v2/01_总纲_架构边界_依赖图_统一验收规则.md`
- `docs/unified_writing_pr_plan_v2/PR-06_Writer 精确消费已确认规划并切换全量编写链路.md`
- 与本 PR 有关的 ADR、生产代码和现有测试

本 PR 唯一目标：

让 `confirmed_plan_v2` 的全量编写章节只能通过已确认规划生成正文。
Writer 精确消费用户确认的来源，正文阶段不再搜索。
legacy_inline 工作空间继续沿用当前行为。


允许范围：

允许：

- 扩展 WriterInputBundle；
- `ChapterWritingRequest` 增加 plan refs；
- `ChapterWritingService.require_confirmed_plan()`；
- Bundle assembler 精确 source/evidence；
- v2 flow 禁止 inline research；
- structured `chapter.plan.execute`；
- chat “开始写”调用同一 command；
- pilot workspace 切 confirmed_plan_v2；
- legacy adapter 保留。

本 PR 先支持单章全量编写。


硬性禁止：

禁止：

- 删除 legacy_inline；
- 标书改写；
- legacy sources；
- v2 批量执行；
- Writer 自己搜索；
- Writer 读取整个 SourceIndex；
- Writer 使用未确认来源；
- 自动确认 plan。


执行要求：
1. 先列出当前真实调用链、将修改的文件和风险点。
2. 先补能证明当前行为和目标行为的测试，再改实现。
3. 每完成一个小步骤立即运行对应局部测试。
4. Feature Flag 关闭时必须保持现有行为。
5. 旧测试与当前受支持行为冲突时，不得直接删除；必须给出证据并补替代测试。
6. 数据库迁移必须可重复、可升级旧工作空间、不可破坏原表。
7. 完成后运行全部门禁命令。
8. 最终提交本 PR 报告，然后停止，不得开始 PR-07。

必须运行：
python -m compileall -q src
ruff check src tests --quiet
python -m pytest tests -q --tb=line -k "not live_llm"
cd frontend
npm ci
npm test
npm run build

本 PR 特别要求：

- 必须证明 v2 path 没有调用 research coordinator。
- 必须保留 legacy_inline 回归。
- 不得在本 PR 开启 v2 batch。


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


---

<!-- FILE: PR-07_旧投标书解析_LegacyBidIndex与Markdown投影.md -->

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


---

<!-- FILE: PR-08_新旧目录融合与目录来源追踪.md -->

# PR-08：新招标要求与旧投标书目录融合及来源追踪


- 计划版本：`PR Plan V2.0`
- 仓库：`breezePeak/bid_agent`
- 起始基线：`85702e3aa60bd5e2f7b26a130ef7a6048499e020`
- 开发原则：本 PR 独立可部署、可测试、可回滚


## 1. 开始条件


- PR-07 已合并并全绿。
- LegacyBidIndex 可稳定晋级。
- 随机抽查旧目录和段落定位通过。


不满足开始条件时，本 PR 立即停止。不得以“先做一部分不影响”为理由继续。

## 2. 本 PR 的唯一目标


在自动目录模式下，根据新招标要求和评分责任生成目录，同时参考旧投标书的多级目录结构，
对旧标题执行保留、改名、合并、拆分或删除，并在 Blueprint 中保留来源追踪。


## 3. 当前代码事实


当前 `OutlineDecompositionInput` 只包含 RequirementLedger、ScoreModel、
TemplateStructureContract、document mode 和 feedback。

当前目录提示词明确限制只读取这些输入，因此模型看不到旧投标书目录。
新标书中一个评分句可能只生成一个标题，而旧标书已有成熟下级结构无法被参考。


## 4. 本 PR 允许修改的范围


允许：

- `legacy_outline_context`；
- outline candidate lineage refs；
- BlueprintNode outline_lineage；
- prompt/inference/capability version；
- G2 lineage validation；
- auto_outline 融合；
- template_strict 只做语义参考；
- H1 目录预览展示来源；
- 用户继续使用现有目录编辑和确认。

不做正文段落匹配。


## 5. 本 PR 明确不做


禁止：

- 直接复制旧目录；
- 旧目录覆盖新评分要求；
- strict template 改标题/顺序；
- 根据旧正文写新正文；
- 改写类型；
- 工作台旧段落图；
- Writer 读取旧标书。


## 6. 预计文件、类、接口和表


| 模块 | 变化 |
|---|---|
| `planning_inference.py` | Outline input/candidate |
| `inference_inputs.py` | exact legacy projection |
| `planning_agent.py` | 提供 LegacyBidIndex dependency |
| `prompts/v3_planning_agent_blueprint.md` | 融合规则 |
| `contracts.py` | OutlineLineageRef、BlueprintNode |
| `scoring_outline_policy.py` | lineage/coverage audit |
| `artifact_registry.py` | ChapterBlueprint optional dependency |
| H1 preview/API | 显示目录来源 |
| 前端规划页 | 目录节点来源详情 |


文件清单是实施导航，不是让开发者不看代码就机械修改。实际文件有变化时，必须在 PR 描述中说明原因。

## 7. 详细实现顺序


### 7.1 输入投影

目录模型只收到：

- old section ID；
- 编号；
- 标题；
- parent；
- order；
- description；
- answers；
- entity risk summary。

不把整本旧正文塞入目录模型。

### 7.2 融合优先级

```text
严格模板结构
> 新招标强制目录
> 新评分责任和 requirement coverage
> 旧目录候选结构
> 模型补充结构
```

### 7.3 处理动作

每个新节点 lineage decision：

```text
new_required
legacy_reused
legacy_adapted
legacy_merged
legacy_split
```

删除的旧节点不进入 Blueprint，但进入融合报告并说明原因。

### 7.4 示例规则

新要求：

```text
项目任务背景描述清楚
```

旧目录：

```text
2.1 项目任务背景
  2.1.1 年度调查新变化
  2.1.2 国土调查云平台支撑
  2.1.3 国家级核查工作背景
```

模型应判断下级结构是否仍适用，而不是机械只生成“项目任务背景”。

### 7.5 Gate

阻断：

- lineage 指向未知 old section；
- new requirement coverage 丢失；
- score unit 多 primary；
- strict template 改结构；
- 旧目录新增无依据承诺；
- 旧标题中的旧项目实体未处理。

review only：

- 标题改名幅度较大；
- 旧目录候选未采用；
- 多个旧节点合并；
- needs_review legacy section。

### 7.6 目录编辑

用户编辑后：

- lineage 可保留/删除；
- 新增用户节点标记 `user_added`；
- H1 确认 exact Blueprint；
- 后续章节计划以最终 Blueprint 为准。


## 8. 自动化测试


1. no legacy context 与当前目录结果兼容；
2. subheading reuse；
3. rename；
4. merge/split；
5. new requirement priority；
6. unknown lineage blocked；
7. strict template immutable；
8. score coverage；
9. old entity title risk；
10. deterministic order；
11. H1 exact snapshot；
12. edited directory lineage；
13. inference receipt rebuild；
14. old LegacyBidIndex stale invalidates candidate。


## 9. 人工验收场景


- 用用户截图对应结构制作 fixture。
- 新要求只有“项目任务背景描述清楚”。
- 旧目录有三级子标题。
- 检查新候选目录包含合理下级结构。
- 用户删除一个旧下级并新增“项目实施必要性”。
- 确认 H1。
- 严格模板项目重复测试，模板结构完全不变。


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


关闭 `BID_AGENT_LEGACY_OUTLINE_FUSION_ENABLED`。

bid_rewrite 可以阻止继续到工作台并提示功能维护，不得退回只按旧目录。
full_write 目录不读取 LegacyBidIndex。


## 13. Definition of Done


- [ ] 新要求始终优先；
- [ ] 旧目录仅作候选；
- [ ] 多级结构可复用；
- [ ] lineage 可追溯；
- [ ] strict template 不变；
- [ ] H1 正常；
- [ ] 全 CI 通过；
- [ ] PR-09 未开始。


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
你正在 `breezePeak/bid_agent` 仓库中实施 **PR-08：新招标要求与旧投标书目录融合及来源追踪**。请直接检查代码、补测试并完成实现，不要只输出方案。

计划起始基线：`85702e3aa60bd5e2f7b26a130ef7a6048499e020`

开始前必须执行：
1. `git status --short`
2. `git rev-parse HEAD`
3. `git log -5 --oneline`
4. 如果 HEAD 已晚于计划基线，先审查新提交对本 PR 的影响。
5. 不得覆盖与本任务无关的用户修改。

建议分支：`agent/pr-08-work`

开始条件：

- PR-07 已合并并全绿。
- LegacyBidIndex 可稳定晋级。
- 随机抽查旧目录和段落定位通过。


必须阅读：
- `docs/unified_writing_pr_plan_v2/00_START_HERE_当前只执行PR-00.md`
- `docs/unified_writing_pr_plan_v2/01_总纲_架构边界_依赖图_统一验收规则.md`
- `docs/unified_writing_pr_plan_v2/PR-08_新招标要求与旧投标书目录融合及来源追踪.md`
- 与本 PR 有关的 ADR、生产代码和现有测试

本 PR 唯一目标：

在自动目录模式下，根据新招标要求和评分责任生成目录，同时参考旧投标书的多级目录结构，
对旧标题执行保留、改名、合并、拆分或删除，并在 Blueprint 中保留来源追踪。


允许范围：

允许：

- `legacy_outline_context`；
- outline candidate lineage refs；
- BlueprintNode outline_lineage；
- prompt/inference/capability version；
- G2 lineage validation；
- auto_outline 融合；
- template_strict 只做语义参考；
- H1 目录预览展示来源；
- 用户继续使用现有目录编辑和确认。

不做正文段落匹配。


硬性禁止：

禁止：

- 直接复制旧目录；
- 旧目录覆盖新评分要求；
- strict template 改标题/顺序；
- 根据旧正文写新正文；
- 改写类型；
- 工作台旧段落图；
- Writer 读取旧标书。


执行要求：
1. 先列出当前真实调用链、将修改的文件和风险点。
2. 先补能证明当前行为和目标行为的测试，再改实现。
3. 每完成一个小步骤立即运行对应局部测试。
4. Feature Flag 关闭时必须保持现有行为。
5. 旧测试与当前受支持行为冲突时，不得直接删除；必须给出证据并补替代测试。
6. 数据库迁移必须可重复、可升级旧工作空间、不可破坏原表。
7. 完成后运行全部门禁命令。
8. 最终提交本 PR 报告，然后停止，不得开始 PR-09。

必须运行：
python -m compileall -q src
ruff check src tests --quiet
python -m pytest tests -q --tb=line -k "not live_llm"
cd frontend
npm ci
npm test
npm run build

本 PR 特别要求：

- 必须提供新要求单标题 + 旧目录多级子标题的 Golden。
- 旧目录不能降低 Score/Requirement coverage。
- strict template 测试必须逐节点对比。


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


---

<!-- FILE: PR-09_旧段落匹配_改写类型_污染检测.md -->

# PR-09：旧投标书段落匹配、改写类型与污染检测


- 计划版本：`PR Plan V2.0`
- 仓库：`breezePeak/bid_agent`
- 起始基线：`85702e3aa60bd5e2f7b26a130ef7a6048499e020`
- 开发原则：本 PR 独立可部署、可测试、可回滚


## 1. 开始条件


- PR-08 已合并并全绿。
- bid_rewrite 目录可以生成、编辑和 H1 确认。
- LegacyBidIndex 与 Blueprint lineage 均为 current。


不满足开始条件时，本 PR 立即停止。不得以“先做一部分不影响”为理由继续。

## 2. 本 PR 的唯一目标


为每个新叶子章节检索旧投标书具体段落，绑定到规划内容块，按最终规划动态计算
“全部搬用、简单修改、理解重组、重新编写”，并在写作前阻断旧项目污染风险。


## 3. 当前代码事实


当前 plan v2 已支持 sources/units/bindings，但没有 legacy source provider。
LegacyBidIndex 已提供章节和段落语义，SourceIndex 提供原文。

改写类型不能在目录阶段写死，因为用户可能在规划阶段增加公开资料、删除旧段落或改变重组方式。


## 4. 本 PR 允许修改的范围


允许：

- LegacyBidSourceProvider；
- 候选检索和 rerank；
- exact SourceBlock selection；
- paragraph preview；
- dynamic rewrite type；
- pollution finding；
- plan graph legacy nodes；
- 用户增删旧段落；
- plan confirmation；
- Writer Bundle legacy snapshots 的 assembly 测试。

不开放完整改写入口给普通用户。


## 5. 本 PR 明确不做


禁止：

- 整本旧投标书传给 Writer；
- 只用摘要写正文；
- 自动把旧项目实体替换为猜测值；
- 目录标题标签写入正式 title；
- 未确认 plan 写正文；
- 批量改写。


## 6. 预计文件、类、接口和表


| 模块 | 变化 |
|---|---|
| 新增 `legacy_bid_source_provider.py` | 检索候选 |
| 新增 `rewrite_strategy.py` | 动态分类 |
| 新增 `legacy_pollution_gate.py` | 污染检查 |
| `chapter_writing_plan_builder.py` | legacy source/binding |
| `chapter_writing_plan.py` | edit/confirm validation |
| `writer_bundle.py` | exact SourceBlock snapshot |
| plan API | paragraph preview/search |
| 前端 graph | old chapter/paragraph cards |
| chapter list summary | rewrite_type |


文件清单是实施导航，不是让开发者不看代码就机械修改。实际文件有变化时，必须在 PR 描述中说明原因。

## 7. 详细实现顺序


### 7.1 候选召回

输入：

- 新章节标题、purpose、must_answer；
- requirement/score；
- Blueprint lineage；
- old section title/description；
- paragraph description。

先召回，再读取候选原文 rerank。
不能仅凭标题或摘要最终选择。

### 7.2 选择粒度

支持：

```text
整章全部 block
章节内连续 block range
离散 paragraph/list/table blocks
多个旧章节组合
```

每个选择保存：

```text
input_id
section_id
block_id
content_hash
usage_scope
instruction
```

### 7.3 改写类型

**全部搬用 copy**

- 一个主要旧章节；
- 完整主体；
- 无实质结构变化；
- 无新增公开/项目内容；
- 无 unresolved pollution。

**简单修改 light_edit**

- 一个旧章节为主体；
- 替换项目事实；
- 删除旧实体；
- 少量补充；
- 结构基本不变。

**理解重组 restructure**

- 多旧章节；
- 拆分、合并、重排；
- 多来源共同构成主体。

**重新编写 new_write**

- 没有采用旧正文；
- 主要由新要求、项目资料和公开证据写。

父节点 `mixed` 仅为 UI 汇总，不能作为叶子策略。

### 7.4 污染 Gate

确认 plan 前输出 finding：

```text
old_project_name
old_purchaser
old_location
old_date_or_year
old_duration
old_person
old_quantity
old_product_version
old_standard_version
old_commitment
```

处理动作：

```text
remove
replace_from_confirmed_fact
retain_as_general_background
needs_human
```

缺少新事实时不能自动替换。

### 7.5 规划图

旧来源卡默认按章节折叠，显示：

```text
旧 2.1.2 国土调查云平台支撑
已选择 6 个段落
```

展开后显示 block 编号和描述，点击可预览原文。

### 7.6 类型更新

任何 source/binding 变化后重算 rewrite_type。
标签来自 plan summary，不能手工只改标签。


## 8. 自动化测试


1. title recall；
2. semantic recall；
3. rerank reads original；
4. full section selection；
5. partial block；
6. multi-section；
7. stale content_hash；
8. copy classification；
9. light_edit classification；
10. restructure；
11. new_write；
12. parent mixed；
13. pollution types；
14. missing replacement blocks confirm；
15. exact source Bundle；
16. UI label not title；
17. export title unchanged。


## 9. 人工验收场景


- 对四个章节分别构造四种策略。
- 查看每章旧段落来源和原文。
- 删除一个来源，类型实时变化。
- 增加公开政策来源，copy 变 light_edit。
- 输入包含旧采购人和旧年份，确认前出现风险。
- 没有新项目年份时不能自动替换。
- 最终确认四份 plan。


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


关闭 legacy source provider 和 rewrite UI。

已确认 plan 保留只读，但 bid_rewrite 不允许执行正文，提示功能关闭。
full_write 不受影响。


## 13. Definition of Done


- [ ] 最终判断读取原文；
- [ ] exact block/hash；
- [ ] 四类策略稳定；
- [ ] 污染 finding 完整；
- [ ] 缺事实不自动替换；
- [ ] 标签不进入 title；
- [ ] 全 CI 通过；
- [ ] PR-10 未开始。


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
你正在 `breezePeak/bid_agent` 仓库中实施 **PR-09：旧投标书段落匹配、改写类型与污染检测**。请直接检查代码、补测试并完成实现，不要只输出方案。

计划起始基线：`85702e3aa60bd5e2f7b26a130ef7a6048499e020`

开始前必须执行：
1. `git status --short`
2. `git rev-parse HEAD`
3. `git log -5 --oneline`
4. 如果 HEAD 已晚于计划基线，先审查新提交对本 PR 的影响。
5. 不得覆盖与本任务无关的用户修改。

建议分支：`agent/pr-09-work`

开始条件：

- PR-08 已合并并全绿。
- bid_rewrite 目录可以生成、编辑和 H1 确认。
- LegacyBidIndex 与 Blueprint lineage 均为 current。


必须阅读：
- `docs/unified_writing_pr_plan_v2/00_START_HERE_当前只执行PR-00.md`
- `docs/unified_writing_pr_plan_v2/01_总纲_架构边界_依赖图_统一验收规则.md`
- `docs/unified_writing_pr_plan_v2/PR-09_旧投标书段落匹配、改写类型与污染检测.md`
- 与本 PR 有关的 ADR、生产代码和现有测试

本 PR 唯一目标：

为每个新叶子章节检索旧投标书具体段落，绑定到规划内容块，按最终规划动态计算
“全部搬用、简单修改、理解重组、重新编写”，并在写作前阻断旧项目污染风险。


允许范围：

允许：

- LegacyBidSourceProvider；
- 候选检索和 rerank；
- exact SourceBlock selection；
- paragraph preview；
- dynamic rewrite type；
- pollution finding；
- plan graph legacy nodes；
- 用户增删旧段落；
- plan confirmation；
- Writer Bundle legacy snapshots 的 assembly 测试。

不开放完整改写入口给普通用户。


硬性禁止：

禁止：

- 整本旧投标书传给 Writer；
- 只用摘要写正文；
- 自动把旧项目实体替换为猜测值；
- 目录标题标签写入正式 title；
- 未确认 plan 写正文；
- 批量改写。


执行要求：
1. 先列出当前真实调用链、将修改的文件和风险点。
2. 先补能证明当前行为和目标行为的测试，再改实现。
3. 每完成一个小步骤立即运行对应局部测试。
4. Feature Flag 关闭时必须保持现有行为。
5. 旧测试与当前受支持行为冲突时，不得直接删除；必须给出证据并补替代测试。
6. 数据库迁移必须可重复、可升级旧工作空间、不可破坏原表。
7. 完成后运行全部门禁命令。
8. 最终提交本 PR 报告，然后停止，不得开始 PR-10。

必须运行：
python -m compileall -q src
ruff check src tests --quiet
python -m pytest tests -q --tb=line -k "not live_llm"
cd frontend
npm ci
npm test
npm run build

本 PR 特别要求：

- 不能只用描述匹配，最终 rerank 必须读取原始 SourceBlock。
- 每种 rewrite type 都要有正反例测试。
- 污染 Gate 必须在 plan confirm 前阻断 unresolved finding。


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


---

<!-- FILE: PR-10_标书改写工作台完整闭环.md -->

# PR-10：标书改写工作台完整闭环


- 计划版本：`PR Plan V2.0`
- 仓库：`breezePeak/bid_agent`
- 起始基线：`85702e3aa60bd5e2f7b26a130ef7a6048499e020`
- 开发原则：本 PR 独立可部署、可测试、可回滚


## 1. 开始条件


- PR-09 已合并并全绿。
- 四类改写规划可确认。
- exact legacy SourceBlock Bundle 测试通过。
- 污染 Gate 通过真实 fixture。


不满足开始条件时，本 PR 立即停止。不得以“先做一部分不影响”为理由继续。

## 2. 本 PR 的唯一目标


向用户开放标书改写模式，完成从创建项目、上传新旧文档、融合目录、逐章规划确认、
逐章开始编写、正文编辑到 H2 确认的完整闭环。


## 3. 当前代码事实


前置 PR 已分别建立模式、Plan、LegacyBidIndex、目录融合和段落匹配，
但尚未作为完整产品入口开放。

当前工作台仍需要整合创建模式选择、新旧文件上传提示、旧标书预处理进度、
目录来源、旧段落规划图、章节改写标签、confirmed plan execute 和正文切换。


## 4. 本 PR 允许修改的范围


允许：

- 创建对话框显示全量编写/标书改写；
- bid_rewrite 上传流程；
- preprocessing progress；
- 融合目录确认；
- 工作台完整 plan graph；
- plan edit/confirm；
- per-chapter execute；
- 四类执行；
- UI 标签；
- 正文/H2；
- 完整 E2E。

只支持逐章编写。


## 5. 本 PR 明确不做


禁止：

- 进入工作台前批量写正文；
- 自动写所有章节；
- 未确认 plan 写作；
- v2 bid-rewrite batch；
- 标签进入 Word/MD；
- 旧内容进入 ProjectModel；
- 新增 RewriteWriter。


## 6. 预计文件、类、接口和表


| 层 | 变化 |
|---|---|
| 创建 UI | 模式卡片 |
| 上传 UI | 新招标书/旧投标书分区 |
| Pipeline UI | legacy parse/index/outline fusion progress |
| 目录 UI | lineage 来源 |
| 工作台 | logic/content tabs、legacy graph、标签 |
| Commands | plan generate/search/update/confirm/execute |
| Writer strategy | copy/light_edit/restructure/new_write dispatcher，仍在统一 Service |
| H2/Word | 复用当前 |
| E2E | full rewrite flow |


文件清单是实施导航，不是让开发者不看代码就机械修改。实际文件有变化时，必须在 PR 描述中说明原因。

## 7. 详细实现顺序


### 7.1 创建

选择全量编写或标书改写。
创建后 writing_mode 不允许随意切换；已上传或已规划项目禁止切换，避免状态污染。

### 7.2 上传

bid_rewrite 页面明确要求：

- 新招标书；
- 评分文件可独立或包含在新招标书；
- 至少一份旧投标书；
- 其他项目资料可选。

文件角色由用户确认，不按扩展名猜。

### 7.3 预处理

显示：

```text
新标书解析
旧标书结构恢复
旧标书语义索引
目录融合候选
等待目录确认
```

失败可重试单阶段，不重跑已 current Artifact。

### 7.4 进入工作台

目录 H1 后物化章节。进入时不写正文。

叶子章节默认“编写逻辑”：

- 旧章节/段落；
- 项目资料；
- 搜索来源；
- 内容块；
- 连接线；
- 改写类型；
- 风险；
- 确认状态。

### 7.5 开始编写

只有 current confirmed plan 可执行。

策略执行仍通过统一服务：

```text
copy:
  复制 exact blocks + 格式标准化 + grounding/pollution final gate

light_edit:
  Writer 以旧内容为基础，按 binding 修改

restructure:
  Writer 按 content unit 顺序重组多个来源

new_write:
  与 full_write confirmed plan 相同
```

copy 不应无意义调用模型重写，但仍通过统一 `ChapterWritingService` 策略分派、
内容 Gate 和 Draft commit。

### 7.6 正文

生成后自动切到正文，保留逻辑页可随时查看。

用户可编辑、删除、局部重写、按 current confirmed plan 整章重写并 H2 确认。

### 7.7 标签

叶子显示：

```text
【全部搬用】
【简单修改】
【理解重组】
【重新编写】
```

父节点不同子类型显示 `【混合处理】`。
标签是 UI 元数据，不修改 `title`。


## 8. 自动化测试


1. 创建两种模式；
2. upload role validation；
3. missing old bid；
4. preprocessing resume；
5. directory confirm；
6. workbench no auto-write；
7. four plan types；
8. confirm required；
9. copy strategy；
10. light edit；
11. restructure；
12. new write；
13. pollution final gate；
14. tab switch；
15. H2；
16. label projection；
17. export no labels；
18. full_write regression；
19. legacy_inline regression；
20. service restart。


## 9. 人工验收场景


完整 E2E：

1. 创建标书改写项目；
2. 上传新招标书；
3. 上传旧投标书；
4. 等待 LegacyBidIndex；
5. 查看融合目录；
6. 修改目录；
7. H1；
8. 进入工作台；
9. 逐章查看四种规划；
10. 给一章补搜索来源；
11. 确认计划；
12. 开始编写；
13. 检查正文；
14. 编辑；
15. H2；
16. 导出 Word；
17. 搜索最终 Word 不含旧采购人、旧项目名和修改类型标签。

另跑 full_write E2E，确保完全正常。


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


关闭 `BID_AGENT_BID_REWRITE_ENABLED`，阻止新建和执行。

已存在 bid_rewrite workspace 保留只读和导出已确认正文；
不得悄悄按 full_write 执行。
full_write 不受影响。


## 13. Definition of Done


- [ ] bid_rewrite 完整单章闭环；
- [ ] 进入工作台不自动写；
- [ ] 每章先 plan 后正文；
- [ ] 四类策略正确；
- [ ] 污染不进入输出；
- [ ] 标签不进入导出；
- [ ] full_write 全回归；
- [ ] 全 CI 通过；
- [ ] PR-11 未开始。


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
你正在 `breezePeak/bid_agent` 仓库中实施 **PR-10：标书改写工作台完整闭环**。请直接检查代码、补测试并完成实现，不要只输出方案。

计划起始基线：`85702e3aa60bd5e2f7b26a130ef7a6048499e020`

开始前必须执行：
1. `git status --short`
2. `git rev-parse HEAD`
3. `git log -5 --oneline`
4. 如果 HEAD 已晚于计划基线，先审查新提交对本 PR 的影响。
5. 不得覆盖与本任务无关的用户修改。

建议分支：`agent/pr-10-work`

开始条件：

- PR-09 已合并并全绿。
- 四类改写规划可确认。
- exact legacy SourceBlock Bundle 测试通过。
- 污染 Gate 通过真实 fixture。


必须阅读：
- `docs/unified_writing_pr_plan_v2/00_START_HERE_当前只执行PR-00.md`
- `docs/unified_writing_pr_plan_v2/01_总纲_架构边界_依赖图_统一验收规则.md`
- `docs/unified_writing_pr_plan_v2/PR-10_标书改写工作台完整闭环.md`
- 与本 PR 有关的 ADR、生产代码和现有测试

本 PR 唯一目标：

向用户开放标书改写模式，完成从创建项目、上传新旧文档、融合目录、逐章规划确认、
逐章开始编写、正文编辑到 H2 确认的完整闭环。


允许范围：

允许：

- 创建对话框显示全量编写/标书改写；
- bid_rewrite 上传流程；
- preprocessing progress；
- 融合目录确认；
- 工作台完整 plan graph；
- plan edit/confirm；
- per-chapter execute；
- 四类执行；
- UI 标签；
- 正文/H2；
- 完整 E2E。

只支持逐章编写。


硬性禁止：

禁止：

- 进入工作台前批量写正文；
- 自动写所有章节；
- 未确认 plan 写作；
- v2 bid-rewrite batch；
- 标签进入 Word/MD；
- 旧内容进入 ProjectModel；
- 新增 RewriteWriter。


执行要求：
1. 先列出当前真实调用链、将修改的文件和风险点。
2. 先补能证明当前行为和目标行为的测试，再改实现。
3. 每完成一个小步骤立即运行对应局部测试。
4. Feature Flag 关闭时必须保持现有行为。
5. 旧测试与当前受支持行为冲突时，不得直接删除；必须给出证据并补替代测试。
6. 数据库迁移必须可重复、可升级旧工作空间、不可破坏原表。
7. 完成后运行全部门禁命令。
8. 最终提交本 PR 报告，然后停止，不得开始 PR-11。

必须运行：
python -m compileall -q src
ruff check src tests --quiet
python -m pytest tests -q --tb=line -k "not live_llm"
cd frontend
npm ci
npm test
npm run build

本 PR 特别要求：

- 本 PR 必须交付完整可用闭环，不能留下“下个 PR 再补接口”。
- 暂不开放 bid-rewrite batch。
- Copy 也必须经过统一 Service、Gate 和 draft commit。


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


---

<!-- FILE: PR-11_批量编写_陈旧传播_恢复与导出隔离.md -->

# PR-11：批量编写、陈旧传播、断点恢复与导出隔离


- 计划版本：`PR Plan V2.0`
- 仓库：`breezePeak/bid_agent`
- 起始基线：`85702e3aa60bd5e2f7b26a130ef7a6048499e020`
- 开发原则：本 PR 独立可部署、可测试、可回滚


## 1. 开始条件


- PR-10 已合并并全绿。
- full_write 和 bid_rewrite 单章 E2E 通过。
- 生产式重启测试通过。


不满足开始条件时，本 PR 立即停止。不得以“先做一部分不影响”为理由继续。

## 2. 本 PR 的唯一目标


让 confirmed_plan_v2 支持安全批量编写、精确陈旧传播、断点恢复和导出元数据隔离，
同时保持每个批量项绑定自己的 confirmed plan 快照。


## 3. 当前代码事实


当前批量任务只保存 chapter context ref，并在 Worker 内直接创建 ChapterWritingRequest。
它没有 plan revision/hash、receipt、selected sources、evidence snapshot、rewrite type 和 plan stale check。

直接复用会绕过用户确认。


## 4. 本 PR 允许修改的范围


允许：

- batch item 保存 plan refs；
- 创建 batch 时 gate；
- Worker exact plan reload；
- checkpoint；
- stale propagation；
- retry/replan；
- full_write/bid_rewrite v2 batch；
- UI 只选择 confirmed current 章节；
- export label isolation tests；
- recovery telemetry。


## 5. 本 PR 明确不做


禁止：

- 自动确认未确认 plan；
- batch 中临时搜索；
- stale plan 继续；
- 因一章失败覆盖其他章；
- 标签进入导出；
- 删除 legacy batch 兼容。


## 6. 预计文件、类、接口和表


| 模块 | 变化 |
|---|---|
| `control_plane.py` | batch item plan refs/迁移 |
| `chapter_batch.py` | preflight/execute/recovery |
| `chapter_writing_plan.py` | bulk current validation |
| `chapter_writing_service.py` | batch actor + plan refs |
| `workspace_snapshot.py` | per-item plan status |
| `ChapterWorkbenchView.vue` | selectable confirmed chapters |
| render/export | metadata isolation |
| tests | crash/restart/stale/partial failure |


文件清单是实施导航，不是让开发者不看代码就机械修改。实际文件有变化时，必须在 PR 描述中说明原因。

## 7. 详细实现顺序


### 7.1 创建批量任务

展开叶子章节后逐章检查：

```text
materialized
plan confirmed
plan current
receipt valid
chapter context current
no blocking pollution
```

任何不满足的章节不进入 job，返回明确列表，用户先生成或确认计划。

### 7.2 Batch item 快照

保存：

```text
chapter_id
plan_revision
plan_hash
plan_receipt_id
dependency_fingerprint
rewrite_type
chapter_context_ref
before_content_revision
```

### 7.3 Worker

每个 item：

```text
preflight
→ exact plan validation
→ no research
→ write
→ draft commit
→ checkpoint
```

下一章只能在上一章产生新 content revision 后开始。

### 7.4 断点恢复

服务重启：

- claimed running item 有 fencing token；
- 从最后 durable checkpoint 恢复；
- 已提交 content revision 不重复写；
- 未提交 Writer output 重新执行；
- idempotency key 包含 plan hash。

### 7.5 陈旧传播

依赖变化后：

- 未开始 item：标记 plan_stale，暂停；
- 正在运行 item：在 commit 前二次校验；
- 已成功 item：现有 draft 标记 provenance old，不自动删除；
- 用户重新 plan 后可 retry 单项。

### 7.6 导出隔离

最终标题来源永远是 Blueprint/DocumentContract。
以下不能写进导出：

- rewrite_type label；
- plan status；
- old section number；
- source card；
- internal hash；
- “混合处理”。

### 7.7 legacy batch

legacy_inline batch 继续 current behavior。
不得强迫旧 workspace 补 plan。


## 8. 自动化测试


1. batch excludes unconfirmed；
2. stale blocked；
3. exact receipt；
4. no research；
5. sequential revision；
6. partial failure；
7. retry item；
8. restart recovery；
9. idempotent commit；
10. fencing；
11. dependency changes mid-write；
12. full_write batch；
13. bid_rewrite mixed strategies；
14. legacy batch；
15. export title exact；
16. MD/Word no labels；
17. large batch performance。


## 9. 人工验收场景


- 选择 10 个章节，其中 2 个未确认，确认 UI 只允许 8 个。
- 批量执行到第 4 章时重启服务。
- 确认恢复且前 3 章不重复。
- 第 6 章修改全局事实，任务暂停并提示 stale。
- 重新规划、确认、retry。
- 导出 Word，搜索所有内部标签和旧章节编号。


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


关闭 v2 batch capability。

已有 job 可暂停；已生成 draft 保留。
legacy batch 仍可用。
数据库新增字段保留。


## 13. Definition of Done


- [ ] batch 每项 exact plan；
- [ ] 无确认不入队；
- [ ] 重启不重复；
- [ ] stale 不继续；
- [ ] 单项可恢复；
- [ ] 导出零内部标签；
- [ ] legacy batch 正常；
- [ ] 全 CI 通过；
- [ ] PR-12 未开始。


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
你正在 `breezePeak/bid_agent` 仓库中实施 **PR-11：批量编写、陈旧传播、断点恢复与导出隔离**。请直接检查代码、补测试并完成实现，不要只输出方案。

计划起始基线：`85702e3aa60bd5e2f7b26a130ef7a6048499e020`

开始前必须执行：
1. `git status --short`
2. `git rev-parse HEAD`
3. `git log -5 --oneline`
4. 如果 HEAD 已晚于计划基线，先审查新提交对本 PR 的影响。
5. 不得覆盖与本任务无关的用户修改。

建议分支：`agent/pr-11-work`

开始条件：

- PR-10 已合并并全绿。
- full_write 和 bid_rewrite 单章 E2E 通过。
- 生产式重启测试通过。


必须阅读：
- `docs/unified_writing_pr_plan_v2/00_START_HERE_当前只执行PR-00.md`
- `docs/unified_writing_pr_plan_v2/01_总纲_架构边界_依赖图_统一验收规则.md`
- `docs/unified_writing_pr_plan_v2/PR-11_批量编写、陈旧传播、断点恢复与导出隔离.md`
- 与本 PR 有关的 ADR、生产代码和现有测试

本 PR 唯一目标：

让 confirmed_plan_v2 支持安全批量编写、精确陈旧传播、断点恢复和导出元数据隔离，
同时保持每个批量项绑定自己的 confirmed plan 快照。


允许范围：

允许：

- batch item 保存 plan refs；
- 创建 batch 时 gate；
- Worker exact plan reload；
- checkpoint；
- stale propagation；
- retry/replan；
- full_write/bid_rewrite v2 batch；
- UI 只选择 confirmed current 章节；
- export label isolation tests；
- recovery telemetry。


硬性禁止：

禁止：

- 自动确认未确认 plan；
- batch 中临时搜索；
- stale plan 继续；
- 因一章失败覆盖其他章；
- 标签进入导出；
- 删除 legacy batch 兼容。


执行要求：
1. 先列出当前真实调用链、将修改的文件和风险点。
2. 先补能证明当前行为和目标行为的测试，再改实现。
3. 每完成一个小步骤立即运行对应局部测试。
4. Feature Flag 关闭时必须保持现有行为。
5. 旧测试与当前受支持行为冲突时，不得直接删除；必须给出证据并补替代测试。
6. 数据库迁移必须可重复、可升级旧工作空间、不可破坏原表。
7. 完成后运行全部门禁命令。
8. 最终提交本 PR 报告，然后停止，不得开始 PR-12。

必须运行：
python -m compileall -q src
ruff check src tests --quiet
python -m pytest tests -q --tb=line -k "not live_llm"
cd frontend
npm ci
npm test
npm run build

本 PR 特别要求：

- 批量 Worker 绝不能在没有 plan refs 时对 v2 workspace 写作。
- 必须做真实进程重启测试，不只 mock。
- 导出隔离必须解压 DOCX 检查 XML。


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


---

<!-- FILE: PR-12_真实项目验收_迁移_灰度与正式切换.md -->

# PR-12：真实项目验收、迁移演练、灰度发布与正式切换


- 计划版本：`PR Plan V2.0`
- 仓库：`breezePeak/bid_agent`
- 起始基线：`85702e3aa60bd5e2f7b26a130ef7a6048499e020`
- 开发原则：本 PR 独立可部署、可测试、可回滚


## 1. 开始条件


- PR-11 已合并并全绿。
- 单章和批量两种模式均完成 E2E。
- 所有新功能仍可通过 Feature Flag 回退。


不满足开始条件时，本 PR 立即停止。不得以“先做一部分不影响”为理由继续。

## 2. 本 PR 的唯一目标


使用真实脱敏项目完成最终业务验收、旧工作空间迁移、故障注入、性能基准和灰度发布。
只有全部 Gate 通过后，才把 confirmed_plan_v2 设为新工作空间默认。


## 3. 当前代码事实


此前 PR 证明组件正确，本 PR 要证明产品闭环可用。
不能因为单元测试通过就宣布完成，也不能拿模型生成了一个 DOCX 当成功标准。


## 4. 本 PR 允许修改的范围


允许：

- Golden/holdout 样本；
- 迁移工具；
- 灰度 capability；
- metrics/alerts；
- performance；
- fault injection；
- default switch；
- 运维文档；
- release evidence；
- 旧计划文档正式 supersede 标记。

不删除 legacy compatibility。


## 5. 本 PR 明确不做


禁止：

- CI 未绿发布；
- 只测一个项目；
- 用开发样本当 holdout；
- 自动迁移所有旧工作空间到 v2；
- 删除 legacy_inline；
- 删除旧 plan JSON；
- 忽略 Word 逐页检查；
- 将 review finding 当通过。


## 6. 预计文件、类、接口和表


| 交付 | 内容 |
|---|---|
| Golden | full_write + bid_rewrite |
| Holdout | 未参与开发的项目 |
| Migration CLI | dry-run/backup/apply/verify |
| Metrics | plan/search/write/stale/batch/export |
| Alerts | error rate、stale、rollback |
| Runbook | 发布、回滚、事故 |
| Release manifest | commit/schema/flags/results |
| Default switch | 仅新工作空间 |


文件清单是实施导航，不是让开发者不看代码就机械修改。实际文件有变化时，必须在 PR 描述中说明原因。

## 7. 详细实现顺序


### 7.1 样本集

至少：

- 2 个 full_write 不同领域；
- 2 个 bid_rewrite 不同旧标书结构；
- 1 个 strict template；
- 1 个大型目录；
- 1 个 PDF/OCR 边界；
- 2 个 holdout。

### 7.2 业务指标

评审：

- 招标要求覆盖；
- 评分条件覆盖；
- 旧目录结构利用合理性；
- 旧段落采用正确性；
- 旧项目污染；
- 重复；
- 空话；
- 用户人工修改量；
- 最终 Word 可用性。

### 7.3 迁移工具

`scripts/migrate_chapter_plan_v2.py`：

```text
--dry-run
--backup
--workspace
--apply
--verify
```

旧 workspace 默认不改变 flow。
只有用户显式迁移才切 confirmed_plan_v2。

### 7.4 故障注入

模拟：

- LLM timeout/invalid JSON；
- Tavily timeout/no source；
- SQLite lock；
- process kill；
- browser disconnect；
- stale plan；
- evidence removed；
- old bid replaced；
- batch crash；
- DOCX render failure。

每项都有可恢复路径。

### 7.5 性能

记录 old bid parse、semantic index、outline fusion、plan generation、search、Writer、
graph render、batch throughput 和 DB growth，并设置支持上限和超限提示。

### 7.6 灰度

顺序：

```text
内部 workspace
→ 1 个 full_write pilot
→ 1 个 bid_rewrite pilot
→ 10% 新 workspace
→ 50%
→ 100% 新 workspace
```

旧 workspace 不自动切。

### 7.7 正式切换条件

- CI 连续 5 次全绿；
- 关键指标无回归；
- 两种模式人工验收通过；
- zero critical pollution；
- rollback 演练通过；
- on-call runbook 完成；
- release manifest 签字。

### 7.8 观察期

至少观察一个完整投标周期。
观察期内保留 legacy_inline 和 Feature Flag。


## 8. 自动化测试


1. migration dry-run；
2. backup restore；
3. old workspace no-change；
4. explicit migration；
5. fault matrix；
6. performance thresholds；
7. metric emission；
8. alert；
9. rollout flag；
10. rollback；
11. golden；
12. holdout；
13. DOCX XML/visual；
14. full CI multiple runs。


## 9. 人工验收场景


真实验收必须由不参与实现的人按清单操作：

- 建项目；
- 上传；
- 目录；
- 规划；
- 搜索；
- 改写；
- 编辑；
- H2；
- 批量；
- 导出；
- 恢复。

最终 Word 逐页检查标题、编号、表格、页眉页脚、目录、样式、旧项目污染、
内部标签、空白页和截断。


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


1. 关闭 default flag；
2. 新 workspace 回到 legacy_inline/full_write；
3. bid_rewrite 新建关闭；
4. 已有 v2 workspace 保留只读/已确认正文；
5. 按 backup 恢复迁移 workspace；
6. 不删新表；
7. 保留审计证据。


## 13. Definition of Done


- [ ] Golden/holdout 通过；
- [ ] 迁移演练通过；
- [ ] 回滚演练通过；
- [ ] 故障注入通过；
- [ ] 性能在阈值；
- [ ] Word 逐页通过；
- [ ] CI 连续 5 次全绿；
- [ ] 新 workspace 默认切换；
- [ ] 旧 workspace 不自动迁移；
- [ ] 观察期方案就绪；
- [ ] 项目正式完成。


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
你正在 `breezePeak/bid_agent` 仓库中实施 **PR-12：真实项目验收、迁移演练、灰度发布与正式切换**。请直接检查代码、补测试并完成实现，不要只输出方案。

计划起始基线：`85702e3aa60bd5e2f7b26a130ef7a6048499e020`

开始前必须执行：
1. `git status --short`
2. `git rev-parse HEAD`
3. `git log -5 --oneline`
4. 如果 HEAD 已晚于计划基线，先审查新提交对本 PR 的影响。
5. 不得覆盖与本任务无关的用户修改。

建议分支：`agent/pr-12-work`

开始条件：

- PR-11 已合并并全绿。
- 单章和批量两种模式均完成 E2E。
- 所有新功能仍可通过 Feature Flag 回退。


必须阅读：
- `docs/unified_writing_pr_plan_v2/00_START_HERE_当前只执行PR-00.md`
- `docs/unified_writing_pr_plan_v2/01_总纲_架构边界_依赖图_统一验收规则.md`
- `docs/unified_writing_pr_plan_v2/PR-12_真实项目验收、迁移演练、灰度发布与正式切换.md`
- 与本 PR 有关的 ADR、生产代码和现有测试

本 PR 唯一目标：

使用真实脱敏项目完成最终业务验收、旧工作空间迁移、故障注入、性能基准和灰度发布。
只有全部 Gate 通过后，才把 confirmed_plan_v2 设为新工作空间默认。


允许范围：

允许：

- Golden/holdout 样本；
- 迁移工具；
- 灰度 capability；
- metrics/alerts；
- performance；
- fault injection；
- default switch；
- 运维文档；
- release evidence；
- 旧计划文档正式 supersede 标记。

不删除 legacy compatibility。


硬性禁止：

禁止：

- CI 未绿发布；
- 只测一个项目；
- 用开发样本当 holdout；
- 自动迁移所有旧工作空间到 v2；
- 删除 legacy_inline；
- 删除旧 plan JSON；
- 忽略 Word 逐页检查；
- 将 review finding 当通过。


执行要求：
1. 先列出当前真实调用链、将修改的文件和风险点。
2. 先补能证明当前行为和目标行为的测试，再改实现。
3. 每完成一个小步骤立即运行对应局部测试。
4. Feature Flag 关闭时必须保持现有行为。
5. 旧测试与当前受支持行为冲突时，不得直接删除；必须给出证据并补替代测试。
6. 数据库迁移必须可重复、可升级旧工作空间、不可破坏原表。
7. 完成后运行全部门禁命令。
8. 最终提交本 PR 报告，然后停止，不得开始 任何后续开发。

必须运行：
python -m compileall -q src
ruff check src tests --quiet
python -m pytest tests -q --tb=line -k "not live_llm"
cd frontend
npm ci
npm test
npm run build

本 PR 特别要求：

- 本 PR 以验收和发布为主，不得顺便重构核心链路。
- 必须有独立 holdout。
- 默认切换前必须提供回滚演练证据。


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


---

<!-- FILE: CODEX_PR-00_启动提示词.md -->

# 给 Codex 的第一次开发提示词

下面整段直接复制给 Codex。当前只执行 PR-00。

```text
你正在 `breezePeak/bid_agent` 仓库中开发。请直接检查代码、修复测试和生产回归并提交一个独立 PR，不要只给方案。

当前计划基线：
`85702e3aa60bd5e2f7b26a130ef7a6048499e020`

当前事实：
- 旧开发计划基于 `74cd1ff12a79a373f9c262d48f61e03caa3cd642`，已经过期。
- 最新 main 合并了 PR #10。
- PR #10 的 static 和 frontend job 通过，但 unit job 失败。
- 已知结果为 29 failed、492 passed、4 skipped、44 subtests passed。
- 当前不具备开始新功能开发的可信基线。

本轮唯一任务：
实施 `PR-00：修复当前主分支并冻结可信基线`。

完成 PR-00 后立即停止。禁止实施 PR-01 或任何标书改写新功能。

第一步必须执行：
1. `git status --short`
2. `git rev-parse HEAD`
3. `git log -5 --oneline`
4. 如果 HEAD 不是 `85702e3aa60bd5e2f7b26a130ef7a6048499e020` 或其可证明无冲突的后续提交，先列出差异并判断是否需要停止。
5. 不得覆盖与本任务无关的用户修改。

必须阅读：
- `docs/unified_writing_pr_plan_v2/00_START_HERE_当前只执行PR-00.md`
- `docs/unified_writing_pr_plan_v2/01_总纲_架构边界_依赖图_统一验收规则.md`
- `docs/unified_writing_pr_plan_v2/PR-00_修复当前主分支并冻结可信基线.md`
- `docs/adr/`
- 当前生产代码和全部相关测试

建议分支：
`agent/pr-00-green-baseline`

PR-00 必须完成：

一、完整复现
1. 本地运行与 CI 相同的全部命令。
2. 输出完整失败列表。
3. 对照 GitHub Actions Run `32453280179`。
4. 建立失败分类表，逐项标记：
   - production_regression
   - test_isolation_defect
   - stale_test_after_accepted_change
5. stale test 修改必须提供代码/ADR证据和替代测试。

二、处理已知失败类别
1. 确定性研究测试不得访问 Tavily 或真实网络。
2. 修复章节批量写作测试注入或生产编排回归。
3. 恢复当前受支持的章节确认开关语义。
4. 修复 Grounding 错误分类。
5. 修复 Python 单测对 frontend/dist 的错误依赖。
6. 修复 stage/generation snapshot 兼容。
7. 修复 planning、outline audit、template/source error 分类。
8. 同步 Artifact Registry 版本冻结。
9. 修复 Requirement/ProjectModel 输入结构。
10. 修复 strict template H1 依赖。
11. 修复 visual/diagram orientation。
12. 不得通过 skip、xfail、删测试或放宽断言掩盖问题。

三、冻结可信基线
1. 新增 `scripts/verify_pr00_baseline.py`。
2. 新增 `docs/unified_writing_pr_plan_v2/evidence/PR-00-baseline.md`。
3. 基线脚本检查：
   - 关键模块导入；
   - ControlStore 旧 Schema 升级；
   - ChapterWritingService 是唯一写作入口；
   - 确定性测试无网络；
   - stage registry/snapshot 一致；
   - 章节确认开关；
   - 当前 API 关键 contract；
   - Word 导出模块。
4. 记录当前全量编写真实调用链。

四、完整回归
必须运行：

python -m compileall -q src
ruff check src tests --quiet
python -m pytest tests -q --tb=line -k "not live_llm"

cd frontend
npm ci
npm test
npm run build

cd ..
python scripts/verify_pr00_baseline.py

完整 Python 测试必须连续通过两次，排除顺序和偶发状态问题。

还必须执行当前业务回归：
1. 创建 full_write 工作空间。
2. 上传招标书/评分文件。
3. 生成目录并 H1。
4. 打开章节。
5. 查看当前内部 WritingPlan。
6. 直接开始写正文。
7. 检查当前 inline research 判断。
8. 正文编辑。
9. H2。
10. 批量编写并模拟服务重启。
11. 当前 Word 导出。
12. strict template 路径。

硬性禁止：
- 不新增 writing_mode。
- 不新增 LEGACY_BID。
- 不新增 plan control tables。
- 不新增 plan approval receipt。
- 不改成“规划确认后才能写”。
- 不增加工作台双页签。
- 不把搜索前移。
- 不开发标书改写。
- 不开始 PR-01。

最终报告必须包含：
1. HEAD 和分支。
2. 29 个失败的逐项分类与处理结果。
3. 修改文件。
4. 每个测试修改的理由。
5. 所有命令真实结果。
6. 业务回归结果。
7. 基线脚本结果。
8. 回滚方法。
9. PR-00 Definition of Done。
10. 明确写出：`PR-01 未开始`。

完成后停止。
```


---

<!-- FILE: 99_旧计划废止与版本记录.md -->

# 旧计划废止与版本记录

## 1. 当前有效计划

有效目录：

```text
docs/unified_writing_pr_plan_v2/
```

有效基线：

```text
85702e3aa60bd5e2f7b26a130ef7a6048499e020
```

当前有效执行顺序：

```text
PR-00 → PR-01 → ... → PR-12
```

## 2. 被替代的旧计划

仓库中的旧开发计划基于：

```text
74cd1ff12a79a373f9c262d48f61e03caa3cd642
```

它们不应继续作为执行依据。

原因：

1. PR #10 已重写 WritingPlan、搜索和章节对话逻辑；
2. 当前 main 的行为与旧现状分析不一致；
3. 当前 CI 尚未全绿；
4. 旧计划的 PR-00 不能代表最新基线；
5. 新计划必须先修复当前主分支。

建议在旧计划首页增加：

```text
状态：Superseded
替代计划：docs/unified_writing_pr_plan_v2/00_START_HERE_当前只执行PR-00.md
```

不要立即删除旧计划，以保留历史决策，但禁止 Codex 混读两个计划。

## 3. 版本记录

| 版本 | 基线 | 说明 |
|---|---|---|
| 旧版 | `74cd1ff12a79a373f9c262d48f61e03caa3cd642` | 按技术分册，已失效 |
| PR Plan V2.0 | `85702e3aa60bd5e2f7b26a130ef7a6048499e020` | 按 PR，自 PR-00 红色基线修复开始 |

## 4. 后续基线更新规则

每个 PR 合并后：

1. 在下一 PR 执行前记录新的 main SHA；
2. Codex 开始时检查 HEAD；
3. 若期间存在额外提交，先做影响分析；
4. 计划与代码冲突时，修改当前/后续 PR 文档；
5. 已完成 PR 的验收证据不可重写；
6. 不得因为基线变化跳过门禁。
