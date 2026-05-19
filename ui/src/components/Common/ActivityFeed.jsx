import React, { useEffect, useState, useMemo, useRef } from 'react'
import {
  Activity, AlertTriangle, ShieldCheck, ShieldX, ChevronRight,
  Filter, Radio, CircleDot,
} from 'lucide-react'
import { useSSE } from '../../hooks/useSSE'

/**
 * ActivityFeed — realtime event rail (Datadog Live Tail / Wiz Live Risk feed).
 *
 *   <ActivityFeed
 *     maxItems={50}
 *     defaultSeverity="all"
 *     onSelect={(ev) => router.push(`/flight-recorder?id=${ev.request_id}`)}
 *   />
 *
 * Streams from `/events/stream` via `useSSE`. Items render newest-first; the
 * feed pauses auto-scroll when the user is hovering or focused inside it
 * (Linear-style — never steal scroll while reading).
 */
const SEV_TOKENS = {
  CRITICAL: { color: 'text-red-400',     bg: 'bg-red-500/10',     dot: 'bg-red-500',     icon: ShieldX },
  HIGH:     { color: 'text-orange-400',  bg: 'bg-orange-500/10',  dot: 'bg-orange-500',  icon: AlertTriangle },
  MEDIUM:   { color: 'text-amber-400',   bg: 'bg-amber-500/10',   dot: 'bg-amber-500',   icon: AlertTriangle },
  LOW:      { color: 'text-blue-400',    bg: 'bg-blue-500/10',    dot: 'bg-blue-500',    icon: ShieldCheck },
  INFO:     { color: 'text-neutral-400', bg: 'bg-white/[0.04]',   dot: 'bg-neutral-500', icon: Activity },
}

const inferSeverity = (ev) => {
  const explicit = (ev?.severity || ev?.level || '').toUpperCase()
  if (SEV_TOKENS[explicit]) return explicit
  const risk = Number(ev?.risk_score || 0)
  if (risk >= 0.9) return 'CRITICAL'
  if (risk >= 0.7) return 'HIGH'
  if (risk >= 0.5) return 'MEDIUM'
  if (risk > 0)    return 'LOW'
  return 'INFO'
}

const formatTime = (ts) => {
  const d = ts ? new Date(ts) : new Date()
  return d.toLocaleTimeString(undefined, { hour12: false })
}

const SEVERITY_FILTERS = ['all', 'CRITICAL', 'HIGH', 'MEDIUM', 'LOW']

