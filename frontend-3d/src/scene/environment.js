import * as THREE from 'three'
import { createPavilion } from './pavilion.js'

/** 飘渺宇宙背景 + 唐风丹殿长廊 */
export function createEnvironment(scene) {
  // 深空宇宙
  scene.background = new THREE.Color(0x060818)
  // 略降雾密度，避免灵力波扩到远处被雾吞掉
  scene.fog = new THREE.FogExp2(0x0a1028, 0.0045)

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

  // 八卦炉
  const pedestal = createBaguaFurnace()
  pedestal.position.copy(pavilion.hallCenter)
  pedestal.scale.setScalar(HALL_SCALE * 0.75)
  scene.add(pedestal)

  const crystal = pedestal.userData.crystal
  const coreLight = pedestal.userData.coreLight
  const flame = pedestal.userData.flame
  const baguaRing = pedestal.userData.baguaRing

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

  // 流星：细线淡扫；lookAt 后 -Z 朝前、拖尾在 +Z
  // 高度压在镜头 FOV 内（相机约 y=7 仰角有限，过高会飞出画幅）
  const meteors = []
  const METEOR_N = 5
  const _meteorUp = new THREE.Vector3(0, 1, 0)
  const _meteorMat = new THREE.Matrix4()
  const _meteorDir = new THREE.Vector3()
  const trailLen = 2.8

  for (let i = 0; i < METEOR_N; i++) {
    const head = new THREE.Mesh(
      new THREE.SphereGeometry(0.08, 6, 6),
      new THREE.MeshBasicMaterial({
        color: 0xe8eef8,
        transparent: true,
        opacity: 0,
        depthWrite: false,
      }),
    )
    const trail = new THREE.Mesh(
      new THREE.CylinderGeometry(0.006, 0.04, trailLen, 5, 1, true),
      new THREE.MeshBasicMaterial({
        color: 0x9aadc8,
        transparent: true,
        opacity: 0,
        side: THREE.DoubleSide,
        depthWrite: false,
      }),
    )
    trail.rotation.x = -Math.PI / 2
    trail.position.z = trailLen * 0.48

    const g = new THREE.Group()
    g.add(trail)
    g.add(head)
    g.visible = false
    g.renderOrder = 2
    scene.add(g)
    meteors.push({
      group: g,
      head,
      trail,
      nextT: 1 + i * 1.2 + Math.random() * 2,
      life: 0,
      duration: 1.4,
      active: false,
      start: new THREE.Vector3(),
      end: new THREE.Vector3(),
    })
  }

  function orientMeteor(group, from, to) {
    _meteorDir.subVectors(to, from)
    if (_meteorDir.lengthSq() < 1e-8) return
    _meteorMat.lookAt(from, to, _meteorUp)
    group.quaternion.setFromRotationMatrix(_meteorMat)
  }

  function spawnMeteor(m) {
    const fromLeft = Math.random() > 0.5
    // 落在主视角可见的天幕带：y 14–26、z 靠殿前
    const y0 = 14 + Math.random() * 12
    const z0 = 8 + Math.random() * 28
    const spanX = 48 + Math.random() * 20
    const dropY = 5 + Math.random() * 6
    if (fromLeft) {
      m.start.set(-42, y0 + 2, z0)
      m.end.set(m.start.x + spanX, y0 - dropY, z0 + (Math.random() - 0.5) * 8)
    } else {
      m.start.set(42, y0 + 2, z0)
      m.end.set(m.start.x - spanX, y0 - dropY, z0 + (Math.random() - 0.5) * 8)
    }
    m.life = 0
    m.duration = 1.3 + Math.random() * 0.7
    m.active = true
    m.group.visible = true
    m.group.position.copy(m.start)
    orientMeteor(m.group, m.start, m.end)
    m.head.material.opacity = 0
    m.trail.material.opacity = 0
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
    baguaRing,
    pavilion,
    meteors,
    update(t, progress = 0) {
      crystal.rotation.y = t * 0.5
      crystal.position.y = 2.55 + Math.sin(t * 1.3) * 0.05
      crystal.scale.setScalar(0.85 + progress * 0.5)
      coreLight.intensity = 1.8 + progress * 1.5 + Math.sin(t * 2.8) * 0.25
      if (flame) {
        flame.scale.y = 0.9 + Math.sin(t * 5) * 0.15 + progress * 0.3
        flame.scale.x = 0.85 + Math.sin(t * 4.2) * 0.1
        flame.scale.z = flame.scale.x
        flame.traverse((ch) => {
          if (ch.material?.opacity != null && ch.material.transparent) {
            const base = ch.userData.baseOpacity ?? ch.material.opacity
            ch.userData.baseOpacity = base
            ch.material.opacity = base * (0.85 + progress * 0.35 + Math.sin(t * 3) * 0.12)
          }
        })
      }
      if (pedestal.userData.floorBagua) {
        pedestal.userData.floorBagua.rotation.z = -t * 0.12
        pedestal.userData.floorBagua.material.opacity = 0.28 + progress * 0.2 + Math.sin(t * 1.5) * 0.05
      }
      if (baguaRing) {
        baguaRing.rotation.y = t * 0.35
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
          m.nextT = t + 2 + Math.random() * 3
          continue
        }
        const e = k * k * (3 - 2 * k)
        m.group.position.lerpVectors(m.start, m.end, e)
        orientMeteor(m.group, m.start, m.end)
        const fade = k < 0.15 ? k / 0.15 : k > 0.72 ? (1 - k) / 0.28 : 1
        m.head.material.opacity = fade * 0.55
        m.trail.material.opacity = fade * 0.22
      }
    },
  }
}

