import axios from 'axios'
import { csrfToken } from '../csrf'
import {
  V3_WORKSPACES_PATH,
  buildResearchResolveCommand,
  buildRunPipelineCommand,
  v3WorkspacePath,
  workspaceRevisionFromV3Payload,
} from './v3Contracts.js'

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
  return api.get(V3_WORKSPACES_PATH)
}

export function createRun(name, projectType, expectedPages) {
  return api.post(V3_WORKSPACES_PATH, {
    name,
    project_type: projectType,
    expected_pages: expectedPages,
  })
}

export function fetchLlmSettings() {
  return api.get('/llm-settings')
}

export function fetchFlowSettings() {
  return api.get('/flow-settings')
}

export function saveFlowSettings(settings) {
  return api.post('/flow-settings', { settings })
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

function newCommandId() {
  if (globalThis.crypto && typeof globalThis.crypto.randomUUID === 'function') {
    return globalThis.crypto.randomUUID()
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`
}

export function fetchV3WorkspaceSnapshot(runId) {
  return api.get(v3WorkspacePath(runId, 'snapshot'), { headers: { 'Cache-Control': 'no-cache' } })
}

export async function runV3Pipeline(runId) {
  const snapshot = await fetchV3WorkspaceSnapshot(runId)
  const commandId = newCommandId()
  const command = buildRunPipelineCommand(
    commandId,
    workspaceRevisionFromV3Payload(snapshot?.data),
  )
  return api.post(v3WorkspacePath(runId, 'commands'), command, { timeout: 120000 })
}

export async function resolveV3Research(runId, needId, attachmentInputIds = []) {
  const snapshot = await fetchV3WorkspaceSnapshot(runId)
  const commandId = newCommandId()
  const command = buildResearchResolveCommand(
    commandId,
    workspaceRevisionFromV3Payload(snapshot?.data),
    needId,
    attachmentInputIds,
  )
  return api.post(v3WorkspacePath(runId, 'commands'), command, { timeout: 300000 })
}

export function uploadV3Input(runId, role, file, replacesInputId = '') {
  const formData = new FormData()
  formData.append('role', role)
  formData.append('file', file)
  if (replacesInputId) formData.append('replaces_input_id', replacesInputId)
  return api.post(v3WorkspacePath(runId, 'uploads'), formData, { headers: { 'Content-Type': 'multipart/form-data' }, timeout: 120000 })
}

export function chatV3(runId, message) {
  return api.post(v3WorkspacePath(runId, 'chat/turn'), { message }, { timeout: 120000 })
}

export function downloadV3Final(runId) {
  window.open(`/api${v3WorkspacePath(runId, 'exports/final')}`, '_blank')
}

export default api
