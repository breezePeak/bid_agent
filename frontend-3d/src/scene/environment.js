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

  // 唐风三层大殿 + 宽阔御道（更大气）
  const pavilion = createPavilion(scene)
  const HALL_SCALE = 1.28
  pavilion.root.scale.setScalar(HALL_SCALE)
  pavilion.root.position.set(0, 0, -10)

  // 外围山水庭院（建筑外：假山 / 小溪 / 绿植）
  const outerLandscape = addOuterLandscape(scene, HALL_SCALE)

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
    outerLandscape,
    meteors,
    update(t, progress = 0) {
      const ritualBoost = pedestal.userData.ritualBoost || 0
      crystal.rotation.y = t * 0.5
      // crystal 在盖组本地坐标（盖顶宝珠）
      crystal.position.y = 0.95 + Math.sin(t * 1.3) * 0.04
      crystal.scale.setScalar(0.85 + progress * 0.5 + ritualBoost * 0.2)
      // 九龙头微动（仰首吐息）
      const dragons = pedestal.userData.dragonHeads
      if (dragons) {
        dragons.children.forEach((h, i) => {
          h.rotation.x = -0.55 + Math.sin(t * 1.6 + i * 0.7) * 0.05
          h.position.y = 2.22 + Math.sin(t * 2.0 + i) * 0.015
        })
      }
      if (flame) {
        const uf = pedestal.userData.underFire
        const mf = pedestal.userData.mouthFire
        const sp = pedestal.userData.sparks
        const ml = pedestal.userData.mouthLight
        const flick = 0.88 + Math.sin(t * 7.5) * 0.1 + Math.sin(t * 13.2) * 0.06
        const boost = 0.75 + progress * 0.55 + ritualBoost * 0.45
        if (uf) {
          uf.scale.set(0.95 + Math.sin(t * 4.5) * 0.08, 0.9 + Math.sin(t * 6) * 0.18 + progress * 0.35, 0.95 + Math.cos(t * 4.2) * 0.08)
          uf.rotation.y = Math.sin(t * 2.2) * 0.08
        }
        if (mf) {
          mf.scale.set(0.9 + Math.sin(t * 5.5) * 0.1, 0.85 + Math.sin(t * 8) * 0.2 + progress * 0.25, 0.9 + Math.cos(t * 5) * 0.1)
        }
        flame.traverse((ch) => {
          if (ch.material?.opacity != null && ch.material.transparent && ch.isMesh) {
            const base = ch.userData.baseOpacity ?? ch.material.opacity
            ch.userData.baseOpacity = base
            ch.material.opacity = Math.min(1, base * flick * boost)
          }
        })
        if (sp?.geometry) {
          const arr = sp.geometry.attributes.position.array
          for (let i = 0; i < arr.length; i += 3) {
            arr[i + 1] += (0.35 + progress * 0.4) * 0.016
            if (arr[i + 1] > 1.15) {
              const a = Math.random() * Math.PI * 2
              const r = Math.random() * 0.4
              arr[i] = Math.cos(a) * r
              arr[i + 1] = 0.15 + Math.random() * 0.2
              arr[i + 2] = Math.sin(a) * r
            }
          }
          sp.geometry.attributes.position.needsUpdate = true
          sp.material.opacity = 0.55 + progress * 0.35 + Math.sin(t * 9) * 0.1
        }
        coreLight.intensity = 2.2 + progress * 2.2 + Math.sin(t * 5.5) * 0.4
        if (ml) ml.intensity = 1.4 + progress * 1.6 + Math.sin(t * 6.5) * 0.35
      }
      if (pedestal.userData.floorBagua) {
        pedestal.userData.floorBagua.rotation.z = -t * 0.12
        pedestal.userData.floorBagua.material.opacity =
          0.28 + progress * 0.2 + Math.sin(t * 1.5) * 0.05 + ritualBoost * 0.35
      }
      if (baguaRing) {
        baguaRing.rotation.y = t * (0.35 + ritualBoost * 1.2)
      }
      // 仪式八卦符文
      const runes = pedestal.userData.ritualRunes
      if (runes?.visible) {
        const meshes = pedestal.userData.runeMeshes || []
        const rings = pedestal.userData.ritualRings || []
        const rays = pedestal.userData.ritualRays || []
        runes.rotation.y = t * 0.55
        for (let i = 0; i < meshes.length; i++) {
          const m = meshes[i]
          const a = m.userData.baseAngle + t * 0.4
          const r = m.userData.radius + Math.sin(t * 2 + i) * 0.08
          m.position.x = Math.cos(a) * r
          m.position.z = Math.sin(a) * r
          m.position.y = 0.2 + Math.sin(t * 2.5 + i * 0.7) * 0.12 + ritualBoost * 0.3
          const s = 0.75 + Math.sin(t * 3 + i) * 0.08
          m.scale.set(s, s, 1)
          if (m.material) m.material.opacity = Math.min(1, 0.55 + ritualBoost * 0.45)
        }
        for (let i = 0; i < rings.length; i++) {
          const ringM = rings[i]
          ringM.rotation.z = t * (0.2 + i * 0.08) * (i % 2 ? -1 : 1)
          if (ringM.material) {
            ringM.material.opacity = Math.min(0.7, (0.15 + i * 0.08) * (0.4 + ritualBoost))
          }
        }
        for (const ray of rays) {
          if (ray.material) ray.material.opacity = Math.min(0.55, 0.12 + ritualBoost * 0.4)
        }
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

/** 九龙头炼丹炉：九龙环口 · 三足鼎身 · 可开合穹盖 · 八卦阵 */
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
  const bronzeLite = new THREE.MeshStandardMaterial({
    color: 0xa87030,
    metalness: 0.7,
    roughness: 0.34,
    emissive: 0x401808,
    emissiveIntensity: 0.14,
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
  const scaleMat = new THREE.MeshStandardMaterial({
    color: 0x6a4020,
    metalness: 0.55,
    roughness: 0.45,
    emissive: 0x2a1008,
    emissiveIntensity: 0.12,
  })
  const eyeMat = new THREE.MeshStandardMaterial({
    color: 0xffe060,
    metalness: 0.4,
    roughness: 0.2,
    emissive: 0xff8020,
    emissiveIntensity: 0.95,
  })
  const tongueMat = new THREE.MeshStandardMaterial({
    color: 0xc01818,
    metalness: 0.25,
    roughness: 0.4,
    emissive: 0x600808,
    emissiveIntensity: 0.35,
  })

  const bodyTex = makeFurnaceBodyTexture()
  const baguaTex = makeBaguaTexture()

  // 底座圆台（更厚重）
  const base = new THREE.Mesh(new THREE.CylinderGeometry(2.35, 2.65, 0.32, 36), bronzeDark)
  base.position.y = 0.1
  root.add(base)
  const base2 = new THREE.Mesh(new THREE.CylinderGeometry(2.05, 2.25, 0.18, 32), bronze)
  base2.position.y = 0.32
  root.add(base2)
  const baseRim = new THREE.Mesh(new THREE.TorusGeometry(2.4, 0.07, 8, 48), gold)
  baseRim.rotation.x = Math.PI / 2
  baseRim.position.y = 0.28
  root.add(baseRim)

  // 三足（兽爪鼎足）
  for (let i = 0; i < 3; i++) {
    const a = (i / 3) * Math.PI * 2 + Math.PI / 6
    const legG = new THREE.Group()
    const thigh = new THREE.Mesh(new THREE.CylinderGeometry(0.14, 0.22, 0.7, 8), bronzeDark)
    thigh.position.set(0.12, 0.35, 0)
    thigh.rotation.z = 0.28
    legG.add(thigh)
    const knee = new THREE.Mesh(new THREE.SphereGeometry(0.16, 8, 8), gold)
    knee.position.set(0.28, 0.12, 0)
    legG.add(knee)
    const claw = new THREE.Mesh(new THREE.BoxGeometry(0.28, 0.1, 0.36), bronze)
    claw.position.set(0.38, 0.05, 0)
    legG.add(claw)
    for (const z of [-0.1, 0, 0.1]) {
      const toe = new THREE.Mesh(new THREE.ConeGeometry(0.05, 0.18, 5), gold)
      toe.rotation.z = -Math.PI / 2
      toe.position.set(0.55, 0.06, z)
      legG.add(toe)
    }
    legG.position.set(Math.sin(a) * 1.15, 0, Math.cos(a) * 1.15)
    legG.rotation.y = a
    root.add(legG)
  }

  // 炉腹：鼓腹壳体（顶部开口接炉颈，勿用实心球冠封顶）
  const bellyMat = bronze.clone()
  bellyMat.map = bodyTex
  bellyMat.color = new THREE.Color(0xffffff)
  bellyMat.side = THREE.DoubleSide
  // theta 从 ~0.55 起：跳过北极实心顶，形成敞口鼓腹
  const belly = new THREE.Mesh(
    new THREE.SphereGeometry(1.28, 32, 22, 0, Math.PI * 2, 0.55, Math.PI * 0.55),
    bellyMat,
  )
  belly.position.y = 1.15
  belly.scale.set(1.08, 1.15, 1.08)
  root.add(belly)
  // 上口收边（环，不封口）
  const bellyRim = new THREE.Mesh(new THREE.TorusGeometry(0.95, 0.06, 8, 32), gold)
  bellyRim.rotation.x = Math.PI / 2
  bellyRim.position.y = 1.85
  root.add(bellyRim)
  // 下收腹（空心管）
  const lower = new THREE.Mesh(
    new THREE.CylinderGeometry(1.15, 1.35, 0.55, 28, 1, true),
    bronzeDark.clone(),
  )
  lower.material.side = THREE.DoubleSide
  lower.position.y = 0.55
  root.add(lower)

  // 腹上龙纹箍
  for (const [y, r, thick] of [
    [0.78, 1.32, 0.05],
    [1.25, 1.38, 0.055],
    [1.72, 1.18, 0.05],
  ]) {
    const band = new THREE.Mesh(new THREE.TorusGeometry(r, thick, 8, 48), gold)
    band.rotation.x = Math.PI / 2
    band.position.y = y
    root.add(band)
  }

  // 八卦盘（腰部旋转）
  const baguaRing = new THREE.Group()
  baguaRing.name = 'baguaRing'
  baguaRing.position.y = 1.48
  const disc = new THREE.Mesh(
    new THREE.CylinderGeometry(1.72, 1.72, 0.09, 48),
    new THREE.MeshStandardMaterial({
      map: baguaTex,
      color: 0xffffff,
      metalness: 0.55,
      roughness: 0.35,
      emissive: 0x4a3010,
      emissiveIntensity: 0.28,
    }),
  )
  baguaRing.add(disc)
  const discRim = new THREE.Mesh(new THREE.TorusGeometry(1.72, 0.055, 8, 48), goldBright)
  discRim.rotation.x = Math.PI / 2
  baguaRing.add(discRim)
  for (let i = 0; i < 8; i++) {
    const a = (i / 8) * Math.PI * 2
    const peg = new THREE.Mesh(new THREE.CylinderGeometry(0.045, 0.055, 0.2, 6), gold)
    peg.position.set(Math.cos(a) * 1.64, 0.14, Math.sin(a) * 1.64)
    baguaRing.add(peg)
  }
  root.add(baguaRing)

  // 炉颈 / 炉口 —— 必须是空心管，否则开盖仍像实心顶
  const neck = new THREE.Mesh(
    new THREE.CylinderGeometry(0.88, 1.15, 0.55, 28, 1, true),
    bronze.clone(),
  )
  neck.material.side = THREE.DoubleSide
  neck.position.y = 2.12
  root.add(neck)
  // 外沿金口（环，不封顶）
  const mouth = new THREE.Mesh(new THREE.TorusGeometry(0.9, 0.1, 10, 36), gold)
  mouth.rotation.x = Math.PI / 2
  mouth.position.y = 2.42
  root.add(mouth)
  // 口沿内圈（细环，仍不封口）
  const mouthInner = new THREE.Mesh(new THREE.TorusGeometry(0.72, 0.04, 8, 28), goldBright)
  mouthInner.rotation.x = Math.PI / 2
  mouthInner.position.y = 2.4
  root.add(mouthInner)
  // 内壁（更深的空心管，看见炉膛）
  const neckLiner = new THREE.Mesh(
    new THREE.CylinderGeometry(0.7, 0.85, 0.7, 24, 1, true),
    new THREE.MeshStandardMaterial({
      color: 0x1a0800,
      metalness: 0.25,
      roughness: 0.8,
      emissive: 0xff3a10,
      emissiveIntensity: 0.45,
      side: THREE.DoubleSide,
    }),
  )
  neckLiner.position.y = 2.05
  root.add(neckLiner)

  // ——— 九龙头环炉口（外翻仰首） ———
  const dragonHeads = new THREE.Group()
  dragonHeads.name = 'nineDragons'
  for (let i = 0; i < 9; i++) {
    const a = (i / 9) * Math.PI * 2
    const head = makeDragonHead({ bronze, bronzeDark, bronzeLite, gold, goldBright, scaleMat, eyeMat, tongueMat })
    // 颈接炉口，吻部外翻上扬
    const r = 1.28
    head.position.set(Math.sin(a) * r, 2.22, Math.cos(a) * r)
    head.rotation.order = 'YXZ'
    head.rotation.y = a
    head.rotation.x = -0.55
    head.rotation.z = 0
    head.scale.setScalar(0.88)
    dragonHeads.add(head)
  }
  root.add(dragonHeads)

  // 穹盖：铰链在炉口后沿，掀开后整盖翻到后方，炉口完全露出
  // lidPivot = 铰链点；lidGroup = 盖本体（相对铰链偏移）
  const LID_Y = 2.48
  const LID_HINGE_Z = -0.82
  const lidPivot = new THREE.Group()
  lidPivot.name = 'furnaceLidPivot'
  lidPivot.position.set(0, LID_Y, LID_HINGE_Z)
  root.add(lidPivot)

  const lidGroup = new THREE.Group()
  lidGroup.name = 'furnaceLid'
  // 盖心相对铰链前移，合上时盖住炉口
  lidGroup.position.set(0, 0.02, 0.82)
  lidPivot.add(lidGroup)

  const lid = new THREE.Mesh(
    new THREE.SphereGeometry(0.95, 28, 18, 0, Math.PI * 2, 0, Math.PI * 0.5),
    bronze.clone(),
  )
  lid.material.map = bodyTex
  lid.material.color = new THREE.Color(0xffffff)
  lidGroup.add(lid)

  // 盖棱（九瓣）
  for (let i = 0; i < 9; i++) {
    const a = (i / 9) * Math.PI * 2
    const rib = new THREE.Mesh(new THREE.BoxGeometry(0.07, 0.09, 0.78), gold)
    rib.position.set(Math.cos(a) * 0.42, 0.28, Math.sin(a) * 0.42)
    rib.rotation.y = -a
    rib.rotation.x = 0.55
    lidGroup.add(rib)
  }

  // 盖顶宝珠（不再挂小龙头，开盖时轮廓更干净）
  const finial = new THREE.Mesh(new THREE.CylinderGeometry(0.12, 0.18, 0.2, 10), gold)
  finial.position.y = 0.72
  lidGroup.add(finial)

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
  crystal.position.y = 0.95
  lidGroup.add(crystal)

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
  topDisc.position.y = 0.55
  lidGroup.add(topDisc)

  // 炉膛底（在开口下方，从上往下能看见）
  const cavityFloor = new THREE.Mesh(
    new THREE.CircleGeometry(0.68, 28),
    new THREE.MeshStandardMaterial({
      color: 0x2a0c04,
      metalness: 0.25,
      roughness: 0.75,
      emissive: 0xff5018,
      emissiveIntensity: 0.7,
      side: THREE.DoubleSide,
    }),
  )
  cavityFloor.rotation.x = -Math.PI / 2
  cavityFloor.position.y = 1.72
  root.add(cavityFloor)
  // 膛内余火光
  const cavityGlow = new THREE.Mesh(
    new THREE.CircleGeometry(0.45, 20),
    new THREE.MeshBasicMaterial({
      color: 0xff8020,
      transparent: true,
      opacity: 0.55,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
      side: THREE.DoubleSide,
    }),
  )
  cavityGlow.rotation.x = -Math.PI / 2
  cavityGlow.position.y = 1.74
  root.add(cavityGlow)

  const coreLight = new THREE.PointLight(0xff6020, 4.0, 38, 2)
  coreLight.position.set(0, 0.65, 0)
  root.add(coreLight)
  const mouthLight = new THREE.PointLight(0xffa040, 2.4, 20, 2)
  mouthLight.position.set(0, 2.55, 0)
  root.add(mouthLight)

  // 炉火
  const flame = new THREE.Group()
  flame.name = 'furnaceFlame'

  function addFlameCone(parent, { color, opacity, r, h, y, x = 0, z = 0, rotZ = 0 }) {
    const cone = new THREE.Mesh(
      new THREE.ConeGeometry(r, h, 12, 1, true),
      new THREE.MeshBasicMaterial({
        color,
        transparent: true,
        opacity,
        side: THREE.DoubleSide,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
        fog: false,
      }),
    )
    cone.position.set(x, y, z)
    cone.rotation.z = rotZ
    cone.renderOrder = 20
    cone.userData.baseOpacity = opacity
    parent.add(cone)
    return cone
  }

  const underFire = new THREE.Group()
  underFire.position.y = 0.1
  for (const L of [
    { color: 0xff3a10, opacity: 0.75, r: 0.6, h: 0.9, y: 0.45 },
    { color: 0xff8020, opacity: 0.7, r: 0.45, h: 1.1, y: 0.55 },
    { color: 0xffd050, opacity: 0.65, r: 0.3, h: 1.25, y: 0.65 },
    { color: 0xfff0a0, opacity: 0.55, r: 0.16, h: 1.0, y: 0.75 },
  ]) {
    addFlameCone(underFire, L)
  }
  const ember = new THREE.Mesh(
    new THREE.CylinderGeometry(0.75, 0.9, 0.1, 16),
    new THREE.MeshBasicMaterial({
      color: 0xff5018,
      transparent: true,
      opacity: 0.55,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
      fog: false,
    }),
  )
  ember.position.y = 0.06
  ember.userData.baseOpacity = 0.55
  underFire.add(ember)
  const sparkGeo = new THREE.BufferGeometry()
  const sparkN = 32
  const sparkPos = new Float32Array(sparkN * 3)
  for (let i = 0; i < sparkN; i++) {
    const a = Math.random() * Math.PI * 2
    const r = Math.random() * 0.5
    sparkPos[i * 3] = Math.cos(a) * r
    sparkPos[i * 3 + 1] = 0.2 + Math.random() * 0.95
    sparkPos[i * 3 + 2] = Math.sin(a) * r
  }
  sparkGeo.setAttribute('position', new THREE.BufferAttribute(sparkPos, 3))
  const sparks = new THREE.Points(
    sparkGeo,
    new THREE.PointsMaterial({
      color: 0xffd080,
      size: 0.07,
      transparent: true,
      opacity: 0.9,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
      sizeAttenuation: true,
      fog: false,
    }),
  )
  underFire.add(sparks)
  flame.add(underFire)

  // 炉口火：从膛内向上冒，不封口
  const mouthFire = new THREE.Group()
  mouthFire.position.y = 1.85
  for (const L of [
    { color: 0xff6018, opacity: 0.5, r: 0.35, h: 0.7, y: 0.4 },
    { color: 0xffc040, opacity: 0.55, r: 0.22, h: 0.9, y: 0.5 },
    { color: 0xfff0b0, opacity: 0.45, r: 0.12, h: 0.7, y: 0.6 },
  ]) {
    addFlameCone(mouthFire, L)
  }
  flame.add(mouthFire)
  root.add(flame)

  // 周遭八卦符文
  const ritualRunes = new THREE.Group()
  ritualRunes.name = 'ritualRunes'
  ritualRunes.visible = false
  ritualRunes.position.y = 0.05
  const runeChars = ['乾', '坤', '震', '巽', '坎', '离', '艮', '兑']
  const runeMeshes = []
  for (let i = 0; i < 8; i++) {
    const a = (i / 8) * Math.PI * 2
    const rune = makeRuneSprite(runeChars[i])
    rune.position.set(Math.cos(a) * 3.6, 0.15, Math.sin(a) * 3.6)
    rune.scale.set(0.001, 0.001, 0.001)
    rune.userData.baseAngle = a
    rune.userData.radius = 3.6
    ritualRunes.add(rune)
    runeMeshes.push(rune)
  }
  const ringMats = []
  for (const r of [3.2, 4.05, 4.85]) {
    const ringMesh = new THREE.Mesh(
      new THREE.TorusGeometry(r, 0.03, 8, 64),
      new THREE.MeshBasicMaterial({
        color: 0xe0b84a,
        transparent: true,
        opacity: 0,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
      }),
    )
    ringMesh.rotation.x = Math.PI / 2
    ringMesh.position.y = 0.04
    ritualRunes.add(ringMesh)
    ringMats.push(ringMesh)
  }
  const rayLines = []
  for (let i = 0; i < 8; i++) {
    const a = (i / 8) * Math.PI * 2
    const geo = new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(0, 0.06, 0),
      new THREE.Vector3(Math.cos(a) * 4.6, 0.06, Math.sin(a) * 4.6),
    ])
    const line = new THREE.Line(
      geo,
      new THREE.LineBasicMaterial({
        color: 0xffd070,
        transparent: true,
        opacity: 0,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
      }),
    )
    ritualRunes.add(line)
    rayLines.push(line)
  }
  root.add(ritualRunes)

  root.userData.underFire = underFire
  root.userData.mouthFire = mouthFire
  root.userData.sparks = sparks
  root.userData.mouthLight = mouthLight
  root.userData.dragonHeads = dragonHeads

  const floorBagua = new THREE.Mesh(
    new THREE.CircleGeometry(2.9, 48),
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

  root.userData.lidOpen = 0
  root.userData.lidGroup = lidGroup
  root.userData.lidPivot = lidPivot
  root.userData.lidClosedY = LID_Y
  root.userData.crystal = crystal
  root.userData.coreLight = coreLight
  root.userData.flame = flame
  root.userData.baguaRing = baguaRing
  root.userData.floorBagua = floorBagua
  root.userData.ritualRunes = ritualRunes
  root.userData.runeMeshes = runeMeshes
  root.userData.ritualRings = ringMats
  root.userData.ritualRays = rayLines
  root.userData.ritualBoost = 0

  /** 盖开合 k∈[0,1]：绕后沿铰链向后掀开约 125°，炉口完全敞开 */
  root.userData.setLidOpen = (k) => {
    const t = Math.max(0, Math.min(1, k))
    root.userData.lidOpen = t
    // ease 出一点加速感
    const e = t * t * (3 - 2 * t)
    lidPivot.rotation.x = -e * 2.2 // ~126°
    lidPivot.position.y = LID_Y + e * 0.15
  }
  root.userData.setLidOpen(0)

  return root
}

/**
 * 中式龙头（局部 +X 为吻部朝向）
 * 圆润颅骨 + 细长吻 + 鹿角 + 鬃须 + 鳞颈
 */
function makeDragonHead({ bronze, bronzeDark, bronzeLite, gold, goldBright, scaleMat, eyeMat, tongueMat, crown = false }) {
  const g = new THREE.Group()

  // 颅骨（圆润）
  const skull = new THREE.Mesh(new THREE.SphereGeometry(0.2, 14, 12), bronzeLite)
  skull.position.set(0.02, 0.08, 0)
  skull.scale.set(1.05, 0.95, 1.0)
  g.add(skull)

  // 后脑隆起
  const occiput = new THREE.Mesh(new THREE.SphereGeometry(0.14, 10, 10), bronze)
  occiput.position.set(-0.12, 0.1, 0)
  occiput.scale.set(0.9, 0.85, 1.05)
  g.add(occiput)

  // 上吻（拉长椭圆）
  const snoutTop = new THREE.Mesh(new THREE.SphereGeometry(0.13, 12, 10), bronze)
  snoutTop.position.set(0.28, 0.06, 0)
  snoutTop.scale.set(1.55, 0.72, 0.78)
  g.add(snoutTop)

  // 鼻端
  const nose = new THREE.Mesh(new THREE.SphereGeometry(0.07, 10, 8), bronzeDark)
  nose.position.set(0.48, 0.05, 0)
  nose.scale.set(1.1, 0.75, 0.85)
  g.add(nose)
  // 鼻翼
  for (const z of [-0.05, 0.05]) {
    const n = new THREE.Mesh(new THREE.SphereGeometry(0.035, 8, 8), bronzeDark)
    n.position.set(0.5, 0.04, z)
    n.scale.set(1.2, 0.8, 0.9)
    g.add(n)
  }

  // 下颌
  const jaw = new THREE.Mesh(new THREE.SphereGeometry(0.11, 12, 10), bronzeDark)
  jaw.position.set(0.26, -0.06, 0)
  jaw.scale.set(1.45, 0.55, 0.7)
  g.add(jaw)
  const chin = new THREE.Mesh(new THREE.SphereGeometry(0.055, 8, 8), bronzeDark)
  chin.position.set(0.42, -0.08, 0)
  chin.scale.set(1.1, 0.6, 0.75)
  g.add(chin)

  // 口缝（金线）
  const lip = new THREE.Mesh(new THREE.TorusGeometry(0.12, 0.012, 6, 16, Math.PI * 1.1), gold)
  lip.position.set(0.38, 0.0, 0)
  lip.rotation.y = Math.PI / 2
  lip.rotation.z = 0.15
  lip.scale.set(0.55, 1.0, 1.2)
  g.add(lip)

  // 鹿角（分段弯曲）
  for (const side of [-1, 1]) {
    const hornRoot = new THREE.Mesh(new THREE.CylinderGeometry(0.028, 0.04, 0.12, 7), gold)
    hornRoot.position.set(-0.02, 0.22, side * 0.07)
    hornRoot.rotation.z = 0.25
    hornRoot.rotation.x = side * 0.15
    g.add(hornRoot)
    const hornMid = new THREE.Mesh(new THREE.CylinderGeometry(0.018, 0.028, 0.16, 7), gold)
    hornMid.position.set(0.02, 0.36, side * 0.1)
    hornMid.rotation.z = 0.55
    hornMid.rotation.x = side * 0.25
    g.add(hornMid)
    const hornTip = new THREE.Mesh(new THREE.ConeGeometry(0.016, 0.14, 6), goldBright)
    hornTip.position.set(0.08, 0.48, side * 0.13)
    hornTip.rotation.z = 0.85
    hornTip.rotation.x = side * 0.2
    g.add(hornTip)
    // 小分叉
    const branch = new THREE.Mesh(new THREE.ConeGeometry(0.012, 0.1, 5), gold)
    branch.position.set(-0.02, 0.34, side * 0.14)
    branch.rotation.z = 0.2
    branch.rotation.x = side * 0.9
    g.add(branch)
  }

  // 额鬃 / 背鬃（几缕）
  for (let i = 0; i < 5; i++) {
    const a = (i - 2) * 0.22
    const mane = new THREE.Mesh(new THREE.ConeGeometry(0.04, 0.2 + (i % 2) * 0.06, 6), goldBright)
    mane.position.set(-0.08 - Math.abs(a) * 0.05, 0.18 + (i % 2) * 0.04, a * 0.12)
    mane.rotation.z = 1.9
    mane.rotation.y = a * 0.4
    mane.scale.set(0.7, 1, 0.5)
    g.add(mane)
  }

  // 腮须（长而弯）
  for (const side of [-1, 1]) {
    for (let k = 0; k < 3; k++) {
      const w = new THREE.Mesh(
        new THREE.CylinderGeometry(0.012 - k * 0.002, 0.006, 0.28 + k * 0.06, 5),
        k === 0 ? goldBright : gold,
      )
      w.position.set(0.32 + k * 0.04, -0.02 - k * 0.02, side * (0.1 + k * 0.03))
      w.rotation.z = -1.1 - k * 0.15
      w.rotation.y = side * (0.55 + k * 0.2)
      g.add(w)
    }
  }

  // 眼眶 + 金瞳
  for (const side of [-1, 1]) {
    const socket = new THREE.Mesh(new THREE.SphereGeometry(0.045, 8, 8), bronzeDark)
    socket.position.set(0.16, 0.12, side * 0.11)
    socket.scale.set(1.1, 0.85, 0.7)
    g.add(socket)
    const eye = new THREE.Mesh(new THREE.SphereGeometry(0.032, 10, 10), eyeMat)
    eye.position.set(0.19, 0.125, side * 0.115)
    g.add(eye)
    const lid = new THREE.Mesh(new THREE.TorusGeometry(0.038, 0.008, 6, 12), gold)
    lid.position.set(0.18, 0.13, side * 0.11)
    lid.rotation.y = Math.PI / 2
    lid.scale.set(0.7, 1, 1)
    g.add(lid)
  }

  // 眉骨
  for (const side of [-1, 1]) {
    const brow = new THREE.Mesh(new THREE.SphereGeometry(0.04, 8, 6), bronze)
    brow.position.set(0.12, 0.18, side * 0.1)
    brow.scale.set(1.3, 0.4, 0.6)
    brow.rotation.z = -0.2
    g.add(brow)
  }

  // 口内火光（小）
  const glow = new THREE.Mesh(
    new THREE.SphereGeometry(0.05, 8, 8),
    new THREE.MeshBasicMaterial({
      color: 0xff6020,
      transparent: true,
      opacity: 0.55,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    }),
  )
  glow.position.set(0.46, -0.01, 0)
  g.add(glow)
  const tongue = new THREE.Mesh(new THREE.ConeGeometry(0.028, 0.12, 5), tongueMat)
  tongue.rotation.z = -Math.PI / 2
  tongue.position.set(0.52, -0.02, 0)
  g.add(tongue)

  // 鳞颈（多节渐粗）
  for (let i = 0; i < 3; i++) {
    const seg = new THREE.Mesh(
      new THREE.SphereGeometry(0.12 + i * 0.025, 10, 8),
      i % 2 ? scaleMat : bronzeDark,
    )
    seg.position.set(-0.12 - i * 0.12, 0.02 - i * 0.01, 0)
    seg.scale.set(1.1, 0.75, 0.9)
    g.add(seg)
  }

  // 颈侧金环
  const collar = new THREE.Mesh(new THREE.TorusGeometry(0.13, 0.018, 6, 16), gold)
  collar.position.set(-0.18, 0.02, 0)
  collar.rotation.y = Math.PI / 2
  g.add(collar)

  // 额珠
  if (crown) {
    const pearl = new THREE.Mesh(new THREE.SphereGeometry(0.07, 12, 12), goldBright)
    pearl.position.set(0.06, 0.26, 0)
    g.add(pearl)
    const halo = new THREE.Mesh(
      new THREE.TorusGeometry(0.09, 0.012, 6, 16),
      gold,
    )
    halo.position.set(0.06, 0.26, 0)
    halo.rotation.x = Math.PI / 2
    g.add(halo)
  } else {
    const gem = new THREE.Mesh(new THREE.SphereGeometry(0.035, 8, 8), goldBright)
    gem.position.set(0.08, 0.2, 0)
    g.add(gem)
  }

  // 颊侧小鳞
  for (const side of [-1, 1]) {
    for (let i = 0; i < 3; i++) {
      const sc = new THREE.Mesh(new THREE.SphereGeometry(0.03, 6, 6), scaleMat)
      sc.position.set(0.1 + i * 0.08, 0.0, side * (0.14 - i * 0.01))
      sc.scale.set(1.1, 0.5, 0.8)
      g.add(sc)
    }
  }

  return g
}

/** 八卦符文精灵 */
function makeRuneSprite(char) {
  const canvas = document.createElement('canvas')
  canvas.width = 128
  canvas.height = 128
  const ctx = canvas.getContext('2d')
  ctx.clearRect(0, 0, 128, 128)
  // 金环底
  ctx.beginPath()
  ctx.arc(64, 64, 54, 0, Math.PI * 2)
  ctx.fillStyle = 'rgba(40, 24, 8, 0.75)'
  ctx.fill()
  ctx.lineWidth = 4
  ctx.strokeStyle = '#e0b84a'
  ctx.stroke()
  ctx.font = 'bold 64px "Noto Serif SC", "Songti SC", "SimSun", serif'
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.shadowColor = 'rgba(255, 180, 40, 0.8)'
  ctx.shadowBlur = 12
  ctx.fillStyle = '#ffe8a0'
  ctx.fillText(char, 64, 68)
  const tex = new THREE.CanvasTexture(canvas)
  tex.colorSpace = THREE.SRGBColorSpace
  const mat = new THREE.SpriteMaterial({
    map: tex,
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    opacity: 0,
  })
  const sp = new THREE.Sprite(mat)
  sp.scale.set(0.9, 0.9, 1)
  return sp
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
