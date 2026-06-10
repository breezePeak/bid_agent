# 标书写作 Agent 第一版整体开发计划

## 1. 项目目标

本项目目标是开发一个“标书写作 Agent”的第一版 MVP。

第一版不追求完整产品化，不做复杂多 Agent 平台，不做 Web 页面，不做数据库，不做向量知识库，核心目标只有一个：

> 跑通“招标文件 / 评分标准 / 公司资料 / 标书模板 → 生成章节 → 审核章节 → 拼接 Word”的主流程。

整体流程：

```text
原始输入资料（PDF/DOCX/MD 等）
        ↓
资料导入层 → 转换为标准 MD 并落入 inputs/
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

## 2. 第一版范围

### 2.1 当前版本（第二阶段）做

1. **资料导入层**：支持将 PDF/DOCX/MD 等原始资料自动转换为标准 inputs/ 下的 Markdown。
2. 读取本地输入文件。
3. **大标书切分**：将招标文件和公司资料按标题结构切分为 chunk，输出到 workspace/chunks/。
4. 解析评分标准，生成结构化评分点。
5. 从招标文件和公司资料中提取全局事实。
6. 根据评分点生成标书大纲。
7. **章节任务规划**：为每个一级章节生成独立的 job 任务包。
8. **章节上下文选择**：由 LLM 为每章智能选择最相关的招标文件/公司资料 chunk（每类最多 8 个）。
9. **SubAgent 并发章节写作**：根据 job + context 按章节生成 Markdown 内容，支持 ThreadPoolExecutor 并发。
10. 对每章进行评分点覆盖审核。
11. 全文一致性审核。
12. 将章节拼接成完整 Markdown。
13. 将 Markdown 转成 Word 文件。
14. 所有中间结果落盘，方便调试和断点续跑。
15. 提供 CLI 命令运行整套流程。

### 2.2 第一版暂时不做

1. 不做 Web 页面。
2. 不做数据库。
3. 不做向量库 / RAG。
4. 不做复杂多 Agent 框架（仅本地 job + worker 模式）。
5. 不做任务队列。
6. 不做自动查重。
7. 不做深度废标项检查。
8. 不做复杂 Word 模板样式还原。

---

## 3. 技术选型

### 3.1 语言

使用 Python。

### 3.2 运行方式

使用本地 CLI 命令运行：

```bash
python src/main.py run
```

### 3.3 大模型接口

使用 OpenAI-compatible API。

配置通过 `.env` 文件读取：

```env
OPENAI_BASE_URL=http://your-api-host/v1
OPENAI_API_KEY=your-api-key
OPENAI_MODEL=gpt-5.5
```

所有业务模块都只能调用统一的 `llm_client.py`，不要在业务模块中重复写 API 请求逻辑。

### 3.4 Word 生成

第一版使用 `python-docx`。

第一版只要求：

1. 标题层级正确。
2. 段落正确。
3. Markdown 表格能转 Word 表格。
4. 能生成 `outputs/final.docx`。

---

## 4. 项目目录结构

```text
bid-agent/
  sources/                    ← 用户放入原始资料
    tender/                   ← 招标文件、答疑、补遗等
      招标文件.pdf
      招标文件.docx
      答疑文件.docx
      补遗文件.pdf
    company/                  ← 公司介绍、产品白皮书、案例等
      公司介绍.docx
      产品白皮书.pdf
      案例资料.docx
    template/                 ← 标书模板
      模板.docx

  inputs/                     ← prepare-inputs 自动生成
    tender.md
    score.md
    company.md
    template.docx

  workspace/
    chunks/                   ← 文档切分结果
      tender_chunks.json
      company_chunks.json
    jobs/                     ← 章节任务包
      01.json
      02.json
    contexts/                 ← 每章上下文选择结果
      01_context.json
      02_context.json
    score_points.json
    global_facts.json
    outline.json
    global_review.json
    chapters/
      01.md
      02.md
      03.md
    reviews/
      01_review.json
      02_review.json
      03_review.json
    debug_*_raw.txt           ← 模型解析失败时的原始输出

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

  src/
    main.py
    config.py
    llm_client.py
    utils.py
    file_loader.py
    document_converter.py      ← PDF/DOCX → Markdown 转换
    input_preparer.py          ← 统筹资料导入流程
    tender_extractor.py        ← 招标文件批量导入
    company_extractor.py       ← 公司资料批量导入
    document_splitter.py       ← 文档切分为 chunk
    job_planner.py             ← 章节任务包生成
    context_selector.py        ← 章节上下文选择
    subagent_runner.py         ← SubAgent 并发调度
    score_parser.py
    fact_extractor.py
    outline_generator.py
    chapter_writer.py
    chapter_reviewer.py
    global_reviewer.py
    docx_builder.py
    agents/                    ← Agent 薄封装层
      score_agent.py
      fact_agent.py
      outline_agent.py
      chapter_writer_agent.py
      chapter_review_agent.py
      context_agent.py
      global_review_agent.py
    graph/                     ← LangGraph 编排层
      state.py
      nodes.py
      routers.py
      bid_graph.py
      chapter_subgraph.py

  .env
  requirements.txt
  README.md
