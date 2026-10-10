import { useEffect, useRef } from 'react'

/** Subtle futuristic background: perspective grid + faint particles. */
export function Background({ intensity }: { intensity: number }) {
  const ref = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = ref.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    let raf = 0
    let t = 0
    const dpr = Math.min(window.devicePixelRatio || 1, 1.5)

    const resize = () => {
      canvas.width = canvas.clientWidth * dpr
      canvas.height = canvas.clientHeight * dpr
    }
    resize()
    const ro = new ResizeObserver(resize)
    ro.observe(canvas)

    const particles = Array.from({ length: 60 }, () => ({
      x: Math.random(),
      y: Math.random(),
      vx: (Math.random() - 0.5) * 0.00012,
      vy: (Math.random() - 0.5) * 0.00012,
      s: 0.5 + Math.random() * 1.2,
      a: 0.04 + Math.random() * 0.12,
    }))

    const draw = () => {
      const w = canvas.width
      const h = canvas.height
      t += 0.25 * intensity
      ctx.clearRect(0, 0, w, h)

      // horizontal scan drift
      const grad = ctx.createLinearGradient(0, 0, 0, h)
      grad.addColorStop(0, 'rgba(10,18,32,0.9)')
      grad.addColorStop(0.5, 'rgba(4,7,12,0.95)')
      grad.addColorStop(1, 'rgba(7,13,22,0.9)')
      ctx.fillStyle = grad
      ctx.fillRect(0, 0, w, h)

      // grid
      const cell = 56 * dpr
      ctx.strokeStyle = `rgba(56,189,248,${0.05 * intensity})`
      ctx.lineWidth = 1
      ctx.beginPath()
      for (let x = (t * 0.3) % cell; x < w; x += cell) {
        ctx.moveTo(x, 0)
        ctx.lineTo(x, h)
      }
      for (let y = (t * 0.18) % cell; y < h; y += cell) {
        ctx.moveTo(0, y)
        ctx.lineTo(w, y)
      }
      ctx.stroke()

      // horizon glow line
      const gy = h * 0.78
      const glow = ctx.createLinearGradient(0, gy - 40, 0, gy + 40)
      glow.addColorStop(0, 'rgba(34,211,238,0)')
      glow.addColorStop(0.5, `rgba(34,211,238,${0.05 * intensity})`)
      glow.addColorStop(1, 'rgba(34,211,238,0)')
      ctx.fillStyle = glow
      ctx.fillRect(0, gy - 40, w, 80)

      // particles
      for (const p of particles) {
        p.x += p.vx * intensity
        p.y += p.vy * intensity
        if (p.x < 0) p.x = 1
        if (p.x > 1) p.x = 0
        if (p.y < 0) p.y = 1
        if (p.y > 1) p.y = 0
        ctx.beginPath()
        ctx.arc(p.x * w, p.y * h, p.s * dpr, 0, Math.PI * 2)
        ctx.fillStyle = `rgba(125,211,252,${p.a * intensity})`
        ctx.fill()
      }

      raf = requestAnimationFrame(draw)
    }
    raf = requestAnimationFrame(draw)
    return () => {
      cancelAnimationFrame(raf)
      ro.disconnect()
    }
  }, [intensity])

  return <canvas ref={ref} className="bg-canvas" aria-hidden="true" />
}
