/**
 * Single live status bus for the main console (not 3D).
 *
 * All panels must read from here — do not independently poll
 * /agent/goal, /agent/activity, /repair-jobs/current for "truth".
 * Source: GET /api/v2/workspaces/:id/snapshot.
 */
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { fetchWorkspaceSnapshot } from '../api'
import { statusFromV2Snapshot } from './workspaceSnapshot'

const state = reactive({
  runId: '',
  loading: false,
  error: '',
  updatedAt: 0,
  /** V2 snapshot adapted to the existing presentation view-model */
  status: null,
  /** presentation-only runtime diagnostics from the V2 snapshot */
  runtime: null,
})

let pollTimer = null
let eventSource = null
let eventRunId = ''
let eventCursor = 0
let eventRefreshTimer = null
let pollInFlight = false
let refreshPending = false
let subscribers = 0
let pollMs = 2000

function applyStatusPayload(data) {
  if (!data || typeof data !== 'object') return
  state.status = data
  state.runtime = data.runtime && typeof data.runtime === 'object' ? data.runtime : state.runtime
  state.updatedAt = Date.now()
  state.error = ''
}

async function refresh() {
  if (pollInFlight) {
    refreshPending = true
    return state.status
  }
  pollInFlight = true
  state.loading = true
  const requestedRunId = resolveBoundRunId()
  try {
    if (!requestedRunId) return null
    const resp = await fetchWorkspaceSnapshot(requestedRunId)
    const body = resp?.data || resp
    const data = statusFromV2Snapshot(body?.snapshot)
    if (requestedRunId !== resolveBoundRunId()) return null
    applyStatusPayload(data)
    return state.status
  } catch (e) {
    state.error = e?.message || 'status poll failed'
    return null
  } finally {
    pollInFlight = false
    state.loading = false
    if (refreshPending) {
      refreshPending = false
      queueMicrotask(() => refresh())
    }
  }
}

function resolveBoundRunId() {
  return String(state.runId || '').trim()
}

function startPolling(ms = pollMs) {
  pollMs = ms
  stopPolling()
  refresh()
  pollTimer = setInterval(() => refresh(), pollMs)
}

function scheduleEventRefresh() {
  if (eventRefreshTimer) return
  eventRefreshTimer = setTimeout(() => {
    eventRefreshTimer = null
    refresh()
  }, 50)
}

function stopEventStream() {
  if (eventSource) eventSource.close()
  eventSource = null
  eventRunId = ''
  if (eventRefreshTimer) clearTimeout(eventRefreshTimer)
  eventRefreshTimer = null
}

function startEventStream(runId) {
  const id = String(runId || '').trim()
  if (!id || typeof EventSource === 'undefined') return
  if (eventSource && eventRunId === id) return
  stopEventStream()
  eventRunId = id
  const path = `/api/v2/workspaces/${encodeURIComponent(id)}/events?after_seq=${eventCursor}`
  eventSource = new EventSource(path)
  eventSource.addEventListener('WorkspaceEvent', (event) => {
    if (eventRunId !== state.runId) return
    const seq = Number(event.lastEventId || 0)
    if (seq > 0) {
      if (eventCursor > 0 && seq > eventCursor + 1) {
        eventCursor = 0
      } else if (seq <= eventCursor) {
        return
      } else {
        eventCursor = seq
      }
    }
    scheduleEventRefresh()
  })
  eventSource.onerror = () => {
    // Browser reconnect and the polling fallback both preserve availability.
  }
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
    eventCursor = 0
    if (subscribers > 0) startEventStream(id)
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
    // V2 snapshot exposes the authoritative full Goal object.
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
    startEventStream(resolveRunId())
    if (auto && subscribers === 1) startPolling(intervalMs)
    else if (auto) refresh()
  })

  onBeforeUnmount(() => {
    subscribers = Math.max(0, subscribers - 1)
    if (subscribers === 0) {
      stopPolling()
      stopEventStream()
    }
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
    // Kept as a presentation API alias; V2 UI no longer invokes V1 healing reads.
    heal: refresh,
  }
}

export async function forceRuntimeRefresh(runId = '') {
  const id = String(runId || '').trim()
  if (id) bindRun(id)
  return refresh()
}
