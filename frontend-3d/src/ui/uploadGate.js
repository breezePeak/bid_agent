/**
 * 开场三宝门：卷轴入场 · 灵光投料 · 开炉收束
 * demo=true 时点击或一键模拟投料；live 时真实上传
 */

const CATEGORIES = [
  {
    id: 'tender',
    title: '招标文件',
    rune: '招',
    hint: '招标书 · 评分办法 · 技术要求',
    accept: '.pdf,.docx,.doc,.md,.txt',
  },
  {
    id: 'company',
    title: '公司资料',
    rune: '资',
    hint: '介绍 · 资质 · 案例 · 人员',
    accept: '.pdf,.docx,.doc,.md,.txt,.zip',
  },
  {
    id: 'template',
    title: 'Word 模板',
    rune: '式',
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
      <div class="upload-gate-bg">
        <div class="ug-stars" aria-hidden="true"></div>
        <div class="ug-vignette" aria-hidden="true"></div>
        <div class="ug-orbit" aria-hidden="true"></div>
      </div>
      <div class="upload-gate-inner">
        <div class="upload-gate-title">
          <div class="ugt-seal" aria-hidden="true">丹</div>
          <div class="ugt-main">
            <span class="ugt-char" style="--ci:0">天</span>
            <span class="ugt-char" style="--ci:1">降</span>
            <span class="ugt-char" style="--ci:2">三</span>
            <span class="ugt-char" style="--ci:3">宝</span>
            <span class="ugt-dot">·</span>
            <span class="ugt-char" style="--ci:4">开</span>
            <span class="ugt-char" style="--ci:5">炉</span>
            <span class="ugt-char" style="--ci:6">投</span>
            <span class="ugt-char" style="--ci:7">料</span>
          </div>
          <div class="ugt-line" aria-hidden="true"></div>
          <div class="ugt-sub">${
            demo
              ? '演法模式：点选宝匣模拟投料，齐备后开炼'
              : '观火模式：上传招标文件、公司资料与 Word 模板'
          }</div>
        </div>
        <div class="upload-panels" id="upload-panels"></div>
        <div class="upload-gate-foot">
          <div class="upload-progress" aria-hidden="true">
            <div class="ug-prog-track"><div class="ug-prog-fill" id="upload-prog"></div></div>
            <div class="upload-status" id="upload-status">尚缺三宝，请先投料</div>
          </div>
          <div class="upload-gate-actions">
            ${
              demo
                ? `<button type="button" class="btn primary ug-btn" id="btn-auto-feed">一键投料</button>`
                : ''
            }
            <button type="button" class="btn primary ug-btn" id="btn-start-forge" disabled>开炉炼制</button>
            <button type="button" class="btn ghost ug-btn" id="btn-skip-gate">跳过 · 直接观览</button>
          </div>
        </div>
      </div>
    </div>
  `)

  // 背景星尘
  const starsHost = root.querySelector('.ug-stars')
  for (let i = 0; i < 36; i++) {
    const s = document.createElement('i')
    s.className = 'ug-star'
    s.style.setProperty('--sx', `${Math.random() * 100}%`)
    s.style.setProperty('--sy', `${Math.random() * 100}%`)
    s.style.setProperty('--sd', `${1.5 + Math.random() * 3.5}s`)
    s.style.setProperty('--ss', `${0.6 + Math.random() * 1.4}`)
    starsHost.appendChild(s)
  }

  const panelsHost = root.querySelector('#upload-panels')
  const statusEl = root.querySelector('#upload-status')
  const progEl = root.querySelector('#upload-prog')
  const btnStart = root.querySelector('#btn-start-forge')
  const btnSkip = root.querySelector('#btn-skip-gate')
  const btnAuto = root.querySelector('#btn-auto-feed')

  const cardEls = {}

  CATEGORIES.forEach((cat, i) => {
    const card = el(`
      <div class="upload-panel" data-cat="${cat.id}" style="--drop-i:${i}">
        <div class="up-frame" aria-hidden="true">
          <span class="up-corner tl"></span><span class="up-corner tr"></span>
          <span class="up-corner bl"></span><span class="up-corner br"></span>
        </div>
        <div class="up-glow"></div>
        <div class="up-trail" aria-hidden="true"></div>
        <div class="up-rune">${cat.rune}</div>
        <div class="up-title">${cat.title}</div>
        <div class="up-hint">${cat.hint}</div>
        <div class="up-files" data-files></div>
        <div class="up-badge"><span>待投</span></div>
        <div class="up-seal" aria-hidden="true">备</div>
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
      // 整卡可点
      card.addEventListener('click', (e) => {
        if (e.target.closest('.up-action')) return
        if (busy || state[cat.id].length) return
        simulateOne(cat.id)
      })
    } else {
      const input = card.querySelector('[data-input]')
      input?.addEventListener('change', () => {
        const files = Array.from(input.files || [])
        if (!files.length) return
        state[cat.id] = files
        playFeedFx(card)
        refreshCard(cat.id)
        refreshFoot()
      })
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
        playFeedFx(card)
        refreshCard(cat.id)
        refreshFoot()
      })
    }
  })

  function playFeedFx(card) {
    if (!card) return
    card.classList.remove('feeding', 'seal-pop')
    // 强制重启动画
    void card.offsetWidth
    card.classList.add('feeding')
    spawnSparks(card)
    setTimeout(() => {
      card.classList.remove('feeding')
      card.classList.add('seal-pop')
      setTimeout(() => card.classList.remove('seal-pop'), 700)
    }, 520)
  }

  function spawnSparks(card) {
    const host = card.querySelector('.up-glow') || card
    for (let i = 0; i < 10; i++) {
      const p = document.createElement('i')
      p.className = 'up-spark'
      const ang = (i / 10) * Math.PI * 2
      const dist = 28 + Math.random() * 36
      p.style.setProperty('--px', `${Math.cos(ang) * dist}px`)
      p.style.setProperty('--py', `${Math.sin(ang) * dist - 10}px`)
      p.style.setProperty('--pd', `${0.35 + Math.random() * 0.35}s`)
      host.appendChild(p)
      setTimeout(() => p.remove(), 700)
    }
  }

  function refreshCard(id) {
    const card = cardEls[id]
    if (!card) return
    const files = state[id] || []
    const list = card.querySelector('[data-files]')
    const badge = card.querySelector('.up-badge span')
    if (files.length) {
      card.classList.add('ready')
      if (badge) badge.textContent = '已备'
      list.innerHTML = files
        .map((f) => `<div class="up-file">${escapeHtml(f.name || f)}</div>`)
        .join('')
      const act = card.querySelector('.up-action')
      if (act && demo) act.textContent = '已投料'
    } else {
      card.classList.remove('ready')
      if (badge) badge.textContent = '待投'
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
    if (progEl) progEl.style.width = `${(n / 3) * 100}%`
    root.classList.toggle('all-ready', n >= 3)
    if (n >= 3) {
      statusEl.textContent = '三宝齐备 · 可开炉炼制'
      btnStart.disabled = false
      btnStart.classList.add('ug-pulse')
    } else {
      statusEl.textContent = `尚缺 ${3 - n} 宝 · 已备 ${n}/3`
      btnStart.disabled = true
      btnStart.classList.remove('ug-pulse')
    }
  }

  function simulateOne(id) {
    const names = {
      tender: '招标文件-示例.pdf',
      company: '公司资质与案例.docx',
      template: '标书模板.docx',
    }
    const card = cardEls[id]
    playFeedFx(card)
    statusEl.textContent = `正在投送${CATEGORIES.find((c) => c.id === id)?.title || ''}…`
    setTimeout(() => {
      state[id] = [{ name: names[id], demo: true }]
      refreshCard(id)
      refreshFoot()
    }, 480 + Math.random() * 200)
  }

  async function simulateAll() {
    if (busy) return
    busy = true
    btnAuto && (btnAuto.disabled = true)
    for (const cat of CATEGORIES) {
      if (!state[cat.id].length) {
        await new Promise((r) => {
          simulateOne(cat.id)
          setTimeout(r, 620)
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

  function dismiss(mode = 'leave') {
    if (dismissed) return
    dismissed = true
    root.classList.add(mode)
    setTimeout(() => root.remove(), mode === 'forge' ? 1100 : 700)
  }

  btnStart.addEventListener('click', async () => {
    if (!allReady() || busy) return
    setBusy(true, demo ? '演法开炉中…' : '创建丹房 · 上传三宝…')
    // 面板吸入后立刻撤掉全屏遮罩，避免挡住 3D 开炉仪式
    root.classList.add('forge')
    Object.values(cardEls).forEach((c, i) => {
      c.style.setProperty('--forge-i', String(i))
      c.classList.add('forge-in')
    })
    const payload = {
      demo,
      files: {
        tender: state.tender,
        company: state.company,
        template: state.template,
      },
    }
    // 约 0.55s 后移除遮罩，再执行开炉（仪式需看见 3D）
    await new Promise((r) => setTimeout(r, 550))
    dismiss('forge')
    try {
      await opts.onComplete?.(payload)
    } catch (err) {
      console.error('[upload-gate]', err)
      // 遮罩已关；失败提示交给外层 calligraphy / console
    }
  })

  btnSkip.addEventListener('click', () => {
    if (busy) return
    opts.onSkip?.()
    dismiss('leave')
  })

  btnAuto?.addEventListener('click', () => simulateAll())

  mountParent.appendChild(root)
  requestAnimationFrame(() => {
    root.classList.add('show')
    // 标题字逐个亮
    root.querySelectorAll('.ugt-char').forEach((ch) => {
      ch.classList.add('in')
    })
  })

  return {
    root,
    dismiss: () => dismiss('leave'),
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
