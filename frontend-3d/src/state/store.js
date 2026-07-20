import { STAGE_DEFS, PHASES } from '../config/stages.js'

/**
 * Normalize backend /api/status + activity into a render-ready snapshot.
 */

function emptySnapshot() {
  return {
    connected: false,
    demo: false,
    running: false,
    currentTask: '',
    runId: '',
    runName: '',
    runState: { stage: '', status: '', message: '', updated_at: '' },
    projectType: '',
    progress: 0,
    doneCount: 0,
    totalCount: STAGE_DEFS.length,
    stages: STAGE_DEFS.map((s, i) => ({
      ...s,
      index: i,
      state: 'pending',
      message: '',
      durationLabel: '',
      done: false,
      ready: false,
    })),
    phases: PHASES.map((p) => ({ ...p, done: 0, total: 0, progress: 0 })),
    agents: [],
    activity: {
      status: 'idle',
      phase: '',
      phase_label: '',
      summary: { total: 0, running: 0, done: 0, failed: 0, queued: 0 },
    },
    workspace: {},
    outputs: {},
    goal: null,
    goalSummary: '',
    events: [],
    materialsDeferred: 0,
    issuesOpen: 0,
    clock: Date.now(),
  }
}

function mapWorkflowState(step, running, currentTask) {
  if (!step) return 'pending'
  if (step.state) return String(step.state)
  if (step.done) return 'done'
  if (running && step.command === currentTask) return 'running'
  if (step.ready) return 'ready'
  return 'pending'
}

export function createStore() {
  let snapshot = emptySnapshot()
  const listeners = new Set()

  function emit() {
    for (const fn of listeners) fn(snapshot)
  }

  return {
    get() {
      return snapshot
    },
    subscribe(fn) {
      listeners.add(fn)
      fn(snapshot)
      return () => listeners.delete(fn)
    },
    reset() {
      snapshot = emptySnapshot()
      emit()
    },
    applyStatus(status, { demo = false, connected = true } = {}) {
      if (!status || typeof status !== 'object') return

      const workflow = Array.isArray(status.workflow) ? status.workflow : []
      const byCommand = Object.fromEntries(workflow.map((s) => [s.command, s]))
      const byId = Object.fromEntries(workflow.map((s) => [s.id, s]))
      const running = Boolean(status.running)
      const currentTask = String(status.current_task || status.pipeline?.current_stage || '')
      const runState = status.run_state || {}
      const activity = status.agent_activity || {}

      const stages = STAGE_DEFS.map((def, i) => {
        const step = byCommand[def.command] || byId[def.id] || {}
        const state = mapWorkflowState(step, running, currentTask)
        return {
          ...def,
          index: i,
          state,
          message: String(step.message || ''),
          durationLabel: String(step.duration_label || step.durationLabel || ''),
          done: Boolean(step.done) || state === 'done',
          ready: Boolean(step.ready),
        }
      })

      const doneCount = stages.filter((s) => s.done || s.state === 'done').length
      const phaseStats = PHASES.map((p) => {
        const members = stages.filter((s) => s.phase === p.id)
        const done = members.filter((s) => s.done || s.state === 'done').length
        return {
          ...p,
          done,
          total: members.length,
          progress: members.length ? done / members.length : 0,
        }
      })

      let agents = []
      if (Array.isArray(activity.agents)) {
        agents = activity.agents.filter((a) => a && typeof a === 'object')
      }

      snapshot = {
        ...snapshot,
        connected,
        demo,
        running,
        currentTask,
        runId: String(status.active_run?.id || status.running_run?.id || ''),
        runName: String(status.active_run?.name || status.active_run?.id || ''),
        runState: {
          stage: String(runState.stage || ''),
          status: String(runState.status || ''),
          message: String(runState.message || ''),
          updated_at: String(runState.updated_at || ''),
        },
        projectType: String(status.project_profile?.project_type || ''),
        progress: stages.length ? doneCount / stages.length : 0,
        doneCount,
        totalCount: stages.length,
        stages,
        phases: phaseStats,
        agents,
        activity: {
          status: String(activity.status || 'idle'),
          phase: String(activity.phase || ''),
          phase_label: String(activity.phase_label || ''),
          summary: activity.summary || { total: 0, running: 0, done: 0, failed: 0, queued: 0 },
          materials_deferred: Number(activity.materials_deferred || 0),
        },
        workspace: status.workspace || {},
        outputs: status.outputs || {},
        events: Array.isArray(status.run_events_tail) ? status.run_events_tail.slice(-12).reverse() : [],
        materialsDeferred: Number(
          activity.materials_deferred ||
            status.materials_summary?.deferred ||
            0,
        ),
        issuesOpen: Number(status.issues_summary?.open || status.issues_summary?.total_open || 0),
        clock: Date.now(),
      }
      emit()
    },
    applyActivity(payload) {
      const activity = payload?.activity || payload || {}
      if (!activity || typeof activity !== 'object') return
      const agents = Array.isArray(activity.agents) ? activity.agents : snapshot.agents
      snapshot = {
        ...snapshot,
        agents,
        activity: {
          status: String(activity.status || snapshot.activity.status),
          phase: String(activity.phase || snapshot.activity.phase),
          phase_label: String(activity.phase_label || snapshot.activity.phase_label),
          summary: activity.summary || snapshot.activity.summary,
          materials_deferred: Number(activity.materials_deferred || snapshot.activity.materials_deferred || 0),
        },
        materialsDeferred: Number(activity.materials_deferred || snapshot.materialsDeferred || 0),
        clock: Date.now(),
      }
      emit()
    },
    applyGoal(payload) {
      snapshot = {
        ...snapshot,
        goal: payload?.goal || null,
        goalSummary: String(payload?.summary || ''),
        clock: Date.now(),
      }
      emit()
    },
  }
}
