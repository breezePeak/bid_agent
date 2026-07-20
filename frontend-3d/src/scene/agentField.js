import * as THREE from 'three'
import { roleMeta, agentStatusColor } from '../config/stages.js'
import { createAgentLabel, createBossLabel } from './labels.js'

function makeAgentMesh(colorHex, isBoss = false) {
  const g = new THREE.Group()
  const color = new THREE.Color(colorHex)

  // body capsule-ish
  const body = new THREE.Mesh(
    new THREE.CapsuleGeometry(isBoss ? 0.28 : 0.18, isBoss ? 0.55 : 0.38, 6, 12),
    new THREE.MeshStandardMaterial({
      color: 0x0f172a,
      metalness: 0.6,
      roughness: 0.3,
      emissive: color,
      emissiveIntensity: 0.45,
    }),
  )
  body.position.y = isBoss ? 0.75 : 0.55
  g.add(body)

  const head = new THREE.Mesh(
    new THREE.SphereGeometry(isBoss ? 0.22 : 0.15, 16, 16),
    new THREE.MeshStandardMaterial({
      color: 0xe2e8f0,
      metalness: 0.3,
      roughness: 0.4,
      emissive: color,
      emissiveIntensity: 0.25,
    }),
  )
  head.position.y = isBoss ? 1.35 : 0.98
  g.add(head)

  // visor
  const visor = new THREE.Mesh(
    new THREE.BoxGeometry(isBoss ? 0.28 : 0.18, isBoss ? 0.08 : 0.05, 0.06),
    new THREE.MeshBasicMaterial({
      color,
      transparent: true,
      opacity: 0.95,
      blending: THREE.AdditiveBlending,
    }),
  )
  visor.position.set(0, isBoss ? 1.36 : 0.99, isBoss ? 0.18 : 0.12)
  g.add(visor)

  // status ring under feet
  const ring = new THREE.Mesh(
    new THREE.TorusGeometry(isBoss ? 0.55 : 0.38, 0.03, 8, 32),
    new THREE.MeshBasicMaterial({
      color,
      transparent: true,
      opacity: 0.7,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    }),
  )
  ring.rotation.x = Math.PI / 2
  ring.position.y = 0.05
  g.add(ring)

  // soft light
  const light = new THREE.PointLight(color, isBoss ? 1.4 : 0.7, isBoss ? 8 : 4, 2)
  light.position.y = 1.2
  g.add(light)

  // desk for workers
  if (!isBoss) {
    const desk = new THREE.Mesh(
      new THREE.BoxGeometry(0.7, 0.08, 0.4),
      new THREE.MeshStandardMaterial({ color: 0x1e293b, metalness: 0.7, roughness: 0.35 }),
    )
    desk.position.set(0.35, 0.45, 0)
    g.add(desk)
    const screen = new THREE.Mesh(
      new THREE.BoxGeometry(0.35, 0.22, 0.02),
      new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.85 }),
    )
    screen.position.set(0.35, 0.68, -0.08)
    g.add(screen)
  }

  g.userData = { body, head, ring, light, color }
  return g
}

