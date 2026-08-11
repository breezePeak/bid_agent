import axios from 'axios'
import { csrfToken } from '../csrf'
import { readNdjsonStream } from './ndjsonStream.js'
import {
  V3_WORKSPACES_PATH,
  buildResearchResolveCommand,
  buildConfirmPlanningCommand,
  buildPrepareOutlineCommand,
  buildRunPipelineCommand,
  buildCreateChapterCommand,
  buildSaveChapterMetadataCommand,
  buildArchiveChapterCommand,
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

export function deleteRun(runId) {
  return api.delete(v3WorkspacePath(runId))
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

export function fetchV3ContentUnit(runId, unitId) {
  const encodedUnitId = encodeURIComponent(String(unitId || '').trim())
  if (!encodedUnitId) throw new TypeError('unitId is required')
  return api.get(v3WorkspacePath(runId, `content-units/${encodedUnitId}`), {
    headers: { 'Cache-Control': 'no-cache' },
  })
}

export function fetchV3GenerationStage(runId, stageId) {
  const encodedStageId = encodeURIComponent(String(stageId || '').trim())
  if (!encodedStageId) throw new TypeError('stageId is required')
  return api.get(v3WorkspacePath(runId, `generation-stages/${encodedStageId}`), {
    headers: { 'Cache-Control': 'no-cache' },
  })
}

export function fetchV3DocumentPreview(runId) {
  return api.get(v3WorkspacePath(runId, 'document-preview'), {
    headers: { 'Cache-Control': 'no-cache' },
  })
}

export async function runV3Pipeline(runId, chapterIds = []) {
  const snapshot = await fetchV3WorkspaceSnapshot(runId)
  const commandId = newCommandId()
  const command = buildRunPipelineCommand(
    commandId,
    workspaceRevisionFromV3Payload(snapshot?.data),
    chapterIds,
  )
  // Full generation may include several bounded DeepSeek research turns before
  // Writer bundles are frozen.
  return api.post(v3WorkspacePath(runId, 'commands'), command, { timeout: 900000 })
}

export async function prepareV3Outline(runId) {
  const snapshot = await fetchV3WorkspaceSnapshot(runId)
  const commandId = newCommandId()
  const command = buildPrepareOutlineCommand(
    commandId,
    workspaceRevisionFromV3Payload(snapshot?.data),
  )
  // Score semantics may need an initial response plus one controlled repair;
  // the backend model timeout alone can be 300 seconds per attempt.
  return api.post(v3WorkspacePath(runId, 'commands'), command, { timeout: 720000 })
}

export async function confirmV3Planning(runId, planningSnapshot) {
  const snapshot = await fetchV3WorkspaceSnapshot(runId)
  const commandId = newCommandId()
  const command = buildConfirmPlanningCommand(
    commandId,
    workspaceRevisionFromV3Payload(snapshot?.data),
    planningSnapshot,
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

/** Workspace-level chat (pipeline / project studio). Not chapter-scoped. */
export function fetchChapterChatHistory(runId, chapterId, limit = 40) {
  const id = encodeURIComponent(String(chapterId || '').trim())
  if (!id) throw new TypeError('chapterId is required')
  return api.get(v3WorkspacePath(runId, `chapters/${id}/chat/history`), {
    params: { limit },
    headers: { 'Cache-Control': 'no-cache' },
  })
}

/** Isolated per-chapter dialogue; history never mixes across chapters. */
export function chatChapterV3(runId, chapterId, message) {
  const id = encodeURIComponent(String(chapterId || '').trim())
  if (!id) throw new TypeError('chapterId is required')
  return api.post(
    v3WorkspacePath(runId, `chapters/${id}/chat/turn`),
    { message },
    { timeout: 120000 },
  )
}

/** Read-only inspection of another chapter from the current chapter workbench. */
export function fetchChapterReadonlyView(runId, viewerChapterId, targetChapterId) {
  const viewer = encodeURIComponent(String(viewerChapterId || '').trim())
  const target = encodeURIComponent(String(targetChapterId || '').trim())
  if (!viewer || !target) throw new TypeError('viewerChapterId and targetChapterId are required')
  return api.get(v3WorkspacePath(runId, `chapters/${viewer}/readonly/${target}`), {
    headers: { 'Cache-Control': 'no-cache' },
  })
}

export function downloadV3Final(runId) {
  window.open(`/api${v3WorkspacePath(runId, 'exports/final')}`, '_blank')
}

export function fetchChapters(runId, includeArchived = true) {
  return api.get(v3WorkspacePath(runId, 'chapters'), {
    params: { include_archived: includeArchived },
    headers: { 'Cache-Control': 'no-cache' },
  })
}

export function fetchChapter(runId, chapterId) {
  const id = encodeURIComponent(String(chapterId || '').trim())
  if (!id) throw new TypeError('chapterId is required')
  return api.get(v3WorkspacePath(runId, `chapters/${id}`), {
    headers: { 'Cache-Control': 'no-cache' },
  })
}

export async function streamChapterDraft(runId, chapterId, payload = {}, options = {}) {
  const id = encodeURIComponent(String(chapterId || '').trim())
  if (!id) throw new TypeError('chapterId is required')
  const headers = {
    Accept: 'application/x-ndjson, text/event-stream',
    'Content-Type': 'application/json',
  }
  const token = csrfToken()
  if (token) headers['X-CSRF-Token'] = token
  const response = await fetch(`/api${v3WorkspacePath(runId, `chapters/${id}/draft/stream`)}`, {
    method: 'POST',
    credentials: 'same-origin',
    headers,
    body: JSON.stringify(payload || {}),
    signal: options.signal,
  })
  if (!response.ok) {
    let detail = ''
    try {
      const body = await response.json()
      detail = body?.error?.message || body?.message || ''
    } catch (_) {
      detail = await response.text().catch(() => '')
    }
    if (response.status === 405) {
      detail = '当前后端尚未加载流式写作接口，请重启 api.v3_app 服务后重试。'
    }
    const error = new Error(detail || `流式生成失败（HTTP ${response.status}）`)
    error.status = response.status
    throw error
  }
  await readNdjsonStream(response, options.onEvent)
}

export { readNdjsonStream }

export function fetchChapterRevisions(runId, chapterId, limit = 100) {
  const id = encodeURIComponent(String(chapterId || '').trim())
  if (!id) throw new TypeError('chapterId is required')
  return api.get(v3WorkspacePath(runId, `chapters/${id}/revisions`), {
    params: { limit },
    headers: { 'Cache-Control': 'no-cache' },
  })
}

export function fetchDocumentCompose(runId) {
  return api.get(v3WorkspacePath(runId, 'document/compose'), {
    headers: { 'Cache-Control': 'no-cache' },
  })
}

export function fetchSnapshot(runId) {
  return fetchV3WorkspaceSnapshot(runId)
}

export function submitV3Command(runId, command) {
  const body = {
    command_id: command.command_id || newCommandId(),
    kind: command.kind,
    payload: command.payload || {},
    expected_revision: Number(command.expected_revision || 0),
    idempotency_key: command.idempotency_key || newCommandId(),
  }
  return api.post(v3WorkspacePath(runId, 'commands'), body, { timeout: 300000 })
}

export async function createChapter(runId, chapterId, title = '') {
  const snapshot = await fetchV3WorkspaceSnapshot(runId)
  const commandId = newCommandId()
  const command = buildCreateChapterCommand(
    commandId,
    workspaceRevisionFromV3Payload(snapshot?.data),
    chapterId,
    title,
  )
  return submitV3Command(runId, command)
}

export async function saveChapterMetadata(runId, chapterId, metadata) {
  const snapshot = await fetchV3WorkspaceSnapshot(runId)
  const commandId = newCommandId()
  const command = buildSaveChapterMetadataCommand(
    commandId,
    workspaceRevisionFromV3Payload(snapshot?.data),
    chapterId,
    metadata,
  )
  return submitV3Command(runId, command)
}

export async function archiveChapter(runId, chapterId) {
  const snapshot = await fetchV3WorkspaceSnapshot(runId)
  const commandId = newCommandId()
  const command = buildArchiveChapterCommand(
    commandId,
    workspaceRevisionFromV3Payload(snapshot?.data),
    chapterId,
  )
  return submitV3Command(runId, command)
}

export default api
