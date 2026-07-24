import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { CSS2DRenderer } from 'three/addons/renderers/CSS2DRenderer.js'
import { createEnvironment } from './environment.js'
import { createStageTrack } from './stageTrack.js'
import { createAgentField } from './agentField.js'
import { createDataFlow } from './dataFlow.js'
import { createDanFx } from './danFx.js'
import { createSoundscape } from '../audio/soundscape.js'

export class App3D {
  constructor(canvas) {
    this.canvas = canvas
    this.clock = new THREE.Clock()
    this.raycaster = new THREE.Raycaster()
    this.pointer = new THREE.Vector2()
    this.autoOrbit = false
    this.focusMode = 'overview'
    this.onPick = null
    this._userDriving = false
    this._camAnim = null
    this._camTour = null
    this._lastSnap = null
    this._pickables = []

    // Cap pixel ratio hard — 2x DPR destroys FPS on 4K/laptop
    const dpr = Math.min(window.devicePixelRatio || 1, 1.25)

    this.renderer = new THREE.WebGLRenderer({
      canvas,
      antialias: dpr < 1.2,
      alpha: false,
      powerPreference: 'high-performance',
      stencil: false,
      depth: true,
    })
    this.renderer.setPixelRatio(dpr)
    this.renderer.setSize(window.innerWidth, window.innerHeight, false)
    this.renderer.outputColorSpace = THREE.SRGBColorSpace
    this.renderer.toneMapping = THREE.NoToneMapping // cheaper than ACES
    this.renderer.setClearColor(0x060818, 1)

    this.labelRenderer = new CSS2DRenderer()
    this.labelRenderer.setSize(window.innerWidth, window.innerHeight)
    const labelEl = this.labelRenderer.domElement
    labelEl.style.position = 'absolute'
    labelEl.style.inset = '0'
    labelEl.style.pointerEvents = 'none'
    // 必须低于 HUD(10)，否则会挡住底部按钮
    labelEl.style.zIndex = '1'
    // 插到 canvas 之后、hud 之前，避免盖住 UI
    const parent = canvas.parentElement
    const hudEl = document.getElementById('hud')
    if (hudEl) parent.insertBefore(labelEl, hudEl)
    else parent.appendChild(labelEl)

    this.scene = new THREE.Scene()
    this.camera = new THREE.PerspectiveCamera(42, window.innerWidth / window.innerHeight, 0.3, 220)
    // 御道尽头仰视大殿（气势）
    this.camera.position.set(0, 7, 58)

    this.controls = new OrbitControls(this.camera, canvas)
    this.controls.enabled = true
    this.controls.enableDamping = true
    this.controls.dampingFactor = 0.08
    this.controls.enableZoom = true
    this.controls.zoomSpeed = 1.35
    this.controls.minDistance = 1.2
    this.controls.maxDistance = 160
    this.controls.minPolarAngle = 0.02
    this.controls.maxPolarAngle = Math.PI - 0.02
    this.controls.enablePan = true
    this.controls.panSpeed = 1.1
    this.controls.rotateSpeed = 0.9
    this.controls.screenSpacePanning = true
    this.controls.target.set(0, 3.5, 4)
    this.controls.update()

    // 保证 canvas 能收到拖拽
    canvas.style.touchAction = 'none'
    canvas.style.pointerEvents = 'auto'
    canvas.tabIndex = 0

    // 在 canvas 上按下时强制恢复轨道控制（防止视角动画后卡住）
    canvas.addEventListener(
      'pointerdown',
      () => {
        // 环游刚启动的保护窗内，忽略误触打断
        if (this._tourLockUntil && performance.now() < this._tourLockUntil) return
        // 投料仪式中不打断
        if (this._feedRitual && !this._feedRitual.finished) return
        this.controls.enabled = true
        this.autoOrbit = false
        this._userDriving = true
        this._camAnim = null
        this._camTour = null
      },
      { capture: true },
    )

    this.env = createEnvironment(this.scene)
    this.stageTrack = createStageTrack(this.scene)
    const pav = this.env.pavilion
    this.agentField = createAgentField(this.scene, {
      bossStand: pav.bossStand,
      furnacePos: pav.hallCenter,
      hallCenter: pav.hallCenter,
      workSlots: pav.workSlots,
      queueOrigin: pav.queueOrigin,
      doorPos: pav.doorPos,
      queueSlots: pav.queueSlots,
      loungeSlots: pav.loungeSlots,
    })
    this.dataFlow = createDataFlow(this.scene, this.stageTrack.curve)
    // 结丹飞升特效（从丹炉飞向工序）
    const furnacePos = this.env.pedestal
      ? this.env.pedestal.getWorldPosition(new THREE.Vector3()).add(new THREE.Vector3(0, 2.2, 0))
      : new THREE.Vector3(0, 4, -6)
    this.danFx = createDanFx(this.scene, furnacePos)
    this.audio = createSoundscape()
    this.onStageComplete = null
    this._completedIds = new Set()
    this._allDoneFired = false
    this._lastFailSig = ''
    this._feedRitual = null
    this._feedScrolls = []

    this._rebuildPickables()

    this._onResize = () => this.resize()
    this._onPointer = (e) => this.onPointerMove(e)
    this._onClick = (e) => this.onClick(e)
    window.addEventListener('resize', this._onResize)
    canvas.addEventListener('pointermove', this._onPointer)
    canvas.addEventListener('click', this._onClick)

    this.controls.addEventListener('start', () => {
      if (this._tourLockUntil && performance.now() < this._tourLockUntil) return
      this._userDriving = true
      this.autoOrbit = false
      this.controls.enabled = true
      this._camAnim = null
      this._camTour = null
    })
    this.controls.addEventListener('end', () => {
      this.controls.enabled = true
      window.setTimeout(() => {
        this._userDriving = false
      }, 1200)
    })

    this._raf = 0
    this._running = false
    this._frame = 0
  }

