# 标书写作 Agent 开发计划

## 1. 项目目标

> 跑通"招标文件 / 评分标准 / 公司资料 / 标书模板 → 生成章节 → 审核章节 → 拼接 Word"的主流程。

整体流程：

```text
原始输入资料 (PDF/DOCX/MD)
        ↓
资料导入层 → 转换为标准 MD 并落入 inputs/
        ↓
招标文件按标题切块 + AI 分类 → score.md / tender.md / other.md
        ↓
招标文件切分为 chunk
        ↓
解析评分点
        ↓
提取全局事实
        ↓
生成标书大纲
        ↓
章节任务规划 + 上下文选择
        ↓
SubAgent 并发章节写作
        ↓
逐章审核评分点覆盖情况
        ↓
全文一致性审核
        ↓
拼接 Markdown
        ↓
生成 Word 文件
```

---

## 2. 开发阶段

### 阶段 1：基础 CLI 和文件读写 — 已完成

- `init` 命令：创建目录结构，初始化输入文件占位和默认提示词
- 统一的文件读写工具 `utils.py`
- `.env` 配置读取 `config.py`
- 基础 CLI 框架 `main.py`（argparse）

### 阶段 2：LLM 调用与评分解析 — 已完成

- `llm_client.py`：统一的 OpenAI-compatible API 调用，支持重试
- `score_parser.py`：解析 `inputs/score.md` → `workspace/score_points.json`
- `fact_extractor.py`：从招标文件 + 公司资料提取全局事实 → `workspace/global_facts.json`
- `outline_generator.py`：根据评分点生成标书大纲 → `workspace/outline.json`
- 提示词模板 `prompts/*.md`

### 阶段 3：资料导入 (prepare-inputs) — 已完成

- `document_converter.py`：PDF/DOCX/MD → Markdown 转换
- `tender_extractor.py`：招标文件导入 → 切块 → AI 分类 → 拼接 score.md/tender.md/other.md
- `company_extractor.py`：公司资料批量合并
- `input_preparer.py`：统筹 sources/ → inputs/ 完整流程
- 保留 `workspace/imported/tender_raw.md` 原始合并文件

### 阶段 4：LangGraph 主流程 — 已完成

- `graph/state.py`：BidState + ChapterState TypedDict
- `graph/nodes.py`：13 个图节点函数
- `graph/routers.py`：路由函数
- `graph/bid_graph.py`：主图构建 + `graph-run` 命令
- 完整 13 节点线性流程，无分支/条件

### 阶段 5：job/context 章节 SubAgent — 已完成

- `document_splitter.py`：按标题切分文档为 chunk（max 3500 chars）
- `job_planner.py`：从大纲生成章节任务包 → `workspace/jobs/*.json`
- `context_selector.py`：LLM 为每章选择相关 chunk → `workspace/contexts/*_context.json`
- `chapter_writer.py` / `chapter_reviewer.py`：章节写作 + 审核
- `global_reviewer.py`：全文一致性审核
- `subagent_runner.py`：ThreadPoolExecutor 并发章节写作
- `graph/chapter_subgraph.py`：章节子图 (load_job → load_context → write → self_check → save)
- `agents/`：Agent 薄封装层（7 个 agent）
- `docx_builder.py`：Markdown → Word 转换

### 阶段 6：validate 项目检查 — 已完成

- `project_validator.py`：26 项闭环检查
  - sources/ 目录及文件
  - inputs/ 文件存在性及非空
  - prompts/ 提示词完整性
  - .env / 环境变量配置
  - workspace/ 中间产物状态
  - outputs/ 最终产物状态
- 结果等级：ok / warn / fail
- fail 返回非 0 退出码

### 阶段 7：init-demo — 已完成

- 新增 `init-demo` 命令，一键生成演示招标文件和公司资料
- 新增 `src/demo_initializer.py`，实现 `init_demo(root)` 函数
- 演示文件已存在时跳过，不覆盖
- 降低新用户首次体验门槛

