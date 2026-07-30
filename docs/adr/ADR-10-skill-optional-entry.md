# ADR-10：外部 Skill 可选，内部版本化推理 Skill 可作为受控 Provider

- 状态：Accepted（2026-07-28 修订）
- 日期：2026-07-27
- 修订日期：2026-07-28

## 背景

“Skill”在本项目中有两种不同含义，必须明确区分：

1. **外部 Bid Master/Codex Skill**：面向用户的协作入口，例如接收 `/bid plan` 并调用
   Bid Master 公开 Command。
2. **BidAgent 内部版本化推理 Skill**：`Planning Agent` 内部的受控推理 Provider，以固定
   Skill ID、版本化 Prompt、强类型输入/输出 Schema 和受限能力完成专业语义决策。

二者不能混为一谈。外部 Skill 不是产品核心能力载体；内部推理 Skill 则可以是产品核心
Planning 能力的实现方式，但不能获得新的权威写入权限。

## 决策

### 外部 Skill

- 外部 Bid Master/Codex Skill 不在核心生产链上，只能调用 Bid Master 公开 Command。
- Web/API 在没有任何外部 Skill 时必须具备完整产品能力。
- 外部 Skill 禁止重新解析标书、保存核心 Prompt/状态、直读写 Artifact 或
  `control.db`、绕过 Gate、自动上传真实文件或建立第二套工作流。

### 内部版本化推理 Skill

- BidAgent 可以静态注册内部推理 Skill，并将其作为 `Planning Agent` 的核心 Provider。
- 内部 Skill 不是 Codex `SKILL.md`、外部插件、独立 Agent 或第二套状态机；它只在现有
  `CommandGateway → StageRunner → PlanningAgent → Proposal` 链中运行。
- 内部 Skill 只读取冻结的 promoted Artifact 快照，只输出强类型 Candidate/Proposal；
  不得直接写 canonical Artifact、active revision、数据库或最终文件。
- Skill ID、Skill 版本、Prompt checksum、模型、温度、输出 Schema 和 policy version
  必须进入 Proposal/dependency fingerprint，并参与 stale 传播。
- 内部 Skill 的调用权限必须由 CapabilityRegistry 静态授权；不得由模型自由发现、安装或
  调用任意工具。

### 规划目标链

```text
评分语义 LLM
→ 项目整体理解 LLM
→ Topic / Duty 语义规划 LLM
→ BidAgent 内部版本化章节目录拆分 Skill
→ exact Validation / G1 / G2 / Promotion
→ H1 PlanningConfirm
```

LLM 与内部 Skill 负责语义判断；规则负责来源、ID、引用、分值、权限、依赖、覆盖、模板和
树结构校验，以及 canonical ID/顺序等机械编译。规则不得代替内部 Skill 主导项目理解或
目录语义。

## 失败策略

- 模型不可用、Skill 调用失败、JSON/Schema 非法、引用不存在、G1/G2 不通过时一律
  fail closed。
- 禁止静默回退到规则生成的 `ProjectModel`、`ResponseTopicGraph` 或
  `ChapterBlueprint` 并作为正式结果返回。
- 确定性 baseline 只可用于测试、显式 shadow 或离线对照，不能冒充已执行 LLM/Skill。
- Skill/Prompt/模型/Schema/policy 或任何规划依赖变化后，旧 Proposal、Blueprint 和适用
  H1 必须按 dependency fingerprint 重新评估或失效。

## 保留的安全边界

- Agent/LLM/Skill 永远只产生 Proposal，不签发 Gate，不执行 Promotion。
- Validator 必须从 Store 重新读取 exact Proposal 并独立解析 active 依赖。
- `ChapterBlueprint` 仍是下游结构唯一权威，且必须通过 G2 与认证用户 H1。
- 外部 Skill 的安装与否不得改变 Web/API 的规划语义结果或安全门。
