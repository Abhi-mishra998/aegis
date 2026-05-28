import React, { useState, useRef, useCallback, useMemo, useEffect, useContext } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Radio, Wifi, WifiOff, Loader2, Trash2, Pause, Play,
  AlertTriangle, Shield, Activity, Filter, ChevronRight,
} from 'lucide-react'
import { useSSE } from '../hooks/useSSE'
import { auditService } from '../services/api'
import { AgentContext } from '../context/AgentContext'

const EVENT_META = {
  risk_updated:       { label: 'Risk Update',      color: 'text-amber-400',  dot: 'bg-amber-500',  border: 'border-amber-500/20' },
  tool_executed:      { label: 'Tool Executed',     color: 'text-blue-400',   dot: 'bg-blue-500',   border: 'border-blue-500/20' },
  policy_decision:    { label: 'Policy Decision',   color: 'text-purple-400', dot: 'bg-purple-500', border: 'border-purple-500/20' },
  alert:              { label: 'Security Alert',    color: 'text-red-400',    dot: 'bg-red-500',    border: 'border-red-500/20' },
  agent_changed:      { label: 'Agent Changed',     color: 'text-green-400',  dot: 'bg-green-500',  border: 'border-green-500/20' },
  insight_generated:  { label: 'Insight',           color: 'text-blue-400',   dot: 'bg-blue-500',   border: 'border-blue-500/20' },
  behavior_flagged:   { label: 'Behavior Flagged',  color: 'text-orange-400', dot: 'bg-orange-500', border: 'border-orange-500/20' },
  kill_switch:        { label: 'Kill Switch',       color: 'text-red-400',    dot: 'bg-red-500',    border: 'border-red-500/20' },
}

const ALL_TYPES = Object.keys(EVENT_META)
const MAX_EVENTS = 200

