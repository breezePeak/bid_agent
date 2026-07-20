import * as THREE from 'three'

/**
 * 唐风丹殿 + 无顶宽阔御道 + 灯杆宫灯 + 两侧花园
 */
export function createPavilion(scene) {
  const root = new THREE.Group()
  root.name = 'pavilion'
  scene.add(root)

  const mat = {
    red: new THREE.MeshStandardMaterial({
      color: 0xb82018,
      roughness: 0.48,
      metalness: 0.12,
      emissive: 0x3a0808,
      emissiveIntensity: 0.1,
    }),
    redDeep: new THREE.MeshStandardMaterial({
      color: 0x8a1410,
      roughness: 0.52,
      metalness: 0.1,
    }),
    wood: new THREE.MeshStandardMaterial({ color: 0x6b3a18, roughness: 0.58, metalness: 0.08 }),
    woodDark: new THREE.MeshStandardMaterial({ color: 0x4a2810, roughness: 0.6, metalness: 0.08 }),
    gold: new THREE.MeshStandardMaterial({
      color: 0xe0b84a,
      metalness: 0.72,
      roughness: 0.28,
      emissive: 0x7a5018,
      emissiveIntensity: 0.28,
    }),
    goldSoft: new THREE.MeshStandardMaterial({
      color: 0xc49a38,
      metalness: 0.55,
      roughness: 0.38,
      emissive: 0x5a3810,
      emissiveIntensity: 0.15,
    }),
    tile: new THREE.MeshStandardMaterial({
      color: 0x3a6a58,
      roughness: 0.45,
      metalness: 0.2,
      emissive: 0x0a2018,
      emissiveIntensity: 0.08,
    }),
    plaster: new THREE.MeshStandardMaterial({ color: 0xf0e6d4, roughness: 0.88, metalness: 0.02 }),
    stone: new THREE.MeshStandardMaterial({ color: 0xc8c0b0, roughness: 0.82, metalness: 0.05 }),
    stoneDark: new THREE.MeshStandardMaterial({ color: 0xa8a090, roughness: 0.85, metalness: 0.04 }),
    carpet: new THREE.MeshStandardMaterial({
      color: 0xb81818,
      roughness: 0.72,
      metalness: 0.04,
      emissive: 0x380808,
      emissiveIntensity: 0.12,
    }),
    carpetGold: new THREE.MeshStandardMaterial({
      color: 0xd4a840,
      metalness: 0.5,
      roughness: 0.42,
      emissive: 0x5a3810,
      emissiveIntensity: 0.16,
    }),
    lantern: new THREE.MeshStandardMaterial({
      color: 0xff6a40,
      emissive: 0xff4018,
      emissiveIntensity: 0.95,
      roughness: 0.35,
    }),
    lanternFrame: new THREE.MeshStandardMaterial({
      color: 0xb82018,
      roughness: 0.5,
      metalness: 0.15,
    }),
    pole: new THREE.MeshStandardMaterial({
      color: 0x3a3028,
      metalness: 0.35,
      roughness: 0.55,
    }),
    grass: new THREE.MeshStandardMaterial({
      color: 0x2a5a38,
      roughness: 0.9,
      metalness: 0.02,
    }),
    grassDeep: new THREE.MeshStandardMaterial({
      color: 0x1a3a28,
      roughness: 0.92,
      metalness: 0.02,
    }),
    leaf: new THREE.MeshStandardMaterial({
      color: 0x3a8a50,
      roughness: 0.75,
      metalness: 0.05,
      emissive: 0x0a2010,
      emissiveIntensity: 0.08,
    }),
    leafDark: new THREE.MeshStandardMaterial({
      color: 0x2a6040,
      roughness: 0.8,
      metalness: 0.05,
    }),
    blossom: new THREE.MeshStandardMaterial({
      color: 0xffb0c8,
      roughness: 0.55,
      metalness: 0.05,
      emissive: 0x602030,
      emissiveIntensity: 0.2,
    }),
    blossomWhite: new THREE.MeshStandardMaterial({
      color: 0xffe8f0,
      roughness: 0.5,
      metalness: 0.05,
      emissive: 0x403040,
      emissiveIntensity: 0.12,
    }),
    water: new THREE.MeshStandardMaterial({
      color: 0x1a4060,
      roughness: 0.15,
      metalness: 0.45,
      transparent: true,
      opacity: 0.75,
      emissive: 0x0a2038,
      emissiveIntensity: 0.15,
    }),
    rock: new THREE.MeshStandardMaterial({
      color: 0x6a6870,
      roughness: 0.88,
      metalness: 0.08,
    }),
  }

  // ========== 大殿台基 ==========
  const base = new THREE.Mesh(new THREE.BoxGeometry(22, 0.7, 16), mat.stone)
  base.position.set(0, 0.25, -2)
  root.add(base)
  const base2 = new THREE.Mesh(new THREE.BoxGeometry(20, 0.45, 14), mat.stoneDark)
  base2.position.set(0, 0.72, -2)
  root.add(base2)
  const floor = new THREE.Mesh(new THREE.BoxGeometry(18.5, 0.1, 12.5), mat.wood)
  floor.position.set(0, 0.98, -2)
  root.add(floor)
  const centerFloor = new THREE.Mesh(new THREE.BoxGeometry(6, 0.12, 5), mat.goldSoft)
  centerFloor.position.set(0, 1.0, -2)
  root.add(centerFloor)

  // 殿前宽台阶
  for (let i = 0; i < 6; i++) {
    const step = new THREE.Mesh(new THREE.BoxGeometry(12 - i * 0.2, 0.16, 0.75), mat.stone)
    step.position.set(0, 0.08 + i * 0.16, 5.8 + i * 0.6)
    root.add(step)
  }

  // ========== 唐风大殿主体 ==========
  const hallY = 1.05
  const colH = 5.2
  const colY = hallY + colH / 2

  const backWall = new THREE.Mesh(new THREE.BoxGeometry(17, 4.8, 0.35), mat.plaster)
  backWall.position.set(0, hallY + 2.4, -7.5)
  root.add(backWall)
  const mural = new THREE.Mesh(new THREE.BoxGeometry(8, 3.2, 0.12), mat.redDeep)
  mural.position.set(0, hallY + 2.6, -7.28)
  root.add(mural)
  const muralGold = new THREE.Mesh(new THREE.BoxGeometry(7.2, 2.6, 0.1), mat.goldSoft)
  muralGold.position.set(0, hallY + 2.6, -7.2)
  root.add(muralGold)

  for (const x of [-8.6, 8.6]) {
    const wall = new THREE.Mesh(new THREE.BoxGeometry(0.3, 3.2, 10), mat.plaster)
    wall.position.set(x, hallY + 1.6, -2.5)
    root.add(wall)
  }

  const colXs = [-7.5, -3.75, 0, 3.75, 7.5]
  const colZs = [-6.5, -2, 2.5]
  for (const x of colXs) {
    for (const z of colZs) {
      if (z > 1.5 && Math.abs(x) < 2.5) continue
      const thick = Math.abs(x) > 6 || Math.abs(z + 2) < 0.5
      const r = thick ? 0.32 : 0.24
      const col = new THREE.Mesh(new THREE.CylinderGeometry(r, r * 1.08, colH, 12), mat.red)
      col.position.set(x, colY, z)
      root.add(col)
      const baseCap = new THREE.Mesh(new THREE.CylinderGeometry(r * 1.5, r * 1.65, 0.25, 10), mat.stone)
      baseCap.position.set(x, hallY + 0.1, z)
      root.add(baseCap)
      const band = new THREE.Mesh(new THREE.TorusGeometry(r * 1.05, 0.035, 6, 14), mat.gold)
      band.rotation.x = Math.PI / 2
      band.position.set(x, hallY + 0.55, z)
      root.add(band)
    }
  }

  const beamY = hallY + colH + 0.2
  const beamFront = new THREE.Mesh(new THREE.BoxGeometry(16.5, 0.45, 0.5), mat.woodDark)
  beamFront.position.set(0, beamY, 2.5)
  root.add(beamFront)
  const beamGold = new THREE.Mesh(new THREE.BoxGeometry(16.6, 0.12, 0.55), mat.gold)
  beamGold.position.set(0, beamY + 0.28, 2.5)
  root.add(beamGold)
  const beamBack = new THREE.Mesh(new THREE.BoxGeometry(16.5, 0.45, 0.5), mat.woodDark)
  beamBack.position.set(0, beamY, -6.5)
  root.add(beamBack)
  for (const x of [-7.5, 7.5]) {
    const side = new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.45, 9.5), mat.woodDark)
    side.position.set(x, beamY, -2)
    root.add(side)
  }

  const plaque = new THREE.Mesh(new THREE.BoxGeometry(4.2, 1.0, 0.15), mat.gold)
  plaque.position.set(0, beamY + 0.85, 2.85)
  root.add(plaque)
  const plaqueIn = new THREE.Mesh(new THREE.BoxGeometry(3.7, 0.75, 0.1), mat.redDeep)
  plaqueIn.position.set(0, beamY + 0.85, 2.95)
  root.add(plaqueIn)

  const roofY = beamY + 0.55
  const roof = new THREE.Mesh(new THREE.BoxGeometry(20, 0.25, 13.5), mat.tile)
  roof.position.set(0, roofY, -2)
  root.add(roof)
  addTangRoofSlopes(root, 0, roofY + 0.1, -2, 20, 13.5, 2.2, mat.tile)
  const ridge = new THREE.Mesh(new THREE.BoxGeometry(14, 0.35, 0.4), mat.gold)
  ridge.position.set(0, roofY + 2.35, -2)
  root.add(ridge)
  for (const x of [-6.8, 6.8]) {
    const owl = new THREE.Mesh(new THREE.ConeGeometry(0.35, 1.1, 6), mat.gold)
    owl.position.set(x, roofY + 2.9, -2)
    root.add(owl)
  }
  const finial = new THREE.Mesh(new THREE.SphereGeometry(0.4, 12, 12), mat.gold)
  finial.position.set(0, roofY + 2.85, -2)
  root.add(finial)

  for (const x of [-3.2, 0, 3.2]) {
    const door = new THREE.Mesh(new THREE.BoxGeometry(2.4, 3.6, 0.15), mat.red)
    door.position.set(x, hallY + 1.9, 2.7)
    root.add(door)
    const frame = new THREE.Mesh(new THREE.BoxGeometry(2.55, 3.75, 0.08), mat.goldSoft)
    frame.position.set(x, hallY + 1.9, 2.8)
    root.add(frame)
    for (let r = 0; r < 4; r++) {
      for (let c = 0; c < 3; c++) {
        const stud = new THREE.Mesh(new THREE.SphereGeometry(0.05, 6, 6), mat.gold)
        stud.position.set(x - 0.55 + c * 0.55, hallY + 0.9 + r * 0.55, 2.9)
        root.add(stud)
      }
    }
  }

  const altar = new THREE.Mesh(new THREE.CylinderGeometry(2.3, 2.6, 0.3, 24), mat.goldSoft)
  altar.position.set(0, hallY + 0.15, -2)
  root.add(altar)

  // ========== 宽阔无顶御道 ==========
  const avenueLen = 48
  const avenueStartZ = 9.5
  const avenueW = 14
  const carpetW = 4.2
  const poleSpacing = 4.5
  const poleCount = Math.floor(avenueLen / poleSpacing)
  const poleH = 4.2
  const poleX = avenueW / 2 + 0.8

  // 宽石道
  const avenueBase = new THREE.Mesh(
    new THREE.BoxGeometry(avenueW, 0.28, avenueLen + 2),
    mat.stone,
  )
  avenueBase.position.set(0, 0.08, avenueStartZ + avenueLen / 2)
  root.add(avenueBase)
  // 石道边沿
  for (const side of [-1, 1]) {
    const curb = new THREE.Mesh(
      new THREE.BoxGeometry(0.35, 0.22, avenueLen + 2),
      mat.stoneDark,
    )
    curb.position.set(side * (avenueW / 2 - 0.1), 0.22, avenueStartZ + avenueLen / 2)
    root.add(curb)
  }
  // 中线石纹
  for (let i = 0; i < 20; i++) {
    const seam = new THREE.Mesh(new THREE.BoxGeometry(avenueW - 0.8, 0.02, 0.06), mat.stoneDark)
    seam.position.set(0, 0.23, avenueStartZ + 1.5 + i * 2.3)
    root.add(seam)
  }

  // 红地毯（宽）
  const carpet = new THREE.Mesh(
    new THREE.BoxGeometry(carpetW, 0.05, avenueLen + 3),
    mat.carpet,
  )
  carpet.position.set(0, 0.26, avenueStartZ + avenueLen / 2 - 0.5)
  root.add(carpet)
  for (const side of [-1, 1]) {
    const edge = new THREE.Mesh(
      new THREE.BoxGeometry(0.14, 0.06, avenueLen + 3),
      mat.carpetGold,
    )
    edge.position.set(side * (carpetW / 2 - 0.05), 0.28, avenueStartZ + avenueLen / 2 - 0.5)
    root.add(edge)
  }
  for (let i = 0; i < 18; i++) {
    const stripe = new THREE.Mesh(new THREE.BoxGeometry(carpetW * 0.7, 0.055, 0.12), mat.carpetGold)
    stripe.position.set(0, 0.29, avenueStartZ + 1.8 + i * 2.5)
    root.add(stripe)
  }
  // 殿口圆毯
  const roundCarpet = new THREE.Mesh(new THREE.CylinderGeometry(2.8, 2.8, 0.05, 28), mat.carpet)
  roundCarpet.position.set(0, 0.27, 8.5)
  root.add(roundCarpet)
  const roundGold = new THREE.Mesh(new THREE.TorusGeometry(2.8, 0.07, 8, 28), mat.carpetGold)
  roundGold.rotation.x = Math.PI / 2
  roundGold.position.set(0, 0.3, 8.5)
  root.add(roundGold)

  // 灯杆宫灯（电线杆式，不靠柱子）
  for (let i = 0; i <= poleCount; i++) {
    const z = avenueStartZ + 1.2 + i * poleSpacing
    for (const side of [-1, 1]) {
      const x = side * poleX
      // 杆身
      const pole = new THREE.Mesh(new THREE.CylinderGeometry(0.08, 0.12, poleH, 8), mat.pole)
      pole.position.set(x, poleH / 2 + 0.15, z)
      root.add(pole)
      // 底座
      const base = new THREE.Mesh(new THREE.CylinderGeometry(0.28, 0.32, 0.25, 8), mat.stoneDark)
      base.position.set(x, 0.2, z)
      root.add(base)
      // 横臂（向内侧伸出）
      const arm = new THREE.Mesh(new THREE.BoxGeometry(1.1, 0.08, 0.08), mat.pole)
      arm.position.set(x - side * 0.55, poleH + 0.05, z)
      root.add(arm)
      // 吊链
      const chain = new THREE.Mesh(new THREE.CylinderGeometry(0.015, 0.015, 0.5, 4), mat.goldSoft)
      chain.position.set(x - side * 1.0, poleH - 0.2, z)
      root.add(chain)
      // 灯笼
      const lx = x - side * 1.0
      const ly = poleH - 0.65
      const lantern = new THREE.Mesh(new THREE.SphereGeometry(0.32, 10, 10), mat.lantern)
      lantern.scale.set(1, 1.3, 1)
      lantern.position.set(lx, ly, z)
      root.add(lantern)
      const lid = new THREE.Mesh(new THREE.CylinderGeometry(0.26, 0.2, 0.1, 8), mat.lanternFrame)
      lid.position.set(lx, ly + 0.32, z)
      root.add(lid)
      const bot = new THREE.Mesh(new THREE.CylinderGeometry(0.2, 0.16, 0.1, 8), mat.lanternFrame)
      bot.position.set(lx, ly - 0.35, z)
      root.add(bot)
      const tassel = new THREE.Mesh(new THREE.CylinderGeometry(0.03, 0.01, 0.35, 5), mat.gold)
      tassel.position.set(lx, ly - 0.55, z)
      root.add(tassel)
      // 灯火
      const light = new THREE.PointLight(0xff8040, 0.7, 9, 2)
      light.position.set(lx, ly, z)
      root.add(light)
    }
  }

  // 华表（道端）
  const endZ = avenueStartZ + avenueLen
  for (const side of [-1, 1]) {
    const pillar = new THREE.Mesh(new THREE.CylinderGeometry(0.28, 0.32, 4.5, 10), mat.stone)
    pillar.position.set(side * (poleX + 1.5), 2.3, endZ)
    root.add(pillar)
    const cloud = new THREE.Mesh(new THREE.BoxGeometry(1.8, 0.16, 0.16), mat.gold)
    cloud.position.set(side * (poleX + 1.5), 4.5, endZ)
    root.add(cloud)
    const ball = new THREE.Mesh(new THREE.SphereGeometry(0.32, 10, 10), mat.gold)
    ball.position.set(side * (poleX + 1.5), 4.95, endZ)
    root.add(ball)
  }

  // ========== 两侧花园 ==========
  addGarden(root, mat, -1, avenueStartZ, avenueLen, avenueW)
  addGarden(root, mat, 1, avenueStartZ, avenueLen, avenueW)
  // 殿后花园
  addBackGarden(root, mat)

  const hallLight = new THREE.PointLight(0xffd0a0, 1.5, 24, 2)
  hallLight.position.set(0, 4.5, -2)
  root.add(hallLight)

  // 排队：红毯两侧宽道上
  const queueSlots = []
  for (let row = 0; row < 12; row++) {
    const z = avenueStartZ + 2.5 + row * 3.2
    queueSlots.push(new THREE.Vector3(-2.4, 0.35, z))
    queueSlots.push(new THREE.Vector3(2.4, 0.35, z))
  }

  const loungeSlots = []
  for (let i = 0; i < 12; i++) {
    loungeSlots.push(
      new THREE.Vector3(
        -6 + (i % 6) * 1.3,
        0.3,
        endZ + 2 + Math.floor(i / 6) * 1.3,
      ),
    )
  }

  return {
    root,
    hallCenter: new THREE.Vector3(0, hallY + 0.2, -2),
    doorPos: new THREE.Vector3(0, hallY + 0.5, 3.5),
    bossStand: new THREE.Vector3(0, hallY + 0.2, 0.5),
    workSlots: [
      new THREE.Vector3(-2.5, hallY + 0.2, -1.5),
      new THREE.Vector3(2.5, hallY + 0.2, -1.5),
      new THREE.Vector3(-2.0, hallY + 0.2, 0.5),
      new THREE.Vector3(2.0, hallY + 0.2, 0.5),
      new THREE.Vector3(-3.2, hallY + 0.2, -0.2),
      new THREE.Vector3(3.2, hallY + 0.2, -0.2),
    ],
    queueOrigin: new THREE.Vector3(0, 0.35, endZ - 1),
    queueSlots,
    loungeSlots,
    update(t) {
      hallLight.intensity = 1.35 + Math.sin(t * 2) * 0.12
    },
  }
}

