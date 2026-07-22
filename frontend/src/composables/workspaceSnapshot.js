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
    quality: {
      ...quality,
      latest_gate_evaluations: Array.isArray(quality.latest_gate_evaluations)
        ? quality.latest_gate_evaluations
        : [],
    },
    artifacts: Array.isArray(snapshot.artifacts) ? snapshot.artifacts : [],
    artifact_files: snapshot.artifact_files && typeof snapshot.artifact_files === 'object'
      ? snapshot.artifact_files
      : {},
    control_source: 'control.db',
  }
}
