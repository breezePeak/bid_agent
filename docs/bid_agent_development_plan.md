# 标书写作 Agent 第一版整体开发计划

## 1. 项目目标

本项目目标是开发一个“标书写作 Agent”的第一版 MVP。

第一版不追求完整产品化，不做复杂多 Agent 平台，不做 Web 页面，不做数据库，不做向量知识库，核心目标只有一个：

> 跑通“招标文件 / 评分标准 / 公司资料 / 标书模板 → 生成章节 → 审核章节 → 拼接 Word”的主流程。

整体流程：

```text
招标文件 / 评分标准 / 公司资料 / 标书模板
        ↓
解析评分点
        ↓
提取全局事实
        ↓
生成标书大纲
        ↓
按章节逐章生成内容
        ↓
逐章审核评分点覆盖情况
        ↓
拼接 Markdown
        ↓
生成 Word 文件
```

---

## 2. 第一版范围

### 2.1 第一版要做

1. 读取本地输入文件。
2. 解析评分标准，生成结构化评分点。
3. 从招标文件和公司资料中提取全局事实。
4. 根据评分点生成标书大纲。
5. 根据大纲逐章生成 Markdown 内容。
6. 对每章进行评分点覆盖审核。
7. 将章节拼接成完整 Markdown。
8. 将 Markdown 转成 Word 文件。
9. 所有中间结果落盘，方便调试和断点续跑。
10. 提供 CLI 命令运行整套流程。

### 2.2 第一版暂时不做

1. 不做 PDF 解析。
2. 不做 Web 页面。
3. 不做数据库。
4. 不做向量库 / RAG。
5. 不做复杂多 Agent 框架。
6. 不做任务队列。
7. 不做自动查重。
8. 不做深度废标项检查。
9. 不做复杂 Word 模板样式还原。
10. 不做并发生成。

第一版重点是主流程跑通，后续再逐步增强。

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
  inputs/
    tender.md
    score.md
    company.md
    template.docx

  workspace/
    score_points.json
    global_facts.json
    outline.json
    chapters/
      01.md
      02.md
      03.md
    reviews/
      01_review.json
      02_review.json
      03_review.json

  outputs/
    final.md
    final.docx

  prompts/
    parse_score.md
    extract_facts.md
    generate_outline.md
    write_chapter.md
    review_chapter.md

  src/
    main.py
    config.py
    llm_client.py
    file_loader.py
    score_parser.py
    fact_extractor.py
    outline_generator.py
    chapter_writer.py
    chapter_reviewer.py
    docx_builder.py
    utils.py

  .env
  requirements.txt
  README.md
```

---

## 5. 输入文件说明

### 5.1 `inputs/tender.md`

招标文件文本内容。

第一版不直接解析 PDF，先人工或外部工具将招标文件转换成 Markdown。

### 5.2 `inputs/score.md`

评分标准文本内容。

这是第一版最重要的输入之一，后续大纲和章节内容都要围绕评分点生成。

### 5.3 `inputs/company.md`

公司资料、产品资料、案例资料、人员资料、资质资料等。

章节生成时不能编造公司没有提供的资质、案例、人员、证书。

### 5.4 `inputs/template.docx`

标书模板。

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

第一版需要支持以下命令：

```bash
python src/main.py init

python src/main.py parse-score

python src/main.py extract-facts

python src/main.py generate-outline

python src/main.py write-chapter --chapter 01

python src/main.py write-all

python src/main.py review-chapter --chapter 01

python src/main.py review-all

python src/main.py build-md

python src/main.py build-docx

