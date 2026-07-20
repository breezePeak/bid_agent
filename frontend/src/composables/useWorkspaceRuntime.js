/**
 * Single live status bus for the main console (not 3D).
 *
 * All panels must read from here — do not independently poll
 * /agent/goal, /agent/activity, /repair-jobs/current for "truth".
 * Source: GET /api/status (embeds runtime + goal + activity + repair_job).
 */
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { fetchStatus, fetchRuntimeStatus } from '../api'

const state = reactive({
  runId: '',
  loading: false,
  error: '',
  updatedAt: 0,
  /** full /api/status payload */
  status: null,
  /** /api/runtime stores slice (also inside status.runtime) */
  runtime: null,
})

let pollTimer = null
let pollInFlight = false
let subscribers = 0
let pollMs = 2000

function applyStatusPayload(data) {
  if (!data || typeof data !== 'object') return
  state.status = data
  state.runtime = data.runtime && typeof data.runtime === 'object' ? data.runtime : state.runtime
  state.updatedAt = Date.now()
  state.error = ''
}

function applyRuntimePayload(data) {
  if (!data || typeof data !== 'object' || data.ok === false) return
  state.runtime = data
  // mirror into status shell so consumers can use either path
  if (state.status && typeof state.status === 'object') {
    state.status = {
      ...state.status,
      runtime: data,
      product_mode: data.product_mode,
      product_mode_label: data.product_mode_label,
      consistent: data.consistent,
      consistency_warnings: data.warnings || [],
      goal: goalFromRuntime(data) || state.status.goal,
      agent_activity: activityFromRuntime(data) || state.status.agent_activity,
      repair_job: repairFromRuntime(data) || state.status.repair_job,
      materials_summary: materialsFromRuntime(data) || state.status.materials_summary,
    }
  }
  state.updatedAt = Date.now()
}

function goalFromRuntime(runtime) {
  const g = runtime?.stores?.goal
  if (!g?.exists) return state.status?.goal || null
  return {
    goal_id: g.goal_id,
    status: g.status,
    all_criteria_ok: g.all_criteria_ok,
    blocked_reason: g.blocked_reason,
    raw_user_goal: g.raw_user_goal,
    ...(state.status?.goal && typeof state.status.goal === 'object' ? {} : {}),
  }
}

function activityFromRuntime(runtime) {
  const a = runtime?.stores?.activity
  if (!a) return state.status?.agent_activity || null
  // keep full activity from /api/status when present; runtime only has summary slice
  return state.status?.agent_activity || {
    status: a.status,
    phase: a.phase,
    phase_label: a.phase_label,
    summary: {
      running: a.running,
      queued: a.queued,
      done: a.done,
      failed: a.failed,
    },
    materials_deferred: a.materials_deferred,
    agents: [],
  }
}

function repairFromRuntime(runtime) {
  const r = runtime?.stores?.repair
  if (!r?.exists && !r?.job_id) return state.status?.repair_job || null
  return {
    ...(state.status?.repair_job || {}),
    job_id: r.job_id,
    status: r.status,
    phase: r.phase,
    message: r.message,
    resume_command: r.resume_command,
  }
}

function materialsFromRuntime(runtime) {
  const m = runtime?.stores?.materials
  if (!m) return state.status?.materials_summary || null
  return {
    exists: m.exists,
    total: m.total,
    deferred: m.deferred,
    ready: m.ready,
    missing: m.missing,
  }
}

async function refresh({ heal = false } = {}) {
  if (pollInFlight) return state.status
  pollInFlight = true
  state.loading = true
  try {
    // Primary: full status (workflow + activity agents + repair + runtime)
    const resp = await fetchStatus()
    const data = resp?.data || resp
    applyStatusPayload(data)
    // Optional heal path via runtime endpoint
    if (heal) {
      const r = await fetchRuntimeStatus(true)
      applyRuntimePayload(r?.data || r)
    }
    return state.status
  } catch (e) {
    state.error = e?.message || 'status poll failed'
    return null
  } finally {
    pollInFlight = false
    state.loading = false
  }
}

