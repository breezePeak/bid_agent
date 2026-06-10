# 标书写作 Agent

本项目是一个本地 CLI 版标书写作 Agent，使用 LangGraph 编排主流程和章节 SubAgent。支持 PDF/DOCX/MD 原始资料导入，自动生成评分标准分离和投标文件内容，并通过多 Agent 并发章节写作最终输出 Markdown 和 Word。

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 初始化项目
python src/main.py init

# （可选）生成演示数据，用于快速验证流程
python src/main.py init-demo

# 配置 API（复制 .env.example 为 .env 并填写）
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=your-api-key
OPENAI_MODEL=gpt-4.1-mini

# 放入原始资料
#   招标文件  → sources/tender/
#   公司资料  → sources/company/
#   标书模板  → sources/template/

# 导入资料并检查
python src/main.py prepare-inputs
python src/main.py validate

# 运行完整流程
python src/main.py graph-run --workers 2
```

## 推荐运行流程

```bash
python src/main.py init
python src/main.py init-demo
# 将招标文件放入 sources/tender/
# 将公司资料放入 sources/company/
# 将模板放入 sources/template/
python src/main.py prepare-inputs
python src/main.py validate
python src/main.py graph-run --workers 2
```

> **注意：** `init-demo` 仅用于开发测试，生成的演示数据不代表任何真实招标项目或企业，不可用作真实投标资料。

## 目录结构

```
bid_agent/
  sources/                        ← 用户放入原始资料
    tender/                       ← 招标文件、答疑、补遗 (.pdf/.docx/.md)
    company/                      ← 公司资料、案例、产品白皮书 (.pdf/.docx/.md)
    template/                     ← 标书模板 (.docx)

  inputs/                         ← prepare-inputs 自动生成
    tender.md
    score.md
    company.md
    template.docx

  workspace/
    imported/                     ← 资料导入中间产物
      tender_raw.md               ← 招标文件原始合并
      tender_blocks.json          ← 招标文件切块
      tender_classified_blocks.json  ← AI 分类后的块
      tender_other.md             ← 其他内容（非招标正文、非评分标准）
      tender_classification_report.json ← 分类报告
    chunks/                       ← 文档切分结果
      tender_chunks.json
      company_chunks.json
    jobs/                         ← 章节任务包 (01.json, 02.json, ...)
    contexts/                     ← 上下文选择结果 (01_context.json, ...)
    chapters/                     ← 已生成章节 (01.md, 02.md, ...)
    reviews/                      ← 章节审核结果 (01_review.json, ...)
    score_points.json
    global_facts.json
    outline.json
    global_review.json

  outputs/
    final.md
    final.docx

  prompts/                        ← 提示词模板
    parse_score.md
    extract_facts.md
    generate_outline.md
    write_chapter.md
    review_chapter.md
    select_context.md
    global_review.md
    classify_tender_blocks.md

  src/                            ← 源代码
    main.py                       ← CLI 入口
    demo_initializer.py           ← 演示数据初始化
    config.py                     ← API 配置 + 切块/分类全局常量
    llm_client.py                 ← 统一 LLM 调用
    utils.py                      ← 文件读写、JSON 解析
    document_converter.py         ← PDF/DOCX → Markdown 转换
    input_preparer.py             ← 统筹资料导入流程
    tender_extractor.py           ← 招标文件导入 + 切块 + AI 分类 + 拼接
    company_extractor.py          ← 公司资料批量导入
    document_splitter.py          ← 文档切分为 chunk
    job_planner.py                ← 章节任务包生成
    context_selector.py           ← 章节上下文选择（含 chunk-ranker 本地粗筛）
    chunk_ranker.py               ← 本地关键词 chunk 相关性排序
    subagent_runner.py            ← SubAgent 并发调度
    project_validator.py          ← 项目闭环检查
    score_parser.py
    fact_extractor.py
    outline_generator.py
    chapter_writer.py
    chapter_reviewer.py
    chapter_rewriter.py           ← 章节自动改稿
    global_reviewer.py
    docx_builder.py
    agents/                       ← Agent 薄封装层
    graph/                        ← LangGraph 编排层
      state.py
      nodes.py
      routers.py
      bid_graph.py
      chapter_subgraph.py

  .env                            ← API 配置
  requirements.txt
  readme.MD
