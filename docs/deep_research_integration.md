# 章节 Deep Research 实际链路

章节公开资料研究仍属于现有 Evidence Service，不新增正文入口、顶层 Runner 或可写
canonical Artifact 的 Agent：

```text
WriterResearchCoordinator / AutonomousResearchCoordinator / V3ResearchTool
  -> ResearchService.resolve(EvidenceNeed)
  -> DeepResearchTavilyAdapter.research_need()
  -> DeepResearchEngine（内部、短生命周期、严格预算）
     -> SupervisorPlan：1-4 Claims、最多 3 Research Units
     -> Researcher loop：Search metadata -> URL selection -> Extract raw source
     -> EvidenceSufficiencyGate
  -> ResearchCandidate[] + DeepResearchRunResult
  -> ResearchService 相关性、authority class、anchor 和 usage constraint 校验
  -> immutable EvidenceBatch
  -> WriterInputBundle evidence snapshot
  -> ContentWriter.write_bundle()
```

`web_search` 明确关闭 Tavily answer 与 raw content，仅把 `result.content` 保存为
`WebSearchHit.snippet` 供 URL 初筛。`ResearchCandidate.content` 只能由
`web_extract` 成功返回的 `raw_content` 构造；提取失败、正文过短或 URL 被安全规则拒绝时
不会回退到 snippet。

最终充分性由确定性 Gate 判定。所有 required Claim 都必须有成功 Extract 的原文，且满足
项目 anchor、官方/标准来源或独立来源数量规则。模型声明完成不能绕过 Gate；资料不足且仍
有预算时继续研究，预算耗尽形成 gap，gap/failed batch 不会进入 Writer。

运行轨迹以非权威、append-only JSON 保存到
`workspace/v3/evidence/research_runs/<run_id>.json`。正式 Evidence ID、batch 发布和
EvidenceNeed 状态更新仍只由 `ResearchService` 执行。企业资质、业绩、案例、人员、财务、
报价、承诺、法定代表人和本企业能力相关 Need 在首次 Search 前被拒绝。
