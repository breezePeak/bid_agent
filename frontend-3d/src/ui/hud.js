import { PHASES } from '../config/stages.js'

const STATE_LABEL = {
  done: '成丹',
  running: '炼制',
  ready: '候火',
  blocked: '滞火',
  error: '炸炉',
  failed: '炸炉',
  pending: '静候',
}

function el(html) {
  const t = document.createElement('template')
  t.innerHTML = html.trim()
  return t.content.firstElementChild
}

function shortRunName(run) {
  if (!run) return ''
  const raw = String(run.project_label || run.id || '')
  // runs often look like 20260325_HHMMSS_项目名
  const parts = raw.split('_')
  if (parts.length >= 3 && /^\d{8}/.test(parts[0])) {
    return parts.slice(2).join('_') || raw
  }
  return raw
}

function runProgressLabel(run) {
  const p = run?.progress
  if (!p) return ''
  const done = Number(p.done || 0)
  const total = Number(p.total || 0)
  if (total > 0) return `${done}/${total}`
  if (p.status_label) return String(p.status_label)
  return ''
}

export function createHud(root, { onAction, onStageClick, onAgentClick, onSelectRun }) {
  root.innerHTML = ''

  root.append(el('<div class="vignette"></div>'), el('<div class="scanline"></div>'))

  const top = el(`
    <div class="hud-top">
      <div class="brand">
        <div class="brand-title">炼丹阁</div>
        <div class="brand-sub">标书全流程 · 丹道周天</div>
      </div>
      <div class="top-center interactive">
        <div class="workspace-picker">
          <span class="ws-label">丹房</span>
          <select id="workspace-select" title="选择工作空间">
            <option value="">加载中…</option>
          </select>
        </div>
        <div class="mode-toggle">
          <button type="button" class="mode-btn live" data-act="live">观火</button>
          <button type="button" class="mode-btn demo" data-act="demo">演法</button>
        </div>
      </div>
      <div class="top-metrics" id="top-metrics"></div>
    </div>
  `)

  const left = el(`
    <div class="hud-left">
      <div class="panel" style="flex:1.4">
        <div class="panel-header">
          <h3>炼制工序</h3>
          <span class="hint" id="stage-progress-hint">0/0</span>
        </div>
        <div class="panel-body">
          <div class="phase-bar" id="phase-bar"></div>
          <div class="progress-track"><div class="progress-fill" id="progress-fill"></div></div>
          <div class="stage-list interactive" id="stage-list"></div>
        </div>
      </div>
      <div class="panel" style="flex:0.55">
        <div class="panel-header"><h3>丹愿</h3></div>
        <div class="panel-body">
          <div class="goal-box" id="goal-box">暂无丹愿</div>
          <div class="stat-row"><span>丹房产物</span><b id="ws-stats">—</b></div>
          <div class="stat-row"><span>待补药材</span><b id="mat-stats">0</b></div>
          <div class="stat-row"><span>未决疑难</span><b id="issue-stats">0</b></div>
        </div>
      </div>
    </div>
  `)

  const right = el(`
    <div class="hud-right">
      <div class="panel" style="flex:1.2">
        <div class="panel-header">
          <h3>丹房道众</h3>
          <span class="hint" id="agent-summary">静候</span>
        </div>
        <div class="panel-body">
          <div class="agent-grid interactive" id="agent-grid"></div>
        </div>
      </div>
      <div class="panel" style="flex:0.7">
        <div class="panel-header"><h3>丹录</h3></div>
        <div class="panel-body">
          <div class="event-list" id="event-list"></div>
        </div>
      </div>
    </div>
  `)

  const bottom = el(`
    <div class="hud-bottom">
      <div class="bottom-msg">
        <div class="title" id="bottom-title">炉火未启</div>
        <div class="text" id="bottom-text">静候观火，或启演法一窥丹道</div>
      </div>
      <div class="legend">
        <span><i style="background:#5fa88a;color:#5fa88a"></i>成丹</span>
        <span><i style="background:#ff8a3d;color:#ff8a3d"></i>炼制</span>
        <span><i style="background:#e0b44a;color:#e0b44a"></i>候火</span>
        <span><i style="background:#e05555;color:#e05555"></i>炸炉</span>
      </div>
      <div class="controls view-controls">
        <button class="btn" data-act="overview" title="1">总览</button>
        <button class="btn" data-act="front" title="殿前">殿前</button>
        <button class="btn" data-act="hall" title="4 殿内">殿内</button>
        <button class="btn" data-act="furnace" title="5 丹炉">丹炉</button>
        <button class="btn" data-act="agents" title="3 列队">列队</button>
        <button class="btn" data-act="active" title="2 工序">工序</button>
        <button class="btn" data-act="side" title="侧视">侧视</button>
        <button class="btn" data-act="top" title="俯视">俯视</button>
        <button class="btn" data-act="back" title="殿后">殿后</button>
        <button class="btn" data-act="zoom-in" title="滚轮也可">放大</button>
        <button class="btn" data-act="zoom-out" title="滚轮也可">缩小</button>
        <button class="btn" data-act="orbit" title="空格">环游</button>
        <button class="btn" data-act="refresh-runs">刷新</button>
      </div>
    </div>
  `)

  root.append(top, left, right, bottom)

  const wsSelect = document.getElementById('workspace-select')
  wsSelect.addEventListener('change', () => {
    const id = wsSelect.value
    if (id) onSelectRun?.(id)
  })

  root.querySelectorAll('[data-act]').forEach((btn) => {
    btn.addEventListener('click', () => onAction?.(btn.dataset.act, btn))
  })

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
  let lastRunsSig = ''

  function setRuns(runs, activeId, { offline = false, switching = false, demo = false } = {}) {
    const list = Array.isArray(runs) ? runs : []
    const sig = `${offline}|${switching}|${demo}|${activeId || ''}|${list.map((r) => `${r.id}:${r.progress?.done || 0}`).join(',')}`
    if (sig === lastRunsSig) return
    lastRunsSig = sig

    if (offline && !list.length) {
      wsSelect.disabled = true
      wsSelect.innerHTML = `<option value="">未接通</option>`
      return
    }

    if (!list.length) {
      wsSelect.disabled = true
      wsSelect.innerHTML = demo
        ? `<option value="">演法中 · 无真实丹房</option>`
        : `<option value="">暂无丹房</option>`
      return
    }

    wsSelect.disabled = switching
    const prefer = activeId || list.find((r) => r.active)?.id || list[0]?.id || ''
    wsSelect.innerHTML = list
      .map((r) => {
        const name = shortRunName(r)
        const prog = runProgressLabel(r)
        const tag = r.progress?.status === 'running' ? ' ▶' : ''
        const label = prog ? `${name} · ${prog}${tag}` : `${name}${tag}`
        return `<option value="${r.id}">${label}</option>`
      })
      .join('')
    if (prefer) wsSelect.value = prefer
  }

  function render(snap, extras = {}) {
    const runs = extras.runs || []
    const sig = [
      snap.progress,
      snap.doneCount,
      snap.running,
      snap.demo,
      snap.connected,
      snap.currentTask,
      snap.runId,
      snap.runState?.message,
      snap.activity?.phase_label,
      (snap.agents || []).map((a) => a.id + a.status).join(','),
      (snap.stages || []).map((s) => s.state).join(''),
      extras.activeButtons?.orbit,
      extras.activeButtons?.demo,
      extras.activeButtons?.live,
      extras.switchingWorkspace,
      runs.map((r) => r.id).join(','),
    ].join('|')
    if (sig === lastRenderSig) return
    lastRenderSig = sig

    setRuns(runs, extras.activeRunId || snap.runId, {
      offline: !snap.connected && !snap.demo,
      switching: Boolean(extras.switchingWorkspace),
      demo: Boolean(snap.demo),
    })

    const liveClass = snap.demo ? 'live-pill demo' : snap.running ? 'live-pill on' : 'live-pill'
    const liveText = snap.demo ? '演法' : snap.connected ? (snap.running ? '炉开' : '静候') : '离线'
    document.getElementById('top-metrics').innerHTML = `
      <div class="${liveClass}"><span class="dot"></span>${liveText}</div>
      <div class="metric"><div class="label">火候</div><div class="value">${Math.round((snap.progress || 0) * 100)}%</div></div>
      <div class="metric"><div class="label">工序</div><div class="value gold">${snap.doneCount}/${snap.totalCount}</div></div>
      <div class="metric"><div class="label">炼丹中</div><div class="value green">${snap.activity?.summary?.running || 0}</div></div>
      <div class="metric"><div class="label">候火</div><div class="value">${snap.activity?.summary?.queued || 0}</div></div>
      <div class="metric"><div class="label">炸炉</div><div class="value red">${snap.activity?.summary?.failed || 0}</div></div>
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
      `${summary.running || 0} 炼 · ${summary.queued || 0} 候 · ${summary.done || 0} 成`

    agentGrid.innerHTML = agents.length
      ? agents
          .slice(0, 16)
          .map((a) => {
            const st = a.status || 'idle'
            return `<div class="agent-card ${st}" data-id="${a.id}">
            <div class="agent-avatar">${a.emoji || '☯'}</div>
            <div class="agent-meta">
              <div class="role">${a.label || a.role}
                <span class="status-badge state-${st}">${STATE_LABEL[st] || st}</span>
              </div>
              <div class="sub">${a.chapter_id ? `丹章 ${a.chapter_id}` : a.role}</div>
              <div class="msg">${a.message || '—'}</div>
            </div>
          </div>`
          })
          .join('')
      : `<div class="event-item">道众未动。掌炉真人静守炉前。</div>`

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
      : `<div class="event-item">丹录空空</div>`

    document.getElementById('goal-box').textContent =
      snap.goalSummary ||
      (snap.goal?.raw_user_goal ? String(snap.goal.raw_user_goal) : '暂无丹愿 — 可在主前端立愿')
    const ws = snap.workspace || {}
    document.getElementById('ws-stats').textContent =
      `章 ${ws.chapters_count || 0} · 审 ${ws.reviews_count || 0} · 药 ${ws.contexts_count || 0}`
    document.getElementById('mat-stats').textContent = String(snap.materialsDeferred || 0)
    document.getElementById('issue-stats').textContent = String(snap.issuesOpen || 0)

    const title = snap.demo
      ? '演法中'
      : snap.running
        ? '炉火正旺'
        : snap.connected
          ? '炉火静候'
          : '离线 · 可演法'
    document.getElementById('bottom-title').textContent = title
    const runHint = snap.runName || snap.runId
    document.getElementById('bottom-text').textContent =
      snap.runState?.message ||
      (snap.currentTask ? `当前工序：${snap.currentTask}` : '') ||
      (snap.demo
        ? '演法运行中 — 可切换「观火」并选择丹房'
        : snap.connected
          ? runHint
            ? `丹房：${runHint} · 静候炉动`
            : '已接通 — 请择丹房'
          : '未接通后端 — 可启演法一窥')

    root.querySelectorAll('[data-act]').forEach((btn) => {
      const act = btn.dataset.act
      if (act === 'live' || act === 'demo') {
        btn.classList.toggle('active', Boolean(extras.activeButtons?.[act]))
        btn.classList.toggle('live', act === 'live')
        btn.classList.toggle('demo', act === 'demo')
      } else {
        btn.classList.toggle('active', Boolean(extras.activeButtons?.[act]))
      }
    })
  }

  return { render, root, setRuns }
}
