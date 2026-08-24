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
  assert.doesNotMatch(source, /applyChatAuthority/)
  assert.doesNotMatch(source, /review_status: 'idle'/)
  assert.match(source, /type === 'writing_phase'/)
  assert.doesNotMatch(source, /researchGapConfirmation/)
  assert.doesNotMatch(source, /CHAPTER_RESEARCH_CONFIRMATION_REQUIRED/)
  assert.doesNotMatch(source, /allow_research_gap/)
  assert.match(source, /type === 'draft_delta'/)
  assert.match(source, />\s*编写逻辑\s*</)
  assert.match(source, /v-show="centerTab === 'document'"/)
  assert.match(source, /planAbortController\?\.abort\(\)/)
  assert.match(source, /workbench_enabled === true/)
})

test('PR-04 read-only planning components compile and expose no planning mutations', () => {
  const here = dirname(fileURLToPath(import.meta.url))
  const components = [
    'ChapterWritingPlanPanel.vue',
    'ChapterPlanGraph.vue',
    'ChapterPlanSourceCard.vue',
    'ChapterPlanUnitCard.vue',
    'ChapterPlanDetailDrawer.vue',
  ]
  for (const component of components) {
    const filename = resolve(here, `../src/components/${component}`)
    const source = fs.readFileSync(filename, 'utf8')
    const parsed = parse(source, { filename })
    assert.deepEqual(parsed.errors, [], component)
    assert.ok(parsed.descriptor.template, component)
    assert.ok(parsed.descriptor.scriptSetup, component)
    assert.doesNotThrow(() => compileScript(parsed.descriptor, { id: `pr04-${component}` }), component)
    assert.doesNotMatch(source, /确认规划|删除来源|继续搜索|保存规划/, component)
  }

  const panel = fs.readFileSync(resolve(here, '../src/components/ChapterWritingPlanPanel.vue'), 'utf8')
  assert.match(panel, /当前章节仍使用现有内部 WritingPlan/)
  assert.match(panel, /此视图只读|只读/)
  assert.match(panel, /规划已陈旧/)

  const graph = fs.readFileSync(resolve(here, '../src/components/ChapterPlanGraph.vue'), 'utf8')
  assert.match(graph, /ResizeObserver/)
  assert.match(graph, /source_bindings|props\.bindings/)
  assert.match(graph, /aria-expanded/)
})
