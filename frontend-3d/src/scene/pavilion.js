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

  // ——— 后墙：多层唐风影壁（真实厚重） ———
  const wallZ = -7.55
  // 主墙体
  const backWall = new THREE.Mesh(new THREE.BoxGeometry(17.2, 5.0, 0.55), mat.plaster)
  backWall.position.set(0, hallY + 2.5, wallZ)
  root.add(backWall)
  // 墙裙（下段朱红）
  const dado = new THREE.Mesh(new THREE.BoxGeometry(17.0, 1.2, 0.58), mat.redDeep)
  dado.position.set(0, hallY + 0.7, wallZ + 0.02)
  root.add(dado)
  // 金线分隔
  const dadoLine = new THREE.Mesh(new THREE.BoxGeometry(16.5, 0.08, 0.6), mat.gold)
  dadoLine.position.set(0, hallY + 1.32, wallZ + 0.04)
  root.add(dadoLine)
  // 中心影壁 thrice 框
  const frameOuter = new THREE.Mesh(new THREE.BoxGeometry(9.2, 3.6, 0.2), mat.woodDark)
  frameOuter.position.set(0, hallY + 3.0, wallZ + 0.28)
  root.add(frameOuter)
  const frameGold = new THREE.Mesh(new THREE.BoxGeometry(8.7, 3.2, 0.18), mat.goldSoft)
  frameGold.position.set(0, hallY + 3.0, wallZ + 0.36)
  root.add(frameGold)
  // 壁画底（青绿山水感）
  const muralBg = new THREE.Mesh(
    new THREE.BoxGeometry(8.1, 2.85, 0.12),
    new THREE.MeshStandardMaterial({
      color: 0x2a4a58,
      roughness: 0.7,
      metalness: 0.08,
      emissive: 0x0a1820,
      emissiveIntensity: 0.12,
    }),
  )
  muralBg.position.set(0, hallY + 3.0, wallZ + 0.44)
  root.add(muralBg)
  // 山峦层
  for (let i = 0; i < 5; i++) {
    const hill = new THREE.Mesh(
      new THREE.SphereGeometry(1.1 - i * 0.1, 8, 6, 0, Math.PI * 2, 0, Math.PI * 0.55),
      new THREE.MeshStandardMaterial({
        color: i % 2 === 0 ? 0x3a6a58 : 0x2a5048,
        roughness: 0.75,
        metalness: 0.05,
        emissive: 0x0a2018,
        emissiveIntensity: 0.08,
      }),
    )
    hill.position.set(-2.5 + i * 1.3, hallY + 2.15 + (i % 2) * 0.15, wallZ + 0.5)
    hill.scale.set(1.3, 0.9, 0.4)
    root.add(hill)
  }
  // 云纹
  for (let i = 0; i < 4; i++) {
    const cloud = new THREE.Mesh(
      new THREE.SphereGeometry(0.45, 6, 6),
      new THREE.MeshStandardMaterial({
        color: 0xd8e0e8,
        roughness: 0.6,
        metalness: 0.05,
        transparent: true,
        opacity: 0.55,
      }),
    )
    cloud.position.set(-2.8 + i * 1.8, hallY + 3.7 + (i % 2) * 0.2, wallZ + 0.52)
    cloud.scale.set(1.6, 0.55, 0.4)
    root.add(cloud)
  }
  // 中心太极 / 丹纹
  const taiji = new THREE.Mesh(
    new THREE.CircleGeometry(0.55, 24),
    new THREE.MeshStandardMaterial({
      color: 0xe0b84a,
      metalness: 0.5,
      roughness: 0.35,
      emissive: 0x6a4010,
      emissiveIntensity: 0.35,
    }),
  )
  taiji.position.set(0, hallY + 3.15, wallZ + 0.56)
  root.add(taiji)
  const taijiIn = new THREE.Mesh(
    new THREE.CircleGeometry(0.28, 16),
    new THREE.MeshStandardMaterial({
      color: 0x1a1020,
      roughness: 0.5,
      metalness: 0.2,
    }),
  )
  taijiIn.position.set(0, hallY + 3.15, wallZ + 0.58)
  root.add(taijiIn)
  // 两侧挂轴
  for (const x of [-5.8, 5.8]) {
    const scroll = new THREE.Mesh(new THREE.BoxGeometry(1.4, 3.0, 0.08), mat.redDeep)
    scroll.position.set(x, hallY + 2.9, wallZ + 0.32)
    root.add(scroll)
    const scrollGold = new THREE.Mesh(new THREE.BoxGeometry(1.5, 0.12, 0.1), mat.gold)
    scrollGold.position.set(x, hallY + 4.45, wallZ + 0.34)
    root.add(scrollGold)
    const scrollBot = new THREE.Mesh(new THREE.BoxGeometry(1.5, 0.12, 0.1), mat.gold)
    scrollBot.position.set(x, hallY + 1.35, wallZ + 0.34)
    root.add(scrollBot)
    // 轴心纹
    const ink = new THREE.Mesh(
      new THREE.BoxGeometry(0.9, 2.2, 0.06),
      new THREE.MeshStandardMaterial({ color: 0xf0e6d0, roughness: 0.8 }),
    )
    ink.position.set(x, hallY + 2.9, wallZ + 0.38)
    root.add(ink)
  }
  // 墙顶额枋彩画
  const wallBeam = new THREE.Mesh(new THREE.BoxGeometry(17.0, 0.35, 0.5), mat.woodDark)
  wallBeam.position.set(0, hallY + 5.1, wallZ + 0.1)
  root.add(wallBeam)
  const wallBeamGold = new THREE.Mesh(new THREE.BoxGeometry(17.0, 0.1, 0.52), mat.gold)
  wallBeamGold.position.set(0, hallY + 5.3, wallZ + 0.12)
  root.add(wallBeamGold)
  // 墙角立柱装饰
  for (const x of [-8.3, 8.3]) {
    const pilaster = new THREE.Mesh(new THREE.BoxGeometry(0.45, 5.0, 0.5), mat.red)
    pilaster.position.set(x, hallY + 2.5, wallZ + 0.15)
    root.add(pilaster)
    const pilGold = new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.12, 0.55), mat.gold)
    pilGold.position.set(x, hallY + 4.9, wallZ + 0.18)
    root.add(pilGold)
  }

  // ——— 左右侧墙：格扇 + 窗花 + 挂轴（唐风） ———
  for (const side of [-1, 1]) {
    const x = side * 8.7
    // 主墙板
    const wall = new THREE.Mesh(new THREE.BoxGeometry(0.35, 4.6, 10.5), mat.plaster)
    wall.position.set(x, hallY + 2.3, -2.2)
    root.add(wall)
    // 墙裙
    const dadoS = new THREE.Mesh(new THREE.BoxGeometry(0.4, 1.1, 10.3), mat.redDeep)
    dadoS.position.set(x + side * 0.02, hallY + 0.65, -2.2)
    root.add(dadoS)
    // 金线
    const lineS = new THREE.Mesh(new THREE.BoxGeometry(0.42, 0.07, 10.0), mat.gold)
    lineS.position.set(x + side * 0.03, hallY + 1.25, -2.2)
    root.add(lineS)
    // 三扇窗格
    for (let w = 0; w < 3; w++) {
      const wz = -5.5 + w * 3.2
      const frame = new THREE.Mesh(new THREE.BoxGeometry(0.18, 2.4, 2.4), mat.woodDark)
      frame.position.set(x + side * 0.12, hallY + 2.9, wz)
      root.add(frame)
      const goldF = new THREE.Mesh(new THREE.BoxGeometry(0.12, 2.15, 2.15), mat.goldSoft)
      goldF.position.set(x + side * 0.18, hallY + 2.9, wz)
      root.add(goldF)
      // 窗纸
      const paper = new THREE.Mesh(
        new THREE.BoxGeometry(0.06, 1.9, 1.9),
        new THREE.MeshStandardMaterial({
          color: 0xf5ead0,
          roughness: 0.85,
          metalness: 0.02,
          emissive: 0x403020,
          emissiveIntensity: 0.08,
          transparent: true,
          opacity: 0.9,
        }),
      )
      paper.position.set(x + side * 0.22, hallY + 2.9, wz)
      root.add(paper)
      // 窗棂十字
      const barV = new THREE.Mesh(new THREE.BoxGeometry(0.05, 1.85, 0.06), mat.woodDark)
      barV.position.set(x + side * 0.24, hallY + 2.9, wz)
      root.add(barV)
      const barH = new THREE.Mesh(new THREE.BoxGeometry(0.05, 0.06, 1.85), mat.woodDark)
      barH.position.set(x + side * 0.24, hallY + 2.9, wz)
      root.add(barH)
      // 菱花简化
      for (let g = 0; g < 4; g++) {
        const diamond = new THREE.Mesh(
          new THREE.BoxGeometry(0.04, 0.35, 0.35),
          mat.woodDark,
        )
        diamond.rotation.x = Math.PI / 4
        const gx = ((g % 2) - 0.5) * 0.7
        const gy = (Math.floor(g / 2) - 0.5) * 0.7
        diamond.position.set(x + side * 0.25, hallY + 2.9 + gy, wz + gx)
        root.add(diamond)
      }
    }
    // 侧挂灯笼
    for (const wz of [-4.5, 0.5]) {
      const chain = new THREE.Mesh(new THREE.CylinderGeometry(0.02, 0.02, 0.5, 4), mat.goldSoft)
      chain.position.set(x + side * 0.5, hallY + 4.3, wz)
      root.add(chain)
      const lan = new THREE.Mesh(
        new THREE.SphereGeometry(0.28, 8, 8),
        new THREE.MeshStandardMaterial({
          color: 0xff6a40,
          emissive: 0xff4018,
          emissiveIntensity: 0.85,
          roughness: 0.35,
        }),
      )
      lan.scale.set(1, 1.25, 1)
      lan.position.set(x + side * 0.5, hallY + 3.85, wz)
      root.add(lan)
      const light = new THREE.PointLight(0xff8040, 0.4, 6, 2)
      light.position.set(x + side * 0.5, hallY + 3.85, wz)
      root.add(light)
    }
    // 顶梁
    const topBeam = new THREE.Mesh(new THREE.BoxGeometry(0.4, 0.3, 10.5), mat.woodDark)
    topBeam.position.set(x, hallY + 4.75, -2.2)
    root.add(topBeam)
    const topGold = new THREE.Mesh(new THREE.BoxGeometry(0.42, 0.08, 10.5), mat.gold)
    topGold.position.set(x, hallY + 4.95, -2.2)
    root.add(topGold)
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
  // 额枋（前檐开敞，仅横梁，无门板遮挡）
  const beamFront = new THREE.Mesh(new THREE.BoxGeometry(16.5, 0.5, 0.55), mat.woodDark)
  beamFront.position.set(0, beamY, 2.5)
  root.add(beamFront)
  const beamGold = new THREE.Mesh(new THREE.BoxGeometry(16.6, 0.14, 0.6), mat.gold)
  beamGold.position.set(0, beamY + 0.3, 2.5)
  root.add(beamGold)
  // 前檐彩画枋
  const paintFront = new THREE.Mesh(
    new THREE.BoxGeometry(15.5, 0.28, 0.2),
    new THREE.MeshStandardMaterial({
      color: 0x1a6a5a,
      roughness: 0.5,
      metalness: 0.12,
      emissive: 0x0a3028,
      emissiveIntensity: 0.12,
    }),
  )
  paintFront.position.set(0, beamY - 0.32, 2.72)
  root.add(paintFront)
  const beamBack = new THREE.Mesh(new THREE.BoxGeometry(16.5, 0.5, 0.55), mat.woodDark)
  beamBack.position.set(0, beamY, -6.5)
  root.add(beamBack)
  for (const x of [-7.5, 7.5]) {
    const side = new THREE.Mesh(new THREE.BoxGeometry(0.55, 0.5, 9.5), mat.woodDark)
    side.position.set(x, beamY, -2)
    root.add(side)
  }

  // 匾额挂于一层前檐梁下
  const plaque = new THREE.Mesh(new THREE.BoxGeometry(3.6, 0.85, 0.14), mat.gold)
  plaque.position.set(0, beamY + 0.15, 2.95)
  root.add(plaque)
  const plaqueIn = new THREE.Mesh(new THREE.BoxGeometry(3.15, 0.62, 0.1), mat.redDeep)
  plaqueIn.position.set(0, beamY + 0.15, 3.05)
  root.add(plaqueIn)

  // 一层前檐开敞：仅门槛石
  const threshold = new THREE.Mesh(new THREE.BoxGeometry(9.5, 0.18, 0.55), mat.stone)
  threshold.position.set(0, hallY + 0.05, 2.85)
  root.add(threshold)
  for (const x of [-4.6, 4.6]) {
    const stone = new THREE.Mesh(new THREE.BoxGeometry(0.55, 0.45, 0.7), mat.stoneDark)
    stone.position.set(x, hallY + 0.25, 2.9)
    root.add(stone)
  }

  // ========== 二层楼阁（中庭贯穿 + 二层四面窗） ==========
  // 一层平座：仅四周回廊，中央中庭开洞贯通上下
  const midFloorY = beamY + 0.55
  const atriumW = 9.5
  const atriumD = 6.5
  // 外缘四段楼板（中央中庭开洞贯通）
  const deckFront = new THREE.Mesh(new THREE.BoxGeometry(18.5, 0.28, 2.6), mat.woodDark)
  deckFront.position.set(0, midFloorY, 3.0)
  root.add(deckFront)
  const deckBack = new THREE.Mesh(new THREE.BoxGeometry(18.5, 0.28, 2.4), mat.woodDark)
  deckBack.position.set(0, midFloorY, -6.7)
  root.add(deckBack)
  for (const side of [-1, 1]) {
    const deckSide = new THREE.Mesh(new THREE.BoxGeometry(4.2, 0.28, atriumD + 0.6), mat.woodDark)
    deckSide.position.set(side * 7.15, midFloorY, -2)
    root.add(deckSide)
  }
  // 回廊木地板
  const galFront = new THREE.Mesh(new THREE.BoxGeometry(17.5, 0.14, 2.4), mat.wood)
  galFront.position.set(0, midFloorY + 0.18, 2.85)
  root.add(galFront)
  const galBack = new THREE.Mesh(new THREE.BoxGeometry(17.5, 0.14, 2.2), mat.wood)
  galBack.position.set(0, midFloorY + 0.18, -6.55)
  root.add(galBack)
  for (const side of [-1, 1]) {
    const galSide = new THREE.Mesh(new THREE.BoxGeometry(3.6, 0.14, atriumD + 0.4), mat.wood)
    galSide.position.set(side * (atriumW / 2 + 1.9), midFloorY + 0.18, -2)
    root.add(galSide)
  }
  // 中庭洞口金框 + 护栏
  for (const side of [-1, 1]) {
    const wellRail = new THREE.Mesh(new THREE.BoxGeometry(0.1, 0.55, atriumD), mat.red)
    wellRail.position.set(side * (atriumW / 2), midFloorY + 0.5, -2)
    root.add(wellRail)
    const wellTop = new THREE.Mesh(new THREE.BoxGeometry(0.14, 0.07, atriumD + 0.1), mat.goldSoft)
    wellTop.position.set(side * (atriumW / 2), midFloorY + 0.82, -2)
    root.add(wellTop)
  }
  for (const sz of [-1, 1]) {
    const wellRail = new THREE.Mesh(new THREE.BoxGeometry(atriumW, 0.55, 0.1), mat.red)
    wellRail.position.set(0, midFloorY + 0.5, -2 + sz * (atriumD / 2))
    root.add(wellRail)
    const wellTop = new THREE.Mesh(new THREE.BoxGeometry(atriumW + 0.1, 0.07, 0.14), mat.goldSoft)
    wellTop.position.set(0, midFloorY + 0.82, -2 + sz * (atriumD / 2))
    root.add(wellTop)
  }

  // 外圈平座栏杆
  for (const side of [-1, 1]) {
    const rail = new THREE.Mesh(new THREE.BoxGeometry(0.12, 0.7, 11.2), mat.red)
    rail.position.set(side * 8.6, midFloorY + 0.55, -2)
    root.add(rail)
    const railTop = new THREE.Mesh(new THREE.BoxGeometry(0.16, 0.08, 11.3), mat.goldSoft)
    railTop.position.set(side * 8.6, midFloorY + 0.95, -2)
    root.add(railTop)
  }
  const railFront = new THREE.Mesh(new THREE.BoxGeometry(17.0, 0.7, 0.12), mat.red)
  railFront.position.set(0, midFloorY + 0.55, 3.6)
  root.add(railFront)
  const railFrontTop = new THREE.Mesh(new THREE.BoxGeometry(17.1, 0.08, 0.16), mat.goldSoft)
  railFrontTop.position.set(0, midFloorY + 0.95, 3.6)
  root.add(railFrontTop)
  for (let i = 0; i < 7; i++) {
    const px = -7.5 + i * 2.5
    const post = new THREE.Mesh(new THREE.CylinderGeometry(0.08, 0.09, 0.85, 6), mat.red)
    post.position.set(px, midFloorY + 0.55, 3.6)
    root.add(post)
    const ball = new THREE.Mesh(new THREE.SphereGeometry(0.1, 6, 6), mat.goldSoft)
    ball.position.set(px, midFloorY + 1.05, 3.6)
    root.add(ball)
  }

  // 一层腰檐：仅外圈挑檐，不封中庭（上下贯通）
  const lowerW = 21.5
  const lowerD = 15
  const lowerEaveY = midFloorY + 0.15
  const eaveDepth = 2.4
  // 前后挑檐坡
  for (const [sz, ez] of [[1, 3.6], [-1, -7.6]]) {
    const eave = new THREE.Mesh(new THREE.BoxGeometry(lowerW, 0.16, eaveDepth), mat.tile)
    eave.position.set(0, lowerEaveY, ez)
    eave.rotation.x = sz * 0.28
    root.add(eave)
    const trim = new THREE.Mesh(new THREE.BoxGeometry(lowerW + 0.2, 0.08, 0.14), mat.goldSoft)
    trim.position.set(0, lowerEaveY - 0.15, ez + sz * (eaveDepth * 0.45))
    root.add(trim)
  }
  // 左右挑檐坡
  for (const side of [-1, 1]) {
    const eave = new THREE.Mesh(new THREE.BoxGeometry(eaveDepth, 0.16, lowerD - 1.5), mat.tile)
    eave.position.set(side * 8.8, lowerEaveY, -2)
    eave.rotation.z = -side * 0.28
    root.add(eave)
    const trim = new THREE.Mesh(new THREE.BoxGeometry(0.14, 0.08, lowerD - 1.2), mat.goldSoft)
    trim.position.set(side * (8.8 + eaveDepth * 0.4), lowerEaveY - 0.12, -2)
    root.add(trim)
  }
  for (let i = 0; i < 9; i++) {
    const bx = -8 + i * 2
    const block = new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.32, 0.4), mat.woodDark)
    block.position.set(bx, lowerEaveY - 0.28, 2.55)
    root.add(block)
  }

  // ——— 二层楼身：回廊 + 四面窗，中庭上空贯通 ———
  const f2Y = midFloorY + 1.15
  const f2H = 3.6
  const f2ColY = f2Y + f2H / 2
  // 二层仅角柱/外圈柱，中庭无柱
  const f2Xs = [-5.5, -2.75, 2.75, 5.5]
  const f2Zs = [-5.2, 1.2]
  for (const x of f2Xs) {
    for (const z of f2Zs) {
      const r = Math.abs(x) > 4 ? 0.2 : 0.16
      const col = new THREE.Mesh(new THREE.CylinderGeometry(r, r * 1.06, f2H, 10), mat.red)
      col.position.set(x, f2ColY, z)
      root.add(col)
      const cap = new THREE.Mesh(new THREE.CylinderGeometry(r * 1.4, r * 1.5, 0.15, 8), mat.stone)
      cap.position.set(x, f2Y + 0.08, z)
      root.add(cap)
    }
  }
  // 侧廊中柱
  for (const side of [-1, 1]) {
    for (const z of [-3.5, -0.5]) {
      const col = new THREE.Mesh(new THREE.CylinderGeometry(0.16, 0.17, f2H, 10), mat.red)
      col.position.set(side * 5.5, f2ColY, z)
      root.add(col)
    }
  }
  // 二层额枋
  const f2BeamY = f2Y + f2H + 0.15
  const f2BeamF = new THREE.Mesh(new THREE.BoxGeometry(13.5, 0.4, 0.45), mat.woodDark)
  f2BeamF.position.set(0, f2BeamY, 1.2)
  root.add(f2BeamF)
  const f2BeamG = new THREE.Mesh(new THREE.BoxGeometry(13.6, 0.1, 0.5), mat.gold)
  f2BeamG.position.set(0, f2BeamY + 0.22, 1.2)
  root.add(f2BeamG)
  const f2BeamB = new THREE.Mesh(new THREE.BoxGeometry(13.5, 0.4, 0.45), mat.woodDark)
  f2BeamB.position.set(0, f2BeamY, -5.2)
  root.add(f2BeamB)
  for (const x of [-5.5, 5.5]) {
    const s = new THREE.Mesh(new THREE.BoxGeometry(0.45, 0.4, 6.8), mat.woodDark)
    s.position.set(x, f2BeamY, -2)
    root.add(s)
  }

  // 二层窗纸材质
  const windowPaperMat = new THREE.MeshStandardMaterial({
    color: 0xf5ead0,
    roughness: 0.85,
    transparent: true,
    opacity: 0.72,
    emissive: 0x403020,
    emissiveIntensity: 0.08,
  })

  // 前檐：整排格扇窗 + 中央竖匾
  const frontWinXs = [-5.0, -3.2, 3.2, 5.0]
  for (const x of frontWinXs) {
    addLatticeWindow(root, mat, windowPaperMat, x, f2Y + 1.95, 1.38, 1.7, 2.5, 0)
  }
  // 后檐格扇窗
  for (const x of [-4.5, -2.2, 0, 2.2, 4.5]) {
    addLatticeWindow(root, mat, windowPaperMat, x, f2Y + 1.95, -5.35, 1.9, 2.5, Math.PI)
  }
  // 左右侧墙改整排窗（不再实墙）
  for (const side of [-1, 1]) {
    const x = side * 5.85
    // 墙裙矮墙
    const dadoS = new THREE.Mesh(new THREE.BoxGeometry(0.22, 0.85, 7.2), mat.redDeep)
    dadoS.position.set(x, f2Y + 0.42, -2)
    root.add(dadoS)
    for (const z of [-4.4, -2.6, -0.8, 1.0]) {
      addLatticeWindow(root, mat, windowPaperMat, x, f2Y + 1.95, z, 1.55, 2.4, side > 0 ? Math.PI / 2 : -Math.PI / 2)
    }
  }

  // 二层竖匾「炼丹阁」（前檐中央）
  const plaque2Y = f2Y + 2.05
  const plaque2Z = 1.55
  const plaque2Outer = new THREE.Mesh(new THREE.BoxGeometry(1.15, 2.9, 0.16), mat.gold)
  plaque2Outer.position.set(0, plaque2Y, plaque2Z)
  root.add(plaque2Outer)
  const plaque2In = new THREE.Mesh(new THREE.BoxGeometry(0.95, 2.65, 0.1), mat.redDeep)
  plaque2In.position.set(0, plaque2Y, plaque2Z + 0.08)
  root.add(plaque2In)
  const plaque2Top = new THREE.Mesh(new THREE.BoxGeometry(1.25, 0.12, 0.2), mat.gold)
  plaque2Top.position.set(0, plaque2Y + 1.5, plaque2Z + 0.02)
  root.add(plaque2Top)
  const plaque2Bot = new THREE.Mesh(new THREE.BoxGeometry(1.25, 0.12, 0.2), mat.gold)
  plaque2Bot.position.set(0, plaque2Y - 1.5, plaque2Z + 0.02)
  root.add(plaque2Bot)
  for (const sx of [-0.35, 0.35]) {
    const ring = new THREE.Mesh(new THREE.TorusGeometry(0.08, 0.02, 6, 12), mat.gold)
    ring.position.set(sx, plaque2Y + 1.56, plaque2Z + 0.05)
    root.add(ring)
  }
  const plaque2Text = createPlaqueTextMesh('炼丹阁', 0.78, 2.35, true)
  plaque2Text.position.set(0, plaque2Y, plaque2Z + 0.15)
  root.add(plaque2Text)

  // ——— 二层庑殿顶 ———
  const upperY = f2BeamY + 0.55
  const upperW = 16.5
  const upperD = 11.5
  const upperH = 2.9
  const upperBase = new THREE.Mesh(new THREE.BoxGeometry(upperW + 0.6, 0.3, upperD + 0.6), mat.woodDark)
  upperBase.position.set(0, upperY - 0.1, -2)
  root.add(upperBase)
  const upperDeck = new THREE.Mesh(new THREE.BoxGeometry(upperW, 0.18, upperD), mat.tile)
  upperDeck.position.set(0, upperY, -2)
  root.add(upperDeck)
  addTangRoofSlopes(root, 0, upperY + 0.08, -2, upperW, upperD, upperH, mat.tile)
  addEaveTrim(root, 0, upperY + 0.04, -2, upperW, upperD, mat.goldSoft)
  // 二层斗拱
  for (let i = 0; i < 7; i++) {
    const bx = -6 + i * 2
    const block = new THREE.Mesh(new THREE.BoxGeometry(0.45, 0.28, 0.35), mat.woodDark)
    block.position.set(bx, upperY - 0.25, 1.25)
    root.add(block)
  }

  // 正脊 + 鸱尾 + 宝顶
  const ridgeY = upperY + upperH + 0.12
  const ridge = new THREE.Mesh(new THREE.BoxGeometry(12, 0.42, 0.48), mat.gold)
  ridge.position.set(0, ridgeY, -2)
  root.add(ridge)
  const ridgeTop = new THREE.Mesh(new THREE.BoxGeometry(11.6, 0.1, 0.32), mat.goldSoft)
  ridgeTop.position.set(0, ridgeY + 0.26, -2)
  root.add(ridgeTop)
  for (const x of [-5.7, 5.7]) {
    const b = new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.35, 0.5), mat.gold)
    b.position.set(x, ridgeY + 0.15, -2)
    root.add(b)
    const owl = new THREE.Mesh(new THREE.ConeGeometry(0.38, 1.5, 7), mat.gold)
    owl.position.set(x, ridgeY + 1.05, -2)
    owl.rotation.z = x > 0 ? -0.22 : 0.22
    root.add(owl)
    const tip = new THREE.Mesh(new THREE.SphereGeometry(0.12, 8, 8), mat.goldSoft)
    tip.position.set(x + (x > 0 ? 0.12 : -0.12), ridgeY + 1.8, -2)
    root.add(tip)
  }
  const finial = new THREE.Mesh(new THREE.SphereGeometry(0.42, 12, 12), mat.gold)
  finial.position.set(0, ridgeY + 0.6, -2)
  root.add(finial)
  const finialTop = new THREE.Mesh(new THREE.ConeGeometry(0.16, 0.65, 8), mat.goldSoft)
  finialTop.position.set(0, ridgeY + 1.1, -2)
  root.add(finialTop)
  // 垂脊
  for (const sx of [-1, 1]) {
    for (const sz of [-1, 1]) {
      const hip = new THREE.Mesh(new THREE.BoxGeometry(0.16, 0.16, upperH * 0.9), mat.goldSoft)
      hip.position.set(sx * (upperW * 0.28), upperY + upperH * 0.42, -2 + sz * (upperD * 0.28))
      hip.rotation.x = sz * 0.52
      hip.rotation.z = -sx * 0.32
      root.add(hip)
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
  // 前后坡（更厚、更长出檐）
  const front = new THREE.Mesh(new THREE.BoxGeometry(w * 0.98, 0.2, Math.hypot(hd, h) * 0.95), material)
  front.position.set(cx, cy + h * 0.48, cz + hd * 0.48)
  front.rotation.x = -Math.atan2(h, hd)
  root.add(front)
  const back = new THREE.Mesh(new THREE.BoxGeometry(w * 0.98, 0.2, Math.hypot(hd, h) * 0.95), material)
  back.position.set(cx, cy + h * 0.48, cz - hd * 0.48)
  back.rotation.x = Math.atan2(h, hd)
  root.add(back)
  // 左右坡
  const left = new THREE.Mesh(new THREE.BoxGeometry(Math.hypot(hw, h) * 0.92, 0.2, d * 0.72), material)
  left.position.set(cx - hw * 0.42, cy + h * 0.42, cz)
  left.rotation.z = Math.atan2(h, hw)
  root.add(left)
  const right = new THREE.Mesh(new THREE.BoxGeometry(Math.hypot(hw, h) * 0.92, 0.2, d * 0.72), material)
  right.position.set(cx + hw * 0.42, cy + h * 0.42, cz)
  right.rotation.z = -Math.atan2(h, hw)
  root.add(right)
  // 四角翼角微翘（小三角板）
  for (const sx of [-1, 1]) {
    for (const sz of [-1, 1]) {
      const wing = new THREE.Mesh(new THREE.ConeGeometry(0.9, 0.7, 4), material)
      wing.position.set(cx + sx * hw * 0.92, cy + 0.35, cz + sz * hd * 0.92)
      wing.rotation.y = Math.PI / 4
      wing.scale.set(1.2, 0.7, 1.2)
      root.add(wing)
    }
  }
}

/** 唐风格扇窗（可旋转朝向） */
function addLatticeWindow(root, mat, paperMat, x, y, z, w, h, rotY = 0) {
  const g = new THREE.Group()
  g.position.set(x, y, z)
  g.rotation.y = rotY
  const frame = new THREE.Mesh(new THREE.BoxGeometry(w, h, 0.12), mat.woodDark)
  g.add(frame)
  const goldF = new THREE.Mesh(new THREE.BoxGeometry(w - 0.12, h - 0.12, 0.08), mat.goldSoft)
  goldF.position.z = 0.02
  g.add(goldF)
  const paper = new THREE.Mesh(new THREE.BoxGeometry(w - 0.28, h - 0.28, 0.05), paperMat)
  paper.position.z = 0.05
  g.add(paper)
  // 窗棂
  const barV = new THREE.Mesh(new THREE.BoxGeometry(0.05, h - 0.32, 0.04), mat.woodDark)
  barV.position.z = 0.07
  g.add(barV)
  const barH = new THREE.Mesh(new THREE.BoxGeometry(w - 0.32, 0.05, 0.04), mat.woodDark)
  barH.position.z = 0.07
  g.add(barH)
  // 菱花
  for (let i = 0; i < 4; i++) {
    const diamond = new THREE.Mesh(new THREE.BoxGeometry(0.04, 0.28, 0.28), mat.woodDark)
    diamond.rotation.x = Math.PI / 4
    const gx = ((i % 2) - 0.5) * (w * 0.28)
    const gy = (Math.floor(i / 2) - 0.5) * (h * 0.28)
    diamond.position.set(gx, gy, 0.08)
    g.add(diamond)
  }
  root.add(g)
}

function addEaveTrim(root, cx, cy, cz, w, d, material) {
  // 四面檐口金线
  const front = new THREE.Mesh(new THREE.BoxGeometry(w + 0.3, 0.1, 0.18), material)
  front.position.set(cx, cy, cz + d / 2)
  root.add(front)
  const back = new THREE.Mesh(new THREE.BoxGeometry(w + 0.3, 0.1, 0.18), material)
  back.position.set(cx, cy, cz - d / 2)
  root.add(back)
  const left = new THREE.Mesh(new THREE.BoxGeometry(0.18, 0.1, d + 0.3), material)
  left.position.set(cx - w / 2, cy, cz)
  root.add(left)
  const right = new THREE.Mesh(new THREE.BoxGeometry(0.18, 0.1, d + 0.3), material)
  right.position.set(cx + w / 2, cy, cz)
  root.add(right)
}

/** 匾额金字（Canvas 纹理）；vertical=true 时竖排大字 */
function createPlaqueTextMesh(text, width, height, vertical = false) {
  const chars = [...text]
  const canvas = document.createElement('canvas')
  if (vertical) {
    canvas.width = 256
    canvas.height = 768
  } else {
    canvas.width = 512
    canvas.height = 128
  }
  const ctx = canvas.getContext('2d')
  ctx.clearRect(0, 0, canvas.width, canvas.height)
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.shadowColor = 'rgba(80, 40, 0, 0.7)'
  ctx.shadowBlur = 8
  ctx.shadowOffsetX = 2
  ctx.shadowOffsetY = 3
  ctx.strokeStyle = '#6a4810'
  ctx.lineWidth = vertical ? 10 : 6

  if (vertical) {
    const fontSize = Math.floor((canvas.height / chars.length) * 0.72)
    ctx.font = `bold ${fontSize}px "Noto Serif SC", "Songti SC", "SimSun", serif`
    const step = canvas.height / (chars.length + 0.4)
    const startY = step * 0.7
    for (let i = 0; i < chars.length; i++) {
      const x = canvas.width / 2
      const y = startY + i * step
      const grad = ctx.createLinearGradient(x - fontSize * 0.4, y - fontSize * 0.4, x + fontSize * 0.4, y + fontSize * 0.4)
      grad.addColorStop(0, '#fff0b0')
      grad.addColorStop(0.4, '#e8c050')
      grad.addColorStop(1, '#a07018')
      ctx.strokeText(chars[i], x, y)
      ctx.fillStyle = grad
      ctx.fillText(chars[i], x, y)
    }
  } else {
    ctx.font = 'bold 78px "Noto Serif SC", "Songti SC", "SimSun", serif'
    ctx.strokeText(text, canvas.width / 2, canvas.height / 2 + 2)
    const grad = ctx.createLinearGradient(0, 20, 0, 110)
    grad.addColorStop(0, '#ffe8a0')
    grad.addColorStop(0.45, '#e0b84a')
    grad.addColorStop(1, '#a87820')
    ctx.fillStyle = grad
    ctx.fillText(text, canvas.width / 2, canvas.height / 2 + 2)
  }

  const tex = new THREE.CanvasTexture(canvas)
  tex.colorSpace = THREE.SRGBColorSpace
  tex.needsUpdate = true
  const mat = new THREE.MeshBasicMaterial({
    map: tex,
    transparent: true,
    depthWrite: false,
    side: THREE.DoubleSide,
  })
  return new THREE.Mesh(new THREE.PlaneGeometry(width, height), mat)
}
