# 最终验收清单与 Definition of Done

> 上线前逐项勾选。任何 blocking 项未完成，不得以“后续优化”名义放行。

## 1. 基线与仓库

- [ ] 实现基线 commit 已记录。
- [ ] 计划涉及文件与当前 HEAD 重新核对。
- [ ] V1/V2 未恢复。
- [ ] 没有新增第二套 Writer。
- [ ] 没有新增第二套工作台。
- [ ] 当前 CI 全绿。
- [ ] Feature Flag 全关时基线回归全绿。
- [ ] 旧工作空间 fixture 已保存并可重复。

---

## 2. 项目创建与模式

- [ ] `writing_mode` 与 `document_mode` 独立。
- [ ] `full_write` 可创建。
- [ ] `bid_rewrite` 可创建。
- [ ] `project_type` 持久化。
- [ ] `expected_pages` 持久化。
- [ ] 刷新后字段不丢。
- [ ] 旧请求只传 name 仍成功。
- [ ] 非法 mode 被拒绝。
- [ ] bid 功能关闭时有明确状态，不静默降级。
- [ ] workspace list/snapshot 返回正确 mode/capability。

---

## 3. 数据库与迁移

- [ ] Schema 29 到最终版本成功。
- [ ] 空库初始化成功。
- [ ] 迁移重复执行成功。
- [ ] 中断迁移可恢复。
- [ ] 并发初始化安全。
- [ ] `PRAGMA quick_check` 通过。
- [ ] `PRAGMA foreign_key_check` 通过。
- [ ] 旧数据未丢。
- [ ] 新列默认值正确。
- [ ] 旧 content revision 未伪造 plan refs。
- [ ] head 指针有对应 revision。
- [ ] confirmed plan 有 receipt。
- [ ] 备份和恢复演练完成。
- [ ] 回滚旧代码可读取。

---

## 4. 架构边界

- [ ] `ChapterBlueprint` 仍是章节结构权威。
- [ ] Writer 只消费 `WriterInputBundle`。
- [ ] Writer 无联网工具。
- [ ] Writer 无数据库写权限。
- [ ] Writer 无任意工作空间读取权限。
- [ ] Agent 不能直接写 canonical plan。
- [ ] Agent 不能直接切 head revision。
- [ ] Proposal/Validation/Gate/Promotion 完整。
- [ ] CAS 生效。
- [ ] 幂等生效。
- [ ] stale base 被拒。
- [ ] receipt 重放被拒。
- [ ] 全局 H1 仍只有一次。
- [ ] 章节 plan confirmation 不可改 Blueprint。

---

## 5. LegacyBidIndex

- [ ] `InputRole.LEGACY_BID` 正常上传。
- [ ] 多旧投标书支持。
- [ ] 文件替换支持。
- [ ] 所有旧段落使用 canonical `block_id`。
- [ ] 目录树 parent 无环。
- [ ] 父章节 lead 未丢。
- [ ] preamble 有归属。
- [ ] 表格/图片引用保留。
- [ ] 章节描述完整。
- [ ] 段落描述完整。
- [ ] 描述不代替原文。
- [ ] 污染实体风险记录。
- [ ] Coverage Gate 达标。
- [ ] LegacyBidIndex 注册 Artifact Registry。
- [ ] Promotion dependency 精确。
- [ ] Markdown 是投影，不是权威。
- [ ] Markdown 可下载和人工核对。
- [ ] projection 失败不损坏 canonical。

---

## 6. 新旧目录融合

- [ ] 新招标和评分拥有内容主权。
- [ ] 旧目录只作结构候选。
- [ ] 可以保留旧下级结构。
- [ ] 可以改名。
- [ ] 可以合并。
- [ ] 可以拆分。
- [ ] 可以忽略。
- [ ] 可以根据新要求新增。
- [ ] `outline_lineage` 精确。
- [ ] auto outline 正确。
- [ ] template strict 不改 topology。
- [ ] ScoreResponseUnit primary 唯一。
- [ ] condition 覆盖完整。
- [ ] requirement 覆盖完整。
- [ ] 用户可修改目录。
- [ ] H1 receipt exact。
- [ ] full_write 目录结果无回归。

---

## 7. ChapterWritingPlan

