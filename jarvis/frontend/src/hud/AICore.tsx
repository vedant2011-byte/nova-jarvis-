import { useEffect, useMemo, useRef } from 'react'
import type { UIState } from '../hooks/useJarvisStore'

type Props = {
  state: UIState
  level: number
  intensity: number
}

/** Central animated AI core: concentric rings, arcs, waveform, orbitals. */
export function AICore({ state, level, intensity }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const stateRef = useRef(state)
  const levelRef = useRef(level)
  stateRef.current = state
  levelRef.current = level

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    let raf = 0
    let t = 0
    const dpr = Math.min(window.devicePixelRatio || 1, 2)

    const resize = () => {
      const rect = canvas.getBoundingClientRect()
      canvas.width = rect.width * dpr
      canvas.height = rect.height * dpr
    }
    resize()
    const ro = new ResizeObserver(resize)
    ro.observe(canvas)

    // particles orbiting the core
    const particles = Array.from({ length: 42 }, (_, i) => ({
      a: Math.random() * Math.PI * 2,
      r: 0.62 + Math.random() * 0.5,
      speed: (0.0006 + Math.random() * 0.0016) * (i % 2 ? 1 : -1),
      size: 0.6 + Math.random() * 1.4,
      alpha: 0.15 + Math.random() * 0.5,
    }))

    const wave = Array.from({ length: 64 }, () => 0)

    const draw = () => {
      const w = canvas.width
      const h = canvas.height
      const cx = w / 2
      const cy = h / 2
      const R = Math.min(w, h) * 0.42
      t += 1

      ctx.clearRect(0, 0, w, h)
      const s = stateRef.current
      const animScale = intensity

      // base rotation speeds per state
      const speed =
        s === 'thinking' ? 0.045 : s === 'executing' || s === 'transcribing' ? 0.03
        : s === 'listening' ? 0.02 : 0.006
      const rot = t * speed * animScale

      const glowColor =
        s === 'error' ? '248,113,113' : s === 'confirmation' ? '251,191,36' : '34,211,238'
      const accent = `rgba(${glowColor}`

      // --- breathing base glow ---
      const breathe = 0.5 + 0.5 * Math.sin(t * 0.02 * animScale)
      const coreR = R * (s === 'listening' ? 0.30 + levelRef.current * 0.08
        : s === 'transcribing' ? 0.29 + 0.02 * Math.sin(t * 0.25)
        : 0.28 + breathe * 0.03)

      // outer gradient halo
      const halo = ctx.createRadialGradient(cx, cy, coreR * 0.2, cx, cy, R * 1.15)
      halo.addColorStop(0, `${accent},${0.16 * animScale + (s === 'thinking' ? 0.08 : 0)})`)
      halo.addColorStop(0.55, `${accent},0.05)`)
      halo.addColorStop(1, 'rgba(0,0,0,0)')
      ctx.fillStyle = halo
      ctx.fillRect(0, 0, w, h)

      // --- waveform circle (inner) ---
      const target = s === 'listening' ? levelRef.current
        : s === 'transcribing' ? 0.3 + 0.2 * Math.sin(t * 0.3)
        : s === 'speaking' ? 0.55 : 0
      for (let i = 0; i < wave.length; i++) {
        const decay = 0.86
        const noise = Math.abs(Math.sin(t * 0.11 + i * 0.55)) * (0.25 + levelRef.current)
        const goal = target * noise * R * 0.16
        wave[i] += (goal - wave[i]) * (1 - decay)
      }
      ctx.beginPath()
      for (let i = 0; i <= wave.length; i++) {
        const ang = (i / wave.length) * Math.PI * 2
        const amp = wave[i % wave.length]
        const rr = coreR + amp
        const x = cx + Math.cos(ang) * rr
        const y = cy + Math.sin(ang) * rr
        if (i === 0) ctx.moveTo(x, y)
        else ctx.lineTo(x, y)
      }
      ctx.closePath()
      ctx.strokeStyle = `${accent},${0.75 * animScale})`
      ctx.lineWidth = 1.4 * dpr
      ctx.shadowColor = `${accent},0.8)`
      ctx.shadowBlur = 12 * dpr
      ctx.stroke()
      ctx.shadowBlur = 0

      // --- concentric rings ---
      const rings = [
        { r: 1.0, dash: [2, 14], w: 1, speedMul: -0.5, alpha: 0.35 },
        { r: 0.86, dash: [40, 80], w: 1.5, speedMul: 0.8, alpha: 0.5 },
        { r: 0.72, dash: [6, 4], w: 1, speedMul: -1.4, alpha: 0.3 },
        { r: 1.14, dash: [1, 6], w: 1, speedMul: 0.25, alpha: 0.22 },
      ]
      for (const ring of rings) {
        ctx.save()
        ctx.translate(cx, cy)
        ctx.rotate(rot * ring.speedMul * 8)
        ctx.beginPath()
        ctx.arc(0, 0, R * ring.r, 0, Math.PI * 2)
        ctx.setLineDash(ring.dash.map(d => d * dpr))
        ctx.strokeStyle = `${accent},${ring.alpha * animScale})`
        ctx.lineWidth = ring.w * dpr
        ctx.stroke()
        ctx.restore()
      }
      ctx.setLineDash([])

      // --- rotating arc segments (large, cinematic) ---
      const segCount = 3
      for (let i = 0; i < segCount; i++) {
        const start = rot * (1.3 + i * 0.35) + (i * Math.PI * 2) / segCount
        const span = Math.PI * (0.5 - i * 0.08)
        ctx.beginPath()
        ctx.arc(cx, cy, R * (0.93 + i * 0.05), start, start + span)
        ctx.strokeStyle = `${accent},${(0.5 - i * 0.12) * animScale})`
        ctx.lineWidth = 2 * dpr
        ctx.stroke()
      }

      // --- tick marks ---
      ctx.save()
      ctx.translate(cx, cy)
      ctx.rotate(-rot * 2)
      for (let i = 0; i < 60; i++) {
        const ang = (i / 60) * Math.PI * 2
        const long = i % 5 === 0
        const r1 = R * 1.18
        const r2 = r1 + (long ? 8 : 4) * dpr
        ctx.beginPath()
        ctx.moveTo(Math.cos(ang) * r1, Math.sin(ang) * r1)
        ctx.lineTo(Math.cos(ang) * r2, Math.sin(ang) * r2)
        ctx.strokeStyle = `${accent},${(long ? 0.5 : 0.22) * animScale})`
        ctx.lineWidth = dpr
        ctx.stroke()
      }
      ctx.restore()

      // --- orbiting particles ---
      for (const p of particles) {
        p.a += p.speed * (s === 'thinking' ? 3 : 1) * animScale
        const x = cx + Math.cos(p.a) * R * p.r
        const y = cy + Math.sin(p.a) * R * p.r
        ctx.beginPath()
        ctx.arc(x, y, p.size * dpr, 0, Math.PI * 2)
        ctx.fillStyle = `${accent},${p.alpha * animScale})`
        ctx.fill()
      }

      // --- center dot / nucleus ---
      const nucleus = ctx.createRadialGradient(cx, cy, 0, cx, cy, coreR * 0.5)
      nucleus.addColorStop(0, `${accent},${0.9 * animScale})`)
      nucleus.addColorStop(0.35, `${accent},${0.35 * animScale})`)
      nucleus.addColorStop(1, 'rgba(0,0,0,0)')
      ctx.beginPath()
      ctx.arc(cx, cy, coreR * 0.5, 0, Math.PI * 2)
      ctx.fillStyle = nucleus
      ctx.fill()

      // center ring
      ctx.beginPath()
      ctx.arc(cx, cy, coreR * 0.34, 0, Math.PI * 2)
      ctx.strokeStyle = `${accent},0.85)`
      ctx.lineWidth = 1.2 * dpr
      ctx.stroke()

      raf = requestAnimationFrame(draw)
    }

    raf = requestAnimationFrame(draw)
    return () => {
      cancelAnimationFrame(raf)
      ro.disconnect()
    }
  }, [intensity])

  return <canvas ref={canvasRef} className="ai-core-canvas" aria-hidden="true" />
}
