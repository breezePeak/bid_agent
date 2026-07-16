# 标书写作 Agent

这是一个用来辅助生成标书的本地工具。

你只需要准备 3 类资料：
- 招标文件
- 公司资料
- Word 标书模板

系统会自动完成资料整理、评分点解析、章节生成、审核改写，并输出最终的 `Word` 文件。

## 适合谁用

如果你更关心“怎么把资料丢进去，然后尽快拿到一版可编辑的标书”，看这份 `README` 就够了。

如果你要排查流程、理解阶段逻辑或看实现细节，再去看 [docs/current_logic_flow.md](/D:/my_project/bid_agent/docs/current_logic_flow.md:1)。

## 安装

```bash
conda create -n bid_agent python=3.11 -y
conda activate bid_agent
pip install -r requirements.txt
```

## 配置大模型

在项目根目录创建 `.env`，至少写这 3 项：

```bash
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=your-api-key
OPENAI_MODEL=gpt-4.1-mini
```

## 怎么用

最推荐直接用 Web 页面。

启动：

```bash
python src/web_app.py
```

打开 [http://127.0.0.1:7860](http://127.0.0.1:7860)

然后按这个顺序操作：

1. 创建一个工作空间
2. 上传招标文件
3. 上传公司资料
4. 上传 Word 模板
5. 等系统自动往下跑
6. 最后下载 `final.docx`

## 资料怎么准备

建议这样放：

- 招标文件：完整的招标书、评分办法、技术要求、商务要求
- 公司资料：公司介绍、资质、案例、人员、证书、方案素材
- Word 模板：你希望最终套用样式的模板文件

如果你用命令行方式整理资料，放置目录如下：

```text
sources/tender/     招标文件
sources/company/    公司资料
sources/template/   Word 模板
```

## 使用过程中你会看到什么

上传完资料后，系统会自动执行整套流程，一般会经过这些步骤：

1. 导入资料
2. 解析评分点
3. 提取公司和项目事实
4. 生成大纲
5. 为每章选择上下文
6. 生成章节
7. 审核并自动改写
8. 汇总成 Markdown
9. 生成 Word
10. 做最终检查

你不需要手动一阶段一阶段点下去，系统会自己往后推进。

## 结果在哪里

最终产物在：

```text
outputs/final.md
outputs/final.docx
```

在 Web 页面里也可以直接下载。

## 常见情况

### 1. 页面刷新了，会不会中断

不会。当前版本会继续接着跑。

### 2. 大模型刚刚限流/超时，后面恢复了怎么办

系统会自动重试。恢复后会继续当前阶段，一般不需要人工重新启动。

### 3. 我改了模型或 API Key，要不要重启

通常不需要。后续新的请求会自动使用新的配置。

### 4. 生成结果还要不要人工改

要。这个工具的定位是先帮你快速生成一版能继续加工的标书，不是完全替代人工定稿。

## 命令行用法

如果你不想用 Web，也可以直接跑命令：

```bash
python src/main.py init
python src/main.py prepare-inputs
python src/main.py validate
python src/main.py graph-run --workers 2
```

## 建议

- 第一次使用，优先走 Web 页面
- 公司资料越完整，生成效果越稳定
- 模板越规范，最终 Word 越省后处理
- 重要表述、资质承诺、商务条款，最后一定要人工复核

## 进一步说明

- 逻辑与流程说明：[docs/current_logic_flow.md](/D:/my_project/bid_agent/docs/current_logic_flow.md:1)
- 开发演进记录：[docs/bid_agent_development_plan.md
- Agent 化改造计划：[docs/agentization_development_plan.md](docs/agentization_development_plan.md)](/D:/my_project/bid_agent/docs/bid_agent_development_plan.md:1)
