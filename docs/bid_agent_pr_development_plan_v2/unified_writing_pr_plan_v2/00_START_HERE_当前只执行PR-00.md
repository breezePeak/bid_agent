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
