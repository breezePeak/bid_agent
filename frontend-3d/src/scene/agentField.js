import * as THREE from 'three'
import { roleMeta, agentStatusColor } from '../config/stages.js'
import { createAgentLabel, createBossLabel } from './labels.js'

// Shared geometries
const bodyGeo = new THREE.CapsuleGeometry(0.16, 0.34, 4, 8)
const bossBodyGeo = new THREE.CapsuleGeometry(0.26, 0.5, 4, 8)
const headGeo = new THREE.SphereGeometry(0.14, 10, 10)
const bossHeadGeo = new THREE.SphereGeometry(0.2, 10, 10)
const ringGeo = new THREE.TorusGeometry(0.36, 0.025, 6, 20)
const bossRingGeo = new THREE.TorusGeometry(0.52, 0.025, 6, 20)
const deskGeo = new THREE.BoxGeometry(0.65, 0.07, 0.36)
const screenGeo = new THREE.BoxGeometry(0.32, 0.2, 0.02)
const hitGeo = new THREE.SphereGeometry(0.55, 8, 8)

function makeAgentMesh(colorHex, isBoss = false) {
  const g = new THREE.Group()
  const color = new THREE.Color(colorHex)

  const body = new THREE.Mesh(
    isBoss ? bossBodyGeo : bodyGeo,
    new THREE.MeshStandardMaterial({
      color: 0x0f172a,
      metalness: 0.5,
      roughness: 0.4,
      emissive: color,
      emissiveIntensity: 0.4,
    }),
  )
  body.position.y = isBoss ? 0.7 : 0.5
  g.add(body)

  const head = new THREE.Mesh(
    isBoss ? bossHeadGeo : headGeo,
    new THREE.MeshStandardMaterial({
      color: 0xe2e8f0,
      metalness: 0.25,
      roughness: 0.45,
      emissive: color,
      emissiveIntensity: 0.2,
    }),
  )
  head.position.y = isBoss ? 1.25 : 0.9
  g.add(head)

  const visor = new THREE.Mesh(
    new THREE.BoxGeometry(isBoss ? 0.26 : 0.16, isBoss ? 0.07 : 0.045, 0.05),
    new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.9 }),
  )
  visor.position.set(0, isBoss ? 1.26 : 0.91, isBoss ? 0.16 : 0.11)
  g.add(visor)

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

  // NO PointLight per agent — that was freezing the page

  if (!isBoss) {
    const desk = new THREE.Mesh(deskGeo, new THREE.MeshBasicMaterial({ color: 0x1e293b }))
    desk.position.set(0.32, 0.4, 0)
    g.add(desk)
    const screen = new THREE.Mesh(
      screenGeo,
      new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.85 }),
    )
    screen.position.set(0.32, 0.62, -0.07)
    g.add(screen)
  }

  g.userData = { body, head, ring, color, light: null }
  return g
}

export function createAgentField(scene) {
  const root = new THREE.Group()
  root.name = 'agentField'
  scene.add(root)

  const bossPad = new THREE.Group()
  bossPad.position.set(0, 0, 5.5)
  bossPad.add(
    new THREE.Mesh(
      new THREE.CylinderGeometry(1.5, 1.7, 0.18, 20),
      new THREE.MeshStandardMaterial({
        color: 0x111827,
        metalness: 0.7,
        roughness: 0.4,
        emissive: 0x4f46e5,
        emissiveIntensity: 0.2,
      }),
    ),
  )
  const boss = makeAgentMesh('#818cf8', true)
  boss.position.y = 0.08
  bossPad.add(boss)
  const bossLabel = createBossLabel('主 Agent · 统筹中枢')
  bossPad.add(bossLabel)
  root.add(bossPad)

  const bayMat = new THREE.MeshBasicMaterial({ color: 0x0b1220, transparent: true, opacity: 0.9 })
  const bayLeft = new THREE.Mesh(new THREE.BoxGeometry(6.5, 0.1, 3), bayMat)
  bayLeft.position.set(-6.2, 0.05, 6.2)
  root.add(bayLeft)
  const bayRight = new THREE.Mesh(new THREE.BoxGeometry(6.5, 0.1, 3), bayMat)
  bayRight.position.set(6.2, 0.05, 6.2)
  root.add(bayRight)

  const queueMesh = new THREE.Mesh(
    new THREE.BoxGeometry(9, 0.06, 1.0),
    new THREE.MeshBasicMaterial({ color: 0x0f172a }),
  )
  queueMesh.position.set(0, 0.03, 9.2)
  root.add(queueMesh)
  const loungeMesh = new THREE.Mesh(
    new THREE.BoxGeometry(7.5, 0.06, 1.2),
    new THREE.MeshBasicMaterial({ color: 0x0f172a }),
  )
  loungeMesh.position.set(0, 0.03, 11.1)
  root.add(loungeMesh)

  const agentMeshes = new Map()
  const MAX_VISIBLE_AGENTS = 12

  function slotPosition(status, index) {
    if (status === 'running') {
      const side = index % 2 === 0 ? -1 : 1
      const row = Math.floor(index / 2) % 4
      return new THREE.Vector3(side * (4.0 + (row % 2) * 1.3), 0.08, 5.3 + Math.floor(row / 2) * 1.0)
    }
    if (status === 'queued') {
      return new THREE.Vector3(-3.6 + (index % 7) * 1.1, 0.08, 9.2)
    }
    return new THREE.Vector3(-2.8 + (index % 6) * 1.05, 0.08, 11.1)
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

    root.add(mesh)
    entry = {
      id: agent.id,
      group: mesh,
      mesh,
      label,
      status: agent.status,
      role: agent.role,
      target: new THREE.Vector3(),
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
            // geometries are shared; only dispose instance materials
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
      // Stable signature to skip redundant work
      const sig = list.map((a) => `${a.id}:${a.status}:${a.message || ''}`).join('|') +
        `:${activity.phase_label || ''}:${activity.status || ''}`
      if (sig === lastSig) return
      lastSig = sig

      const subs = list.filter((a) => a && a.role !== 'coordinator' && !a.is_coordinator)
      const coordinator = list.find((a) => a && (a.role === 'coordinator' || a.is_coordinator))

      const bossEl = bossLabel.element
      if (bossEl) {
        const msg = coordinator?.message || activity.phase_label || '主 Agent · 统筹中枢'
        const node = bossEl.querySelector('.css2d-boss')
        if (node) node.textContent = msg
      }
      boss.userData.ring.material.opacity =
        !activity.status || activity.status === 'running' || coordinator?.status === 'running' ? 0.8 : 0.4

      // Cap visible agents for performance
      const prioritized = [
        ...subs.filter((a) => a.status === 'running'),
        ...subs.filter((a) => a.status === 'failed'),
        ...subs.filter((a) => a.status === 'queued'),
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
          entry.mesh.userData.body.material.emissiveIntensity = status === 'running' ? 0.7 : 0.3
          const nameEl = entry.label.userData.el?.querySelector('.css2d-aname')
          if (nameEl) {
            const meta = roleMeta(agent.role)
            nameEl.textContent = agent.chapter_id
              ? `${meta.label.replace(' Agent', '')} · ${agent.chapter_id}`
              : meta.label
          }
        })
      }
      removeStale(activeIds)
    },
    update(t) {
      boss.position.y = 0.08 + Math.sin(t * 1.8) * 0.03
      for (const entry of agentMeshes.values()) {
        entry.group.position.lerp(entry.target, 0.1)
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
