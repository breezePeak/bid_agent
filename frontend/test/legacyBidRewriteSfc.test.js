import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'
import { parse } from '@vue/compiler-sfc'

import { normalizeV3WorkspaceSnapshot } from '../src/api/v3Contracts.js'


test('bid rewrite UI components parse and remain mode-gated', () => {
  for (const filename of [
    'CreateWorkspaceDialog.vue',
    'LegacyBidUploadPanel.vue',
    'LegacyBidIndexPreview.vue',
    'V3WorkspaceView.vue',
  ]) {
    const source = fs.readFileSync(path.resolve('src/components', filename), 'utf8')
    const result = parse(source, { filename })
    assert.deepEqual(result.errors, [])
  }
  const workspace = fs.readFileSync(path.resolve('src/components/V3WorkspaceView.vue'), 'utf8')
  assert.match(workspace, /for="quick-upload-legacy-bid"/)
  assert.match(workspace, /@click="openLegacyPreview\(item\)"/)
  for (const label of ['新招标文件解析', '评分与项目理解', '生成新目录', '等待目录确认']) {
    assert.match(workspace, new RegExp(label))
  }
  assert.match(workspace, /const rawPipelineStages = computed/)
})

test('workspace normalization defaults old workspaces and preserves legacy status', () => {
  assert.equal(normalizeV3WorkspaceSnapshot({}).profile.project_mode, 'full_write')
  const snapshot = normalizeV3WorkspaceSnapshot({
    profile: { project_mode: 'bid_rewrite' },
    legacy_bid: {
      status: 'ready',
      active_id: 'legacy-1',
      filename: 'old.docx',
      section_count: 3,
      block_count: 12,
    },
    material_readiness: {
      project_mode: 'bid_rewrite',
      ready: true,
      required: ['tender', 'legacy_bid'],
      items: { tender: { ready: true }, legacy_bid: { ready: true } },
    },
  })
  assert.equal(snapshot.profile.project_mode, 'bid_rewrite')
  assert.equal(snapshot.legacy_bid.status, 'ready')
  assert.equal(snapshot.legacy_bid.section_count, 3)
  assert.equal(snapshot.material_readiness.ready, true)
  assert.deepEqual(snapshot.material_readiness.required, ['tender', 'legacy_bid'])
})