function timeAgo(ts) {
  const diff = Date.now() - ts
  if (diff < 60_000) return `${Math.floor(diff / 1000)}s`
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m`
  return `${Math.floor(diff / 3_600_000)}h`
}

const SSE_REASON_LABEL = {
  auth_expired:       'session expired',
  cors:               'cookie blocked',
  network:            'network error',
  heartbeat_timeout:  'stream stalled',
  unknown:            null,
}

function ConnectionBadge({ state, lastError }) {
  if (state === 'open') return (
    <span className="flex items-center gap-1.5 text-[11px] text-green-400">
      <Wifi size={12} /> Live
    </span>
  )
  if (state === 'connecting') return (
    <span className="flex items-center gap-1.5 text-[11px] text-amber-400">
      <Loader2 size={12} className="animate-spin" /> Connecting
    </span>
  )
  const reason = SSE_REASON_LABEL[lastError]
  return (
    <span className="flex items-center gap-1.5 text-[11px] text-neutral-500" title={lastError || ''}>
      <WifiOff size={12} />
      <span>Disconnected{reason ? ` — ${reason}` : ''}</span>
    </span>
  )
}

function EventRow({ ev, onInvestigate }) {
  const meta = EVENT_META[ev.type] ?? EVENT_META.alert
  return (
    <div className={`group flex items-start gap-3 px-4 py-3 border-b border-[var(--border-subtle)] hover:bg-white/[0.02] transition-colors`}>
      <div className={`mt-1.5 w-2 h-2 rounded-full shrink-0 ${meta.dot} ${ev.fresh ? 'animate-pulse' : ''}`} aria-hidden="true" />

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-0.5">
          <span className={`text-[10px] font-bold uppercase tracking-wider ${meta.color}`}>{meta.label}</span>
          {ev.data?.decision && (
            <span className={`text-[10px] px-1.5 py-0 rounded border font-mono ${ev.data.decision === 'deny' ? 'border-red-500/30 text-red-400' : 'border-green-500/30 text-green-400'}`}>
              {ev.data.decision}
            </span>
          )}
        </div>
        {ev.data?.agent_id && (
          <p className="text-[11px] text-neutral-500 font-mono">
            Agent: {ev.data.agent_id?.slice(0, 16)}
          </p>
        )}
        {ev.data?.tool && (
          <p className="text-[11px] text-neutral-500 font-mono">Tool: {ev.data.tool}</p>
        )}
        {ev.data?.reason && (
          <p className="text-[11px] text-neutral-500 italic truncate">"{ev.data.reason}"</p>
        )}
        {ev.data?.risk_score !== undefined && (
          <p className={`text-[11px] font-mono ${Number(ev.data.risk_score) > 0.7 ? 'text-red-400' : Number(ev.data.risk_score) > 0.4 ? 'text-amber-400' : 'text-green-400'}`}>
            Risk: {Number(ev.data.risk_score).toFixed(3)}
          </p>
        )}
      </div>

      <div className="shrink-0 text-right flex flex-col items-end gap-1.5">
        <span className="text-[10px] text-neutral-700 font-mono">{timeAgo(ev.ts)}</span>
        {ev.data?.agent_id && (
          <button
            onClick={() => onInvestigate(ev)}
            className="opacity-0 group-hover:opacity-100 text-[10px] text-neutral-500 hover:text-white flex items-center gap-1 transition-all"
            aria-label="Investigate in Forensics"
          >
            Investigate <ChevronRight size={10} />
          </button>
        )}
      </div>
    </div>
  )
}

export default function LiveFeed() {
  const navigate = useNavigate()
  const { selectedAgentId, selectedAgent } = useContext(AgentContext)
  const [events, setEvents] = useState([])
  const [paused, setPaused] = useState(false)
  const [filterTypes, setFilterTypes] = useState(new Set())
  const [backfillLoading, setBackfillLoading] = useState(true)
  const pausedRef = useRef(false)

  pausedRef.current = paused

  // Backfill the last N decisions on mount + when the agent scope changes.
  // Without this, the feed sits empty until a new event is published — users
  // wait minutes thinking the stream is broken when really nothing has
  // happened yet. We pull from /audit/logs which is the same source of
  // truth tool_executed events are derived from.
  useEffect(() => {
    let cancelled = false
    const load = async () => {
      setBackfillLoading(true)
      try {
        const res = await auditService.getLogs(50, 0, selectedAgentId || undefined)
        const items = res?.data?.items || res?.items || []
        if (cancelled) return
        const seeded = items
          .map((row) => {
            const eventType = row.decision === 'block' || row.decision === 'deny'
              ? 'policy_decision'
              : 'tool_executed'
            return {
              id:    `bf-${row.id || crypto.randomUUID()}`,
              type:  eventType,
              data: {
                request_id: row.request_id,
                agent_id:   row.agent_id,
                tool:       row.tool,
                action:     row.decision,
                decision:   row.decision,
                risk:       row.metadata_json?.risk_score,
                risk_score: row.metadata_json?.risk_score,
                reason:     row.reason || (row.metadata_json?.reasons || [])[0],
                reasons:    row.metadata_json?.reasons || [],
              },
              ts:    row.timestamp ? new Date(row.timestamp).getTime() : Date.now(),
              fresh: false,
            }
          })
          .filter((e) => e.data.agent_id && e.data.agent_id !== '00000000-0000-0000-0000-000000000000')
        setEvents(seeded)
      } catch (_e) {
        // backfill is best-effort; SSE will still populate live events
      } finally {
        if (!cancelled) setBackfillLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [selectedAgentId])

  const handleMessage = useCallback((raw) => {
    if (pausedRef.current) return
    const type = raw?.type || raw?.event || 'alert'
    // Backend SSE wraps the actual payload as {type, data:{...}, ts}.
    // Unwrap the inner object so EventRow can read fields like agent_id,
    // tool, action, risk directly. Keep the outer wrapper for legacy
    // event shapes that came in flat (e.g. raw alerts from older publishers).
    const inner = (raw && typeof raw === 'object' && raw.data && typeof raw.data === 'object') ? raw.data : raw
    // Normalise action → decision so existing display logic works.
    const normalised = {
      ...inner,
      decision: inner?.decision ?? inner?.action,
      // Surface the numeric risk as risk_score so the colour helper hits.
      risk_score: inner?.risk_score ?? inner?.risk,
    }
    if (!ALL_TYPES.includes(type) && !normalised?.risk_score && !normalised?.decision) return
    const entry = {
      id:    crypto.randomUUID(),
      type:  ALL_TYPES.includes(type) ? type : 'alert',
      data:  normalised,
      ts:    Date.now(),
      fresh: true,
    }
    setEvents((prev) => {
      const next = [entry, ...prev].slice(0, MAX_EVENTS)
      // clear fresh flag after 2s without setState loop
      setTimeout(() => {
        setEvents((p) => p.map((e) => e.id === entry.id ? { ...e, fresh: false } : e))
      }, 2000)
      return next
    })
  }, [])

  const channels = useMemo(() => {
    const ch = {}
    ALL_TYPES.forEach((t) => { ch[t] = (data) => handleMessage({ ...data, type: t }) })
    return ch
  }, [handleMessage])

  const { state, reconnect, lastError } = useSSE({
    onMessage: handleMessage,
    channels,
    agentId: selectedAgentId || undefined,
  })

  const visible = filterTypes.size > 0
    ? events.filter((e) => filterTypes.has(e.type))
    : events

  const toggleFilter = (type) => {
    setFilterTypes((prev) => {
      const next = new Set(prev)
      if (next.has(type)) next.delete(type)
      else next.add(type)
      return next
    })
  }

  const investigate = (ev) => {
    const agentId = ev.data?.agent_id
    if (agentId) navigate(`/forensics?agent=${agentId}`)
    else navigate('/forensics')
  }

  const typeCounts = useMemo(() => {
    const c = {}
    events.forEach((e) => { c[e.type] = (c[e.type] || 0) + 1 })
    return c
  }, [events])

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <header className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div>
            <h1 className="text-2xl font-semibold text-white mb-1 flex items-center gap-2">
              <Radio size={20} className="text-neutral-500" aria-hidden="true" />
              Live Event Feed
            </h1>
            <p className="text-sm text-neutral-400">Real-time security events from the gateway SSE stream.</p>
            <div className="flex items-center gap-2 mt-1.5">
              {selectedAgent ? (
                <span className="inline-flex items-center gap-1.5 text-[10px] px-2 py-0.5 rounded-full bg-white/[0.05] border border-white/10 text-neutral-400">
                  <Filter size={9} /> Scope: {selectedAgent.name || selectedAgentId.slice(0, 8)}
                </span>
              ) : (
                <span className="inline-flex items-center gap-1.5 text-[10px] px-2 py-0.5 rounded-full bg-white/[0.04] border border-white/[0.06] text-neutral-500">
                  <Filter size={9} /> Scope: All agents (tenant-wide)
                </span>
              )}
              {backfillLoading && (
                <span className="inline-flex items-center gap-1 text-[10px] text-neutral-500">
                  <Loader2 size={9} className="animate-spin" /> Loading recent events…
                </span>
              )}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <ConnectionBadge state={state} lastError={lastError} />
          {state !== 'open' && (
            <button
              onClick={reconnect}
              className="text-xs text-neutral-400 hover:text-white border border-[var(--border-subtle)] px-3 py-1.5 rounded-lg"
            >
              Reconnect
            </button>
          )}
          <button
            onClick={() => setPaused((v) => !v)}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-xs transition-colors ${paused ? 'border-amber-500/30 text-amber-400 bg-amber-500/10' : 'border-[var(--border-subtle)] text-neutral-400 hover:text-white'}`}
            aria-label={paused ? 'Resume stream' : 'Pause stream'}
          >
            {paused ? <Play size={12} /> : <Pause size={12} />}
            {paused ? 'Resume' : 'Pause'}
          </button>
          <button
            onClick={() => setEvents([])}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[var(--border-subtle)] text-xs text-neutral-500 hover:text-red-400 hover:border-red-500/30 transition-colors"
            aria-label="Clear all events"
          >
            <Trash2 size={12} /> Clear
          </button>
        </div>
      </header>

      {/* Stats bar */}
      <div className="grid grid-cols-3 sm:grid-cols-6 gap-2">
        {ALL_TYPES.slice(0, 6).map((t) => {
          const meta = EVENT_META[t]
          const count = typeCounts[t] || 0
          return (
            <div key={t} className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg p-2.5 text-center">
              <div className={`text-lg font-bold ${meta.color}`}>{count}</div>
              <div className="text-[10px] text-neutral-600 mt-0.5 truncate">{meta.label}</div>
            </div>
          )
        })}
      </div>

      {/* Filters */}
      <div className="flex items-center gap-2 flex-wrap">
        <Filter size={12} className="text-neutral-600" aria-hidden="true" />
        <button
          onClick={() => setFilterTypes(new Set())}
          className={`text-[10px] px-2.5 py-1 rounded-lg border transition-all ${filterTypes.size === 0 ? 'border-white/20 bg-white/[0.05] text-white' : 'border-[var(--border-subtle)] text-neutral-600 hover:text-white'}`}
        >
          All types
        </button>
        {ALL_TYPES.map((t) => {
          const meta = EVENT_META[t]
          const active = filterTypes.has(t)
          return (
            <button
              key={t}
              onClick={() => toggleFilter(t)}
              className={`text-[10px] px-2.5 py-1 rounded-lg border transition-all ${active ? `border-white/20 bg-white/[0.05] ${meta.color}` : 'border-[var(--border-subtle)] text-neutral-600 hover:text-white'}`}
            >
              {meta.label}
              {typeCounts[t] > 0 && <span className="ml-1 opacity-70">({typeCounts[t]})</span>}
            </button>
          )
        })}
      </div>

      {/* Paused banner */}
      {paused && (
        <div className="flex items-center gap-2 p-3 bg-amber-500/10 border border-amber-500/20 rounded-lg text-xs text-amber-400">
          <Pause size={12} />
          Stream paused — events are not being captured. Click Resume to continue.
        </div>
      )}

      {/* Event list */}
      <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl overflow-hidden">
        {visible.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 gap-3 text-neutral-700">
            <Activity size={32} className="opacity-30" aria-hidden="true" />
            <p className="text-sm">
              {state === 'open'
                ? filterTypes.size > 0
                  ? 'No events match the selected filters.'
                  : 'Waiting for events…'
                : 'Connect to start receiving events.'}
            </p>
          </div>
        ) : (
          <div>
            <div className="px-4 py-2 border-b border-[var(--border-subtle)] flex items-center justify-between">
              <span className="text-[10px] text-neutral-600">{visible.length} events{visible.length === MAX_EVENTS ? ` (capped at ${MAX_EVENTS})` : ''}</span>
              <span className="text-[10px] text-neutral-700 font-mono">newest first</span>
            </div>
            {visible.map((ev) => (
              <EventRow key={ev.id} ev={ev} onInvestigate={investigate} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
