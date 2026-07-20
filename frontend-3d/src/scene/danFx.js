import * as THREE from 'three'

/**
 * 结丹特效
 * 1) 丹炉：水平黄色灵力波（细环向外扩，不是实心坨）
 * 2) 金丹飞向工序盘
 * 3) 终局旋合 → 黄金标书
 */
export function createDanFx(scene, furnaceWorldPos) {
  const root = new THREE.Group()
  root.name = 'danFx'
  scene.add(root)

  const flights = []
  const bursts = []
  /** 灵力波：半径变、带宽固定的水平细环 */
  const waves = []
  const origin = furnaceWorldPos?.clone?.() || new THREE.Vector3(0, 3.5, -6)
  let roofCenter = new THREE.Vector3(0, 6, 14)

  const WAVE_MAX = 95
  const WAVE_Y = 1.35

  const orbGeo = new THREE.SphereGeometry(0.32, 14, 14)
  const sparkGeo = new THREE.SphereGeometry(0.07, 5, 5)

  let goldenBook = null
  let bookSpin = 0
  let finale = null

  // 柔边金环贴图：中间透明，边缘亮金再淡出（涟漪感，不是硬边色块）
  function makeSoftRingTex() {
    const size = 256
    const c = document.createElement('canvas')
    c.width = size
    c.height = size
    const ctx = c.getContext('2d')
    const mid = size / 2
    // 更窄亮边：只在最外缘成细环
    const g = ctx.createRadialGradient(mid, mid, mid * 0.9, mid, mid, mid * 0.998)
    g.addColorStop(0, 'rgba(255, 200, 60, 0)')
    g.addColorStop(0.2, 'rgba(255, 210, 90, 0)')
    g.addColorStop(0.45, 'rgba(255, 220, 120, 0.12)')
    g.addColorStop(0.7, 'rgba(255, 236, 160, 0.55)')
    g.addColorStop(0.88, 'rgba(255, 210, 90, 0.35)')
    g.addColorStop(1, 'rgba(255, 150, 20, 0)')
    ctx.fillStyle = g
    ctx.beginPath()
    ctx.arc(mid, mid, mid, 0, Math.PI * 2)
    ctx.fill()
    const tex = new THREE.CanvasTexture(c)
    tex.colorSpace = THREE.SRGBColorSpace
    tex.needsUpdate = true
    return tex
  }
  const softRingTex = makeSoftRingTex()

  function clearSpiritWaves() {
    for (const w of waves) {
      root.remove(w.mesh)
      w.mesh.geometry?.dispose?.()
      // 贴图共享，不 dispose map
      w.mesh.material?.dispose?.()
    }
    waves.length = 0
  }

  /**
   * 水平灵力波：柔边金环 + 轻微起伏，错相三层
   * 用 scale 扩半径；贴图保证永远是「光圈」不是硬色坨
   */
  function spiritWavesFromFurnace() {
    const cx = origin.x
    const cz = origin.z

    const layers = [
      { delay: 0, dur: 2.8, rMax: WAVE_MAX, opacity: 0.55, y: WAVE_Y },
      { delay: 0.32, dur: 2.7, rMax: WAVE_MAX * 0.94, opacity: 0.42, y: WAVE_Y + 0.08 },
      { delay: 0.62, dur: 2.6, rMax: WAVE_MAX * 0.88, opacity: 0.32, y: WAVE_Y + 0.16 },
    ]

    for (let i = 0; i < layers.length; i++) {
      const L = layers[i]
      const mat = new THREE.MeshBasicMaterial({
        map: softRingTex,
        color: 0xffe080,
        transparent: true,
        opacity: 0,
        depthWrite: false,
        depthTest: false,
        side: THREE.DoubleSide,
        fog: false,
        blending: THREE.AdditiveBlending,
      })
      // 单位圆盘，靠 scale 变大；贴图在边缘成环
      const mesh = new THREE.Mesh(new THREE.CircleGeometry(1, 96), mat)
      mesh.rotation.x = -Math.PI / 2
      mesh.position.set(cx, L.y, cz)
      mesh.scale.setScalar(2.5)
      mesh.renderOrder = 500 + i
      mesh.frustumCulled = false
      mesh.visible = false
      root.add(mesh)
      waves.push({
        mesh,
        t: -L.delay,
        dur: L.dur,
        r0: 2.5,
        r1: L.rMax,
        baseOpacity: L.opacity,
        y: L.y,
        cx,
        cz,
        soft: true,
      })
    }

  }

  function createGoldenBook() {
    const g = new THREE.Group()
    g.name = 'goldenBook'
    g.visible = false

    const cover = new THREE.Mesh(
      new THREE.BoxGeometry(2.6, 3.4, 0.2),
      new THREE.MeshStandardMaterial({
        color: 0xd4a020,
        metalness: 0.78,
        roughness: 0.22,
        emissive: 0x8a5010,
        emissiveIntensity: 0.5,
      }),
    )
    g.add(cover)

    const page = new THREE.Mesh(
      new THREE.BoxGeometry(2.3, 3.05, 0.1),
      new THREE.MeshStandardMaterial({
        color: 0xfff6e0,
        metalness: 0.12,
        roughness: 0.5,
        emissive: 0x403010,
        emissiveIntensity: 0.15,
      }),
    )
    page.position.z = 0.1
    g.add(page)

    const frame = new THREE.Mesh(
      new THREE.BoxGeometry(2.75, 3.55, 0.08),
      new THREE.MeshStandardMaterial({
        color: 0xffe080,
        metalness: 0.82,
        roughness: 0.18,
        emissive: 0xaa7010,
        emissiveIntensity: 0.55,
      }),
    )
    frame.position.z = -0.02
    g.add(frame)

    const gem = new THREE.Mesh(
      new THREE.OctahedronGeometry(0.38, 0),
      new THREE.MeshStandardMaterial({
        color: 0xfff0a0,
        metalness: 0.65,
        roughness: 0.18,
        emissive: 0xffc040,
        emissiveIntensity: 0.8,
      }),
    )
    gem.position.set(0, 0.6, 0.2)
    g.add(gem)

    const spine = new THREE.Mesh(
      new THREE.BoxGeometry(0.24, 3.45, 0.38),
      new THREE.MeshStandardMaterial({
        color: 0xb88018,
        metalness: 0.72,
        roughness: 0.28,
        emissive: 0x6a4010,
        emissiveIntensity: 0.4,
      }),
    )
    spine.position.x = -1.25
    g.add(spine)

    const glow = new THREE.PointLight(0xffd060, 4.5, 22, 2)
    glow.position.set(0, 0, 1.8)
    g.add(glow)

    for (let i = 0; i < 14; i++) {
      const a = (i / 14) * Math.PI * 2
      const p = new THREE.Mesh(
        sparkGeo,
        new THREE.MeshBasicMaterial({ color: 0xffe8a0, transparent: true, opacity: 0.9 }),
      )
      p.position.set(Math.cos(a) * 2.5, Math.sin(a * 2) * 0.45, Math.sin(a) * 2.5)
      g.add(p)
      p.userData.orbitA = a
    }

    g.scale.setScalar(0.01)
    root.add(g)
    return g
  }

  goldenBook = createGoldenBook()

  function launch({ target, flyFrom }) {
    if (!target) return

    clearSpiritWaves()
    spiritWavesFromFurnace()

    const start =
      flyFrom && flyFrom.clone ? flyFrom.clone() : origin.clone().add(new THREE.Vector3(0, 0.8, 0))

    const orb = new THREE.Mesh(
      orbGeo,
      new THREE.MeshBasicMaterial({
        color: 0xffd700,
        transparent: true,
        opacity: 1,
        fog: false,
        depthWrite: false,
      }),
    )
    orb.position.copy(start)
    root.add(orb)
    const glow = new THREE.PointLight(0xffd700, 6, 24, 2)
    orb.add(glow)

    const trail = []
    for (let i = 0; i < 10; i++) {
      const s = new THREE.Mesh(
        sparkGeo,
        new THREE.MeshBasicMaterial({
          color: 0xffd700,
          transparent: true,
          opacity: 0.65,
          fog: false,
        }),
      )
      s.position.copy(start)
      root.add(s)
      trail.push(s)
    }

    flights.push({
      orb,
      glow,
      trail,
      start,
      mid: start.clone().add(new THREE.Vector3(0, 7, 0)),
      end: target.clone().add(new THREE.Vector3(0, 0.45, 0)),
      t: 0,
      dur: 1.45,
      done: false,
    })
  }

  function spawnBurst(pos) {
    const parts = []
    for (let i = 0; i < 16; i++) {
      const a = (i / 16) * Math.PI * 2
      const elev = (Math.random() - 0.2) * 0.7
      const dir = new THREE.Vector3(
        Math.cos(a) * Math.cos(elev),
        Math.sin(elev) + 0.5,
        Math.sin(a) * Math.cos(elev),
      ).normalize()
      const m = new THREE.Mesh(
        sparkGeo,
        new THREE.MeshBasicMaterial({
          color: i % 2 ? 0xffd700 : 0xffb300,
          transparent: true,
          opacity: 0.95,
          fog: false,
        }),
      )
      m.position.copy(pos)
      root.add(m)
      parts.push({ mesh: m, dir, speed: 2.4 + Math.random() * 2.5 })
    }
    const flash = new THREE.PointLight(0xffd700, 5, 18, 2)
    flash.position.copy(pos)
    root.add(flash)
    bursts.push({ parts, flash, t: 0, dur: 0.7 })
  }

  function showGoldenBook(center) {
    if (!goldenBook) return
    const c = center || roofCenter
    goldenBook.position.set(c.x, c.y + 0.5, c.z)
    goldenBook.rotation.set(0, 0, 0)
    goldenBook.visible = true
    goldenBook.scale.setScalar(0.05)
    bookSpin = 0
  }

  function hideGoldenBook() {
    if (!goldenBook) return
    goldenBook.visible = false
    goldenBook.scale.setScalar(0.01)
  }

  function startFinaleAscend() {
    if (finale) return
    const light = new THREE.PointLight(0xffd700, 16, 80, 2)
    light.position.copy(roofCenter)
    root.add(light)
    clearSpiritWaves()
    spiritWavesFromFurnace()
    finale = { light, t: 0, bookShown: false }
  }

  return {
    root,
    setOrigin(pos) {
      if (pos) origin.copy(pos)
    },
    setRoofCenter(pos) {
      if (pos) roofCenter.copy(pos)
    },
    launch,
    startFinaleAscend,
    showGoldenBook,
    hideGoldenBook,
    clearSpiritWaves,
    debugPulse() {
      clearSpiritWaves()
      spiritWavesFromFurnace()
    },
    clearFinale() {
      if (finale) {
        root.remove(finale.light)
        finale = null
      }
      clearSpiritWaves()
      hideGoldenBook()
    },
    update(dt) {
      // 柔边金环水平扩张
      for (let i = waves.length - 1; i >= 0; i--) {
        const w = waves[i]
        w.t += dt
        if (w.t < 0) {
          w.mesh.visible = false
          w.mesh.material.opacity = 0
          continue
        }

        const k = Math.min(1, w.t / w.dur)
        // 先快后缓，像水波推开
        const e = 1 - Math.pow(1 - k, 1.65)
        const radius = w.r0 + (w.r1 - w.r0) * e
        w.mesh.scale.set(radius, radius, radius)
        // 轻微起伏
        w.mesh.position.set(w.cx, w.y + Math.sin(w.t * 3.2) * 0.04, w.cz)
        w.mesh.rotation.x = -Math.PI / 2

        let fade = 1
        if (k < 0.1) fade = 0.4 + (k / 0.1) * 0.6
        else if (k > 0.42) fade = Math.max(0, 1 - (k - 0.42) / 0.58)

        w.mesh.visible = fade > 0.02
        w.mesh.material.opacity = w.baseOpacity * fade

        if (k >= 1) {
          root.remove(w.mesh)
          w.mesh.geometry.dispose()
          w.mesh.material.dispose()
          waves.splice(i, 1)
        }
      }

      for (let i = flights.length - 1; i >= 0; i--) {
        const f = flights[i]
        f.t += dt
        const k = Math.min(1, f.t / f.dur)
        const e = 1 - (1 - k) ** 3
        const p0 = f.start
        const p1 = f.mid
        const p2 = f.end
        const u = 1 - e
        f.orb.position.set(
          u * u * p0.x + 2 * u * e * p1.x + e * e * p2.x,
          u * u * p0.y + 2 * u * e * p1.y + e * e * p2.y,
          u * u * p0.z + 2 * u * e * p1.z + e * e * p2.z,
        )
        f.orb.scale.setScalar(1 + Math.sin(k * Math.PI) * 0.55)
        f.glow.intensity = 3 + Math.sin(k * Math.PI) * 4
        for (let j = f.trail.length - 1; j >= 0; j--) {
          const prev = j === 0 ? f.orb.position : f.trail[j - 1].position
          f.trail[j].position.lerp(prev, 0.42)
          f.trail[j].material.opacity = (1 - k) * 0.55 * (1 - j / f.trail.length)
        }
        if (k >= 1 && !f.done) {
          f.done = true
          spawnBurst(f.end.clone())
          root.remove(f.orb)
          f.orb.material.dispose()
          for (const s of f.trail) {
            root.remove(s)
            s.material.dispose()
          }
          flights.splice(i, 1)
        }
      }

      for (let i = bursts.length - 1; i >= 0; i--) {
        const b = bursts[i]
        b.t += dt
        const k = Math.min(1, b.t / b.dur)
        for (const p of b.parts) {
          p.mesh.position.addScaledVector(p.dir, p.speed * dt * (1 - k * 0.4))
          p.mesh.material.opacity = 0.95 * (1 - k)
        }
        b.flash.intensity = 5 * (1 - k)
        if (k >= 1) {
          for (const p of b.parts) {
            root.remove(p.mesh)
            p.mesh.material.dispose()
          }
          root.remove(b.flash)
          bursts.splice(i, 1)
        }
      }

      if (finale) {
        finale.t += dt
        finale.light.intensity = 8 + Math.sin(finale.t * 3) * 2.5
        if (!finale.bookShown && finale.t > 2.4) {
          finale.bookShown = true
          showGoldenBook(roofCenter)
        }
      }

      if (goldenBook?.visible) {
        bookSpin += dt
        const grow = Math.min(1, bookSpin / 0.85)
        goldenBook.scale.setScalar(0.08 + grow * 1.25)
        goldenBook.rotation.y += dt * (0.5 + (1 - grow) * 1.8)
        goldenBook.position.y += Math.sin(bookSpin * 2) * 0.003
        goldenBook.traverse((ch) => {
          if (ch.userData.orbitA != null) {
            const a = ch.userData.orbitA + bookSpin * 1.6
            ch.position.set(Math.cos(a) * 2.6, Math.sin(a * 2 + bookSpin) * 0.45, Math.sin(a) * 2.6)
          }
        })
      }
    },
  }
}