### 阶段 8：chunk-ranker — 已完成

- 新增 `src/chunk_ranker.py`，实现本地关键词 chunk 相关性排序
- `rank_chunks_for_job()` 从 job + score_points 提取关键词，对 chunks 加权评分
- `context_selector.py` 集成 chunk-ranker，LLM 选择前先用 top 30 粗筛
- 排序结果写入 `workspace/contexts/{chapter_id}_ranked_chunks.json`
- 支持大型招标文件（300+ 页），避免 prompt 超长
- ranker 失败时自动回退到全量 chunks

### 阶段 9：AI tender block classifier — 已完成

- `tender_extractor.py` 实现完整的"代码切块 + AI 分类 + 拼接输出"流程
- `split_tender_into_blocks()` 按标题切块，生成 rule_hints
- `classify_tender_blocks_with_ai()` 批量 AI 分类（每批 12 个 block）
- `assemble_inputs_from_classified_blocks()` 拼接 score.md / tender.md / tender_other.md
- `fallback_classify_blocks()` 规则兜底，失败批次自动降级
- `_generate_classification_report()` 生成分类报告含 warnings
- 提示词 `prompts/classify_tender_blocks.md` 内置分类规则
- 分类精度后续可按阶段 9b 继续优化

### 阶段 10：chapter-summary + global-review 优化 — 待开发

- 为每章生成结构化的内容摘要
- 后续章节写作时注入前序章节摘要，避免内容重复和事实冲突
- `global_reviewer.py` 强化的全文一致性审核

### 阶段 11：retry/resume — 待开发

- 失败章节重试：`write-chapter --chapter XX`（已有基础支持）
- 断点续跑：`graph-run --resume`，跳过已完成章节，只生成失败/缺失章节
- 失败原因持久化记录

---

## 3. 技术选型

- **语言**：Python
- **运行方式**：CLI 命令 `python src/main.py <command>`
- **大模型接口**：OpenAI-compatible API，统一通过 `llm_client.py` 调用
- **编排框架**：LangGraph
- **Word 生成**：python-docx
- **PDF 解析**：pdfplumber / PyMuPDF
- **DOCX 解析**：python-docx

---

## 4. 目录结构

```text
bid_agent/
  sources/                        ← 用户放入原始资料
    tender/                       ← .pdf / .docx / .md
    company/                      ← .pdf / .docx / .md
    template/                     ← .docx

  inputs/                         ← prepare-inputs 自动生成
    tender.md
    score.md
    company.md
    template.docx

  workspace/
    imported/
      tender_raw.md               ← 招标文件原始合并
      tender_blocks.json          ← 切块结果
      tender_classified_blocks.json ← AI 分类后分类块
      tender_other.md             ← 非招标正文、非评分标准内容
      tender_classification_report.json ← 分类报告
    chunks/
      tender_chunks.json
      company_chunks.json
    jobs/                         ← 01.json, 02.json, ...
    contexts/                     ← 01_context.json, 02_context.json, ...
    chapters/                     ← 01.md, 02.md, ...
    reviews/                      ← 01_review.json, ...
    score_points.json
    global_facts.json
    outline.json
    global_review.json

  outputs/
    final.md
    final.docx

  prompts/
    parse_score.md
    extract_facts.md
    generate_outline.md
    write_chapter.md
    review_chapter.md
    select_context.md
    global_review.md
    classify_tender_blocks.md

  src/
    main.py
    demo_initializer.py
    config.py
    llm_client.py
    utils.py
    document_converter.py
    input_preparer.py
    tender_extractor.py
    company_extractor.py
    document_splitter.py
    job_planner.py
    context_selector.py
    chunk_ranker.py
    subagent_runner.py
    project_validator.py
    score_parser.py
    fact_extractor.py
    outline_generator.py
    chapter_writer.py
    chapter_reviewer.py
    global_reviewer.py
    docx_builder.py
    agents/
    graph/

  .env
  requirements.txt
  readme.MD
```

