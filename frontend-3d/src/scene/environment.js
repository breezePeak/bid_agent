import * as THREE from 'three'
import { createPavilion } from './pavilion.js'

/** 飘渺宇宙背景 + 唐风丹殿长廊 */
export function createEnvironment(scene) {
  // 深空宇宙
  scene.background = new THREE.Color(0x060818)
  scene.fog = new THREE.FogExp2(0x0a1028, 0.008)

  scene.add(new THREE.AmbientLight(0x6a7aaa, 0.55))
  const key = new THREE.DirectionalLight(0xd0e0ff, 0.95)
  key.position.set(12, 26, 18)
  scene.add(key)
  const fill = new THREE.DirectionalLight(0x6080c0, 0.35)
  fill.position.set(-14, 10, -8)
  scene.add(fill)
  const rim = new THREE.DirectionalLight(0xff8060, 0.4)
  rim.position.set(-6, 8, 14)
  scene.add(rim)
  // 宫灯暖光补
  const warm = new THREE.DirectionalLight(0xffb070, 0.35)
  warm.position.set(0, 6, 20)
  scene.add(warm)
  scene.add(new THREE.HemisphereLight(0x304878, 0x0a0818, 0.45))

  // 星空
  const starCount = 1200
  const starGeo = new THREE.BufferGeometry()
  const positions = new Float32Array(starCount * 3)
  const colors = new Float32Array(starCount * 3)
  for (let i = 0; i < starCount; i++) {
    const r = 45 + Math.random() * 90
    const theta = Math.random() * Math.PI * 2
    const phi = Math.acos(2 * Math.random() - 1)
    positions[i * 3] = r * Math.sin(phi) * Math.cos(theta)
    positions[i * 3 + 1] = r * Math.cos(phi) * 0.65 + 8
    positions[i * 3 + 2] = r * Math.sin(phi) * Math.sin(theta)
    // 蓝白金星
    const c = Math.random()
    if (c > 0.85) {
      colors[i * 3] = 1
      colors[i * 3 + 1] = 0.85
      colors[i * 3 + 2] = 0.55
    } else if (c > 0.5) {
      colors[i * 3] = 0.7
      colors[i * 3 + 1] = 0.85
      colors[i * 3 + 2] = 1
    } else {
      colors[i * 3] = 0.9
      colors[i * 3 + 1] = 0.92
      colors[i * 3 + 2] = 1
    }
  }
  starGeo.setAttribute('position', new THREE.BufferAttribute(positions, 3))
  starGeo.setAttribute('color', new THREE.BufferAttribute(colors, 3))
  const stars = new THREE.Points(
    starGeo,
    new THREE.PointsMaterial({
      size: 0.14,
      vertexColors: true,
      transparent: true,
      opacity: 0.9,
      depthWrite: false,
      sizeAttenuation: true,
    }),
  )
  scene.add(stars)

  // 星云带（大而淡的粒子团）
  const nebulaCount = 200
  const nebPos = new Float32Array(nebulaCount * 3)
  const nebCol = new Float32Array(nebulaCount * 3)
  for (let i = 0; i < nebulaCount; i++) {
    nebPos[i * 3] = (Math.random() - 0.5) * 100
    nebPos[i * 3 + 1] = 5 + Math.random() * 40
    nebPos[i * 3 + 2] = (Math.random() - 0.5) * 100 - 20
    const hue = Math.random()
    if (hue > 0.5) {
      nebCol[i * 3] = 0.35
      nebCol[i * 3 + 1] = 0.25
      nebCol[i * 3 + 2] = 0.7
    } else {
      nebCol[i * 3] = 0.2
      nebCol[i * 3 + 1] = 0.4
      nebCol[i * 3 + 2] = 0.75
    }
  }
  const nebGeo = new THREE.BufferGeometry()
  nebGeo.setAttribute('position', new THREE.BufferAttribute(nebPos, 3))
  nebGeo.setAttribute('color', new THREE.BufferAttribute(nebCol, 3))
  const nebula = new THREE.Points(
    nebGeo,
    new THREE.PointsMaterial({
      size: 2.8,
      vertexColors: true,
      transparent: true,
      opacity: 0.08,
      depthWrite: false,
      sizeAttenuation: true,
    }),
  )
  scene.add(nebula)

  // 长方形庭院地面（替代圆形平台）
  const groundW = 110
  const groundD = 140
  const ground = new THREE.Mesh(
    new THREE.BoxGeometry(groundW, 0.2, groundD),
    new THREE.MeshStandardMaterial({
      color: 0x1a2438,
      roughness: 0.9,
      metalness: 0.08,
    }),
  )
  ground.position.set(0, -0.14, 10)
  scene.add(ground)

  // 草皮层（略小于石板，露出边缘）
  const turf = new THREE.Mesh(
    new THREE.BoxGeometry(groundW - 6, 0.08, groundD - 6),
    new THREE.MeshStandardMaterial({
      color: 0x1e4a32,
      roughness: 0.95,
      metalness: 0.02,
    }),
  )
  turf.position.set(0, -0.02, 10)
  scene.add(turf)

  // 庭院金边（长方形轮廓）
  const edgeMat = new THREE.MeshBasicMaterial({
    color: 0xc49b4e,
    transparent: true,
    opacity: 0.35,
  })
  const edgeY = 0.02
  const hw = groundW / 2 - 0.5
  const hd = groundD / 2 - 0.5
  const gz = 10
  const edges = [
    new THREE.BoxGeometry(groundW - 1, 0.06, 0.35),
    new THREE.BoxGeometry(groundW - 1, 0.06, 0.35),
    new THREE.BoxGeometry(0.35, 0.06, groundD - 1),
    new THREE.BoxGeometry(0.35, 0.06, groundD - 1),
  ]
  const edgePos = [
    [0, edgeY, gz + hd],
    [0, edgeY, gz - hd],
    [-hw, edgeY, gz],
    [hw, edgeY, gz],
  ]
  edges.forEach((geo, i) => {
    const m = new THREE.Mesh(geo, edgeMat)
    m.position.set(...edgePos[i])
    scene.add(m)
  })

  // 保留轻量光环作氛围（弱化，不抢长方形轮廓）
  const ring = new THREE.Mesh(
    new THREE.TorusGeometry(38, 0.03, 8, 64),
    new THREE.MeshBasicMaterial({ color: 0x6080ff, transparent: true, opacity: 0.08 }),
  )
  ring.rotation.x = Math.PI / 2
  ring.position.set(0, 0.01, 8)
  scene.add(ring)
  const ring2 = ring.clone()
  ring2.scale.setScalar(1.25)
  ring2.material = ring.material.clone()
  ring2.material.opacity = 0.05
  scene.add(ring2)

  // 唐风大殿 + 宽阔御道
  const pavilion = createPavilion(scene)
  const HALL_SCALE = 1.15
  pavilion.root.scale.setScalar(HALL_SCALE)
  pavilion.root.position.set(0, 0, -8)

  // 外围山水庭院（建筑外：假山 / 小溪 / 绿植）
  addOuterLandscape(scene, HALL_SCALE)

  const sx = (v) => v.clone().multiplyScalar(HALL_SCALE).add(pavilion.root.position)
  pavilion.hallCenter = sx(pavilion.hallCenter)
  pavilion.doorPos = sx(pavilion.doorPos)
  pavilion.bossStand = sx(pavilion.bossStand)
  pavilion.queueOrigin = sx(pavilion.queueOrigin)
  pavilion.workSlots = pavilion.workSlots.map((v) => sx(v))
  pavilion.queueSlots = pavilion.queueSlots.map((v) => sx(v))
  pavilion.loungeSlots = pavilion.loungeSlots.map((v) => sx(v))

  // 丹炉
  const pedestal = new THREE.Group()
  pedestal.position.copy(pavilion.hallCenter)
  pedestal.scale.setScalar(HALL_SCALE * 0.75)

  pedestal.add(
    new THREE.Mesh(
      new THREE.CylinderGeometry(1.9, 2.2, 0.22, 20),
      new THREE.MeshStandardMaterial({
        color: 0x8b6238,
        metalness: 0.55,
        roughness: 0.4,
        emissive: 0x5a3010,
        emissiveIntensity: 0.15,
      }),
    ),
  )

  const pot = new THREE.Mesh(
    new THREE.CylinderGeometry(1.05, 1.3, 1.25, 18),
    new THREE.MeshStandardMaterial({
      color: 0x9a6a38,
      metalness: 0.65,
      roughness: 0.35,
      emissive: 0x6a2808,
      emissiveIntensity: 0.22,
    }),
  )
  pot.position.y = 0.75
  pedestal.add(pot)

  const potTop = new THREE.Mesh(
    new THREE.CylinderGeometry(0.62, 1.05, 0.4, 18),
    new THREE.MeshStandardMaterial({
      color: 0xb07a40,
      metalness: 0.6,
      roughness: 0.38,
      emissive: 0x4a1808,
      emissiveIntensity: 0.15,
    }),
  )
  potTop.position.y = 1.5
  pedestal.add(potTop)

  const lid = new THREE.Mesh(
    new THREE.SphereGeometry(0.68, 14, 10, 0, Math.PI * 2, 0, Math.PI * 0.5),
    new THREE.MeshStandardMaterial({
      color: 0xc48a48,
      metalness: 0.55,
      roughness: 0.4,
      emissive: 0xd44a32,
      emissiveIntensity: 0.15,
    }),
  )
  lid.position.y = 1.72
  pedestal.add(lid)

  const crystal = new THREE.Mesh(
    new THREE.SphereGeometry(0.26, 12, 12),
    new THREE.MeshStandardMaterial({
      color: 0xff6a40,
      metalness: 0.2,
      roughness: 0.18,
      emissive: 0xff4a18,
      emissiveIntensity: 1.0,
      transparent: true,
      opacity: 0.95,
    }),
  )
  crystal.position.y = 2.15
  pedestal.add(crystal)

  for (let i = 0; i < 3; i++) {
    const a = (i / 3) * Math.PI * 2 + Math.PI / 6
    const leg = new THREE.Mesh(
      new THREE.CylinderGeometry(0.09, 0.14, 0.45, 6),
      new THREE.MeshStandardMaterial({ color: 0x7a4a22, metalness: 0.55, roughness: 0.45 }),
    )
    leg.position.set(Math.sin(a) * 0.95, 0.15, Math.cos(a) * 0.95)
    pedestal.add(leg)
  }

  const coreLight = new THREE.PointLight(0xff8040, 2.6, 30, 2)
  coreLight.position.set(0, 2.0, 0)
  pedestal.add(coreLight)

  const beam = new THREE.Mesh(
    new THREE.CylinderGeometry(0.1, 1.3, 7, 14, 1, true),
    new THREE.MeshBasicMaterial({
      color: 0xffa050,
      transparent: true,
      opacity: 0.1,
      side: THREE.DoubleSide,
      depthWrite: false,
    }),
  )
  beam.position.y = 5.0
  pedestal.add(beam)

  const flame = new THREE.Mesh(
    new THREE.ConeGeometry(0.42, 0.85, 10, 1, true),
    new THREE.MeshBasicMaterial({
      color: 0xffd060,
      transparent: true,
      opacity: 0.45,
      side: THREE.DoubleSide,
      depthWrite: false,
    }),
  )
  flame.position.y = 1.15
  pedestal.add(flame)

  scene.add(pedestal)

  // 灵尘
  const dustCount = 160
  const dustPos = new Float32Array(dustCount * 3)
  for (let i = 0; i < dustCount; i++) {
    dustPos[i * 3] = (Math.random() - 0.5) * 50
    dustPos[i * 3 + 1] = 0.5 + Math.random() * 14
    dustPos[i * 3 + 2] = (Math.random() - 0.5) * 50
  }
  const dustGeo = new THREE.BufferGeometry()
  dustGeo.setAttribute('position', new THREE.BufferAttribute(dustPos, 3))
  const dust = new THREE.Points(
    dustGeo,
    new THREE.PointsMaterial({
      color: 0xa0c0ff,
      size: 0.06,
      transparent: true,
      opacity: 0.35,
      depthWrite: false,
    }),
  )
  scene.add(dust)

  // 流星（周期性划过天幕）
  const meteors = []
  const METEOR_N = 5
  for (let i = 0; i < METEOR_N; i++) {
    const head = new THREE.Mesh(
      new THREE.SphereGeometry(0.12, 6, 6),
      new THREE.MeshBasicMaterial({
        color: 0xfff0d0,
        transparent: true,
        opacity: 0,
      }),
    )
    const trail = new THREE.Mesh(
      new THREE.CylinderGeometry(0.02, 0.08, 3.5, 5, 1, true),
      new THREE.MeshBasicMaterial({
        color: 0xc0d8ff,
        transparent: true,
        opacity: 0,
        side: THREE.DoubleSide,
        depthWrite: false,
      }),
    )
    trail.rotation.z = Math.PI / 2
    trail.position.x = 1.6
    const g = new THREE.Group()
    g.add(head)
    g.add(trail)
    g.visible = false
    scene.add(g)
    meteors.push({
      group: g,
      head,
      trail,
      // 随机相位，错开出现
      nextT: 2 + i * 3.5 + Math.random() * 4,
      life: 0,
      duration: 1.8 + Math.random() * 1.2,
      active: false,
      start: new THREE.Vector3(),
      end: new THREE.Vector3(),
    })
  }

  function spawnMeteor(m) {
    const y = 18 + Math.random() * 22
    const z = -20 + Math.random() * 50
    const fromLeft = Math.random() > 0.5
    m.start.set(fromLeft ? -55 : 55, y + 4 + Math.random() * 6, z)
    m.end.set(fromLeft ? 40 : -40, y - 8 - Math.random() * 6, z + (Math.random() - 0.5) * 15)
    m.life = 0
    m.duration = 1.6 + Math.random() * 1.4
    m.active = true
    m.group.visible = true
    m.group.position.copy(m.start)
    m.group.lookAt(m.end)
  }

  let lastMeteorT = 0

  return {
    stars,
    nebula,
    pedestal,
    crystal,
    coreLight,
    dust,
    ring,
    ring2,
    flame,
    pavilion,
    meteors,
    update(t, progress = 0) {
      crystal.rotation.y = t * 0.5
      crystal.position.y = 2.15 + Math.sin(t * 1.3) * 0.05
      crystal.scale.setScalar(0.85 + progress * 0.5)
      coreLight.intensity = 1.8 + progress * 1.5 + Math.sin(t * 2.8) * 0.25
      if (flame) {
        flame.scale.y = 0.9 + Math.sin(t * 5) * 0.15 + progress * 0.3
        flame.scale.x = 0.85 + Math.sin(t * 4.2) * 0.1
        flame.material.opacity = 0.35 + progress * 0.25 + Math.sin(t * 3) * 0.08
      }
      ring.rotation.z = t * 0.02
      ring2.rotation.z = -t * 0.015
      stars.rotation.y = t * 0.008
      nebula.rotation.y = t * 0.004
      dust.rotation.y = t * 0.01
      pavilion.update(t)

      const dt = Math.min(0.05, Math.max(0, t - lastMeteorT))
      lastMeteorT = t
      for (const m of meteors) {
        if (!m.active && t >= m.nextT) spawnMeteor(m)
        if (!m.active) continue
        m.life += dt
        const k = m.life / m.duration
        if (k >= 1) {
          m.active = false
          m.group.visible = false
          m.head.material.opacity = 0
          m.trail.material.opacity = 0
          m.nextT = t + 4 + Math.random() * 8
          continue
        }
        const e = k * k * (3 - 2 * k)
        m.group.position.lerpVectors(m.start, m.end, e)
        const fade = k < 0.15 ? k / 0.15 : k > 0.7 ? (1 - k) / 0.3 : 1
        m.head.material.opacity = fade * 0.95
        m.trail.material.opacity = fade * 0.55
      }
    },
  }
}

