# 质量门禁与最小修复开发计划

> 状态：**G0–G5 已落地**  
> 日期：2026-07-16  
> 路径：`docs/quality_gate_minimal_fix_plan.md`  
> 审核约定：**仅当产品明确回复「开始执行」后，才允许按本计划改代码。**  
> 关联文档：
> - [agentization_development_plan.md](./agentization_development_plan.md) — Agent 化双模架构  
> - [agentization_phase_status.md](./agentization_phase_status.md) — 已落地进度  
> - [current_logic_flow.md](./current_logic_flow.md) — 当前线上流程真相源  

---

## 0. 背景与问题

### 0.1 业务诉求（来自产品）

1. **每个节点发现问题，都不能进入下一步**（阻断级问题）。  
2. 需要 **重新生成/修复**，但应是 **有问题的内容单独改**，而不是整条流水线重跑。  
3. 需要判断 **问题可能由哪一步造成**（根因追溯），便于定点修复。  
4. 前端要 **直观展示** 问题与修复入口（类似节点成果预览，而不是只靠日志）。

### 0.2 现状能力（可复用）

| 能力 | 位置 | 用途 |
|------|------|------|
| 阶段注册与产物契约 | `pipeline_registry.py` | 定义节点顺序、依赖 |
| 失效传播 | `agent/invalidation.py` | 局部修改后标记下游过期 |
| 定向章节改稿 | `rewrite_chapters` / `subagent_runner` | 最小修复的主手段 |
| 覆盖/合规定向计划 | `fix_coverage` / `fix_compliance` | 问题驱动修复 |
| 全文审核阻断 | `quality_gates.validate_global_review_blocking` | 已有硬门禁 |
| 合规阻断 | `validate_compliance_blocking` | 已有硬门禁 |
| 节点大预览 | `StepDetailView` | 展示成果与问题明细 |
| 子 Agent 工作台 | `AgentWorkbench` + `activity.json` | 展示谁在干活 |
| 人工复核层 | `manual_review.py` | 不污染原始产物的覆盖 |

### 0.3 现状缺口

| 缺口 | 影响 |
|------|------|
| 问题模型不统一 | 各节点报错形态不一，前端难做「问题工作台」 |
| 多数节点无「阻断级」定义 | 发现问题仍可能继续 |
| 缺标准「根因 → 修复动作」表 | 用户不知道该重跑哪一步、改哪一章 |
| 缺「修复后最小重验链」 | 容易全量重跑或修完不复检 |
| 警告与阻断未分级产品化 | 要么全停、要么全不停 |

---

## 1. 目标与非目标

### 1.1 目标

1. **门禁停步**：任一阶段产生 **block** 级问题 → 自动流水线停止，禁止进入下一步。  
2. **问题可运营**：统一 Issue 列表，可在 UI 中查看、筛选、处理。  
3. **根因可提示**：每条 Issue 带 `likely_cause_stage` 与建议修复动作。  
4. **最小修复**：默认只改目标章节/评分点/材料，禁止默认全量 `run`。  
5. **修复后重验**：按失效表只重跑必要下游门禁，通过后才解锁下一步。  

### 1.2 成功标准（验收）

| ID | 标准 |
|----|------|
| A1 | 全文审核有未覆盖评分点/不一致等 block 原因时，auto_run **不能**进入 compliance/build |
| A2 | 合规 blocking 时，**不能**成功导出正式 docx |
| A3 | 章节审核若配置为硬门禁，有 blocker 时 **不能**进入 global-review |
| A4 | UI 对每条 block Issue 展示：描述、可能根因、建议动作按钮 |
| A5 | 「只修第 X 章」后，仅触发约定重验链，不整线重跑 |
| A6 | feature flag 可关闭新门禁策略，回退到改造前行为（合规/全文已有门禁除外可配置） |

### 1.3 非目标（本计划不做）

- 让 LLM 自由发明新阶段或跳过 registry 依赖。  
- 签章、原件证书、真实报价的「全自动解决」。  
- 多用户权限与审批流（仅预留 actor 字段）。  
- 一次改完所有历史节点的完美归因（先规则表，后可选 LLM 辅助）。  

---

## 2. 产品原则（审核时请逐条确认）

| # | 原则 | 建议默认 |
|---|------|----------|
| P1 | 只有 **block** 挡路；**warn** 不挡（出稿前汇总） | 是 |
| P2 | 「重新生成」= **最小修复**，全量重跑是最后手段 | 是 |
| P3 | 根因提示以 **规则表** 为准，LLM 仅辅助 | 是 |
| P4 | 人工项（签章等）只阻断 + 指引，不伪自动 | 是 |
| P5 | 修复必须 **重验同一门禁** 才算关闭 Issue | 是 |
| P6 | 单 workspace 同时仅一个「修复会话」进行中 | 是 |