  _rebuildPickables() {
    this._pickables = this.stageTrack.nodes.map((n) => n.hit)
  }

  start() {
    if (this._running) return
    this._running = true
    const loop = () => {
      if (!this._running) return
      this._raf = requestAnimationFrame(loop)
      try {
        this.tick()
      } catch (err) {
        console.error('[3d] frame error', err)
      }
    }
    this._raf = requestAnimationFrame(loop)
  }

  stop() {
    this._running = false
    cancelAnimationFrame(this._raf)
  }

  dispose() {
    this.stop()
    window.removeEventListener('resize', this._onResize)
    this.canvas.removeEventListener('pointermove', this._onPointer)
    this.canvas.removeEventListener('click', this._onClick)
    this.controls.dispose()
    this.renderer.dispose()
    this.labelRenderer.domElement.remove()
  }

  resize() {
    const w = window.innerWidth
    const h = window.innerHeight
    this.camera.aspect = w / h
    this.camera.updateProjectionMatrix()
    this.renderer.setSize(w, h, false)
    this.labelRenderer.setSize(w, h)
  }

  applySnapshot(snap) {
    if (!snap) return
    const newlyDone = this.stageTrack.applyStages(snap.stages || []) || []
    this.agentField.applyAgents(snap.agents || [], snap.activity || {})
    this.dataFlow.setProgress(snap.progress || 0)

    // 丹炉世界坐标：origin 用炉身中部（波纹中心）；flyFrom 用炉口
    let furnaceMouth = null
    if (this.env.pedestal) {
      const p = this.env.pedestal.getWorldPosition(new THREE.Vector3())
      // 炉身中部，便于地面涟漪 + 炉口升高波同用
      this.danFx.setOrigin(new THREE.Vector3(p.x, Math.max(1.4, p.y + 1.6), p.z))
      furnaceMouth = p.clone()
      furnaceMouth.y += 2.5
    } else if (this.env.pavilion?.hallCenter) {
      const p = this.env.pavilion.hallCenter
      this.danFx.setOrigin(new THREE.Vector3(p.x, Math.max(1.4, p.y + 1.5), p.z))
      furnaceMouth = new THREE.Vector3(p.x, p.y + 2.2, p.z)
    }

    for (const d of newlyDone) {
      if (this._completedIds.has(d.id)) continue
      this._completedIds.add(d.id)
      console.info('[3d] stage done → spirit wave', d.id, d.label)
      this.danFx.launch({
        target: d.position,
        label: d.label,
        flyFrom: furnaceMouth,
      })
      this.audio?.playStageDone?.()
      this.onStageComplete?.(d)
    }

    // 炸炉 / 失败（新出现时响一次）
    const failIds = (snap.stages || [])
      .filter((s) => s.state === 'error' || s.state === 'failed')
      .map((s) => s.id)
      .join('|')
    if (failIds && failIds !== this._lastFailSig) {
      this.audio?.playFail?.()
    }
    this._lastFailSig = failIds

    // 炉火声势随进度
    const runningN = (snap.stages || []).filter((s) => s.state === 'running').length
    const prog = Number(snap.progress || 0)
    this.audio?.setFireIntensity?.(0.28 + prog * 0.55 + Math.min(0.2, runningN * 0.08))

    // 全流程完成 → 节点飞出旋合 → 黄金标书
    const allDone =
      snap.stages?.length > 0 &&
      snap.stages.every((s) => s.state === 'done' || s.done)
    if (allDone && !this._allDoneFired) {
      this._allDoneFired = true
      this._finaleBookShown = false
      // 屏幕前方中央区域
      const center = new THREE.Vector3(0, 6, 14)
      if (this.env.pedestal) {
        const p = this.env.pedestal.getWorldPosition(new THREE.Vector3())
        center.set(p.x, p.y + 5, p.z + 12)
      }
      this.danFx.setRoofCenter(center)
      this.stageTrack.setOrbitMode(true, center)
      this.danFx.startFinaleAscend()
      this.audio?.playFinaleRise?.()
      // 镜头推向中央，看清旋合
      this._animateCamera(new THREE.Vector3(0, 8, 28), center.clone().add(new THREE.Vector3(0, 0, 2)))
      this.onStageComplete?.({ id: '__all__', label: '全流程', all: true })
    }
    if (!allDone && this._allDoneFired) {
      this._allDoneFired = false
      this._finaleBookShown = false
      this.stageTrack.setOrbitMode(false)
      this.danFx.clearFinale()
    }

    // 演示重置时清空已完成集合
    if (snap.demo && (snap.doneCount || 0) < this._completedIds.size) {
      this._completedIds.clear()
      this._allDoneFired = false
      this._finaleBookShown = false
      this.stageTrack.setOrbitMode(false)
      this.danFx.clearFinale()
      this.onReforgeReset?.()
    }

    const active = (snap.stages || []).find((s) => s.state === 'running')
    if (active) {
      const node = this.stageTrack.getNodeByStageId(active.id)
      if (node) this.dataFlow.setActiveTarget(node.position)
    } else {
      this.dataFlow.setActiveTarget(null)
    }
    this._lastSnap = snap
  }

