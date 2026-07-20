import { CSS2DObject } from 'three/addons/renderers/CSS2DRenderer.js'

export function createStageLabel(stage, index) {
  const el = document.createElement('div')
  el.className = 'css2d-label stage-label'
  el.innerHTML = `
    <div class="css2d-card">
      <span class="css2d-idx">${String(index + 1).padStart(2, '0')}</span>
      <span class="css2d-name">${stage.label}</span>
      <span class="css2d-state" data-state>pending</span>
    </div>
  `
  const obj = new CSS2DObject(el)
  obj.position.set(0, 1.55, 0)
  obj.userData = { el, stageId: stage.id }
  return obj
}

export function createAgentLabel(agent) {
  const el = document.createElement('div')
  el.className = 'css2d-label agent-label'
  el.innerHTML = `
    <div class="css2d-agent">
      <span class="css2d-emoji">${agent.emoji || '🤖'}</span>
      <span class="css2d-aname">${agent.chapter_id ? `章 ${agent.chapter_id}` : agent.label || agent.role}</span>
    </div>
  `
  const obj = new CSS2DObject(el)
  obj.position.set(0, 1.15, 0)
  obj.userData = { el, agentId: agent.id }
  return obj
}

export function createBossLabel(text = '主 Agent · 统筹中枢') {
  const el = document.createElement('div')
  el.className = 'css2d-label boss-label'
  el.innerHTML = `<div class="css2d-boss">${text}</div>`
  const obj = new CSS2DObject(el)
  obj.position.set(0, 2.4, 0)
  return obj
}

// Inject CSS2D styles once
const STYLE_ID = 'css2d-label-styles'
if (typeof document !== 'undefined' && !document.getElementById(STYLE_ID)) {
  const style = document.createElement('style')
  style.id = STYLE_ID
  style.textContent = `
    .css2d-label { pointer-events: none; user-select: none; }
    .css2d-card {
      display: flex; align-items: center; gap: 6px;
      padding: 4px 8px; border-radius: 8px;
      background: rgba(2, 6, 23, 0.78);
      border: 1px solid rgba(148, 163, 184, 0.25);
      backdrop-filter: blur(8px);
      font-family: 'Noto Sans SC', system-ui, sans-serif;
      font-size: 11px; color: #e2e8f0;
      white-space: nowrap;
      transform: translateY(-4px);
      transition: border-color .2s, box-shadow .2s, opacity .2s;
    }
    .css2d-idx {
      font-family: Orbitron, monospace; font-size: 10px; color: #64748b;
    }
    .css2d-name { font-weight: 500; max-width: 110px; overflow: hidden; text-overflow: ellipsis; }
    .css2d-state {
      font-size: 9px; letter-spacing: .04em; padding: 1px 5px;
      border-radius: 999px; border: 1px solid #64748b; color: #64748b;
      text-transform: uppercase;
    }
    .css2d-card.is-done { border-color: rgba(34,211,238,.45); box-shadow: 0 0 12px rgba(34,211,238,.2); }
    .css2d-card.is-done .css2d-state { color: #22d3ee; border-color: #22d3ee; }
    .css2d-card.is-running { border-color: rgba(251,191,36,.55); box-shadow: 0 0 16px rgba(251,191,36,.28); }
    .css2d-card.is-running .css2d-state { color: #fbbf24; border-color: #fbbf24; }
    .css2d-card.is-ready .css2d-state { color: #60a5fa; border-color: #60a5fa; }
    .css2d-card.is-error, .css2d-card.is-failed { border-color: rgba(248,113,113,.55); }
    .css2d-card.is-error .css2d-state, .css2d-card.is-failed .css2d-state { color: #f87171; border-color: #f87171; }
    .css2d-card.is-blocked .css2d-state { color: #fb923c; border-color: #fb923c; }
    .css2d-card.is-pending { opacity: 0.55; }
    .css2d-agent {
      display: flex; align-items: center; gap: 4px;
      padding: 3px 7px; border-radius: 999px;
      background: rgba(15,23,42,.82); border: 1px solid rgba(167,139,250,.35);
      font-size: 10px; color: #e2e8f0; white-space: nowrap;
    }
    .css2d-boss {
      padding: 6px 12px; border-radius: 10px;
      background: linear-gradient(135deg, rgba(99,102,241,.35), rgba(34,211,238,.2));
      border: 1px solid rgba(129,140,248,.5);
      font-family: Orbitron, 'Noto Sans SC', sans-serif;
      font-size: 11px; letter-spacing: .08em; color: #c7d2fe;
      box-shadow: 0 0 20px rgba(99,102,241,.25);
    }
  `
  document.head.appendChild(style)
}
