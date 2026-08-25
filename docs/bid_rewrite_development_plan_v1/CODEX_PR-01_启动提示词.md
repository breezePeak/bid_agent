# Codex 启动提示词：PR-01

你正在 `breezePeak/bid_agent` 仓库实施：

```text
PR-01：改写项目入口、旧标书上传与解析预览
```

这次开发的产品目标是：**在现有全量编写功能旁边新增“标书改写”，不是重构全量编写。**

## 开始前

必须执行并记录：

```bash
git status --short
git rev-parse HEAD
git log -10 --oneline
```

计划基线：

```text
dbacba49b75aeb185091b12df44d1f08aa64fdc2
```

如果 HEAD 已变化，先审查新提交，不得覆盖用户修改。

建议分支：

```text
codex/bid-rewrite-pr-01
```

## 必须阅读

```text
docs/bid_rewrite_v1/00_START_HERE_标书改写重新设计.md
docs/bid_rewrite_v1/01_总纲_只新增标书改写_保护全量编写.md
docs/bid_rewrite_v1/02_当前代码基线与扩展点.md
docs/bid_rewrite_v1/PR-01_改写项目入口_旧标书上传_解析预览.md
```

原目录：

```text
docs/bid_agent_pr_development_plan_v2/
docs/unified_writing_pr_plan_v2/
```

只作为已回退历史，不得继续按其中 PR-01～PR-12 实施。

## 本 PR 唯一目标

完成可实际操作的纵向闭环：

```text
创建“标书改写”工作空间
→ 上传一份旧投标书
→ 独立解析
→ 页面查看旧目录和段落原文
```

本 PR 不生成新目录、不做章节匹配、不修改 Writer、不写正文。

## 硬性架构规则

1. 原有 `full_write` 为默认值。
2. 旧工作空间读取为 `full_write`。
3. 旧投标书不得进入现有 `InputManifest`。
4. 旧投标书不得进入现有 `SourceIndex`。
5. 旧投标书使用专用上传接口和独立存储。
6. 允许复用当前结构解析代码，但输出为 `LegacyBidIndex`。
7. 所有控制状态写入经过受控 Service/CommandGateway。
8. 不得新增 Plan v2。
9. 不得修改原 `ChapterWritingService`、`ContentWriter`、内部 WritingPlan 行为。
10. 前后端必须在同一个 PR 中完成。

## 先做测试，再实现

测试至少覆盖：

- 创建默认 full_write；
- 创建 bid_rewrite；
- 旧 workspace 兼容；
- full_write 不显示旧标书上传；
- 通用 uploads 拒绝 legacy bid；
- legacy 文件不在 InputManifest；
- legacy 文件不在 SourceIndex；
- LegacyBidIndex 稳定 ID/hash；
- 目录和段落预览；
- 替换后 stale/rebuild；
- 前端创建模式和上传状态；
- 原 full_write 创建、上传、页面回归。

## 必须运行

```bash
python -m compileall -q src
ruff check src tests --quiet
python -m pytest tests -q --tb=line -k "not live_llm"

cd frontend
npm ci
npm test
npm run build
```

## 人工验收

必须真实启动前后端并完成：

1. 创建 full_write，确认旧页面行为不变；
2. 创建 bid_rewrite；
3. 上传旧 DOCX；
4. 展开目录；
5. 对照原文抽查 10 个段落；
6. 刷新恢复；
7. 替换旧文件；
8. 回到 full_write，确认无改写 UI。

## 提交与 PR

- 禁止直接推 main；
- 提交到 `codex/bid-rewrite-pr-01`；
- 创建正式 GitHub PR；
- PR 描述列出真实测试结果和人工验收；
- CI 未全绿不得合并；
- 完成后停止，不得开始 PR-02。