**请审核人标注：同意 / 修改 / 反对（并给出替代）。**

---

## 3. 核心数据模型

### 3.1 Issue（问题单）

落盘建议：`workspace/issues/issues.jsonl`（追加）+ `workspace/issues/open.json`（当前 open 快照）。

```text
Issue:
  id: str                     # e.g. iss_a1b2
  stage_id: str               # 发现问题的阶段，如 global_review
  command: str                # global-review
  severity: block | warn | info
  code: str                   # e.g. UNCOVERED_SCORE / NAME_INCONSISTENT / COMPLIANCE_FATAL
  title: str
  detail: str
  evidence: object            # 原始片段/字段引用
  target:
    type: chapter | score_point | global | artifact | compliance_item
    ids: string[]
  likely_cause_stage: str     # 可能根因阶段 id
  suggested_actions: Action[]
  status: open | in_progress | fixed | accepted | wontfix
  created_at / updated_at
  source: gate | agent | rule | human
```

### 3.2 Action（修复动作）

```text
Action:
  type:
    rewrite_chapters          # 参数 chapter_ids
    rerun_stage               # 参数 command, force?
    fix_coverage              # 覆盖驱动
    fix_compliance            # 合规驱动
    upload_evidence           # 仅提示人工
    accept_risk               # 人工接受（需权限/二次确认，默认关闭）
    open_detail               # 打开节点预览
  label: str
  params: object
  risk_level: low|medium|high
```

### 3.3 GateResult（阶段门禁结果）

与阶段产物一并写入，例如：

- `workspace/global_review.json` 已有 `blocking` / `blocking_reasons`  
- 扩展：`issues: Issue[]` 或并行 `workspace/issues/by_stage/{stage_id}.json`

```text
GateResult:
  stage_id
  status: pass | warn | block
  issues: Issue[]
  can_proceed: bool
```

### 3.4 RepairPlan（最小修复计划）

```text
RepairPlan:
  issue_ids: string[]
  steps: [
    { action, params, why },
    { action: rerun_stage, command: "global-review", why: "重验门禁" }
  ]
  invalidates: string[]     # 将失效的产物
```

---

## 4. 严重级别与停步策略

### 4.1 级别定义

| 级别 | 含义 | 流水线 |
|------|------|--------|
| **block** | 交付风险/废标/一致性硬伤 | **停止**，禁止下一步 |
| **warn** | 质量风险、需关注 | 可继续，但出稿前汇总 |
| **info** | 提示 | 不挡 |

### 4.2 各阶段门禁策略（草案，待审核）

| 阶段 | 默认硬门禁（block 则停） | 说明 |
|------|--------------------------|------|
| prepare_inputs / split_docs | 缺关键产物 | 已有 requires |
| parse_score | 评分解为空/结构非法 | 建议硬停 |
| extract_facts | 可选 warn | 除非完全空 |
| generate_outline | 评分点未绑定章节 | 已有 quality_gates |
| write_chapters | 单章失败可部分继续或整段失败 | 建议：有失败章 → block 进入 review 前需处理 |
| review_fix_chapters | **可配置** | 建议：存在 blocker/major 未收敛 → block |
| global_review | **硬停** | 已实现 |
| compliance_check | **硬停** | 已实现 |
| build_md / build_docx | 上游 block 未清 → 禁止 | 已部分实现 |

**待审核点：**

- [ ] 章节审核是否默认硬停？  
- [ ] 未覆盖评分点数量阈值是否允许 warn 放行？（建议：**否**，有未覆盖即 block）  
- [ ] 用户是否允许「接受风险继续」？（建议：**P0 不做**，P2 再加管理员开关）  

---

## 5. 根因映射表（规则优先）

### 5.1 表结构

```text
IssueCode → {
  likely_cause_stages: [stage_id...],  # 按优先级
  default_actions: [ActionType...],
  revalidate: [command...]             # 修复后重验链
}
```

### 5.2 初版映射（实现时落到代码常量）