/** 八卦纹贴图（太极 + 八门爻线） */
function makeBaguaTexture() {
  const size = 512
  const c = document.createElement('canvas')
  c.width = size
  c.height = size
  const ctx = c.getContext('2d')
  const mid = size / 2

  ctx.clearRect(0, 0, size, size)

  // 外盘底
  const bg = ctx.createRadialGradient(mid, mid, mid * 0.2, mid, mid, mid * 0.98)
  bg.addColorStop(0, '#3a2410')
  bg.addColorStop(0.55, '#5a3818')
  bg.addColorStop(1, '#2a1808')
  ctx.fillStyle = bg
  ctx.beginPath()
  ctx.arc(mid, mid, mid * 0.98, 0, Math.PI * 2)
  ctx.fill()

  // 金边双环
  ctx.strokeStyle = '#e0b44a'
  ctx.lineWidth = 8
  ctx.beginPath()
  ctx.arc(mid, mid, mid * 0.94, 0, Math.PI * 2)
  ctx.stroke()
  ctx.lineWidth = 3
  ctx.beginPath()
  ctx.arc(mid, mid, mid * 0.86, 0, Math.PI * 2)
  ctx.stroke()
  ctx.beginPath()
  ctx.arc(mid, mid, mid * 0.42, 0, Math.PI * 2)
  ctx.stroke()

  // 太极
  const r = mid * 0.34
  ctx.save()
  ctx.translate(mid, mid)
  ctx.beginPath()
  ctx.arc(0, 0, r, 0, Math.PI * 2)
  ctx.fillStyle = '#f0e8d0'
  ctx.fill()
  ctx.beginPath()
  ctx.arc(0, 0, r, -Math.PI / 2, Math.PI / 2)
  ctx.fillStyle = '#1a1010'
  ctx.fill()
  ctx.beginPath()
  ctx.arc(0, -r / 2, r / 2, 0, Math.PI * 2)
  ctx.fillStyle = '#1a1010'
  ctx.fill()
  ctx.beginPath()
  ctx.arc(0, r / 2, r / 2, 0, Math.PI * 2)
  ctx.fillStyle = '#f0e8d0'
  ctx.fill()
  ctx.beginPath()
  ctx.arc(0, -r / 2, r * 0.12, 0, Math.PI * 2)
  ctx.fillStyle = '#f0e8d0'
  ctx.fill()
  ctx.beginPath()
  ctx.arc(0, r / 2, r * 0.12, 0, Math.PI * 2)
  ctx.fillStyle = '#1a1010'
  ctx.fill()
  ctx.restore()

  // 八卦爻：1=阳实线，0=阴断线；顺序：乾坤震巽坎离艮兑
  const trigrams = [
    [1, 1, 1],
    [0, 0, 0],
    [0, 0, 1],
    [1, 1, 0],
    [0, 1, 0],
    [1, 0, 1],
    [1, 0, 0],
    [0, 1, 1],
  ]
  const names = ['乾', '坤', '震', '巽', '坎', '离', '艮', '兑']
  ctx.strokeStyle = '#ffd878'
  ctx.fillStyle = '#ffd878'
  ctx.font = 'bold 28px serif'
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'

  for (let i = 0; i < 8; i++) {
    const a = (i / 8) * Math.PI * 2 - Math.PI / 2
    const cx = mid + Math.cos(a) * mid * 0.64
    const cy = mid + Math.sin(a) * mid * 0.64
    ctx.save()
    ctx.translate(cx, cy)
    ctx.rotate(a + Math.PI / 2)
    const lines = trigrams[i]
    const w = 36
    const gap = 10
    ctx.lineWidth = 5
    ctx.lineCap = 'butt'
    for (let L = 0; L < 3; L++) {
      const y = (L - 1) * gap
      if (lines[L]) {
        ctx.beginPath()
        ctx.moveTo(-w / 2, y)
        ctx.lineTo(w / 2, y)
        ctx.stroke()
      } else {
        ctx.beginPath()
        ctx.moveTo(-w / 2, y)
        ctx.lineTo(-4, y)
        ctx.stroke()
        ctx.beginPath()
        ctx.moveTo(4, y)
        ctx.lineTo(w / 2, y)
        ctx.stroke()
      }
    }
    ctx.restore()

    // 卦名
    const nx = mid + Math.cos(a) * mid * 0.78
    const ny = mid + Math.sin(a) * mid * 0.78
    ctx.fillStyle = '#e8c868'
    ctx.fillText(names[i], nx, ny)
  }

  // 八向分隔短线
  ctx.strokeStyle = 'rgba(224,180,74,0.55)'
  ctx.lineWidth = 2
  for (let i = 0; i < 8; i++) {
    const a = (i / 8) * Math.PI * 2 - Math.PI / 2 + Math.PI / 8
    ctx.beginPath()
    ctx.moveTo(mid + Math.cos(a) * mid * 0.44, mid + Math.sin(a) * mid * 0.44)
    ctx.lineTo(mid + Math.cos(a) * mid * 0.9, mid + Math.sin(a) * mid * 0.9)
    ctx.stroke()
  }

  const tex = new THREE.CanvasTexture(c)
  tex.colorSpace = THREE.SRGBColorSpace
  tex.needsUpdate = true
  return tex
}