  /** 视角预设 — 机位差异大，切换时可见 */
  focusOverview() {
    this.focusMode = 'overview'
    this._animateCamera(new THREE.Vector3(0, 18, 62), new THREE.Vector3(0, 8, -6))
  }

  focusFront() {
    this.focusMode = 'front'
    this._animateCamera(new THREE.Vector3(0, 5, 42), new THREE.Vector3(0, 6, -4))
  }

  focusHall() {
    this.focusMode = 'hall'
    this._animateCamera(new THREE.Vector3(0, 5.5, 14), new THREE.Vector3(0, 4, -10))
  }

  focusFurnace() {
    this.focusMode = 'furnace'
    const mouth = this._furnaceMouthWorld()
    this._animateCamera(
      mouth.clone().add(new THREE.Vector3(5.5, 2.8, 6.5)),
      mouth.clone().add(new THREE.Vector3(0, -0.3, 0)),
    )
  }

  _furnaceMouthWorld() {
    if (this.env.pedestal) {
      // 九龙炉口约 y≈2.42（本地）
      const p = new THREE.Vector3(0, 2.42, 0)
      this.env.pedestal.localToWorld(p)
      return p
    }
    const h = this.env.pavilion?.hallCenter
    return h ? h.clone().add(new THREE.Vector3(0, 2.4, 0)) : new THREE.Vector3(0, 4, -10)
  }

  _furnaceRoot() {
    return this.env.pedestal
  }