/** 建筑外围：假山 · 小溪 · 绿植（长方形庭院） */
function addOuterLandscape(scene, scale = 1) {
  const root = new THREE.Group()
  root.name = 'outerLandscape'
  scene.add(root)

  const mat = {
    grass: new THREE.MeshStandardMaterial({ color: 0x2a5a38, roughness: 0.92, metalness: 0.02 }),
    grassDeep: new THREE.MeshStandardMaterial({ color: 0x1a3a28, roughness: 0.94, metalness: 0.02 }),
    rock: new THREE.MeshStandardMaterial({ color: 0x6a6870, roughness: 0.88, metalness: 0.08 }),
    rockDark: new THREE.MeshStandardMaterial({ color: 0x4a4850, roughness: 0.9, metalness: 0.06 }),
    water: new THREE.MeshStandardMaterial({
      color: 0x1a4868,
      roughness: 0.12,
      metalness: 0.5,
      transparent: true,
      opacity: 0.78,
      emissive: 0x0a2840,
      emissiveIntensity: 0.18,
    }),
    leaf: new THREE.MeshStandardMaterial({
      color: 0x3a8a50,
      roughness: 0.78,
      metalness: 0.04,
      emissive: 0x0a2010,
      emissiveIntensity: 0.08,
    }),
    leafDark: new THREE.MeshStandardMaterial({ color: 0x2a6040, roughness: 0.82, metalness: 0.04 }),
    blossom: new THREE.MeshStandardMaterial({
      color: 0xffb0c8,
      roughness: 0.55,
      metalness: 0.05,
      emissive: 0x602030,
      emissiveIntensity: 0.18,
    }),
    blossomWhite: new THREE.MeshStandardMaterial({
      color: 0xffe8f0,
      roughness: 0.5,
      metalness: 0.05,
      emissive: 0x403040,
      emissiveIntensity: 0.1,
    }),
    wood: new THREE.MeshStandardMaterial({ color: 0x4a3018, roughness: 0.75, metalness: 0.05 }),
    sand: new THREE.MeshStandardMaterial({ color: 0x8a7a58, roughness: 0.95, metalness: 0.02 }),
  }

  // 建筑/御道占用区（避免叠在建筑上）— 本地坐标，pavilion 已整体后移
  // 外围环带：左右、前后
  const zones = [
    // 左侧大片
    { x: -38, z: 8, w: 28, d: 90 },
    // 右侧大片
    { x: 38, z: 8, w: 28, d: 90 },
    // 殿后
    { x: 0, z: -42, w: 70, d: 28 },
    // 御道尽头前方
    { x: 0, z: 58, w: 70, d: 28 },
  ]

  for (const z of zones) {
    // 草坪底
    const lawn = new THREE.Mesh(new THREE.BoxGeometry(z.w, 0.1, z.d), mat.grass)
    lawn.position.set(z.x, 0.0, z.z)
    root.add(lawn)
  }

  // ——— 左侧小溪（蜿蜒） ———
  addStream(root, mat, -32, -20, 45, 1)
  // ——— 右侧小溪 ———
  addStream(root, mat, 32, -15, 50, -1)

  // ——— 假山群 ———
  const rockSpots = [
    [-36, -8], [-42, 5], [-38, 22], [-34, 40],
    [36, -10], [42, 8], [38, 25], [34, 42],
    [-18, -38], [0, -45], [18, -40],
    [-20, 55], [0, 60], [22, 52],
    [-28, 12], [28, 15], [-40, -25], [40, -22],
  ]
  for (let i = 0; i < rockSpots.length; i++) {
    const [x, z] = rockSpots[i]
    addRockery(root, mat, x, z, 0.8 + (i % 4) * 0.25)
  }

  // ——— 绿植 / 树木 ———
  const treeSpots = []
  for (let i = 0; i < 36; i++) {
    const side = i % 2 === 0 ? -1 : 1
    const x = side * (24 + (i % 5) * 4.5 + Math.sin(i * 1.7) * 2)
    const z = -30 + Math.floor(i / 2) * 5.5 + Math.cos(i) * 2
    // 避开御道中央
    if (Math.abs(x) < 12 && z > -5 && z < 45) continue
    treeSpots.push([x, z, 0.75 + (i % 4) * 0.2, i % 3 === 0])
  }
  // 四角密林
  for (const [cx, cz] of [
    [-42, -35],
    [42, -35],
    [-42, 55],
    [42, 55],
  ]) {
    for (let k = 0; k < 5; k++) {
      treeSpots.push([
        cx + (k % 3) * 3.5 - 3,
        cz + Math.floor(k / 3) * 4 - 2,
        0.9 + (k % 3) * 0.15,
        k % 2 === 0,
      ])
    }
  }
  for (const [x, z, s, blossom] of treeSpots) {
    addOuterTree(root, mat, x, z, s, blossom)
  }

  // 花丛散点
  for (let i = 0; i < 40; i++) {
    const side = i % 2 === 0 ? -1 : 1
    const x = side * (18 + (i % 6) * 3.5 + Math.sin(i * 2.1) * 1.5)
    const z = -25 + i * 2.2
    if (Math.abs(x) < 11 && z > 0 && z < 50) continue
    addOuterBush(root, mat, x, z, i % 2 === 0)
  }

  // 竹丛（细高）
  for (const [x, z] of [
    [-22, -12],
    [-24, -8],
    [22, -14],
    [25, -9],
    [-26, 48],
    [26, 46],
  ]) {
    addBamboo(root, mat, x, z)
  }

  root.scale.setScalar(scale)
  return root
}

