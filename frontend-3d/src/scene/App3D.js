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
    this._animateCamera(new THREE.Vector3(0, 14, 52), new THREE.Vector3(0, 5, -4))
  }

  focusFront() {
    this.focusMode = 'front'
    this._animateCamera(new THREE.Vector3(0, 4, 36), new THREE.Vector3(0, 4, -2))
  }

  focusHall() {
    this.focusMode = 'hall'
    this._animateCamera(new THREE.Vector3(0, 4.5, 12), new THREE.Vector3(0, 3.2, -8))
  }

  focusFurnace() {
    this.focusMode = 'furnace'
    this._animateCamera(new THREE.Vector3(5, 4.2, 2), new THREE.Vector3(0, 3, -8))
  }

  focusSide() {
    this.focusMode = 'side'
    this._animateCamera(new THREE.Vector3(32, 12, 8), new THREE.Vector3(0, 4, -4))
  }

  focusTop() {
    this.focusMode = 'top'
    this._animateCamera(new THREE.Vector3(0, 48, 10), new THREE.Vector3(0, 0, 4))
  }

  focusBack() {
    this.focusMode = 'back'
    this._animateCamera(new THREE.Vector3(0, 14, -40), new THREE.Vector3(0, 5, -6))
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
    // Prefer stage hits; agent hits optional sparse traverse
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

    if (this._camTour && this.autoOrbit) {
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
