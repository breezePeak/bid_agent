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
  const operationStatus = String(snapshot.operation?.status || '')
  const operationRunning = ['queued', 'running', 'pausing', 'cancelling'].includes(operationStatus)
  return {
    ...presentation,
    workspace_revision: Number(snapshot.revision || 0),
    operation: snapshot.operation || null,
    pipeline: snapshot.pipeline || null,
    running: operationRunning || Boolean(presentation.running),
    goal: snapshot.goal || null,
    goal_full: snapshot.goal || null,
    agent_activity: snapshot.activity || null,
    repair_job: snapshot.repair_job || null,
    materials_summary: materials,
    issues_summary: findings.issues_summary || {},
    artifacts: snapshot.artifacts || {},
    control_source: 'control.db',
  }
}
