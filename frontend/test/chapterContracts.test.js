import assert from 'node:assert/strict'
import test from 'node:test'

import {
  chapterChatHistoryPath,
  chapterChatTurnPath,
  chapterChatStreamPath,
  chapterReadonlyPath,
  chapterPath,
  chapterWritingPlanPath,
  chapterStatusLabel,
  chaptersPath,
  documentComposePath,
  normalizeChapterList,
  normalizeChapterWritingPlanResponse,
} from '../src/api/chapterContracts.js'

test('chapter paths are rooted under v3 workspaces', () => {
  assert.equal(chaptersPath('ws-1'), '/v3/workspaces/ws-1/chapters')
  assert.equal(chapterPath('ws-1', 'ch-a'), '/v3/workspaces/ws-1/chapters/ch-a')
  assert.equal(chapterWritingPlanPath('ws-1', 'ch-a'), '/v3/workspaces/ws-1/chapters/ch-a/plan')
  assert.equal(documentComposePath('ws-1'), '/v3/workspaces/ws-1/document/compose')
  assert.equal(
    chapterChatHistoryPath('ws-1', 'ch-a'),
    '/v3/workspaces/ws-1/chapters/ch-a/chat/history',
  )
  assert.equal(
    chapterChatTurnPath('ws-1', 'ch-a'),
    '/v3/workspaces/ws-1/chapters/ch-a/chat/turn',
  )
  assert.equal(
    chapterChatStreamPath('ws-1', 'ch-a'),
    '/v3/workspaces/ws-1/chapters/ch-a/chat/stream',
  )
  assert.equal(
    chapterReadonlyPath('ws-1', 'ch-a', 'ch-b'),
    '/v3/workspaces/ws-1/chapters/ch-a/readonly/ch-b',
  )
  assert.equal(
    chapterChatHistoryPath('客户/A', '章 1'),
    `/v3/workspaces/${encodeURIComponent('客户/A')}/chapters/${encodeURIComponent('章 1')}/chat/history`,
  )
})

const planPayload = () => ({
  ok: true,
  chapter: { chapter_id: 'ch-a', title: '项目方案' },
  plan: {
    chapter_id: 'ch-a',
    plan_revision: 2,
    status: 'stale_source',
    plan_hash: 'plan-hash',
    dependency_fingerprint: 'dep-hash',
    source: 'shadow_builder',
    created_at: '2026-08-24T00:00:00Z',
    binding: { chapter_id: 'ch-a' },
    sources: [{
      source_id: 'src-1',
      source_type: 'TENDER_REQUIREMENT',
      reference_id: 'req-1',
      content_hash: 'source-hash',
      title: '招标要求',
      preview: '必须回答实施方法。',
      snapshot_ref: 'requirements/req-1',
    }],
    content_units: [{
      unit_id: 'unit-1',
      title: '实施方法',
      instructions: '说明实施步骤。',
      purpose: '响应招标要求',
      must_answer: '如何实施？',
      order: 0,
      source_refs: ['src-1'],
    }],
    source_bindings: [{
      source_id: 'src-1',
      content_unit_id: 'unit-1',
      usage_type: 'constraint',
      instruction: '逐项响应',
      required: true,
    }],
    research_decisions: [],
  },
  receipt: null,
  current_dependencies: { chapter_id: 'ch-a' },
})

test('normalizeChapterWritingPlanResponse validates and projects read-only stale plan', () => {
  const result = normalizeChapterWritingPlanResponse(planPayload())
  assert.equal(result.readOnly, true)
  assert.equal(result.stale, true)
  assert.equal(result.plan.sources[0].source_id, 'src-1')
  assert.equal(result.plan.content_units[0].unit_id, 'unit-1')
})

test('normalizeChapterWritingPlanResponse accepts legacy no-plan state', () => {
  const result = normalizeChapterWritingPlanResponse({
    ok: true,
    chapter: { chapter_id: 'legacy' },
    plan: null,
    receipt: null,
    current_dependencies: null,
  })
  assert.equal(result.plan, null)
  assert.equal(result.readOnly, true)
})

test('normalizeChapterWritingPlanResponse rejects dangling bindings', () => {
  const payload = planPayload()
  payload.plan.source_bindings[0].source_id = 'missing'
  assert.throws(() => normalizeChapterWritingPlanResponse(payload), /悬空引用/)
})

test('normalizeChapterList accepts API envelope', () => {
  const list = normalizeChapterList({
    chapters: {
      total: 2,
      materialized: 1,
      active: 1,
      archived: 0,
      items: [{ chapter_id: 'ch-a', status: 'active' }],
    },
  })
  assert.equal(list.total, 2)
  assert.equal(list.items.length, 1)
})

test('chapterStatusLabel covers projected draft and approved', () => {
  assert.equal(chapterStatusLabel({ status: 'projected' }), '未打开')
  assert.equal(chapterStatusLabel({ status: 'active', materialized: true, head_content_revision: 2, formal_content_revision: 0 }), '草稿')
  assert.equal(chapterStatusLabel({ status: 'active', approval_status: 'approved' }), '已确认')
  assert.equal(chapterStatusLabel({ status: 'archived' }), '已归档')
})