/** 炉身竖纹（云纹 / 雷纹带） */
function makeFurnaceBodyTexture() {
  const w = 512
  const h = 256
  const c = document.createElement('canvas')
  c.width = w
  c.height = h
  const ctx = c.getContext('2d')

  const g = ctx.createLinearGradient(0, 0, 0, h)
  g.addColorStop(0, '#6a4420')
  g.addColorStop(0.35, '#8a5a28')
  g.addColorStop(0.7, '#5a3818')
  g.addColorStop(1, '#3a2410')
  ctx.fillStyle = g
  ctx.fillRect(0, 0, w, h)

  // 横箍
  ctx.strokeStyle = '#d4a84a'
  ctx.lineWidth = 6
  for (const y of [28, h / 2, h - 28]) {
    ctx.beginPath()
    ctx.moveTo(0, y)
    ctx.lineTo(w, y)
    ctx.stroke()
  }
  ctx.lineWidth = 2
  ctx.strokeStyle = 'rgba(255,220,120,0.45)'
  for (const y of [36, h / 2 + 8, h - 36]) {
    ctx.beginPath()
    ctx.moveTo(0, y)
    ctx.lineTo(w, y)
    ctx.stroke()
  }

  // 雷云纹单元
  ctx.strokeStyle = 'rgba(232,200,100,0.55)'
  ctx.lineWidth = 2.5
  for (let i = 0; i < 8; i++) {
    const x0 = i * (w / 8) + 18
    const y0 = h * 0.28
    ctx.beginPath()
    ctx.moveTo(x0, y0)
    ctx.lineTo(x0 + 18, y0)
    ctx.lineTo(x0 + 18, y0 + 14)
    ctx.lineTo(x0 + 36, y0 + 14)
    ctx.lineTo(x0 + 36, y0 + 28)
    ctx.lineTo(x0 + 14, y0 + 28)
    ctx.lineTo(x0 + 14, y0 + 42)
    ctx.stroke()

    const y1 = h * 0.62
    ctx.beginPath()
    ctx.arc(x0 + 20, y1, 12, 0.2, Math.PI * 1.6)
    ctx.stroke()
  }

  // 竖棱高光
  for (let i = 0; i < 16; i++) {
    const x = (i / 16) * w
    ctx.fillStyle = i % 2 ? 'rgba(255,200,80,0.06)' : 'rgba(0,0,0,0.12)'
    ctx.fillRect(x, 0, w / 32, h)
  }

  const tex = new THREE.CanvasTexture(c)
  tex.colorSpace = THREE.SRGBColorSpace
  tex.wrapS = THREE.RepeatWrapping
  tex.wrapT = THREE.ClampToEdgeWrapping
  tex.needsUpdate = true
  return tex
}