  /**
   * 开炉投料仪式：镜头飞向丹炉 → 开盖 → 三宝飞入 → 合盖 → 八卦符文显现
   * @returns {Promise<void>}
   */
  playFeedRitual(opts = {}) {
    const labels = opts.labels || ['招标文件', '公司资料', 'Word 模板']
    return new Promise((resolve) => {
      if (this._feedRitual) {
        this._clearFeedRitual(true)
      }
      this.stopCameraTour?.()
      this.autoOrbit = false
      this._userDriving = true
      this.focusMode = 'feed'

      const ped = this._furnaceRoot()
      const mouth = this._furnaceMouthWorld()
      const camFrom = this.camera.position.clone()
      const tgtFrom = this.controls.target.clone()
      const camTo = mouth.clone().add(new THREE.Vector3(4.8, 2.6, 5.8))
      const tgtTo = mouth.clone().add(new THREE.Vector3(0, -0.2, 0))

      // 三份卷轴（屏幕前方生成，飞入炉口）
      this._clearFeedScrolls()
      const scrolls = []
      const colors = [0xe8c050, 0x6aefc0, 0xff8060]
      for (let i = 0; i < 3; i++) {
        const g = this._makeFeedScroll(labels[i] || `宝匣${i + 1}`, colors[i])
        const start = mouth.clone().add(new THREE.Vector3((i - 1) * 2.2, 3.2 + i * 0.15, 8 + i * 0.3))
        g.position.copy(start)
        g.scale.setScalar(0.001)
        g.visible = false
        this.scene.add(g)
        scrolls.push({
          group: g,
          start: start.clone(),
          end: mouth.clone().add(new THREE.Vector3((i - 1) * 0.15, 0.1, (i - 1) * 0.08)),
          delay: 1.55 + i * 0.55,
          dur: 1.15,
          done: false,
        })
      }
      this._feedScrolls = scrolls

      if (ped?.userData?.ritualRunes) {
        ped.userData.ritualRunes.visible = false
        ped.userData.ritualBoost = 0
        for (const m of ped.userData.runeMeshes || []) {
          if (m.material) m.material.opacity = 0
          m.scale.set(0.001, 0.001, 0.001)
        }
        for (const r of ped.userData.ritualRings || []) {
          if (r.material) r.material.opacity = 0
        }
        for (const ray of ped.userData.ritualRays || []) {
          if (ray.material) ray.material.opacity = 0
        }
      }
      ped?.userData?.setLidOpen?.(0)

      this._feedRitual = {
        t: 0,
        phase: 'approach',
        camFrom,
        camTo,
        tgtFrom,
        tgtTo,
        lidOpen: 0,
        lidTarget: 0,
        runeReveal: 0,
        resolve,
        finished: false,
      }
      this.audio?.playChime?.({ bright: true })
    })
  }

  _makeFeedScroll(label, color) {
    const g = new THREE.Group()
    const body = new THREE.Mesh(
      new THREE.BoxGeometry(0.55, 0.75, 0.08),
      new THREE.MeshStandardMaterial({
        color: 0xf5e6c8,
        roughness: 0.65,
        metalness: 0.08,
        emissive: color,
        emissiveIntensity: 0.22,
      }),
    )
    g.add(body)
    for (const y of [0.42, -0.42]) {
      const rod = new THREE.Mesh(
        new THREE.CylinderGeometry(0.05, 0.05, 0.62, 8),
        new THREE.MeshStandardMaterial({
          color,
          metalness: 0.65,
          roughness: 0.3,
          emissive: color,
          emissiveIntensity: 0.35,
        }),
      )
      rod.rotation.z = Math.PI / 2
      rod.position.y = y
      g.add(rod)
    }
    const glow = new THREE.Mesh(
      new THREE.SphereGeometry(0.55, 10, 10),
      new THREE.MeshBasicMaterial({
        color,
        transparent: true,
        opacity: 0.18,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
      }),
    )
    g.add(glow)
    // 简易标签
    const canvas = document.createElement('canvas')
    canvas.width = 256
    canvas.height = 64
    const ctx = canvas.getContext('2d')
    ctx.clearRect(0, 0, 256, 64)
    ctx.font = 'bold 32px "Noto Serif SC", "Songti SC", serif'
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    ctx.fillStyle = '#3a2010'
    ctx.fillText(String(label).slice(0, 6), 128, 34)
    const tex = new THREE.CanvasTexture(canvas)
    tex.colorSpace = THREE.SRGBColorSpace
    const tag = new THREE.Mesh(
      new THREE.PlaneGeometry(0.48, 0.12),
      new THREE.MeshBasicMaterial({ map: tex, transparent: true, depthWrite: false }),
    )
    tag.position.z = 0.05
    g.add(tag)
    return g
  }

  _clearFeedScrolls() {
    for (const s of this._feedScrolls || []) {
      this.scene.remove(s.group)
      s.group.traverse((o) => {
        o.geometry?.dispose?.()
        if (o.material) {
          o.material.map?.dispose?.()
          o.material.dispose?.()
        }
      })
    }
    this._feedScrolls = []
  }

  _clearFeedRitual(resolveEarly = false) {
    const r = this._feedRitual
    this._feedRitual = null
    this._clearFeedScrolls()
    if (resolveEarly && r && !r.finished) {
      r.finished = true
      r.resolve?.()
    }
  }