function addGarden(root, mat, side, startZ, len, avenueW) {
  const gx = side * (avenueW / 2 + 8)
  // 草坪
  const lawn = new THREE.Mesh(new THREE.BoxGeometry(14, 0.12, len + 4), mat.grass)
  lawn.position.set(gx, 0.02, startZ + len / 2)
  root.add(lawn)
  // 深草边
  const edge = new THREE.Mesh(new THREE.BoxGeometry(0.4, 0.18, len + 4), mat.grassDeep)
  edge.position.set(side * (avenueW / 2 + 1.1), 0.1, startZ + len / 2)
  root.add(edge)

  // 树木
  const treePositions = []
  for (let i = 0; i < 8; i++) {
    treePositions.push({
      x: gx + side * (1.5 + (i % 3) * 2.2),
      z: startZ + 4 + i * 5.5,
      scale: 0.85 + (i % 3) * 0.2,
      blossom: i % 2 === 0,
    })
  }
  for (const t of treePositions) {
    addTree(root, mat, t.x, t.z, t.scale, t.blossom)
  }

  // 花丛
  for (let i = 0; i < 14; i++) {
    const fx = gx + side * (0.5 + (i % 4) * 2.0 + Math.sin(i) * 0.5)
    const fz = startZ + 2.5 + i * 3.2
    addFlowerBush(root, mat, fx, fz, i % 3 === 0)
  }

  // 石径
  for (let i = 0; i < 10; i++) {
    const path = new THREE.Mesh(new THREE.CylinderGeometry(0.35, 0.4, 0.08, 8), mat.stone)
    path.position.set(gx + side * 0.8, 0.1, startZ + 3 + i * 4.5)
    root.add(path)
  }

  // 池塘（每侧一个）
  const pondZ = startZ + len * 0.45
  const pond = new THREE.Mesh(new THREE.CylinderGeometry(2.4, 2.6, 0.15, 20), mat.water)
  pond.position.set(gx + side * 3.5, 0.08, pondZ)
  root.add(pond)
  // 池边石
  for (let k = 0; k < 8; k++) {
    const a = (k / 8) * Math.PI * 2
    const rock = new THREE.Mesh(
      new THREE.SphereGeometry(0.25 + (k % 3) * 0.08, 6, 6),
      mat.rock,
    )
    rock.scale.set(1.2, 0.6, 1)
    rock.position.set(
      gx + side * 3.5 + Math.sin(a) * 2.5,
      0.12,
      pondZ + Math.cos(a) * 2.5,
    )
    root.add(rock)
  }
  // 池心睡莲
  for (let k = 0; k < 3; k++) {
    const lily = new THREE.Mesh(new THREE.CylinderGeometry(0.35, 0.35, 0.04, 10), mat.leaf)
    lily.position.set(
      gx + side * 3.5 + (k - 1) * 0.6,
      0.18,
      pondZ + (k % 2) * 0.4,
    )
    root.add(lily)
    const bloom = new THREE.Mesh(new THREE.SphereGeometry(0.12, 6, 6), mat.blossom)
    bloom.position.set(lily.position.x, 0.28, lily.position.z)
    root.add(bloom)
  }

  // 太湖石
  for (let i = 0; i < 4; i++) {
    const rock = new THREE.Mesh(
      new THREE.DodecahedronGeometry(0.5 + (i % 2) * 0.25, 0),
      mat.rock,
    )
    rock.scale.set(1, 1.4 + (i % 3) * 0.2, 0.8)
    rock.position.set(
      gx + side * (4 + (i % 2) * 1.5),
      0.5,
      startZ + 8 + i * 9,
    )
    rock.rotation.y = i * 0.7
    root.add(rock)
  }

  // 石灯笼
  for (let i = 0; i < 3; i++) {
    const z = startZ + 10 + i * 12
    const px = gx + side * 5.5
    const base = new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.3, 0.5), mat.stone)
    base.position.set(px, 0.2, z)
    root.add(base)
    const post = new THREE.Mesh(new THREE.CylinderGeometry(0.1, 0.12, 0.9, 6), mat.stoneDark)
    post.position.set(px, 0.75, z)
    root.add(post)
    const house = new THREE.Mesh(new THREE.BoxGeometry(0.45, 0.4, 0.45), mat.stone)
    house.position.set(px, 1.3, z)
    root.add(house)
    const cap = new THREE.Mesh(new THREE.ConeGeometry(0.4, 0.3, 4), mat.stoneDark)
    cap.position.set(px, 1.65, z)
    cap.rotation.y = Math.PI / 4
    root.add(cap)
    const glow = new THREE.PointLight(0xffc070, 0.35, 5, 2)
    glow.position.set(px, 1.3, z)
    root.add(glow)
  }
}

