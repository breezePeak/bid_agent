/**
 * Pipeline stage metadata aligned with src/pipeline_registry.py
 * Groups map to visual zones on the 3D track.
 */

export const PHASES = [
  {
    id: 'prepare',
    label: '采药',
    color: '#5fa88a',
    description: '初始化与资料导入',
  },
  {
    id: 'analyze',
    label: '辨材',
    color: '#c49b4e',
    description: '评分、事实与材料',
  },
  {
    id: 'plan',
    label: '立鼎',
    color: '#7ec9a8',
    description: '大纲、任务与上下文',
  },
  {
    id: 'write',
    label: '炼丹',
    color: '#d44a32',
    description: '章节并发生成',
  },
  {
    id: 'quality',
    label: '点化',
    color: '#e0b44a',
    description: '审核、覆盖与合规',
  },
  {
    id: 'deliver',
    label: '出炉',
    color: '#ff8a3d',
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
  coordinator: { label: '掌炉真人', color: '#e0b44a', emoji: '☯', tier: 'boss' },
  chapter_writer: { label: '炼丹弟子', color: '#d44a32', emoji: '🔥', tier: 'worker' },
  chapter_reviewer: { label: '验丹长老', color: '#5fa88a', emoji: '👁', tier: 'worker' },
  chapter_rewriter: { label: '改火道人', color: '#ff8a3d', emoji: '✎', tier: 'worker' },
  global_reviewer: { label: '总坛真人', color: '#c49b4e', emoji: '📜', tier: 'specialist' },
  pipeline: { label: '丹道周天', color: '#9a8568', emoji: '◎', tier: 'system' },
  outline_generator: { label: '立鼎师', color: '#7ec9a8', emoji: '◈', tier: 'specialist' },
  chapter_context_selector: { label: '采药童子', color: '#8b9e6b', emoji: '🌿', tier: 'worker' },
  tender_requirement_extractor: { label: '辨材道人', color: '#c49b4e', emoji: '✦', tier: 'specialist' },
  company_facts_extractor: { label: '察事实者', color: '#d48a50', emoji: '✦', tier: 'specialist' },
  score_requirement_extractor: { label: '评品真人', color: '#e0b44a', emoji: '★', tier: 'specialist' },
  score_point_parser: { label: '析分道友', color: '#d4a040', emoji: '★', tier: 'specialist' },
  chapter_summarizer: { label: '录丹史', color: '#a89878', emoji: '≡', tier: 'worker' },
  tender_block_classifier: { label: '分拣药童', color: '#8b7355', emoji: '⇪', tier: 'specialist' },
}

export function roleMeta(role) {
  return AGENT_ROLE_META[role] || { label: role || '道友', color: '#9a8568', emoji: '☯', tier: 'worker' }
}

export function stateColor(state) {
  switch (state) {
    case 'done':
      return '#5fa88a'
    case 'running':
      return '#ff8a3d'
    case 'ready':
      return '#e0b44a'
    case 'error':
    case 'failed':
      return '#e05555'
    case 'blocked':
      return '#d48a50'
    case 'queued':
      return '#9a8568'
    default:
      return '#5a4a38'
  }
}

export function agentStatusColor(status) {
  switch (status) {
    case 'running':
      return '#ff8a3d'
    case 'done':
      return '#5fa88a'
    case 'failed':
      return '#e05555'
    case 'queued':
      return '#9a8568'
    case 'skipped':
      return '#5a4a38'
    default:
      return '#9a8568'
  }
}
