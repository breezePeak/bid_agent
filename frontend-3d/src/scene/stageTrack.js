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
  // 工序环绕大殿上空：中间最高、两端回落（拱形层次）
  const radius = 20
  const arcStart = -Math.PI * 0.72
  const arcEnd = Math.PI * 0.72
  // 按阶段给拱形基准：prepare/deliver 低，write 最高
  const PHASE_ARCH = {
    prepare: 0.05,
    analyze: 0.35,
    plan: 0.7,
    write: 1.0,
    quality: 0.55,
    deliver: 0.12,
  }
  const phaseSlot = {}
  for (const def of STAGE_DEFS) {
    phaseSlot[def.phase] = (phaseSlot[def.phase] || 0) + 1
  }
  const phaseSeen = {}
  const nodes = []
  const curvePoints = []
  const yBase = 9.2
  const yPeak = 7.8

  for (let i = 0; i < n; i++) {
    const def = STAGE_DEFS[i]
    const t = n === 1 ? 0.5 : i / (n - 1)
    const angle = arcStart + (arcEnd - arcStart) * t
    const slot = phaseSeen[def.phase] || 0
    phaseSeen[def.phase] = slot + 1
    const inPhase = phaseSlot[def.phase] || 1
    // 同阶段内轻微错落
    const within = inPhase === 1 ? 0 : (slot / (inPhase - 1) - 0.5) * 0.85
    // 全局 sin 拱 + 阶段权重，中段高、两端低
    const arch = Math.sin(t * Math.PI)
    const phaseW = PHASE_ARCH[def.phase] ?? 0.5
    const y = yBase + yPeak * (0.35 * arch + 0.65 * phaseW) + within
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

    // 丹药核心：仅完成后显示
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
    core.visible = false
    node.add(core)

    const halo = new THREE.Mesh(
      ringGeo,
      new THREE.MeshBasicMaterial({
        color: phaseColor,
        transparent: true,
        opacity: 0.12,
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

    // 灵光粒子：仅完成后显示
    const sparks = []
    for (let k = 0; k < 4; k++) {
      const a = (k / 4) * Math.PI * 2
      const spark = new THREE.Mesh(
        new THREE.SphereGeometry(0.06, 6, 6),
        new THREE.MeshBasicMaterial({ color: phaseColor, transparent: true, opacity: 0.75 }),
      )
      spark.position.set(Math.cos(a) * 0.7, 0.35, Math.sin(a) * 0.7)
      spark.visible = false
      node.add(spark)
      sparks.push(spark)
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
      sparks,
      label,
      hit,
      position: pos.clone(),
      phaseColor,
    })
  }

  function setNodeState(node, state) {
    const color = hexColor(stateColor(state))
    const phaseCol = node.phaseColor || color
    node.core.material.color.copy(state === 'done' ? phaseCol : color)
    node.core.material.emissive.copy(state === 'done' ? phaseCol : color)
    node.halo.material.color.copy(state === 'done' ? phaseCol : color)

    const card = node.label.userData.el?.querySelector('.css2d-card')
    const nameEl = node.label.userData.el?.querySelector('.css2d-name')
    const stateEl = node.label.userData.el?.querySelector('[data-state]')
    if (card) card.className = `css2d-card is-${state}`
    // 默认只显示标号；仅执行中 / 失败 / 阻塞 才显示名称与状态
    const showDetail =
      state === 'running' || state === 'error' || state === 'failed' || state === 'blocked'
    if (nameEl) nameEl.style.display = showDetail ? '' : 'none'
    if (stateEl) {
      const map = {
        done: '完成',
        running: '进行中',
        ready: '就绪',
        blocked: '阻塞',
        error: '失败',
        failed: '失败',
        pending: '等待',
      }
      stateEl.textContent = map[state] || state
      stateEl.style.display = showDetail ? '' : 'none'
    }
    if (card) card.classList.toggle('is-compact', !showDetail)

    // 始终显示标号；执行中/异常显示全名
    setStageLabelVisible(node.label, true)

    const showDan = state === 'done'
    node.core.visible = showDan
    for (const s of node.sparks || []) {
      s.visible = showDan
      if (showDan) s.material.color.copy(phaseCol)
    }

    node.pulse.material.color.set(0xff8a3d)
    if (state === 'done') {
      // 完成：彩色丹药 + 亮环
      node.core.material.emissiveIntensity = 1.1
      node.halo.material.opacity = 0.65
      node.platform.material.emissiveIntensity = 0.45
      node.platform.material.emissive.copy(phaseCol)
      node.pulse.material.opacity = 0
    } else if (state === 'running') {
      // 进行中：无丹药，仅台座脉冲
      node.core.visible = false
      node.halo.material.opacity = 0.55
      node.platform.material.emissiveIntensity = 0.4
      node.platform.material.emissive.copy(color)
      node.pulse.material.opacity = 0.7
    } else if (state === 'ready') {
      node.core.visible = false
      node.halo.material.opacity = 0.25
      node.platform.material.emissiveIntensity = 0.12
      node.platform.material.emissive.setHex(0x4a4030)
      node.pulse.material.opacity = 0
    } else if (state === 'error' || state === 'failed' || state === 'blocked') {
      node.core.visible = false
      node.halo.material.opacity = 0.45
      node.platform.material.emissiveIntensity = 0.3
      node.platform.material.emissive.copy(color)
      node.pulse.material.opacity = 0.35
      node.pulse.material.color.set(state === 'blocked' ? 0xd48a50 : 0xe05555)
    } else {
      // 未完成：暗台、无丹药
      node.core.visible = false
      node.halo.material.opacity = 0.1
      node.platform.material.emissiveIntensity = 0.04
      node.platform.material.emissive.setHex(0x2a2830)
      node.platform.material.color.setHex(0x6a6870)
      node.pulse.material.opacity = 0
    }

    // 完成台座恢复亮色
    if (state === 'done') {
      node.platform.material.color.setHex(0xf5e6c8)
    } else if (state === 'running') {
      node.platform.material.color.setHex(0xe8d4a8)
    }

    node.group.userData.state = state
  }

  let lastDone = -1
  let progressMesh = null
  let orbitMode = false
  let orbitT0 = null
  let orbitPhase = 'orbit'
  let hasAppliedOnce = false
  const orbitCenter = new THREE.Vector3(0, 10, -6)

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
      const newlyDone = []
      for (const s of stages) {
        const node = nodes[s.index] || nodes.find((n) => n.def.id === s.id)
        if (!node) continue
        const next = s.state || (s.done ? 'done' : 'pending')
        const prev = node.group.userData.state
        if (prev !== next) {
          setNodeState(node, next)
          // 运行中→完成 才触发飞升特效（避免首屏已完成刷屏）
          if (hasAppliedOnce && next === 'done' && prev !== 'done') {
            newlyDone.push({
              id: node.def.id,
              label: node.def.label || s.label || '',
              index: node.group.userData.index ?? s.index,
              position: node.position.clone(),
            })
          }
        }
        if (s.done || s.state === 'done') doneCount += 1
      }
      if (doneCount !== lastDone) {
        rebuildProgressTube(doneCount)
        lastDone = doneCount
      }
      hasAppliedOnce = true
      return newlyDone
    },
    /**
     * 终局：金丹飞到屏幕中央围成圆 → 快速旋转 → 合并成标书
     * phase: fly | spin | merge | done
     */
    setOrbitMode(on, center = null) {
      orbitMode = Boolean(on)
      if (center) orbitCenter.copy(center)
      orbitT0 = null
      orbitPhase = 'fly'
      if (!orbitMode) {
        for (const node of nodes) {
          node.group.visible = true
          node.group.position.copy(node.position)
          node.group.scale.setScalar(1)
          // 恢复平台可见
          if (node.platform) node.platform.visible = true
          if (node.halo) node.halo.visible = true
          if (node.pulse) node.pulse.visible = true
        }
      } else {
        for (const node of nodes) {
          // 只飞金丹：隐藏台座/环，只留 core
          node.core.visible = true
          if (node.platform) node.platform.visible = false
          if (node.halo) node.halo.visible = false
          if (node.pulse) node.pulse.visible = false
          for (const s of node.sparks || []) s.visible = false
          setStageLabelVisible(node.label, false)
        }
      }
    },
    isOrbitMode() {
      return orbitMode
    },
    getOrbitPhase() {
      return orbitPhase
    },
    update(t) {
      if (orbitMode) {
        if (orbitT0 == null) orbitT0 = t
        const elapsed = t - orbitT0
        const nCount = nodes.length
        const cx = orbitCenter.x
        const cy = orbitCenter.y + 0.8
        const cz = orbitCenter.z

        // 0~0.7s 飞入成圆；0.7~2.2s 快速旋转；2.2~2.9s 聚拢；之后 done
        if (elapsed < 0.7) {
          orbitPhase = 'fly'
          const k = elapsed / 0.7
          const e = 1 - (1 - k) ** 2
          const r = 5.5
          for (let i = 0; i < nCount; i++) {
            const node = nodes[i]
            const a = (i / nCount) * Math.PI * 2
            const tx = cx + Math.cos(a) * r
            const ty = cy
            const tz = cz + Math.sin(a) * r
            node.group.position.lerpVectors(node.position, new THREE.Vector3(tx, ty, tz), e)
            node.group.scale.setScalar(1.1)
            node.core.rotation.y = elapsed * 6
            node.core.material.emissiveIntensity = 1.3
          }
        } else if (elapsed < 2.2) {
          orbitPhase = 'spin'
          const spinT = elapsed - 0.7
          // 越转越快
          const speed = 2.5 + spinT * spinT * 6
          const r = 5.5 - spinT * 0.4
          for (let i = 0; i < nCount; i++) {
            const node = nodes[i]
            const a = spinT * speed + (i / nCount) * Math.PI * 2
            node.group.position.set(
              cx + Math.cos(a) * r,
              cy + Math.sin(spinT * 4 + i) * 0.25,
              cz + Math.sin(a) * r,
            )
            node.group.scale.setScalar(1.15)
            node.core.rotation.y = elapsed * 10
            node.core.material.emissiveIntensity = 1.5
          }
        } else if (elapsed < 2.9) {
          orbitPhase = 'merge'
          const k = (elapsed - 2.2) / 0.7
          const e = k * k
          const speed = 12 + k * 25
          const r = 5 * (1 - e) + 0.12
          for (let i = 0; i < nCount; i++) {
            const node = nodes[i]
            const a = elapsed * speed + (i / nCount) * Math.PI * 2
            node.group.position.set(
              cx + Math.cos(a) * r,
              cy + Math.sin(a * 2) * r * 0.2,
              cz + Math.sin(a) * r,
            )
            node.group.scale.setScalar(1.2 * (1 - e * 0.9))
            node.core.material.emissiveIntensity = 1.8
          }
        } else {
          orbitPhase = 'done'
          for (const node of nodes) {
            node.group.visible = false
          }
        }
        return
      }

      for (const node of nodes) {
        const baseY = node.group.userData.baseY ?? node.position.y
        const idleFloat = Math.sin(t * 1.2 + (node.group.userData.index || 0) * 0.4) * 0.12
        const st = node.group.userData.state
        if (st === 'running') {
          const s = 1 + Math.sin(t * 5) * 0.1
          node.pulse.scale.set(s, s, s)
          node.pulse.material.opacity = 0.5 + Math.sin(t * 6) * 0.3
          node.halo.material.opacity = 0.45 + Math.sin(t * 3) * 0.15
          node.group.position.y = baseY + idleFloat + Math.sin(t * 3.5) * 0.08
          node.platform.material.emissiveIntensity = 0.4 + Math.sin(t * 4) * 0.12
        } else if (st === 'done' && node.core.visible) {
          node.group.position.y = baseY + idleFloat * 0.6
          node.core.rotation.y = t * 0.4
        } else {
          node.group.position.y = baseY + idleFloat * 0.4
        }
      }
    },
  }
}
