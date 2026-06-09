# 标书写作 Agent MVP

本项目是一个本地 CLI 版标书写作 Agent，第一版只处理 Markdown 输入，输出 Markdown 和 Word。

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
python src/main.py run
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

## 输出

- `workspace/score_points.json`
- `workspace/global_facts.json`
- `workspace/outline.json`
- `workspace/chapters/*.md`
- `workspace/reviews/*.json`
- `outputs/final.md`
- `outputs/final.docx`
