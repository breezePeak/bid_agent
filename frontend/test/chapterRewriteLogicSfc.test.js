import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { compileScript, parse } from '@vue/compiler-sfc'
import { normalizeChapterRewriteMatch, normalizeChapterRewritePlan } from '../src/api/rewriteContracts.js'

const here = dirname(fileURLToPath(import.meta.url))

test('rewrite response normalization accepts command receipts and fills arrays', () => {
  const value = normalizeChapterRewriteMatch({
    receipt: { result: { rewrite_match: { chapter_id: 'chapter-1' } } },
  })
  assert.equal(value.chapter_id, 'chapter-1')
  assert.equal(value.read_only, true)
  assert.deepEqual(value.matches, [])
  assert.deepEqual(value.coverage, [])
  assert.deepEqual(value.writing_plan.blocks, [])
})

test('rewrite plan normalization preserves CAS identity and editable arrays', () => {
  const value = normalizeChapterRewritePlan({
    receipt: { result: { rewrite_plan: { plan_revision: 3, plan_hash: 'hash-3' } } },
  })
  assert.equal(value.plan_revision, 3)
  assert.equal(value.plan_hash, 'hash-3')
  assert.deepEqual(value.selected_legacy_blocks, [])
  assert.deepEqual(value.pollution_findings, [])
})

for (const name of [
  'ChapterRewriteLogicPanel.vue',
  'LegacySectionCard.vue',
  'LegacyBlockPreviewDrawer.vue',
]) {
  test(`${name} parses without a frontend build`, () => {
    const filename = resolve(here, `../src/components/${name}`)
    const parsed = parse(fs.readFileSync(filename, 'utf8'), { filename })
    assert.deepEqual(parsed.errors, [])
    assert.doesNotThrow(() => compileScript(parsed.descriptor, { id: name }))
  })
}

test('rewrite workbench remains mode-gated, CAS editable, and preserves the body editor', () => {
  const source = fs.readFileSync(
    resolve(here, '../src/components/ChapterWorkbenchView.vue'),
    'utf8',
  )
  assert.match(source, /project_mode/)
  assert.match(source, /v-if="isBidRewrite" class="middle-tabs"/)
  assert.match(source, />改写逻辑<\/button>/)
  assert.match(source, />正文<\/button>/)
  assert.match(source, /v-show="!isBidRewrite \|\| middleTab === 'body'"/)
  assert.match(source, /rewriteMatchAbortController\?\.abort\(\)/)
  assert.match(source, /updateChapterRewritePlan/)
  assert.match(source, /CHAPTER_REWRITE_PLAN_CONFLICT/)
  assert.match(source, /executeRewritePlan/)
})

test('rewrite panel emits only structured operations and exposes recovery controls', () => {
  const source = fs.readFileSync(
    resolve(here, '../src/components/ChapterRewriteLogicPanel.vue'),
    'utf8',
  )
  for (const operation of [
    'select_legacy_block', 'unselect_legacy_block', 'change_block_usage',
    'update_instruction', 'set_strategy', 'add_new_content_item',
    'remove_new_content_item', 'resolve_pollution',
  ]) assert.match(source, new RegExp(operation))
  assert.match(source, /刷新并恢复/)
  assert.match(source, /保存草稿/)
  assert.match(source, /补充查询/)
  assert.match(source, /确认当前方案/)
  assert.match(source, /开始改写/)
  assert.match(source, /\$emit\('execute'\)/)
})
