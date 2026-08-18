import test from 'node:test'
import assert from 'node:assert/strict'

import {
  hydrateBatchChapterJob,
  initialBatchChapterJobState,
  reduceBatchChapterJobEvents,
} from '../src/batchChapterJobReducer.js'


test('hydrates selected chapters and replays resumable events exactly once', () => {
  let state = hydrateBatchChapterJob(initialBatchChapterJobState(), {
    job_id: 'batch-1',
    status: 'running',
    items: [
      { item_id: 'item-a', chapter_id: 'a', chapter_title: '章节 A', status: 'queued' },
      { item_id: 'item-b', chapter_id: 'b', chapter_title: '章节 B', status: 'queued' },
    ],
  })
  const events = [
    { event_id: 'e1', sequence: 1, job_id: 'batch-1', chapter_id: 'a', chapter_title: '章节 A', type: 'analysis_started', stage: 'analyzing', status: 'running', message: '开始分析' },
    { event_id: 'e2', sequence: 2, job_id: 'batch-1', chapter_id: 'a', chapter_title: '章节 A', type: 'chapter_committed', stage: 'committed', status: 'succeeded', message: '已提交', data: { head_content_revision: 3 } },
    { event_id: 'e3', sequence: 3, job_id: 'batch-1', chapter_id: 'b', chapter_title: '章节 B', type: 'chapter_failed', stage: 'committing', status: 'failed', error: { code: 'COMMIT_FAILED', message: '写入失败' } },
  ]

  state = reduceBatchChapterJobEvents(state, events)
  state = reduceBatchChapterJobEvents(state, events)

  assert.equal(state.items.a.status, 'succeeded')
  assert.equal(state.items.a.contentRevision, 3)
  assert.equal(state.items.b.error.code, 'COMMIT_FAILED')
  assert.equal(state.chatByChapter.a.length, 2)
  assert.equal(state.chatByChapter.b.length, 1)
  assert.equal(state.lastSequence, 3)
})

test('does not roll a committed chapter back on a late event and resets a different job', () => {
  let state = hydrateBatchChapterJob(initialBatchChapterJobState(), {
    job_id: 'batch-1',
    items: [{ chapter_id: 'a', status: 'running', attempt: 1 }],
  })
  state = reduceBatchChapterJobEvents(state, [
    { event_id: 'commit', sequence: 1, job_id: 'batch-1', chapter_id: 'a', type: 'chapter_committed', stage: 'committed', status: 'succeeded', data: { attempt: 1, head_content_revision: 2 } },
    { event_id: 'late', sequence: 2, job_id: 'batch-1', chapter_id: 'a', type: 'draft_delta', stage: 'drafting', status: 'running', data: { attempt: 1, text: 'late' } },
  ])
  assert.equal(state.items.a.status, 'succeeded')
  assert.equal(state.items.a.stage, 'committed')

  state = hydrateBatchChapterJob(state, {
    job_id: 'batch-2',
    items: [{ chapter_id: 'b', status: 'queued', attempt: 0 }],
  })
  assert.deepEqual(Object.keys(state.items), ['b'])
  assert.equal(state.lastSequence, 0)
})