  _tickFeedRitual(dt) {
    const r = this._feedRitual
    if (!r || r.finished) return false
    r.t += dt
    const ped = this._furnaceRoot()
    const setLid = ped?.userData?.setLidOpen
    const ease = (k) => k * k * (3 - 2 * k)
    const clamp01 = (v) => Math.max(0, Math.min(1, v))
    this.controls.enabled = false
    this._userDriving = false
    this._camAnim = null

    // 镜头：飞向 → 旁观投料 → 抬高看阵
    const highCam = r.camTo.clone().add(new THREE.Vector3(-1.5, 2.2, 1.2))
    const highTgt = r.tgtTo.clone().add(new THREE.Vector3(0, -0.8, 0))
    if (r.t < 1.4) {
      const k = ease(clamp01(r.t / 1.4))
      this.camera.position.lerpVectors(r.camFrom, r.camTo, k)
      this.controls.target.lerpVectors(r.tgtFrom, r.tgtTo, k)
    } else if (r.t < 4.6) {
      this.camera.position.copy(r.camTo)
      this.controls.target.copy(r.tgtTo)
    } else {
      const k = ease(clamp01((r.t - 4.6) / 1.6))
      this.camera.position.lerpVectors(r.camTo, highCam, k)
      this.controls.target.lerpVectors(r.tgtTo, highTgt, k)
    }
    this.camera.lookAt(this.controls.target)

    // 0.9–2.0s 开盖
    if (r.t >= 0.9 && r.t < 2.0) {
      r.lidTarget = ease(clamp01((r.t - 0.9) / 1.0))
      setLid?.(r.lidTarget)
      if (ped?.userData) ped.userData.ritualBoost = Math.max(ped.userData.ritualBoost || 0, 0.25)
    } else if (r.t >= 2.0 && r.t < 3.9) {
      setLid?.(1)
    }

    // 1.55–4.0s 三宝依次飞入
    for (const s of this._feedScrolls) {
      const local = r.t - s.delay
      if (local < 0) continue
      if (!s.group.visible) {
        s.group.visible = true
        s.group.scale.setScalar(1)
        this.audio?.playUiTap?.()
      }
      if (local >= s.dur) {
        if (!s.done) {
          s.done = true
          s.group.visible = false
          this.audio?.playChime?.({ bright: false })
          if (ped?.userData) {
            ped.userData.ritualBoost = Math.min(1, (ped.userData.ritualBoost || 0) + 0.2)
          }
        }
        continue
      }
      const k = ease(clamp01(local / s.dur))
      s.group.position.lerpVectors(s.start, s.end, k)
      s.group.position.y += Math.sin(k * Math.PI) * 1.6
      const sc = Math.max(0.12, 1 - k * 0.88)
      s.group.scale.setScalar(sc)
      s.group.rotation.y = k * Math.PI * 2.2
      s.group.rotation.x = k * 0.6
    }

    // 3.9–5.0s 合盖
    if (r.t >= 3.9 && r.t < 5.1) {
      const k = ease(clamp01((r.t - 3.9) / 1.0))
      r.lidTarget = 1 - k
      setLid?.(r.lidTarget)
    } else if (r.t >= 5.1) {
      setLid?.(0)
    }

    // 4.6s 起八卦符文显现
    if (r.t >= 4.6) {
      const runes = ped?.userData?.ritualRunes
      if (runes) {
        runes.visible = true
        r.runeReveal = ease(clamp01((r.t - 4.6) / 1.4))
        if (ped.userData) {
          ped.userData.ritualBoost = Math.max(ped.userData.ritualBoost || 0, 0.55 + r.runeReveal * 0.45)
        }
        const meshes = ped.userData.runeMeshes || []
        for (let i = 0; i < meshes.length; i++) {
          const m = meshes[i]
          const rk = clamp01(r.runeReveal * 1.3 - i * 0.08)
          const s = 0.15 + rk * 0.8
          m.scale.set(s, s, 1)
          if (m.material) m.material.opacity = rk * 0.9
        }
        for (const ring of ped.userData.ritualRings || []) {
          if (ring.material) ring.material.opacity = r.runeReveal * 0.45
        }
        for (const ray of ped.userData.ritualRays || []) {
          if (ray.material) ray.material.opacity = r.runeReveal * 0.35
        }
      }
    }

    // 结束
    if (r.t >= 6.6) {
      setLid?.(0)
      if (ped?.userData) ped.userData.ritualBoost = Math.max(0.35, ped.userData.ritualBoost || 0)
      this._clearFeedScrolls()
      r.finished = true
      this._feedRitual = null
      this.controls.enabled = true
      this.audio?.playSpiritWave?.()
      r.resolve?.()
      return false
    }
    return true
  }

