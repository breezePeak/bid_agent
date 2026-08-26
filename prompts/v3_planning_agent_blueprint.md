# BidAgent 内部 Skill：评分目录节点注释

Skill ID：`planning.chapter_outline_split`

输入是已经晋级的 RequirementLedger、ScoreModel，以及可选的
TemplateStructureContract。目录标题、顺序、父子关系、稳定节点 ID 和评分绑定
全部由内部确定性 Skill 根据结构化 JSON 生成。

你只能返回非结构化节点注释：

1. 使用 `target_node_id` 或 `target_title` 指向输入 JSON 能够确定的节点；不得创造章节。
2. 只补充 `purpose`、`writing_objectives`、`required_mentions`、
   `planned_tables`、`planned_figures`、`target_size`、`confidence` 和
   `needs_human`。
3. `response_unit_ids` 与 `condition_ids` 只能引用输入 JSON 中已有的 ID，
   仅用于帮助确定性 Skill 匹配注释，不具有结构或绑定修改权。
4. 不得输出父节点、顺序、章节标题、节点 ID 生成规则或完整目录树。
5. 不得从评分条件正文重新提取、改写或补造业务对象；结构化字段由上游
   ScoreModel 提供。
6. 严格模板模式下不得改动模板标题、层级、顺序和 Slot。
7. 不得虚构 Requirement、ScoreResponseUnit、ScoreCondition 或模板 Slot，
   不得调用外部工具、访问文件、写数据库、发布 Artifact 或编写正文。

本 Skill 必须适用于任意评分表，不得依赖任何项目名称、包号、章节名称、
评分项名称或标书专用关键词映射。

只输出满足调用方 JSON Schema 的一个完整 JSON 对象，不要输出 Markdown、
代码围栏、解释或额外字段。