export function createAgentField(scene) {
  const root = new THREE.Group()
  root.name = 'agentField'
  scene.add(root)

  // Boss platform near center, slightly front
  const bossPad = new THREE.Group()
  bossPad.position.set(0, 0, 5.5)
  const padMesh = new THREE.Mesh(
    new THREE.CylinderGeometry(1.6, 1.8, 0.2, 32),
    new THREE.MeshStandardMaterial({
      color: 0x111827,
      metalness: 0.85,
      roughness: 0.3,
      emissive: 0x4f46e5,
      emissiveIntensity: 0.25,
    }),
  )
  bossPad.add(padMesh)
  const boss = makeAgentMesh('#818cf8', true)
  boss.position.y = 0.1
  bossPad.add(boss)
  const bossLabel = createBossLabel('主 Agent · 统筹中枢')
  bossPad.add(bossLabel)
  root.add(bossPad)

  // Work bay platforms (left and right of boss)
  const bayLeft = new THREE.Mesh(
    new THREE.BoxGeometry(7, 0.12, 3.2),
    new THREE.MeshStandardMaterial({
      color: 0x0b1220,
      metalness: 0.8,
      roughness: 0.35,
      emissive: 0x0ea5e9,
      emissiveIntensity: 0.08,
    }),
  )
  bayLeft.position.set(-6.5, 0.06, 6.2)
  root.add(bayLeft)

  const bayRight = bayLeft.clone()
  bayRight.position.set(6.5, 0.06, 6.2)
  bayRight.material = bayLeft.material.clone()
  bayRight.material.emissive = new THREE.Color(0xa855f7)
  root.add(bayRight)

  // Queue strip
  const queueStrip = new THREE.Mesh(
    new THREE.BoxGeometry(10, 0.08, 1.2),
    new THREE.MeshStandardMaterial({
      color: 0x0f172a,
      metalness: 0.6,
      roughness: 0.45,
      emissive: 0x64748b,
      emissiveIntensity: 0.1,
    }),
  )
  queueStrip.position.set(0, 0.04, 9.2)
  root.add(queueStrip)

  // Lounge strip
  const lounge = new THREE.Mesh(
    new THREE.BoxGeometry(8, 0.08, 1.4),
    new THREE.MeshStandardMaterial({
      color: 0x0f172a,
      metalness: 0.6,
      roughness: 0.45,
      emissive: 0x34d399,
      emissiveIntensity: 0.08,
    }),
  )
  lounge.position.set(0, 0.04, 11.2)
  root.add(lounge)

  const agentMeshes = new Map() // id -> { group, mesh, label, status, role }

  function slotPosition(status, index, total) {
    if (status === 'running') {
      // work bay seats
      const perRow = 4
      const side = index % 2 === 0 ? -1 : 1
      const row = Math.floor(index / 2) % perRow
      const x = side * (4.2 + (row % 2) * 1.4)
      const z = 5.4 + Math.floor(row / 2) * 1.1
      return new THREE.Vector3(x, 0.1, z)
    }
    if (status === 'queued') {
      const x = -4 + (index % 8) * 1.15
      const z = 9.2
      return new THREE.Vector3(x, 0.1, z)
    }
    // done / failed / skipped → lounge
    const x = -3.2 + (index % 7) * 1.1
    const z = 11.2 + Math.floor(index / 7) * 0.9
    return new THREE.Vector3(x, 0.1, z)
  }

  function ensureAgent(agent) {
    let entry = agentMeshes.get(agent.id)
    if (entry) return entry

    const meta = roleMeta(agent.role)
    const color = agent.color && agent.color.startsWith('#') ? agent.color : meta.color
    const mesh = makeAgentMesh(color, false)
    const label = createAgentLabel({
      ...agent,
      emoji: agent.emoji || meta.emoji,
      label: agent.label || meta.label,
    })
    mesh.add(label)

    const hit = new THREE.Mesh(
      new THREE.SphereGeometry(0.6, 10, 10),
      new THREE.MeshBasicMaterial({ visible: false }),
    )
    hit.position.y = 0.7
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
        agentMeshes.delete(id)
      }
    }
  }

  return {
    root,
    boss,
    bossLabel,
    applyAgents(agents, activity = {}) {
      const list = Array.isArray(agents) ? agents : []
      const subs = list.filter((a) => a && a.role !== 'coordinator' && !a.is_coordinator)
      const coordinator = list.find((a) => a && (a.role === 'coordinator' || a.is_coordinator))

      // boss message
      const bossEl = bossLabel.element
      if (bossEl) {
        const msg = coordinator?.message || activity.phase_label || '主 Agent · 统筹中枢'
        bossEl.querySelector('.css2d-boss').textContent = msg
      }
      const bossRunning = !activity.status || activity.status === 'running' || coordinator?.status === 'running'
      boss.userData.light.intensity = bossRunning ? 1.6 : 0.8
      boss.userData.ring.material.opacity = bossRunning ? 0.85 : 0.4

      const byStatus = { running: [], queued: [], done: [], failed: [], skipped: [] }
      for (const a of subs) {
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
          entry.target.copy(slotPosition(status, idx, arr.length))
          const sc = agentStatusColor(status)
          entry.mesh.userData.ring.material.color.set(sc)
          entry.mesh.userData.light.color.set(sc)
          entry.mesh.userData.body.material.emissiveIntensity = status === 'running' ? 0.75 : 0.35
          // update label text
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
      // boss idle animation
      boss.position.y = 0.1 + Math.sin(t * 2) * 0.04
      boss.userData.head.rotation.y = Math.sin(t * 1.5) * 0.15

      for (const entry of agentMeshes.values()) {
        // smooth move to target
        entry.group.position.lerp(entry.target, 0.08)
        if (entry.status === 'running') {
          entry.group.position.y = entry.target.y + Math.sin(t * 6 + entry.id.length) * 0.05
          entry.mesh.userData.head.rotation.z = Math.sin(t * 10) * 0.05
          // typing bounce
          entry.mesh.userData.ring.scale.setScalar(1 + Math.sin(t * 5) * 0.08)
        } else if (entry.status === 'queued') {
          entry.group.rotation.y = Math.sin(t * 1.2 + entry.id.length) * 0.2
        } else if (entry.status === 'done') {
          entry.group.rotation.y = t * 0.15
        } else if (entry.status === 'failed') {
          entry.group.position.x = entry.target.x + Math.sin(t * 12) * 0.04
        }
      }
    },
    getAgentPosition(id) {
      return agentMeshes.get(id)?.group.position.clone() || null
    },
  }
}
