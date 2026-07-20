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
    this.autoOrbit = true
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
    this.renderer.setClearColor(0x03060f, 1)

    this.labelRenderer = new CSS2DRenderer()
    this.labelRenderer.setSize(window.innerWidth, window.innerHeight)
    const labelEl = this.labelRenderer.domElement
    labelEl.style.position = 'absolute'
    labelEl.style.inset = '0'
    labelEl.style.pointerEvents = 'none'
    labelEl.style.zIndex = '2'
    canvas.parentElement.appendChild(labelEl)

    this.scene = new THREE.Scene()
    this.camera = new THREE.PerspectiveCamera(48, window.innerWidth / window.innerHeight, 0.2, 120)
    this.camera.position.set(0, 14, 22)

    this.controls = new OrbitControls(this.camera, canvas)
    this.controls.enableDamping = true
    this.controls.dampingFactor = 0.08
    this.controls.minDistance = 6
    this.controls.maxDistance = 40
    this.controls.maxPolarAngle = Math.PI * 0.48
    this.controls.target.set(0, 1.5, 2)
    this.controls.update()

    this.env = createEnvironment(this.scene)
    this.stageTrack = createStageTrack(this.scene)
    this.agentField = createAgentField(this.scene)
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
    })
    this.controls.addEventListener('end', () => {
      window.setTimeout(() => {
        this._userDriving = false
      }, 2000)
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

  focusOverview() {
    this.focusMode = 'overview'
    this._animateCamera(new THREE.Vector3(0, 14, 22), new THREE.Vector3(0, 1.5, 2))
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
    this._animateCamera(pos.clone().add(new THREE.Vector3(0, 6, 8)), pos.clone().add(new THREE.Vector3(0, 0.5, 0)))
  }

  focusAgents() {
    this.focusMode = 'agents'
    this._animateCamera(new THREE.Vector3(0, 8, 18), new THREE.Vector3(0, 0.5, 7))
  }

  focusStage(index) {
    const pos = this.stageTrack.getPosition(index)
    if (!pos) return
    this.focusMode = 'stage'
    this._animateCamera(pos.clone().add(new THREE.Vector3(2, 5, 7)), pos.clone().add(new THREE.Vector3(0, 0.4, 0)))
  }

  _animateCamera(position, target) {
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
      const r = 22
      const ang = t * 0.06
      this.camera.position.x = Math.sin(ang) * r * 0.5
      this.camera.position.z = Math.cos(ang) * r
      this.camera.position.y = 12 + Math.sin(t * 0.25) * 0.4
      this.controls.target.set(0, 1.5, 2)
    }

    this.controls.update()
    this.renderer.render(this.scene, this.camera)
    // CSS2D every other frame is enough for labels
    if (this._frame % 2 === 0) {
      this.labelRenderer.render(this.scene, this.camera)
    }
  }
}
