import './style.css'
import { App3D } from './scene/App3D.js'
import { createStore } from './state/store.js'
import { createHud } from './ui/hud.js'
import { createDemoController } from './demo/mockData.js'
import { fetchStatus, fetchAgentActivity, fetchAgentGoal, probeBackend } from './api/client.js'

const canvas = document.getElementById('scene')
const hudRoot = document.getElementById('hud')
const tooltip = document.getElementById('tooltip')

const store = createStore()
let app
try {
  app = new App3D(canvas)
} catch (err) {
  console.error('[3d] init failed', err)
  hudRoot.innerHTML = `<div style="padding:24px;color:#f87171;font-family:sans-serif">
    <h2>3D 场景初始化失败</h2>
    <pre style="white-space:pre-wrap">${String(err?.stack || err)}</pre>
    <p>请确认浏览器支持 WebGL，并尝试关闭硬件加速后重试。</p>
  </div>`
  throw err
}

const demo = createDemoController(store)

let mode = 'auto'
let pollTimer = null
let orbitOn = true
const buttonState = { orbit: true, demo: false, live: false }

// Throttle HUD + scene apply to avoid main-thread storms
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
      hud.render(snap, { activeButtons: buttonState })
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
  },
  onStageClick(index) {
    app.focusStage(index)
  },
  onAgentClick() {
    app.focusAgents()
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
  scheduleUi()
})

function stopPoll() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
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
  scheduleUi()
}

function startDemo() {
  mode = 'demo'
  stopPoll()
  buttonState.demo = true
  buttonState.live = false
  // Slower demo ticks to reduce thrash
  demo.start(1400)
  scheduleUi()
}

async function bootstrap() {
  app.start()
  // First paint empty scene before heavy data mode
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

  // Prefer idle standby over auto-demo thrash when offline
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
    startLive()
  } else {
    console.info('[3d] backend offline → light demo')
    // Delay demo slightly so first frames stay smooth
    setTimeout(() => startDemo(), 400)
  }
}

bootstrap().catch((err) => {
  console.error('[3d] bootstrap failed', err)
})
