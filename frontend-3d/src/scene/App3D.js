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
    this.focusMode = 'overview' // overview | active | agents
    this._focusIndex = -1
    this.onPick = null

    this.renderer = new THREE.WebGLRenderer({
      canvas,
      antialias: true,
      alpha: false,
      powerPreference: 'high-performance',
    })
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
    this.renderer.setSize(window.innerWidth, window.innerHeight)
    this.renderer.outputColorSpace = THREE.SRGBColorSpace
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping
    this.renderer.toneMappingExposure = 1.15

    this.labelRenderer = new CSS2DRenderer()
    this.labelRenderer.setSize(window.innerWidth, window.innerHeight)
    this.labelRenderer.domElement.style.position = 'absolute'
    this.labelRenderer.domElement.style.inset = '0'
    this.labelRenderer.domElement.style.pointerEvents = 'none'
    this.labelRenderer.domElement.style.zIndex = '2'
    canvas.parentElement.appendChild(this.labelRenderer.domElement)

    this.scene = new THREE.Scene()
    this.camera = new THREE.PerspectiveCamera(48, window.innerWidth / window.innerHeight, 0.1, 200)
    this.camera.position.set(0, 14, 22)

    this.controls = new OrbitControls(this.camera, canvas)
    this.controls.enableDamping = true
    this.controls.dampingFactor = 0.06
    this.controls.minDistance = 6
    this.controls.maxDistance = 45
    this.controls.maxPolarAngle = Math.PI * 0.48
    this.controls.target.set(0, 1.5, 2)
    this.controls.update()

    this.env = createEnvironment(this.scene)
    this.stageTrack = createStageTrack(this.scene)
    this.agentField = createAgentField(this.scene)
    this.dataFlow = createDataFlow(this.scene, this.stageTrack.curve)

    this._onResize = () => this.resize()
    this._onPointer = (e) => this.onPointerMove(e)
    this._onClick = (e) => this.onClick(e)
    window.addEventListener('resize', this._onResize)
    canvas.addEventListener('pointermove', this._onPointer)
    canvas.addEventListener('click', this._onClick)

    // user interaction disables auto orbit briefly
    this.controls.addEventListener('start', () => {
      this._userDriving = true
    })
    this.controls.addEventListener('end', () => {
      setTimeout(() => {
        this._userDriving = false
      }, 2500)
    })

    this._raf = 0
    this._running = false
  }

  start() {
    if (this._running) return
    this._running = true
    const loop = () => {
      this._raf = requestAnimationFrame(loop)
      this.tick()
    }
    loop()
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
    this.renderer.setSize(w, h)
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
      this._focusIndex = active.index
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
    const active = snap?.stages?.find((s) => s.state === 'running') || snap?.stages?.find((s) => s.state === 'ready')
    if (!active) {
      this.focusOverview()
      return
    }
    const pos = this.stageTrack.getPosition(active.index)
    const cam = pos.clone().add(new THREE.Vector3(0, 6, 8))
    this._animateCamera(cam, pos.clone().add(new THREE.Vector3(0, 0.5, 0)))
  }

  focusAgents() {
    this.focusMode = 'agents'
    this._animateCamera(new THREE.Vector3(0, 8, 18), new THREE.Vector3(0, 0.5, 7))
  }

  focusStage(index) {
    const pos = this.stageTrack.getPosition(index)
    if (!pos) return
    this.focusMode = 'stage'
    const cam = pos.clone().add(new THREE.Vector3(2, 5, 7))
    this._animateCamera(cam, pos.clone().add(new THREE.Vector3(0, 0.4, 0)))
  }

  _animateCamera(position, target) {
    this._camAnim = {
      fromPos: this.camera.position.clone(),
      toPos: position.clone(),
      fromTarget: this.controls.target.clone(),
      toTarget: target.clone(),
      t: 0,
      dur: 1.1,
    }
  }

  onPointerMove(e) {
    const rect = this.canvas.getBoundingClientRect()
    this.pointer.x = ((e.clientX - rect.left) / rect.width) * 2 - 1
    this.pointer.y = -((e.clientY - rect.top) / rect.height) * 2 + 1
  }

  _pick() {
    this.raycaster.setFromCamera(this.pointer, this.camera)
    const hits = []
    for (const node of this.stageTrack.nodes) {
      hits.push(node.hit)
    }
    // agent hits are nested; traverse
    this.agentField.root.traverse((obj) => {
      if (obj.userData?.pickType === 'agent') hits.push(obj)
    })
    const intersects = this.raycaster.intersectObjects(hits, false)
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
    const dt = this.clock.getDelta()
    const progress = this._lastSnap?.progress || 0

    this.env.update(t, progress)
    this.stageTrack.update(t)
    this.agentField.update(t)
    this.dataFlow.update(t)

    // camera animation
    if (this._camAnim) {
      this._camAnim.t += dt
      const k = Math.min(1, this._camAnim.t / this._camAnim.dur)
      const e = 1 - Math.pow(1 - k, 3)
      this.camera.position.lerpVectors(this._camAnim.fromPos, this._camAnim.toPos, e)
      this.controls.target.lerpVectors(this._camAnim.fromTarget, this._camAnim.toTarget, e)
      if (k >= 1) this._camAnim = null
    } else if (this.autoOrbit && !this._userDriving && this.focusMode === 'overview') {
      const r = 22
      const ang = t * 0.08
      this.camera.position.x = Math.sin(ang) * r * 0.55
      this.camera.position.z = Math.cos(ang) * r
      this.camera.position.y = 12 + Math.sin(t * 0.3) * 0.6
      this.controls.target.set(0, 1.5, 2)
    }

    this.controls.update()
    this.renderer.render(this.scene, this.camera)
    this.labelRenderer.render(this.scene, this.camera)
  }
}
