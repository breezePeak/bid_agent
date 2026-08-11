import { v3WorkspacePath } from './v3Contracts.js'

export function chaptersPath(workspaceId) {
  return `${v3WorkspacePath(workspaceId)}/chapters`
}

export function chapterPath(workspaceId, chapterId) {
  return `${chaptersPath(workspaceId)}/${encodeURIComponent(chapterId)}`
}

export function chapterContextRevisionsPath(workspaceId, chapterId) {
  return `${chapterPath(workspaceId, chapterId)}/context/revisions`
}

export function chapterContentRevisionsPath(workspaceId, chapterId) {
  return `${chapterPath(workspaceId, chapterId)}/revisions`
}

export function chapterChatHistoryPath(workspaceId, chapterId) {
  return `${chapterPath(workspaceId, chapterId)}/chat/history`
}

export function chapterChatTurnPath(workspaceId, chapterId) {
  return `${chapterPath(workspaceId, chapterId)}/chat/turn`
}

export function chapterChatStreamPath(workspaceId, chapterId) {
  return `${chapterPath(workspaceId, chapterId)}/chat/stream`
}

export function chapterReadonlyPath(workspaceId, viewerChapterId, targetChapterId) {
  return `${chapterPath(workspaceId, viewerChapterId)}/readonly/${encodeURIComponent(targetChapterId)}`
}

export function chapterComparePath(workspaceId, chapterId, fromRev, toRev) {
  return `${chapterContentRevisionsPath(workspaceId, chapterId)}/compare?from=${fromRev}&to=${toRev}`
}

export function documentComposePath(workspaceId) {
  return `${v3WorkspacePath(workspaceId)}/document/compose`
}

export function buildChapterCommand(kind, payload, expectedRevision, idempotencyKey) {
  return {
    kind,
    payload: payload || {},
    expected_revision: Number(expectedRevision || 0),
    idempotency_key: String(idempotencyKey || `${kind}-${Date.now()}`),
  }
}

export function normalizeChapterList(payload) {
  const chapters = payload?.chapters || payload || {}
  const items = Array.isArray(chapters.items) ? chapters.items : []
  return {
    total: Number(chapters.total || items.length || 0),
    materialized: Number(chapters.materialized || 0),
    active: Number(chapters.active || 0),
    archived: Number(chapters.archived || 0),
    items,
  }
}

export function chapterStatusLabel(item) {
  if (!item) return '未知'
  if (item.status === 'archived') return '已归档'
  if (item.status === 'projected') return '未打开'
  if (item.approval_status === 'approved') return '已确认'
  if (Number(item.formal_content_revision || 0) > 0) return '有正式版'
  if (Number(item.head_content_revision || 0) > 0) return '草稿'
  if (item.materialized) return '已物化'
  return '未打开'
}
