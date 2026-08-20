import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { compileScript, parse } from '@vue/compiler-sfc'


test('ChapterWorkbenchView script and template parse without a frontend build', () => {
  const here = dirname(fileURLToPath(import.meta.url))
  const filename = resolve(here, '../src/components/ChapterWorkbenchView.vue')
  const source = fs.readFileSync(filename, 'utf8')
  const parsed = parse(source, { filename })

  assert.deepEqual(parsed.errors, [])
  assert.ok(parsed.descriptor.template)
  assert.ok(parsed.descriptor.scriptSetup)
  assert.doesNotThrow(() => compileScript(parsed.descriptor, { id: 'chapter-workbench' }))
  assert.doesNotMatch(source, />生成草稿</)
  assert.doesNotMatch(source, />H2 确认</)
  assert.doesNotMatch(source, />确认提纲，开始写</)
  assert.doesNotMatch(source, />退回重列</)
  assert.match(source, /document_approval_requested/)
  assert.match(source, /applyChatAuthority\(data\.authority \|\|/)
  assert.match(source, /review_status: 'idle'/)
  assert.match(source, /researchGapConfirmation\.value = null/)
  assert.match(source, /CHAPTER_RESEARCH_CONFIRMATION_REQUIRED/)
  assert.doesNotMatch(source, /generateDraft\(\{ allowResearchGap: true \}\)/)
  assert.match(source, /确认仅使用现有项目资料继续写正文/)
  assert.match(source, /type === 'draft_delta'/)
})
