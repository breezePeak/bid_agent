import { CSS2DObject } from 'three/addons/renderers/CSS2DRenderer.js'

/**
 * Minimal CSS2D labels — NO backdrop-filter (major freeze source on many GPUs).
 * Only create labels when needed; stages mostly rely on HUD list.
 */

let stylesInjected = false
function ensureStyles() {
  if (stylesInjected || typeof document === 'undefined') return
  stylesInjected = true
  const style = document.createElement('style')
  style.id = 'css2d-label-styles'
  style.textContent = `
    .css2d-label { pointer-events: none; user-select: none; will-change: transform; }
    .css2d-card {
      display: flex; align-items: center; gap: 5px;
      padding: 3px 7px; border-radius: 6px;
      background: rgba(2, 6, 23, 0.88);
      border: 1px solid rgba(148, 163, 184, 0.3);
      font-family: 'Noto Sans SC', system-ui, sans-serif;
      font-size: 11px; color: #e2e8f0;
      white-space: nowrap;
    }
    .css2d-idx { font-family: Orbitron, monospace; font-size: 10px; color: #64748b; }
    .css2d-name { font-weight: 500; max-width: 100px; overflow: hidden; text-overflow: ellipsis; }
    .css2d-state {
      font-size: 9px; padding: 1px 5px; border-radius: 999px;
      border: 1px solid #64748b; color: #64748b;
    }
    .css2d-card.is-done { border-color: rgba(34,211,238,.5); }
    .css2d-card.is-done .css2d-state { color: #22d3ee; border-color: #22d3ee; }
    .css2d-card.is-running { border-color: rgba(251,191,36,.6); }
    .css2d-card.is-running .css2d-state { color: #fbbf24; border-color: #fbbf24; }
    .css2d-card.is-ready .css2d-state { color: #60a5fa; border-color: #60a5fa; }
    .css2d-card.is-error .css2d-state, .css2d-card.is-failed .css2d-state { color: #f87171; border-color: #f87171; }
    .css2d-card.is-pending { opacity: 0.5; }
    .css2d-agent {
      display: flex; align-items: center; gap: 3px;
      padding: 2px 6px; border-radius: 999px;
      background: rgba(15,23,42,.9); border: 1px solid rgba(167,139,250,.4);
      font-size: 10px; color: #e2e8f0; white-space: nowrap;
    }
    .css2d-boss {
      padding: 5px 10px; border-radius: 8px;
      background: rgba(49, 46, 129, 0.85);
      border: 1px solid rgba(129,140,248,.5);
      font-family: Orbitron, 'Noto Sans SC', sans-serif;
      font-size: 11px; letter-spacing: .06em; color: #c7d2fe;
    }
  `
  document.head.appendChild(style)
}

export function createStageLabel(stage, index) {
  ensureStyles()
  const el = document.createElement('div')
  el.className = 'css2d-label stage-label'
  el.innerHTML = `
    <div class="css2d-card is-pending">
      <span class="css2d-idx">${String(index + 1).padStart(2, '0')}</span>
      <span class="css2d-name">${stage.label}</span>
      <span class="css2d-state" data-state>等待</span>
    </div>
  `
  // Hidden by default — only show for active / nearby stages (perf)
  el.style.display = 'none'
  const obj = new CSS2DObject(el)
  obj.position.set(0, 1.45, 0)
  obj.userData = { el, stageId: stage.id }
  return obj
}

export function setStageLabelVisible(label, visible) {
  if (label?.userData?.el) {
    label.userData.el.style.display = visible ? '' : 'none'
  }
}

export function createAgentLabel(agent) {
  ensureStyles()
  const el = document.createElement('div')
  el.className = 'css2d-label agent-label'
  el.innerHTML = `
    <div class="css2d-agent">
      <span class="css2d-emoji">${agent.emoji || '🤖'}</span>
      <span class="css2d-aname">${agent.chapter_id ? `章 ${agent.chapter_id}` : agent.label || agent.role}</span>
    </div>
  `
  const obj = new CSS2DObject(el)
  obj.position.set(0, 1.05, 0)
  obj.userData = { el, agentId: agent.id }
  return obj
}

export function createBossLabel(text = '主 Agent · 统筹中枢') {
  ensureStyles()
  const el = document.createElement('div')
  el.className = 'css2d-label boss-label'
  el.innerHTML = `<div class="css2d-boss">${text}</div>`
  const obj = new CSS2DObject(el)
  obj.position.set(0, 2.2, 0)
  return obj
}
