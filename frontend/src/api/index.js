import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
})

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

export function fetchFilePreview(path) {
  return api.get('/file-preview', { params: { path } })
}

export function downloadFinalMd() {
  window.open('/api/download/final-md', '_blank')
}

export function downloadFinalDocx() {
  window.open('/api/download/final-docx', '_blank')
}

export function deleteRun(runId) {
  return api.post('/delete-run', { run_id: runId })
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

export function orchestrateChat(message, { selectedCommand = '' } = {}) {
  return api.post('/chat/orchestrate', { message, selected_command: selectedCommand }, { timeout: 120000 })
}

export default api

export function fetchAgentGoal() {
  return api.get('/agent/goal')
}

export function fetchAgentDecisions(tail = 20) {
  return api.get('/agent/decisions', { params: { tail } })
}

export function fetchAgentTools() {
  return api.get('/agent/tools')
}

export function invokeAgentTool(name, args = {}, { dryRun = false } = {}) {
  return api.post('/agent/tools/invoke', { name, args, dry_run: dryRun }, { timeout: 300000 })
}

