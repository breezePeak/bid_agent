import { PHASES } from '../config/stages.js'

const STATE_LABEL = {
  done: '完成',
  running: '执行中',
  ready: '就绪',
  blocked: '阻塞',
  error: '失败',
  failed: '失败',
  pending: '等待',
}

function el(html) {
  const t = document.createElement('template')
  t.innerHTML = html.trim()
  return t.content.firstElementChild
}

export function createHud(root, { onAction, onStageClick, onAgentClick }) {
  root.innerHTML = ''

  root.append(el('<div class="vignette"></div>'), el('<div class="scanline"></div>'))

  const top = el(`
    <div class="hud-top">
      <div class="brand">
        <div class="brand-title">Bid Command Bridge</div>
        <div class="brand-sub">标书全流程 3D 指挥舱 · Agent 实时态势</div>
      </div>
      <div class="top-metrics" id="top-metrics"></div>
    </div>
  `)

  const left = el(`
    <div class="hud-left">
      <div class="panel" style="flex:1.4">
        <div class="panel-header">
          <h3>Pipeline Stages</h3>
          <span class="hint" id="stage-progress-hint">0/0</span>
        </div>
        <div class="panel-body">
          <div class="phase-bar" id="phase-bar"></div>
          <div class="progress-track"><div class="progress-fill" id="progress-fill"></div></div>
          <div class="stage-list interactive" id="stage-list"></div>
        </div>
      </div>
      <div class="panel" style="flex:0.55">
        <div class="panel-header"><h3>Goal</h3></div>
        <div class="panel-body">
          <div class="goal-box" id="goal-box">暂无活动目标</div>
          <div class="stat-row"><span>工作区产物</span><b id="ws-stats">—</b></div>
          <div class="stat-row"><span>待补材料</span><b id="mat-stats">0</b></div>
          <div class="stat-row"><span>开放 Issues</span><b id="issue-stats">0</b></div>
        </div>
      </div>
    </div>
  `)

  const right = el(`
    <div class="hud-right">
      <div class="panel" style="flex:1.2">
        <div class="panel-header">
          <h3>Agent Fleet</h3>
          <span class="hint" id="agent-summary">待命</span>
        </div>
        <div class="panel-body">
          <div class="agent-grid interactive" id="agent-grid"></div>
        </div>
      </div>
      <div class="panel" style="flex:0.7">
        <div class="panel-header"><h3>Event Stream</h3></div>
        <div class="panel-body">
          <div class="event-list" id="event-list"></div>
        </div>
      </div>
    </div>
  `)

  const bottom = el(`
    <div class="hud-bottom">
      <div class="bottom-msg">
        <div class="title" id="bottom-title">SYSTEM READY</div>
        <div class="text" id="bottom-text">等待连接后端或启动演示模式</div>
      </div>
      <div class="legend">
        <span><i style="background:#22d3ee"></i>完成</span>
        <span><i style="background:#fbbf24"></i>执行</span>
        <span><i style="background:#60a5fa"></i>就绪</span>
        <span><i style="background:#f87171"></i>失败</span>
      </div>
      <div class="controls">
        <button class="btn" data-act="overview">总览</button>
        <button class="btn" data-act="active">聚焦当前</button>
        <button class="btn" data-act="agents">Agent 区</button>
        <button class="btn" data-act="orbit">自动环绕</button>
        <button class="btn primary" data-act="demo">演示模式</button>
        <button class="btn" data-act="live">实时连接</button>
      </div>
    </div>
  `)

  root.append(top, left, right, bottom)

  bottom.querySelectorAll('[data-act]').forEach((btn) => {
    btn.addEventListener('click', () => onAction?.(btn.dataset.act, btn))
  })

  // Event delegation — avoid rebinding on every render
  const stageList = document.getElementById('stage-list')
  stageList.addEventListener('click', (e) => {
    const row = e.target.closest('.stage-row')
    if (!row) return
    onStageClick?.(Number(row.dataset.index), row.dataset.id)
  })
  const agentGrid = document.getElementById('agent-grid')
  agentGrid.addEventListener('click', (e) => {
    const card = e.target.closest('.agent-card')
    if (!card) return
    onAgentClick?.(card.dataset.id)
  })

  let lastRenderSig = ''

  function render(snap, extras = {}) {
    // Skip identical HUD paint (demo can tick faster than needed)
    const sig = [
      snap.progress,
      snap.doneCount,
      snap.running,
      snap.demo,
      snap.connected,
      snap.currentTask,
      snap.runState?.message,
      snap.activity?.phase_label,
      (snap.agents || []).map((a) => a.id + a.status).join(','),
      (snap.stages || []).map((s) => s.state).join(''),
      extras.activeButtons?.orbit,
      extras.activeButtons?.demo,
      extras.activeButtons?.live,
    ].join('|')
    if (sig === lastRenderSig) return
    lastRenderSig = sig

    const liveClass = snap.demo ? 'live-pill demo' : snap.running ? 'live-pill on' : 'live-pill'
    const liveText = snap.demo ? 'DEMO' : snap.connected ? (snap.running ? 'LIVE' : 'IDLE') : 'OFFLINE'
    document.getElementById('top-metrics').innerHTML = `
      <div class="${liveClass}"><span class="dot"></span>${liveText}</div>
      <div class="metric"><div class="label">进度</div><div class="value">${Math.round((snap.progress || 0) * 100)}%</div></div>
      <div class="metric"><div class="label">阶段</div><div class="value gold">${snap.doneCount}/${snap.totalCount}</div></div>
      <div class="metric"><div class="label">Agent 工作</div><div class="value green">${snap.activity?.summary?.running || 0}</div></div>
      <div class="metric"><div class="label">排队</div><div class="value">${snap.activity?.summary?.queued || 0}</div></div>
      <div class="metric"><div class="label">失败</div><div class="value red">${snap.activity?.summary?.failed || 0}</div></div>
      <div class="metric"><div class="label">Run</div><div class="value pink" style="font-size:12px">${snap.runName || snap.runId || '—'}</div></div>
    `

    document.getElementById('phase-bar').innerHTML = (snap.phases || PHASES)
      .map((p) => {
        const pct = Math.round((p.progress || 0) * 100)
        return `<div class="phase-chip" style="border-color:${p.color}55">
        <span>${p.label}</span>
        <span class="pct" style="color:${p.color}">${pct}%</span>
      </div>`
      })
      .join('')

    document.getElementById('stage-progress-hint').textContent = `${snap.doneCount}/${snap.totalCount}`
    document.getElementById('progress-fill').style.width = `${Math.round((snap.progress || 0) * 100)}%`

    stageList.innerHTML = (snap.stages || [])
      .map((s) => {
        const st = s.state || 'pending'
        return `<div class="stage-row${st === 'running' ? ' active' : ''}${st === 'done' ? ' done' : ''}" data-index="${s.index}" data-id="${s.id}">
          <span class="stage-idx">${String(s.index + 1).padStart(2, '0')}</span>
          <span class="stage-name">${s.label}</span>
          <span class="stage-state state-${st}">${STATE_LABEL[st] || st}</span>
        </div>`
      })
      .join('')

    const agents = snap.agents || []
    const summary = snap.activity?.summary || {}
    document.getElementById('agent-summary').textContent =
      snap.activity?.phase_label ||
      `${summary.running || 0} 工作 · ${summary.queued || 0} 排队 · ${summary.done || 0} 完成`

    agentGrid.innerHTML = agents.length
      ? agents
          .slice(0, 16)
          .map((a) => {
            const st = a.status || 'idle'
            return `<div class="agent-card ${st}" data-id="${a.id}">
            <div class="agent-avatar">${a.emoji || '🤖'}</div>
            <div class="agent-meta">
              <div class="role">${a.label || a.role}
                <span class="status-badge state-${st}">${STATE_LABEL[st] || st}</span>
              </div>
              <div class="sub">${a.chapter_id ? `章节 ${a.chapter_id}` : a.role}</div>
              <div class="msg">${a.message || '—'}</div>
            </div>
          </div>`
          })
          .join('')
      : `<div class="event-item">当前无子 Agent 活动。主 Agent 值班中。</div>`

    const events = snap.events || []
    document.getElementById('event-list').innerHTML = events.length
      ? events
          .slice(0, 8)
          .map(
            (e) => `<div class="event-item">
          <div class="ts">${(e.ts || '').replace('T', ' ').slice(0, 19)} · ${e.stage || ''}</div>
          <div>${e.message || ''}</div>
        </div>`,
          )
          .join('')
      : `<div class="event-item">暂无事件</div>`

    document.getElementById('goal-box').textContent =
      snap.goalSummary ||
      (snap.goal?.raw_user_goal ? String(snap.goal.raw_user_goal) : '暂无活动目标 — 可在主前端通过对话设定 Goal')
    const ws = snap.workspace || {}
    document.getElementById('ws-stats').textContent =
      `章 ${ws.chapters_count || 0} · 审 ${ws.reviews_count || 0} · 上下文 ${ws.contexts_count || 0}`
    document.getElementById('mat-stats').textContent = String(snap.materialsDeferred || 0)
    document.getElementById('issue-stats').textContent = String(snap.issuesOpen || 0)

    const title = snap.demo
      ? 'DEMO SIMULATION'
      : snap.running
        ? 'PIPELINE RUNNING'
        : snap.connected
          ? 'STANDBY'
          : 'OFFLINE / DEMO READY'
    document.getElementById('bottom-title').textContent = title
    document.getElementById('bottom-text').textContent =
      snap.runState?.message ||
      (snap.currentTask ? `当前任务：${snap.currentTask}` : '') ||
      (snap.connected ? '后端已连接，等待流水线启动' : '后端未连接 — 点击「演示模式」预览全流程')

    bottom.querySelectorAll('[data-act]').forEach((btn) => {
      btn.classList.toggle('active', Boolean(extras.activeButtons?.[btn.dataset.act]))
    })
  }

  return { render, root }
}