| IssueCode | 可能根因阶段（优先→次要） | 默认最小修复 | 重验链 |
|-----------|---------------------------|--------------|--------|
| UNCOVERED_SCORE | write_chapters → generate_outline → parse_score | rewrite 相关章 / 调整大纲归属 | build-score-coverage → global-review |
| NAME_INCONSISTENT | extract_facts → write_chapters | 改 facts + 改相关章 | global-review |
| CHAPTER_CONFLICT | write_chapters / review_fix | rewrite 冲突章 | global-review |
| FABRICATION_RISK | select_contexts → write_chapters | 补资料 + rewrite | review → global-review |
| CHAPTER_REVIEW_BLOCKER | write_chapters | rewrite 该章 | review-fix（该章） |
| COMPLIANCE_FATAL_STAR | write + 人工材料 | 人工★响应 + 定向章 | compliance-check |
| COMPLIANCE_QUAL | 人工材料 + write | 补资格章节/附件 | compliance-check |
| COMPLIANCE_DQ | write | 废标条款响应专章/偏离表 | compliance-check |
| PRICE_LIMIT | 人工报价 | 改报价表 | compliance-check |
| FORMAT_FAIL | build_md / template | 修 md/模板 | check-format |

### 5.3 归因算法（P0）

1. 由发现阶段 + IssueCode 查表。  
2. 若 target 含 chapter_ids，优先归因 `write_chapters`。  
3. 若无映射，归因 = 发现阶段本身，动作 = `open_detail` + 人工。  
4. **P1 可选**：LLM 辅助归因，但必须经规则白名单校验。  

---

## 6. 最小修复与重验

### 6.1 修复动作优先级

1. `rewrite_chapters(chapter_ids)`  
2. `fix_coverage` / `fix_compliance`（计划 → 确认执行）  
3. `rerun_stage`（仅上游解析/大纲类）  
4. 全量 `auto_run`（需二次确认，默认隐藏）  

### 6.2 重验链生成

```text
修复动作 → invalidation 表标记下游失效
→ RepairPlan.revalidate = 从「发现门禁」到「当前」的最小命令序列
→ 例如：rewrite 03章 → (可选 review 03) → global-review
→ 合规问题：rewrite/人工 → compliance-check
```

### 6.3 与 invalidation 的关系

- 继续使用 `workspace/agent/stale_artifacts.json`。  
- Issue 关闭条件：`status=fixed` **且** 对应门禁重验 `pass`。  
- 若用户只改文件未重验，Issue 保持 `open`，`can_proceed=false`。  

---

## 7. 后端设计

### 7.1 新增模块（建议）

```text
src/agent/issues.py          # Issue CRUD、快照
src/agent/root_cause.py      # 映射表与归因
src/agent/repair.py          # RepairPlan 生成与执行编排
src/agent/gates.py           # 统一 GateResult 适配（包装 quality_gates）
```

或合并到现有 `quality_gates.py` + `issues.py`，避免包膨胀。

### 7.2 阶段接入方式

每个关键 stage runner 结束时：

```text
1. 写业务产物
2. evaluate_gate(stage) → GateResult + Issues
3. persist issues
4. if block: raise RuntimeError(gate_message)  # 使 exit_code!=0，auto_run 停止
5. else return
```

**优先接入顺序：**

1. global_review（已有阻断，改为发 Issue）  
2. compliance_check（已有阻断，改为发 Issue）  
3. review_fix_chapters（新增可配置硬停）  
4. generate_outline / parse_score  
5. 其余阶段  

### 7.3 API（建议）

```text
GET  /api/issues?status=open
GET  /api/issues/{id}
POST /api/issues/{id}/actions/preview   # 返回 RepairPlan，不执行
POST /api/issues/{id}/actions/execute   # 确认后执行最小修复+重验
POST /api/gates/revalidate              # { command: "global-review" }
```

与现有：

- `GET /api/compliance-report`  
- `GET /api/workflow-step-detail`  
- `StepDetailView` 预览  

兼容：详情页展示 Issue 列表与「修复」按钮。

### 7.4 Tool 层扩展

| Tool | 说明 |
|------|------|
| `list_issues` | 只读 |
| `explain_issue` | 只读，含根因 |
| `repair_issue` | 需确认，执行 RepairPlan |
| `revalidate_gate` | 重跑门禁阶段 |

Supervisor 规则：用户说「修全文审核的问题」→ `list_issues` + `repair_issue` 计划。

### 7.5 配置项

```text
QUALITY_GATE_MODE=strict|soft     # strict=block 必停；soft=仅记录（调试）
GLOBAL_REVIEW_GATE=1              # 已有
CHAPTER_REVIEW_GATE=1             # 新增，默认 1 或 0 待审
ISSUE_ACCEPT_RISK_ENABLED=0       # 禁止接受风险继续
REPAIR_MAX_CHAPTERS=8
REPAIR_AUTO_REVALIDATE=1
```

---

## 8. 前端设计

### 8.1 交互主路径（按已确认方向）

