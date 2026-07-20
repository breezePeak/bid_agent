/**
 * Pipeline stage metadata aligned with src/pipeline_registry.py
 * Groups map to visual zones on the 3D track.
 */

export const PHASES = [
  {
    id: 'prepare',
    label: '准备',
    color: '#38bdf8',
    description: '初始化与资料导入',
  },
  {
    id: 'analyze',
    label: '解析',
    color: '#a78bfa',
    description: '评分、事实与材料',
  },
  {
    id: 'plan',
    label: '规划',
    color: '#34d399',
    description: '大纲、任务与上下文',
  },
  {
    id: 'write',
    label: '写作',
    color: '#fbbf24',
    description: '章节并发生成',
  },
  {
    id: 'quality',
    label: '质检',
    color: '#f472b6',
    description: '审核、覆盖与合规',
  },
  {
    id: 'deliver',
    label: '交付',
    color: '#22d3ee',
    description: '成稿与格式检查',
  },
]

export const STAGE_DEFS = [
  { id: 'init_workspace', command: 'init', label: '初始化项目', phase: 'prepare', icon: '◎', agents: [] },
  { id: 'prepare_inputs', command: 'prepare-inputs', label: '导入资料', phase: 'prepare', icon: '⇪', agents: ['tender_block_classifier'] },
  { id: 'split_docs', command: 'split-docs', label: '切分文档', phase: 'prepare', icon: '⧉', agents: [] },
  { id: 'parse_score', command: 'parse-score', label: '解析评分', phase: 'analyze', icon: '★', agents: ['score_requirement_extractor', 'score_point_parser'] },
  { id: 'extract_facts', command: 'extract-facts', label: '提取事实', phase: 'analyze', icon: '✦', agents: ['tender_requirement_extractor', 'company_facts_extractor'] },
  { id: 'build_materials_checklist', command: 'build-materials-checklist', label: '材料资格清单', phase: 'analyze', icon: '☰', agents: [] },
  { id: 'build_template_evidence', command: 'build-template-evidence', label: '生成模板依据', phase: 'analyze', icon: '▤', agents: [] },
  { id: 'generate_outline', command: 'generate-outline', label: '生成大纲', phase: 'plan', icon: '◈', agents: ['outline_generator'] },
  { id: 'plan_chapter_jobs', command: 'plan-jobs', label: '生成任务', phase: 'plan', icon: '☷', agents: [] },
  { id: 'select_contexts', command: 'select-context-all', label: '选择上下文', phase: 'plan', icon: '◎', agents: ['chapter_context_selector'] },
  { id: 'write_chapters', command: 'write-all', label: '生成章节', phase: 'write', icon: '✎', agents: ['chapter_writer'] },
  { id: 'review_fix_chapters', command: 'review-fix-all', label: '审核改稿', phase: 'quality', icon: '✓', agents: ['chapter_reviewer', 'chapter_rewriter'] },
  { id: 'build_source_trace_index', command: 'build-source-trace', label: '来源追溯', phase: 'quality', icon: '⇢', agents: [] },
  { id: 'build_score_coverage_matrix', command: 'build-score-coverage', label: '评分覆盖矩阵', phase: 'quality', icon: '▦', agents: [] },
  { id: 'estimate_final_score', command: 'estimate-score', label: '终稿估分', phase: 'quality', icon: '◉', agents: [] },
  { id: 'summarize_chapters', command: 'summarize-all', label: '生成摘要', phase: 'quality', icon: '≡', agents: ['chapter_summarizer'] },
  { id: 'global_review', command: 'global-review', label: '全文审核', phase: 'quality', icon: '◎', agents: ['global_reviewer'] },
  { id: 'compliance_check', command: 'compliance-check', label: '专项合规检查', phase: 'quality', icon: '⛨', agents: [] },
  { id: 'build_markdown', command: 'build-md', label: '拼接 MD', phase: 'deliver', icon: '☰', agents: [] },
  { id: 'build_docx', command: 'build-docx', label: '生成 Word', phase: 'deliver', icon: '📄', agents: [] },
  { id: 'check_format', command: 'check-format', label: '检查格式', phase: 'deliver', icon: '✔', agents: [] },
]

export const PHASE_BY_ID = Object.fromEntries(PHASES.map((p) => [p.id, p]))

export const AGENT_ROLE_META = {
  coordinator: { label: '主 Agent', color: '#818cf8', emoji: '🧭', tier: 'boss' },
  chapter_writer: { label: '写作 Agent', color: '#60a5fa', emoji: '✍️', tier: 'worker' },
  chapter_reviewer: { label: '审核 Agent', color: '#c084fc', emoji: '🔍', tier: 'worker' },
  chapter_rewriter: { label: '改稿 Agent', color: '#fb923c', emoji: '📝', tier: 'worker' },
  global_reviewer: { label: '全文审核', color: '#2dd4bf', emoji: '📋', tier: 'specialist' },
  pipeline: { label: '流水线', color: '#94a3b8', emoji: '⚙️', tier: 'system' },
  outline_generator: { label: '大纲 Agent', color: '#34d399', emoji: '◈', tier: 'specialist' },
  chapter_context_selector: { label: '上下文 Agent', color: '#a3e635', emoji: '◎', tier: 'worker' },
  tender_requirement_extractor: { label: '需求抽取', color: '#a78bfa', emoji: '✦', tier: 'specialist' },
  company_facts_extractor: { label: '事实抽取', color: '#e879f9', emoji: '✦', tier: 'specialist' },
  score_requirement_extractor: { label: '评分抽取', color: '#fbbf24', emoji: '★', tier: 'specialist' },
  score_point_parser: { label: '评分点解析', color: '#f59e0b', emoji: '★', tier: 'specialist' },
  chapter_summarizer: { label: '摘要 Agent', color: '#67e8f9', emoji: '≡', tier: 'worker' },
  tender_block_classifier: { label: '文档分类', color: '#38bdf8', emoji: '⇪', tier: 'specialist' },
}

export function roleMeta(role) {
  return AGENT_ROLE_META[role] || { label: role || 'Agent', color: '#94a3b8', emoji: '🤖', tier: 'worker' }
}

export function stateColor(state) {
  switch (state) {
    case 'done':
      return '#22d3ee'
    case 'running':
      return '#fbbf24'
    case 'ready':
      return '#60a5fa'
    case 'error':
    case 'failed':
      return '#f87171'
    case 'blocked':
      return '#fb923c'
    case 'queued':
      return '#94a3b8'
    default:
      return '#475569'
  }
}

export function agentStatusColor(status) {
  switch (status) {
    case 'running':
      return '#fbbf24'
    case 'done':
      return '#34d399'
    case 'failed':
      return '#f87171'
    case 'queued':
      return '#64748b'
    case 'skipped':
      return '#475569'
    default:
      return '#64748b'
  }
}
