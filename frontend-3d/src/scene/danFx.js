import * as THREE from 'three'

/**
 * 结丹特效：
 * 1) 丹炉冒出巨大灵力波，扩散到走廊边缘再淡出
 * 2) 金丹飞向工序
 * 3) 终局：金丹围成圆快速旋转 → 黄金标书
 */
export function createDanFx(scene, furnaceWorldPos) {
  const root = new THREE.Group()
  root.name = 'danFx'
  scene.add(root)

  const flights = []
  const bursts = []
  const waves = []
  const hallBursts = []
  const origin = furnaceWorldPos?.clone?.() || new THREE.Vector3(0, 3.5, -6)
  let roofCenter = new THREE.Vector3(0, 6, 14)
  // 走廊边缘大约 20~28 米半径
  const WAVE_EDGE = 26

  const orbGeo = new THREE.SphereGeometry(0.22, 12, 12)
  const sparkGeo = new THREE.SphereGeometry(0.06, 5, 5)

  let goldenBook = null
  let bookSpin = 0

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

  function hallRadiance() {
    const flash = new THREE.PointLight(0xffe0a0, 8, 28, 2)
    flash.position.copy(origin)
    root.add(flash)
    const ring = new THREE.Mesh(
      new THREE.TorusGeometry(0.5, 0.08, 8, 40),
      new THREE.MeshBasicMaterial({
        color: 0xfff0c0,
        transparent: true,
        opacity: 0.9,
        depthWrite: false,
      }),
    )
    ring.rotation.x = Math.PI / 2
    ring.position.copy(origin)
    root.add(ring)
    hallBursts.push({ flash, ring, t: 0, dur: 1.1 })
  }

  /**
   * 巨大灵力波：从丹炉中心扩散到走廊边缘再淡出
   * Ring 初始半径 ~1m，scale 到 WAVE_EDGE
   */
  function spiritWaves() {
    const colors = [0xfffaf0, 0xfff0c8, 0xffe090, 0xffd060, 0xf0c050, 0xe8b040]
    for (let i = 0; i < 6; i++) {
      // 较宽的环带，远处仍清晰
      const ringGeo = new THREE.RingGeometry(0.92, 1.08, 96)
      const mat = new THREE.MeshBasicMaterial({
        color: colors[i % colors.length],
        transparent: true,
        opacity: 0.95 - i * 0.08,
        depthWrite: false,
        side: THREE.DoubleSide,
        depthTest: true,
      })
      const mesh = new THREE.Mesh(ringGeo, mat)
      mesh.rotation.x = -Math.PI / 2
      // 炉口高度，略抬离地面
      mesh.position.set(origin.x, Math.max(0.6, origin.y - 0.3), origin.z)
      mesh.scale.setScalar(0.5)
      mesh.renderOrder = 20
      root.add(mesh)
      waves.push({
        mesh,
        t: -i * 0.16,
        dur: 2.8,
        // 扩到走廊边缘（约 26 米）
        maxScale: WAVE_EDGE + i * 1.5,
        baseOpacity: 0.92 - i * 0.08,
        startY: Math.max(0.6, origin.y - 0.3),
      })
    }
    // 再补两道更高的薄波，增强「从炉中冒出」
    for (let i = 0; i < 2; i++) {
      const ringGeo = new THREE.RingGeometry(0.94, 1.04, 64)
      const mat = new THREE.MeshBasicMaterial({
        color: 0xffffff,
        transparent: true,
        opacity: 0.55,
        depthWrite: false,
        side: THREE.DoubleSide,
      })
      const mesh = new THREE.Mesh(ringGeo, mat)
      mesh.rotation.x = -Math.PI / 2
      mesh.position.set(origin.x, origin.y + 0.4 + i * 0.5, origin.z)
      mesh.scale.setScalar(0.4)
      mesh.renderOrder = 21
      root.add(mesh)
      waves.push({
        mesh,
        t: -0.05 - i * 0.12,
        dur: 2.2,
        maxScale: WAVE_EDGE * 0.85,
        baseOpacity: 0.5,
        startY: origin.y + 0.4 + i * 0.5,
        rise: 1.2,
      })
    }
  }

  function launch({ target, flyFrom }) {
    if (!target) return

    hallRadiance()
    spiritWaves()
    // 第二阵稍晚再冒，形成「阵阵」
    const t = setTimeout(() => {
      if (root.parent) spiritWaves()
    }, 320)
    // 避免泄漏：记录到 flights 无关，短时即可

    const start =
      flyFrom && flyFrom.clone ? flyFrom.clone() : origin.clone().add(new THREE.Vector3(0, 0.6, 0))

    const orb = new THREE.Mesh(
      orbGeo,
      new THREE.MeshBasicMaterial({ color: 0xffe8a0, transparent: true, opacity: 0.95 }),
    )
    orb.position.copy(start)
    root.add(orb)
    const glow = new THREE.PointLight(0xffd070, 3, 16, 2)
    orb.add(glow)

    const trail = []
    for (let i = 0; i < 10; i++) {
      const s = new THREE.Mesh(
        sparkGeo,
        new THREE.MeshBasicMaterial({ color: 0xffe0a0, transparent: true, opacity: 0.55 }),
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
      mid: start.clone().add(new THREE.Vector3(0, 5, 0)),
      end: target.clone().add(new THREE.Vector3(0, 0.4, 0)),
      t: 0,
      dur: 1.3,
      done: false,
      _timer: t,
    })
  }

  function spawnBurst(pos) {
    const parts = []
    for (let i = 0; i < 18; i++) {
      const a = (i / 18) * Math.PI * 2
      const elev = (Math.random() - 0.2) * 0.7
      const dir = new THREE.Vector3(
        Math.cos(a) * Math.cos(elev),
        Math.sin(elev) + 0.5,
        Math.sin(a) * Math.cos(elev),
      ).normalize()
      const m = new THREE.Mesh(
        sparkGeo,
        new THREE.MeshBasicMaterial({
          color: i % 2 ? 0xfff0c0 : 0xffd080,
          transparent: true,
          opacity: 0.95,
        }),
      )
      m.position.copy(pos)
      root.add(m)
      parts.push({ mesh: m, dir, speed: 2.2 + Math.random() * 2.5 })
    }
    const flash = new THREE.PointLight(0xffe8a0, 4.5, 18, 2)
    flash.position.copy(pos)
    root.add(flash)
    bursts.push({ parts, flash, t: 0, dur: 0.8 })
  }

  let finale = null

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
    const light = new THREE.PointLight(0xffe8a0, 10, 55, 2)
    light.position.copy(roofCenter)
    root.add(light)
    spiritWaves()
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
    clearFinale() {
      if (finale) {
        root.remove(finale.light)
        finale = null
      }
      hideGoldenBook()
    },
    update(dt) {
      for (let i = hallBursts.length - 1; i >= 0; i--) {
        const b = hallBursts[i]
        b.t += dt
        const k = Math.min(1, b.t / b.dur)
        b.flash.intensity = 8 * (1 - k)
        b.ring.scale.setScalar(1 + k * 4)
        b.ring.material.opacity = 0.9 * (1 - k)
        if (k >= 1) {
          root.remove(b.flash)
          root.remove(b.ring)
          b.ring.material.dispose()
          hallBursts.splice(i, 1)
        }
      }

      // 巨大涟漪：扩到走廊边缘后淡出
      for (let i = waves.length - 1; i >= 0; i--) {
        const w = waves[i]
        w.t += dt
        if (w.t < 0) continue
        const k = Math.min(1, w.t / w.dur)
        // 前 70% 快速扩张，后段缓
        const e = k < 0.7 ? (k / 0.7) ** 0.85 : 1
        const scale = 0.4 + e * w.maxScale
        w.mesh.scale.set(scale, scale, scale)
        if (w.rise) {
          w.mesh.position.y = w.startY + e * w.rise
        }
        // 靠近边缘才开始明显变淡
        let fade = 1
        if (k < 0.08) fade = k / 0.08
        else if (k > 0.55) fade = 1 - (k - 0.55) / 0.45
        w.mesh.material.opacity = Math.max(0, w.baseOpacity * fade)
        if (k >= 1) {
          root.remove(w.mesh)
          w.mesh.geometry?.dispose?.()
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
        f.orb.scale.setScalar(0.95 + Math.sin(k * Math.PI) * 0.5)
        f.glow.intensity = 2.5 + Math.sin(k * Math.PI) * 3
        for (let j = f.trail.length - 1; j >= 0; j--) {
          const prev = j === 0 ? f.orb.position : f.trail[j - 1].position
          f.trail[j].position.lerp(prev, 0.42)
          f.trail[j].material.opacity = (1 - k) * 0.5 * (1 - j / f.trail.length)
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
        b.flash.intensity = 4.5 * (1 - k)
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
