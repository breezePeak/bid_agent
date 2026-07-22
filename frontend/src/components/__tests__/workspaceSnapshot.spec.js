import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { statusFromV2Snapshot } from '../../composables/workspaceSnapshot.js'

describe('statusFromV2Snapshot', () => {
  it('maps authoritative control domains over presentation fields', () => {
    const status = statusFromV2Snapshot({
      revision: 7,
      operation: { operation_id: 'op-1', status: 'running' },
      goal: { goal_id: 'goal-v2', status: 'running' },
      activity: { status: 'running', phase: 'writing' },
      repair_job: { job_id: 'repair-v2', status: 'partial' },
      materials: { total: 1, ready: 1, source: 'control.db' },
      findings: { issues_summary: { open: 1, source: 'control.db' } },
      quality: {
        source: 'control.db',
        latest_gate_evaluations: [{ command: 'global-review', verdict: 'pass' }],
      },
      artifacts: [{ artifact_key: 'outputs/final.docx', status: 'ready' }],
      stage_runs: [{ stage_command: 'build-docx', status: 'succeeded', attempt: 1 }],
      current_stage_runs: [{ stage_command: 'build-docx', status: 'succeeded', attempt: 1 }],
      artifact_files: { outputs: { final_docx: true } },
      presentation: {
        running: false,
        goal: { goal_id: 'goal-v1' },
        workflow: [{ command: 'write-all' }],
      },
    })

    assert.equal(status.workspace_revision, 7)
    assert.equal(status.running, true)
    assert.equal(status.goal.goal_id, 'goal-v2')
    assert.equal(status.agent_activity.phase, 'writing')
    assert.equal(status.repair_job.job_id, 'repair-v2')
    assert.equal(status.materials_summary.source, 'control.db')
    assert.equal(status.issues_summary.source, 'control.db')
    assert.equal(status.quality.latest_gate_evaluations[0].verdict, 'pass')
    assert.deepEqual(status.workflow, [{ command: 'write-all' }])
    assert.equal(status.artifacts[0].artifact_key, 'outputs/final.docx')
    assert.equal(status.stage_runs[0].stage_command, 'build-docx')
    assert.equal(status.current_stage_runs[0].stage_command, 'build-docx')
    assert.equal(status.artifact_files.outputs.final_docx, true)
    assert.equal(status.control_source, 'control.db')
  })

  it('returns null for an invalid snapshot', () => {
    assert.equal(statusFromV2Snapshot(null), null)
  })
})
