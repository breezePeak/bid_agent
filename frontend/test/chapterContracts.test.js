import assert from 'node:assert/strict'
import test from 'node:test'

import {
  chapterChatHistoryPath,
  chapterChatTurnPath,
  chapterReadonlyPath,
  chapterPath,
  chapterStatusLabel,
  chaptersPath,
  documentComposePath,
  normalizeChapterList,
} from '../src/api/chapterContracts.js'

test('chapter paths are rooted under v3 workspaces', () => {
  assert.equal(chaptersPath('ws-1'), '/v3/workspaces/ws-1/chapters')
  assert.equal(chapterPath('ws-1', 'ch-a'), '/v3/workspaces/ws-1/chapters/ch-a')
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
    chapterReadonlyPath('ws-1', 'ch-a', 'ch-b'),
    '/v3/workspaces/ws-1/chapters/ch-a/readonly/ch-b',
  )
  assert.equal(
    chapterChatHistoryPath('客户/A', '章 1'),
    `/v3/workspaces/${encodeURIComponent('客户/A')}/chapters/${encodeURIComponent('章 1')}/chat/history`,
  )
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
