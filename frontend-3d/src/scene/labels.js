import { CSS2DObject } from 'three/addons/renderers/CSS2DRenderer.js'

/** 宇宙唐风标签 */

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
      padding: 5px 9px; border-radius: 4px;
      background: rgba(10, 14, 32, 0.92);
      border: 1px solid rgba(160, 140, 255, 0.4);
      box-shadow: 0 4px 14px rgba(0,0,0,0.35);
      font-family: 'Noto Serif SC', 'Songti SC', serif;
      font-size: 12px; font-weight: 600; color: #e8ecf8;
      white-space: nowrap;
    }
    .css2d-idx { font-size: 11px; color: #7a84a0; letter-spacing: 0.06em; font-weight: 700; }
    .css2d-name { font-weight: 700; max-width: 110px; overflow: hidden; text-overflow: ellipsis; }
    .css2d-state {
      font-size: 10px; font-weight: 700; padding: 1px 5px; border-radius: 2px;
      border: 1px solid #7a84a0; color: #7a84a0;
    }
    .css2d-card.is-done { border-color: rgba(74,208,160,.55); }
    .css2d-card.is-done .css2d-state { color: #6ae8b8; border-color: #4ad0a0; }
    .css2d-card.is-running { border-color: rgba(255,138,64,.65); }
    .css2d-card.is-running .css2d-state { color: #ff8a40; border-color: #ff8a40; }
    .css2d-card.is-ready .css2d-state { color: #e0b84a; border-color: #e0b84a; }
    .css2d-card.is-error .css2d-state, .css2d-card.is-failed .css2d-state { color: #ff6068; border-color: #ff6068; }
    .css2d-card.is-pending { opacity: 0.8; }
    .css2d-agent {
      display: flex; align-items: center; gap: 3px;
      padding: 3px 8px; border-radius: 4px;
      background: rgba(10, 14, 32, 0.92);
      border: 1px solid rgba(224,184,74,.45);
      box-shadow: 0 3px 10px rgba(0,0,0,0.3);
      font-family: 'Noto Serif SC', serif;
      font-size: 11px; font-weight: 600; color: #e8ecf8; white-space: nowrap;
    }
    .css2d-boss {
      padding: 6px 12px; border-radius: 4px;
      background: rgba(40, 20, 16, 0.94);
      border: 1px solid rgba(224,80,64,.5);
      box-shadow: 0 4px 16px rgba(0,0,0,0.4);
      font-family: 'Noto Serif SC', serif;
      font-size: 13px; letter-spacing: .12em; color: #e0b84a; font-weight: 700;
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
      <span class="css2d-state" data-state>静候</span>
    </div>
  `
  el.style.display = 'none'
  const obj = new CSS2DObject(el)
  obj.position.set(0, 1.0, 0)
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
      <span class="css2d-emoji">${agent.emoji || '☯'}</span>
      <span class="css2d-aname">${agent.chapter_id ? `丹章 ${agent.chapter_id}` : agent.label || agent.role}</span>
    </div>
  `
  const obj = new CSS2DObject(el)
  obj.position.set(0, 1.05, 0)
  obj.userData = { el, agentId: agent.id }
  return obj
}

export function createBossLabel(text = '掌炉真人 · 殿内控炉') {
  ensureStyles()
  const el = document.createElement('div')
  el.className = 'css2d-label boss-label'
  el.innerHTML = `<div class="css2d-boss">${text}</div>`
  const obj = new CSS2DObject(el)
  obj.position.set(0, 2.0, 0)
  return obj
}
