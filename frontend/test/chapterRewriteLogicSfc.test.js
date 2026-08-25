import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { compileScript, parse } from '@vue/compiler-sfc'
import { normalizeChapterRewriteMatch } from '../src/api/rewriteContracts.js'

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

test('rewrite workbench remains mode-gated, read-only, and preserves the body editor', () => {
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
  assert.doesNotMatch(source, /确认改写|执行改写|应用旧文/)
})
