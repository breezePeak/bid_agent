import { CSS2DObject } from 'three/addons/renderers/CSS2DRenderer.js'

/** 宇宙唐风标签 */

let stylesInjected = false
function ensureStyles() {
  if (stylesInjected || typeof document === 'undefined') return
  stylesInjected = true
  const style = document.createElement('style')
  style.id = 'css2d-label-styles'
  style.textContent = `
    .css2d-label, .css2d-label * { pointer-events: none !important; user-select: none; will-change: transform; }
    .css2d-card {
      display: flex; align-items: center; gap: 5px;
      padding: 5px 9px; border-radius: 4px;
      background: rgba(10, 14, 32, 0.9);
      border: 1px solid rgba(120, 110, 140, 0.35);
      box-shadow: 0 4px 14px rgba(0,0,0,0.35);
      font-family: 'Noto Serif SC', 'Songti SC', serif;
      font-size: 12px; font-weight: 600; color: #8a8498;
      white-space: nowrap;
    }
    .css2d-card.is-compact {
      padding: 3px 7px;
      gap: 0;
      background: rgba(10, 14, 32, 0.72);
      box-shadow: 0 2px 8px rgba(0,0,0,0.25);
    }
    .css2d-idx { font-size: 11px; color: #5a5868; letter-spacing: 0.06em; font-weight: 700; }
    .css2d-name { font-weight: 600; max-width: 110px; overflow: hidden; text-overflow: ellipsis; color: #7a7488; }
    .css2d-state {
      font-size: 10px; font-weight: 700; padding: 1px 5px; border-radius: 2px;
      border: 1px solid #5a5868; color: #6a6878;
    }
    /* 默认紧凑标号 */
    .css2d-card.is-pending, .css2d-card.is-ready, .css2d-card.is-done {
      opacity: 0.82;
      border-color: rgba(90, 88, 110, 0.4);
    }
    .css2d-card.is-done .css2d-idx { color: #7ab8a0; }
    .css2d-card.is-pending .css2d-idx, .css2d-card.is-ready .css2d-idx { color: #6a6478; }
    /* 进行中：标号 + 名称 + 状态 */
    .css2d-card.is-running {
      border-color: rgba(255,138,64,.55);
      color: #e8d8c0;
      opacity: 1;
      background: rgba(28, 18, 12, 0.94);
      box-shadow: 0 0 14px rgba(255, 138, 64, 0.28);
    }
    .css2d-card.is-running .css2d-idx { color: #ffb070; }
    .css2d-card.is-running .css2d-name { color: #f0d8b0; }
    .css2d-card.is-running .css2d-state { color: #ff8a40; border-color: #ff8a40; }
    .css2d-card.is-error .css2d-state, .css2d-card.is-failed .css2d-state,
    .css2d-card.is-blocked .css2d-state { color: #ff6068; border-color: #ff6068; }
    .css2d-card.is-error .css2d-name, .css2d-card.is-failed .css2d-name,
    .css2d-card.is-blocked .css2d-name { color: #e8a0a8; }
    .css2d-card.is-error, .css2d-card.is-failed, .css2d-card.is-blocked {
      opacity: 1;
      border-color: rgba(224, 85, 85, 0.55);
    }
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
    <div class="css2d-card is-pending is-compact">
      <span class="css2d-idx">${String(index + 1).padStart(2, '0')}</span>
      <span class="css2d-name" style="display:none">${stage.label}</span>
      <span class="css2d-state" data-state style="display:none">静候</span>
    </div>
  `
  el.style.display = ''
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