function addStream(root, mat, x0, z0, length, dir) {
  // 河床
  const bed = new THREE.Mesh(new THREE.BoxGeometry(3.2, 0.12, length), mat.sand)
  bed.position.set(x0, -0.02, z0 + (length / 2) * dir)
  root.add(bed)
  // 水面
  const water = new THREE.Mesh(new THREE.BoxGeometry(2.4, 0.08, length - 1), mat.water)
  water.position.set(x0, 0.04, z0 + (length / 2) * dir)
  root.add(water)
  // 河岸石
  for (let i = 0; i < 12; i++) {
    const side = i % 2 === 0 ? -1 : 1
    const rock = new THREE.Mesh(
      new THREE.DodecahedronGeometry(0.28 + (i % 3) * 0.1, 0),
      i % 2 ? mat.rock : mat.rockDark,
    )
    rock.scale.set(1.1, 0.7, 1)
    rock.position.set(
      x0 + side * 1.5,
      0.12,
      z0 + dir * (2 + i * (length / 13)),
    )
    rock.rotation.y = i * 0.5
    root.add(rock)
  }
  // 溪中汀步
  for (let i = 0; i < 6; i++) {
    const step = new THREE.Mesh(new THREE.CylinderGeometry(0.35, 0.4, 0.12, 8), mat.rock)
    step.position.set(x0 + Math.sin(i) * 0.3, 0.08, z0 + dir * (4 + i * (length / 8)))
    root.add(step)
  }
}