python src/main.py run
```

### 8.1 `init`

初始化项目目录和默认文件。

需要创建：

```text
inputs/
workspace/
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
```

### 8.2 `parse-score`

解析评分标准。

输入：

```text
inputs/score.md
```

输出：

```text
workspace/score_points.json
```

### 8.3 `extract-facts`

提取全局事实。

输入：

```text
inputs/tender.md
inputs/company.md
```

输出：

```text
workspace/global_facts.json
```

### 8.4 `generate-outline`

生成标书大纲。

输入：

```text
inputs/tender.md
workspace/score_points.json
workspace/global_facts.json
```

输出：

```text
workspace/outline.json
```

### 8.5 `write-chapter`

生成指定章节。

示例：

```bash
python src/main.py write-chapter --chapter 01
```

输出：

```text
workspace/chapters/01.md
```

### 8.6 `write-all`

按大纲顺序生成全部章节。

第一版串行生成，不做并发。

### 8.7 `review-chapter`

审核指定章节。

示例：

```bash
python src/main.py review-chapter --chapter 01
```

输出：

```text
workspace/reviews/01_review.json
```

### 8.8 `review-all`

按大纲顺序审核全部章节。

### 8.9 `build-md`

拼接最终 Markdown。

输入：

```text
workspace/outline.json
workspace/chapters/*.md
```

输出：

```text
outputs/final.md
```

### 8.10 `build-docx`

生成 Word 文件。

输入：

```text
outputs/final.md
inputs/template.docx
```

输出：

```text
outputs/final.docx
```

### 8.11 `run`

执行完整流程，等价于：

```text
parse-score
extract-facts
generate-outline
write-all
review-all
build-md
build-docx
```

运行日志建议：

```text
[1/7] 解析评分标准...
[2/7] 提取全局事实...
[3/7] 生成大纲...
[4/7] 生成章节...
[5/7] 审核章节...
[6/7] 拼接 Markdown...
[7/7] 生成 Word...
```

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
5. 统一日志输出。

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

### 9.5 `src/score_parser.py`

职责：

1. 读取 `inputs/score.md`。
2. 调用模型解析评分点。
3. 校验 JSON。
4. 写入 `workspace/score_points.json`。

失败时保存原始输出：

```text
workspace/debug_parse_score_raw.txt
```

### 9.6 `src/fact_extractor.py`

职责：

1. 读取 `inputs/tender.md` 和 `inputs/company.md`。
2. 调用模型提取全局事实。
3. 校验 JSON。
4. 写入 `workspace/global_facts.json`。

### 9.7 `src/outline_generator.py`

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

### 9.8 `src/chapter_writer.py`

职责：

1. 读取大纲、评分点、全局事实、招标文件、公司资料。
2. 根据章节 ID 找到对应章节。
3. 生成当前章节 Markdown。
4. 写入 `workspace/chapters/{chapter_id}.md`。
5. 支持生成单章和全部章节。

章节生成要求：

1. 每章单独生成。
2. 每章只写当前章节内容。
3. 必须覆盖当前章节绑定的评分点。
4. 必须结合招标文件和公司资料。
5. 不允许编造公司资料中没有的资质、案例、人员、证书。
6. 输出 Markdown。
7. 表格使用 Markdown 表格。

### 9.9 `src/chapter_reviewer.py`

职责：

1. 读取章节 Markdown。
2. 读取大纲、评分点、全局事实。
3. 调用模型审核评分点覆盖情况。
4. 输出审核 JSON。
5. 支持审核单章和全部章节。

第一版只审核，不自动重写。

### 9.10 `src/docx_builder.py`

职责：

1. 读取 `outputs/final.md`。
2. 生成 `outputs/final.docx`。
3. 支持 Markdown 标题、段落、表格。
4. 如果 `inputs/template.docx` 存在，可以基于模板生成；否则新建空白 Word。

第一版不用做复杂样式还原。

### 9.11 `src/main.py`

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
3. 必须结合招标文件和公司资料。
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

### 阶段 10：总流程 `run`

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

第一版跑通后，再考虑第二版增强。

### 15.1 PDF / Word 解析

增加：

```text
PDF → Markdown
Word → Markdown
```

支持直接导入招标文件和模板。

### 15.2 模板占位符

支持：

```text
{{PROJECT_NAME}}
{{BIDDER_NAME}}
{{TECHNICAL_CONTENT}}
{{SERVICE_PERIOD}}
```

将内容精确填入模板位置。

### 15.3 自动补写

审核不通过时：

```text
章节审核 → 发现遗漏 → 自动补写 → 再审核
```

### 15.4 废标项检查

从招标文件中提取废标项、否决项、实质性响应条款，并在最终输出前检查。

### 15.5 知识库 / RAG

公司资料、产品资料、案例资料较多时，引入知识库检索，避免一次性塞入上下文。

### 15.6 任务恢复

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

第一版最终最小链路如下：

```bash
python src/main.py init

# 填写 inputs/tender.md、inputs/score.md、inputs/company.md

python src/main.py run
```

成功后应生成：

```text
outputs/final.docx
```

这就是第一版 MVP 的完成标志。