export default function ActivityFeed({
  maxItems = 50,
  defaultSeverity = 'all',
  onSelect,
  className = '',
  title = 'Live activity',
}) {
  const [events, setEvents]     = useState([])
  const [severity, setSeverity] = useState(defaultSeverity)
  const [paused, setPaused]     = useState(false)
  const seenIds = useRef(new Set())

  const { state } = useSSE({
    onMessage: (ev) => {
      // Dedupe by request_id/event_id when present to handle replay storms.
      const id = ev?.request_id || ev?.event_id || ev?.id
      if (id && seenIds.current.has(id)) return
      if (id) seenIds.current.add(id)
      setEvents((prev) => {
        const next = [{ ...ev, _ts: Date.now(), _sev: inferSeverity(ev) }, ...prev]
        // Keep the dedupe set bounded — same window as the display.
        if (next.length > maxItems * 4) next.length = maxItems * 4
        if (seenIds.current.size > maxItems * 6) seenIds.current = new Set()
        return next
      })
    },
  })

  const filtered = useMemo(() => {
    const trimmed = events.slice(0, maxItems)
    if (severity === 'all') return trimmed
    return trimmed.filter((e) => e._sev === severity)
  }, [events, severity, maxItems])

  return (
    <aside
      className={`
        flex flex-col min-h-0 h-full
        bg-[var(--bg-surface)] border border-[var(--border-subtle)]
        rounded-2xl overflow-hidden
        ${className}
      `}
      aria-label="Live activity feed"
    >
      {/* Header */}
      <div className="px-4 py-3 border-b border-[var(--border-subtle)] flex items-center justify-between gap-3 shrink-0">
        <div className="flex items-center gap-2 min-w-0">
          <div className="relative">
            <Radio size={13} className="text-neutral-500" aria-hidden="true" />
            <span
              className={`
                absolute -top-1 -right-1 w-1.5 h-1.5 rounded-full
                ${state === 'open' ? 'bg-green-500' : 'bg-amber-500 animate-pulse'}
              `}
              aria-hidden="true"
            />
          </div>
          <h3 className="text-[11px] font-bold uppercase tracking-[0.15em] text-white truncate">
            {title}
          </h3>
          <span className="text-[10px] text-neutral-600 font-mono">
            {state === 'open' ? `${filtered.length}` : 'reconnecting…'}
          </span>
        </div>

        <button
          type="button"
          onClick={() => setPaused((v) => !v)}
          className="
            text-[10px] font-semibold uppercase tracking-wider
            text-neutral-500 hover:text-white
            px-2 py-1 rounded-md hover:bg-white/[0.05] transition-colors
            focus-visible:ring-1 focus-visible:ring-white/30
          "
          aria-pressed={paused}
        >
          {paused ? 'Resume' : 'Pause'}
        </button>
      </div>

      {/* Severity filter chips */}
      <div
        className="px-3 py-2 border-b border-[var(--border-subtle)] flex items-center gap-1.5 overflow-x-auto scrollbar-hide shrink-0"
        role="tablist"
        aria-label="Filter by severity"
      >
        <Filter size={11} className="text-neutral-600 shrink-0" aria-hidden="true" />
        {SEVERITY_FILTERS.map((sev) => {
          const tok = SEV_TOKENS[sev]
          const active = severity === sev
          return (
            <button
              key={sev}
              role="tab"
              aria-selected={active}
              onClick={() => setSeverity(sev)}
              className={`
                text-[10px] font-bold uppercase tracking-wider shrink-0
                px-2 py-1 rounded-md border transition-colors
                ${active
                  ? 'bg-white text-black border-white'
                  : 'border-[var(--border-subtle)] text-neutral-500 hover:text-white hover:border-[var(--border-default)]'}
              `}
            >
              {sev}
            </button>
          )
        })}
      </div>

      {/* Stream */}
      <ol
        className="flex-1 min-h-0 overflow-y-auto"
        aria-live="polite"
        aria-relevant="additions"
      >
        {filtered.length === 0 ? (
          <li className="px-4 py-12 text-center text-[11px] text-neutral-600">
            {state === 'open' ? 'Awaiting events…' : 'Stream offline'}
          </li>
        ) : (
          filtered.map((ev, i) => {
            const sev  = ev._sev
            const tok  = SEV_TOKENS[sev] ?? SEV_TOKENS.INFO
            const Icon = tok.icon
            const title = ev?.title || ev?.tool || ev?.message || ev?.kind || 'event'
            const detail = ev?.reason || ev?.summary || ev?.decision || ''
            return (
              <li
                key={`${ev._ts}-${i}`}
                className="border-b border-[var(--border-subtle)] last:border-b-0"
              >
                <button
                  type="button"
                  onClick={() => onSelect?.(ev)}
                  className="
                    w-full text-left flex items-start gap-3 px-3 py-2.5
                    hover:bg-white/[0.03] focus-visible:bg-white/[0.04]
                    transition-colors outline-none
                  "
                >
                  <span className={`mt-0.5 inline-flex w-6 h-6 rounded-md ${tok.bg} items-center justify-center shrink-0`}>
                    <Icon size={12} className={tok.color} aria-hidden="true" />
                  </span>
                  <span className="min-w-0 flex-1 flex flex-col gap-0.5">
                    <span className="flex items-center gap-2">
                      <span className={`text-[10px] font-bold uppercase tracking-wider ${tok.color}`}>{sev}</span>
                      <span className="text-[10px] text-neutral-600 font-mono">{formatTime(ev?.ts || ev._ts)}</span>
                    </span>
                    <span className="text-xs font-medium text-white truncate">{title}</span>
                    {detail && (
                      <span className="text-[11px] text-neutral-500 leading-snug line-clamp-2">{detail}</span>
                    )}
                  </span>
                  {onSelect && (
                    <ChevronRight size={13} className="text-neutral-700 mt-1 shrink-0" aria-hidden="true" />
                  )}
                </button>
              </li>
            )
          })
        )}
      </ol>
    </aside>
  )
}