  focusSide() {
    this.focusMode = 'side'
    this._animateCamera(new THREE.Vector3(38, 16, 10), new THREE.Vector3(0, 8, -6))
  }

  focusTop() {
    this.focusMode = 'top'
    this._animateCamera(new THREE.Vector3(0, 58, 12), new THREE.Vector3(0, 0, 2))
  }

  focusBack() {
    this.focusMode = 'back'
    this._animateCamera(new THREE.Vector3(0, 16, -48), new THREE.Vector3(0, 8, -8))
  }

  focusActive() {
    this.focusMode = 'active'
    const snap = this._lastSnap
    const active =
      snap?.stages?.find((s) => s.state === 'running') || snap?.stages?.find((s) => s.state === 'ready')
    if (!active) {
      this.focusOverview()
      return
    }
    const pos = this.stageTrack.getPosition(active.index)
    this._animateCamera(pos.clone().add(new THREE.Vector3(0, 7, 12)), pos.clone().add(new THREE.Vector3(0, 0.5, 0)))
  }

  focusAgents() {
    this.focusMode = 'agents'
    this._animateCamera(new THREE.Vector3(0, 20, 42), new THREE.Vector3(0, 1, 18))
  }

  focusStage(index) {
    const pos = this.stageTrack.getPosition(index)
    if (!pos) return
    this.focusMode = 'stage'
    this._animateCamera(pos.clone().add(new THREE.Vector3(2, 6, 10)), pos.clone().add(new THREE.Vector3(0, 0.4, 0)))
  }

  zoomBy(factor) {
    this.autoOrbit = false
    this._userDriving = true
    this._camAnim = null
    this._camTour = null
    this.controls.enabled = true
    const dir = new THREE.Vector3().subVectors(this.camera.position, this.controls.target)
    const dist = Math.max(0.001, dir.length())
    const next = Math.min(160, Math.max(1.2, dist * factor))
    dir.setLength(next)
    this.camera.position.copy(this.controls.target).add(dir)
    this.controls.update()
  }

  /**
   * 环游：走廊深处 → 御道前行 → 入殿看丹炉 → 升空飞出 → 环视工序节点 → 总览回环
   */
  startCameraTour() {
    this.autoOrbit = true
    this._userDriving = false
    this._camAnim = null
    this.focusMode = 'tour'
    // 环游期间关掉手动控制，避免 OrbitControls 抢写相机
    this.controls.enabled = false
    // 约 400ms 内防止按钮抬起/冒泡把环游立刻打断
    this._tourLockUntil = performance.now() + 400
    const waypoints = this._buildTourWaypoints()
    if (!waypoints.length) {
      console.warn('[3d] camera tour: empty waypoints')
      return
    }
    // 从当前机位平滑飞到走廊起点，再沿航线前进
    const start = waypoints[0]
    this._camTour = {
      waypoints,
      index: 0,
      t: 0,
      fromPos: this.camera.position.clone(),
      fromTarget: this.controls.target.clone(),
      loop: true,
      startedAt: performance.now(),
    }
    console.info('[3d] camera tour start', waypoints.length, 'legs', {
      from: this.camera.position.toArray().map((n) => +n.toFixed(1)),
      first: start.pos.toArray().map((n) => +n.toFixed(1)),
    })
  }

  stopCameraTour() {
    this.autoOrbit = false
    this._camTour = null
    this._tourLockUntil = 0
    this.controls.enabled = true
  }