```

---

## 5. 输入文件说明

### 资料导入层 `sources/` → `inputs/`

用户只需将原始资料放入 `sources/` 对应目录，运行 `prepare-inputs` 即可自动生成 `inputs/` 下的标准文件：

```text
sources/
  tender/        ← 用户放入原始招标文件（支持 .pdf/.docx/.md）
  company/       ← 用户放入公司相关原始资料（支持 .pdf/.docx/.md）
  template/      ← 用户放入标书模板（.docx）

        ↓  python src/main.py prepare-inputs

inputs/
  tender.md      ← 自动合并生成（按文件名排序拼接）
  score.md       ← 如 sources/tender/ 有评分标准文件则提取，否则保留已有
  company.md     ← 自动合并生成（按文件名排序拼接）
  template.docx  ← 复制 sources/template/ 下第一个 docx 文件
```

`prepare-inputs` 流程：

1. 扫描 `sources/tender/` 下的所有文件 → 调用 `tender_extractor` 逐个转为 Markdown
2. 扫描 `sources/company/` 下的所有文件 → 调用 `company_extractor` 逐个转为 Markdown
3. 将转换结果分别拼接为 `inputs/tender.md` 和 `inputs/company.md`
4. 如有 `sources/template/*.docx`，复制为 `inputs/template.docx`
5. `inputs/score.md`：如已存在则保留，否则尝试从 tender 文件中识别评分标准章节并提取

支持的原始文件格式：

- `.md` → 直接读取
- `.docx` → 使用 `python-docx` 提取段落文本
- `.pdf` → 使用 `pdfplumber` 或 `PyMuPDF` 提取文本

### 5.1 `inputs/tender.md`

招标文件文本内容。由 `prepare-inputs` 自动生成。

内容来自 `sources/tender/` 下所有文件的合并结果。

### 5.2 `inputs/score.md`

评分标准文本内容。优先使用用户手动填写的版本，其次由 `prepare-inputs` 尝试从招标文件中提取。

这是最重要的输入之一，后续大纲和章节内容都要围绕评分点生成。

### 5.3 `inputs/company.md`

公司资料、产品资料、案例资料、人员资料、资质资料等。由 `prepare-inputs` 自动生成。

内容来自 `sources/company/` 下所有文件的合并结果。

章节生成时不能编造公司没有提供的资质、案例、人员、证书。

### 5.4 `inputs/template.docx`

标书模板。由 `prepare-inputs` 从 `sources/template/` 复制。

第一版可以先不做深度模板还原，只保留后续扩展空间。

---

## 6. 工作区文件说明

### 6.1 `workspace/score_points.json`

评分标准解析结果。

示例结构：

```json
[
  {
    "id": "S001",
    "category": "技术方案",
    "title": "项目理解",
    "score": 5,
    "requirement": "对项目背景、建设目标、业务需求理解准确完整",
    "keywords": ["项目背景", "建设目标", "业务需求"],
    "response_strategy": "需要在项目理解章节中详细响应"
  }
]
```

要求：

1. 每个评分点必须有唯一 ID。
2. ID 格式为 `S001`、`S002`、`S003`。
3. 如果无法识别分值，`score` 可以为 `null`。
4. 不允许故意丢失评分项。

### 6.2 `workspace/global_facts.json`

全局事实提取结果。

示例结构：

```json
{
  "project_name": "",
  "bidder_name": "",
  "service_period": "",
  "warranty_period": "",
  "project_location": "",
  "core_products": [],
  "company_advantages": [],
  "similar_cases": [],
  "team_roles": []
}
```

作用：

1. 保证全文项目名称一致。
2. 保证投标人名称一致。
3. 保证服务周期、质保期、项目地点等关键信息一致。
4. 避免每章生成时模型乱编。

要求：

1. 只能从输入资料中提取。
2. 不确定的字段填空字符串或空数组。
3. 后续每章生成都必须带上该文件内容。

### 6.3 `workspace/outline.json`

大纲生成结果。

示例结构：

```json
{
  "chapters": [
    {
      "id": "01",
      "title": "项目理解与需求分析",
      "score_point_ids": ["S001"],
      "description": "回应项目背景、建设目标、业务需求理解等评分要求",
      "sections": [
        {
          "id": "01.01",
          "title": "项目背景理解",
          "score_point_ids": ["S001"],
          "writing_requirements": [
            "结合招标文件描述项目建设背景",
            "说明本项目建设必要性",
            "体现投标人对业务场景的理解"
          ]
        }
      ]
    }
  ]
}
```

要求：

1. 一级目录尽量对应评分大类。
2. 每个章节都必须绑定 `score_point_ids`。
3. 每个评分点至少被一个章节覆盖。
4. 每个章节至少包含一个二级目录。
5. 不要生成无关目录。

### 6.4 `workspace/chapters/*.md`

逐章生成的 Markdown 文件。

示例：

```text
workspace/chapters/01.md
workspace/chapters/02.md
workspace/chapters/03.md
```

每个章节文件需要包含一级标题：

```markdown
# 01 项目理解与需求分析
```

### 6.5 `workspace/reviews/*.json`

章节审核结果。

示例结构：

```json
{
  "chapter_id": "01",
  "chapter_title": "项目理解与需求分析",
  "score_coverage": [
    {
      "score_point_id": "S001",
      "covered": true,
      "coverage_level": "high",
      "evidence": "正文已说明项目背景、建设目标和业务需求",
      "suggestion": ""
    }
  ],
  "problems": [
    {
      "type": "content_too_generic",
      "description": "部分内容偏通用",
      "suggestion": "增加招标文件中的具体业务场景"
    }
  ],
  "need_rewrite": false
}
```

第一版只审核，不自动重写。

---

## 7. 输出文件说明

### 7.1 `outputs/final.md`

所有章节拼接后的完整 Markdown 文件。

要求：

1. 按 `outline.json` 中章节顺序拼接。
2. 章节之间使用空行分隔。
3. 缺失章节需要打印警告，但不阻断已生成章节的拼接。

### 7.2 `outputs/final.docx`

最终 Word 文件。

第一版 Word 生成目标：

1. 标题层级正确。
2. 正文段落正确。
3. Markdown 表格能转换为 Word 表格。
4. 成功生成 `outputs/final.docx`。

---

## 8. CLI 命令设计

当前版本支持以下命令：

```bash
python src/main.py init

python src/main.py prepare-inputs

python src/main.py split-docs

python src/main.py parse-score

python src/main.py extract-facts

python src/main.py generate-outline

python src/main.py plan-jobs

python src/main.py select-context --chapter 01

python src/main.py select-context-all

python src/main.py write-chapter --chapter 01

python src/main.py write-all --workers 3

python src/main.py review-chapter --chapter 01

python src/main.py review-all

python src/main.py build-md

python src/main.py build-docx

python src/main.py run --workers 3

python src/main.py graph-run --workers 3
```

### 8.1 `init`

初始化项目目录和默认文件。

需要创建：

```text
sources/tender/
sources/company/
sources/template/
inputs/
workspace/
workspace/chunks/
workspace/jobs/
workspace/contexts/
workspace/chapters/
workspace/reviews/
outputs/
prompts/
```

如果以下文件不存在，则创建空文件：

```text
inputs/tender.md
inputs/score.md
inputs/company.md
```

如果提示词文件不存在，则创建默认提示词文件：

```text
prompts/parse_score.md
prompts/extract_facts.md
prompts/generate_outline.md
prompts/write_chapter.md
prompts/review_chapter.md
prompts/select_context.md
prompts/global_review.md
```

### 8.2 `prepare-inputs`（新增）

资料导入命令。将 `sources/` 下的原始资料自动转换为 `inputs/` 下的标准 Markdown 文件。

流程：

1. 扫描 `sources/tender/` → 调用 `tender_extractor` → 拼接 → `inputs/tender.md`
2. 扫描 `sources/company/` → 调用 `company_extractor` → 拼接 → `inputs/company.md`
3. 复制 `sources/template/` 下第一个 `.docx` → `inputs/template.docx`
4. `inputs/score.md` 如已存在则保留，否则尝试从招标文件中提取评分标准章节

支持的原料格式：`.pdf`、`.docx`、`.md`

### 8.3 `split-docs`（新增）

将 `inputs/tender.md` 和 `inputs/company.md` 按标题结构切分为 chunk。

输出：`workspace/chunks/tender_chunks.json`、`workspace/chunks/company_chunks.json`

### 8.4 `parse-score`

解析评分标准。

输入：`inputs/score.md`
输出：`workspace/score_points.json`

### 8.5 `extract-facts`

提取全局事实。

输入：`inputs/tender.md`、`inputs/company.md`
输出：`workspace/global_facts.json`

### 8.6 `generate-outline`

生成标书大纲。

输入：`inputs/tender.md`、`workspace/score_points.json`、`workspace/global_facts.json`
输出：`workspace/outline.json`

### 8.7 `plan-jobs`（新增）

根据 `workspace/outline.json` 为每个一级章节生成独立的 job 任务包。

输出：`workspace/jobs/{chapter_id}.json`

每个 job 包含：`job_id`、`chapter_id`、`chapter_title`、`score_point_ids`、`sections`、`output_path`、`review_path`、`context_path`

### 8.8 `select-context / select-context-all`（新增）

调用 LLM 为章节智能选择最相关的招标文件和公司资料 chunk。

- `select-context --chapter 01`：为单个章节选择上下文
- `select-context-all`：为所有章节批量选择

输出：`workspace/contexts/{chapter_id}_context.json`

每章最多选择 8 个 tender chunk 和 8 个 company chunk。

### 8.9 `write-chapter`

生成指定章节（依赖 job + context 文件）。

```bash
python src/main.py write-chapter --chapter 01
```

输出：`workspace/chapters/01.md`

若 context 文件缺失，会提示先执行 `select-context`。

### 8.10 `write-all`

按大纲顺序生成全部章节，支持并发。

```bash
python src/main.py write-all --workers 3        # 最多 3 个并发
```

使用 ThreadPoolExecutor 实现 SubAgent 并发写作，默认 workers=2，最大 5。单个章节失败不会中断其他任务。

### 8.11 `review-chapter`

审核指定章节。

```bash
python src/main.py review-chapter --chapter 01
```

输出：`workspace/reviews/01_review.json`

### 8.12 `review-all`

按大纲顺序审核全部章节。

### 8.13 `build-md`

拼接最终 Markdown。

输入：`workspace/outline.json`、`workspace/chapters/*.md`
输出：`outputs/final.md`

### 8.14 `build-docx`

生成 Word 文件。

输入：`outputs/final.md`、`inputs/template.docx`（可选）
输出：`outputs/final.docx`

### 8.15 `run`

执行完整流程（CLI 模式），等价于：

```text
split-docs → parse-score → extract-facts → generate-outline
→ plan-jobs → select-context-all → write-all → review-all
→ build-md → build-docx
```

```bash
python src/main.py run --workers 3
```

### 8.16 `graph-run`

按 LangGraph 主图运行完整流程，支持 workers 参数。`run` 命令的 LangGraph 版本。

---

## 9. 模块设计

### 9.1 `src/config.py`

职责：

1. 从 `.env` 读取配置。
2. 校验必要配置是否存在。
3. 对外提供配置对象。

需要读取：

```text
OPENAI_BASE_URL
OPENAI_API_KEY
OPENAI_MODEL
```

缺少必要配置时，需要给出清晰错误提示。

### 9.2 `src/llm_client.py`

职责：

1. 统一调用大模型。
2. 支持 OpenAI-compatible API。
3. 支持失败重试。
4. 清理模型返回中的 Markdown 代码块。
5. 所有业务模块都通过它调用模型。

建议函数：

```python
def chat(messages: list[dict], temperature: float = 0.2) -> str:
    pass
```

要求：

1. 默认重试 3 次。
2. 请求失败时打印错误原因。
3. 返回内容需要自动去掉 ```json 和 ```。
4. 不要在业务模块里重复写 API 请求代码。

### 9.3 `src/file_loader.py`

职责：

1. 读取文本文件。
2. 写入文本文件。
3. 读取 JSON 文件。
4. 写入 JSON 文件。
5. 检查文件是否存在。

建议函数：

```python
def read_text(path: str) -> str:
    pass

def write_text(path: str, content: str) -> None:
    pass

def read_json(path: str):
    pass

def write_json(path: str, data) -> None:
    pass
```

### 9.4 `src/utils.py`

职责：

1. 清理模型输出。
2. 提取 JSON。
3. 校验 JSON。
4. 获取项目根目录。
5. 统一文件读写。

建议函数：

```python
def clean_code_fence(text: str) -> str:
    pass

def parse_json_or_save_debug(text: str, debug_path: str):
    pass

def ensure_dir(path: str) -> None:
    pass

def log(message: str) -> None:
    pass
```

### 9.5 `src/document_converter.py`（新增）

职责：

1. 统一 PDF / DOCX → Markdown 文本转换。
2. `.md` 文件直接读取。
3. `.docx` 文件使用 `python-docx` 提取段落。
4. `.pdf` 文件使用 `pdfplumber` 或 `PyMuPDF` 提取文本。
5. 对外提供单一入口 `convert_to_markdown(file_path) -> str`。

### 9.6 `src/input_preparer.py`（新增）

职责：

1. 统筹资料导入流程。
2. 调用 `tender_extractor` 和 `company_extractor`。
3. 生成 `inputs/tender.md`、`inputs/company.md`。
4. 复制模板到 `inputs/template.docx`。
5. 对外提供 `prepare_inputs(root)` 入口。

### 9.7 `src/tender_extractor.py`（新增）

职责：

1. 扫描 `sources/tender/` 下所有文件。
2. 逐个调用 `document_converter.convert_to_markdown`。
3. 按文件名排序拼接为完整 `inputs/tender.md`。
4. 尝试识别评分标准文件并单独保存为 `inputs/score.md`（如不存在）。

### 9.8 `src/company_extractor.py`（新增）

职责：

1. 扫描 `sources/company/` 下所有文件。
2. 逐个调用 `document_converter.convert_to_markdown`。
3. 按文件名排序拼接为完整 `inputs/company.md`。

### 9.9 `src/document_splitter.py`

职责：

1. 读取 `inputs/tender.md` 和 `inputs/company.md`。
2. 按 Markdown 标题层级切分文档。
3. 标题块过长时按字符长度继续切分（默认 3500 字符）。
4. 输出 `workspace/chunks/tender_chunks.json` 和 `workspace/chunks/company_chunks.json`。

### 9.10 `src/job_planner.py`

职责：

1. 读取 `workspace/outline.json` 和 `workspace/score_points.json`。
2. 为每个一级章节生成独立的 job 文件。
3. 校验每个章节绑定的 score_point_ids 是否合法。
4. 输出 `workspace/jobs/{chapter_id}.json`。

### 9.11 `src/context_selector.py`

职责：

1. 读取章节 job、评分点、chunk 目录和全局事实。
2. 调用 LLM 为当前章节智能选择最相关的招标文件和公司资料 chunk。
3. 每章最多选择 8 个 tender chunk 和 8 个 company chunk。
4. 失败时使用标题顺序兜底选择。
5. 输出 `workspace/contexts/{chapter_id}_context.json`。

### 9.12 `src/subagent_runner.py`

职责：

1. 使用 ThreadPoolExecutor 实现章节并发写作。
2. 支持 `--workers` 参数控制并发数（默认 2，最大 5）。
3. 单个章节失败不中断全部任务，记录失败信息。

### 9.13 `src/score_parser.py`

职责：

1. 读取 `inputs/score.md`。
2. 调用模型解析评分点。
3. 校验 JSON。
4. 写入 `workspace/score_points.json`。

失败时保存原始输出：

```text
workspace/debug_parse_score_raw.txt
```

### 9.14 `src/fact_extractor.py`

职责：

1. 读取 `inputs/tender.md` 和 `inputs/company.md`。
2. 调用模型提取全局事实。
3. 校验 JSON。
4. 写入 `workspace/global_facts.json`。

### 9.15 `src/outline_generator.py`

职责：

1. 读取招标文件、评分点、全局事实。
2. 调用模型生成大纲。
3. 校验 JSON。
4. 确保每个评分点至少被一个章节覆盖。
5. 写入 `workspace/outline.json`。

失败时保存原始输出：

```text
workspace/debug_outline_raw.txt
```

### 9.16 `src/chapter_writer.py`

职责：

1. 读取章节 job 和 context 文件。
2. 根据 context 中选定的 chunk id 加载对应文档片段。
3. 每个章节 SubAgent 只拿当前章节、绑定评分点、全局事实、相关 chunks。
4. 不再将完整招标文件/公司资料传给模型。
5. 写入 `workspace/chapters/{chapter_id}.md`。
6. 支持生成单章和全部章节。

章节生成要求：

1. 每章单独生成。
2. 每章只写当前章节内容。
3. 必须覆盖当前章节绑定的评分点。
4. 必须结合选中的招标文件片段和公司资料片段。
5. 不允许编造公司资料中没有的资质、案例、人员、证书。
6. 输出 Markdown。
7. 表格使用 Markdown 表格。

### 9.17 `src/chapter_reviewer.py`

职责：

1. 读取章节 Markdown。
2. 读取大纲、评分点、全局事实。
3. 调用模型审核评分点覆盖情况。
4. 输出审核 JSON。
5. 支持审核单章和全部章节。

第一版只审核，不自动重写。

### 9.18 `src/global_reviewer.py`

职责：

1. 读取全局事实、大纲、评分点、所有章节正文和审核结果。
2. 调用模型进行全文一致性审核。
3. 检查项目名称、投标人名称、服务周期、质保期等是否一致。
4. 检查章节冲突、评分点遗漏、编造风险。
5. 输出 `workspace/global_review.json`。

### 9.19 `src/docx_builder.py`

职责：

1. 读取 `outputs/final.md`。
2. 生成 `outputs/final.docx`。
3. 支持 Markdown 标题、段落、表格。
4. 如果 `inputs/template.docx` 存在，可以基于模板生成；否则新建空白 Word。

第一版不用做复杂样式还原。

### 9.20 `src/main.py`

职责：

1. 使用 `argparse` 实现命令路由。
2. 调用各个模块。
3. 打印清晰日志。
4. 实现 `run` 总流程。

---

## 10. 提示词文件设计

### 10.1 `prompts/parse_score.md`

用于解析评分标准。

核心要求：

1. 从评分标准中提取所有评分点。
2. 不允许丢失评分项。
3. 输出合法 JSON 数组。
4. 每个评分点包含 `id`、`category`、`title`、`score`、`requirement`、`keywords`、`response_strategy`。
5. 如果分值无法识别，`score` 为 `null`。

### 10.2 `prompts/extract_facts.md`

用于提取全局事实。

核心要求：

1. 只能从招标文件和公司资料中提取。
2. 不允许编造。
3. 不确定字段填空字符串或空数组。
4. 输出合法 JSON 对象。

### 10.3 `prompts/generate_outline.md`

用于生成大纲。

核心要求：

1. 根据评分点生成标书大纲。
2. 一级目录尽量对应评分大类。
3. 每个章节绑定评分点 ID。
4. 每个评分点至少被一个章节覆盖。
5. 输出合法 JSON 对象。

### 10.4 `prompts/write_chapter.md`

用于生成章节内容。

核心要求：

1. 只写当前章节。
2. 必须覆盖当前章节绑定的评分点。
3. 必须结合已提供的招标文件片段和公司资料片段（不再传入完整文档）。
4. 不允许编造公司资料中没有的内容。
5. 输出 Markdown。

### 10.5 `prompts/review_chapter.md`

用于审核章节。

核心要求：

1. 检查当前章节是否覆盖绑定评分点。
2. 检查内容是否空泛。
3. 检查是否有明显编造。
4. 检查是否和全局事实冲突。
5. 输出合法 JSON 对象。

### 10.6 `prompts/select_context.md`（新增）

用于为当前章节选择最相关的资料片段。

核心要求：

1. 根据章节任务、评分点、chunk 目录选择相关 chunk。
2. 每章最多选择 8 个 tender chunk 和 8 个 company chunk。
3. 只能选择真实存在的 chunk id。
4. 优先选择能直接支撑评分点响应的片段。
5. 输出合法 JSON 对象。

### 10.7 `prompts/global_review.md`（新增）

用于全文一致性审核。

核心要求：

1. 检查项目名称、投标人名称是否一致。
2. 检查服务周期、质保期是否一致。
3. 检查章节冲突、评分点遗漏、编造风险。
4. 输出合法 JSON 对象。

---

## 11. 开发阶段计划

### 阶段 1：项目骨架和初始化

目标：

1. 创建目录结构。
2. 实现 `init` 命令。
3. 实现基础文件读写工具。
4. 实现日志工具。

交付内容：

```text
src/main.py
src/file_loader.py
src/utils.py
```

验收命令：

```bash
python src/main.py init
```

验收标准：

1. 所有目录自动创建。
2. 输入文件自动创建。
3. prompts 文件自动创建。
4. 重复执行不会报错。

### 阶段 2：配置和模型调用

目标：

1. 实现 `.env` 配置读取。
2. 实现 OpenAI-compatible API 调用。
3. 实现失败重试。
4. 实现模型输出清理。

交付内容：

```text
src/config.py
src/llm_client.py
```

验收标准：

1. `.env` 缺配置时报错清晰。
2. 能正常调用模型。
3. 模型返回 ```json 时能清理。
4. 失败时能重试。

### 阶段 3：评分标准解析

目标：

1. 读取 `inputs/score.md`。
2. 调用模型解析评分点。
3. 生成 `workspace/score_points.json`。

交付内容：

```text
src/score_parser.py
prompts/parse_score.md
```

验收命令：

```bash
python src/main.py parse-score
```

验收标准：

1. 成功生成 `workspace/score_points.json`。
2. JSON 合法。
3. 每个评分点有唯一 ID。
4. 解析失败时保存 raw 输出。

### 阶段 4：全局事实提取

目标：

1. 读取 `inputs/tender.md` 和 `inputs/company.md`。
2. 调用模型提取全局事实。
3. 生成 `workspace/global_facts.json`。

交付内容：

```text
src/fact_extractor.py
prompts/extract_facts.md
```

验收命令：

```bash
python src/main.py extract-facts
```

验收标准：

1. 成功生成 `workspace/global_facts.json`。
2. JSON 合法。
3. 不确定字段为空。
4. 不编造输入资料之外的信息。

### 阶段 5：大纲生成

目标：

1. 读取评分点、全局事实和招标文件。
2. 生成绑定评分点的大纲。
3. 生成 `workspace/outline.json`。

交付内容：

```text
src/outline_generator.py
prompts/generate_outline.md
```

验收命令：

```bash
python src/main.py generate-outline
```

验收标准：

1. 成功生成 `workspace/outline.json`。
2. 每个章节绑定评分点。
3. 每个评分点至少被一个章节覆盖。
4. JSON 合法。
5. 解析失败时保存 raw 输出。

### 阶段 6：章节生成

目标：

1. 支持生成指定章节。
2. 支持生成全部章节。
3. 每章输出 Markdown。

交付内容：

```text
src/chapter_writer.py
prompts/write_chapter.md
```

验收命令：

```bash
python src/main.py write-chapter --chapter 01
python src/main.py write-all
```

验收标准：

1. 成功生成 `workspace/chapters/01.md`。
2. 每章只写当前章节内容。
3. 内容覆盖绑定评分点。
4. 不编造公司资料中没有的内容。
5. 输出 Markdown。

### 阶段 7：章节审核

目标：

1. 支持审核指定章节。
2. 支持审核全部章节。
3. 输出审核 JSON。

交付内容：

```text
src/chapter_reviewer.py
prompts/review_chapter.md
```

验收命令：

```bash
python src/main.py review-chapter --chapter 01
python src/main.py review-all
```

验收标准：

1. 成功生成 `workspace/reviews/01_review.json`。
2. JSON 合法。
3. 能判断评分点覆盖情况。
4. 能指出内容空泛、遗漏、冲突等问题。
5. 第一版不自动重写。

### 阶段 8：拼接 Markdown

目标：

1. 按大纲顺序拼接章节。
2. 生成完整 Markdown。

交付内容：

```text
build-md 命令
```

验收命令：

```bash
python src/main.py build-md
```

验收标准：

1. 成功生成 `outputs/final.md`。
2. 章节顺序正确。
3. 缺失章节时打印警告。
4. 已生成章节能正常拼接。

### 阶段 9：生成 Word

目标：

1. 将 `outputs/final.md` 转成 `outputs/final.docx`。
2. 支持标题、段落、表格。

交付内容：

```text
src/docx_builder.py
```

验收命令：

```bash
python src/main.py build-docx
```

验收标准：

1. 成功生成 `outputs/final.docx`。
2. 标题层级正确。
3. 段落正确。
4. Markdown 表格能转换为 Word 表格。

### 阶段 10：总流程 `run`（MVP 完成）

目标：

1. 实现完整流程一键运行。
2. 日志清晰。
3. 任一步失败都能定位。

交付内容：

```text
src/main.py
```

验收命令：

```bash
python src/main.py run
```

验收标准：

1. 能按顺序执行所有步骤。
2. 生成所有中间文件。
3. 生成最终 Word。
4. 日志清晰。

---

### 阶段 11：大标书切分 + 章节任务规划（第二阶段基础）

目标：

1. 实现文档切分为 chunk（`document_splitter.py`）。
2. 实现章节任务包生成（`job_planner.py`）。
3. 为后续上下文选择和并发写作铺路。

交付内容：

```text
src/document_splitter.py
src/job_planner.py
```

验收命令：

```bash
python src/main.py split-docs
python src/main.py plan-jobs
```

验收标准：

1. 按标题结构切分文档，超长段落继续切分。
2. `workspace/chunks/` 下生成 `tender_chunks.json` 和 `company_chunks.json`。
3. 每个章节生成独立 job 文件到 `workspace/jobs/`。
4. 需要先执行 `parse-score` 和 `generate-outline`。

### 阶段 12：章节上下文选择 + 上下文写作改造

目标：

1. 实现 LLM 驱动的章节上下文选择（`context_selector.py`）。
2. 改造 `chapter_writer.py`：不再传递完整文档，改为读取 job + context。
3. 新增 `prompts/select_context.md`。

交付内容：

```text
src/context_selector.py
src/chapter_writer.py（改造）
prompts/select_context.md
```

验收命令：

```bash
python src/main.py select-context --chapter 01
python src/main.py select-context-all
python src/main.py write-chapter --chapter 01
python src/main.py write-all
```

验收标准：

1. 每章通过 LLM 选择最多 8 个 tender chunk + 8 个 company chunk。
2. 上下文选择失败时有兜底策略（取前 8 个 chunk）。
3. 章节写作不再传完整文档，只传选中的 chunk 内容。
4. context 缺失时 `write-chapter` 给出明确错误提示。

### 阶段 13：SubAgent 并发写作

目标：

1. 实现并发章节写作（`subagent_runner.py`）。
2. `write-all` 支持 `--workers` 参数。
3. 单个章节失败不中断全部任务。

交付内容：

```text
src/subagent_runner.py
src/main.py（改造 write-all、run）
src/graph/nodes.py（改造 write_chapters_node）
```

验收命令：

```bash
python src/main.py write-all --workers 3
python src/main.py run --workers 3
```

验收标准：

1. ThreadPoolExecutor 并发执行章节写作。
2. workers 默认 2，最大 5。
3. 单个章节失败时其他章节继续执行。
4. 完成时输出成功/失败章节统计。

### 阶段 14：资料导入层（当前待开发）

目标：

1. 用户只需将原始资料放入 `sources/`，系统自动生成 `inputs/` 下的标准文件。
2. 支持 PDF / DOCX / MD 自动转换。
3. 新增 `prepare-inputs` 命令。
4. LangGraph 主图新增 `prepare_inputs` 节点。

交付内容：

```text
src/document_converter.py
src/input_preparer.py
src/tender_extractor.py
src/company_extractor.py
src/main.py（新增 prepare-inputs 命令）
src/graph/nodes.py（新增 prepare_inputs_node）
sources/  目录（init 命令自动创建）
```

验收命令：

```bash
# 用户在 sources/tender/、sources/company/、sources/template/ 放入原始文件后：
python src/main.py prepare-inputs
python src/main.py run
```

验收标准：

1. 支持 `.pdf`、`.docx`、`.md` 格式自动转换为 Markdown。
2. `inputs/tender.md` 由 `sources/tender/` 下所有文件按文件名排序拼接生成。
3. `inputs/company.md` 由 `sources/company/` 下所有文件按文件名排序拼接生成。
4. `inputs/template.docx` 从 `sources/template/` 复制。
5. 如 `sources/` 下无文件，给出明确提示。
6. LangGraph 主图流程更新为：

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
  → write_chapters
  → review_chapters
  → global_review
  → build_markdown
  → build_docx
  → END
```

7. 现有 `run` 流程更新为：

```text
prepare-inputs → split-docs → parse-score → extract-facts
→ generate-outline → plan-jobs → select-context-all
→ write-all → review-all → build-md → build-docx
```

---

## 12. 开发提交建议

建议按以下提交拆分：

### 第 1 次提交

```text
init + 目录结构 + 文件读写 + 日志工具
```

### 第 2 次提交

```text
config + llm_client + .env 读取 + 模型调用
```

### 第 3 次提交

```text
parse-score + extract-facts
```

### 第 4 次提交

```text
generate-outline
```

### 第 5 次提交

```text
write-chapter + write-all
```

### 第 6 次提交

```text
review-chapter + review-all
```

### 第 7 次提交

```text
build-md + build-docx
```

### 第 8 次提交

```text
run 总流程 + README + 最终测试
```

### 第 9 次提交（第二阶段）

```text
第二阶段改造：document_splitter + job_planner + context_selector
+ chapter_writer 上下文改造 + subagent_runner 并发
```

### 第 10 次提交（资料导入层）

```text
资料导入层：document_converter + input_preparer
+ tender_extractor + company_extractor
+ prepare-inputs 命令 + LangGraph 主图更新

---

## 13. 健壮性要求

1. 所有 JSON 写入前必须校验。
2. 模型返回 JSON 外包了 ```json 时，需要自动清理。
3. JSON 解析失败时，需要保存原始模型输出。
4. 文件不存在时，给出明确提示。
5. 不要吞异常。
6. 日志要能定位失败步骤。
7. 所有路径尽量基于项目根目录，不依赖当前运行目录。
8. `.env` 中缺少必要配置时要立即报错。
9. API Key 不允许写死到代码里。
10. 模型调用失败需要重试。

---

## 14. 第一版验收标准

完成后，应该可以执行：

```bash
python src/main.py init
```

然后用户填入：

```text
inputs/tender.md
inputs/score.md
inputs/company.md
```

再执行：

```bash
python src/main.py run
```

最终生成：

```text
workspace/score_points.json
workspace/global_facts.json
workspace/outline.json
workspace/chapters/*.md
workspace/reviews/*.json
outputs/final.md
outputs/final.docx
```

第一版整体通过标准：

1. 能读取输入文件。
2. 能解析评分点。
3. 能提取全局事实。
4. 能生成绑定评分点的大纲。
5. 能逐章生成 Markdown。
6. 能逐章审核评分点覆盖情况。
7. 能拼接完整 Markdown。
8. 能生成 Word。
9. 每个章节至少对应一个评分点。
10. 每个评分点至少被一个章节覆盖。
11. 失败时能定位是哪一步出错。

---

## 15. 后续增强方向

当前已完成第一阶段 MVP 和第二阶段（大标书切分 + 上下文选择 + 并发写作）。资料导入层（阶段 14）为当前待开发区块。之后可继续考虑：

### 15.1 模板占位符

支持：

```text
{{PROJECT_NAME}}
{{BIDDER_NAME}}
{{TECHNICAL_CONTENT}}
{{SERVICE_PERIOD}}
```

将内容精确填入模板位置。

### 15.2 自动补写

审核不通过时：

```text
章节审核 → 发现遗漏 → 自动补写 → 再审核
```

### 15.3 废标项检查

从招标文件中提取废标项、否决项、实质性响应条款，并在最终输出前检查。

### 15.4 知识库 / RAG

公司资料、产品资料、案例资料较多时，引入知识库检索，避免一次性塞入上下文。可替代当前的 chunk 上下文选择方案。

### 15.5 任务恢复

生成长标书时支持断点续跑：

```text
已生成章节不重复生成
失败章节可单独重跑
```

### 15.7 多 Agent 化

后续可以拆分为：

```text
招标文件解析 Agent
评分标准分析 Agent
大纲规划 Agent
章节写作 Agent
符合性审核 Agent
Word 拼装 Agent
```

但第一版不需要先做复杂多 Agent。

---

## 16. 给编码 Agent 的总要求

开发时必须遵守：

1. 先跑通主流程，再优化细节。
2. 不要一开始引入复杂框架。
3. 不要做超出第一版范围的功能。
4. 所有中间结果都要落盘。
5. 所有模块都要能单独运行。
6. 失败时必须输出清晰错误信息。
7. 代码结构要简单、清晰、可维护。
8. 不允许硬编码 API Key。
9. 不允许模型编造公司没有提供的内容。
10. 每个章节必须绑定评分点。

---

## 17. 最小可运行链路

### 当前版本（已实现）

```bash
python src/main.py init

# 填写 inputs/tender.md、inputs/score.md、inputs/company.md

python src/main.py run --workers 3
```

### 资料导入层完成后

```bash
python src/main.py init

# 用户只需将原始文件放入 sources/ 目录：
#   sources/tender/     → 招标文件.pdf、答疑文件.docx 等
#   sources/company/    → 公司介绍.docx、案例资料.docx 等
#   sources/template/   → 模板.docx

python src/main.py run --workers 3
```

成功后应生成：

```text
outputs/final.docx
```
