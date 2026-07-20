import * as THREE from 'three'

/** Lightweight particle stream along stage curve. */
export function createDataFlow(scene, curve) {
  const group = new THREE.Group()
  scene.add(group)

  const count = 80
  const geo = new THREE.BufferGeometry()
  const positions = new Float32Array(count * 3)
  const colors = new Float32Array(count * 3)
  const speeds = new Float32Array(count)
  const offsets = new Float32Array(count)

  const palette = [new THREE.Color(0xc43c2c), new THREE.Color(0xd4a017), new THREE.Color(0xff8a40)]

  for (let i = 0; i < count; i++) {
    offsets[i] = Math.random()
    speeds[i] = 0.035 + Math.random() * 0.05
    const col = palette[i % 3]
    colors[i * 3] = col.r
    colors[i * 3 + 1] = col.g
    colors[i * 3 + 2] = col.b
    const p = curve.getPoint(offsets[i] * 0.01)
    positions[i * 3] = p.x
    positions[i * 3 + 1] = p.y + 0.15
    positions[i * 3 + 2] = p.z
  }

  geo.setAttribute('position', new THREE.BufferAttribute(positions, 3))
  geo.setAttribute('color', new THREE.BufferAttribute(colors, 3))

  const points = new THREE.Points(
    geo,
    new THREE.PointsMaterial({
      size: 0.11,
      vertexColors: true,
      transparent: true,
      opacity: 0.8,
      depthWrite: false,
      sizeAttenuation: true,
    }),
  )
  group.add(points)

  const arcMat = new THREE.LineBasicMaterial({
    color: 0xfbbf24,
    transparent: true,
    opacity: 0.5,
    depthWrite: false,
  })
  let arcLine = null
  let progressLimit = 0.08
  // Update particles every other frame
  let frame = 0

  return {
    group,
    setProgress(p) {
      progressLimit = Math.max(0.06, Math.min(1, p + 0.06))
    },
    setActiveTarget(targetPos) {
      if (arcLine) {
        group.remove(arcLine)
        arcLine.geometry.dispose()
        arcLine = null
      }
      if (!targetPos) return
      const start = new THREE.Vector3(0, 2.0, 0)
      const mid = start.clone().lerp(targetPos, 0.5)
      mid.y += 3.2
      const curveArc = new THREE.QuadraticBezierCurve3(
        start,
        mid,
        targetPos.clone().add(new THREE.Vector3(0, 0.5, 0)),
      )
      arcLine = new THREE.Line(new THREE.BufferGeometry().setFromPoints(curveArc.getPoints(24)), arcMat)
      group.add(arcLine)
    },
    update(t) {
      frame += 1
      if (frame % 2 !== 0) return
      const arr = geo.attributes.position.array
      for (let i = 0; i < count; i++) {
        offsets[i] = (offsets[i] + speeds[i] * 0.03) % progressLimit
        const p = curve.getPoint(offsets[i])
        arr[i * 3] = p.x
        arr[i * 3 + 1] = p.y + 0.18
        arr[i * 3 + 2] = p.z
      }
      geo.attributes.position.needsUpdate = true
      if (arcLine) arcMat.opacity = 0.35 + Math.sin(t * 3.5) * 0.2
    },
  }
}