  _buildTourWaypoints() {
    const hall =
      this.env.pavilion?.hallCenter?.clone?.() || new THREE.Vector3(0, 1.5, -10)
    const door = this.env.pavilion?.doorPos?.clone?.() || new THREE.Vector3(0, 2, -4)
    const queue =
      this.env.pavilion?.queueOrigin?.clone?.() || new THREE.Vector3(0, 0.4, 48)
    let furnace = hall.clone().add(new THREE.Vector3(0, 2.2, 0))
    if (this.env.pedestal) {
      furnace = this.env.pedestal.getWorldPosition(new THREE.Vector3())
      furnace.y += 2.2
    }

    const midZ = (queue.z + door.z) * 0.55
    const approachZ = door.z + 10
    const nodes = this.stageTrack?.nodes || []
    const path = []

    // 1 走廊尽头仰视大殿
    path.push({
      pos: new THREE.Vector3(0.6, 3.0, Math.max(queue.z - 1, door.z + 28)),
      target: door.clone().add(new THREE.Vector3(0, 2.5, 0)),
      dur: 2.5,
    })
    // 2 沿御道缓慢前行
    path.push({
      pos: new THREE.Vector3(-0.4, 3.4, midZ),
      target: furnace.clone().add(new THREE.Vector3(0, 1, 0)),
      dur: 6.0,
    })
    // 3 接近殿门
    path.push({
      pos: new THREE.Vector3(0, 3.8, approachZ),
      target: furnace.clone().add(new THREE.Vector3(0, 0.8, 0)),
      dur: 4.5,
    })
    // 4 跨过门槛入殿
    path.push({
      pos: new THREE.Vector3(0.2, 4.0, door.z + 1.2),
      target: furnace.clone().add(new THREE.Vector3(0, 0.4, 0)),
      dur: 3.2,
    })
    // 5 殿内环视丹炉
    path.push({
      pos: new THREE.Vector3(4.2, 4.4, hall.z + 5),
      target: furnace.clone(),
      dur: 3.0,
    })
    path.push({
      pos: new THREE.Vector3(-3.6, 4.6, hall.z + 3.5),
      target: furnace.clone().add(new THREE.Vector3(0, 0.5, 0)),
      dur: 2.8,
    })
    // 6 抬升飞出大殿
    path.push({
      pos: new THREE.Vector3(2, 11, hall.z + 14),
      target: furnace.clone().add(new THREE.Vector3(0, 5, 0)),
      dur: 3.5,
    })
    path.push({
      pos: new THREE.Vector3(12, 20, 16),
      target: new THREE.Vector3(0, 14, -6),
      dur: 3.8,
    })

    // 7 依次环视工序节点（采样，避免过长）
    const n = nodes.length
    const step = n <= 8 ? 1 : Math.max(1, Math.floor(n / 10))
    for (let i = 0; i < n; i += step) {
      const npos = nodes[i].position
      const ang = (i / Math.max(1, n - 1)) * Math.PI * 1.35 - Math.PI * 0.2
      const dist = 9
      path.push({
        pos: new THREE.Vector3(
          npos.x + Math.sin(ang) * dist,
          npos.y + 4.5,
          npos.z + Math.cos(ang) * dist + 2,
        ),
        target: npos.clone().add(new THREE.Vector3(0, 0.2, 0)),
        dur: 2.2,
      })
    }

    // 8 高空总览，衔接下一段回环
    path.push({
      pos: new THREE.Vector3(-18, 22, 36),
      target: new THREE.Vector3(0, 6, -4),
      dur: 3.5,
    })
    path.push({
      pos: new THREE.Vector3(0, 16, 50),
      target: new THREE.Vector3(0, 5, -4),
      dur: 3.2,
    })

    return path
  }

  _tickCameraTour(dt) {
    const tour = this._camTour
    if (!tour || !tour.waypoints?.length) return false

    // 用真实时间推进，避免 clock delta 异常导致停滞
    const now = performance.now()
    if (tour._lastNow == null) tour._lastNow = now
    let step = (now - tour._lastNow) / 1000
    tour._lastNow = now
    if (!(step > 0) || step > 0.1) step = Math.min(0.05, Math.max(dt || 0.016, 0.008))

    if (tour.index >= tour.waypoints.length) {
      if (tour.loop) {
        tour.waypoints = this._buildTourWaypoints()
        tour.index = 0
        tour.t = 0
        tour.fromPos = this.camera.position.clone()
        tour.fromTarget = this.controls.target.clone()
        if (tour.waypoints[0]) tour.waypoints[0].dur = 5.0
      } else {
        this.stopCameraTour()
        return false
      }
    }

    const wp = tour.waypoints[tour.index]
    if (!wp?.pos || !wp?.target) return false
    const dur = Math.max(0.2, wp.dur || 2)
    tour.t += step
    const k = Math.min(1, tour.t / dur)
    const e = k * k * (3 - 2 * k)

    this.camera.position.lerpVectors(tour.fromPos, wp.pos, e)
    this.controls.target.lerpVectors(tour.fromTarget, wp.target, e)
    this.camera.lookAt(this.controls.target)

    if (k >= 1) {
      this.camera.position.copy(wp.pos)
      this.controls.target.copy(wp.target)
      tour.fromPos = wp.pos.clone()
      tour.fromTarget = wp.target.clone()
      tour.index += 1
      tour.t = 0
    }
    return true
  }

