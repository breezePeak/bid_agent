import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { CSS2DRenderer } from 'three/addons/renderers/CSS2DRenderer.js'
import { createEnvironment } from './environment.js'
import { createStageTrack } from './stageTrack.js'
import { createAgentField } from './agentField.js'
import { createDataFlow } from './dataFlow.js'

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
    labelEl.style.zIndex = '2'
    canvas.parentElement.appendChild(labelEl)

    this.scene = new THREE.Scene()
    this.camera = new THREE.PerspectiveCamera(42, window.innerWidth / window.innerHeight, 0.3, 220)
    // 长廊尽头仰视大殿
    this.camera.position.set(0, 8, 48)

    this.controls = new OrbitControls(this.camera, canvas)
    this.controls.enableDamping = true
    this.controls.dampingFactor = 0.08
    // 任意缩放
    this.controls.enableZoom = true
    this.controls.zoomSpeed = 1.35
    this.controls.minDistance = 1.2
    this.controls.maxDistance = 160
    // 360° 环绕 + 近全俯仰
    this.controls.minPolarAngle = 0.02
    this.controls.maxPolarAngle = Math.PI - 0.02
    this.controls.enablePan = true
    this.controls.panSpeed = 1.1
    this.controls.rotateSpeed = 0.85
    this.controls.screenSpacePanning = true
    this.controls.target.set(0, 4, 2)
    this.controls.update()

    this.env = createEnvironment(this.scene)
    this.stageTrack = createStageTrack(this.scene)
    const pav = this.env.pavilion
    this.agentField = createAgentField(this.scene, {
      bossStand: pav.bossStand,
      workSlots: pav.workSlots,
      queueOrigin: pav.queueOrigin,
      doorPos: pav.doorPos,
      queueSlots: pav.queueSlots,
      loungeSlots: pav.loungeSlots,
    })
    this.dataFlow = createDataFlow(this.scene, this.stageTrack.curve)

    this._rebuildPickables()

    this._onResize = () => this.resize()
    this._onPointer = (e) => this.onPointerMove(e)
    this._onClick = (e) => this.onClick(e)
    window.addEventListener('resize', this._onResize)
    canvas.addEventListener('pointermove', this._onPointer)
    canvas.addEventListener('click', this._onClick)

    this.controls.addEventListener('start', () => {
      this._userDriving = true
      // 用户拖拽/滚轮时暂停自动环游，避免抢控制
      this._camAnim = null
    })
    this.controls.addEventListener('end', () => {
      window.setTimeout(() => {
        this._userDriving = false
      }, 2500)
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
    this.stageTrack.applyStages(snap.stages || [])
    this.agentField.applyAgents(snap.agents || [], snap.activity || {})
    this.dataFlow.setProgress(snap.progress || 0)

    const active = (snap.stages || []).find((s) => s.state === 'running')
    if (active) {
      const node = this.stageTrack.getNodeByStageId(active.id)
      if (node) this.dataFlow.setActiveTarget(node.position)
    } else {
      this.dataFlow.setActiveTarget(null)
    }
    this._lastSnap = snap
  }

  /** 视角预设：自由缩放/旋转始终可用，预设只负责切机位 */
  focusOverview() {
    this.focusMode = 'overview'
    this._animateCamera(new THREE.Vector3(0, 8, 48), new THREE.Vector3(0, 4, 2))
  }

  focusFront() {
    this.focusMode = 'front'
    // 廊中红毯视角
    this._animateCamera(new THREE.Vector3(0, 3.5, 36), new THREE.Vector3(0, 3.5, 0))
  }

  focusHall() {
    this.focusMode = 'hall'
    this._animateCamera(new THREE.Vector3(0, 4.2, 10), new THREE.Vector3(0, 3, -6))
  }

  focusFurnace() {
    this.focusMode = 'furnace'
    this._animateCamera(new THREE.Vector3(3.5, 3.8, 0), new THREE.Vector3(0, 2.8, -6))
  }

  focusSide() {
    this.focusMode = 'side'
    this._animateCamera(new THREE.Vector3(28, 10, 16), new THREE.Vector3(0, 3, 8))
  }

  focusTop() {
    this.focusMode = 'top'
    this._animateCamera(new THREE.Vector3(0, 55, 12), new THREE.Vector3(0, 0, 8))
  }

  focusBack() {
    this.focusMode = 'back'
    this._animateCamera(new THREE.Vector3(0, 12, -32), new THREE.Vector3(0, 4, -4))
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
    this._animateCamera(pos.clone().add(new THREE.Vector3(0, 6, 10)), pos.clone().add(new THREE.Vector3(0, 0.5, 0)))
  }

  focusAgents() {
    this.focusMode = 'agents'
    // 俯瞰长廊列队
    this._animateCamera(new THREE.Vector3(0, 16, 40), new THREE.Vector3(0, 1, 18))
  }

  focusStage(index) {
    const pos = this.stageTrack.getPosition(index)
    if (!pos) return
    this.focusMode = 'stage'
    this._animateCamera(pos.clone().add(new THREE.Vector3(2, 5, 8)), pos.clone().add(new THREE.Vector3(0, 0.4, 0)))
  }

  zoomBy(factor) {
    // 相对当前距离缩放
    const dir = new THREE.Vector3().subVectors(this.camera.position, this.controls.target)
    const dist = dir.length()
    const next = Math.min(160, Math.max(1.2, dist * factor))
    dir.setLength(next)
    this.camera.position.copy(this.controls.target).add(dir)
    this.controls.update()
    this._userDriving = true
    this.autoOrbit = false
  }

  _animateCamera(position, target) {
    this._userDriving = true
    this._camAnim = {
      fromPos: this.camera.position.clone(),
      toPos: position.clone(),
      fromTarget: this.controls.target.clone(),
      toTarget: target.clone(),
      t: 0,
      dur: 0.85,
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
    const t = this.clock.getElapsedTime()
    const dt = Math.min(this.clock.getDelta(), 0.05)
    const progress = this._lastSnap?.progress || 0
    this._frame += 1

    this.env.update(t, progress)
    this.stageTrack.update(t)
    this.agentField.update(t)
    this.dataFlow.update(t)

    if (this._camAnim) {
      this._camAnim.t += dt
      const k = Math.min(1, this._camAnim.t / this._camAnim.dur)
      const e = 1 - (1 - k) ** 3
      this.camera.position.lerpVectors(this._camAnim.fromPos, this._camAnim.toPos, e)
      this.controls.target.lerpVectors(this._camAnim.fromTarget, this._camAnim.toTarget, e)
      if (k >= 1) this._camAnim = null
    } else if (this.autoOrbit && !this._userDriving && this.focusMode === 'overview') {
      // 环殿廊 360° 公转
      const r = 42
      const ang = t * 0.04
      this.camera.position.x = Math.sin(ang) * r
      this.camera.position.z = Math.cos(ang) * r + 8
      this.camera.position.y = 9 + Math.sin(t * 0.2) * 1.0
      this.controls.target.set(0, 4, 2)
    }

    this.controls.update()
    this.renderer.render(this.scene, this.camera)
    // CSS2D every other frame is enough for labels
    if (this._frame % 2 === 0) {
      this.labelRenderer.render(this.scene, this.camera)
    }
  }
}
