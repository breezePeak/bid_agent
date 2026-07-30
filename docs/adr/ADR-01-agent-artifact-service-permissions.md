# ADR-01：Agent / Artifact / Service 权限模型

- 状态：Accepted（PR-14.0 冻结）
- 日期：2026-07-27
- 关联：`agent.md` V3 主架构基线、PR-15.1 可信内核

## 背景

V3 需要防止 Agent、LLM 或普通 Service 直接写入权威事实。历史实现中存在“候选内容与运行时事实混用”的风险。

## 决策

固定四层分工：

```text
Agent    → Decision / Proposal
Service  → Deterministic Execution
Artifact → Versioned Truth
Gate     → Validation and Promotion Decision
```

硬约束：

1. Agent 只读冻结快照，只输出 `Proposal` / `Finding` / `RepairRequest`。
2. Agent 默认无 canonical Artifact 写权限、数据库写权限、任意文件读取权限和未声明外部工具权限。
3. 只有 `ArtifactPromotionService` 可通过 CAS 切换 active revision。
4. CapabilityRegistry / ArtifactKindRegistry 以 allow-list 约束角色可提议的 kind；未知角色与越权 kind 一律 fail closed。
5. 新增 Agent 不得获得绕过 Artifact / Gate / Promotion 的权威写入路径。

## 后果

- 所有语义写路径必须先形成 Proposal。
- 测试必须包含“Agent 直接写 canonical Artifact 失败”负向用例。
- 更换模型 Provider 不得改变权限边界。

## 引用

- `src/agent/capability_registry.py`
- `src/document_pipeline/artifact_registry.py`
- `src/document_pipeline/artifact_promotion.py`
