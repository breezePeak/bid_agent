import * as THREE from 'three'
import { roleMeta, agentStatusColor } from '../config/stages.js'
import { createAgentLabel, createBossLabel } from './labels.js'

const bodyGeo = new THREE.CapsuleGeometry(0.16, 0.34, 4, 8)
const bossBodyGeo = new THREE.CapsuleGeometry(0.28, 0.55, 4, 8)
const headGeo = new THREE.SphereGeometry(0.14, 10, 10)
const bossHeadGeo = new THREE.SphereGeometry(0.22, 10, 10)
const ringGeo = new THREE.TorusGeometry(0.36, 0.025, 6, 20)
const bossRingGeo = new THREE.TorusGeometry(0.55, 0.03, 6, 20)
const hitGeo = new THREE.SphereGeometry(0.55, 8, 8)

function makeAgentMesh(colorHex, isBoss = false) {
  const g = new THREE.Group()
  const color = new THREE.Color(colorHex)

  // 道袍：朱 / 金
  const robeColor = isBoss ? 0xb82820 : 0x8a5030
  const body = new THREE.Mesh(
    isBoss ? bossBodyGeo : bodyGeo,
    new THREE.MeshStandardMaterial({
      color: robeColor,
      metalness: 0.25,
      roughness: 0.5,
      emissive: color,
      emissiveIntensity: isBoss ? 0.45 : 0.32,
    }),
  )
  body.position.y = isBoss ? 0.72 : 0.5
  g.add(body)

  // 衣摆
  const skirt = new THREE.Mesh(
    new THREE.CylinderGeometry(isBoss ? 0.32 : 0.2, isBoss ? 0.38 : 0.24, 0.35, 8),
    new THREE.MeshStandardMaterial({
      color: isBoss ? 0x8a1810 : 0x6a3820,
      roughness: 0.55,
      metalness: 0.15,
      emissive: color,
      emissiveIntensity: 0.15,
    }),
  )
  skirt.position.y = isBoss ? 0.28 : 0.22
  g.add(skirt)

  const head = new THREE.Mesh(
    isBoss ? bossHeadGeo : headGeo,
    new THREE.MeshStandardMaterial({
      color: 0xffe8c8,
      metalness: 0.08,
      roughness: 0.5,
      emissive: color,
      emissiveIntensity: 0.15,
    }),
  )
  head.position.y = isBoss ? 1.3 : 0.9
  g.add(head)

  // 道冠
  const crown = new THREE.Mesh(
    new THREE.CylinderGeometry(isBoss ? 0.12 : 0.08, isBoss ? 0.16 : 0.11, isBoss ? 0.18 : 0.12, 8),
    new THREE.MeshStandardMaterial({
      color: isBoss ? 0xe8c050 : 0xd4a840,
      metalness: 0.6,
      roughness: 0.35,
      emissive: 0x6a4010,
      emissiveIntensity: 0.25,
    }),
  )
  crown.position.y = isBoss ? 1.52 : 1.05
  g.add(crown)

  const ring = new THREE.Mesh(
    isBoss ? bossRingGeo : ringGeo,
    new THREE.MeshBasicMaterial({
      color,
      transparent: true,
      opacity: 0.65,
      depthWrite: false,
    }),
  )
  ring.rotation.x = Math.PI / 2
  ring.position.y = 0.04
  g.add(ring)

  g.userData = { body, head, ring, color, light: null }
  return g
}

/**
 * 掌炉真人殿内控炉；
 * running → 升殿入阁；
 * queued → 红毯两侧列队（如朝臣）；
 * done → 远端歇息
 */
