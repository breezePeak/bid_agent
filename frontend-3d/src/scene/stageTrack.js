import * as THREE from 'three'
import { STAGE_DEFS, PHASE_BY_ID, stateColor } from '../config/stages.js'
import { createStageLabel, setStageLabelVisible } from './labels.js'

function hexColor(hex) {
  return new THREE.Color(hex)
}

/** Shared geometries — do not clone heavy meshes per node. */
const hexGeo = new THREE.CylinderGeometry(0.65, 0.78, 0.16, 6)
const pillarGeo = new THREE.CylinderGeometry(0.05, 0.09, 0.9, 6)
const ringGeo = new THREE.TorusGeometry(0.88, 0.025, 6, 24)
const coreGeo = new THREE.IcosahedronGeometry(0.24, 0)
const pulseGeo = new THREE.TorusGeometry(1.05, 0.035, 6, 28)
const hitGeo = new THREE.SphereGeometry(0.85, 8, 8)
const footGeo = new THREE.CylinderGeometry(0.16, 0.22, 0.06, 8)

export function createStageTrack(scene) {
  const group = new THREE.Group()
  group.name = 'stageTrack'
  scene.add(group)

  const n = STAGE_DEFS.length
  const radius = 14
  const arcStart = -Math.PI * 0.72
  const arcEnd = Math.PI * 0.72
  const nodes = []
  const curvePoints = []

  for (let i = 0; i < n; i++) {
    const t = n === 1 ? 0.5 : i / (n - 1)
    const angle = arcStart + (arcEnd - arcStart) * t
    const y = 0.35 + Math.sin(t * Math.PI) * 0.85
    const x = Math.sin(angle) * radius
    const z = -Math.cos(angle) * radius * 0.72 + 2
    curvePoints.push(new THREE.Vector3(x, y, z))
  }
  const curve = new THREE.CatmullRomCurve3(curvePoints, false, 'catmullrom', 0.35)

  // Backbone tube — lower segments
  group.add(
    new THREE.Mesh(
      new THREE.TubeGeometry(curve, 64, 0.03, 5, false),
      new THREE.MeshBasicMaterial({ color: 0x1e3a5f, transparent: true, opacity: 0.5 }),
    ),
  )

  const progressHolder = new THREE.Group()
  group.add(progressHolder)

  for (let i = 0; i < n; i++) {
    const def = STAGE_DEFS[i]
    const pos = curvePoints[i].clone()
    const phase = PHASE_BY_ID[def.phase]
    const phaseColor = hexColor(phase?.color || '#64748b')

    const node = new THREE.Group()
    node.position.copy(pos)
    node.userData = { stageId: def.id, command: def.command, index: i, state: 'pending' }

    const platform = new THREE.Mesh(
      hexGeo,
      new THREE.MeshStandardMaterial({
        color: 0x0f172a,
        metalness: 0.55,
        roughness: 0.45,
        emissive: phaseColor,
        emissiveIntensity: 0.1,
      }),
    )
    platform.rotation.y = Math.PI / 6
    node.add(platform)

    const pillar = new THREE.Mesh(
      pillarGeo,
      new THREE.MeshBasicMaterial({ color: 0x1e293b }),
    )
    pillar.position.y = -0.5
    node.add(pillar)

    const foot = new THREE.Mesh(footGeo, new THREE.MeshBasicMaterial({ color: 0x0b1220 }))
    foot.position.y = -pos.y - 0.02
    node.add(foot)

    const core = new THREE.Mesh(
      coreGeo,
      new THREE.MeshStandardMaterial({
        color: phaseColor,
        metalness: 0.2,
        roughness: 0.25,
        emissive: phaseColor,
        emissiveIntensity: 0.3,
      }),
    )
    core.position.y = 0.48
    node.add(core)

    const halo = new THREE.Mesh(
      ringGeo,
      new THREE.MeshBasicMaterial({
        color: phaseColor,
        transparent: true,
        opacity: 0.22,
        depthWrite: false,
      }),
    )
    halo.rotation.x = Math.PI / 2
    halo.position.y = 0.1
    node.add(halo)

    const pulse = new THREE.Mesh(
      pulseGeo,
      new THREE.MeshBasicMaterial({
        color: 0xfbbf24,
        transparent: true,
        opacity: 0,
        depthWrite: false,
      }),
    )
    pulse.rotation.x = Math.PI / 2
    pulse.position.y = 0.18
    node.add(pulse)

    // Labels hidden by default (only running / selected shown)
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
        done: '完成',
        running: '执行中',
        ready: '就绪',
        blocked: '阻塞',
        error: '失败',
        failed: '失败',
        pending: '等待',
      }
      stateEl.textContent = map[state] || state
    }

    // Only show labels for active / error / ready (cuts CSS2D cost massively)
    const showLabel = state === 'running' || state === 'error' || state === 'failed' || state === 'blocked'
    setStageLabelVisible(node.label, showLabel)

    node.pulse.material.color.set(0xfbbf24)
    if (state === 'done') {
      node.core.material.emissiveIntensity = 0.75
      node.halo.material.opacity = 0.5
      node.platform.material.emissiveIntensity = 0.22
      node.pulse.material.opacity = 0
    } else if (state === 'running') {
      node.core.material.emissiveIntensity = 1.0
      node.halo.material.opacity = 0.65
      node.platform.material.emissiveIntensity = 0.35
      node.pulse.material.opacity = 0.8
    } else if (state === 'ready') {
      node.core.material.emissiveIntensity = 0.45
      node.halo.material.opacity = 0.35
      node.platform.material.emissiveIntensity = 0.15
      node.pulse.material.opacity = 0
    } else if (state === 'error' || state === 'failed' || state === 'blocked') {
      node.core.material.emissiveIntensity = 0.9
      node.halo.material.opacity = 0.6
      node.platform.material.emissiveIntensity = 0.3
      node.pulse.material.opacity = 0.35
      node.pulse.material.color.set(state === 'blocked' ? 0xfb923c : 0xf87171)
    } else {
      node.core.material.emissiveIntensity = 0.15
      node.halo.material.opacity = 0.12
      node.platform.material.emissiveIntensity = 0.06
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
      new THREE.TubeGeometry(subCurve, samples, 0.05, 5, false),
      new THREE.MeshBasicMaterial({
        color: 0x22d3ee,
        transparent: true,
        opacity: 0.7,
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
        if (node.group.userData.state === 'running') {
          node.core.rotation.y = t * 0.8
          const s = 1 + Math.sin(t * 4) * 0.07
          node.pulse.scale.set(s, s, s)
          node.pulse.material.opacity = 0.45 + Math.sin(t * 5) * 0.3
          node.group.position.y = node.position.y + Math.sin(t * 3) * 0.05
        } else {
          // keep cores still when idle (save CPU)
          node.group.position.y = node.position.y
        }
      }
    },
  }
}
