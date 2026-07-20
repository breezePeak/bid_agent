import * as THREE from 'three'
import { STAGE_DEFS, PHASE_BY_ID, stateColor } from '../config/stages.js'
import { createStageLabel, setStageLabelVisible } from './labels.js'

function hexColor(hex) {
  return new THREE.Color(hex)
}

const hexGeo = new THREE.CylinderGeometry(0.72, 0.85, 0.14, 6)
const coreGeo = new THREE.IcosahedronGeometry(0.32, 0)
const ringGeo = new THREE.TorusGeometry(0.95, 0.03, 6, 24)
const pulseGeo = new THREE.TorusGeometry(1.15, 0.04, 6, 28)
const hitGeo = new THREE.SphereGeometry(0.95, 8, 8)

/** 空中漂浮工序节点 · 灵光闪动 */
export function createStageTrack(scene) {
  const group = new THREE.Group()
  group.name = 'stageTrack'
  scene.add(group)

  const n = STAGE_DEFS.length
  // 工序环绕大殿上空（宇宙中漂浮）
  const radius = 20
  const arcStart = -Math.PI * 0.72
  const arcEnd = Math.PI * 0.72
  const nodes = []
  const curvePoints = []

  for (let i = 0; i < n; i++) {
    const t = n === 1 ? 0.5 : i / (n - 1)
    const angle = arcStart + (arcEnd - arcStart) * t
    const y = 12 + Math.sin(t * Math.PI) * 2.2 + (i % 3) * 0.35
    const x = Math.sin(angle) * radius
    const z = -Math.cos(angle) * radius * 0.4 - 8
    curvePoints.push(new THREE.Vector3(x, y, z))
  }
  const curve = new THREE.CatmullRomCurve3(curvePoints, false, 'catmullrom', 0.35)

  // 灵脉光带
  group.add(
    new THREE.Mesh(
      new THREE.TubeGeometry(curve, 72, 0.04, 5, false),
      new THREE.MeshBasicMaterial({ color: 0xe0b44a, transparent: true, opacity: 0.45 }),
    ),
  )

  const progressHolder = new THREE.Group()
  group.add(progressHolder)

  for (let i = 0; i < n; i++) {
    const def = STAGE_DEFS[i]
    const pos = curvePoints[i].clone()
    const phase = PHASE_BY_ID[def.phase]
    const phaseColor = hexColor(phase?.color || '#c49b4e')

    const node = new THREE.Group()
    node.position.copy(pos)
    node.userData = { stageId: def.id, command: def.command, index: i, state: 'pending', baseY: pos.y }

    const platform = new THREE.Mesh(
      hexGeo,
      new THREE.MeshStandardMaterial({
        color: 0xf5e6c8,
        metalness: 0.35,
        roughness: 0.4,
        emissive: phaseColor,
        emissiveIntensity: 0.25,
      }),
    )
    platform.rotation.y = Math.PI / 6
    node.add(platform)

    // 下方灵丝
    const cord = new THREE.Mesh(
      new THREE.CylinderGeometry(0.02, 0.02, Math.max(1.5, pos.y - 1.2), 4),
      new THREE.MeshBasicMaterial({ color: 0xe0b44a, transparent: true, opacity: 0.2 }),
    )
    cord.position.y = -(pos.y - 1.2) / 2
    node.add(cord)

    const core = new THREE.Mesh(
      coreGeo,
      new THREE.MeshStandardMaterial({
        color: phaseColor,
        metalness: 0.15,
        roughness: 0.2,
        emissive: phaseColor,
        emissiveIntensity: 0.55,
      }),
    )
    core.position.y = 0.42
    node.add(core)

    const halo = new THREE.Mesh(
      ringGeo,
      new THREE.MeshBasicMaterial({
        color: phaseColor,
        transparent: true,
        opacity: 0.35,
        depthWrite: false,
      }),
    )
    halo.rotation.x = Math.PI / 2
    halo.position.y = 0.12
    node.add(halo)

    const pulse = new THREE.Mesh(
      pulseGeo,
      new THREE.MeshBasicMaterial({
        color: 0xff8a3d,
        transparent: true,
        opacity: 0,
        depthWrite: false,
      }),
    )
    pulse.rotation.x = Math.PI / 2
    pulse.position.y = 0.2
    node.add(pulse)

    // 灵光粒子环（静态小球）
    for (let k = 0; k < 4; k++) {
      const a = (k / 4) * Math.PI * 2
      const spark = new THREE.Mesh(
        new THREE.SphereGeometry(0.06, 6, 6),
        new THREE.MeshBasicMaterial({ color: phaseColor, transparent: true, opacity: 0.7 }),
      )
      spark.position.set(Math.cos(a) * 0.7, 0.35, Math.sin(a) * 0.7)
      node.add(spark)
    }

    const label = createStageLabel(def, i)
    node.add(label)

    const hit = new THREE.Mesh(hitGeo, new THREE.MeshBasicMaterial({ visible: false }))
    hit.position.y = 0.35
    hit.userData = { pickType: 'stage', stageId: def.id, index: i }
    node.add(hit)

    group.add(node)
    nodes.push({
      def,
      group: node,
      platform,
      core,
      halo,
      pulse,
      label,
      hit,
      position: pos.clone(),
    })
  }

  function setNodeState(node, state) {
    const color = hexColor(stateColor(state))
    node.core.material.color.copy(color)
    node.core.material.emissive.copy(color)
    node.halo.material.color.copy(color)

    const card = node.label.userData.el?.querySelector('.css2d-card')
    const stateEl = node.label.userData.el?.querySelector('[data-state]')
    if (card) card.className = `css2d-card is-${state}`
    if (stateEl) {
      const map = {
        done: '成丹',
        running: '炼制',
        ready: '候火',
        blocked: '滞火',
        error: '炸炉',
        failed: '炸炉',
        pending: '静候',
      }
      stateEl.textContent = map[state] || state
    }

    // 空中节点：运行中、就绪、失败都显示标签
    const showLabel =
      state === 'running' || state === 'error' || state === 'failed' || state === 'blocked' || state === 'ready'
    setStageLabelVisible(node.label, showLabel)

    node.pulse.material.color.set(0xff8a3d)
    if (state === 'done') {
      node.core.material.emissiveIntensity = 0.85
      node.halo.material.opacity = 0.55
      node.platform.material.emissiveIntensity = 0.35
      node.pulse.material.opacity = 0
    } else if (state === 'running') {
      node.core.material.emissiveIntensity = 1.25
      node.halo.material.opacity = 0.8
      node.platform.material.emissiveIntensity = 0.5
      node.pulse.material.opacity = 0.85
    } else if (state === 'ready') {
      node.core.material.emissiveIntensity = 0.65
      node.halo.material.opacity = 0.45
      node.platform.material.emissiveIntensity = 0.28
      node.pulse.material.opacity = 0
    } else if (state === 'error' || state === 'failed' || state === 'blocked') {
      node.core.material.emissiveIntensity = 1.0
      node.halo.material.opacity = 0.7
      node.platform.material.emissiveIntensity = 0.4
      node.pulse.material.opacity = 0.4
      node.pulse.material.color.set(state === 'blocked' ? 0xd48a50 : 0xe05555)
    } else {
      node.core.material.emissiveIntensity = 0.35
      node.halo.material.opacity = 0.22
      node.platform.material.emissiveIntensity = 0.15
      node.pulse.material.opacity = 0
    }
    node.group.userData.state = state
  }

  let lastDone = -1
  let progressMesh = null

  function rebuildProgressTube(doneCount) {
    if (progressMesh) {
      progressHolder.remove(progressMesh)
      progressMesh.geometry.dispose()
      progressMesh = null
    }
    if (doneCount <= 0) return
    const maxT = Math.min(1, doneCount / Math.max(1, n - 1))
    const samples = Math.max(6, Math.floor(40 * maxT))
    const pts = []
    for (let i = 0; i <= samples; i++) pts.push(curve.getPoint(maxT * (i / samples)))
    if (pts.length < 2) return
    const subCurve = new THREE.CatmullRomCurve3(pts)
    progressMesh = new THREE.Mesh(
      new THREE.TubeGeometry(subCurve, samples, 0.06, 5, false),
      new THREE.MeshBasicMaterial({
        color: 0xd44a32,
        transparent: true,
        opacity: 0.8,
        depthWrite: false,
      }),
    )
    progressHolder.add(progressMesh)
  }

  return {
    group,
    nodes,
    curve,
    getPosition(index) {
      return nodes[index]?.position.clone() || new THREE.Vector3()
    },
    getNodeByStageId(id) {
      return nodes.find((n) => n.def.id === id)
    },
    getNodeByCommand(cmd) {
      return nodes.find((n) => n.def.command === cmd)
    },
    applyStages(stages) {
      let doneCount = 0
      for (const s of stages) {
        const node = nodes[s.index] || nodes.find((n) => n.def.id === s.id)
        if (!node) continue
        const next = s.state || (s.done ? 'done' : 'pending')
        if (node.group.userData.state !== next) setNodeState(node, next)
        if (s.done || s.state === 'done') doneCount += 1
      }
      if (doneCount !== lastDone) {
        rebuildProgressTube(doneCount)
        lastDone = doneCount
      }
    },
    update(t) {
      for (const node of nodes) {
        const baseY = node.group.userData.baseY ?? node.position.y
        const idleFloat = Math.sin(t * 1.2 + (node.group.userData.index || 0) * 0.4) * 0.12
        if (node.group.userData.state === 'running') {
          node.core.rotation.y = t * 1.2
          node.core.rotation.x = t * 0.4
          const s = 1 + Math.sin(t * 5) * 0.1
          node.pulse.scale.set(s, s, s)
          node.pulse.material.opacity = 0.5 + Math.sin(t * 6) * 0.35
          node.halo.material.opacity = 0.55 + Math.sin(t * 3) * 0.25
          node.group.position.y = baseY + idleFloat + Math.sin(t * 3.5) * 0.08
          node.platform.material.emissiveIntensity = 0.45 + Math.sin(t * 4) * 0.15
        } else {
          node.group.position.y = baseY + idleFloat
          node.core.rotation.y = t * 0.15
        }
      }
    },
  }
}
