import * as THREE from 'three'

export function createEnvironment(scene) {
  scene.background = new THREE.Color(0x03060f)
  scene.fog = new THREE.FogExp2(0x03060f, 0.018)

  const ambient = new THREE.AmbientLight(0x6b7cff, 0.35)
  scene.add(ambient)

  const key = new THREE.DirectionalLight(0xb6e3ff, 1.15)
  key.position.set(8, 18, 10)
  key.castShadow = false
  scene.add(key)

  const rim = new THREE.DirectionalLight(0xff7ad9, 0.45)
  rim.position.set(-12, 6, -8)
  scene.add(rim)

  const hemi = new THREE.HemisphereLight(0x1e3a5f, 0x0a0a12, 0.55)
  scene.add(hemi)

  // Starfield
  const starCount = 1800
  const starGeo = new THREE.BufferGeometry()
  const positions = new Float32Array(starCount * 3)
  const sizes = new Float32Array(starCount)
  for (let i = 0; i < starCount; i++) {
    const r = 40 + Math.random() * 90
    const theta = Math.random() * Math.PI * 2
    const phi = Math.acos(2 * Math.random() - 1)
    positions[i * 3] = r * Math.sin(phi) * Math.cos(theta)
    positions[i * 3 + 1] = r * Math.cos(phi) * 0.55 + 10
    positions[i * 3 + 2] = r * Math.sin(phi) * Math.sin(theta)
    sizes[i] = Math.random()
  }
  starGeo.setAttribute('position', new THREE.BufferAttribute(positions, 3))
  starGeo.setAttribute('aSize', new THREE.BufferAttribute(sizes, 1))
  const starMat = new THREE.PointsMaterial({
    color: 0xa5d8ff,
    size: 0.08,
    transparent: true,
    opacity: 0.85,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    sizeAttenuation: true,
  })
  const stars = new THREE.Points(starGeo, starMat)
  scene.add(stars)

  // Ground grid
  const grid = new THREE.GridHelper(80, 80, 0x1d4ed8, 0x0f172a)
  grid.position.y = -0.02
  grid.material.transparent = true
  grid.material.opacity = 0.45
  scene.add(grid)

  // Reflective floor disc
  const floor = new THREE.Mesh(
    new THREE.CircleGeometry(36, 96),
    new THREE.MeshStandardMaterial({
      color: 0x070b16,
      metalness: 0.85,
      roughness: 0.35,
      transparent: true,
      opacity: 0.92,
    }),
  )
  floor.rotation.x = -Math.PI / 2
  floor.position.y = -0.04
  scene.add(floor)

  // Outer ring
  const ring = new THREE.Mesh(
    new THREE.TorusGeometry(18, 0.04, 12, 160),
    new THREE.MeshBasicMaterial({
      color: 0x22d3ee,
      transparent: true,
      opacity: 0.35,
    }),
  )
  ring.rotation.x = Math.PI / 2
  ring.position.y = 0.02
  scene.add(ring)

  const ring2 = new THREE.Mesh(
    new THREE.TorusGeometry(22, 0.025, 12, 160),
    new THREE.MeshBasicMaterial({
      color: 0xa78bfa,
      transparent: true,
      opacity: 0.22,
    }),
  )
  ring2.rotation.x = Math.PI / 2
  ring2.position.y = 0.02
  scene.add(ring2)

  // Central hologram base
  const pedestal = new THREE.Group()
  const base = new THREE.Mesh(
    new THREE.CylinderGeometry(2.4, 3.0, 0.35, 48),
    new THREE.MeshStandardMaterial({
      color: 0x0f172a,
      metalness: 0.9,
      roughness: 0.25,
      emissive: 0x0ea5e9,
      emissiveIntensity: 0.15,
    }),
  )
  pedestal.add(base)

  const crystal = new THREE.Mesh(
    new THREE.OctahedronGeometry(1.1, 0),
    new THREE.MeshPhysicalMaterial({
      color: 0x67e8f9,
      metalness: 0.1,
      roughness: 0.05,
      transmission: 0.55,
      thickness: 1.2,
      transparent: true,
      opacity: 0.85,
      emissive: 0x0891b2,
      emissiveIntensity: 0.4,
    }),
  )
  crystal.position.y = 1.5
  pedestal.add(crystal)

  const coreLight = new THREE.PointLight(0x22d3ee, 2.5, 28, 2)
  coreLight.position.set(0, 2.2, 0)
  pedestal.add(coreLight)

  const beam = new THREE.Mesh(
    new THREE.CylinderGeometry(0.08, 1.6, 8, 32, 1, true),
    new THREE.MeshBasicMaterial({
      color: 0x22d3ee,
      transparent: true,
      opacity: 0.08,
      side: THREE.DoubleSide,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    }),
  )
  beam.position.y = 5
  pedestal.add(beam)
  pedestal.position.set(0, 0, 0)
  scene.add(pedestal)

  // Floating dust particles near center
  const dustCount = 400
  const dustGeo = new THREE.BufferGeometry()
  const dustPos = new Float32Array(dustCount * 3)
  for (let i = 0; i < dustCount; i++) {
    dustPos[i * 3] = (Math.random() - 0.5) * 30
    dustPos[i * 3 + 1] = Math.random() * 8
    dustPos[i * 3 + 2] = (Math.random() - 0.5) * 30
  }
  dustGeo.setAttribute('position', new THREE.BufferAttribute(dustPos, 3))
  const dust = new THREE.Points(
    dustGeo,
    new THREE.PointsMaterial({
      color: 0x7dd3fc,
      size: 0.04,
      transparent: true,
      opacity: 0.35,
      blending: THREE.AdditiveBlending,
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
      crystal.rotation.y = t * 0.35
      crystal.rotation.x = Math.sin(t * 0.4) * 0.15
      crystal.position.y = 1.5 + Math.sin(t * 1.2) * 0.08
      const scale = 0.85 + progress * 0.55
      crystal.scale.setScalar(scale)
      coreLight.intensity = 1.8 + progress * 2.2 + Math.sin(t * 3) * 0.3
      ring.rotation.z = t * 0.05
      ring2.rotation.z = -t * 0.03
      stars.rotation.y = t * 0.01
      dust.rotation.y = t * 0.02
      const dArr = dust.geometry.attributes.position.array
      for (let i = 0; i < dustCount; i++) {
        dArr[i * 3 + 1] += Math.sin(t + i) * 0.002
        if (dArr[i * 3 + 1] > 9) dArr[i * 3 + 1] = 0
      }
      dust.geometry.attributes.position.needsUpdate = true
    },
  }
}
