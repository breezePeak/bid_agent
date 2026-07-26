# 标书写作 Agent

本地辅助生成标书的工具：准备招标文件、公司资料与 Word 模板后，系统可自动整理资料、解析评分点、生成与审核章节，并输出可编辑的 `final.docx`。

## 适合谁用

- **业务使用**：如何上传资料、启动流程、拿到 Word → 看本文即可。
- **3D 态势大屏**：炼丹阁可视化全流程与 Agent 活动 → 见 [frontend-3d/README.md](frontend-3d/README.md)。
- **当前控制架构 V2**：Agent 控制面、唯一执行内核与迁移验收 → 见 [docs/current_logic_flow_v2.md](docs/current_logic_flow_v2.md)。
- **V1 历史归档**：仅供迁移取证，不提供兼容接口 → 见 [docs/current_logic_flow_v1.md](docs/current_logic_flow_v1.md)。

## 安装

```bash
conda create -n bid_agent python=3.11 -y
conda activate bid_agent
pip install -r requirements.txt
```

## 配置大模型

在项目根目录创建 `.env`，至少配置：

```bash
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=your-api-key
OPENAI_MODEL=gpt-4.1-mini
BID_AGENT_AUTH_USER=admin
BID_AGENT_AUTH_PASSWORD=请替换为高强度随机密码
```

可参考 `.env.example`。

## 怎么用（Web 控制台）

推荐使用 Web 页面（聊天编排 + 执行计划 + 工作空间）。

启动后端：

```bash
python src/web_app.py
```

打开 [http://127.0.0.1:7860](http://127.0.0.1:7860)

Web API 使用服务端会话认证；未配置 `BID_AGENT_AUTH_PASSWORD` 时会拒绝登录。工作区首次由管理员访问时写入所有者 ACL，之后所有 V2 API 请求都会校验会话及工作区权限；旧 V1 API 统一返回 `410 V1_API_RETIRED`。

建议顺序：

1. 创建工作空间  
2. 上传招标文件、公司资料、Word 模板  
3. 在对话中下达目标（如「一键生成标书」「继续整个流程」）或使用页面上的流水线操作  
4. 关注执行计划与阻断提示；材料/质量门禁未通过时按提示补齐  
5. 下载 `final.docx`

### 对话与「继续」

- 可用自然语言推进：例如「继续」「继续整个流程」「一键跑完」。  
- 变更类步骤默认需确认；确认后会按目标计划或流水线继续执行。  
- **目标是否「已完成」**以成功条件与开放阻断为准，不会仅因单次工具返回成功就标记整单完成。

## 3D 炼丹阁（可选）

独立 Three.js 大屏，实时或演示展示工序节点、丹炉结丹、道众列队与音效。

```bash
cd frontend-3d
npm install
npm run dev
```

浏览器打开 [http://127.0.0.1:5174](http://127.0.0.1:5174)（默认代理后端 `7860`）。

- 后端在线 → **观火（实时）**，轮询状态 / Agent 活动 / Goal  
- 后端离线 → **演法（演示）**，模拟一整轮后停在成丹，需点「重新炼制」再开  

详见 [frontend-3d/README.md](frontend-3d/README.md)。

## 资料怎么准备

- **招标文件**：招标书、评分办法、技术/商务要求  
- **公司资料**：介绍、资质、案例、人员、证书、方案素材  
- **Word 模板**：最终希望套用的样式模板  
- **外部参考资料（可选）**：政策、标准、行业资料、类似项目技术方法  
- **项目写作要求（可选）**：章节结构、重点、口径和方案粒度  

命令行整理时的目录：

```text
sources/tender/     招标文件
sources/company/    公司资料
sources/template/   Word 模板
sources/reference/  外部参考资料（不作为公司资质/业绩证据）
sources/guidance/   项目写作要求
```

外部参考资料和公司证据严格分开，具体见 [外部参考资料与项目写作要求](docs/research_and_writing_brief.md)。

## 流程概览

上传资料后，典型阶段包括：

1. 导入资料  
2. 解析评分点  
3. 提取公司与项目事实  
4. 生成大纲  
5. 为每章选择上下文  
6. 生成章节  
7. 审核并自动改写  
8. 汇总 Markdown  
9. 生成 Word  
10. 最终检查  

Web 端也可由 Supervisor 按 **Goal 计划**分步调用工具，遇到材料缺失或质量阻断时进入人工/补料，而不是假成功。

## 结果在哪里

```text
outputs/final.md
outputs/final.docx
```

Web 页面可直接下载。

## 常见情况

### 1. 页面刷新了，会不会中断

不会。当前版本会继续接着跑。

### 2. 大模型限流/超时后恢复

系统会自动重试，一般无需人工重启整条流水线。

### 3. 改了模型或 API Key

通常不需要重启服务；后续新请求会使用新配置。

### 4. 生成结果还要不要人工改

要。工具用于快速生成可继续加工的底稿，不能替代终稿责任与合规复核。

### 5. 提示「仍有开放阻断」却显示完成

新版本会拒绝在开放阻断 / 材料未齐时把 Goal 标为成功；请按 Issues / 聊天提示处理后再「继续」。

## 命令行用法

```bash
python src/main.py init
python src/main.py prepare-inputs
python src/main.py validate
python src/main.py graph-run --workers 2
```

V2 控制面 CLI 不直接调用阶段 runner，而是经过与聊天和页面按钮相同的认证、CommandGateway、确认和工作区 lease：

```bash
set BID_AGENT_AUTH_PASSWORD=your-password
python src/main.py control submit --workspace demo --kind pipeline.start --payload "{}"
python src/main.py control confirm --workspace demo --action-id ACTION_ID
python src/main.py control snapshot --workspace demo
```

## 建议

- 第一次使用优先走 Web 页面  
- 公司资料越完整，生成越稳  
- 模板越规范，Word 后处理越少  
- 资质承诺、商务条款、关键表述务必人工复核  

## 进一步说明

- 流程版本导航：[docs/current_logic_flow.md](docs/current_logic_flow.md)  
- 当前控制架构 V2：[docs/current_logic_flow_v2.md](docs/current_logic_flow_v2.md)
- V1 历史归档：[docs/current_logic_flow_v1.md](docs/current_logic_flow_v1.md)
- 开发演进：[docs/bid_agent_development_plan.md](docs/bid_agent_development_plan.md)  
- 历史 Agent 化阶段记录：[docs/agentization_development_plan.md](docs/agentization_development_plan.md)  
- 3D 前端：[frontend-3d/README.md](frontend-3d/README.md)  