function addBackGarden(root, mat) {
  // 殿后草坪
  const lawn = new THREE.Mesh(new THREE.BoxGeometry(30, 0.1, 14), mat.grass)
  lawn.position.set(0, 0.02, -16)
  root.add(lawn)
  for (let i = 0; i < 6; i++) {
    addTree(root, mat, -10 + i * 4, -14 - (i % 2) * 3, 0.9 + (i % 3) * 0.15, i % 2 === 0)
  }
  for (let i = 0; i < 8; i++) {
    addFlowerBush(root, mat, -8 + i * 2.2, -18, i % 2 === 0)
  }
}

function addTree(root, mat, x, z, scale, blossom) {
  const trunk = new THREE.Mesh(
    new THREE.CylinderGeometry(0.12 * scale, 0.18 * scale, 1.8 * scale, 6),
    mat.woodDark,
  )
  trunk.position.set(x, 0.9 * scale, z)
  root.add(trunk)
  const foliage = new THREE.Mesh(
    new THREE.SphereGeometry(1.1 * scale, 8, 8),
    blossom ? mat.leaf : mat.leafDark,
  )
  foliage.position.set(x, 2.2 * scale, z)
  foliage.scale.set(1.1, 0.9, 1.1)
  root.add(foliage)
  if (blossom) {
    for (let k = 0; k < 5; k++) {
      const a = (k / 5) * Math.PI * 2
      const petal = new THREE.Mesh(
        new THREE.SphereGeometry(0.2 * scale, 6, 6),
        k % 2 === 0 ? mat.blossom : mat.blossomWhite,
      )
      petal.position.set(
        x + Math.sin(a) * 0.7 * scale,
        2.1 * scale + (k % 2) * 0.25,
        z + Math.cos(a) * 0.7 * scale,
      )
      root.add(petal)
    }
  }
  // 次冠
  const top = new THREE.Mesh(
    new THREE.SphereGeometry(0.7 * scale, 7, 7),
    mat.leaf,
  )
  top.position.set(x + 0.3 * scale, 2.7 * scale, z - 0.2 * scale)
  root.add(top)
}

