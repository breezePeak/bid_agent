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

  // 地面：暗玉石平台（浮于虚空）
  const ground = new THREE.Mesh(
    new THREE.CircleGeometry(55, 48),
    new THREE.MeshStandardMaterial({
      color: 0x12182a,
      roughness: 0.85,
      metalness: 0.15,
      transparent: true,
      opacity: 0.92,
    }),
  )
  ground.rotation.x = -Math.PI / 2
  ground.position.y = -0.12
  scene.add(ground)

  // 虚空光环
  const ring = new THREE.Mesh(
    new THREE.TorusGeometry(30, 0.04, 8, 72),
    new THREE.MeshBasicMaterial({ color: 0x6080ff, transparent: true, opacity: 0.18 }),
  )
  ring.rotation.x = Math.PI / 2
  ring.position.set(0, 0.02, 8)
  scene.add(ring)

  const ring2 = new THREE.Mesh(
    new THREE.TorusGeometry(42, 0.03, 8, 72),
    new THREE.MeshBasicMaterial({ color: 0xa070ff, transparent: true, opacity: 0.1 }),
  )
  ring2.rotation.x = Math.PI / 2
  ring2.position.set(0, 0.02, 8)
  scene.add(ring2)

  // 唐风大殿 + 长廊
  const pavilion = createPavilion(scene)
  const HALL_SCALE = 1.2
  pavilion.root.scale.setScalar(HALL_SCALE)
  pavilion.root.position.set(0, 0, -6)

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
    },
  }
}