function addRockery(root, mat, x, z, scale) {
  const g = new THREE.Group()
  g.position.set(x, 0, z)
  // 主峰
  const main = new THREE.Mesh(new THREE.DodecahedronGeometry(1.1 * scale, 0), mat.rock)
  main.scale.set(1.1, 1.8 + scale * 0.4, 0.9)
  main.position.y = 1.0 * scale
  main.rotation.y = Math.random() * Math.PI
  g.add(main)
  // 侧峰
  for (let i = 0; i < 3; i++) {
    const a = (i / 3) * Math.PI * 2 + 0.4
    const r = 0.9 * scale
    const s = new THREE.Mesh(
      new THREE.DodecahedronGeometry(0.55 * scale, 0),
      i % 2 ? mat.rockDark : mat.rock,
    )
    s.scale.set(1, 1.3 + (i % 2) * 0.4, 0.85)
    s.position.set(Math.cos(a) * r, 0.55 * scale, Math.sin(a) * r)
    g.add(s)
  }
  // 山脚苔藓
  const moss = new THREE.Mesh(new THREE.SphereGeometry(1.3 * scale, 8, 6), mat.grassDeep)
  moss.scale.set(1.2, 0.25, 1.1)
  moss.position.y = 0.08
  g.add(moss)
  root.add(g)
}