export function createAgentField(scene, layout = {}) {
  const root = new THREE.Group()
  root.name = 'agentField'
  scene.add(root)

  const bossStand = layout.bossStand || new THREE.Vector3(0, 0.98, 2.6)
  const workSlots = layout.workSlots || [
    new THREE.Vector3(-2.6, 0.98, -0.2),
    new THREE.Vector3(2.6, 0.98, -0.2),
    new THREE.Vector3(-2.0, 0.98, 1.6),
    new THREE.Vector3(2.0, 0.98, 1.6),
  ]
  const queueSlots = layout.queueSlots || []
  const loungeSlots = layout.loungeSlots || []
  const queueOrigin = layout.queueOrigin || new THREE.Vector3(0, 0.12, 20)

  // 掌炉真人 — 殿内金台
  const bossPad = new THREE.Group()
  bossPad.position.copy(bossStand)
  bossPad.add(
    new THREE.Mesh(
      new THREE.CylinderGeometry(0.95, 1.05, 0.14, 18),
      new THREE.MeshStandardMaterial({
        color: 0xe8c050,
        metalness: 0.55,
        roughness: 0.35,
        emissive: 0x8b3a18,
        emissiveIntensity: 0.28,
      }),
    ),
  )
  const boss = makeAgentMesh('#e0b44a', true)
  boss.position.y = 0.08
  bossPad.add(boss)
  const bossLabel = createBossLabel('掌炉真人 · 殿内控炉')
  bossPad.add(bossLabel)
  root.add(bossPad)

  const agentMeshes = new Map()
  const MAX_VISIBLE_AGENTS = 16

  function slotPosition(status, index) {
    if (status === 'running' || status === 'failed') {
      return (workSlots[index % workSlots.length] || workSlots[0]).clone()
    }
    if (status === 'queued') {
      if (queueSlots.length) {
        return queueSlots[index % queueSlots.length].clone()
      }
      // fallback：红毯两侧
      const side = index % 2 === 0 ? -1 : 1
      const row = Math.floor(index / 2)
      return new THREE.Vector3(side * 1.85, 0.12, 10 + row * 1.35)
    }
    // done / idle
    if (loungeSlots.length) {
      return loungeSlots[index % loungeSlots.length].clone()
    }
    return new THREE.Vector3(-4 + (index % 6) * 1.2, 0.12, 22)
  }

  function ensureAgent(agent) {
    let entry = agentMeshes.get(agent.id)
    if (entry) return entry

    const meta = roleMeta(agent.role)
    const color = typeof agent.color === 'string' && agent.color.startsWith('#') ? agent.color : meta.color
    const mesh = makeAgentMesh(color, false)
    const label = createAgentLabel({
      ...agent,
      emoji: agent.emoji || meta.emoji,
      label: agent.label || meta.label,
    })
    mesh.add(label)

    const hit = new THREE.Mesh(hitGeo, new THREE.MeshBasicMaterial({ visible: false }))
    hit.position.y = 0.6
    hit.userData = { pickType: 'agent', agentId: agent.id }
    mesh.add(hit)

    mesh.position.copy(queueOrigin)
    // 面朝大殿（-Z 方向）
    mesh.rotation.y = Math.PI
    root.add(mesh)
    entry = {
      id: agent.id,
      group: mesh,
      mesh,
      label,
      status: agent.status,
      role: agent.role,
      target: queueOrigin.clone(),
      color,
    }
    agentMeshes.set(agent.id, entry)
    return entry
  }

  function removeStale(activeIds) {
    for (const [id, entry] of agentMeshes) {
      if (!activeIds.has(id)) {
        root.remove(entry.group)
        entry.group.traverse((obj) => {
          if (obj.isMesh && obj.material) {
            if (Array.isArray(obj.material)) obj.material.forEach((m) => m.dispose?.())
            else obj.material.dispose?.()
          }
        })
        agentMeshes.delete(id)
      }
    }
  }

  let lastSig = ''

  return {
    root,
    boss,
    bossLabel,
    applyAgents(agents, activity = {}) {
      const list = Array.isArray(agents) ? agents : []
      const sig =
        list.map((a) => `${a.id}:${a.status}:${a.message || ''}`).join('|') +
        `:${activity.phase_label || ''}:${activity.status || ''}`
      if (sig === lastSig) return
      lastSig = sig

      const subs = list.filter((a) => a && a.role !== 'coordinator' && !a.is_coordinator)
      const coordinator = list.find((a) => a && (a.role === 'coordinator' || a.is_coordinator))

      const bossEl = bossLabel.element
      if (bossEl) {
        const msg = coordinator?.message || activity.phase_label || '掌炉真人 · 殿内控炉'
        const node = bossEl.querySelector('.css2d-boss')
        if (node) node.textContent = msg
      }
      boss.userData.ring.material.opacity =
        !activity.status || activity.status === 'running' || coordinator?.status === 'running' ? 0.85 : 0.45

      // 排队优先展示，形成朝臣列队感
      const prioritized = [
        ...subs.filter((a) => a.status === 'queued'),
        ...subs.filter((a) => a.status === 'running'),
        ...subs.filter((a) => a.status === 'failed'),
        ...subs.filter((a) => a.status === 'done' || a.status === 'skipped'),
      ].slice(0, MAX_VISIBLE_AGENTS)

      const byStatus = { running: [], queued: [], done: [], failed: [], skipped: [] }
      for (const a of prioritized) {
        const st = a.status || 'queued'
        if (!byStatus[st]) byStatus[st] = []
        byStatus[st].push(a)
      }

      const activeIds = new Set()
      for (const [status, arr] of Object.entries(byStatus)) {
        arr.forEach((agent, idx) => {
          activeIds.add(agent.id)
          const entry = ensureAgent(agent)
          entry.status = status
          entry.target.copy(slotPosition(status, idx))
          const sc = agentStatusColor(status)
          entry.mesh.userData.ring.material.color.set(sc)
          entry.mesh.userData.body.material.emissiveIntensity = status === 'running' ? 0.7 : 0.28

          // 朝向：排队面朝大殿，阁内面朝丹炉
          if (status === 'running' || status === 'failed') {
            entry.group.rotation.y = 0
          } else {
            entry.group.rotation.y = Math.PI
          }

          const nameEl = entry.label.userData.el?.querySelector('.css2d-aname')
          if (nameEl) {
            const meta = roleMeta(agent.role)
            const where =
              status === 'running' || status === 'failed' ? '升殿' : status === 'queued' ? '列队' : '退朝'
            nameEl.textContent = agent.chapter_id
              ? `${meta.label} · ${agent.chapter_id}`
              : `${meta.label} · ${where}`
          }
        })
      }
      removeStale(activeIds)
    },
    update(t) {
      boss.position.y = 0.08 + Math.sin(t * 1.8) * 0.03
      for (const entry of agentMeshes.values()) {
        entry.group.position.lerp(entry.target, 0.07)
        if (entry.status === 'running') {
          entry.group.position.y = entry.target.y + Math.sin(t * 5 + entry.id.length) * 0.04
          entry.mesh.userData.ring.scale.setScalar(1 + Math.sin(t * 4) * 0.06)
        }
      }
    },
    getAgentPosition(id) {
      return agentMeshes.get(id)?.group.position.clone() || null
    },
  }
}