```

## .env 配置

```env
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=your-api-key
OPENAI_MODEL=gpt-4.1-mini
OPENAI_TIMEOUT=120
OPENAI_MAX_RETRIES=3
```

## CLI 命令说明

| 命令 | 说明 |
|---|---|
| `init` | 初始化目录、输入文件和默认提示词 |
| `init-demo` | 生成最小演示招标文件和公司资料（仅用于开发测试） |
| `prepare-inputs` | 导入原始资料：将 sources/ 下的 PDF/DOCX/MD 转为 inputs/ 下标准文件 |
| `validate` | 项目功能闭环检查：验证文件、环境变量、中间产物完整性（不调用 AI） |
| `split-docs` | 切分招标文件和公司资料为 chunk |
| `parse-score` | 解析评分标准 → score_points.json |
| `extract-facts` | 提取全局事实 → global_facts.json |
| `generate-outline` | 生成标书大纲 → outline.json |
| `plan-jobs` | 生成章节任务包 → jobs/*.json |
| `select-context --chapter 01` | 为单个章节选择上下文 |
| `select-context-all` | 为所有章节选择上下文 |
| `write-chapter --chapter 01` | 生成单个章节 |
| `write-all --workers 2` | 并发生成所有章节（--workers 默认 2，最大 5） |
| `review-chapter --chapter 01` | 审核单个章节 |
| `review-all` | 串行审核所有章节 |
| `rewrite-chapter --chapter 01` | 根据审核意见重写单个章节 |
| `rewrite-all` | 重写所有 need_rewrite=true 的章节 |
| `review-fix-all` | 审核所有章节并自动改稿（最多 2 轮） |
| `summarize-chapter --chapter 01` | 为单个章节生成结构化摘要 |
| `summarize-all` | 为所有章节生成结构化摘要 |
| `global-review` | 全文一致性审核（优先使用章节摘要） |
| `build-md` | 拼接最终 Markdown → outputs/final.md |
| `build-docx` | 生成 Word 文件 → outputs/final.docx |
| `run --workers 2` | CLI 模式完整流水线（13 步） |
| `graph-run --workers 2` | LangGraph 主图完整流程 |

## 自动改稿流程

`review-fix-all` 命令会自动审核所有章节，并对 need_rewrite=true 的章节进行重写，然后再次审核，最多 2 轮。

```bash
python src/main.py write-all --workers 2
python src/main.py review-fix-all
python src/main.py build-md
python src/main.py build-docx
```

说明：
- `review-fix-all` 会先审核章节，对 need_rewrite=true 的章节自动重写
- 重写后会重新审核，每章最多重写 2 轮
- 重写日志保存在 `workspace/rewrites/*_rewrite_log.json`
- 如果没有需要重写的章节，命令正常结束并打印"无需重写"

## LangGraph 流程

```text
START
  → init_workspace
  → prepare_inputs
  → split_docs
  → parse_score
  → extract_facts
  → generate_outline
  → plan_chapter_jobs
  → select_contexts
  → write_chapters (章节子图：load_job → load_context → write → self_check → save)
  → review_chapters
  → summarize_chapters (为每章生成结构化摘要)
  → global_review (基于章节摘要做全文审核)
  → build_markdown
  → build_docx
  → END
```

## 输出

```
workspace/imported/tender_raw.md
workspace/imported/tender_blocks.json
workspace/imported/tender_classified_blocks.json
workspace/imported/tender_other.md
workspace/imported/tender_classification_report.json
workspace/chunks/tender_chunks.json
workspace/chunks/company_chunks.json
workspace/score_points.json
workspace/global_facts.json
workspace/outline.json
workspace/jobs/*.json
workspace/contexts/*_context.json
workspace/contexts/*_ranked_chunks.json
workspace/chapters/*.md
workspace/reviews/*.json
workspace/rewrites/*_rewrite_log.json
workspace/summaries/*_summary.json
workspace/global_review.json
outputs/final.md
outputs/final.docx
```

## Chunk Ranker（本地粗筛）

`select-context` 阶段在调用 LLM 之前，先通过 `chunk_ranker.py` 对全量 chunks 做本地关键词粗筛，只将 top 30 个相关 chunks 交给 LLM 选择。这样可以支持大型招标文件（300+ 页、500+ 页），避免 prompt 超长。

- 排序结果调试文件：`workspace/contexts/*_ranked_chunks.json`
- 排序基于章节标题、评分点关键词、正文关键词匹配的加权评分
- 如果 ranker 出错，自动回退到全量 chunks，不影响流程

## 常见问题

**Q: 评分标准没识别到怎么办？**
A: 执行 `validate` 会提示 score_blocks=0。检查 `workspace/imported/tender_classification_report.json` 中的 warnings，并可手动编辑 `inputs/score.md`。

**Q: 章节生成失败如何补救？**
A: 失败章节会在控制台输出 ID，确认原因后可使用 `write-chapter --chapter XX` 单章重跑。

**Q: 目录不存在？**
A: 先执行 `python src/main.py init` 初始化所有目录。

**Q: .env 未配置？**
A: 复制 `.env.example` 为 `.env` 并填写 API 信息后重新运行。

## 当前开发状态

| 功能 | 状态 |
|---|---|
| 基础 CLI 和文件读写 | 已完成 |
| LLM 调用与评分解析 | 已完成 |
| 资料导入 (prepare-inputs) | 已完成 |
| LangGraph 主流程 | 已完成 |
| job/context 章节 SubAgent 并发写作 | 已完成 |
| validate 项目检查 | 已完成 |
| 招标文件 AI 块分类 | 已完成（待进一步优化） |
| init-demo | 已完成 |
| chunk-ranker | 已完成 |
| chapter-summary + global-review 优化 | 已完成 |
| review-fix-all 自动改稿 | 已完成 |
| retry/resume 断点续跑 | 待开发 |

## 下一步计划

1. **retry/resume**：支持失败章节重试和已生成章节的断点续跑。
