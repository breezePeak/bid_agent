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
const app = new App3D(canvas)
const demo = createDemoController(store)

let mode = 'auto' // auto | live | demo
let pollTimer = null
let orbitOn = true
const buttonState = { orbit: true, demo: false, live: false }

const hud = createHud(hudRoot, {
  onAction(act, btn) {
    if (act === 'overview') app.focusOverview()
    if (act === 'active') app.focusActive()
    if (act === 'agents') app.focusAgents()
    if (act === 'orbit') {
      orbitOn = !orbitOn
      app.autoOrbit = orbitOn
      buttonState.orbit = orbitOn
      refreshHud()
    }
    if (act === 'demo') {
      startDemo()
    }
    if (act === 'live') {
      startLive()
    }
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
    const snap = store.get()
    const stage = snap.stages?.[data.index]
    showTooltip(
      stage?.label || data.stageId,
      `状态：${stage?.state || '—'}\\n${stage?.message || ''}\\n命令：${stage?.command || ''}`,
    )
  }
  if (data.type === 'agent') {
    const agent = store.get().agents?.find((a) => a.id === data.agentId)
    showTooltip(
      agent?.label || data.agentId,
      `状态：${agent?.status || '—'}\\n${agent?.message || ''}\\n章节：${agent?.chapter_id || '—'}`,
    )
  }
}

function showTooltip(title, body) {
  tooltip.classList.remove('hidden')
  tooltip.innerHTML = `<div class="tt-title">${title}</div><div class="tt-body">${body.replace(/\\n/g, '<br/>')}</div>`
  clearTimeout(showTooltip._t)
  showTooltip._t = setTimeout(() => tooltip.classList.add('hidden'), 3200)
}

function refreshHud() {
  hud.render(store.get(), { activeButtons: buttonState })
}

store.subscribe((snap) => {
  app.applySnapshot(snap)
  refreshHud()
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
      refreshHud()
    }
  }
  tick()
  pollTimer = setInterval(tick, 2000)
  refreshHud()
}

function startDemo() {
  mode = 'demo'
  stopPoll()
  buttonState.demo = true
  buttonState.live = false
  demo.start(850)
  refreshHud()
}

async function bootstrap() {
  app.start()
  app.focusOverview()

  // hover tooltip position
  window.addEventListener('pointermove', (e) => {
    if (tooltip.classList.contains('hidden')) return
    tooltip.style.left = `${e.clientX}px`
    tooltip.style.top = `${e.clientY}px`
  })

  // keyboard shortcuts
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
      refreshHud()
    }
  })

  const online = await probeBackend()
  if (online) {
    console.info('[3d] backend online → live mode')
    startLive()
  } else {
    console.info('[3d] backend offline → demo mode')
    startDemo()
  }
}

bootstrap()
