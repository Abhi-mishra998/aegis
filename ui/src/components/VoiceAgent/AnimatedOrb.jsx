import React, { useEffect, useRef } from 'react'

/**
 * Audio-reactive animated orb — the visual centerpiece of the voice agent
 * panel. Renders to a <canvas> so it stays smooth at 60fps without
 * triggering React re-renders.
 *
 * State drives the base behaviour:
 *   - idle:        slow breathing
 *   - connecting:  faster pulse with a cool blue hue
 *   - listening:   responsive to user mic volume (reactive amplitude)
 *   - thinking:    rotating dual-ring, no audio input
 *   - speaking:    responsive to agent audio amplitude (warmer hue)
 *
 * Audio reactivity comes from optional `audioLevel` prop in [0, 1].
 * If not provided, we fall back to a sinusoidal "breathing" envelope.
 */

const STATE_COLORS = {
  idle:       { hue: 200, sat: 30,  name: 'Idle' },
  connecting: { hue: 220, sat: 70,  name: 'Connecting' },
  listening:  { hue: 200, sat: 100, name: 'Listening' },
  thinking:   { hue: 280, sat: 80,  name: 'Thinking' },
  speaking:   { hue: 180, sat: 100, name: 'Speaking' },
  error:      { hue: 0,   sat: 80,  name: 'Error' },
}

export default function AnimatedOrb({ state = 'idle', audioLevel = 0, size = 280 }) {
  const canvasRef = useRef(null)
  const stateRef  = useRef(state)
  const levelRef  = useRef(audioLevel)
  const rafRef    = useRef(0)

  useEffect(() => { stateRef.current = state }, [state])
  useEffect(() => { levelRef.current = audioLevel }, [audioLevel])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const dpr = Math.min(window.devicePixelRatio || 1, 2)
    canvas.width  = size * dpr
    canvas.height = size * dpr
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    ctx.scale(dpr, dpr)

    const cx = size / 2
    const cy = size / 2
    const baseR = size * 0.28

    // Smoothed audio level for non-jittery reactivity
    let smoothLevel = 0

    // Ring offsets for the orbiting rings (Thinking + ambient)
    let rotation = 0

    const t0 = performance.now()

    const tick = () => {
      const now = performance.now()
      const t = (now - t0) / 1000
      const s = stateRef.current
      const target = levelRef.current
      smoothLevel += (target - smoothLevel) * 0.18

      const palette = STATE_COLORS[s] || STATE_COLORS.idle
      const hue = palette.hue
      const sat = palette.sat

      // Breathing envelope when audio is silent
      const breath = (Math.sin(t * 1.4) + 1) / 2
      const amp = Math.max(smoothLevel, breath * 0.18)

      rotation += s === 'thinking' ? 0.025 : 0.005

      // Clear with subtle radial vignette
      ctx.clearRect(0, 0, size, size)

      // Outer halo glow
      const haloR = baseR + 60 + amp * 40
      const halo = ctx.createRadialGradient(cx, cy, baseR * 0.6, cx, cy, haloR)
      halo.addColorStop(0,    `hsla(${hue}, ${sat}%, 60%, ${0.35 + amp * 0.3})`)
      halo.addColorStop(0.45, `hsla(${hue + 20}, ${sat}%, 50%, ${0.18 + amp * 0.15})`)
      halo.addColorStop(1,    `hsla(${hue}, ${sat}%, 30%, 0)`)
      ctx.fillStyle = halo
      ctx.beginPath()
      ctx.arc(cx, cy, haloR, 0, Math.PI * 2)
      ctx.fill()

      // Orbital rings — 3 concentric, slightly offset, rotate slowly
      for (let i = 0; i < 3; i++) {
        const ringR = baseR + 18 + i * 14 + Math.sin(t * 1.2 + i) * 6 + amp * (10 + i * 6)
        const alpha = (0.22 - i * 0.05) + amp * 0.18
        ctx.strokeStyle = `hsla(${hue + i * 12}, ${sat}%, ${70 - i * 8}%, ${alpha})`
        ctx.lineWidth = 1.2
        ctx.beginPath()
        // Subtle non-circular wobble using a parametric ellipse with phase
        const phase = rotation + i * 0.8
        for (let a = 0; a <= Math.PI * 2 + 0.05; a += 0.05) {
          const wobble = 1 + Math.sin(a * 5 + phase) * 0.02 * (1 + amp * 2)
          const x = cx + Math.cos(a) * ringR * wobble
          const y = cy + Math.sin(a) * ringR * wobble
          if (a === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y)
        }
        ctx.stroke()
      }

      // Core orb — radial gradient
      const coreR = baseR + amp * 14
      const core = ctx.createRadialGradient(
        cx - coreR * 0.25, cy - coreR * 0.25, 0,
        cx, cy, coreR,
      )
      core.addColorStop(0,    `hsla(${hue + 30}, ${sat}%, 75%, 0.95)`)
      core.addColorStop(0.45, `hsla(${hue + 10}, ${sat}%, 55%, 0.85)`)
      core.addColorStop(1,    `hsla(${hue - 10}, ${sat}%, 28%, 0.9)`)
      ctx.fillStyle = core
      ctx.beginPath()
      ctx.arc(cx, cy, coreR, 0, Math.PI * 2)
      ctx.fill()

      // Inner highlight (top-left)
      const hl = ctx.createRadialGradient(
        cx - coreR * 0.4, cy - coreR * 0.5, 1,
        cx - coreR * 0.4, cy - coreR * 0.5, coreR * 0.7,
      )
      hl.addColorStop(0, `hsla(0, 0%, 100%, ${0.45 + amp * 0.25})`)
      hl.addColorStop(1, 'hsla(0, 0%, 100%, 0)')
      ctx.fillStyle = hl
      ctx.beginPath()
      ctx.arc(cx, cy, coreR, 0, Math.PI * 2)
      ctx.fill()

      // Audio-reactive shimmer ring on the edge — only when there's signal
      if (smoothLevel > 0.05) {
        const shimmerR = coreR + 4 + smoothLevel * 6
        ctx.strokeStyle = `hsla(${hue + 60}, 100%, 80%, ${smoothLevel * 0.9})`
        ctx.lineWidth = 1.8
        ctx.beginPath()
        ctx.arc(cx, cy, shimmerR, 0, Math.PI * 2)
        ctx.stroke()
      }

      // Thinking-state extra: small particles orbiting
      if (s === 'thinking') {
        for (let i = 0; i < 6; i++) {
          const a = rotation * 3 + (i * Math.PI * 2) / 6
          const r = baseR + 28 + Math.sin(t * 2 + i) * 5
          const px = cx + Math.cos(a) * r
          const py = cy + Math.sin(a) * r
          ctx.fillStyle = `hsla(${hue + i * 8}, ${sat}%, 75%, 0.85)`
          ctx.beginPath()
          ctx.arc(px, py, 2.5, 0, Math.PI * 2)
          ctx.fill()
        }
      }

      rafRef.current = requestAnimationFrame(tick)
    }

    rafRef.current = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(rafRef.current)
  }, [size])

  return (
    <canvas
      ref={canvasRef}
      style={{
        width:  `${size}px`,
        height: `${size}px`,
        display: 'block',
      }}
      aria-hidden="true"
    />
  )
}

export { STATE_COLORS }
