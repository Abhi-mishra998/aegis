import React, { useEffect, useRef, useState } from 'react'
import { TrendingUp, TrendingDown, Minus } from 'lucide-react'

/**
 * LiveKpiTile — single-metric tile with sparkline + delta + pulse-on-update.
 *
 *   <LiveKpiTile
 *     label="Requests / min"
 *     value={942}
 *     unit=""
 *     deltaPct={+12.4}
 *     series={[920, 931, 928, 941, 942]}
 *     hint="last 5m"
 *   />
 *
 * Pulse animation triggers when `value` changes — gives operators a real-time
 * "this number just moved" signal without yanking attention from the rest of
 * the dashboard. Respects prefers-reduced-motion.
 */
function Sparkline({ series, width = 80, height = 24, color = 'rgba(255,255,255,0.35)' }) {
  if (!Array.isArray(series) || series.length < 2) {
    return <div style={{ width, height }} aria-hidden="true" />
  }
  const min = Math.min(...series)
  const max = Math.max(...series)
  const range = max - min || 1
  const step  = width / (series.length - 1)
  const points = series
    .map((v, i) => `${i * step},${height - ((v - min) / range) * (height - 2) - 1}`)
    .join(' ')
  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      aria-hidden="true"
      preserveAspectRatio="none"
    >
      <polyline
        fill="none"
        stroke={color}
        strokeWidth="1.25"
        strokeLinejoin="round"
        strokeLinecap="round"
        points={points}
      />
    </svg>
  )
}

const formatValue = (v, unit = '') => {
  if (v == null) return '—'
  if (typeof v !== 'number') return `${v}${unit}`
  if (Math.abs(v) >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M${unit}`
  if (Math.abs(v) >= 1_000)     return `${(v / 1_000).toFixed(1)}k${unit}`
  return Number.isInteger(v) ? `${v}${unit}` : `${v.toFixed(2)}${unit}`
}

export default function LiveKpiTile({
  label,
  value,
  unit = '',
  deltaPct = null,
  series = [],
  hint,
  intent = 'neutral',          // "good" | "bad" | "neutral"
  className = '',
}) {
  const prev = useRef(value)
  const [pulse, setPulse] = useState(false)

  useEffect(() => {
    if (prev.current !== value) {
      setPulse(true)
      const t = setTimeout(() => setPulse(false), 700)
      prev.current = value
      return () => clearTimeout(t)
    }
  }, [value])

  let intentColor = 'text-neutral-300'
  let deltaIcon = Minus
  let deltaColor = 'text-neutral-500'
  if (deltaPct != null) {
    if (deltaPct > 0) {
      deltaIcon  = TrendingUp
      deltaColor = intent === 'bad' ? 'text-red-400' : 'text-emerald-400'
    } else if (deltaPct < 0) {
      deltaIcon  = TrendingDown
      deltaColor = intent === 'bad' ? 'text-emerald-400' : 'text-red-400'
    }
  }
  const DeltaIcon = deltaIcon

  return (
    <div
      className={`
        relative overflow-hidden
        flex flex-col gap-3 min-w-0
        bg-[var(--bg-surface)] border border-[var(--border-subtle)]
        rounded-2xl px-4 py-4
        transition-colors
        ${pulse ? 'border-white/30' : ''}
        ${className}
      `}
    >
      {pulse && (
        <span
          aria-hidden="true"
          className="
            absolute inset-0 pointer-events-none rounded-2xl
            ring-1 ring-white/15
            motion-reduce:hidden
            animate-fade-in-opacity
          "
        />
      )}
      <div className="flex items-center justify-between gap-2 min-w-0">
        <span className="text-[10px] font-bold uppercase tracking-[0.15em] text-neutral-500 truncate">
          {label}
        </span>
        {deltaPct != null && (
          <span className={`inline-flex items-center gap-0.5 text-[10px] font-bold ${deltaColor}`}>
            <DeltaIcon size={10} aria-hidden="true" />
            {Math.abs(deltaPct).toFixed(1)}%
          </span>
        )}
      </div>
      <div className="flex items-end justify-between gap-3 min-w-0">
        <span className={`text-2xl font-bold tracking-tight ${intentColor} truncate`}>
          {formatValue(value, unit)}
        </span>
        <Sparkline series={series} />
      </div>
      {hint && (
        <span className="text-[10px] text-neutral-600 truncate">{hint}</span>
      )}
    </div>
  )
}