function addFlowerBush(root, mat, x, z, pink) {
  const bush = new THREE.Mesh(new THREE.SphereGeometry(0.45, 7, 7), mat.leafDark)
  bush.position.set(x, 0.35, z)
  bush.scale.set(1.2, 0.7, 1.1)
  root.add(bush)
  for (let k = 0; k < 4; k++) {
    const a = (k / 4) * Math.PI * 2
    const f = new THREE.Mesh(
      new THREE.SphereGeometry(0.12, 5, 5),
      pink ? mat.blossom : mat.blossomWhite,
    )
    f.position.set(x + Math.sin(a) * 0.3, 0.5, z + Math.cos(a) * 0.3)
    root.add(f)
  }
}

function addTangRoofSlopes(root, cx, cy, cz, w, d, h, material) {
  const hw = w / 2
  const hd = d / 2
  const front = new THREE.Mesh(new THREE.BoxGeometry(w * 0.96, 0.14, Math.hypot(hd, h) * 0.9), material)
  front.position.set(cx, cy + h * 0.42, cz + hd * 0.45)
  front.rotation.x = -Math.atan2(h, hd)
  root.add(front)
  const back = new THREE.Mesh(new THREE.BoxGeometry(w * 0.96, 0.14, Math.hypot(hd, h) * 0.9), material)
  back.position.set(cx, cy + h * 0.42, cz - hd * 0.45)
  back.rotation.x = Math.atan2(h, hd)
  root.add(back)
  const left = new THREE.Mesh(new THREE.BoxGeometry(Math.hypot(hw, h) * 0.85, 0.14, d * 0.65), material)
  left.position.set(cx - hw * 0.4, cy + h * 0.38, cz)
  left.rotation.z = Math.atan2(h, hw)
  root.add(left)
  const right = new THREE.Mesh(new THREE.BoxGeometry(Math.hypot(hw, h) * 0.85, 0.14, d * 0.65), material)
  right.position.set(cx + hw * 0.4, cy + h * 0.38, cz)
  right.rotation.z = -Math.atan2(h, hw)
  root.add(right)
}
