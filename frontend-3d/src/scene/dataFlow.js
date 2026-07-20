import * as THREE from 'three'

/**
 * Particle streams along the stage curve — denser near active progress.
 */
export function createDataFlow(scene, curve) {
  const group = new THREE.Group()
  scene.add(group)

  const count = 220
  const geo = new THREE.BufferGeometry()
  const positions = new Float32Array(count * 3)
  const colors = new Float32Array(count * 3)
  const speeds = new Float32Array(count)
  const offsets = new Float32Array(count)

  const cA = new THREE.Color(0x22d3ee)
  const cB = new THREE.Color(0xa78bfa)
  const cC = new THREE.Color(0xf472b6)

  for (let i = 0; i < count; i++) {
    offsets[i] = Math.random()
    speeds[i] = 0.04 + Math.random() * 0.08
    const col = i % 3 === 0 ? cA : i % 3 === 1 ? cB : cC
    colors[i * 3] = col.r
    colors[i * 3 + 1] = col.g
    colors[i * 3 + 2] = col.b
    const p = curve.getPoint(offsets[i])
    positions[i * 3] = p.x
    positions[i * 3 + 1] = p.y + 0.15
    positions[i * 3 + 2] = p.z
  }

  geo.setAttribute('position', new THREE.BufferAttribute(positions, 3))
  geo.setAttribute('color', new THREE.BufferAttribute(colors, 3))

  const mat = new THREE.PointsMaterial({
    size: 0.12,
    vertexColors: true,
    transparent: true,
    opacity: 0.85,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
    sizeAttenuation: true,
  })
  const points = new THREE.Points(geo, mat)
  group.add(points)

  // Energy arcs from center crystal to active stage
  const arcMat = new THREE.LineBasicMaterial({
    color: 0xfbbf24,
    transparent: true,
    opacity: 0.55,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
  })
  let arcLine = null

  function setActiveTarget(targetPos) {
    if (arcLine) {
      group.remove(arcLine)
      arcLine.geometry.dispose()
      arcLine = null
    }
    if (!targetPos) return
    const start = new THREE.Vector3(0, 2.2, 0)
    const mid = start.clone().lerp(targetPos, 0.5)
    mid.y += 3.5
    const curveArc = new THREE.QuadraticBezierCurve3(start, mid, targetPos.clone().add(new THREE.Vector3(0, 0.6, 0)))
    const pts = curveArc.getPoints(40)
    const g = new THREE.BufferGeometry().setFromPoints(pts)
    arcLine = new THREE.Line(g, arcMat)
    group.add(arcLine)
  }

  let progressLimit = 1

  return {
    group,
    setProgress(p) {
      progressLimit = Math.max(0.05, Math.min(1, p + 0.05))
    },
    setActiveTarget,
    update(t) {
      const arr = geo.attributes.position.array
      for (let i = 0; i < count; i++) {
        offsets[i] = (offsets[i] + speeds[i] * 0.016) % progressLimit
        // wrap within completed portion of pipeline
        const u = offsets[i]
        const p = curve.getPoint(u)
        const wobble = Math.sin(t * 3 + i) * 0.08
        arr[i * 3] = p.x + wobble
        arr[i * 3 + 1] = p.y + 0.2 + Math.cos(t * 2 + i * 0.3) * 0.06
        arr[i * 3 + 2] = p.z + wobble * 0.5
      }
      geo.attributes.position.needsUpdate = true
      if (arcLine) {
        arcMat.opacity = 0.35 + Math.sin(t * 4) * 0.25
      }
    },
  }
}