- [ ] content unit stable ID。
- [ ] plan sources 唯一。
- [ ] bindings 两端合法。
- [ ] exclusions 生效。
- [ ] dependency fingerprint 稳定。
- [ ] plan revision append-only。
- [ ] plan history 可查。
- [ ] human confirmation。
- [ ] delegated confirmation。
- [ ] auto confirmation。
- [ ] confirmation 绑定 exact hash。
- [ ] plan edit 产生新 revision。
- [ ] plan stale 不能写。
- [ ] plan failure 保留诊断。
- [ ] 规划图不存坐标。
- [ ] plan summary 不携带全文。
- [ ] 章节隔离。

---

## 8. 搜索

- [ ] 搜索发生在规划阶段。
- [ ] 搜索有明确 gap。
- [ ] 查询有预算。
- [ ] 搜索摘要仅候选。
- [ ] 原文抓取后才 ready。
- [ ] lead_only 不能进 Writer。
- [ ] 搜索失败可局部重试。
- [ ] 用户可继续搜索。
- [ ] 用户可仅用现有资料。
- [ ] blocking gap 不能自动接受。
- [ ] 规划确认后 Writer 不搜索。
- [ ] `run_research=true` 在新路径被拒。
- [ ] 搜索来源有 snapshot/hash。
- [ ] 过期/冲突来源有风险。

---

## 9. 改写类型

- [ ] copy 判定准确。
- [ ] light_edit 判定准确。
- [ ] restructure 判定准确。
- [ ] new_write 判定准确。
- [ ] 类型由后端确定性计算。
- [ ] Gate 重算。
- [ ] 用户编辑来源后动态更新。
- [ ] parent mixed 仅为汇总。
- [ ] badge 与 title 分离。
- [ ] 类型理由可查看。
- [ ] 最终导出不含 badge。

---

## 10. Writer 统一链路

- [ ] 全量与改写共用 `ChapterWritingService`。
- [ ] 共用 `ContentWriter`。
- [ ] Bundle 带 plan refs。
- [ ] Bundle 只含确认来源。
- [ ] 用户删除来源不进入 Bundle。
- [ ] 相关但未选择来源不进入 Bundle。
- [ ] lead_only 不进入 Bundle。
- [ ] 旧标书只传 selected blocks。
- [ ] exclusion 传入 Writer。
- [ ] 写作后来源 Gate。
- [ ] 写作后污染 Gate。
- [ ] gap 返回 plan revision required。
- [ ] content revision 保存 plan refs。
- [ ] 同 plan 重写可用。
- [ ] 新 plan 使旧 draft stale。
- [ ] 不产生半提交 revision。

---

## 11. 前端工作台

- [ ] 中间有“编写逻辑/正文”。
- [ ] 无正文默认编写逻辑。
- [ ] 有正文默认正文。
- [ ] 用户手动切换不会被普通刷新抢走。
- [ ] 规划图三列。
- [ ] 来源卡。
- [ ] 内容块卡。
- [ ] 目标章节卡。
- [ ] 连接线可选。
- [ ] 详情 drawer。
- [ ] 原文按需加载。
- [ ] 大图可折叠。
- [ ] 有列表 fallback。
- [ ] plan API 失败不白屏。
- [ ] chat 失败不影响中间。
- [ ] 正文失败不影响规划。
- [ ] stale banner。
- [ ] 409 保留用户编辑。
- [ ] 结构化开始编写。
- [ ] 不再靠“开始编写本章正文”猜动作。
- [ ] 页面刷新恢复状态。
- [ ] SSE/NDJSON 重连去重。
- [ ] 100 节点性能达标。
- [ ] 键盘和文本可访问性。

---

## 12. 权限模式

- [ ] human_review 需用户确认。
- [ ] delegate_review 有代审回执。
- [ ] full_authority 有 auto 回执。
- [ ] full_authority 默认不批量预写整本。
- [ ] `_authority.json` 不再是新 plan 权威。
- [ ] 旧 workspace 仍可读旧 authority。
- [ ] 聊天历史不丢。
- [ ] 规划确认与权限模式分离。

---

## 13. 批量编写

- [ ] 只选择已确认 plan。
- [ ] stale plan 不可入队。
- [ ] 队列冻结 plan refs。
- [ ] 每项开始前复验。
- [ ] 单项失败不破坏其他项。
- [ ] plan stale 错误明确。
- [ ] pause/resume。
- [ ] restart recovery。
- [ ] idempotency 防重复。
- [ ] 旧 batch 路径 Flag off 回归。
- [ ] UI 显示不可编写原因。

