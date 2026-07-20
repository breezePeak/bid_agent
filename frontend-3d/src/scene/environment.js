import * as THREE from 'three'

/** Lightweight environment — no transmission materials, few lights, modest particles. */
export function createEnvironment(scene) {
  scene.background = new THREE.Color(0x03060f)
  scene.fog = new THREE.FogExp2(0x03060f, 0.016)

  scene.add(new THREE.AmbientLight(0x8b9cff, 0.55))
  const key = new THREE.DirectionalLight(0xcfe8ff, 1.0)
  key.position.set(8, 18, 10)
  scene.add(key)
  const rim = new THREE.DirectionalLight(0xff7ad9, 0.35)
  rim.position.set(-12, 6, -8)
  scene.add(rim)
  scene.add(new THREE.HemisphereLight(0x1e3a5f, 0x0a0a12, 0.45))

  // Starfield (reduced)
  const starCount = 600
  const starGeo = new THREE.BufferGeometry()
  const positions = new Float32Array(starCount * 3)
  for (let i = 0; i < starCount; i++) {
    const r = 40 + Math.random() * 80
    const theta = Math.random() * Math.PI * 2
    const phi = Math.acos(2 * Math.random() - 1)
    positions[i * 3] = r * Math.sin(phi) * Math.cos(theta)
    positions[i * 3 + 1] = r * Math.cos(phi) * 0.55 + 10
    positions[i * 3 + 2] = r * Math.sin(phi) * Math.sin(theta)
  }
  starGeo.setAttribute('position', new THREE.BufferAttribute(positions, 3))
  const stars = new THREE.Points(
    starGeo,
    new THREE.PointsMaterial({
      color: 0xa5d8ff,
      size: 0.1,
      transparent: true,
      opacity: 0.75,
      depthWrite: false,
      sizeAttenuation: true,
    }),
  )
  scene.add(stars)

  const grid = new THREE.GridHelper(60, 40, 0x1d4ed8, 0x0f172a)
  grid.position.y = -0.02
  if (Array.isArray(grid.material)) {
    grid.material.forEach((m) => {
      m.transparent = true
      m.opacity = 0.4
    })
  } else {
    grid.material.transparent = true
    grid.material.opacity = 0.4
  }
  scene.add(grid)

  const floor = new THREE.Mesh(
    new THREE.CircleGeometry(28, 48),
    new THREE.MeshBasicMaterial({
      color: 0x070b16,
      transparent: true,
      opacity: 0.88,
    }),
  )
  floor.rotation.x = -Math.PI / 2
  floor.position.y = -0.04
  scene.add(floor)

  const ring = new THREE.Mesh(
    new THREE.TorusGeometry(18, 0.04, 8, 64),
    new THREE.MeshBasicMaterial({ color: 0x22d3ee, transparent: true, opacity: 0.3 }),
  )
  ring.rotation.x = Math.PI / 2
  ring.position.y = 0.02
  scene.add(ring)

  const ring2 = new THREE.Mesh(
    new THREE.TorusGeometry(22, 0.025, 8, 64),
    new THREE.MeshBasicMaterial({ color: 0xa78bfa, transparent: true, opacity: 0.18 }),
  )
  ring2.rotation.x = Math.PI / 2
  ring2.position.y = 0.02
  scene.add(ring2)

  // Central crystal — Basic/Standard only (NO transmission — freezes many GPUs)
  const pedestal = new THREE.Group()
  pedestal.add(
    new THREE.Mesh(
      new THREE.CylinderGeometry(2.2, 2.8, 0.3, 24),
      new THREE.MeshStandardMaterial({
        color: 0x0f172a,
        metalness: 0.7,
        roughness: 0.4,
        emissive: 0x0ea5e9,
        emissiveIntensity: 0.12,
      }),
    ),
  )

  const crystal = new THREE.Mesh(
    new THREE.OctahedronGeometry(1.05, 0),
    new THREE.MeshStandardMaterial({
      color: 0x67e8f9,
      metalness: 0.35,
      roughness: 0.15,
      emissive: 0x0891b2,
      emissiveIntensity: 0.55,
      transparent: true,
      opacity: 0.92,
    }),
  )
  crystal.position.y = 1.5
  pedestal.add(crystal)

  // Single point light for whole scene accent (agents must NOT add more)
  const coreLight = new THREE.PointLight(0x22d3ee, 1.6, 24, 2)
  coreLight.position.set(0, 2.2, 0)
  pedestal.add(coreLight)

  const beam = new THREE.Mesh(
    new THREE.CylinderGeometry(0.06, 1.4, 7, 16, 1, true),
    new THREE.MeshBasicMaterial({
      color: 0x22d3ee,
      transparent: true,
      opacity: 0.07,
      side: THREE.DoubleSide,
      depthWrite: false,
    }),
  )
  beam.position.y = 4.5
  pedestal.add(beam)
  scene.add(pedestal)

  // Dust (static positions — no per-frame attribute rewrite)
  const dustCount = 120
  const dustPos = new Float32Array(dustCount * 3)
  for (let i = 0; i < dustCount; i++) {
    dustPos[i * 3] = (Math.random() - 0.5) * 28
    dustPos[i * 3 + 1] = Math.random() * 7
    dustPos[i * 3 + 2] = (Math.random() - 0.5) * 28
  }
  const dustGeo = new THREE.BufferGeometry()
  dustGeo.setAttribute('position', new THREE.BufferAttribute(dustPos, 3))
  const dust = new THREE.Points(
    dustGeo,
    new THREE.PointsMaterial({
      color: 0x7dd3fc,
      size: 0.05,
      transparent: true,
      opacity: 0.3,
      depthWrite: false,
    }),
  )
  scene.add(dust)

  return {
    stars,
    pedestal,
    crystal,
    coreLight,
    dust,
    ring,
    ring2,
    update(t, progress = 0) {
      crystal.rotation.y = t * 0.3
      crystal.position.y = 1.5 + Math.sin(t * 1.1) * 0.06
      const scale = 0.9 + progress * 0.45
      crystal.scale.setScalar(scale)
      coreLight.intensity = 1.2 + progress * 1.4 + Math.sin(t * 2.5) * 0.15
      ring.rotation.z = t * 0.04
      ring2.rotation.z = -t * 0.025
      stars.rotation.y = t * 0.008
      dust.rotation.y = t * 0.015
    },
  }
}
