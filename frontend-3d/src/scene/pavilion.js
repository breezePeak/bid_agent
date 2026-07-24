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
    // 唐风青灰瓦（黛色），非明清琉璃绿
    tile: new THREE.MeshStandardMaterial({
      color: 0x4a5058,
      roughness: 0.62,
      metalness: 0.12,
      emissive: 0x101418,
      emissiveIntensity: 0.06,
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

  // ========== 大殿台基（加宽加高，更显气势） ==========
  const base = new THREE.Mesh(new THREE.BoxGeometry(28, 0.85, 20), mat.stone)
  base.position.set(0, 0.3, -2)
  root.add(base)
  const base2 = new THREE.Mesh(new THREE.BoxGeometry(25.5, 0.5, 17.5), mat.stoneDark)
  base2.position.set(0, 0.82, -2)
  root.add(base2)
  const base3 = new THREE.Mesh(new THREE.BoxGeometry(23.5, 0.28, 15.5), mat.stone)
  base3.position.set(0, 1.12, -2)
  root.add(base3)
  const floor = new THREE.Mesh(new THREE.BoxGeometry(22, 0.1, 14.5), mat.wood)
  floor.position.set(0, 1.28, -2)
  root.add(floor)
  const centerFloor = new THREE.Mesh(new THREE.BoxGeometry(7.5, 0.12, 6), mat.goldSoft)
  centerFloor.position.set(0, 1.32, -2)
  root.add(centerFloor)

  // 殿前宽台阶（三道踏跺气势）
  for (let i = 0; i < 8; i++) {
    const step = new THREE.Mesh(new THREE.BoxGeometry(15 - i * 0.25, 0.18, 0.85), mat.stone)
    step.position.set(0, 0.08 + i * 0.18, 7.2 + i * 0.65)
    root.add(step)
  }
  // 御路中央斜道
  const ramp = new THREE.Mesh(new THREE.BoxGeometry(3.2, 0.12, 6.2), mat.stoneDark)
  ramp.position.set(0, 0.55, 10.2)
  ramp.rotation.x = -0.12
  root.add(ramp)

  // ========== 唐风三层丹殿主体 ==========
  const hallY = 1.35
  const colH = 6.2
  const colY = hallY + colH / 2

  // ——— 后墙：多层唐风影壁（真实厚重） ———
  const wallZ = -8.8
  // 主墙体
  const backWall = new THREE.Mesh(new THREE.BoxGeometry(21.5, 6.0, 0.6), mat.plaster)
  backWall.position.set(0, hallY + 3.0, wallZ)
  root.add(backWall)
  // 墙裙（下段朱红）
  const dado = new THREE.Mesh(new THREE.BoxGeometry(21.2, 1.4, 0.62), mat.redDeep)
  dado.position.set(0, hallY + 0.8, wallZ + 0.02)
  root.add(dado)
  // 金线分隔
  const dadoLine = new THREE.Mesh(new THREE.BoxGeometry(20.5, 0.1, 0.64), mat.gold)
  dadoLine.position.set(0, hallY + 1.55, wallZ + 0.04)
  root.add(dadoLine)
  // 中心影壁 thrice 框
  const frameOuter = new THREE.Mesh(new THREE.BoxGeometry(11.5, 4.2, 0.22), mat.woodDark)
  frameOuter.position.set(0, hallY + 3.5, wallZ + 0.3)
  root.add(frameOuter)
  const frameGold = new THREE.Mesh(new THREE.BoxGeometry(10.8, 3.7, 0.2), mat.goldSoft)
  frameGold.position.set(0, hallY + 3.5, wallZ + 0.38)
  root.add(frameGold)
  // 壁画底（青绿山水感）
  const muralBg = new THREE.Mesh(
    new THREE.BoxGeometry(10.0, 3.3, 0.12),
    new THREE.MeshStandardMaterial({
      color: 0x2a4a58,
      roughness: 0.7,
      metalness: 0.08,
      emissive: 0x0a1820,
      emissiveIntensity: 0.12,
    }),
  )
  muralBg.position.set(0, hallY + 3.5, wallZ + 0.46)
  root.add(muralBg)
  // 山峦层
  for (let i = 0; i < 6; i++) {
    const hill = new THREE.Mesh(
      new THREE.SphereGeometry(1.25 - i * 0.1, 8, 6, 0, Math.PI * 2, 0, Math.PI * 0.55),
      new THREE.MeshStandardMaterial({
        color: i % 2 === 0 ? 0x3a6a58 : 0x2a5048,
        roughness: 0.75,
        metalness: 0.05,
        emissive: 0x0a2018,
        emissiveIntensity: 0.08,
      }),
    )
    hill.position.set(-3.2 + i * 1.35, hallY + 2.45 + (i % 2) * 0.18, wallZ + 0.52)
    hill.scale.set(1.4, 0.95, 0.4)
    root.add(hill)
  }
  // 云纹
  for (let i = 0; i < 5; i++) {
    const cloud = new THREE.Mesh(
      new THREE.SphereGeometry(0.5, 6, 6),
      new THREE.MeshStandardMaterial({
        color: 0xd8e0e8,
        roughness: 0.6,
        metalness: 0.05,
        transparent: true,
        opacity: 0.55,
      }),
    )
    cloud.position.set(-3.4 + i * 1.75, hallY + 4.3 + (i % 2) * 0.2, wallZ + 0.54)
    cloud.scale.set(1.7, 0.55, 0.4)
    root.add(cloud)
  }
  // 中心太极 / 丹纹
  const taiji = new THREE.Mesh(
    new THREE.CircleGeometry(0.65, 24),
    new THREE.MeshStandardMaterial({
      color: 0xe0b84a,
      metalness: 0.5,
      roughness: 0.35,
      emissive: 0x6a4010,
      emissiveIntensity: 0.35,
    }),
  )
  taiji.position.set(0, hallY + 3.7, wallZ + 0.58)
  root.add(taiji)
  const taijiIn = new THREE.Mesh(
    new THREE.CircleGeometry(0.32, 16),
    new THREE.MeshStandardMaterial({
      color: 0x1a1020,
      roughness: 0.5,
      metalness: 0.2,
    }),
  )
  taijiIn.position.set(0, hallY + 3.7, wallZ + 0.6)
  root.add(taijiIn)
  // 两侧挂轴
  for (const x of [-7.2, 7.2]) {
    const scroll = new THREE.Mesh(new THREE.BoxGeometry(1.6, 3.5, 0.08), mat.redDeep)
    scroll.position.set(x, hallY + 3.4, wallZ + 0.32)
    root.add(scroll)
    const scrollGold = new THREE.Mesh(new THREE.BoxGeometry(1.7, 0.12, 0.1), mat.gold)
    scrollGold.position.set(x, hallY + 5.2, wallZ + 0.34)
    root.add(scrollGold)
    const scrollBot = new THREE.Mesh(new THREE.BoxGeometry(1.7, 0.12, 0.1), mat.gold)
    scrollBot.position.set(x, hallY + 1.6, wallZ + 0.34)
    root.add(scrollBot)
    const ink = new THREE.Mesh(
      new THREE.BoxGeometry(1.05, 2.6, 0.06),
      new THREE.MeshStandardMaterial({ color: 0xf0e6d0, roughness: 0.8 }),
    )
    ink.position.set(x, hallY + 3.4, wallZ + 0.38)
    root.add(ink)
  }
  // 墙顶额枋彩画
  const wallBeam = new THREE.Mesh(new THREE.BoxGeometry(21.2, 0.4, 0.55), mat.woodDark)
  wallBeam.position.set(0, hallY + 6.1, wallZ + 0.1)
  root.add(wallBeam)
  const wallBeamGold = new THREE.Mesh(new THREE.BoxGeometry(21.2, 0.12, 0.58), mat.gold)
  wallBeamGold.position.set(0, hallY + 6.35, wallZ + 0.12)
  root.add(wallBeamGold)
  // 墙角立柱装饰
  for (const x of [-10.4, 10.4]) {
    const pilaster = new THREE.Mesh(new THREE.BoxGeometry(0.55, 6.0, 0.55), mat.red)
    pilaster.position.set(x, hallY + 3.0, wallZ + 0.15)
    root.add(pilaster)
    const pilGold = new THREE.Mesh(new THREE.BoxGeometry(0.6, 0.14, 0.6), mat.gold)
    pilGold.position.set(x, hallY + 5.9, wallZ + 0.18)
    root.add(pilGold)
  }

  // ——— 左右侧墙：内外双侧格扇窗（唐风） ———
  const sidePaperMat = new THREE.MeshStandardMaterial({
    color: 0xf5ead0,
    roughness: 0.85,
    metalness: 0.02,
    emissive: 0x403020,
    emissiveIntensity: 0.1,
    transparent: true,
    opacity: 0.88,
    side: THREE.DoubleSide,
  })
  for (const side of [-1, 1]) {
    const x = side * 10.8
    // 主墙板
    const wall = new THREE.Mesh(new THREE.BoxGeometry(0.4, 5.6, 13), mat.plaster)
    wall.position.set(x, hallY + 2.8, -2.4)
    root.add(wall)
    // 墙裙（内外都有）
    for (const face of [-1, 1]) {
      const dadoS = new THREE.Mesh(new THREE.BoxGeometry(0.14, 1.25, 12.7), mat.redDeep)
      dadoS.position.set(x + face * 0.22, hallY + 0.72, -2.4)
      root.add(dadoS)
      const lineS = new THREE.Mesh(new THREE.BoxGeometry(0.12, 0.08, 12.4), mat.gold)
      lineS.position.set(x + face * 0.24, hallY + 1.4, -2.4)
      root.add(lineS)
    }
    // 四扇窗格：外侧 + 内侧各一套
    for (let w = 0; w < 4; w++) {
      const wz = -6.8 + w * 3.0
      for (const face of [-1, 1]) {
        const ox = x + face * 0.24
        const frame = new THREE.Mesh(new THREE.BoxGeometry(0.1, 2.7, 2.5), mat.woodDark)
        frame.position.set(ox, hallY + 3.4, wz)
        root.add(frame)
        const goldF = new THREE.Mesh(new THREE.BoxGeometry(0.06, 2.4, 2.25), mat.goldSoft)
        goldF.position.set(ox + face * 0.04, hallY + 3.4, wz)
        root.add(goldF)
        const paper = new THREE.Mesh(new THREE.BoxGeometry(0.04, 2.1, 2.0), sidePaperMat)
        paper.position.set(ox + face * 0.06, hallY + 3.4, wz)
        root.add(paper)
        const barV = new THREE.Mesh(new THREE.BoxGeometry(0.04, 2.05, 0.06), mat.woodDark)
        barV.position.set(ox + face * 0.08, hallY + 3.4, wz)
        root.add(barV)
        const barH = new THREE.Mesh(new THREE.BoxGeometry(0.04, 0.06, 2.0), mat.woodDark)
        barH.position.set(ox + face * 0.08, hallY + 3.4, wz)
        root.add(barH)
        for (let g = 0; g < 4; g++) {
          const diamond = new THREE.Mesh(new THREE.BoxGeometry(0.03, 0.35, 0.35), mat.woodDark)
          diamond.rotation.x = Math.PI / 4
          const gz = ((g % 2) - 0.5) * 0.75
          const gy = (Math.floor(g / 2) - 0.5) * 0.75
          diamond.position.set(ox + face * 0.09, hallY + 3.4 + gy, wz + gz)
          root.add(diamond)
        }
      }
    }
    // 殿内侧挂灯笼
    for (const wz of [-5.5, -1.5, 2.0]) {
      const chain = new THREE.Mesh(new THREE.CylinderGeometry(0.02, 0.02, 0.55, 4), mat.goldSoft)
      chain.position.set(x - side * 0.6, hallY + 5.2, wz)
      root.add(chain)
      const lan = new THREE.Mesh(
        new THREE.SphereGeometry(0.32, 8, 8),
        new THREE.MeshStandardMaterial({
          color: 0xff6a40,
          emissive: 0xff4018,
          emissiveIntensity: 0.85,
          roughness: 0.35,
        }),
      )
      lan.scale.set(1, 1.25, 1)
      lan.position.set(x - side * 0.6, hallY + 4.7, wz)
      root.add(lan)
      const light = new THREE.PointLight(0xff8040, 0.5, 8, 2)
      light.position.set(x - side * 0.6, hallY + 4.7, wz)
      root.add(light)
    }
    // 顶梁
    const topBeam = new THREE.Mesh(new THREE.BoxGeometry(0.45, 0.35, 13), mat.woodDark)
    topBeam.position.set(x, hallY + 5.75, -2.4)
    root.add(topBeam)
    const topGold = new THREE.Mesh(new THREE.BoxGeometry(0.48, 0.1, 13), mat.gold)
    topGold.position.set(x, hallY + 6.0, -2.4)
    root.add(topGold)
  }

  // 一层柱网：仅外圈角柱 + 前后檐柱，殿心留空
  const colXs = [-9.5, -4.75, 4.75, 9.5]
  const colZs = [-7.5, 3.5]
  for (const x of colXs) {
    for (const z of colZs) {
      // 前檐正中开敞：去掉最靠中的前柱
      if (z > 2 && Math.abs(x) < 5) continue
      const thick = Math.abs(x) > 8
      const r = thick ? 0.4 : 0.3
      const col = new THREE.Mesh(new THREE.CylinderGeometry(r, r * 1.1, colH, 12), mat.red)
      col.position.set(x, colY, z)
      root.add(col)
      const baseCap = new THREE.Mesh(new THREE.CylinderGeometry(r * 1.55, r * 1.7, 0.28, 10), mat.stone)
      baseCap.position.set(x, hallY + 0.12, z)
      root.add(baseCap)
      const band = new THREE.Mesh(new THREE.TorusGeometry(r * 1.08, 0.04, 6, 14), mat.gold)
      band.rotation.x = Math.PI / 2
      band.position.set(x, hallY + 0.65, z)
      root.add(band)
      const topBand = new THREE.Mesh(new THREE.TorusGeometry(r * 1.05, 0.03, 6, 12), mat.goldSoft)
      topBand.rotation.x = Math.PI / 2
      topBand.position.set(x, hallY + colH - 0.35, z)
      root.add(topBand)
    }
  }
  // 两侧中柱各一根承梁
  for (const x of [-9.5, 9.5]) {
    const col = new THREE.Mesh(new THREE.CylinderGeometry(0.36, 0.4, colH, 12), mat.red)
    col.position.set(x, colY, -2)
    root.add(col)
    const baseCap = new THREE.Mesh(new THREE.CylinderGeometry(0.55, 0.6, 0.28, 10), mat.stone)
    baseCap.position.set(x, hallY + 0.12, -2)
    root.add(baseCap)
    const band = new THREE.Mesh(new THREE.TorusGeometry(0.39, 0.04, 6, 14), mat.gold)
    band.rotation.x = Math.PI / 2
    band.position.set(x, hallY + 0.65, -2)
    root.add(band)
  }

  const beamY = hallY + colH + 0.25
  // 额枋（前檐开敞，仅横梁，无门板遮挡）
  const beamFront = new THREE.Mesh(new THREE.BoxGeometry(20.5, 0.55, 0.6), mat.woodDark)
  beamFront.position.set(0, beamY, 3.5)
  root.add(beamFront)
  const beamGold = new THREE.Mesh(new THREE.BoxGeometry(20.6, 0.16, 0.65), mat.gold)
  beamGold.position.set(0, beamY + 0.32, 3.5)
  root.add(beamGold)
  // 前檐彩画枋
  const paintFront = new THREE.Mesh(
    new THREE.BoxGeometry(19.2, 0.32, 0.22),
    new THREE.MeshStandardMaterial({
      color: 0x1a6a5a,
      roughness: 0.5,
      metalness: 0.12,
      emissive: 0x0a3028,
      emissiveIntensity: 0.12,
    }),
  )
  paintFront.position.set(0, beamY - 0.35, 3.75)
  root.add(paintFront)
  const beamBack = new THREE.Mesh(new THREE.BoxGeometry(20.5, 0.55, 0.6), mat.woodDark)
  beamBack.position.set(0, beamY, -7.5)
  root.add(beamBack)
  for (const x of [-9.5, 9.5]) {
    const side = new THREE.Mesh(new THREE.BoxGeometry(0.6, 0.55, 11.5), mat.woodDark)
    side.position.set(x, beamY, -2)
    root.add(side)
  }

  // 一层前檐开敞：仅门槛石
  const threshold = new THREE.Mesh(new THREE.BoxGeometry(11.5, 0.2, 0.6), mat.stone)
  threshold.position.set(0, hallY + 0.05, 3.9)
  root.add(threshold)
  for (const x of [-5.5, 5.5]) {
    const stone = new THREE.Mesh(new THREE.BoxGeometry(0.65, 0.55, 0.8), mat.stoneDark)
    stone.position.set(x, hallY + 0.28, 3.95)
    root.add(stone)
  }

  // ========== 二 / 三层楼阁（逐层收分，中庭贯通） ==========
  const midFloorY = beamY + 0.6
  const atriumW = 11
  const atriumD = 7.5
  // 一层平座：四周回廊 + 中庭开洞
  const deckFront = new THREE.Mesh(new THREE.BoxGeometry(23, 0.3, 3.0), mat.woodDark)
  deckFront.position.set(0, midFloorY, 3.8)
  root.add(deckFront)
  const deckBack = new THREE.Mesh(new THREE.BoxGeometry(23, 0.3, 2.8), mat.woodDark)
  deckBack.position.set(0, midFloorY, -7.9)
  root.add(deckBack)
  for (const side of [-1, 1]) {
    const deckSide = new THREE.Mesh(new THREE.BoxGeometry(5.2, 0.3, atriumD + 0.8), mat.woodDark)
    deckSide.position.set(side * 8.9, midFloorY, -2)
    root.add(deckSide)
  }
  const galFront = new THREE.Mesh(new THREE.BoxGeometry(21.5, 0.14, 2.7), mat.wood)
  galFront.position.set(0, midFloorY + 0.18, 3.6)
  root.add(galFront)
  const galBack = new THREE.Mesh(new THREE.BoxGeometry(21.5, 0.14, 2.5), mat.wood)
  galBack.position.set(0, midFloorY + 0.18, -7.7)
  root.add(galBack)
  for (const side of [-1, 1]) {
    const galSide = new THREE.Mesh(new THREE.BoxGeometry(4.4, 0.14, atriumD + 0.5), mat.wood)
    galSide.position.set(side * (atriumW / 2 + 2.4), midFloorY + 0.18, -2)
    root.add(galSide)
  }
  // 中庭护栏
  for (const side of [-1, 1]) {
    const wellRail = new THREE.Mesh(new THREE.BoxGeometry(0.12, 0.6, atriumD), mat.red)
    wellRail.position.set(side * (atriumW / 2), midFloorY + 0.52, -2)
    root.add(wellRail)
    const wellTop = new THREE.Mesh(new THREE.BoxGeometry(0.16, 0.08, atriumD + 0.1), mat.goldSoft)
    wellTop.position.set(side * (atriumW / 2), midFloorY + 0.88, -2)
    root.add(wellTop)
  }
  for (const sz of [-1, 1]) {
    const wellRail = new THREE.Mesh(new THREE.BoxGeometry(atriumW, 0.6, 0.12), mat.red)
    wellRail.position.set(0, midFloorY + 0.52, -2 + sz * (atriumD / 2))
    root.add(wellRail)
    const wellTop = new THREE.Mesh(new THREE.BoxGeometry(atriumW + 0.1, 0.08, 0.16), mat.goldSoft)
    wellTop.position.set(0, midFloorY + 0.88, -2 + sz * (atriumD / 2))
    root.add(wellTop)
  }

  // 外圈平座栏杆
  for (const side of [-1, 1]) {
    const rail = new THREE.Mesh(new THREE.BoxGeometry(0.14, 0.75, 13.5), mat.red)
    rail.position.set(side * 10.7, midFloorY + 0.58, -2)
    root.add(rail)
    const railTop = new THREE.Mesh(new THREE.BoxGeometry(0.18, 0.09, 13.6), mat.goldSoft)
    railTop.position.set(side * 10.7, midFloorY + 1.0, -2)
    root.add(railTop)
  }
  const railFront = new THREE.Mesh(new THREE.BoxGeometry(21.2, 0.75, 0.14), mat.red)
  railFront.position.set(0, midFloorY + 0.58, 4.5)
  root.add(railFront)
  const railFrontTop = new THREE.Mesh(new THREE.BoxGeometry(21.3, 0.09, 0.18), mat.goldSoft)
  railFrontTop.position.set(0, midFloorY + 1.0, 4.5)
  root.add(railFrontTop)
  for (let i = 0; i < 9; i++) {
    const px = -9.5 + i * 2.4
    const post = new THREE.Mesh(new THREE.CylinderGeometry(0.09, 0.1, 0.9, 6), mat.red)
    post.position.set(px, midFloorY + 0.58, 4.5)
    root.add(post)
    const ball = new THREE.Mesh(new THREE.SphereGeometry(0.11, 6, 6), mat.goldSoft)
    ball.position.set(px, midFloorY + 1.12, 4.5)
    root.add(ball)
  }

  // 一层腰檐
  const lowerW = 26.5
  const lowerD = 18
  const lowerEaveY = midFloorY + 0.15
  const eaveDepth = 2.8
  for (const [sz, ez] of [[1, 4.5], [-1, -9.0]]) {
    const eave = new THREE.Mesh(new THREE.BoxGeometry(lowerW, 0.18, eaveDepth), mat.tile)
    eave.position.set(0, lowerEaveY, ez)
    eave.rotation.x = sz * 0.28
    root.add(eave)
    const trim = new THREE.Mesh(new THREE.BoxGeometry(lowerW + 0.25, 0.09, 0.15), mat.goldSoft)
    trim.position.set(0, lowerEaveY - 0.16, ez + sz * (eaveDepth * 0.45))
    root.add(trim)
  }
  for (const side of [-1, 1]) {
    const eave = new THREE.Mesh(new THREE.BoxGeometry(eaveDepth, 0.18, lowerD - 1.8), mat.tile)
    eave.position.set(side * 10.9, lowerEaveY, -2)
    eave.rotation.z = -side * 0.28
    root.add(eave)
    const trim = new THREE.Mesh(new THREE.BoxGeometry(0.15, 0.09, lowerD - 1.5), mat.goldSoft)
    trim.position.set(side * (10.9 + eaveDepth * 0.4), lowerEaveY - 0.13, -2)
    root.add(trim)
  }
  for (let i = 0; i < 11; i++) {
    const bx = -10 + i * 2
    const block = new THREE.Mesh(new THREE.BoxGeometry(0.55, 0.35, 0.42), mat.woodDark)
    block.position.set(bx, lowerEaveY - 0.3, 3.35)
    root.add(block)
  }

  // ——— 二层楼身：仅四角 + 前后檐柱 ———
  const f2Y = midFloorY + 1.2
  const f2H = 4.0
  const f2ColY = f2Y + f2H / 2
  const f2Cols = [
    [-7.0, -6.0],
    [7.0, -6.0],
    [-7.0, 1.8],
    [7.0, 1.8],
    [-3.5, -6.0],
    [3.5, -6.0],
    [-3.5, 1.8],
    [3.5, 1.8],
  ]
  for (const [x, z] of f2Cols) {
    const r = Math.abs(x) > 5 ? 0.22 : 0.17
    const col = new THREE.Mesh(new THREE.CylinderGeometry(r, r * 1.06, f2H, 10), mat.red)
    col.position.set(x, f2ColY, z)
    root.add(col)
    const cap = new THREE.Mesh(new THREE.CylinderGeometry(r * 1.4, r * 1.5, 0.16, 8), mat.stone)
    cap.position.set(x, f2Y + 0.08, z)
    root.add(cap)
  }
  const f2BeamY = f2Y + f2H + 0.18
  const f2BeamF = new THREE.Mesh(new THREE.BoxGeometry(16.5, 0.42, 0.48), mat.woodDark)
  f2BeamF.position.set(0, f2BeamY, 1.8)
  root.add(f2BeamF)
  const f2BeamG = new THREE.Mesh(new THREE.BoxGeometry(16.6, 0.12, 0.52), mat.gold)
  f2BeamG.position.set(0, f2BeamY + 0.24, 1.8)
  root.add(f2BeamG)
  const f2BeamB = new THREE.Mesh(new THREE.BoxGeometry(16.5, 0.42, 0.48), mat.woodDark)
  f2BeamB.position.set(0, f2BeamY, -6.0)
  root.add(f2BeamB)
  for (const x of [-7.0, 7.0]) {
    const s = new THREE.Mesh(new THREE.BoxGeometry(0.48, 0.42, 8.2), mat.woodDark)
    s.position.set(x, f2BeamY, -2)
    root.add(s)
  }

  const windowPaperMat = new THREE.MeshStandardMaterial({
    color: 0xf5ead0,
    roughness: 0.85,
    transparent: true,
    opacity: 0.72,
    emissive: 0x403020,
    emissiveIntensity: 0.08,
  })

  for (const x of [-6.2, -4.0, 4.0, 6.2]) {
    addLatticeWindow(root, mat, windowPaperMat, x, f2Y + 2.15, 2.0, 1.75, 2.6, 0)
  }
  for (const x of [-5.5, -2.8, 0, 2.8, 5.5]) {
    addLatticeWindow(root, mat, windowPaperMat, x, f2Y + 2.15, -6.15, 1.95, 2.6, Math.PI)
  }
  for (const side of [-1, 1]) {
    const x = side * 7.35
    const dadoS = new THREE.Mesh(new THREE.BoxGeometry(0.24, 0.9, 8.5), mat.redDeep)
    dadoS.position.set(x, f2Y + 0.45, -2)
    root.add(dadoS)
    for (const z of [-5.2, -3.0, -0.8, 1.2]) {
      addLatticeWindow(root, mat, windowPaperMat, x, f2Y + 2.15, z, 1.6, 2.5, side > 0 ? Math.PI / 2 : -Math.PI / 2)
    }
  }

  // 二层腰檐 + 三层平座
  const mid2FloorY = f2BeamY + 0.55
  const mid2DeckF = new THREE.Mesh(new THREE.BoxGeometry(17.5, 0.26, 2.4), mat.woodDark)
  mid2DeckF.position.set(0, mid2FloorY, 2.2)
  root.add(mid2DeckF)
  const mid2DeckB = new THREE.Mesh(new THREE.BoxGeometry(17.5, 0.26, 2.2), mat.woodDark)
  mid2DeckB.position.set(0, mid2FloorY, -6.2)
  root.add(mid2DeckB)
  for (const side of [-1, 1]) {
    const d = new THREE.Mesh(new THREE.BoxGeometry(3.6, 0.26, 7.2), mat.woodDark)
    d.position.set(side * 7.0, mid2FloorY, -2)
    root.add(d)
  }
  // 二层腰檐
  const midEaveW = 20.5
  const midEaveD = 14
  const midEaveY = mid2FloorY + 0.12
  const midEaveDepth = 2.2
  for (const [sz, ez] of [[1, 2.6], [-1, -6.6]]) {
    const eave = new THREE.Mesh(new THREE.BoxGeometry(midEaveW, 0.15, midEaveDepth), mat.tile)
    eave.position.set(0, midEaveY, ez)
    eave.rotation.x = sz * 0.3
    root.add(eave)
    const trim = new THREE.Mesh(new THREE.BoxGeometry(midEaveW + 0.2, 0.08, 0.12), mat.goldSoft)
    trim.position.set(0, midEaveY - 0.14, ez + sz * (midEaveDepth * 0.45))
    root.add(trim)
  }
  for (const side of [-1, 1]) {
    const eave = new THREE.Mesh(new THREE.BoxGeometry(midEaveDepth, 0.15, midEaveD - 1.5), mat.tile)
    eave.position.set(side * 8.4, midEaveY, -2)
    eave.rotation.z = -side * 0.3
    root.add(eave)
  }
  // 二层平座栏杆
  for (const side of [-1, 1]) {
    const rail = new THREE.Mesh(new THREE.BoxGeometry(0.12, 0.65, 10), mat.red)
    rail.position.set(side * 8.2, mid2FloorY + 0.5, -2)
    root.add(rail)
    const rt = new THREE.Mesh(new THREE.BoxGeometry(0.15, 0.07, 10.1), mat.goldSoft)
    rt.position.set(side * 8.2, mid2FloorY + 0.88, -2)
    root.add(rt)
  }
  const rail2F = new THREE.Mesh(new THREE.BoxGeometry(16.2, 0.65, 0.12), mat.red)
  rail2F.position.set(0, mid2FloorY + 0.5, 2.9)
  root.add(rail2F)
  const rail2FT = new THREE.Mesh(new THREE.BoxGeometry(16.3, 0.07, 0.15), mat.goldSoft)
  rail2FT.position.set(0, mid2FloorY + 0.88, 2.9)
  root.add(rail2FT)

  // ——— 三层楼身：仅四角柱 ———
  const f3Y = mid2FloorY + 1.05
  const f3H = 3.4
  const f3ColY = f3Y + f3H / 2
  const f3Cols = [
    [-5.0, -4.6],
    [5.0, -4.6],
    [-5.0, 0.8],
    [5.0, 0.8],
  ]
  for (const [x, z] of f3Cols) {
    const r = 0.18
    const col = new THREE.Mesh(new THREE.CylinderGeometry(r, r * 1.06, f3H, 10), mat.red)
    col.position.set(x, f3ColY, z)
    root.add(col)
    const cap = new THREE.Mesh(new THREE.CylinderGeometry(r * 1.35, r * 1.45, 0.14, 8), mat.stone)
    cap.position.set(x, f3Y + 0.07, z)
    root.add(cap)
  }
  const f3BeamY = f3Y + f3H + 0.15
  const f3BeamF = new THREE.Mesh(new THREE.BoxGeometry(12.2, 0.38, 0.42), mat.woodDark)
  f3BeamF.position.set(0, f3BeamY, 0.8)
  root.add(f3BeamF)
  const f3BeamG = new THREE.Mesh(new THREE.BoxGeometry(12.3, 0.1, 0.46), mat.gold)
  f3BeamG.position.set(0, f3BeamY + 0.2, 0.8)
  root.add(f3BeamG)
  const f3BeamB = new THREE.Mesh(new THREE.BoxGeometry(12.2, 0.38, 0.42), mat.woodDark)
  f3BeamB.position.set(0, f3BeamY, -4.6)
  root.add(f3BeamB)
  for (const x of [-5.0, 5.0]) {
    const s = new THREE.Mesh(new THREE.BoxGeometry(0.42, 0.38, 5.8), mat.woodDark)
    s.position.set(x, f3BeamY, -1.9)
    root.add(s)
  }
  // 三层格扇窗
  for (const x of [-4.0, 4.0]) {
    addLatticeWindow(root, mat, windowPaperMat, x, f3Y + 1.85, 1.0, 1.55, 2.3, 0)
  }
  for (const x of [-3.5, 0, 3.5]) {
    addLatticeWindow(root, mat, windowPaperMat, x, f3Y + 1.85, -4.75, 1.7, 2.3, Math.PI)
  }
  for (const side of [-1, 1]) {
    const x = side * 5.3
    const dadoS = new THREE.Mesh(new THREE.BoxGeometry(0.2, 0.8, 6.2), mat.redDeep)
    dadoS.position.set(x, f3Y + 0.4, -1.9)
    root.add(dadoS)
    for (const z of [-3.8, -1.6, 0.4]) {
      addLatticeWindow(root, mat, windowPaperMat, x, f3Y + 1.85, z, 1.4, 2.2, side > 0 ? Math.PI / 2 : -Math.PI / 2)
    }
  }
  // 仅顶层一块竖匾「炼丹阁」
  const plaqueY = f3Y + 1.9
  const plaqueZ = 1.15
  const plaqueOuter = new THREE.Mesh(new THREE.BoxGeometry(1.15, 2.9, 0.16), mat.gold)
  plaqueOuter.position.set(0, plaqueY, plaqueZ)
  root.add(plaqueOuter)
  const plaqueIn = new THREE.Mesh(new THREE.BoxGeometry(0.95, 2.65, 0.1), mat.redDeep)
  plaqueIn.position.set(0, plaqueY, plaqueZ + 0.08)
  root.add(plaqueIn)
  const plaqueTop = new THREE.Mesh(new THREE.BoxGeometry(1.25, 0.12, 0.2), mat.gold)
  plaqueTop.position.set(0, plaqueY + 1.5, plaqueZ + 0.02)
  root.add(plaqueTop)
  const plaqueBot = new THREE.Mesh(new THREE.BoxGeometry(1.25, 0.12, 0.2), mat.gold)
  plaqueBot.position.set(0, plaqueY - 1.5, plaqueZ + 0.02)
  root.add(plaqueBot)
  const plaqueText = createPlaqueTextMesh('炼丹阁', 0.78, 2.35, true)
  plaqueText.position.set(0, plaqueY, plaqueZ + 0.15)
  root.add(plaqueText)

  // ——— 三层庑殿顶（主脊） ———
  const upperY = f3BeamY + 0.5
  const upperW = 14.5
  const upperD = 10
  const upperH = 3.2
  const upperBase = new THREE.Mesh(new THREE.BoxGeometry(upperW + 0.7, 0.32, upperD + 0.7), mat.woodDark)
  upperBase.position.set(0, upperY - 0.1, -1.9)
  root.add(upperBase)
  const upperDeck = new THREE.Mesh(new THREE.BoxGeometry(upperW, 0.18, upperD), mat.tile)
  upperDeck.position.set(0, upperY, -1.9)
  root.add(upperDeck)
  addTangRoofSlopes(root, 0, upperY + 0.08, -1.9, upperW, upperD, upperH, mat.tile)
  addEaveTrim(root, 0, upperY + 0.04, -1.9, upperW, upperD, mat.goldSoft)
  for (let i = 0; i < 7; i++) {
    const bx = -5.5 + i * 1.85
    const block = new THREE.Mesh(new THREE.BoxGeometry(0.48, 0.3, 0.36), mat.woodDark)
    block.position.set(bx, upperY - 0.26, 0.85)
    root.add(block)
  }

  // 正脊 + 鸱尾 + 宝顶
  const ridgeY = upperY + upperH + 0.14
  const ridge = new THREE.Mesh(new THREE.BoxGeometry(10.5, 0.48, 0.52), mat.gold)
  ridge.position.set(0, ridgeY, -1.9)
  root.add(ridge)
  const ridgeTop = new THREE.Mesh(new THREE.BoxGeometry(10.1, 0.12, 0.35), mat.goldSoft)
  ridgeTop.position.set(0, ridgeY + 0.28, -1.9)
  root.add(ridgeTop)
  for (const x of [-4.9, 4.9]) {
    const b = new THREE.Mesh(new THREE.BoxGeometry(0.55, 0.4, 0.55), mat.gold)
    b.position.set(x, ridgeY + 0.18, -1.9)
    root.add(b)
    const owl = new THREE.Mesh(new THREE.ConeGeometry(0.45, 1.7, 7), mat.gold)
    owl.position.set(x, ridgeY + 1.15, -1.9)
    owl.rotation.z = x > 0 ? -0.22 : 0.22
    root.add(owl)
    const tip = new THREE.Mesh(new THREE.SphereGeometry(0.14, 8, 8), mat.goldSoft)
    tip.position.set(x + (x > 0 ? 0.14 : -0.14), ridgeY + 2.0, -1.9)
    root.add(tip)
  }
  const finial = new THREE.Mesh(new THREE.SphereGeometry(0.5, 12, 12), mat.gold)
  finial.position.set(0, ridgeY + 0.7, -1.9)
  root.add(finial)
  const finialMid = new THREE.Mesh(new THREE.SphereGeometry(0.28, 10, 10), mat.goldSoft)
  finialMid.position.set(0, ridgeY + 1.15, -1.9)
  root.add(finialMid)
  const finialTop = new THREE.Mesh(new THREE.ConeGeometry(0.18, 0.75, 8), mat.goldSoft)
  finialTop.position.set(0, ridgeY + 1.65, -1.9)
  root.add(finialTop)
  // 垂脊
  for (const sx of [-1, 1]) {
    for (const sz of [-1, 1]) {
      const hip = new THREE.Mesh(new THREE.BoxGeometry(0.16, 0.16, upperH * 0.95), mat.goldSoft)
      hip.position.set(sx * (upperW * 0.28), upperY + upperH * 0.42, -1.9 + sz * (upperD * 0.28))
      hip.rotation.x = sz * 0.52
      hip.rotation.z = -sx * 0.32
      root.add(hip)
    }
  }

  const altar = new THREE.Mesh(new THREE.CylinderGeometry(2.6, 2.9, 0.32, 24), mat.goldSoft)
  altar.position.set(0, hallY + 0.15, -2)
  root.add(altar)

  // ========== 宽阔无顶御道 ==========
  const avenueLen = 52
  const avenueStartZ = 12
  const avenueW = 16
  const carpetW = 4.8
  const poleSpacing = 4.5
  const poleCount = Math.floor(avenueLen / poleSpacing)
  const poleH = 4.6
  const poleX = avenueW / 2 + 0.9

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

  const hallLight = new THREE.PointLight(0xffd0a0, 1.6, 28, 2)
  hallLight.position.set(0, 5.2, -2)
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

  // 脊顶约 y≈ ridgeY+2 ≈ 28（本地），供工序轨高度参考
  const roofPeakY = ridgeY + 2.2

  return {
    root,
    roofPeakY,
    hallCenter: new THREE.Vector3(0, hallY + 0.2, -2),
    doorPos: new THREE.Vector3(0, hallY + 0.5, 4.2),
    bossStand: new THREE.Vector3(0, hallY + 0.2, 0.8),
    workSlots: [
      new THREE.Vector3(-3.0, hallY + 0.2, -1.5),
      new THREE.Vector3(3.0, hallY + 0.2, -1.5),
      new THREE.Vector3(-2.4, hallY + 0.2, 0.8),
      new THREE.Vector3(2.4, hallY + 0.2, 0.8),
      new THREE.Vector3(-3.8, hallY + 0.2, -0.2),
      new THREE.Vector3(3.8, hallY + 0.2, -0.2),
    ],
    queueOrigin: new THREE.Vector3(0, 0.35, endZ - 1),
    queueSlots,
    loungeSlots,
    update(t) {
      hallLight.intensity = 1.4 + Math.sin(t * 2) * 0.12
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