- 节点成果 / 报告 / 阻断摘要 → **右侧大预览**（与 Word 预览同布局）。  
- 预览内展示：成果摘要 + **问题列表**。  
- 点问题 → 展开根因与动作：  
  - 「只重写相关章节」  
  - 「重跑本门禁」  
  - 「查看可能根因阶段」  

### 8.2 组件规划

| 组件 | 职责 |
|------|------|
| `StepDetailView` | 扩展：统一渲染 `issues[]` + 动作按钮 |
| `IssueList` / `IssueItem` | 筛选 block/warn、展开详情 |
| `RepairConfirmDialog` | 展示 RepairPlan、影响章节、将重验步骤 |
| 执行计划行 | 保持点击打开节点成果；block 时行状态为 error |

### 8.3 状态展示

- 流水线 `blocked`：计划区明确「已阻断：全文审核未通过」等。  
- 禁止在 block 时点亮「执行下一步」主按钮（或点后提示先处理问题）。  

### 8.4 不做的 UI

- 不用右侧文件区永久替换为问题列表（已按产品意见改为大预览）。  
- 日志仍保留，但是次要信息。  

---

## 9. 分阶段实施计划

> **再次强调：下列阶段仅在审核通过且指令「开始执行」后开工。**

### Phase G0 — 模型与协议（约 2–3 天）

**交付：**

- Issue / GateResult / Action 类型定义  
- `issues.py` 落盘读写  
- 将 global_review / compliance 现有阻断原因 **转换为 Issue**（适配层）  
- 单测：序列化、开关 flag  

**退出：** 不改变行为或仅增加字段；全量 unittest 绿。

### Phase G1 — 停步一致性（约 3–5 天）

**交付：**

- 统一 `can_proceed`：auto_run / run_stage / build_* 前检查 open block Issues  
- 章节审核门禁（可配置）  
- 前端阻断文案与禁止下一步  
- 单测 + 手工：global-review 失败后无法 start 下一步  

**退出：** A1/A2/A3 满足。

### Phase G2 — 根因与修复计划（约 1 周）

**交付：**

- `root_cause.py` 映射表  
- `repair.py` 生成 RepairPlan  
- API preview/execute  
- StepDetailView 问题 + 修复按钮  
- 与 `rewrite_chapters` / invalidation / 重验链打通  

**退出：** A4/A5 在 demo 上可演示「未覆盖评分点 → 只改相关章 → 重跑全文审核」。

### Phase G3 — 多门禁覆盖与硬化（约 1 周）

**交付：**

- outline/parse/write 等门禁发 Issue  
- 出稿前总检查：open block == 0  
- 指标：阻断次数、修复成功率、平均修复章节数  
- 文档同步 `current_logic_flow.md`  

### Phase G4 — 可选增强

- LLM 辅助归因（白名单）  
- accept_risk（管理员）  
- 批量修复多条 Issue  

---

## 10. 详细任务清单（G0–G2）

### G0

- [ ] 定义 `Issue`/`Action`/`GateResult`（Python dataclass + JSON schema）  
- [ ] `workspace/issues/` 读写与并发文件锁  
- [ ] `global_review` → Issues 适配  
- [ ] `compliance_report` → Issues 适配  
- [ ] 测试 `tests/test_issues_model.py`  

### G1

- [ ] `assert_can_proceed(root, next_command)`  
- [ ] 接入 `pipeline_supervisor` / `web_app` start-pipeline / run-command  
- [ ] `review_fix` 门禁策略 + 配置项  
- [ ] 前端阻断态与按钮禁用  
- [ ] 测试 `tests/test_gate_can_proceed.py`  

### G2

- [ ] 根因表 v1（第 5 章）  
- [ ] RepairPlan 生成器  
- [ ] API：preview/execute/revalidate  
- [ ] UI：Issue 列表 + 确认弹窗  
- [ ] 执行后写 invalidation + 重验  
- [ ] E2E 脚本或手工验收用例文档  

---

## 11. 风险与对策

| ID | 风险 | 对策 |
|----|------|------|
| R1 | 门禁过严导致永远跑不完 | 分级 block/warn；配置项放宽；清晰修复路径 |
| R2 | 归因错误导致修错章 | 规则表优先；UI 展示「可能」并允许改选章节 |
| R3 | 修复冲掉已有正确内容 | 章节锁 + 仅 touch 目标章 + 备份/mtime |
| R4 | 与 auto_recovery 冲突 | block 时禁止自动跳过门禁 |
| R5 | Issue 与报告双真相 | Issue 从报告生成，报告仍为权威产物 |
| R6 | 性能 | open.json 快照；列表分页 |

---

## 12. 测试计划

### 12.1 单测

