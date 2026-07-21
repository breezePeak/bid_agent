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

// 确保 HUD / 底栏永远在 3D 标签层之上
if (hudRoot) {
  hudRoot.style.zIndex = '20'
  hudRoot.style.position = 'absolute'
  hudRoot.style.inset = '0'
}
const viewBar = document.getElementById('view-bar')
if (viewBar) {
  viewBar.style.zIndex = '100'
  viewBar.style.pointerEvents = 'auto'
}

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
let orbitOn = false
let runs = []
let activeRunId = ''
let switchingWorkspace = false
const buttonState = { orbit: false, demo: false, live: false }

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

function stopAutoOrbit() {
  orbitOn = false
  app.autoOrbit = false
  app.stopCameraTour?.()
  buttonState.orbit = false
}

const VIEW_ACTIONS = {
  overview: () => app.focusOverview(),
  front: () => app.focusFront(),
  hall: () => app.focusHall(),
  furnace: () => app.focusFurnace(),
  side: () => app.focusSide(),
  top: () => app.focusTop(),
  back: () => app.focusBack(),
  active: () => app.focusActive(),
  agents: () => app.focusAgents(),
  'zoom-in': () => app.zoomBy(0.78),
  'zoom-out': () => app.zoomBy(1.28),
}

function handleHudAction(act) {
  if (!act || !app) return
  console.info('[3d] action', act)
  try {
    if (act === 'mute') {
      const muted = app.audio?.toggleMute?.()
      buttonState.mute = !!muted
      const muteBtn = document.querySelector('[data-act="mute"]')
      if (muteBtn) {
        muteBtn.textContent = muted ? '开声' : '静音'
        muteBtn.classList.toggle('active', muted)
      }
      scheduleUi()
      return
    }
    app.audio?.playUiTap?.()
    if (VIEW_ACTIONS[act]) {
      stopAutoOrbit()
      VIEW_ACTIONS[act]()
      scheduleUi()
      return
    }
    if (act === 'orbit') {
      orbitOn = !orbitOn
      buttonState.orbit = orbitOn
      if (orbitOn) {
        app.startCameraTour?.()
      } else {
        app.stopCameraTour?.()
      }
      scheduleUi()
      return
    }
    if (act === 'demo') startDemo()
    if (act === 'live') startLive()
    if (act === 'refresh-runs') loadRuns({ force: true })
    if (act === 'wave') {
      // 强制播放水平灵力波；俯视才能看清圆环扩张
      app.danFx?.debugPulse?.()
      app.audio?.playSpiritWave?.()
      app.focusTop?.()
    }
  } catch (err) {
    console.error('[3d] action failed', act, err)
  }
}