  _animateCamera(position, target) {
    this.autoOrbit = false
    this._camTour = null
    this._userDriving = true
    // 动画中仍保持 enabled，只是 tick 里不调用 controls.update
    // 用户随时可拖拽打断（pointerdown 会清空 _camAnim）
    this.controls.enabled = true
    this._camAnim = {
      fromPos: this.camera.position.clone(),
      toPos: position.clone(),
      fromTarget: this.controls.target.clone(),
      toTarget: target.clone(),
      t: 0,
      dur: 0.9,
    }
  }

  onPointerMove(e) {
    const rect = this.canvas.getBoundingClientRect()
    this.pointer.x = ((e.clientX - rect.left) / rect.width) * 2 - 1
    this.pointer.y = -((e.clientY - rect.top) / rect.height) * 2 + 1
  }

  _pick() {
    this.raycaster.setFromCamera(this.pointer, this.camera)
    let intersects = this.raycaster.intersectObjects(this._pickables, false)
    if (!intersects.length) {
      const agentHits = []
      this.agentField.root.traverse((obj) => {
        if (obj.userData?.pickType === 'agent') agentHits.push(obj)
      })
      intersects = this.raycaster.intersectObjects(agentHits, false)
    }
    if (!intersects.length) return null
    return intersects[0].object.userData
  }

  onClick() {
    const data = this._pick()
    if (!data) return
    if (data.pickType === 'stage') {
      this.focusStage(data.index)
      this.onPick?.({ type: 'stage', ...data })
    } else if (data.pickType === 'agent') {
      this.focusAgents()
      this.onPick?.({ type: 'agent', ...data })
    }
  }

  tick() {
    // 必须先 getDelta：getElapsedTime 内部会调用 getDelta，再取会得到 0
    const dt = Math.min(this.clock.getDelta(), 0.05)
    const t = this.clock.elapsedTime
    const progress = this._lastSnap?.progress || 0
    this._frame += 1

    this.env.update(t, progress)
    this.stageTrack.update(t)
    this.agentField.update(t)
    this.dataFlow.update(t)
    this.danFx?.update(dt)
    this.audio?.update?.(dt)
    // 旋合结束后弹出黄金标书
    if (this._allDoneFired && !this._finaleBookShown && this.stageTrack.getOrbitPhase?.() === 'done') {
      this._finaleBookShown = true
      const c = this.env.pedestal
        ? this.env.pedestal.getWorldPosition(new THREE.Vector3()).add(new THREE.Vector3(0, 5, 12))
        : new THREE.Vector3(0, 6, 14)
      this.danFx.showGoldenBook?.(c)
      this.audio?.playBookReveal?.()
      this.onFinaleBook?.(c)
    }

    // 投料仪式优先驱动相机
    if (this._feedRitual && !this._feedRitual.finished) {
      this._tickFeedRitual(dt)
    } else if (this._camTour && this.autoOrbit) {
      // 环游优先：不让 OrbitControls / 其它动画覆盖相机
      this._userDriving = false
      this.controls.enabled = false
      this._tickCameraTour(dt)
    } else if (this._camAnim) {
      this._camAnim.t += dt
      const k = Math.min(1, this._camAnim.t / this._camAnim.dur)
      const e = 1 - (1 - k) ** 3
      this.camera.position.lerpVectors(this._camAnim.fromPos, this._camAnim.toPos, e)
      this.controls.target.lerpVectors(this._camAnim.fromTarget, this._camAnim.toTarget, e)
      this.camera.lookAt(this.controls.target)
      if (k >= 1) {
        this.camera.position.copy(this._camAnim.toPos)
        this.controls.target.copy(this._camAnim.toTarget)
        this._camAnim = null
        this.controls.enabled = true
        this.controls.update()
      }
    } else if (this.autoOrbit && !this._userDriving) {
      // 无航线时退化为缓慢环绕
      this.controls.enabled = false
      const r = 48
      const ang = t * 0.035
      this.camera.position.x = Math.sin(ang) * r
      this.camera.position.z = Math.cos(ang) * r + 12
      this.camera.position.y = 10 + Math.sin(t * 0.2) * 1.0
      this.controls.target.set(0, 4, -2)
      this.camera.lookAt(this.controls.target)
    } else {
      this.controls.enabled = true
      this.controls.update()
    }

    this.renderer.render(this.scene, this.camera)
    // CSS2D every other frame is enough for labels
    if (this._frame % 2 === 0) {
      this.labelRenderer.render(this.scene, this.camera)
    }
  }
}
