import './style.css'
import { App3D } from './scene/App3D.js'
import { createStore } from './state/store.js'
import { createHud } from './ui/hud.js'
import { createDemoController } from './demo/mockData.js'
import {
  fetchStatus,
  fetchAgentActivity,
  fetchAgentGoal,
  fetchRuns,
  selectRun,
  probeBackend,
} from './api/client.js'

const canvas = document.getElementById('scene')
const hudRoot = document.getElementById('hud')
const tooltip = document.getElementById('tooltip')

const store = createStore()
let app
try {
  app = new App3D(canvas)
} catch (err) {
  console.error('[3d] init failed', err)
  hudRoot.innerHTML = `<div style="padding:24px;color:#ff7b8a;font-family:sans-serif">
    <h2>3D 场景初始化失败</h2>
    <pre style="white-space:pre-wrap">${String(err?.stack || err)}</pre>
    <p>请确认浏览器支持 WebGL，并尝试关闭硬件加速后重试。</p>
  </div>`
  throw err
}

const demo = createDemoController(store)

let mode = 'auto'
let pollTimer = null
let runsTimer = null
let orbitOn = true
let runs = []
let activeRunId = ''
let switchingWorkspace = false
const buttonState = { orbit: true, demo: false, live: false }

let pendingSnap = null
let hudScheduled = false
function scheduleUi() {
  if (hudScheduled) return
  hudScheduled = true
  requestAnimationFrame(() => {
    hudScheduled = false
    const snap = pendingSnap || store.get()
    pendingSnap = null
    try {
      app.applySnapshot(snap)
      hud.render(snap, {
        activeButtons: buttonState,
        runs,
        activeRunId,
        switchingWorkspace,
      })
    } catch (err) {
      console.error('[3d] ui apply error', err)
    }
  })
}

const hud = createHud(hudRoot, {
  onAction(act) {
    if (act === 'overview') app.focusOverview()
    if (act === 'active') app.focusActive()
    if (act === 'agents') app.focusAgents()
    if (act === 'orbit') {
      orbitOn = !orbitOn
      app.autoOrbit = orbitOn
      buttonState.orbit = orbitOn
      scheduleUi()
    }
    if (act === 'demo') startDemo()
    if (act === 'live') startLive()
    if (act === 'refresh-runs') loadRuns({ force: true })
  },
  onStageClick(index) {
    app.focusStage(index)
  },
  onAgentClick() {
    app.focusAgents()
  },
  onSelectRun(runId) {
    handleSelectRun(runId)
  },
})

app.onPick = (data) => {
  if (data.type === 'stage') {
    const stage = store.get().stages?.[data.index]
    showTooltip(
      stage?.label || data.stageId,
      `状态：${stage?.state || '—'}<br/>${stage?.message || ''}<br/>命令：${stage?.command || ''}`,
    )
  }
  if (data.type === 'agent') {
    const agent = store.get().agents?.find((a) => a.id === data.agentId)
    showTooltip(
      agent?.label || data.agentId,
      `状态：${agent?.status || '—'}<br/>${agent?.message || ''}<br/>章节：${agent?.chapter_id || '—'}`,
    )
  }
}

function showTooltip(title, body) {
  tooltip.classList.remove('hidden')
  tooltip.innerHTML = `<div class="tt-title">${title}</div><div class="tt-body">${body}</div>`
  clearTimeout(showTooltip._t)
  showTooltip._t = setTimeout(() => tooltip.classList.add('hidden'), 2800)
}

store.subscribe((snap) => {
  pendingSnap = snap
  if (snap.runId && !snap.demo) activeRunId = snap.runId
  scheduleUi()
})

function stopPoll() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

async function loadRuns({ force = false } = {}) {
  try {
    const data = await fetchRuns()
    runs = Array.isArray(data?.runs) ? data.runs : []
    if (data?.active_run_id) {
      activeRunId = data.active_run_id
    } else if (!activeRunId && runs[0]) {
      activeRunId = runs[0].id
    }
    scheduleUi()
    return true
  } catch (err) {
    if (force) console.warn('[3d] load runs failed', err)
    if (mode !== 'demo') {
      runs = []
      scheduleUi()
    }
    return false
  }
}

async function handleSelectRun(runId) {
  if (!runId || runId === activeRunId) {
    if (mode !== 'live') startLive()
    return
  }
  switchingWorkspace = true
  scheduleUi()
  try {
    await selectRun(runId)
    activeRunId = runId
    if (mode !== 'live') startLive()
    else await pollOnce()
    await loadRuns()
  } catch (err) {
    console.warn('[3d] select run failed', err)
  } finally {
    switchingWorkspace = false
    scheduleUi()
  }
}

async function pollOnce() {
  try {
    const status = await fetchStatus()
    store.applyStatus(status, { connected: true, demo: false })
    try {
      const act = await fetchAgentActivity()
      if (act?.activity) store.applyActivity(act)
    } catch {
      /* optional */
    }
    try {
      const goal = await fetchAgentGoal()
      store.applyGoal(goal)
    } catch {
      /* optional */
    }
    return true
  } catch (err) {
    console.warn('[3d] poll failed', err)
    return false
  }
}

function startLive() {
  mode = 'live'
  demo.stop()
  buttonState.demo = false
  buttonState.live = true
  stopPoll()
  const tick = async () => {
    const ok = await pollOnce()
    if (!ok && mode === 'live') {
      buttonState.live = false
      scheduleUi()
    }
  }
  tick()
  pollTimer = setInterval(tick, 2500)
  loadRuns()
  scheduleUi()
}

function startDemo() {
  mode = 'demo'
  stopPoll()
  buttonState.demo = true
  buttonState.live = false
  demo.start(1400)
  scheduleUi()
}

async function bootstrap() {
  app.start()
  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))
  app.focusOverview()

  window.addEventListener('pointermove', (e) => {
    if (tooltip.classList.contains('hidden')) return
    tooltip.style.left = `${e.clientX}px`
    tooltip.style.top = `${e.clientY}px`
  })

  window.addEventListener('keydown', (e) => {
    if (e.key === '1') app.focusOverview()
    if (e.key === '2') app.focusActive()
    if (e.key === '3') app.focusAgents()
    if (e.key === 'd' || e.key === 'D') startDemo()
    if (e.key === 'l' || e.key === 'L') startLive()
    if (e.key === ' ') {
      e.preventDefault()
      orbitOn = !orbitOn
      app.autoOrbit = orbitOn
      buttonState.orbit = orbitOn
      scheduleUi()
    }
  })

  let online = false
  try {
    online = await Promise.race([
      probeBackend(),
      new Promise((resolve) => setTimeout(() => resolve(false), 1500)),
    ])
  } catch {
    online = false
  }

  if (online) {
    console.info('[3d] backend online → live mode')
    await loadRuns()
    startLive()
    // refresh workspace list periodically in live mode
    runsTimer = setInterval(() => {
      if (mode === 'live') loadRuns()
    }, 15000)
  } else {
    console.info('[3d] backend offline → demo')
    setTimeout(() => startDemo(), 400)
  }
}

bootstrap().catch((err) => {
  console.error('[3d] bootstrap failed', err)
})
