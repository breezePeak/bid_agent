import * as THREE from 'three'
import { roleMeta, agentStatusColor } from '../config/stages.js'
import { createAgentLabel, createBossLabel } from './labels.js'

const hitGeo = new THREE.SphereGeometry(0.7, 8, 8)

function makeMat(hex, opts = {}) {
  return new THREE.MeshStandardMaterial({
    color: hex,
    metalness: opts.metalness ?? 0.12,
    roughness: opts.roughness ?? 0.55,
    emissive: opts.emissive ?? 0x000000,
    emissiveIntensity: opts.emissiveIntensity ?? 0,
  })
}

/** 唐风道士 / 掌炉真人 — 有脸有袖有冠 */
function makeAgentMesh(colorHex, isBoss = false) {
  const g = new THREE.Group()
  const color = new THREE.Color(colorHex)
  const s = isBoss ? 1.35 : 1

  const robeHex = isBoss ? 0xc42820 : 0x9a4830
  const robeDeep = isBoss ? 0x7a1410 : 0x5a2818
  const trimHex = isBoss ? 0xe8c050 : 0xd0a040
  const skinHex = 0xffe0b8
  const hairHex = 0x1a1410

  const robeMat = makeMat(robeHex, {
    emissive: color,
    emissiveIntensity: isBoss ? 0.28 : 0.18,
    roughness: 0.52,
    metalness: 0.1,
  })
  const robeDeepMat = makeMat(robeDeep, {
    emissive: color,
    emissiveIntensity: 0.1,
    roughness: 0.58,
  })
  const trimMat = makeMat(trimHex, {
    metalness: 0.62,
    roughness: 0.32,
    emissive: 0x6a4010,
    emissiveIntensity: 0.28,
  })
  const skinMat = makeMat(skinHex, { roughness: 0.62, metalness: 0.04 })
  const hairMat = makeMat(hairHex, { roughness: 0.75 })
  const inkMat = makeMat(0x1a1010, { roughness: 0.55 })
  const lipMat = makeMat(0xc05048, { roughness: 0.5, emissive: 0x401010, emissiveIntensity: 0.15 })

  // —— 下摆宽袍 ——
  const skirt = new THREE.Mesh(
    new THREE.CylinderGeometry(0.22 * s, 0.34 * s, 0.55 * s, 12),
    robeDeepMat,
  )
  skirt.position.y = 0.28 * s
  g.add(skirt)

  // 袍摆金边
  const hem = new THREE.Mesh(new THREE.TorusGeometry(0.33 * s, 0.02 * s, 6, 20), trimMat)
  hem.rotation.x = Math.PI / 2
  hem.position.y = 0.04 * s
  g.add(hem)

  // —— 上身道袍 ——
  const body = new THREE.Mesh(
    new THREE.CapsuleGeometry(0.18 * s, 0.38 * s, 5, 10),
    robeMat,
  )
  body.position.y = 0.68 * s
  g.add(body)

  // 交领
  for (const side of [-1, 1]) {
    const collar = new THREE.Mesh(new THREE.BoxGeometry(0.08 * s, 0.28 * s, 0.06 * s), trimMat)
    collar.position.set(side * 0.08 * s, 0.92 * s, 0.14 * s)
    collar.rotation.z = side * 0.35
    collar.rotation.x = -0.15
    g.add(collar)
  }

  // 腰带
  const belt = new THREE.Mesh(new THREE.CylinderGeometry(0.2 * s, 0.2 * s, 0.07 * s, 12), trimMat)
  belt.position.y = 0.48 * s
  g.add(belt)
  const buckle = new THREE.Mesh(new THREE.BoxGeometry(0.1 * s, 0.08 * s, 0.06 * s), trimMat)
  buckle.position.set(0, 0.48 * s, 0.2 * s)
  g.add(buckle)

  // —— 双袖（略张） ——
  const arms = new THREE.Group()
  arms.name = 'arms'
  for (const side of [-1, 1]) {
    const armG = new THREE.Group()
    const upper = new THREE.Mesh(
      new THREE.CapsuleGeometry(0.055 * s, 0.16 * s, 3, 6),
      robeMat,
    )
    upper.position.set(0, -0.08 * s, 0)
    upper.rotation.z = side * 0.55
    armG.add(upper)
    const sleeve = new THREE.Mesh(
      new THREE.CylinderGeometry(0.07 * s, 0.1 * s, 0.22 * s, 8),
      robeDeepMat,
    )
    sleeve.position.set(side * 0.12 * s, -0.22 * s, 0.02 * s)
    sleeve.rotation.z = side * 0.35
    armG.add(sleeve)
    const hand = new THREE.Mesh(new THREE.SphereGeometry(0.045 * s, 6, 6), skinMat)
    hand.position.set(side * 0.16 * s, -0.34 * s, 0.04 * s)
    armG.add(hand)
    armG.position.set(side * 0.22 * s, 0.82 * s, 0)
    arms.add(armG)
  }
  g.add(arms)

  // —— 头 ——
  const headG = new THREE.Group()
  headG.name = 'head'
  headG.position.y = 1.12 * s

  const head = new THREE.Mesh(new THREE.SphereGeometry(0.145 * s, 14, 12), skinMat)
  head.scale.set(1, 1.08, 0.95)
  headG.add(head)

  // 耳
  for (const side of [-1, 1]) {
    const ear = new THREE.Mesh(new THREE.SphereGeometry(0.035 * s, 6, 6), skinMat)
    ear.scale.set(0.55, 1, 0.7)
    ear.position.set(side * 0.14 * s, 0, 0)
    headG.add(ear)
  }

  // 眉
  for (const side of [-1, 1]) {
    const brow = new THREE.Mesh(new THREE.BoxGeometry(0.055 * s, 0.012 * s, 0.012 * s), hairMat)
    brow.position.set(side * 0.05 * s, 0.045 * s, 0.12 * s)
    brow.rotation.z = side * -0.15
    headG.add(brow)
  }

  // 眼白 + 瞳
  for (const side of [-1, 1]) {
    const eyeW = new THREE.Mesh(new THREE.SphereGeometry(0.028 * s, 6, 6), makeMat(0xf8f0e8, { roughness: 0.4 }))
    eyeW.scale.set(1.1, 0.7, 0.5)
    eyeW.position.set(side * 0.048 * s, 0.015 * s, 0.125 * s)
    headG.add(eyeW)
    const pupil = new THREE.Mesh(new THREE.SphereGeometry(0.014 * s, 6, 6), inkMat)
    pupil.position.set(side * 0.048 * s, 0.015 * s, 0.145 * s)
    headG.add(pupil)
    const shine = new THREE.Mesh(
      new THREE.SphereGeometry(0.005 * s, 4, 4),
      new THREE.MeshBasicMaterial({ color: 0xffffff }),
    )
    shine.position.set(side * 0.052 * s, 0.02 * s, 0.155 * s)
    headG.add(shine)
  }

  // 鼻
  const nose = new THREE.Mesh(new THREE.SphereGeometry(0.022 * s, 6, 6), skinMat)
  nose.scale.set(0.7, 1, 0.9)
  nose.position.set(0, -0.01 * s, 0.14 * s)
  headG.add(nose)

  // 嘴
  const mouth = new THREE.Mesh(new THREE.BoxGeometry(0.04 * s, 0.01 * s, 0.012 * s), lipMat)
  mouth.position.set(0, -0.05 * s, 0.13 * s)
  headG.add(mouth)

  // 颊红
  for (const side of [-1, 1]) {
    const blush = new THREE.Mesh(
      new THREE.SphereGeometry(0.03 * s, 6, 6),
      new THREE.MeshStandardMaterial({
        color: 0xff9a88,
        transparent: true,
        opacity: 0.35,
        roughness: 0.7,
        depthWrite: false,
      }),
    )
    blush.scale.set(1, 0.7, 0.4)
    blush.position.set(side * 0.09 * s, -0.02 * s, 0.1 * s)
    headG.add(blush)
  }

  // 发髻 + 道冠
  const hair = new THREE.Mesh(new THREE.SphereGeometry(0.15 * s, 12, 10), hairMat)
  hair.scale.set(1.05, 0.7, 1.05)
  hair.position.y = 0.06 * s
  headG.add(hair)

  const bun = new THREE.Mesh(new THREE.SphereGeometry(0.07 * s, 10, 8), hairMat)
  bun.position.y = 0.16 * s
  headG.add(bun)

  const crownBase = new THREE.Mesh(
    new THREE.CylinderGeometry(0.09 * s, 0.12 * s, 0.06 * s, 10),
    trimMat,
  )
  crownBase.position.y = 0.18 * s
  headG.add(crownBase)

  const crown = new THREE.Mesh(
    new THREE.ConeGeometry(0.07 * s, 0.14 * s, 8),
    trimMat,
  )
  crown.position.y = 0.28 * s
  headG.add(crown)

  if (isBoss) {
    // 掌炉：如意冠顶珠 + 长须
    const jewel = new THREE.Mesh(
      new THREE.SphereGeometry(0.04 * s, 8, 8),
      makeMat(0xff6040, {
        metalness: 0.3,
        roughness: 0.25,
        emissive: 0xff3010,
        emissiveIntensity: 0.7,
      }),
    )
    jewel.position.y = 0.36 * s
    headG.add(jewel)

    const beard = new THREE.Mesh(
      new THREE.ConeGeometry(0.08 * s, 0.28 * s, 8),
      makeMat(0xe8e0d0, { roughness: 0.7 }),
    )
    beard.position.set(0, -0.18 * s, 0.1 * s)
    beard.rotation.x = 0.25
    headG.add(beard)

    // 肩帔
    const cape = new THREE.Mesh(
      new THREE.TorusGeometry(0.28 * s, 0.05 * s, 6, 16, Math.PI),
      trimMat,
    )
    cape.rotation.x = Math.PI / 2
    cape.rotation.z = Math.PI
    cape.position.set(0, 0.95 * s, 0.02 * s)
    g.add(cape)
  }

  g.add(headG)

  // 足尖微露
  for (const side of [-1, 1]) {
    const shoe = new THREE.Mesh(
      new THREE.BoxGeometry(0.08 * s, 0.04 * s, 0.14 * s),
      makeMat(0x2a1810, { roughness: 0.7 }),
    )
    shoe.position.set(side * 0.08 * s, 0.02 * s, 0.04 * s)
    g.add(shoe)
  }

  // 脚底灵光环
  const ring = new THREE.Mesh(
    new THREE.TorusGeometry(isBoss ? 0.48 : 0.34, isBoss ? 0.028 : 0.022, 6, 24),
    new THREE.MeshBasicMaterial({
      color,
      transparent: true,
      opacity: 0.7,
      depthWrite: false,
    }),
  )
  ring.rotation.x = Math.PI / 2
  ring.position.y = 0.03
  g.add(ring)

  g.userData = { body, head, headG, arms, ring, color, light: null }
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
  const furnacePos = layout.furnacePos || layout.hallCenter || new THREE.Vector3(0, 1.2, -2)
  const workSlots = layout.workSlots || [
    new THREE.Vector3(-2.6, 0.98, -0.2),
    new THREE.Vector3(2.6, 0.98, -0.2),
    new THREE.Vector3(-2.0, 0.98, 1.6),
    new THREE.Vector3(2.0, 0.98, 1.6),
  ]
  const queueSlots = layout.queueSlots || []
  const loungeSlots = layout.loungeSlots || []
  const queueOrigin = layout.queueOrigin || new THREE.Vector3(0, 0.12, 20)

  // 面朝目标（模型正面为本地 +Z）
  function faceToward(obj, from, to) {
    const dx = to.x - from.x
    const dz = to.z - from.z
    if (dx * dx + dz * dz < 1e-6) return
    obj.rotation.y = Math.atan2(dx, dz)
  }

  // 掌炉真人 — 殿内金台，面向丹炉
  const bossPad = new THREE.Group()
  bossPad.position.copy(bossStand)
  faceToward(bossPad, bossStand, furnacePos)
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

          // 朝向：阁内面朝丹炉，排队/退朝面朝大殿
          if (status === 'running' || status === 'failed') {
            faceToward(entry.group, entry.target, furnacePos)
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
      // 身已朝丹炉；头微俯视炉口，略有观火动
      if (boss.userData.headG) {
        boss.userData.headG.rotation.y = Math.sin(t * 0.7) * 0.08
        boss.userData.headG.rotation.x = 0.18 + Math.sin(t * 1.2) * 0.04
      }
      if (boss.userData.arms) {
        boss.userData.arms.rotation.z = Math.sin(t * 1.6) * 0.05
        boss.userData.arms.rotation.x = -0.12 + Math.sin(t * 1.1) * 0.04
      }
      boss.userData.ring.scale.setScalar(1 + Math.sin(t * 2.2) * 0.05)

      for (const entry of agentMeshes.values()) {
        entry.group.position.lerp(entry.target, 0.07)
        const ud = entry.mesh.userData
        const phase = t + entry.id.length * 0.7
        if (ud.headG) {
          ud.headG.rotation.y = Math.sin(phase * 1.1) * 0.15
          ud.headG.rotation.x = Math.sin(phase * 1.5) * 0.05
        }
        if (ud.arms) {
          ud.arms.rotation.z = Math.sin(phase * 2.1) * 0.08
        }
        if (entry.status === 'running') {
          entry.group.position.y = entry.target.y + Math.sin(t * 5 + entry.id.length) * 0.04
          ud.ring.scale.setScalar(1 + Math.sin(t * 4) * 0.08)
          if (ud.arms) ud.arms.rotation.x = Math.sin(t * 6) * 0.12
        } else if (entry.status === 'queued') {
          entry.group.position.y = entry.target.y + Math.sin(phase * 1.8) * 0.015
          ud.ring.scale.setScalar(1 + Math.sin(phase * 1.5) * 0.03)
        } else {
          ud.ring.scale.setScalar(1)
        }
      }
    },
    getAgentPosition(id) {
      return agentMeshes.get(id)?.group.position.clone() || null
    },
  }
}
