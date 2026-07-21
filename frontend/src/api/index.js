import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
})

api.interceptors.response.use(
  response => response,
  error => {
    if (error?.response?.status === 401 && !window.location.pathname.startsWith('/login')) {
      window.location.assign('/login')
    }
    return Promise.reject(error)
  },
)

export function login(username, password) {
  return api.post('/auth/login', { username, password })
}

export function logout() {
  return api.post('/auth/logout')
}

export function fetchCurrentUser() {
  return api.get('/auth/me')
}

export function fetchRuns() {
  return api.get('/runs')
}

export function createRun(name, projectType, expectedPages) {
  return api.post('/start-run', {
    name,
    project_type: projectType,
    expected_pages: expectedPages,
  })
}

export function selectRun(runId) {
  return api.post('/select-run', { run_id: runId })
}

export function fetchStatus() {
  return api.get('/status')
}

export function fetchProjectProfile() {
  return api.get('/project-profile')
}

export function fetchProjectProfileChoices() {
  return api.get('/project-profile')
}

export function uploadFile(category, file) {
  const formData = new FormData()
  formData.append('category', category)
  formData.append('file', file)
  return api.post('/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

export function runCommand(command) {
  return api.post('/run-command', { command })
}

export function startPipeline(runId, startCommand = '') {
  return api.post('/start-pipeline', { run_id: runId, start_command: startCommand })
}

export function fetchLogs(lines = 200) {
  return api.get('/logs', { params: { lines } })
}

export function fetchWorkflowStepDetail(command) {
  return api.get('/workflow-step-detail', { params: { command } })
}

export function fetchManualReviewSummary() {
  return api.get('/manual-review/summary')
}

export function fetchManualReviewItems(category) {
  return api.get('/manual-review/items', { params: { category } })
}

export function updateManualReview(runId, category, payload) {
  return submitWorkspaceCommand(runId, 'review.update', { category, payload })
}

export function fetchFilePreview(path) {
  return api.get('/file-preview', { params: { path } })
}

export function fetchWorkspaceFiles() {
  return api.get('/workspace-files')
}

export function downloadFinalMd() {
  window.open('/api/download/final-md', '_blank')
}

export async function downloadFinalDocx(runId) {
  if (!runId) throw new Error('缺少工作空间 ID，无法下载正式稿')
  const target = window.open('', '_blank')
  try {
    await revalidateFormalGate(runId)
    const response = await fetchLatestGateReceipt(runId)
    const receipt = response?.data?.gate_receipt
    if (!receipt?.receipt_id) throw new Error('正式稿门禁未返回有效凭据')
    const workspace = encodeURIComponent(runId)
    const receiptId = encodeURIComponent(receipt.receipt_id)
    const url = `/api/v2/workspaces/${workspace}/exports/final?gate_receipt_id=${receiptId}`
    if (target) target.location.href = url
    else window.open(url, '_blank')
    return receipt
  } catch (error) {
    if (target) target.close()
    throw error
  }
}

export async function deleteRun(runId) {
  const proposed = await api.post('/delete-run', { run_id: runId })
  const actionId = proposed?.data?.action?.confirmation_id
  if (!actionId) return proposed
  return confirmWorkspaceAction(runId, actionId)
}

export function fetchLlmSettings() {
  return api.get('/llm-settings')
}

export function saveLlmModel(model, setActive = false) {
  return api.post('/llm-settings', { model, set_active: setActive })
}

export function activateLlmModel(id) {
  return api.post('/llm-settings/activate', { id })
}

export function deleteLlmModel(id) {
  return api.post('/llm-settings/delete', { id })
}

export function testLlmModel(model, { useActive = false } = {}) {
  return api.post('/llm-settings/test', { model, use_active: useActive }, { timeout: 90000 })
}

export function fetchChatMessages() {
  return api.get('/chat/messages')
}

export function saveChatMessage(role, content, { thinking = '', actions = [], kind = 'message' } = {}) {
  return api.post('/chat/messages', { role, content, thinking, actions, kind })
}

export function clearChatMessages() {
  return api.delete('/chat/messages')
}

export function orchestrateChat(message, { runId = '', selectedCommand = '', action = null } = {}) {
  const payload = { message, run_id: runId, selected_command: selectedCommand }
  if (action && typeof action === 'object') payload.action = action
  const path = runId
    ? `/v2/workspaces/${encodeURIComponent(runId)}/chat/turn`
    : '/chat/orchestrate'
  return api.post(path, payload, { timeout: 120000 })
}

function newCommandId() {
  if (globalThis.crypto && typeof globalThis.crypto.randomUUID === 'function') {
    return globalThis.crypto.randomUUID()
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`
}

export function fetchWorkspaceSnapshot(runId) {
  return api.get(`/v2/workspaces/${encodeURIComponent(runId)}/snapshot`)
}

export async function submitWorkspaceCommand(runId, kind, payload = {}, options = {}) {
  const snapshotResponse = await fetchWorkspaceSnapshot(runId)
  const revision = Number(snapshotResponse?.data?.snapshot?.revision || 0)
  const commandId = options.commandId || newCommandId()
  return api.post(`/v2/workspaces/${encodeURIComponent(runId)}/commands`, {
    command_id: commandId,
    kind,
    payload,
    expected_revision: revision,
    idempotency_key: options.idempotencyKey || commandId,
  })
}

export async function startOrResumePipeline(runId, startCommand = '') {
  const snapshotResponse = await fetchWorkspaceSnapshot(runId)
  const snapshot = snapshotResponse?.data?.snapshot || {}
  const operation = snapshot.operation && typeof snapshot.operation === 'object' ? snapshot.operation : null
  const resume = operation && ['paused', 'blocked'].includes(String(operation.status || ''))
  const kind = resume ? 'pipeline.resume' : 'pipeline.start'
  const payload = { start_command: startCommand || '' }
  if (resume) payload.operation_id = operation.operation_id
  const commandId = newCommandId()
  return api.post(`/v2/workspaces/${encodeURIComponent(runId)}/commands`, {
    command_id: commandId,
    kind,
    payload,
    expected_revision: Number(snapshot.revision || 0),
    idempotency_key: commandId,
  })
}

export function confirmWorkspaceAction(runId, actionId) {
  return api.post(`/v2/workspaces/${encodeURIComponent(runId)}/actions/${encodeURIComponent(actionId)}/confirm`)
}

export function declineWorkspaceAction(runId, actionId) {
  return api.post(`/v2/workspaces/${encodeURIComponent(runId)}/actions/${encodeURIComponent(actionId)}/decline`)
}

export function fetchCurrentRepairJob() {
  return api.get('/repair-jobs/current')
}

export default api

export function fetchComplianceReport() {
  return api.get('/compliance-report')
}

export function fetchAgentActivity() {
  return api.get('/agent/activity')
}

export function fetchAgentGoal() {
  return api.get('/agent/goal')
}

export function fetchAgentDecisions(tail = 20) {
  return api.get('/agent/decisions', { params: { tail } })
}

export function fetchAgentTools() {
  return api.get('/agent/tools')
}

export function fetchAgentFlags() {
  return api.get('/agent/flags')
}

export function fetchConcurrencyMetrics() {
  return api.get('/concurrency')
}

/** Unified live runtime (goal + activity + repair + pipeline) */
export function fetchRuntimeStatus(heal = false) {
  return api.get('/runtime', { params: heal ? { heal: true } : {} })
}

export function invokeAgentTool(name, args = {}, { dryRun = false } = {}) {
  return api.post('/agent/tools/invoke', { name, args, dry_run: dryRun }, { timeout: 300000 })
}


export function fetchIssues(status = 'open') {
  return api.get('/issues', { params: { status } })
}

export function previewIssueRepair(issueId) {
  return api.post(`/issues/${encodeURIComponent(issueId)}/actions/preview`)
}

export function executeIssueRepair(runId, issueId, { dryRun = false } = {}) {
  if (dryRun) {
    return api.post(`/issues/${encodeURIComponent(issueId)}/actions/execute`, { dry_run: true })
  }
  return submitWorkspaceCommand(runId, 'repair.issues', { issue_ids: [issueId] })
}

export function revalidateGate(runId, command) {
  return submitWorkspaceCommand(runId, 'quality.revalidate', { command })
}

export function acceptIssueRisk(runId, issueId, reason) {
  return submitWorkspaceCommand(runId, 'issues.accept_risk', { issue_id: issueId, reason })
}

export function explainIssueCause(issueId) {
  return api.post(`/issues/${encodeURIComponent(issueId)}/actions/explain`, {})
}

export function batchPreviewRepairs(issueIds) {
  return api.post('/issues/actions/batch-preview', { issue_ids: issueIds })
}

export function batchExecuteRepairs(runId, issueIds) {
  return submitWorkspaceCommand(runId, 'repair.issues', { issue_ids: issueIds })
}

export function fetchExportPreflight() {
  return api.get('/export-preflight')
}

export function fetchMaterialsChecklist() {
  return api.get('/materials-checklist')
}

export function updateMaterialsChecklistItem(runId, payload) {
  return submitWorkspaceCommand(runId, 'materials.update', payload)
}

export function rebuildMaterialsChecklist(runId) {
  return submitWorkspaceCommand(runId, 'materials.rebuild', {})
}

export function refillMaterialsChecklist(runId, payload = {}) {
  return submitWorkspaceCommand(runId, 'materials.refill', payload)
}

export function stageMaterialUpload(runId, file) {
  const formData = new FormData()
  formData.append('file', file)
  return api.post(`/v2/workspaces/${encodeURIComponent(runId)}/materials/uploads`, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 120000,
  })
}

export async function registerMaterialUpload(runId, itemId, file, note = '') {
  const staged = await stageMaterialUpload(runId, file)
  const uploadToken = staged?.data?.upload_token
  if (!uploadToken) throw new Error('材料暂存未返回 upload_token')
  return submitWorkspaceCommand(runId, 'materials.upload', {
    item_id: itemId,
    upload_token: uploadToken,
    note,
  })
}

export function verifyMaterial(runId, payload) {
  return submitWorkspaceCommand(runId, 'materials.verify', payload)
}

export function confirmMaterialVerification(runId, payload) {
  return submitWorkspaceCommand(runId, 'materials.confirm_verification', payload)
}

export function revalidateFormalGate(runId) {
  return submitWorkspaceCommand(runId, 'gate.revalidate', {})
}

export function fetchLatestGateReceipt(runId) {
  return api.get(`/v2/workspaces/${encodeURIComponent(runId)}/gates/latest`)
}

export function downloadFormalDocx(runId, gateReceiptId) {
  const workspace = encodeURIComponent(runId)
  const receipt = encodeURIComponent(gateReceiptId)
  window.open(`/api/v2/workspaces/${workspace}/exports/final?gate_receipt_id=${receipt}`, '_blank')
}
