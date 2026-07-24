export function statusFromV2Snapshot(snapshot) {
  if (!snapshot || typeof snapshot !== 'object') return null
  const presentation = snapshot.presentation && typeof snapshot.presentation === 'object'
    ? snapshot.presentation
    : {}
  const materials = snapshot.materials && typeof snapshot.materials === 'object'
    ? snapshot.materials
    : {}
  const findings = snapshot.findings && typeof snapshot.findings === 'object'
    ? snapshot.findings
    : {}
  const quality = snapshot.quality && typeof snapshot.quality === 'object'
    ? snapshot.quality
    : {}
  const goal = snapshot.goal
    && typeof snapshot.goal === 'object'
    && (snapshot.goal.goal_id || snapshot.goal.status || snapshot.goal.raw_user_goal || snapshot.goal.summary)
    ? snapshot.goal
    : null
  const rawPipeline = snapshot.pipeline && typeof snapshot.pipeline === 'object'
    ? snapshot.pipeline
    : null
  const pipelineStatus = String(rawPipeline?.status || '')
  const hasPipelineAuthority = Boolean(rawPipeline && pipelineStatus)
  const pipelineRunning = ['running', 'recovering', 'retrying', 'pausing', 'cancelling'].includes(pipelineStatus)
  const currentStageRuns = Array.isArray(snapshot.current_stage_runs) ? snapshot.current_stage_runs : []
  const pipelineStageRuns = Array.isArray(snapshot.pipeline_stage_runs)
    ? snapshot.pipeline_stage_runs
    : currentStageRuns.filter(run => (
        !rawPipeline?.operation_id
        || String(run?.operation_id || '') === String(rawPipeline.operation_id)
      ))
  const activeStageRun = pipelineStageRuns.find(run => ['queued', 'running'].includes(String(run?.status || '')))
  const activeStageCommand = String(activeStageRun?.stage_command || '')
  const workflow = Array.isArray(presentation.workflow)
    ? presentation.workflow.map(step => (
      pipelineRunning && activeStageCommand && step?.command === activeStageCommand
        ? { ...step, done: false, ready: true, state: 'running', message: '运行中', artifact_source: 'control.db' }
        : step
    ))
    : []
  const pipeline = rawPipeline
    ? {
        ...rawPipeline,
        ...(pipelineRunning && activeStageCommand ? { current_stage: activeStageCommand } : {}),
      }
    : null
  const sources = snapshot.sources && typeof snapshot.sources === 'object'
    ? snapshot.sources
    : (presentation.sources && typeof presentation.sources === 'object' ? presentation.sources : null)
  return {
    ...presentation,
    workflow,
    current_task: pipelineRunning
      ? (activeStageCommand || pipeline?.current_stage || presentation.current_task || '')
      : '',
    workspace_revision: Number(snapshot.revision || 0),
    operation: snapshot.operation || null,
    pipeline,
    running: hasPipelineAuthority ? pipelineRunning : Boolean(presentation.running),
    goal,
    goal_full: goal,
    agent_activity: snapshot.activity || null,
    repair_job: snapshot.repair_job || null,
    materials_summary: materials,
    issues_summary: findings.issues_summary || {},
    quality: {
      ...quality,
      latest_gate_evaluations: Array.isArray(quality.latest_gate_evaluations)
        ? quality.latest_gate_evaluations
        : [],
    },
    artifacts: Array.isArray(snapshot.artifacts) ? snapshot.artifacts : [],
    stage_runs: Array.isArray(snapshot.stage_runs) ? snapshot.stage_runs : [],
    current_stage_runs: currentStageRuns,
    pipeline_stage_runs: pipelineStageRuns,
    ...(sources ? { sources } : {}),
    artifact_files: snapshot.artifact_files && typeof snapshot.artifact_files === 'object'
      ? snapshot.artifact_files
      : {},
    control_source: 'control.db',
  }
}

export function sourceFilesFromSections(sections) {
  const result = { tender: [], company: [], template: [] }
  for (const section of Array.isArray(sections) ? sections : []) {
    if (!Object.prototype.hasOwnProperty.call(result, section?.key)) continue
    result[section.key] = (Array.isArray(section.items) ? section.items : [])
      .map(item => String(item?.name || '').trim())
      .filter(Boolean)
  }
  return result
}

export function normalizeSequentialPlanSteps(steps) {
  let reachedIncomplete = false
  return (Array.isArray(steps) ? steps : []).map((step) => {
    const normalized = { ...step }
    if (reachedIncomplete && normalized.status === 'done') {
      normalized.status = 'pending'
      normalized.message = '等待前置步骤'
      normalized.durationLabel = ''
    }
    if (normalized.status !== 'done') reachedIncomplete = true
    return normalized
  })
}

export function statusDetailText(value) {
  if (typeof value === 'string') return value.trim()
  if (!value || typeof value !== 'object') return ''
  const message = String(value.message || value.detail || value.code || '').trim()
  if (message) return message
  try {
    return JSON.stringify(value)
  } catch (_) {
    return '未知错误'
  }
}

export function isStalePipelineControlMessage(value) {
  const text = String(value || '').trim()
  if (!text || text.startsWith('历史状态（已恢复）')) return false
  return /(?:后端流水线已停止|流水线启动(?:请求)?失败|恢复流水线缺少\s*control\.db\s*中的起始阶段|流程已停止)/.test(text)
}

export function hasPipelineExecutionHistory(status) {
  return Boolean(
    status
    && typeof status === 'object'
    && status.pipeline
    && typeof status.pipeline === 'object'
    && String(status.pipeline.operation_id || '').trim()
  )
}