- Issue 读写与状态机  
- 根因映射  
- can_proceed 矩阵（有/无 block，目标阶段）  
- RepairPlan 章节集合与 revalidate 序列  
- 与 global/compliance 门禁回归  

### 12.2 场景验收

1. 全文审核未覆盖评分点 → 停止 → 详情可见 Issue → 定向改章 → 重验通过 → 可继续。  
2. 合规 fatal → 停止 → 不可 build-docx。  
3. 仅 warn → 可继续，出稿前提示。  
4. flag soft 模式 → 不阻断（调试）。  

---

## 13. 文档与发布

- 实现后更新 `current_logic_flow.md`（门禁与停步行为）。  
- 更新 `agentization_phase_status.md` 增加 G0–G3 状态。  
- `.env.example` 增加门禁相关变量说明。  
- 发布说明：强调「阻断后不会静默继续」。  

---

## 14. 工作量粗估

| 阶段 | 工期 | 依赖审核点 |
|------|------|------------|
| G0 | 2–3 天 | 模型字段是否够用 |
| G1 | 3–5 天 | 哪些阶段硬停 |
| G2 | 5–7 天 | 修复动作是否要二次确认 |
| G3 | 5 天 | — |
| **合计** | **约 3–4 周** | 含联调与验收 |

---

## 15. 审核清单（请直接在副本上勾选）

### 15.1 原则

- [ ] 同意 P1–P6  
- [ ] 修改：________________  

### 15.2 门禁范围

- [ ] 同意第 4.2 表  
- [ ] 章节审核默认：硬停 / 软提示（请圈选）  
- [ ] 未覆盖评分点：一律 block / 允许阈值（请圈选）  

### 15.3 修复体验

- [ ] 修复前必须确认弹窗：是 / 否  
- [ ] 是否允许「接受风险继续」：P0 不做 / P2 做  

### 15.4 开工指令

- [ ] **计划通过，开始执行**（请书面回复此句）  
- [ ] 计划需修改后再审  
- [ ] 暂缓  

---

## 16. 附录：与现有阶段顺序

```text
init → prepare_inputs → split_docs → parse_score → extract_facts
→ build_template_evidence → generate_outline → plan_chapter_jobs
→ select_contexts → write_chapters → review_fix_chapters
→ source_trace → score_coverage → estimate_score
→ summarize → global_review  ← 硬门禁（已有）
→ compliance_check           ← 硬门禁（已有）
→ build_md → build_docx → check_format
```

本计划在「每个关键节点后」增加 **GateResult + Issues**，在硬门禁处强制 `can_proceed=false`。

---

## 17. 变更记录

| 版本 | 日期 | 说明 |
|------|------|------|
| v0.1 | 2026-07-16 | 首版详细计划，**待审核，未开工** |

---

**结论：**  
本文件仅描述方案与任务分解。  
**在你明确回复「开始执行」之前，不进行任何编码实现。**


---

## 18. 实施进度

| 阶段 | 状态 | 说明 |
|------|------|------|
| G0 | **完成** | Issue 模型、落盘、global/compliance 适配 |
| G1 | **完成** | can_proceed + 流水线阶段间门禁 + start/run 门禁 + UI 阻断条 |
| G2 | **完成骨架** | RepairPlan 预览/执行 API + 详情页修复按钮 + 重验链 |
| G3 | **完成** | 多阶段门禁发 Issue、出稿总检查、metrics、流程文档 |


### G3 实施记录

- 新增阶段 Issue：`review_fix` / `write` 失败 / `outline` / `empty score`
- 流水线阶段间 + 出稿前 `assert_can_proceed`
- `GET /api/issues/metrics` 阻断/修复计数
- `docs/current_logic_flow.md` 已同步门禁行为


### G5 实施记录（工具层贯通 + 出稿清单）

- Tool：`list_issues` / `explain_issue` / `repair_issue` / `export_preflight`
- `run_stage` / `build_export` 接入质量门禁；允许重验产生阻断的门禁阶段本身
- `GET /api/export-preflight` 出稿前检查清单
- 前端快捷按钮「出稿前检查」
- 修复：sync 在报告缺失时不再清空 Issue
- 测试：`tests/test_g5_tools_preflight.py`

### G4 实施记录

- `ISSUE_LLM_CAUSE_ENABLED`：LLM 归因，阶段白名单校验，非法阶段回退规则表
- `ISSUE_ACCEPT_RISK_ENABLED`：接受风险（需原因），accepted 不再阻断
- 批量修复：`/api/issues/actions/batch-preview|batch-execute`
- 详情页：智能归因 / 接受风险 / 批量修复按钮
- 测试：`tests/test_g4_enhancements.py`