function startPolling(ms = pollMs) {
  pollMs = ms
  stopPolling()
  refresh()
  pollTimer = setInterval(() => refresh(), pollMs)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

function bindRun(runId) {
  const id = String(runId || '')
  if (state.runId !== id) {
    state.runId = id
    state.status = null
    state.runtime = null
    state.error = ''
  }
}

/**
 * Use in any Vue component that needs live workspace truth.
 * First subscriber starts poll; last unmount stops it.
 */
export function useWorkspaceRuntime(options = {}) {
  const intervalMs = options.intervalMs ?? 2000
  const auto = options.auto !== false
  const runIdRef = options.runId // may be ref or string

  const status = computed(() => state.status)
  const runtime = computed(() => state.runtime || state.status?.runtime || null)
  const productMode = computed(
    () => runtime.value?.product_mode || state.status?.product_mode || ''
  )
  const productModeLabel = computed(
    () => runtime.value?.product_mode_label || state.status?.product_mode_label || ''
  )
  const consistent = computed(() => {
    if (runtime.value && typeof runtime.value.consistent === 'boolean') return runtime.value.consistent
    if (typeof state.status?.consistent === 'boolean') return state.status.consistent
    return true
  })
  const consistencyWarnings = computed(() => {
    const w = runtime.value?.warnings || state.status?.consistency_warnings || []
    return Array.isArray(w) ? w : []
  })
  const goal = computed(() => {
    // Prefer full goal object from /api/status (single bus)
    const full = state.status?.goal_full
    if (full && typeof full === 'object' && (full.goal_id || full.status)) {
      return {
        ...full,
        summary: state.status?.goal?.summary || full.summary || '',
      }
    }
    return state.status?.goal || null
  })
  const activity = computed(() => state.status?.agent_activity || null)
  const repairJob = computed(() => state.status?.repair_job || null)
  const materialsSummary = computed(() => state.status?.materials_summary || null)
  const materialsDeferred = computed(() => Number(materialsSummary.value?.deferred || 0) || 0)
  const workflow = computed(() => state.status?.workflow || [])
  const running = computed(() => !!state.status?.running)
  const pipeline = computed(() => state.status?.pipeline || null)
  const issuesSummary = computed(() => state.status?.issues_summary || null)
  const complianceSummary = computed(() => state.status?.compliance_summary || null)
  const stores = computed(() => runtime.value?.stores || null)

  function resolveRunId() {
    if (runIdRef == null) return state.runId
    return typeof runIdRef === 'object' && 'value' in runIdRef ? runIdRef.value : runIdRef
  }

  onMounted(() => {
    subscribers += 1
    bindRun(resolveRunId())
    if (auto && subscribers === 1) startPolling(intervalMs)
    else if (auto) refresh()
  })

  onBeforeUnmount(() => {
    subscribers = Math.max(0, subscribers - 1)
    if (subscribers === 0) stopPolling()
  })

  if (runIdRef && typeof runIdRef === 'object' && 'value' in runIdRef) {
    watch(runIdRef, (id) => {
      bindRun(id)
      refresh()
    })
  }

  return {
    state,
    status,
    runtime,
    productMode,
    productModeLabel,
    consistent,
    consistencyWarnings,
    goal,
    activity,
    repairJob,
    materialsSummary,
    materialsDeferred,
    workflow,
    running,
    pipeline,
    issuesSummary,
    complianceSummary,
    stores,
    loading: computed(() => state.loading),
    error: computed(() => state.error),
    updatedAt: computed(() => state.updatedAt),
    refresh,
    heal: () => refresh({ heal: true }),
  }
}

/** Non-component access (e.g. after orchestrate) */
export function getWorkspaceRuntimeSnapshot() {
  return {
    status: state.status,
    runtime: state.runtime,
    updatedAt: state.updatedAt,
  }
}

export function pushStatusSnapshot(data) {
  applyStatusPayload(data)
}

export async function forceRuntimeRefresh(opts) {
  return refresh(opts)
}