---

## 14. 陈旧传播

- [ ] Blueprint 变化。
- [ ] chapter context 变化。
- [ ] global fact 变化。
- [ ] source input replacement。
- [ ] evidence superseded。
- [ ] plan edit。
- [ ] 无关 chat 不影响。
- [ ] 无关章节不影响。
- [ ] 旧 plan/draft 保留。
- [ ] stale draft 不能确认。
- [ ] stale 原因可见。
- [ ] dependency 不含不稳定时间戳。

---

## 15. 旧项目污染

- [ ] 项目名。
- [ ] 甲方。
- [ ] 投标人。
- [ ] 年份。
- [ ] 日期。
- [ ] 地点。
- [ ] 工期。
- [ ] 人员。
- [ ] 数量。
- [ ] 金额。
- [ ] 平台版本。
- [ ] 标准版本。
- [ ] 联系方式。
- [ ] 旧评分术语。
- [ ] 规划风险。
- [ ] Bundle forbidden。
- [ ] 正文后 Gate。
- [ ] blocking 命中不提交正常草稿。

---

## 16. 导出

- [ ] Word 正常。
- [ ] PDF/最终产物正常。
- [ ] Markdown 正常。
- [ ] 章节顺序正常。
- [ ] title 正常。
- [ ] badge 不导出。
- [ ] source ID 不导出。
- [ ] plan instruction 不导出。
- [ ] 旧文档路径不导出。
- [ ] 内部评分 ID 不导出。
- [ ] 旧 workspace 导出回归。

---

## 17. 故障与恢复

- [ ] LLM timeout。
- [ ] LLM invalid JSON。
- [ ] LLM wrong IDs。
- [ ] 搜索 timeout。
- [ ] 原文抓取失败。
- [ ] SQLite lock。
- [ ] disk full。
- [ ] 浏览器断线。
- [ ] 重复事件。
- [ ] 双击。
- [ ] 两标签页。
- [ ] 服务重启。
- [ ] batch 重启。
- [ ] migration 中断。
- [ ] 每种失败有明确状态和下一步。
- [ ] 不存在永久 loading。

---

## 18. 性能

- [ ] 大旧标书索引内存受控。
- [ ] 候选检索 P95 达标。
- [ ] Plan GET P95 达标。
- [ ] Confirm P95 达标。
- [ ] snapshot 无全文。
- [ ] source detail lazy。
- [ ] 图 100 节点达标。
- [ ] 50 次切章无明显内存泄漏。
- [ ] batch 查询有索引。
- [ ] 不用加无限超时掩盖问题。

---

## 19. 安全

- [ ] ACL。
- [ ] CSRF。
- [ ] cross workspace。
- [ ] cross chapter。
- [ ] path traversal。
- [ ] URL scheme。
- [ ] prompt injection 作为资料。
- [ ] source ID forgery。
- [ ] receipt replay。
- [ ] 日志脱敏。
- [ ] 诊断包受控。
- [ ] Writer 权限测试。
- [ ] Agent 越权测试。

---

## 20. 业务场景验收

### 全量编写

- [ ] 新建。
- [ ] 上传。
- [ ] H1。
- [ ] 规划。
- [ ] 搜索。
- [ ] 删除来源。
- [ ] 确认。
- [ ] 逐章写。
- [ ] 编辑。
- [ ] 确认。
- [ ] 批量。
- [ ] 导出。

### 标书改写

- [ ] 新旧文件。
- [ ] 旧索引。
- [ ] 融合目录。
- [ ] 目录修改/H1。
- [ ] 旧段落图。
- [ ] Web 补充。
- [ ] 四种类型。
- [ ] 动态重算。
- [ ] 逐章写。
- [ ] 污染检查。
- [ ] 导出。

### 旧工作空间

- [ ] 打开。
- [ ] 聊天。
- [ ] 写作。
- [ ] 批量。
- [ ] 确认。
- [ ] 导出。
- [ ] Flag off 完整回归。

---

## 21. 发布签字

- [ ] 架构负责人确认。
- [ ] 后端负责人确认。
- [ ] 前端负责人确认。
- [ ] 质量负责人确认。
- [ ] 发布负责人确认。
- [ ] 回滚负责人确认。
- [ ] 真实用户/业务验收确认。

只有所有 blocking 项完成，才能称为“开发完成”。代码能启动只是它最基本的礼貌，不是验收。