const hud = createHud(hudRoot, {
  onAction: handleHudAction,
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

// 底栏按钮：捕获阶段绑定到 document，确保一定能点到
function wireViewBar() {
  const bar = document.getElementById('view-bar')
  if (!bar) {
    console.warn('[3d] #view-bar missing')
    return
  }
  bar.style.zIndex = '9999'
  bar.style.pointerEvents = 'auto'
  bar.style.position = 'absolute'

  // pointerup+click 会各触发一次 → 环游开关被立刻关掉；用防抖保证只处理一次
  let lastActKey = ''
  let lastActAt = 0
  const onBarClick = (e) => {
    const btn = e.target.closest?.('[data-act]')
    if (!btn || !bar.contains(btn)) return
    e.preventDefault()
    e.stopPropagation()
    const act = btn.getAttribute('data-act')
    if (!act) return
    const now = performance.now()
    const key = `${act}`
    if (key === lastActKey && now - lastActAt < 350) return
    lastActKey = key
    lastActAt = now
    // 点击反馈
    btn.classList.add('active')
    setTimeout(() => {
      if (act !== 'orbit' || !buttonState.orbit) btn.classList.remove('active')
      if (act === 'orbit' && buttonState.orbit) btn.classList.add('active')
    }, 180)
    handleHudAction(act)
  }
  bar.addEventListener('click', onBarClick, true)
  bar.addEventListener('pointerdown', (e) => {
    e.stopPropagation()
  }, true)
  console.info('[3d] view-bar wired, buttons=', bar.querySelectorAll('[data-act]').length)
}
wireViewBar()

// 结丹毛笔字
const calliEl = document.getElementById('calligraphy')
const calliMain = document.getElementById('calli-main')
const calliSub = document.getElementById('calli-sub')
const calliActions = document.getElementById('calli-actions')
const btnReforge = document.getElementById('btn-reforge')
const btnDismissCalli = document.getElementById('btn-dismiss-calli')
let calliTimer = null
let calliPinned = false

function hideCalligraphy() {
  if (!calliEl) return
  clearTimeout(calliTimer)
  calliPinned = false
  calliEl.classList.remove('show', 'pinned')
  if (calliActions) calliActions.hidden = true
  const bookTitle = document.getElementById('book-title')
  if (bookTitle) bookTitle.hidden = true
  setTimeout(() => calliEl.classList.add('hidden'), 280)
}

function showCalligraphy(mainText, subText = '', { pin = false } = {}) {
  if (!calliEl) return
  clearTimeout(calliTimer)
  calliPinned = pin
  calliEl.classList.remove('hidden', 'show', 'pinned')
  calliMain.textContent = mainText
  calliSub.textContent = subText
  // 长文案略缩小，默认更大更醒目
  const len = String(mainText || '').length
  if (len >= 8) calliMain.style.fontSize = 'clamp(40px, 5vw, 72px)'
  else if (len >= 5) calliMain.style.fontSize = 'clamp(48px, 5.8vw, 84px)'
  else calliMain.style.fontSize = 'clamp(56px, 6.5vw, 96px)'
  calliMain.style.letterSpacing = len >= 6 ? '0.14em' : '0.2em'
  // 单步成功绝不显示按钮；仅 pin（全流程完成）时显示
  if (calliActions) {
    calliActions.hidden = !pin
    calliActions.style.display = pin ? '' : 'none'
  }
  void calliEl.offsetWidth
  calliEl.classList.add('show')
  if (pin) {
    calliEl.classList.add('pinned')
  } else {
    calliTimer = setTimeout(() => {
      calliEl.classList.remove('show')
      setTimeout(() => calliEl.classList.add('hidden'), 300)
    }, 2600)
  }
}

btnReforge?.addEventListener('click', () => {
  hideCalligraphy()
  // 重新炼制 = 重启演示
  app.stageTrack?.setOrbitMode?.(false)
  app.danFx?.clearFinale?.()
  app._completedIds?.clear?.()
  app._allDoneFired = false
  app._finaleBookShown = false
  startDemo()
})
btnDismissCalli?.addEventListener('click', () => {
  hideCalligraphy()
})

app.onStageComplete = (d) => {
  // 按钮仅全流程完成后出现；单步只显示「工序名 + 成功」
  if (d?.all) {
    // 旋合过程中先提示，标书出现后再 pin
    showCalligraphy('大道将成', '节点聚灵 · 标书成形', { pin: false })
    return
  }
  const name = (d?.label || '工序').replace(/成功$/, '')
  showCalligraphy(`${name}成功`, '', { pin: false })
}

app.onFinaleBook = () => {
  showCalligraphy('标书已成', '黄金成卷 · 可重新炼制', { pin: true })
  const bookTitle = document.getElementById('book-title')
  if (bookTitle) {
    bookTitle.hidden = false
    bookTitle.textContent = '标 书'
  }
}

app.onReforgeReset = () => {
  if (calliPinned) hideCalligraphy()
}

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
  if (!runId) return
  // Selecting a workspace always switches to live realtime view
  if (runId === activeRunId && mode === 'live') return
  switchingWorkspace = true
  scheduleUi()
  try {
    if (runId !== activeRunId) {
      await selectRun(runId)
      activeRunId = runId
    }
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
    const tag = (e.target && e.target.tagName) || ''
    if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return

    const stopOrbit = () => {
      orbitOn = false
      app.autoOrbit = false
      app.stopCameraTour?.()
      buttonState.orbit = false
    }

    if (e.key === '1') {
      stopOrbit()
      app.focusOverview()
      scheduleUi()
    }
    if (e.key === '2') {
      stopOrbit()
      app.focusActive()
      scheduleUi()
    }
    if (e.key === '3') {
      stopOrbit()
      app.focusAgents()
      scheduleUi()
    }
    if (e.key === '4') {
      stopOrbit()
      app.focusHall()
      scheduleUi()
    }
    if (e.key === '5') {
      stopOrbit()
      app.focusFurnace()
      scheduleUi()
    }
    if (e.key === '6') {
      stopOrbit()
      app.focusSide()
      scheduleUi()
    }
    if (e.key === '7') {
      stopOrbit()
      app.focusTop()
      scheduleUi()
    }
    if (e.key === '8') {
      stopOrbit()
      app.focusBack()
      scheduleUi()
    }
    if (e.key === '9') {
      stopOrbit()
      app.focusFront()
      scheduleUi()
    }
    if (e.key === '=' || e.key === '+') {
      stopOrbit()
      app.zoomBy(0.78)
      scheduleUi()
    }
    if (e.key === '-' || e.key === '_') {
      stopOrbit()
      app.zoomBy(1.28)
      scheduleUi()
    }
    if (e.key === 'd' || e.key === 'D') startDemo()
    if (e.key === 'l' || e.key === 'L') startLive()
    if (e.key === ' ') {
      e.preventDefault()
      orbitOn = !orbitOn
      buttonState.orbit = orbitOn
      if (orbitOn) app.startCameraTour?.()
      else app.stopCameraTour?.()
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