/** 经典八卦炉：三足 · 双耳 · 八卦盘 · 穹盖 · 炉火 */
function createBaguaFurnace() {
  const root = new THREE.Group()
  root.name = 'baguaFurnace'

  const bronze = new THREE.MeshStandardMaterial({
    color: 0x8a5a28,
    metalness: 0.72,
    roughness: 0.32,
    emissive: 0x3a1808,
    emissiveIntensity: 0.18,
  })
  const bronzeDark = new THREE.MeshStandardMaterial({
    color: 0x5a3818,
    metalness: 0.68,
    roughness: 0.4,
    emissive: 0x2a1008,
    emissiveIntensity: 0.12,
  })
  const gold = new THREE.MeshStandardMaterial({
    color: 0xd4a84a,
    metalness: 0.82,
    roughness: 0.28,
    emissive: 0x6a4010,
    emissiveIntensity: 0.35,
  })
  const goldBright = new THREE.MeshStandardMaterial({
    color: 0xffe080,
    metalness: 0.85,
    roughness: 0.22,
    emissive: 0xaa7018,
    emissiveIntensity: 0.45,
  })

  const bodyTex = makeFurnaceBodyTexture()
  const baguaTex = makeBaguaTexture()

  // 底座圆台
  const base = new THREE.Mesh(new THREE.CylinderGeometry(2.05, 2.35, 0.28, 32), bronzeDark)
  base.position.y = 0.08
  root.add(base)

  const baseRim = new THREE.Mesh(new THREE.TorusGeometry(2.15, 0.06, 8, 40), gold)
  baseRim.rotation.x = Math.PI / 2
  baseRim.position.y = 0.22
  root.add(baseRim)

  // 三足（鼎足外撇）
  for (let i = 0; i < 3; i++) {
    const a = (i / 3) * Math.PI * 2 + Math.PI / 6
    const legG = new THREE.Group()
    const foot = new THREE.Mesh(new THREE.CylinderGeometry(0.12, 0.2, 0.55, 8), bronzeDark)
    foot.position.y = 0.22
    foot.rotation.z = 0.18
    legG.add(foot)
    const pad = new THREE.Mesh(new THREE.SphereGeometry(0.18, 8, 8), gold)
    pad.scale.set(1.2, 0.45, 1.2)
    pad.position.set(0.08, 0.02, 0)
    legG.add(pad)
    const claw = new THREE.Mesh(new THREE.BoxGeometry(0.14, 0.08, 0.28), bronze)
    claw.position.set(0.16, 0.04, 0)
    legG.add(claw)
    legG.position.set(Math.sin(a) * 1.05, 0, Math.cos(a) * 1.05)
    legG.rotation.y = a
    root.add(legG)
  }

  // 炉腹（鼓腹）
  const bellyMat = bronze.clone()
  bellyMat.map = bodyTex
  bellyMat.color = new THREE.Color(0xffffff)
  const belly = new THREE.Mesh(new THREE.SphereGeometry(1.15, 28, 20, 0, Math.PI * 2, 0, Math.PI * 0.62), bellyMat)
  belly.position.y = 1.05
  belly.scale.set(1.05, 1.15, 1.05)
  root.add(belly)

  // 腹中横箍
  for (const [y, r] of [
    [0.72, 1.18],
    [1.15, 1.22],
    [1.55, 1.05],
  ]) {
    const band = new THREE.Mesh(new THREE.TorusGeometry(r, 0.045, 8, 40), gold)
    band.rotation.x = Math.PI / 2
    band.position.y = y
    root.add(band)
  }

  // 八卦盘（腰部旋转）
  const baguaRing = new THREE.Group()
  baguaRing.name = 'baguaRing'
  baguaRing.position.y = 1.35
  const disc = new THREE.Mesh(
    new THREE.CylinderGeometry(1.55, 1.55, 0.08, 48),
    new THREE.MeshStandardMaterial({
      map: baguaTex,
      color: 0xffffff,
      metalness: 0.55,
      roughness: 0.35,
      emissive: 0x4a3010,
      emissiveIntensity: 0.25,
    }),
  )
  baguaRing.add(disc)
  const discRim = new THREE.Mesh(new THREE.TorusGeometry(1.55, 0.05, 8, 48), goldBright)
  discRim.rotation.x = Math.PI / 2
  baguaRing.add(discRim)
  // 盘上立起八卦短柱装饰
  for (let i = 0; i < 8; i++) {
    const a = (i / 8) * Math.PI * 2
    const peg = new THREE.Mesh(new THREE.CylinderGeometry(0.04, 0.05, 0.18, 6), gold)
    peg.position.set(Math.cos(a) * 1.48, 0.12, Math.sin(a) * 1.48)
    baguaRing.add(peg)
  }
  root.add(baguaRing)

  // 炉颈 / 炉口
  const neck = new THREE.Mesh(new THREE.CylinderGeometry(0.72, 1.0, 0.45, 24), bronze)
  neck.position.y = 1.95
  root.add(neck)
  const mouth = new THREE.Mesh(new THREE.TorusGeometry(0.78, 0.08, 10, 32), gold)
  mouth.rotation.x = Math.PI / 2
  mouth.position.y = 2.18
  root.add(mouth)
  const mouthInner = new THREE.Mesh(
    new THREE.CylinderGeometry(0.62, 0.62, 0.12, 24),
    new THREE.MeshStandardMaterial({
      color: 0x1a0800,
      metalness: 0.3,
      roughness: 0.7,
      emissive: 0xff4a10,
      emissiveIntensity: 0.55,
    }),
  )
  mouthInner.position.y = 2.12
  root.add(mouthInner)

  // 双耳（兽环耳）
  for (const side of [-1, 1]) {
    const ear = new THREE.Group()
    const arm = new THREE.Mesh(new THREE.TorusGeometry(0.28, 0.07, 8, 20, Math.PI), bronze)
    arm.rotation.z = side > 0 ? -Math.PI / 2 : Math.PI / 2
    arm.rotation.y = Math.PI / 2
    arm.position.set(side * 1.22, 1.55, 0)
    ear.add(arm)
    const ring = new THREE.Mesh(new THREE.TorusGeometry(0.16, 0.04, 8, 16), gold)
    ring.position.set(side * 1.48, 1.35, 0)
    ring.rotation.y = Math.PI / 2
    ear.add(ring)
    const knob = new THREE.Mesh(new THREE.SphereGeometry(0.1, 8, 8), goldBright)
    knob.position.set(side * 1.22, 1.78, 0)
    ear.add(knob)
    root.add(ear)
  }

  // 穹盖
  const lid = new THREE.Mesh(
    new THREE.SphereGeometry(0.85, 24, 16, 0, Math.PI * 2, 0, Math.PI * 0.48),
    bronze.clone(),
  )
  lid.material.map = bodyTex
  lid.material.color = new THREE.Color(0xffffff)
  lid.position.y = 2.22
  root.add(lid)

  // 盖棱（八瓣）
  for (let i = 0; i < 8; i++) {
    const a = (i / 8) * Math.PI * 2
    const rib = new THREE.Mesh(new THREE.BoxGeometry(0.06, 0.08, 0.72), gold)
    rib.position.set(Math.cos(a) * 0.38, 2.48, Math.sin(a) * 0.38)
    rib.rotation.y = -a
    rib.rotation.x = 0.55
    root.add(rib)
  }

  // 盖钮 + 宝珠
  const finial = new THREE.Mesh(new THREE.CylinderGeometry(0.12, 0.18, 0.22, 10), gold)
  finial.position.y = 2.78
  root.add(finial)

  const crystal = new THREE.Mesh(
    new THREE.SphereGeometry(0.28, 16, 16),
    new THREE.MeshStandardMaterial({
      color: 0xff6a40,
      metalness: 0.25,
      roughness: 0.15,
      emissive: 0xff4a18,
      emissiveIntensity: 1.15,
      transparent: true,
      opacity: 0.95,
    }),
  )
  crystal.position.y = 2.55
  root.add(crystal)

  // 盖顶小八卦盘
  const topDisc = new THREE.Mesh(
    new THREE.CircleGeometry(0.32, 24),
    new THREE.MeshStandardMaterial({
      map: baguaTex,
      color: 0xffffff,
      metalness: 0.5,
      roughness: 0.35,
      emissive: 0x503010,
      emissiveIntensity: 0.3,
      side: THREE.DoubleSide,
    }),
  )
  topDisc.rotation.x = -Math.PI / 2
  topDisc.position.y = 2.95
  root.add(topDisc)

  const coreLight = new THREE.PointLight(0xff8040, 2.8, 32, 2)
  coreLight.position.set(0, 2.2, 0)
  root.add(coreLight)

  // 炉内火苗（多层）
  const flame = new THREE.Group()
  flame.position.y = 1.35
  const flameMats = [
    { color: 0xffd060, opacity: 0.5, s: 1, y: 0 },
    { color: 0xff8020, opacity: 0.4, s: 0.72, y: 0.15 },
    { color: 0xfff0a0, opacity: 0.35, s: 0.45, y: 0.28 },
  ]
  for (const f of flameMats) {
    const cone = new THREE.Mesh(
      new THREE.ConeGeometry(0.48 * f.s, 0.95 * f.s, 10, 1, true),
      new THREE.MeshBasicMaterial({
        color: f.color,
        transparent: true,
        opacity: f.opacity,
        side: THREE.DoubleSide,
        depthWrite: false,
      }),
    )
    cone.position.y = f.y
    flame.add(cone)
  }
  root.add(flame)

  // 地面八卦阵纹（微光）
  const floorBagua = new THREE.Mesh(
    new THREE.CircleGeometry(2.6, 48),
    new THREE.MeshBasicMaterial({
      map: baguaTex,
      transparent: true,
      opacity: 0.35,
      depthWrite: false,
      side: THREE.DoubleSide,
    }),
  )
  floorBagua.rotation.x = -Math.PI / 2
  floorBagua.position.y = 0.02
  root.add(floorBagua)

  root.userData.crystal = crystal
  root.userData.coreLight = coreLight
  root.userData.flame = flame
  root.userData.baguaRing = baguaRing
  root.userData.floorBagua = floorBagua

  return root
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
