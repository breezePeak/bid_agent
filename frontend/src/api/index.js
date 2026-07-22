import axios from 'axios'
import { csrfToken } from '../csrf'

const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
})

api.interceptors.request.use(config => {
  const method = String(config.method || 'get').toUpperCase()
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    const token = csrfToken()
    if (token) config.headers.set('X-CSRF-Token', token)
  }
  return config
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
  return api.get('/v2/workspaces')
}

export function createRun(name, projectType, expectedPages) {
  return api.post('/v2/workspaces', {
    name,
    project_type: projectType,
    expected_pages: expectedPages,
  })
}

export function fetchProjectProfileChoices() {
  return api.get('/v2/project-profiles')
}

export function fetchWorkflowStepDetail(runId, command) {
  return api.get(`/v2/workspaces/${encodeURIComponent(runId)}/workflow-step-detail`, { params: { command } })
}

export function fetchManualReviewSummary(runId) {
  return api.get(`/v2/workspaces/${encodeURIComponent(runId)}/manual-review/summary`)
}

export function fetchManualReviewItems(runId, category) {
  return api.get(`/v2/workspaces/${encodeURIComponent(runId)}/manual-review/items`, { params: { category } })
}

export function updateManualReview(runId, category, payload) {
  return submitWorkspaceCommand(runId, 'review.update', { category, payload })
}

export function downloadFinalMd(runId) {
  if (!runId) throw new Error('缺少工作空间 ID，无法下载草稿')
  const workspace = encodeURIComponent(runId)
  window.open(`/api/v2/workspaces/${workspace}/exports/draft`, '_blank')
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
  const proposed = await submitWorkspaceCommand(runId, 'workspace.archive', {})
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

export function fetchChatMessages(runId) {
  return api.get(`/v2/workspaces/${encodeURIComponent(runId)}/chat/messages`)
}

export function saveChatMessage(runId, role, content, { thinking = '', actions = [], kind = 'message' } = {}) {
  return api.post(`/v2/workspaces/${encodeURIComponent(runId)}/chat/messages`, { role, content, thinking, actions, kind })
}

export function clearChatMessages(runId) {
  return api.delete(`/v2/workspaces/${encodeURIComponent(runId)}/chat/messages`)
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

export function fetchMigrationBackups(runId) {
  return api.get(`/v2/workspaces/${encodeURIComponent(runId)}/migration/backups`)
}

export function drillMigrationBackup(runId, path) {
  return api.post(`/v2/workspaces/${encodeURIComponent(runId)}/migration/backups/drill`, { path })
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

export default api

export function fetchComplianceReport(runId) {
  return api.get(`/v2/workspaces/${encodeURIComponent(runId)}/compliance-report`)
}

export function fetchAgentDecisions(runId, tail = 20) {
  return api.get(`/v2/workspaces/${encodeURIComponent(runId)}/agent/decisions`, { params: { tail } })
}

export function fetchAgentFlags() {
  return api.get('/agent/flags')
}


export function fetchIssues(runId, status = 'open') {
  return api.get(`/v2/workspaces/${encodeURIComponent(runId)}/issues`, { params: { status } })
}

export function previewIssueRepair(runId, issueId) {
  return api.post(`/v2/workspaces/${encodeURIComponent(runId)}/issues/${encodeURIComponent(issueId)}/actions/preview`)
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

export function explainIssueCause(runId, issueId) {
  return api.post(`/v2/workspaces/${encodeURIComponent(runId)}/issues/${encodeURIComponent(issueId)}/actions/explain`, {})
}

export function batchPreviewRepairs(runId, issueIds) {
  return api.post(`/v2/workspaces/${encodeURIComponent(runId)}/issues/actions/batch-preview`, { issue_ids: issueIds })
}

export function batchExecuteRepairs(runId, issueIds) {
  return submitWorkspaceCommand(runId, 'repair.issues', { issue_ids: issueIds })
}

export function fetchExportPreflight(runId) {
  return api.get(`/v2/workspaces/${encodeURIComponent(runId)}/export-preflight`)
}

export function fetchMaterialsChecklist(runId) {
  return api.get(`/v2/workspaces/${encodeURIComponent(runId)}/materials-checklist`)
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
