import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import {
  hasPipelineExecutionHistory,
  isStalePipelineControlMessage,
  normalizeSequentialPlanSteps,
  sourceFilesFromSections,
  statusDetailText,
  statusFromV2Snapshot,
} from '../../composables/workspaceSnapshot.js'

describe('statusFromV2Snapshot', () => {
  it('maps authoritative control domains over presentation fields', () => {
    const status = statusFromV2Snapshot({
      revision: 7,
      operation: { operation_id: 'op-1', status: 'running' },
      pipeline: { operation_id: 'op-1', status: 'running', current_stage: 'build-docx' },
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
      sources: {
        tender: [{ name: '招标文件.docx' }],
        company: [],
        template: [{ name: '模板.docx' }],
      },
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
    assert.deepEqual(status.sources, {
      tender: [{ name: '招标文件.docx' }],
      company: [],
      template: [{ name: '模板.docx' }],
    })
    assert.equal(status.stage_runs[0].stage_command, 'build-docx')
    assert.equal(status.current_stage_runs[0].stage_command, 'build-docx')
    assert.equal(status.artifact_files.outputs.final_docx, true)
    assert.equal(status.control_source, 'control.db')
  })

  it('returns null for an invalid snapshot', () => {
    assert.equal(statusFromV2Snapshot(null), null)
  })

  it('does not invent an empty sources object for an older snapshot', () => {
    const status = statusFromV2Snapshot({ presentation: {} })
    assert.equal(Object.hasOwn(status, 'sources'), false)
    assert.equal(status.goal, null)
  })

  it('projects the authoritative active StageRun into the visible workflow', () => {
    const status = statusFromV2Snapshot({
      operation: { operation_id: 'op-2', status: 'running' },
      pipeline: { status: 'running', current_stage: '' },
      current_stage_runs: [
        { operation_id: 'op-2', stage_command: 'parse-score', status: 'running' },
      ],
      presentation: {
        running: false,
        current_task: '',
        workflow: [
          { command: 'prepare-inputs', done: true, state: 'done' },
          { command: 'parse-score', done: false, state: 'ready' },
        ],
      },
    })

    assert.equal(status.running, true)
    assert.equal(status.current_task, 'parse-score')
    assert.equal(status.pipeline.current_stage, 'parse-score')
    assert.equal(status.workflow[1].state, 'running')
  })

  it('does not report a non-pipeline Operation as a running pipeline', () => {
    const status = statusFromV2Snapshot({
      operation: { operation_id: 'repair-1', kind: 'repair.start', status: 'running' },
      pipeline: { operation_id: 'pipeline-1', status: 'complete', current_stage: 'build-docx' },
      current_stage_runs: [{ operation_id: 'repair-1', stage_command: 'minimal-repair', status: 'running' }],
      pipeline_stage_runs: [{ operation_id: 'pipeline-1', stage_command: 'build-docx', status: 'succeeded' }],
      presentation: {
        running: true,
        current_task: 'minimal-repair',
        workflow: [{ command: 'build-docx', done: true, state: 'done' }],
      },
    })

    assert.equal(status.running, false)
    assert.equal(status.current_task, '')
    assert.equal(status.pipeline.current_stage, 'build-docx')
    assert.equal(status.pipeline_stage_runs[0].operation_id, 'pipeline-1')
  })
})

describe('sourceFilesFromSections', () => {
  it('restores upload tiles from the same file-tree payload used by FileExplorer', () => {
    assert.deepEqual(sourceFilesFromSections([
      { key: 'tender', items: [{ name: '招标文件.docx' }] },
      { key: 'company', items: [] },
      { key: 'template', items: [{ name: '模板.docx' }] },
      { key: 'outputs', items: [{ name: 'final.docx' }] },
    ]), {
      tender: ['招标文件.docx'],
      company: [],
      template: ['模板.docx'],
    })
  })
})

describe('normalizeSequentialPlanSteps', () => {
  it('does not show a later historical artifact as complete while an earlier step is running', () => {
    assert.deepEqual(normalizeSequentialPlanSteps([
      { command: 'prepare-inputs', status: 'running', durationLabel: '2秒' },
      { command: 'split-docs', status: 'pending' },
      { command: 'build-materials-checklist', status: 'done', durationLabel: '1秒' },
    ]), [
      { command: 'prepare-inputs', status: 'running', durationLabel: '2秒' },
      { command: 'split-docs', status: 'pending' },
      {
        command: 'build-materials-checklist',
        status: 'pending',
        durationLabel: '',
        message: '等待前置步骤',
      },
    ])
  })
})

describe('statusDetailText', () => {
  it('renders structured pipeline errors instead of object coercion', () => {
    assert.equal(statusDetailText({ code: 'STAGE_FAILED', message: '解析评分超时' }), '解析评分超时')
    assert.equal(statusDetailText({ code: 'STAGE_FAILED' }), 'STAGE_FAILED')
  })
})

describe('isStalePipelineControlMessage', () => {
  it('recognizes obsolete pipeline control failures without matching normal stage logs', () => {
    assert.equal(isStalePipelineControlMessage('流水线启动请求失败: 缺少起始阶段'), true)
    assert.equal(isStalePipelineControlMessage('后端流水线已停止：恢复流水线缺少 control.db 中的起始阶段'), true)
    assert.equal(isStalePipelineControlMessage('解析评分执行失败，请查看日志'), false)
    assert.equal(isStalePipelineControlMessage('历史状态（已恢复）：后端流水线已停止'), false)
  })
})

describe('hasPipelineExecutionHistory', () => {
  it('distinguishes a fresh workspace from one with an existing pipeline operation', () => {
    assert.equal(hasPipelineExecutionHistory({ pipeline: {} }), false)
    assert.equal(hasPipelineExecutionHistory({
      pipeline: { operation_id: 'pipeline-1', status: 'failed' },
    }), true)
  })
})
