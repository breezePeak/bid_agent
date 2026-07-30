import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const stylesheet = await readFile(
  new URL('../src/assets/styles/main.css', import.meta.url),
  'utf8',
)
const workspaceView = await readFile(
  new URL('../src/components/V3WorkspaceView.vue', import.meta.url),
  'utf8',
)

test('business content owns vertical scrolling inside the fixed application shell', () => {
  const rule = stylesheet.match(/\.main-content\s*\{([^}]*)\}/)

  assert.ok(rule, 'expected a .main-content layout rule')
  assert.match(rule[1], /overflow-y:\s*auto\s*;/)
  assert.match(rule[1], /overflow-x:\s*hidden\s*;/)
  assert.match(rule[1], /min-height:\s*0\s*;/)
  assert.doesNotMatch(rule[1], /overflow:\s*hidden\s*;/)
})

test('LLM request UI distinguishes transport return from candidate validation', () => {
  assert.match(workspaceView, /succeeded:\s*'接口已返回'/)
  assert.match(workspaceView, /controlled_repair:\s*'受控修复'/)
  assert.match(workspaceView, /parameters\.logical_batch_id/)
})

test('program audit warnings remain visible without presenting the product as failed', () => {
  assert.match(workspaceView, /程序审核提示（不阻塞后续流程）/)
  assert.match(workspaceView, /product\.status === 'warning'/)
  assert.match(workspaceView, /product\.warnings/)
  assert.match(workspaceView, /warning:\s*'需复核'/)
  assert.match(workspaceView, /warning_count:\s*'审核提示'/)
})

test('planning UI exposes condition traceability without relying on raw JSON', () => {
  assert.match(workspaceView, /condition\.normalized_condition \|\| condition\.text/)
  assert.match(workspaceView, /conditionRoleLabel\(condition\.condition_role\)/)
  assert.match(workspaceView, /condition\.source_excerpt/)
  assert.match(workspaceView, /condition\.source_location\.label/)
  assert.match(workspaceView, /condition\.response_units/)
  assert.match(workspaceView, /condition\.destinations/)
  assert.match(workspaceView, /chapter\.score_conditions/)
  assert.match(workspaceView, /chapter\.requirements/)
  assert.match(workspaceView, /planningView\.quality_gates/)
  assert.match(workspaceView, /document_quality_gate/)
})

test('full document generation stays observable and loads chapter bodies on demand', () => {
  assert.match(workspaceView, /完整标书生成任务已启动/)
  assert.match(workspaceView, /window\.setInterval\(\(\) => \{/)
  assert.match(workspaceView, /\}, 2000\)/)
  assert.match(workspaceView, /fetchV3ContentUnit/)
  assert.match(workspaceView, /generationContent\.units/)
  assert.match(workspaceView, /正在生成，不要重复提交/)
  assert.match(workspaceView, /DeepSeek 研究/)
})
