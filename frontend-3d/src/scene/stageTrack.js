import * as THREE from 'three'
import { STAGE_DEFS, PHASE_BY_ID, stateColor } from '../config/stages.js'
import { createStageLabel } from './labels.js'

function hexColor(hex) {
  return new THREE.Color(hex)
}

/**
 * Arrange stages along a wide arc (command table layout).
 * Returns positions in world space + node meshes.
 */
export function createStageTrack(scene) {
  const group = new THREE.Group()
  group.name = 'stageTrack'
  scene.add(group)

  const n = STAGE_DEFS.length
  const radius = 14
  const arcStart = -Math.PI * 0.72
  const arcEnd = Math.PI * 0.72
  const nodes = []

  // Path curve for data flow
  const curvePoints = []
  for (let i = 0; i < n; i++) {
    const t = n === 1 ? 0.5 : i / (n - 1)
    const angle = arcStart + (arcEnd - arcStart) * t
    // slight wave in Y for visual depth
    const y = 0.35 + Math.sin(t * Math.PI) * 0.9 + (i % 3) * 0.08
    const x = Math.sin(angle) * radius
    const z = -Math.cos(angle) * radius * 0.72 + 2
    curvePoints.push(new THREE.Vector3(x, y, z))
  }
  const curve = new THREE.CatmullRomCurve3(curvePoints, false, 'catmullrom', 0.35)

  // Tube path (dim backbone)
  const tubeGeo = new THREE.TubeGeometry(curve, 120, 0.035, 8, false)
  const tubeMat = new THREE.MeshBasicMaterial({
    color: 0x1e3a5f,
    transparent: true,
    opacity: 0.55,
  })
  const tube = new THREE.Mesh(tubeGeo, tubeMat)
  group.add(tube)

  // Glow path (progress)
  const progressMat = new THREE.MeshBasicMaterial({
    color: 0x22d3ee,
    transparent: true,
    opacity: 0.0,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
  })
  // We'll rebuild progress tube on update; keep a holder
  const progressHolder = new THREE.Group()
  group.add(progressHolder)

  const hexGeo = new THREE.CylinderGeometry(0.72, 0.85, 0.18, 6)
  const pillarGeo = new THREE.CylinderGeometry(0.06, 0.1, 1, 8)
  const ringGeo = new THREE.TorusGeometry(0.95, 0.03, 8, 32)
  const coreGeo = new THREE.IcosahedronGeometry(0.28, 0)

  for (let i = 0; i < n; i++) {
    const def = STAGE_DEFS[i]
    const pos = curvePoints[i].clone()
    const phase = PHASE_BY_ID[def.phase]
    const phaseColor = hexColor(phase?.color || '#64748b')

    const node = new THREE.Group()
    node.position.copy(pos)
    node.userData = {
      stageId: def.id,
      command: def.command,
      index: i,
      state: 'pending',
      phaseColor,
    }

    const platform = new THREE.Mesh(
      hexGeo,
      new THREE.MeshStandardMaterial({
        color: 0x0f172a,
        metalness: 0.7,
        roughness: 0.35,
        emissive: phaseColor,
        emissiveIntensity: 0.12,
      }),
    )
    platform.rotation.y = Math.PI / 6
    node.add(platform)

    const pillar = new THREE.Mesh(
      pillarGeo,
      new THREE.MeshStandardMaterial({
        color: 0x1e293b,
        metalness: 0.6,
        roughness: 0.4,
        emissive: phaseColor,
        emissiveIntensity: 0.08,
      }),
    )
    pillar.position.y = -0.55
    node.add(pillar)

    // footing on ground
    const foot = new THREE.Mesh(
      new THREE.CylinderGeometry(0.2, 0.28, 0.08, 12),
      new THREE.MeshStandardMaterial({ color: 0x0b1220, metalness: 0.8, roughness: 0.4 }),
    )
    foot.position.y = -pos.y - 0.02
    node.add(foot)

    const core = new THREE.Mesh(
      coreGeo,
      new THREE.MeshStandardMaterial({
        color: phaseColor,
        metalness: 0.2,
        roughness: 0.2,
        emissive: phaseColor,
        emissiveIntensity: 0.35,
        transparent: true,
        opacity: 0.9,
      }),
    )
    core.position.y = 0.55
    node.add(core)

    const halo = new THREE.Mesh(
      ringGeo,
      new THREE.MeshBasicMaterial({
        color: phaseColor,
        transparent: true,
        opacity: 0.25,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
      }),
    )
    halo.rotation.x = Math.PI / 2
    halo.position.y = 0.12
    node.add(halo)

    // running pulse ring
    const pulse = new THREE.Mesh(
      new THREE.TorusGeometry(1.15, 0.04, 8, 48),
      new THREE.MeshBasicMaterial({
        color: 0xfbbf24,
        transparent: true,
        opacity: 0,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
      }),
    )
    pulse.rotation.x = Math.PI / 2
    pulse.position.y = 0.2
    node.add(pulse)

    const label = createStageLabel(def, i)
    node.add(label)

    // hit target
    const hit = new THREE.Mesh(
      new THREE.SphereGeometry(0.95, 12, 12),
      new THREE.MeshBasicMaterial({ visible: false }),
    )
    hit.position.y = 0.4
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

  // Phase zone markers under arc segments
  const phaseMarkers = []
  const phaseIds = [...new Set(STAGE_DEFS.map((s) => s.phase))]
  for (const pid of phaseIds) {
    const members = nodes.filter((n) => n.def.phase === pid)
    if (!members.length) continue
    const mid = members[Math.floor(members.length / 2)].position.clone()
    mid.y = 0.05
    const phase = PHASE_BY_ID[pid]
    const marker = new THREE.Mesh(
      new THREE.RingGeometry(0.5, 0.65, 32),
      new THREE.MeshBasicMaterial({
        color: hexColor(phase.color),
        transparent: true,
        opacity: 0.35,
        side: THREE.DoubleSide,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
      }),
    )
    marker.rotation.x = -Math.PI / 2
    marker.position.copy(mid)
    marker.position.y = 0.03
    group.add(marker)
    phaseMarkers.push(marker)
  }

  function setNodeState(node, state) {
    const color = hexColor(stateColor(state))
    node.core.material.color.copy(color)
    node.core.material.emissive.copy(color)
    node.halo.material.color.copy(color)

    const card = node.label.userData.el?.querySelector('.css2d-card')
    const stateEl = node.label.userData.el?.querySelector('[data-state]')
    if (card) {
      card.className = `css2d-card is-${state}`
    }
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

    node.pulse.material.color.set(0xfbbf24)
    if (state === 'done') {
      node.core.material.emissiveIntensity = 0.85
      node.halo.material.opacity = 0.55
      node.platform.material.emissiveIntensity = 0.28
      node.pulse.material.opacity = 0
    } else if (state === 'running') {
      node.core.material.emissiveIntensity = 1.2
      node.halo.material.opacity = 0.7
      node.platform.material.emissiveIntensity = 0.4
      node.pulse.material.opacity = 0.85
    } else if (state === 'ready') {
      node.core.material.emissiveIntensity = 0.55
      node.halo.material.opacity = 0.4
      node.platform.material.emissiveIntensity = 0.2
      node.pulse.material.opacity = 0
    } else if (state === 'error' || state === 'failed' || state === 'blocked') {
      node.core.material.emissiveIntensity = 1.0
      node.halo.material.opacity = 0.65
      node.platform.material.emissiveIntensity = 0.35
      node.pulse.material.opacity = 0.4
      node.pulse.material.color.set(state === 'blocked' ? 0xfb923c : 0xf87171)
    } else {
      node.core.material.emissiveIntensity = 0.2
      node.halo.material.opacity = 0.15
      node.platform.material.emissiveIntensity = 0.08
      node.pulse.material.opacity = 0
    }
    node.group.userData.state = state
  }

  function rebuildProgressTube(doneCount) {
    while (progressHolder.children.length) {
      const c = progressHolder.children.pop()
      c.geometry?.dispose()
      progressHolder.remove(c)
    }
    if (doneCount <= 0) return
    const maxT = Math.min(1, doneCount / Math.max(1, n - 1))
    const pts = []
    const samples = Math.max(8, Math.floor(80 * maxT))
    for (let i = 0; i <= samples; i++) {
      pts.push(curve.getPoint(maxT * (i / samples)))
    }
    if (pts.length < 2) return
    const subCurve = new THREE.CatmullRomCurve3(pts)
    const geo = new THREE.TubeGeometry(subCurve, samples, 0.055, 8, false)
    const mesh = new THREE.Mesh(
      geo,
      new THREE.MeshBasicMaterial({
        color: 0x22d3ee,
        transparent: true,
        opacity: 0.75,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
      }),
    )
    progressHolder.add(mesh)
  }

  let lastDone = -1

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
        setNodeState(node, s.state || (s.done ? 'done' : 'pending'))
        if (s.done || s.state === 'done') doneCount += 1
      }
      if (doneCount !== lastDone) {
        rebuildProgressTube(doneCount)
        lastDone = doneCount
      }
    },
    update(t) {
      for (const node of nodes) {
        node.core.rotation.y = t * 0.6
        node.core.rotation.x = Math.sin(t + node.def.index) * 0.2
        if (node.group.userData.state === 'running') {
          const s = 1 + Math.sin(t * 4) * 0.08
          node.pulse.scale.set(s, s, s)
          node.pulse.material.opacity = 0.45 + Math.sin(t * 5) * 0.35
          node.group.position.y = node.position.y + Math.sin(t * 3) * 0.06
        } else {
          node.group.position.y = node.position.y
        }
      }
    },
  }
}
