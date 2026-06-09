# 标书写作 Agent MVP

本项目是一个本地 CLI 版标书写作 Agent，使用 LangGraph 编排主流程和章节 SubAgent。第一版只处理 Markdown 输入，输出 Markdown 和 Word。

## 初始化

```bash
python src/main.py init
```

初始化后填写：

- `inputs/tender.md`：招标文件
- `inputs/score.md`：评分标准
- `inputs/company.md`：公司资料

复制 `.env.example` 为 `.env`，并配置：

```env
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=your-api-key
OPENAI_MODEL=gpt-4.1-mini
```

安装 Word 生成依赖：

```bash
pip install -r requirements.txt
```

## 运行

完整流程：

```bash
python src/main.py run --workers 3
```

`run` 等价于：

```bash
python src/main.py graph-run --workers 3
```

单步命令：

```bash
python src/main.py parse-score
python src/main.py extract-facts
python src/main.py generate-outline
python src/main.py write-chapter --chapter 01
python src/main.py write-all
python src/main.py review-chapter --chapter 01
python src/main.py review-all
python src/main.py build-md
python src/main.py build-docx
```

## LangGraph 流程

主图节点：

```text
START
  -> init_workspace
  -> split_docs
  -> parse_score
  -> extract_facts
  -> generate_outline
  -> plan_chapter_jobs
  -> select_contexts
  -> write_chapters
  -> review_chapters
  -> global_review
  -> build_markdown
  -> build_docx
  -> END
```

章节写作由 `src/graph/chapter_subgraph.py` 中的章节子图执行。章节子图只读取当前章节 job、context 里选中的 chunks、全局事实和绑定评分点，不再读取完整 `tender.md` / `company.md`。

## 输出

- `workspace/chunks/tender_chunks.json`
- `workspace/chunks/company_chunks.json`
- `workspace/score_points.json`
- `workspace/global_facts.json`
- `workspace/outline.json`
- `workspace/jobs/*.json`
- `workspace/contexts/*_context.json`
- `workspace/chapters/*.md`
- `workspace/reviews/*.json`
- `workspace/global_review.json`
- `outputs/final.md`
- `outputs/final.docx`
