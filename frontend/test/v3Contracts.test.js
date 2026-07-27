import assert from 'node:assert/strict'
import test from 'node:test'

import {
  V3_WORKSPACES_PATH,
  buildResearchResolveCommand,
  buildRunPipelineCommand,
  isDeepSeekEligibleInput,
  normalizeV3WorkspaceSnapshot,
  selectDeepSeekAttachmentIds,
  v3WorkspacePath,
  workspaceRevisionFromV3Payload,
} from '../src/api/v3Contracts.js'

test('V3 workspace routes stay in the V3 namespace and encode run IDs', () => {
  const runId = '客户 A/标书 01'
  const routes = [
    V3_WORKSPACES_PATH,
    v3WorkspacePath(runId),
    v3WorkspacePath(runId, 'snapshot'),
    v3WorkspacePath(runId, '/commands/'),
    v3WorkspacePath(runId, 'uploads'),
    v3WorkspacePath(runId, 'chat/turn'),
    v3WorkspacePath(runId, 'exports/final'),
  ]

  assert.equal(V3_WORKSPACES_PATH, '/v3/workspaces')
  assert.equal(
    routes[2],
    `/v3/workspaces/${encodeURIComponent(runId)}/snapshot`,
  )
  for (const route of routes) {
    assert.match(route, /^\/v3\/workspaces(?:\/|$)/)
    assert.doesNotMatch(route, /\/api\/v2\/workspaces|\/v2\/workspaces/)
  }
  assert.throws(() => v3WorkspacePath(''), /runId is required/)
})

test('pipeline command uses the frozen command envelope', () => {
  assert.deepEqual(buildRunPipelineCommand('cmd-pipeline-1', 17), {
    command_id: 'cmd-pipeline-1',
    kind: 'document.run_pipeline',
    payload: {},
    expected_revision: 17,
    idempotency_key: 'cmd-pipeline-1',
  })
})

test('research command carries explicit DeepSeek attachments and revision', () => {
  const attachments = ['input-a', 'input-b']
  const command = buildResearchResolveCommand(
    'cmd-research-1',
    23,
    'need-security-standard',
    attachments,
  )

  assert.deepEqual(command, {
    command_id: 'cmd-research-1',
    kind: 'research.resolve',
    payload: {
      need_id: 'need-security-standard',
      provider_id: 'deepseek_web',
      attachment_input_ids: ['input-a', 'input-b'],
    },
    expected_revision: 23,
    idempotency_key: 'cmd-research-1',
  })
  attachments.push('late-mutation')
  assert.deepEqual(command.payload.attachment_input_ids, ['input-a', 'input-b'])
  assert.throws(
    () => buildResearchResolveCommand('cmd', -1, 'need', []),
    /expectedRevision must be a non-negative integer/,
  )
})

test('workspace snapshot normalization produces stable V3 component inputs', () => {
  const normalized = normalizeV3WorkspaceSnapshot({
    snapshot: {
      workspace_revision: '9',
      inputs: { inputs: [{ input_id: 'tender-1', active: true }] },
      content_units: null,
      quality: { report: null },
      materials: { summary: null, items: 'invalid' },
      evidence_needs: [{ need_id: 'need-1' }],
    },
  })

  assert.equal(normalized.workspace_revision, 9)
  assert.deepEqual(normalized.inputs.inputs, [{ input_id: 'tender-1', active: true }])
  assert.deepEqual(normalized.document, {})
  assert.deepEqual(normalized.content_units, [])
  assert.deepEqual(normalized.quality.report, {})
  assert.deepEqual(normalized.materials, { summary: {}, items: [] })
  assert.deepEqual(normalized.evidence_needs, [{ need_id: 'need-1' }])
  assert.equal(workspaceRevisionFromV3Payload({ snapshot: { workspace_revision: 9 } }), 9)
  assert.equal(workspaceRevisionFromV3Payload({ snapshot: { workspace_revision: -2 } }), 0)
  assert.equal(workspaceRevisionFromV3Payload(null), 0)
})

test('DeepSeek attachment selection permits only active supported inputs', () => {
  const inputs = [
    { input_id: 'active-pdf', active: true, filename: 'tender.PDF' },
    { input_id: 'active-docx', active: true, filename: 'proof.docx' },
    { input_id: 'inactive-pdf', active: false, filename: 'old.pdf' },
    { input_id: 'active-exe', active: true, filename: 'unsafe.exe' },
  ]

  assert.equal(isDeepSeekEligibleInput(inputs[0]), true)
  assert.equal(isDeepSeekEligibleInput(inputs[2]), false)
  assert.equal(isDeepSeekEligibleInput(inputs[3]), false)
  assert.deepEqual(
    selectDeepSeekAttachmentIds(inputs, [
      'active-pdf',
      'inactive-pdf',
      'active-docx',
      'active-pdf',
      'unknown',
    ]),
    ['active-pdf', 'active-docx'],
  )
})