function addOuterTree(root, mat, x, z, scale, blossom) {
  const trunk = new THREE.Mesh(
    new THREE.CylinderGeometry(0.1 * scale, 0.16 * scale, 1.7 * scale, 6),
    mat.wood,
  )
  trunk.position.set(x, 0.85 * scale, z)
  root.add(trunk)
  const crown = new THREE.Mesh(
    new THREE.SphereGeometry(1.05 * scale, 8, 8),
    blossom ? mat.leaf : mat.leafDark,
  )
  crown.position.set(x, 2.1 * scale, z)
  crown.scale.set(1.15, 0.95, 1.15)
  root.add(crown)
  const crown2 = new THREE.Mesh(new THREE.SphereGeometry(0.65 * scale, 7, 7), mat.leaf)
  crown2.position.set(x + 0.35 * scale, 2.55 * scale, z - 0.2 * scale)
  root.add(crown2)
  if (blossom) {
    for (let k = 0; k < 4; k++) {
      const a = (k / 4) * Math.PI * 2
      const p = new THREE.Mesh(
        new THREE.SphereGeometry(0.18 * scale, 5, 5),
        k % 2 ? mat.blossom : mat.blossomWhite,
      )
      p.position.set(
        x + Math.cos(a) * 0.65 * scale,
        2.05 * scale + (k % 2) * 0.2,
        z + Math.sin(a) * 0.65 * scale,
      )
      root.add(p)
    }
  }
}

