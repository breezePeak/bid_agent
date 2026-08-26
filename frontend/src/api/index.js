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
  // Snapshot refreshes can overlap model-side persistence and may briefly take
  // longer than the old 30 second browser limit. Long-running commands still
  // supply their own, larger limits below.
  timeout: 120000,
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

export function createRun(name, projectType, expectedPages, projectMode = 'full_write') {
  return api.post(V3_WORKSPACES_PATH, {
    name,
    project_type: projectType,
    expected_pages: expectedPages,
    project_mode: projectMode,
  })
}

export function uploadLegacyBid(runId, file) {
  const formData = new FormData()
  formData.append('file', file)
  return api.post(v3WorkspacePath(runId, 'legacy-bids'), formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 120000,
  })
}

export function fetchLegacyBidIndex(runId, legacyBidId) {
  const id = encodeURIComponent(String(legacyBidId || '').trim())
  if (!id) throw new TypeError('legacyBidId is required')
  return api.get(v3WorkspacePath(runId, `legacy-bids/${id}/index`), {
    headers: { 'Cache-Control': 'no-cache' },
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

export function fetchV3WorkspaceSnapshot(runId, options = {}) {
  return api.get(v3WorkspacePath(runId, 'snapshot'), {
    headers: { 'Cache-Control': 'no-cache' },
    ...options,
  })
}

/** Subscribe to pushed workspace snapshots. Returns a function that closes the stream. */
export function subscribeV3Workspace(runId, { onSnapshot, onClosed } = {}) {
  const stream = new EventSource(`/api${v3WorkspacePath(runId, 'stream')}`)
  stream.addEventListener('snapshot', event => {
    try {
      const payload = JSON.parse(event.data)
      onSnapshot?.(payload)
    } catch (_) {
      // Ignore one malformed transport frame; EventSource will stay connected.
    }
  })
  stream.addEventListener('closed', event => {
    let payload = {}
    try { payload = JSON.parse(event.data) } catch (_) {}
    stream.close()
    onClosed?.(payload)
  })
  return () => stream.close()
}

/** Read the exact frozen snapshot required for the H1 planning confirmation. */
export function fetchV3PlanningConfirmation(runId) {
  return api.get(v3WorkspacePath(runId, 'planning/confirmation'), {
    headers: { 'Cache-Control': 'no-cache' },
  })
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
  // Full generation may include several bounded Tavily research turns before
  // Writer bundles are frozen.
  return api.post(v3WorkspacePath(runId, 'commands'), command, { timeout: 900000 })
}

export async function prepareV3Outline(runId, options = {}) {
  const snapshot = await fetchV3WorkspaceSnapshot(runId)
  const commandId = newCommandId()
  const command = buildPrepareOutlineCommand(
    commandId,
    workspaceRevisionFromV3Payload(snapshot?.data),
    options,
  )
  // A malformed multi-score response is adaptively split into smaller batches.
  // That can legitimately take longer than a fixed browser deadline while the
  // workspace stream continues to report progress.  Never turn a live server
  // operation into a misleading client-side "failed" state at 12 minutes.
  return api.post(v3WorkspacePath(runId, 'commands'), command, { timeout: 0 })
}

export async function confirmV3Planning(runId, planningSnapshot) {
  const snapshot = await fetchV3WorkspaceSnapshot(runId)
  const commandId = newCommandId()
  const command = buildConfirmPlanningCommand(
    commandId,
    workspaceRevisionFromV3Payload(snapshot?.data),
    planningSnapshot,
  )
  // 改写模式首次确认后还会执行旧投标书目录融合，沿用无浏览器超时的长任务策略。
  return api.post(v3WorkspacePath(runId, 'commands'), command, { timeout: 0 })
}

export async function resolveV3Research(runId, needId) {
  const snapshot = await fetchV3WorkspaceSnapshot(runId)
  const commandId = newCommandId()
  const command = buildResearchResolveCommand(
    commandId,
    workspaceRevisionFromV3Payload(snapshot?.data),
    needId,
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

/** Persist an in-place edit of one chapter-chat turn. */
export function saveChapterChatTurn(runId, chapterId, payload) {
  const id = encodeURIComponent(String(chapterId || '').trim())
  if (!id) throw new TypeError('chapterId is required')
  return api.put(v3WorkspacePath(runId, `chapters/${id}/chat/history`), payload || {})
}

/** Persist a new Agent execution record in the chapter conversation. */
export function appendChapterChatTurn(runId, chapterId, payload) {
  const id = encodeURIComponent(String(chapterId || '').trim())
  if (!id) throw new TypeError('chapterId is required')
  return api.post(v3WorkspacePath(runId, `chapters/${id}/chat/history`), payload || {})
}

/** Permanently delete one persisted chapter-chat turn. */
export function deleteChapterChatTurn(runId, chapterId, payload) {
  const id = encodeURIComponent(String(chapterId || '').trim())
  if (!id) throw new TypeError('chapterId is required')
  return api.delete(v3WorkspacePath(runId, `chapters/${id}/chat/history`), {
    data: payload || {},
  })
}

/** Permanently delete all persisted chapter-chat turns. */
export function clearChapterChatHistory(runId, chapterId) {
  const id = encodeURIComponent(String(chapterId || '').trim())
  if (!id) throw new TypeError('chapterId is required')
  return api.delete(v3WorkspacePath(runId, `chapters/${id}/chat/history`), {
    data: { clear_all: true },
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

/** Stream chapter chat thinking + answer as NDJSON events. */
export async function streamChapterChat(runId, chapterId, message, options = {}) {
  const id = encodeURIComponent(String(chapterId || '').trim())
  if (!id) throw new TypeError('chapterId is required')
  const headers = {
    Accept: 'application/x-ndjson, text/event-stream',
    'Content-Type': 'application/json',
  }
  const token = csrfToken()
  if (token) headers['X-CSRF-Token'] = token
  const response = await fetch(`/api${v3WorkspacePath(runId, `chapters/${id}/chat/stream`)}`, {
    method: 'POST',
    credentials: 'same-origin',
    headers,
    body: JSON.stringify({ message }),
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
    const error = new Error(detail || `章节对话流式失败（HTTP ${response.status}）`)
    error.status = response.status
    throw error
  }
  await readNdjsonStream(response, options.onEvent)
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

export function downloadV3CurrentWord(runId) {
  window.open(`/api${v3WorkspacePath(runId, 'exports/word')}`, '_blank')
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

export function submitV3Command(runId, command, options = {}) {
  const body = {
    command_id: command.command_id || newCommandId(),
    kind: command.kind,
    payload: command.payload || {},
    expected_revision: Number(command.expected_revision || 0),
    idempotency_key: command.idempotency_key || newCommandId(),
  }
  return api.post(v3WorkspacePath(runId, 'commands'), body, {
    timeout: 300000,
    ...options,
  })
}

export function fetchChapterRewriteMatch(runId, chapterId, options = {}) {
  const id = encodeURIComponent(String(chapterId || '').trim())
  if (!id) throw new TypeError('chapterId is required')
  return api.get(v3WorkspacePath(runId, `chapters/${id}/rewrite-match`), {
    headers: { 'Cache-Control': 'no-cache' },
    ...options,
  })
}

export async function generateChapterRewriteMatch(runId, chapterId, options = {}) {
  const id = String(chapterId || '').trim()
  if (!id) throw new TypeError('chapterId is required')
  const snapshot = await fetchV3WorkspaceSnapshot(runId, options)
  const commandId = newCommandId()
  return submitV3Command(runId, {
    command_id: commandId,
    kind: 'bid_rewrite.match.generate',
    payload: { chapter_id: id },
    expected_revision: workspaceRevisionFromV3Payload(snapshot?.data),
    idempotency_key: `bid_rewrite.match.generate-${id}-${commandId}`,
  }, options)
}

export function fetchChapterRewritePlan(runId, chapterId, options = {}) {
  const id = encodeURIComponent(String(chapterId || '').trim())
  if (!id) throw new TypeError('chapterId is required')
  return api.get(v3WorkspacePath(runId, `chapters/${id}/rewrite-plan`), {
    headers: { 'Cache-Control': 'no-cache' },
    ...options,
  })
}

export function fetchChapterRewritePlanRevisions(runId, chapterId, options = {}) {
  const id = encodeURIComponent(String(chapterId || '').trim())
  if (!id) throw new TypeError('chapterId is required')
  return api.get(v3WorkspacePath(runId, `chapters/${id}/rewrite-plan/revisions`), {
    headers: { 'Cache-Control': 'no-cache' },
    ...options,
  })
}

async function submitChapterRewritePlanCommand(runId, kind, payload, options = {}) {
  const snapshot = await fetchV3WorkspaceSnapshot(runId, options)
  const commandId = newCommandId()
  return submitV3Command(runId, {
    command_id: commandId,
    kind,
    payload,
    expected_revision: workspaceRevisionFromV3Payload(snapshot?.data),
    idempotency_key: `${kind}-${payload.chapter_id || 'chapter'}-${commandId}`,
  }, options)
}

export function generateChapterRewritePlan(runId, chapterId, options = {}) {
  return submitChapterRewritePlanCommand(runId, 'bid_rewrite.plan.generate', {
    chapter_id: String(chapterId || '').trim(),
  }, options)
}

export function updateChapterRewritePlan(runId, chapterId, plan, operations, options = {}) {
  return submitChapterRewritePlanCommand(runId, 'bid_rewrite.plan.update', {
    chapter_id: String(chapterId || '').trim(),
    expected_plan_revision: Number(plan?.plan_revision || 0),
    expected_plan_hash: String(plan?.plan_hash || ''),
    operations: Array.isArray(operations) ? operations : [],
  }, options)
}

export function searchChapterRewritePlan(runId, chapterId, plan, itemId, query, options = {}) {
  return submitChapterRewritePlanCommand(runId, 'bid_rewrite.plan.search', {
    chapter_id: String(chapterId || '').trim(),
    expected_plan_revision: Number(plan?.plan_revision || 0),
    expected_plan_hash: String(plan?.plan_hash || ''),
    item_id: String(itemId || ''),
    query: String(query || ''),
  }, options)
}

export function confirmChapterRewritePlan(runId, chapterId, plan, chapterRevision, options = {}) {
  return submitChapterRewritePlanCommand(runId, 'bid_rewrite.plan.confirm', {
    chapter_id: String(chapterId || '').trim(),
    expected_chapter_revision: Number(chapterRevision || 0),
    plan_revision: Number(plan?.plan_revision || 0),
    plan_hash: String(plan?.plan_hash || ''),
  }, options)
}

export function reopenChapterRewritePlan(runId, chapterId, plan, options = {}) {
  return submitChapterRewritePlanCommand(runId, 'bid_rewrite.plan.reopen', {
    chapter_id: String(chapterId || '').trim(),
    expected_plan_revision: Number(plan?.plan_revision || 0),
    expected_plan_hash: String(plan?.plan_hash || ''),
  }, options)
}

export function createChapterBatchJob(runId, chapterIds, idempotencyKey = '') {
  return api.post(v3WorkspacePath(runId, 'chapter-batch-jobs'), {
    chapter_ids: Array.isArray(chapterIds) ? chapterIds : [],
    idempotency_key: idempotencyKey || newCommandId(),
  })
}

export function fetchChapterBatchJob(runId, jobId) {
  const id = encodeURIComponent(String(jobId || '').trim())
  if (!id) throw new TypeError('jobId is required')
  return api.get(v3WorkspacePath(runId, `chapter-batch-jobs/${id}`), {
    headers: { 'Cache-Control': 'no-cache' },
  })
}

export function fetchCurrentChapterBatchJob(runId) {
  return api.get(v3WorkspacePath(runId, 'chapter-batch-jobs/current'), {
    headers: { 'Cache-Control': 'no-cache' },
  })
}

export function fetchChapterBatchEvents(runId, jobId, afterSequence = 0) {
  const id = encodeURIComponent(String(jobId || '').trim())
  if (!id) throw new TypeError('jobId is required')
  return api.get(v3WorkspacePath(runId, `chapter-batch-jobs/${id}/events`), {
    params: { after_sequence: Number(afterSequence || 0) },
    headers: { 'Cache-Control': 'no-cache' },
  })
}

export function actOnChapterBatchJob(runId, jobId, action) {
  const id = encodeURIComponent(String(jobId || '').trim())
  const command = encodeURIComponent(String(action || '').trim())
  if (!id || !command) throw new TypeError('jobId and action are required')
  return api.post(v3WorkspacePath(runId, `chapter-batch-jobs/${id}/${command}`))
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
