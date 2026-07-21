/**
 * 炼丹阁音景 — Web Audio 程序化合成，无需外部资源
 * 与画面事件一一对应：炉火 / 灵力波 / 结丹 / 终局 / UI
 */
export function createSoundscape() {
  let ctx = null
  let master = null
  let ambientGain = null
  let sfxGain = null
  let fireGain = null
  let fireFilter = null
  let droneOsc = null
  let droneOsc2 = null
  let noiseNode = null
  let noiseFilter = null
  let started = false
  let muted = false
  let fireLevel = 0.35
  let targetFire = 0.35
  let lastWhoosh = 0
  let lastChime = 0
  let unlockBound = false

  function ensureCtx() {
    if (ctx) return ctx
    const AC = window.AudioContext || window.webkitAudioContext
    if (!AC) return null
    ctx = new AC()
    master = ctx.createGain()
    master.gain.value = muted ? 0 : 0.55
    master.connect(ctx.destination)

    ambientGain = ctx.createGain()
    ambientGain.gain.value = 0.55
    ambientGain.connect(master)

    sfxGain = ctx.createGain()
    sfxGain.gain.value = 0.9
    sfxGain.connect(master)

    return ctx
  }

  function resume() {
    const c = ensureCtx()
    if (!c) return Promise.resolve(false)
    if (c.state === 'suspended') return c.resume().then(() => true)
    return Promise.resolve(true)
  }

  function bindUnlock() {
    if (unlockBound) return
    unlockBound = true
    const unlock = () => {
      resume().then((ok) => {
        if (ok && !started) startAmbient()
      })
      window.removeEventListener('pointerdown', unlock)
      window.removeEventListener('keydown', unlock)
    }
    window.addEventListener('pointerdown', unlock, { once: true })
    window.addEventListener('keydown', unlock, { once: true })
  }

  function noiseBuffer(seconds = 2) {
    const c = ensureCtx()
    const len = Math.floor(c.sampleRate * seconds)
    const buf = c.createBuffer(1, len, c.sampleRate)
    const data = buf.getChannelData(0)
    for (let i = 0; i < len; i++) data[i] = Math.random() * 2 - 1
    return buf
  }

  function startAmbient() {
    const c = ensureCtx()
    if (!c || started) return
    started = true

    // 低沉丹炉嗡鸣（双音轻微失谐）
    droneOsc = c.createOscillator()
    droneOsc.type = 'sine'
    droneOsc.frequency.value = 55
    droneOsc2 = c.createOscillator()
    droneOsc2.type = 'triangle'
    droneOsc2.frequency.value = 82.5
    const droneMix = c.createGain()
    droneMix.gain.value = 0.12
    const droneFilter = c.createBiquadFilter()
    droneFilter.type = 'lowpass'
    droneFilter.frequency.value = 220
    droneOsc.connect(droneMix)
    droneOsc2.connect(droneMix)
    droneMix.connect(droneFilter)
    droneFilter.connect(ambientGain)
    droneOsc.start()
    droneOsc2.start()

    // 炉火噪声（粉噪感：低频滤波 + 增益起伏在 update 里做）
    noiseNode = c.createBufferSource()
    noiseNode.buffer = noiseBuffer(3)
    noiseNode.loop = true
    noiseFilter = c.createBiquadFilter()
    noiseFilter.type = 'bandpass'
    noiseFilter.frequency.value = 900
    noiseFilter.Q.value = 0.6
    fireFilter = c.createBiquadFilter()
    fireFilter.type = 'lowpass'
    fireFilter.frequency.value = 1800
    fireGain = c.createGain()
    fireGain.gain.value = 0.08
    noiseNode.connect(noiseFilter)
    noiseFilter.connect(fireFilter)
    fireFilter.connect(fireGain)
    fireGain.connect(ambientGain)
    noiseNode.start()

    // 极轻风声层
    const wind = c.createBufferSource()
    wind.buffer = noiseBuffer(4)
    wind.loop = true
    const windF = c.createBiquadFilter()
    windF.type = 'highpass'
    windF.frequency.value = 400
    const windG = c.createGain()
    windG.gain.value = 0.018
    wind.connect(windF)
    windF.connect(windG)
    windG.connect(ambientGain)
    wind.start()
  }

  function envGain(node, t0, a, peak, hold, rel) {
    const g = node.gain
    g.cancelScheduledValues(t0)
    g.setValueAtTime(0.0001, t0)
    g.exponentialRampToValueAtTime(Math.max(0.0002, peak), t0 + a)
    g.exponentialRampToValueAtTime(Math.max(0.0002, peak * 0.7), t0 + a + hold)
    g.exponentialRampToValueAtTime(0.0001, t0 + a + hold + rel)
  }

  /** 灵力波 / 谁osh：低频扫 + 噪声爆发 */
  function playWhoosh({ intense = false } = {}) {
    const c = ensureCtx()
    if (!c || muted) return
    const now = c.currentTime
    if (now - lastWhoosh < 0.12) return
    lastWhoosh = now
    resume()

    const dur = intense ? 1.8 : 1.15
    const src = c.createBufferSource()
    src.buffer = noiseBuffer(dur + 0.2)
    const bp = c.createBiquadFilter()
    bp.type = 'bandpass'
    bp.Q.value = 0.8
    bp.frequency.setValueAtTime(280, now)
    bp.frequency.exponentialRampToValueAtTime(intense ? 2400 : 1600, now + dur * 0.45)
    bp.frequency.exponentialRampToValueAtTime(400, now + dur)
    const g = c.createGain()
    envGain(g, now, 0.04, intense ? 0.32 : 0.22, 0.15, dur * 0.75)
    src.connect(bp)
    bp.connect(g)
    g.connect(sfxGain)
    src.start(now)
    src.stop(now + dur + 0.05)

    // 金铃底音
    const osc = c.createOscillator()
    osc.type = 'sine'
    osc.frequency.setValueAtTime(220, now)
    osc.frequency.exponentialRampToValueAtTime(110, now + dur)
    const og = c.createGain()
    envGain(og, now, 0.02, intense ? 0.12 : 0.08, 0.1, dur * 0.8)
    osc.connect(og)
    og.connect(sfxGain)
    osc.start(now)
    osc.stop(now + dur + 0.05)
  }

  /** 结丹金音：五度琶音 + 叮 */
  function playChime({ bright = false } = {}) {
    const c = ensureCtx()
    if (!c || muted) return
    const now = c.currentTime
    if (now - lastChime < 0.08) return
    lastChime = now
    resume()

    const base = bright ? 523.25 : 392
    const notes = bright ? [base, base * 1.25, base * 1.5, base * 2] : [base, base * 4 / 3, base * 1.5]
    notes.forEach((f, i) => {
      const t0 = now + i * 0.07
      const osc = c.createOscillator()
      osc.type = i === notes.length - 1 ? 'triangle' : 'sine'
      osc.frequency.value = f
      const g = c.createGain()
      envGain(g, t0, 0.01, bright ? 0.18 : 0.12, 0.05, bright ? 1.2 : 0.7)
      const filter = c.createBiquadFilter()
      filter.type = 'lowpass'
      filter.frequency.value = 4200
      osc.connect(filter)
      filter.connect(g)
      g.connect(sfxGain)
      osc.start(t0)
      osc.stop(t0 + 1.4)
    })
  }

  /** 金丹飞出：上行滑音 */
  function playOrbFly() {
    const c = ensureCtx()
    if (!c || muted) return
    resume()
    const now = c.currentTime
    const osc = c.createOscillator()
    osc.type = 'sine'
    osc.frequency.setValueAtTime(330, now)
    osc.frequency.exponentialRampToValueAtTime(880, now + 1.1)
    const g = c.createGain()
    envGain(g, now, 0.03, 0.1, 0.5, 0.55)
    const sh = c.createBiquadFilter()
    sh.type = 'highpass'
    sh.frequency.value = 200
    osc.connect(sh)
    sh.connect(g)
    g.connect(sfxGain)
    osc.start(now)
    osc.stop(now + 1.25)

    // 细碎火花噪声尾
    const n = c.createBufferSource()
    n.buffer = noiseBuffer(0.6)
    const nf = c.createBiquadFilter()
    nf.type = 'highpass'
    nf.frequency.value = 2500
    const ng = c.createGain()
    envGain(ng, now + 0.9, 0.01, 0.06, 0.05, 0.25)
    n.connect(nf)
    nf.connect(ng)
    ng.connect(sfxGain)
    n.start(now + 0.9)
    n.stop(now + 1.3)
  }

  /** 工序完成：波 + 飞丹 + 金铃 */
  function playStageDone() {
    playWhoosh()
    playOrbFly()
    setTimeout(() => playChime(), 180)
  }

  /** 灵力波按钮 / 终局波 */
  function playSpiritWave() {
    playWhoosh({ intense: true })
    playChime({ bright: false })
  }

  /** 终局旋合：持续上升和弦 */
  function playFinaleRise() {
    const c = ensureCtx()
    if (!c || muted) return
    resume()
    const now = c.currentTime
    const freqs = [130.8, 164.8, 196, 261.6]
    freqs.forEach((f, i) => {
      const osc = c.createOscillator()
      osc.type = 'sine'
      osc.frequency.setValueAtTime(f, now)
      osc.frequency.exponentialRampToValueAtTime(f * 2, now + 2.8)
      const g = c.createGain()
      envGain(g, now + i * 0.12, 0.15, 0.07, 1.6, 1.2)
      osc.connect(g)
      g.connect(sfxGain)
      osc.start(now + i * 0.12)
      osc.stop(now + 3.5)
    })
    playWhoosh({ intense: true })
  }

  /** 黄金标书现身 */
  function playBookReveal() {
    const c = ensureCtx()
    if (!c || muted) return
    resume()
    playChime({ bright: true })
    const now = c.currentTime
    // 大钟
    const bell = c.createOscillator()
    bell.type = 'sine'
    bell.frequency.setValueAtTime(196, now)
    bell.frequency.exponentialRampToValueAtTime(98, now + 2.5)
    const bg = c.createGain()
    envGain(bg, now, 0.02, 0.22, 0.3, 2.2)
    const partial = c.createOscillator()
    partial.type = 'triangle'
    partial.frequency.value = 392
    const pg = c.createGain()
    envGain(pg, now, 0.01, 0.08, 0.2, 1.8)
    bell.connect(bg)
    partial.connect(pg)
    bg.connect(sfxGain)
    pg.connect(sfxGain)
    bell.start(now)
    partial.start(now)
    bell.stop(now + 2.8)
    partial.stop(now + 2.2)
  }

  /** 炸炉 / 失败 */
  function playFail() {
    const c = ensureCtx()
    if (!c || muted) return
    resume()
    const now = c.currentTime
    const n = c.createBufferSource()
    n.buffer = noiseBuffer(0.5)
    const f = c.createBiquadFilter()
    f.type = 'lowpass'
    f.frequency.setValueAtTime(1200, now)
    f.frequency.exponentialRampToValueAtTime(180, now + 0.4)
    const g = c.createGain()
    envGain(g, now, 0.01, 0.28, 0.05, 0.35)
    n.connect(f)
    f.connect(g)
    g.connect(sfxGain)
    n.start(now)
    n.stop(now + 0.5)
    const osc = c.createOscillator()
    osc.type = 'sawtooth'
    osc.frequency.setValueAtTime(160, now)
    osc.frequency.exponentialRampToValueAtTime(60, now + 0.45)
    const og = c.createGain()
    envGain(og, now, 0.01, 0.08, 0.05, 0.35)
    osc.connect(og)
    og.connect(sfxGain)
    osc.start(now)
    osc.stop(now + 0.5)
  }

  /** 轻点 UI */
  function playUiTap() {
    const c = ensureCtx()
    if (!c || muted) return
    resume()
    if (!started) startAmbient()
    const now = c.currentTime
    const osc = c.createOscillator()
    osc.type = 'sine'
    osc.frequency.value = 660
    const g = c.createGain()
    envGain(g, now, 0.005, 0.05, 0.02, 0.08)
    osc.connect(g)
    g.connect(sfxGain)
    osc.start(now)
    osc.stop(now + 0.12)
  }

  /** 根据炼丹进度调节炉火声势 0~1 */
  function setFireIntensity(level) {
    targetFire = Math.max(0.12, Math.min(1, level))
  }

  function setMuted(m) {
    muted = !!m
    if (master) {
      const c = ensureCtx()
      const t = c ? c.currentTime : 0
      master.gain.cancelScheduledValues(t)
      master.gain.setTargetAtTime(muted ? 0 : 0.55, t, 0.05)
    }
  }

  function toggleMute() {
    setMuted(!muted)
    return muted
  }

  function update(dt = 0.016) {
    if (!started || !fireGain || !ctx) return
    fireLevel += (targetFire - fireLevel) * Math.min(1, dt * 2.5)
    const t = ctx.currentTime
    // 炉火噼啪感：增益与滤波微抖
    const crackle = 0.85 + Math.sin(t * 17.3) * 0.08 + Math.sin(t * 41) * 0.05
    const g = 0.04 + fireLevel * 0.14 * crackle
    fireGain.gain.setTargetAtTime(g, t, 0.05)
    if (fireFilter) {
      fireFilter.frequency.setTargetAtTime(900 + fireLevel * 1600 + Math.sin(t * 9) * 120, t, 0.08)
    }
    if (droneOsc) {
      droneOsc.frequency.setTargetAtTime(48 + fireLevel * 18, t, 0.2)
    }
    if (noiseFilter) {
      noiseFilter.frequency.setTargetAtTime(700 + fireLevel * 500, t, 0.1)
    }
  }

  bindUnlock()

  return {
    resume,
    startAmbient,
    playStageDone,
    playSpiritWave,
    playFinaleRise,
    playBookReveal,
    playFail,
    playUiTap,
    playWhoosh,
    playChime,
    setFireIntensity,
    setMuted,
    toggleMute,
    isMuted: () => muted,
    update,
  }
}
