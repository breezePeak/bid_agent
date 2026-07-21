/**
 * 开场三面板：从天而降，投递招标 / 公司 / 模板
 * demo=true 时点击或一键模拟投料；live 时真实上传
 */

const CATEGORIES = [
  {
    id: 'tender',
    title: '招标文件',
    emoji: '📜',
    hint: '招标书 · 评分办法 · 技术要求',
    accept: '.pdf,.docx,.doc,.md,.txt',
  },
  {
    id: 'company',
    title: '公司资料',
    emoji: '🏢',
    hint: '介绍 · 资质 · 案例 · 人员',
    accept: '.pdf,.docx,.doc,.md,.txt,.zip',
  },
  {
    id: 'template',
    title: 'Word 模板',
    emoji: '📑',
    hint: '最终套用的标书样式模板',
    accept: '.docx,.doc',
  },
]

function el(html) {
  const t = document.createElement('template')
  t.innerHTML = html.trim()
  return t.content.firstElementChild
}

/**
 * @param {HTMLElement} mountParent
 * @param {{ demo?: boolean, onComplete: (payload) => void | Promise<void>, onSkip?: () => void }} opts
 */
export function createUploadGate(mountParent, opts = {}) {
  const demo = Boolean(opts.demo)
  const state = {
    tender: [],
    company: [],
    template: [],
  }
  let busy = false
  let dismissed = false

  const root = el(`
    <div class="upload-gate" id="upload-gate" aria-modal="true" role="dialog">
      <div class="upload-gate-bg"></div>
      <div class="upload-gate-inner">
        <div class="upload-gate-title">
          <div class="ugt-main">天降三宝 · 开炉投料</div>
          <div class="ugt-sub">${
            demo
              ? '演法模式：点选面板模拟投料，齐备后开炼'
              : '观火模式：上传招标文件、公司资料与 Word 模板'
          }</div>
        </div>
        <div class="upload-panels" id="upload-panels"></div>
        <div class="upload-gate-foot">
          <div class="upload-status" id="upload-status">尚缺三宝，请先投料</div>
          <div class="upload-gate-actions">
            ${
              demo
                ? `<button type="button" class="btn primary" id="btn-auto-feed">一键投料</button>`
                : ''
            }
            <button type="button" class="btn primary" id="btn-start-forge" disabled>开炉炼制</button>
            <button type="button" class="btn ghost" id="btn-skip-gate">跳过 · 直接观览</button>
          </div>
        </div>
      </div>
    </div>
  `)

  const panelsHost = root.querySelector('#upload-panels')
  const statusEl = root.querySelector('#upload-status')
  const btnStart = root.querySelector('#btn-start-forge')
  const btnSkip = root.querySelector('#btn-skip-gate')
  const btnAuto = root.querySelector('#btn-auto-feed')

  const cardEls = {}

  CATEGORIES.forEach((cat, i) => {
    const card = el(`
      <div class="upload-panel" data-cat="${cat.id}" style="--drop-i:${i}">
        <div class="up-glow"></div>
        <div class="up-emoji">${cat.emoji}</div>
        <div class="up-title">${cat.title}</div>
        <div class="up-hint">${cat.hint}</div>
        <div class="up-files" data-files></div>
        <div class="up-badge">待投</div>
        ${
          demo
            ? `<button type="button" class="up-action" data-sim>点此投料</button>`
            : `<label class="up-action">
                选择文件
                <input type="file" multiple accept="${cat.accept}" hidden data-input />
              </label>`
        }
      </div>
    `)
    panelsHost.appendChild(card)
    cardEls[cat.id] = card

    if (demo) {
      card.querySelector('[data-sim]')?.addEventListener('click', () => {
        if (busy || state[cat.id].length) return
        simulateOne(cat.id)
      })
    } else {
      const input = card.querySelector('[data-input]')
      input?.addEventListener('change', () => {
        const files = Array.from(input.files || [])
        if (!files.length) return
        state[cat.id] = files
        refreshCard(cat.id)
        refreshFoot()
      })
      // 拖放
      card.addEventListener('dragover', (e) => {
        e.preventDefault()
        card.classList.add('drag')
      })
      card.addEventListener('dragleave', () => card.classList.remove('drag'))
      card.addEventListener('drop', (e) => {
        e.preventDefault()
        card.classList.remove('drag')
        const files = Array.from(e.dataTransfer?.files || [])
        if (!files.length) return
        state[cat.id] = files
        refreshCard(cat.id)
        refreshFoot()
      })
    }
  })

  function refreshCard(id) {
    const card = cardEls[id]
    if (!card) return
    const files = state[id] || []
    const list = card.querySelector('[data-files]')
    const badge = card.querySelector('.up-badge')
    if (files.length) {
      card.classList.add('ready')
      badge.textContent = '已备'
      list.innerHTML = files
        .map((f) => `<div class="up-file">${escapeHtml(f.name || f)}</div>`)
        .join('')
      const act = card.querySelector('.up-action')
      if (act && demo) act.textContent = '已投料'
    } else {
      card.classList.remove('ready')
      badge.textContent = '待投'
      list.innerHTML = ''
    }
  }

  function readyCount() {
    return CATEGORIES.filter((c) => (state[c.id] || []).length > 0).length
  }

  function allReady() {
    return readyCount() === 3
  }

  function refreshFoot() {
    const n = readyCount()
    if (n >= 3) {
      statusEl.textContent = '三宝齐备 · 可开炉炼制'
      btnStart.disabled = false
    } else {
      statusEl.textContent = `尚缺 ${3 - n} 宝 · 已备 ${n}/3`
      btnStart.disabled = true
    }
  }

  function simulateOne(id) {
    const names = {
      tender: '招标文件-示例.pdf',
      company: '公司资质与案例.docx',
      template: '标书模板.docx',
    }
    const card = cardEls[id]
    card?.classList.add('feeding')
    statusEl.textContent = `正在投送${CATEGORIES.find((c) => c.id === id)?.title || ''}…`
    setTimeout(() => {
      state[id] = [{ name: names[id], demo: true }]
      card?.classList.remove('feeding')
      refreshCard(id)
      refreshFoot()
    }, 480 + Math.random() * 320)
  }

  async function simulateAll() {
    if (busy) return
    busy = true
    btnAuto && (btnAuto.disabled = true)
    for (const cat of CATEGORIES) {
      if (!state[cat.id].length) {
        await new Promise((r) => {
          simulateOne(cat.id)
          setTimeout(r, 700)
        })
      }
    }
    busy = false
    btnAuto && (btnAuto.disabled = false)
    refreshFoot()
  }

  function setBusy(v, msg) {
    busy = v
    btnStart.disabled = v || !allReady()
    if (btnAuto) btnAuto.disabled = v
    btnSkip.disabled = v
    if (msg) statusEl.textContent = msg
  }

  function dismiss() {
    if (dismissed) return
    dismissed = true
    root.classList.add('leave')
    setTimeout(() => root.remove(), 700)
  }

  btnStart.addEventListener('click', async () => {
    if (!allReady() || busy) return
    setBusy(true, demo ? '演法开炉中…' : '创建丹房 · 上传三宝…')
    try {
      await opts.onComplete?.({
        demo,
        files: {
          tender: state.tender,
          company: state.company,
          template: state.template,
        },
      })
      dismiss()
    } catch (err) {
      console.error('[upload-gate]', err)
      setBusy(false, `开炉失败：${err?.message || err}`)
    }
  })

  btnSkip.addEventListener('click', () => {
    if (busy) return
    opts.onSkip?.()
    dismiss()
  })

  btnAuto?.addEventListener('click', () => simulateAll())

  mountParent.appendChild(root)
  // 入场动画
  requestAnimationFrame(() => root.classList.add('show'))

  return {
    root,
    dismiss,
    isOpen: () => !dismissed && document.body.contains(root),
  }
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}