function addOuterBush(root, mat, x, z, pink) {
  const bush = new THREE.Mesh(new THREE.SphereGeometry(0.42, 7, 7), mat.leafDark)
  bush.position.set(x, 0.32, z)
  bush.scale.set(1.25, 0.7, 1.15)
  root.add(bush)
  for (let k = 0; k < 3; k++) {
    const a = (k / 3) * Math.PI * 2
    const f = new THREE.Mesh(
      new THREE.SphereGeometry(0.11, 5, 5),
      pink ? mat.blossom : mat.blossomWhite,
    )
    f.position.set(x + Math.cos(a) * 0.28, 0.48, z + Math.sin(a) * 0.28)
    root.add(f)
  }
}

function addBamboo(root, mat, x, z) {
  for (let i = 0; i < 5; i++) {
    const h = 2.2 + (i % 3) * 0.4
    const stalk = new THREE.Mesh(
      new THREE.CylinderGeometry(0.04, 0.05, h, 5),
      mat.leaf,
    )
    stalk.position.set(x + (i - 2) * 0.22, h / 2, z + (i % 2) * 0.15)
    root.add(stalk)
    const top = new THREE.Mesh(new THREE.SphereGeometry(0.2, 5, 5), mat.leafDark)
    top.scale.set(1.4, 0.5, 1)
    top.position.set(stalk.position.x, h + 0.1, stalk.position.z)
    root.add(top)
  }
}
