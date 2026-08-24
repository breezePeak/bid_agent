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

export function chapterWritingPlanPath(workspaceId, chapterId) {
  return `${chapterPath(workspaceId, chapterId)}/plan`
}

const PLAN_STATUSES = new Set([
  'current',
  'confirmed',
  'stale_blueprint',
  'stale_global_context',
  'stale_chapter_context',
  'stale_source',
  'stale_evidence',
])

function contractError(path, expected) {
  throw new TypeError(`章节编写规划响应无效：${path} 必须是${expected}`)
}

function record(value, path) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) contractError(path, '对象')
  return value
}

function text(value, path, { optional = false } = {}) {
  if (optional && (value == null || value === '')) return ''
  if (typeof value !== 'string' || !value.trim()) contractError(path, '非空字符串')
  return value.trim()
}

function list(value, path) {
  if (!Array.isArray(value)) contractError(path, '数组')
  return value
}

/** Strict, read-only projection consumed by the PR-04 workbench. */
export function normalizeChapterWritingPlanResponse(payload) {
  const root = record(payload, 'response')
  if (root.ok !== true) contractError('ok', 'true')
  const chapter = record(root.chapter, 'chapter')
  const chapterId = text(chapter.chapter_id, 'chapter.chapter_id')
  if (root.plan == null) {
    return {
      chapter,
      plan: null,
      receipt: root.receipt || null,
      currentDependencies: root.current_dependencies || null,
      stale: false,
      readOnly: true,
    }
  }

  const rawPlan = record(root.plan, 'plan')
  if (text(rawPlan.chapter_id, 'plan.chapter_id') !== chapterId) {
    throw new TypeError('章节编写规划响应无效：plan.chapter_id 与 chapter 不一致')
  }
  const revision = Number(rawPlan.plan_revision)
  if (!Number.isInteger(revision) || revision < 1) contractError('plan.plan_revision', '正整数')
  const status = text(rawPlan.status, 'plan.status')
  if (!PLAN_STATUSES.has(status)) contractError('plan.status', '已知状态')

  const sources = list(rawPlan.sources, 'plan.sources').map((item, index) => {
    const source = record(item, `plan.sources[${index}]`)
    return {
      ...source,
      source_id: text(source.source_id, `plan.sources[${index}].source_id`),
      source_type: text(source.source_type, `plan.sources[${index}].source_type`),
      reference_id: text(source.reference_id, `plan.sources[${index}].reference_id`),
      content_hash: text(source.content_hash, `plan.sources[${index}].content_hash`),
      title: text(source.title, `plan.sources[${index}].title`),
      preview: text(source.preview, `plan.sources[${index}].preview`, { optional: true }),
      snapshot_ref: text(source.snapshot_ref, `plan.sources[${index}].snapshot_ref`),
    }
  })
  const sourceIds = new Set(sources.map(item => item.source_id))
  if (sourceIds.size !== sources.length) throw new TypeError('章节编写规划响应无效：source_id 重复')

  const contentUnits = list(rawPlan.content_units, 'plan.content_units').map((item, index) => {
    const unit = record(item, `plan.content_units[${index}]`)
    const order = Number(unit.order)
    if (!Number.isInteger(order) || order < 0) contractError(`plan.content_units[${index}].order`, '非负整数')
    const sourceRefs = list(unit.source_refs || [], `plan.content_units[${index}].source_refs`)
      .map((value, refIndex) => text(value, `plan.content_units[${index}].source_refs[${refIndex}]`))
    if (sourceRefs.some(sourceId => !sourceIds.has(sourceId))) {
      throw new TypeError('章节编写规划响应无效：content unit 指向未知 source')
    }
    return {
      ...unit,
      unit_id: text(unit.unit_id, `plan.content_units[${index}].unit_id`),
      title: text(unit.title, `plan.content_units[${index}].title`),
      instructions: text(unit.instructions, `plan.content_units[${index}].instructions`),
      purpose: text(unit.purpose, `plan.content_units[${index}].purpose`, { optional: true }),
      must_answer: text(unit.must_answer, `plan.content_units[${index}].must_answer`, { optional: true }),
      order,
      source_refs: sourceRefs,
    }
  })
  const unitIds = new Set(contentUnits.map(item => item.unit_id))
  if (!contentUnits.length || unitIds.size !== contentUnits.length) {
    throw new TypeError('章节编写规划响应无效：content_units 为空或 unit_id 重复')
  }

  const sourceBindings = list(rawPlan.source_bindings, 'plan.source_bindings').map((item, index) => {
    const binding = record(item, `plan.source_bindings[${index}]`)
    const sourceId = text(binding.source_id, `plan.source_bindings[${index}].source_id`)
    const unitId = text(binding.content_unit_id, `plan.source_bindings[${index}].content_unit_id`)
    if (!sourceIds.has(sourceId) || !unitIds.has(unitId)) {
      throw new TypeError('章节编写规划响应无效：source binding 存在悬空引用')
    }
    return {
      ...binding,
      source_id: sourceId,
      content_unit_id: unitId,
      usage_type: text(binding.usage_type, `plan.source_bindings[${index}].usage_type`),
      instruction: text(binding.instruction, `plan.source_bindings[${index}].instruction`),
      required: binding.required === true,
    }
  })

  return {
    chapter,
    plan: {
      ...rawPlan,
      plan_revision: revision,
      status,
      sources,
      content_units: [...contentUnits].sort((a, b) => a.order - b.order),
      source_bindings: sourceBindings,
      research_decisions: list(rawPlan.research_decisions, 'plan.research_decisions'),
    },
    receipt: root.receipt || null,
    currentDependencies: root.current_dependencies || null,
    stale: status.startsWith('stale_'),
    readOnly: true,
  }
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