---

## 5. 输入文件

### sources/ → inputs/ 资料导入

用户只需将原始资料放入 `sources/` 对应目录：

```text
sources/tender/     ← 招标文件 (.pdf/.docx/.md)
sources/company/    ← 公司资料 (.pdf/.docx/.md)
sources/template/   ← 标书模板 (.docx)

        ↓ python src/main.py prepare-inputs

inputs/tender.md      ← 自动合并 + AI 分类
inputs/score.md       ← AI 分类的评分标准
inputs/company.md     ← 自动合并
inputs/template.docx  ← 复制第一个 .docx
```

支持的原始文件格式：`.md`（直接读取）、`.docx`（python-docx 提取段落和表格）、`.pdf`（pdfplumber 或 PyMuPDF 提取文本）。

### inputs/tender.md

招标文件正文内容。由 `prepare-inputs` 从 `sources/tender/` 自动生成，经 AI 分类后提取非评分、非附录类内容。

### inputs/score.md

评分标准内容。由 `prepare-inputs` 经 AI 块分类识别，将评分相关块提取到此文件。

### inputs/company.md

公司资料、产品资料、案例资料、人员资料、资质资料合并。由 `prepare-inputs` 自动生成。章节生成时不能编造公司没有提供的内容。

---

## 6. CLI 命令

| 命令 | 说明 |
|---|---|
| `init` | 初始化目录、输入文件和默认提示词 |
| `init-demo` | 生成最小演示招标文件和公司资料（仅用于开发测试） |
| `prepare-inputs` | 导入原始资料：PDF/DOCX/MD → inputs/ |
| `validate` | 项目功能闭环检查（26 项，不调用 AI） |
| `split-docs` | 切分文档为 chunk |
| `parse-score` | 解析评分标准 → score_points.json |
| `extract-facts` | 提取全局事实 → global_facts.json |
| `generate-outline` | 生成标书大纲 → outline.json |
| `plan-jobs` | 生成章节任务包 → jobs/*.json |
| `select-context --chapter 01` | 为单个章节选择上下文（含 chunk-ranker 本地粗筛） |
| `select-context-all` | 为所有章节选择上下文（含 chunk-ranker 本地粗筛） |
| `write-chapter --chapter 01` | 生成单个章节 |
| `write-all --workers 2` | 并发生成所有章节 |
| `review-chapter --chapter 01` | 审核单个章节 |
| `review-all` | 审核所有章节 |
| `build-md` | 拼接 Markdown → outputs/final.md |
| `build-docx` | 生成 Word → outputs/final.docx |
| `run --workers 2` | CLI 模式完整流水线（11 步） |
| `graph-run --workers 2` | LangGraph 主图完整流程 |

---

## 7. LangGraph 主图

```text
START → init_workspace → prepare_inputs → split_docs
  → parse_score → extract_facts → generate_outline
  → plan_chapter_jobs → select_contexts → write_chapters
  → review_chapters → global_review → build_markdown → build_docx → END
```

---

## 8. 给编码 Agent 的总要求

1. 先跑通主流程，再优化细节。
2. 不要一开始引入复杂框架。
3. 所有中间结果都要落盘。
4. 所有模块都要能单独运行。
5. 失败时必须输出清晰错误信息。
6. 代码结构要简单、清晰、可维护。
7. 不允许硬编码 API Key。
8. 不允许模型编造公司没有提供的内容。
9. 每个章节必须绑定评分点。
10. 修改代码后必须提交并推送。

---

## 9. 最小可运行链路

```bash
python src/main.py init
python src/main.py init-demo
# 放入原始文件到 sources/ 各子目录（或使用 init-demo 生成的演示数据）
python src/main.py prepare-inputs
python src/main.py validate
python src/main.py graph-run --workers 2
```
