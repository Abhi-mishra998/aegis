import React, { useState, useRef, useCallback, useMemo, useEffect, useContext } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Radio, Wifi, WifiOff, Loader2, Trash2, Pause, Play,
  Activity, Filter, ChevronRight, User, Cpu, Download, Gauge,
} from 'lucide-react'
import { useSSE } from '../hooks/useSSE'
import { auditService } from '../services/api'
import { AgentContext } from '../context/AgentContext'
import { AuthContext } from '../context/AuthContext'
import DataFreshness from '../components/Common/DataFreshness'

// ─── EVENT TYPE REGISTRY ────────────────────────────────────────────────────
// Backend publishes 15 event types on the SSE stream; this map controls how
// each is rendered. Keep keys in sync with the names emitted by
// `services/gateway/sse_publisher.py` (and the audit / autonomy / quota /
// shadow-mode publishers that feed it). `ALL_TYPES` is derived from this map
// so adding a new key here is sufficient — no other edits are needed.
const EVENT_META = {
  // LLM-proxy events from /v1/messages + /v1/chat/completions.
  // Aegis publishes these on every Claude / OpenAI call so the feed shows
  // real traffic, not just /execute tool-calls.
  llm_proxy_call:     { label: 'LLM Call',           color: 'text-sky-400',    dot: 'bg-sky-500',    border: 'border-sky-500/20' },
  llm_proxy_escalate: { label: 'Approval Queued',    color: 'text-amber-400',  dot: 'bg-amber-500',  border: 'border-amber-500/20' },
  approval_required:  { label: 'Approval Required',  color: 'text-purple-400', dot: 'bg-purple-500', border: 'border-purple-500/20' },
  approval_resolved:  { label: 'Approval Resolved',  color: 'text-green-400',  dot: 'bg-green-500',  border: 'border-green-500/20' },
  // Existing /execute tool-call + signal-engine events.
  risk_updated:       { label: 'Risk Update',        color: 'text-amber-400',  dot: 'bg-amber-500',  border: 'border-amber-500/20' },
  tool_executed:      { label: 'Tool Executed',      color: 'text-blue-400',   dot: 'bg-blue-500',   border: 'border-blue-500/20' },
  policy_decision:    { label: 'Policy Decision',    color: 'text-purple-400', dot: 'bg-purple-500', border: 'border-purple-500/20' },
  alert:              { label: 'Security Alert',     color: 'text-red-400',    dot: 'bg-red-500',    border: 'border-red-500/20' },
  agent_changed:      { label: 'Agent Changed',      color: 'text-green-400',  dot: 'bg-green-500',  border: 'border-green-500/20' },
  agent_created:      { label: 'Agent Created',      color: 'text-green-400',  dot: 'bg-green-500',  border: 'border-green-500/20' },
  agent_deleted:      { label: 'Agent Deleted',      color: 'text-red-400',    dot: 'bg-red-500',    border: 'border-red-500/20' },
  incident_updated:   { label: 'Incident Updated',   color: 'text-purple-400', dot: 'bg-purple-500', border: 'border-purple-500/20' },
  insight_generated:  { label: 'Insight',            color: 'text-blue-400',   dot: 'bg-blue-500',   border: 'border-blue-500/20' },
  behavior_flagged:   { label: 'Behavior Flagged',   color: 'text-orange-400', dot: 'bg-orange-500', border: 'border-orange-500/20' },
  would_have_blocked: { label: 'Would Have Blocked', color: 'text-orange-400', dot: 'bg-orange-500', border: 'border-orange-500/20' },
  quota_warning:      { label: 'Quota Warning',      color: 'text-amber-400',  dot: 'bg-amber-500',  border: 'border-amber-500/20' },
  kill_switch:        { label: 'Kill Switch',        color: 'text-red-400',    dot: 'bg-red-500',    border: 'border-red-500/20' },
}

const ALL_TYPES = Object.keys(EVENT_META)
const MAX_EVENTS         = 200
// Throughput gauge: 12 buckets × 5 s each = 60 s window.
const THROUGHPUT_BUCKETS = 12
const THROUGHPUT_BUCKET_MS = 5_000

// Types listed under each scope must exist in EVENT_META above — anything
// else gets silently dropped by the visibility filter.
const SCOPES = {
  all:      { label: 'All',              types: ALL_TYPES },
  decisions:{ label: 'Decisions',        types: ['policy_decision', 'tool_executed'] },
  risk:     { label: 'Risk',             types: ['risk_updated', 'behavior_flagged'] },
  security: { label: 'Security Signals', types: ['alert', 'kill_switch'] },
  system:   { label: 'System',           types: ['agent_changed', 'insight_generated'] },
}

function timeAgo(ts) {
  const diff = Date.now() - ts
  if (diff < 60_000)    return `${Math.floor(diff / 1000)}s`
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

// ─── CONNECTION BADGE ───────────────────────────────────────────────────────
// Enlarged for monitor-wall visibility (text-sm / 14px icon, pill background).
function ConnectionBadge({ state, lastError }) {
  if (state === 'open') return (
    <span
      className="flex items-center gap-1.5 text-sm font-medium px-3 py-1.5 rounded-full bg-green-500/15 text-green-300 border border-green-500/30"
      title="SSE stream live"
    >
      <Wifi size={14} aria-hidden="true" /> Live
    </span>
  )
  if (state === 'connecting') return (
    <span
      className="flex items-center gap-1.5 text-sm font-medium px-3 py-1.5 rounded-full bg-amber-500/15 text-amber-300 border border-amber-500/30"
      title="Establishing SSE connection"
    >
      <Loader2 size={14} className="animate-spin" aria-hidden="true" /> Connecting
    </span>
  )
  const reason = SSE_REASON_LABEL[lastError]
  return (
    <span
      className="flex items-center gap-1.5 text-sm font-medium px-3 py-1.5 rounded-full bg-red-500/15 text-red-300 border border-red-500/30"
      title={lastError || 'Disconnected'}
    >
      <WifiOff size={14} aria-hidden="true" />
      <span>Disconnected{reason ? ` — ${reason}` : ''}</span>
    </span>
  )
}

// ─── THROUGHPUT GAUGE ───────────────────────────────────────────────────────
// Inline SVG sparkline. We deliberately avoid recharts here — it's 400 KB
// and is already lazy-loaded for the heavier dashboards.
function ThroughputGauge({ events }) {
  const [now, setNow] = useState(() => Date.now())

  // Recompute every 5 s, matching the bucket size so the right-most bar
  // always corresponds to "this very moment". A faster cadence wastes
  // renders; a slower one would let the right-most bar look stale.
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), THROUGHPUT_BUCKET_MS)
    return () => clearInterval(t)
  }, [])

  const { buckets, rate } = useMemo(() => {
    const b = new Array(THROUGHPUT_BUCKETS).fill(0)
    const oldest = now - THROUGHPUT_BUCKETS * THROUGHPUT_BUCKET_MS
    for (const ev of events) {
      if (!ev.ts || ev.ts < oldest || ev.ts > now) continue
      const idx = Math.min(
        THROUGHPUT_BUCKETS - 1,
        Math.floor((ev.ts - oldest) / THROUGHPUT_BUCKET_MS),
      )
      b[idx] += 1
    }
    // Current rate = events in the latest (right-most) full bucket / bucket-seconds.
    const r = b[THROUGHPUT_BUCKETS - 1] / (THROUGHPUT_BUCKET_MS / 1000)
    return { buckets: b, rate: r }
  }, [events, now])

  const max  = Math.max(1, ...buckets)
  const w    = 120
  const h    = 28
  const stepX = w / (THROUGHPUT_BUCKETS - 1)
  const points = buckets
    .map((v, i) => `${(i * stepX).toFixed(1)},${(h - (v / max) * h).toFixed(1)}`)
    .join(' ')

  return (
    <div
      className="flex flex-col items-end"
      title={`Events received in the last ${THROUGHPUT_BUCKETS * THROUGHPUT_BUCKET_MS / 1000}s`}
      aria-label={`Throughput: ${rate.toFixed(1)} events per second`}
    >
      <div className="flex items-center gap-1.5 text-xs font-mono text-neutral-400">
        <Gauge size={12} aria-hidden="true" />
        <span className="text-white font-semibold tabular-nums">{rate.toFixed(1)}</span>
        <span className="text-neutral-600">ev/s</span>
      </div>
      <svg width={w} height={h} className="mt-0.5" aria-hidden="true">
        <polyline
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          className="text-cyan-400"
          points={points}
        />
      </svg>
    </div>
  )
}

function EventRow({ ev, onInvestigate }) {
  const meta = EVENT_META[ev.type] ?? EVENT_META.alert
  const isLLMCall  = ev.type === 'llm_proxy_call'
  const isEscalate = ev.type === 'llm_proxy_escalate'
  // Decision pill colour: deny → red, escalate → amber, allow → green.
  const decisionColour =
    ev.data?.decision === 'deny'     ? 'border-red-500/30 text-red-400' :
    isEscalate                       ? 'border-amber-500/30 text-amber-400' :
                                       'border-green-500/30 text-green-400'
  return (
    <div className={`group flex items-start gap-3 px-4 py-3 border-b border-[var(--border-subtle)] hover:bg-white/[0.02] transition-colors`}>
      <div className={`mt-1.5 w-2 h-2 rounded-full shrink-0 ${meta.dot} ${ev.fresh ? 'animate-pulse' : ''}`} aria-hidden="true" />

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-0.5 flex-wrap">
          <span className={`text-[10px] font-bold uppercase tracking-wider ${meta.color}`}>{meta.label}</span>
          {(ev.data?.decision || isEscalate) && (
            <span className={`text-[10px] px-1.5 py-0 rounded border font-mono ${decisionColour}`}>
              {isEscalate ? 'escalate' : ev.data.decision}
            </span>
          )}
          {ev.data?.model && (
            <span className="text-[10px] px-1.5 py-0 rounded border border-white/10 text-neutral-400 font-mono truncate max-w-[180px]">
              {ev.data.model}
            </span>
          )}
          {ev.data?.employee_email && (
            <span className="text-[10px] px-1.5 py-0 rounded border border-cyan-500/30 text-cyan-300 font-mono flex items-center gap-1">
              <User size={9} /> {ev.data.employee_email}
            </span>
          )}
          {ev.data?.model && (
            <span className="text-[10px] px-1.5 py-0 rounded border border-purple-500/30 text-purple-300 font-mono flex items-center gap-1">
              <Cpu size={9} /> {ev.data.model}
            </span>
          )}
        </div>
        {/* LLM-proxy traffic: surface who called what, how much, how long. */}
        {(isLLMCall || isEscalate) && ev.data?.employee_email && (
          <p className="text-[11px] text-neutral-400 font-mono truncate">
            {ev.data.employee_email}
          </p>
        )}
        {isLLMCall && (ev.data?.input_tokens != null || ev.data?.output_tokens != null) && (
          <p className="text-[11px] text-neutral-500 font-mono">
            {ev.data.input_tokens ?? 0} in · {ev.data.output_tokens ?? 0} out
            {ev.data?.latency_ms != null && (
              <> · <span className={Number(ev.data.latency_ms) > 1500 ? 'text-amber-400' : ''}>{ev.data.latency_ms} ms</span></>
            )}
            {ev.data?.cost_usd != null && Number(ev.data.cost_usd) > 0 && (
              <> · ${Number(ev.data.cost_usd).toFixed(4)}</>
            )}
          </p>
        )}
        {isEscalate && ev.data?.matched_pattern && (
          <p className="text-[11px] text-amber-300/80 font-mono truncate">
            {ev.data.matched_pattern}
            {ev.data?.approver_role && <span className="text-neutral-500"> → {ev.data.approver_role}</span>}
          </p>
        )}
        {/* Legacy /execute pipeline events. */}
        {ev.data?.agent_id && (
          <p className="text-[11px] text-neutral-500 font-mono">
            Agent: {ev.data.agent_id?.slice(0, 16)}
          </p>
        )}
        {ev.data?.tool && (
          <p className="text-[11px] text-neutral-500 font-mono">Tool: {ev.data.tool}</p>
        )}
        {ev.data?.matched_pattern && (
          <p className="text-[11px] text-neutral-500 font-mono">Pattern: {ev.data.matched_pattern}</p>
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
        {(ev.data?.agent_id || ev.data?.approval_id) && (
          <button
            onClick={() => onInvestigate(ev)}
            className="text-[10px] text-neutral-400 hover:text-white flex items-center gap-1 transition-all border border-[var(--border-subtle)] hover:border-white/30 rounded px-2 py-0.5"
            aria-label={isEscalate ? "Open approval inbox" : "Investigate in Forensics"}
          >
            {isEscalate ? 'Review' : 'Investigate'} <ChevronRight size={10} />
          </button>
        )}
      </div>
    </div>
  )
}

// ─── KEYBOARD-ACCESSIBLE FILTER CHIP ─────────────────────────────────────────
// Centralised so the type / employee / model chip rows all share the same
// focus management + Enter/Space activation contract.
function FilterChip({ active, onClick, color, children, label }) {
  const handleKey = (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      onClick()
    }
  }
  return (
    <button
      type="button"
      role="button"
      tabIndex={0}
      aria-pressed={active}
      aria-label={label}
      onClick={onClick}
      onKeyDown={handleKey}
      className={`text-[10px] px-2.5 py-1 rounded-lg border transition-all focus:outline-none focus:ring-2 focus:ring-cyan-500/40 ${
        active
          ? `border-white/20 bg-white/[0.05] ${color || 'text-white'}`
          : 'border-[var(--border-subtle)] text-neutral-600 hover:text-white'
      }`}
    >
      {children}
    </button>
  )
}

export default function LiveFeed() {
  const navigate = useNavigate()
  const { selectedAgentId, selectedAgent } = useContext(AgentContext)
  const { addToast } = useContext(AuthContext)
  const [events, setEvents] = useState([])
  const [paused, setPaused] = useState(false)
  // Filter model widened from a flat Set<type> to a tri-axis selector so the
  // UI can chain {type ∧ employee ∧ model}. Each Set is independent — an
  // empty Set means "no filter on this axis".
  const [filterTypes,     setFilterTypes]     = useState(new Set())
  const [filterEmployees, setFilterEmployees] = useState(new Set())
  const [filterModels,    setFilterModels]    = useState(new Set())
  // Scope intersects with filterTypes — picking a scope narrows which event
  // types are reachable; per-type chips still toggle within that scope.
  const [scope, setScope] = useState('all')
  const [backfillLoading, setBackfillLoading] = useState(true)
  const pausedRef     = useRef(false)
  const addToastRef   = useRef(addToast)
  const navigateRef   = useRef(navigate)
  // Stable signature set used to skip SSE/backfill duplicates without
  // dropping legitimately-distinct rows.
  const seenSigRef    = useRef(new Set())

  pausedRef.current   = paused
  addToastRef.current = addToast
  navigateRef.current = navigate

  // Build a dedup signature: prefer request_id when present; otherwise a
  // tuple of fields that uniquely identify the action. Lower ts resolution
  // to whole seconds so a backfill row at ms=001 and an SSE delta at ms=017
  // for the same logical event collapse.
  const sigFor = useCallback((entry) => {
    const rid = entry?.data?.request_id
    if (rid) return `rid:${rid}`
    const tsSec  = Math.floor((entry.ts || 0) / 1000)
    const cost   = entry?.data?.cost_usd ?? ''
    const patt   = entry?.data?.matched_pattern ?? ''
    return `tup:${tsSec}:${entry.type}:${cost}:${patt}`
  }, [])

  // Backfill the last N decisions on mount + when the agent scope changes.
  // Without this, the feed sits empty until a new event is published — users
  // wait minutes thinking the stream is broken when really nothing has
  // happened yet. We pull from /audit/logs which is the same source of
  // truth tool_executed events are derived from.
  useEffect(() => {
    let cancelled = false
    const load = async () => {
      setBackfillLoading(true)
      // Reset the seen-signature set when scope changes — a row that was a
      // duplicate under the previous scope's events is fair game now.
      seenSigRef.current = new Set()
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
        // Dedup within the backfill itself (the API can return the same
        // request_id twice if the audit row was rewritten).
        const dedup = []
        for (const entry of seeded) {
          const sig = sigFor(entry)
          if (seenSigRef.current.has(sig)) continue
          seenSigRef.current.add(sig)
          dedup.push(entry)
        }
        setEvents(dedup)
      } catch (_e) {
        // backfill is best-effort; SSE will still populate live events
      } finally {
        if (!cancelled) setBackfillLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [selectedAgentId, sigFor])

  const fireEscalationToast = useCallback((entry) => {
    const employee   = entry.data?.employee_email || 'unknown user'
    const pattern    = entry.data?.matched_pattern || entry.data?.reason || 'policy match'
    const approver   = entry.data?.approver_role || 'approver'
    const approvalId = entry.data?.approval_id || entry.data?.request_id
    const label      = entry.type === 'llm_proxy_escalate' ? 'LLM ESCALATE' : 'APPROVAL'
    addToastRef.current?.(
      `${label}: "${pattern}" from ${employee} → ${approver}`,
      'warning',
      {
        ttl: 10_000,
        action: {
          label: 'Review',
          onClick: () => {
            navigateRef.current(
              approvalId ? `/approval-inbox?id=${approvalId}` : '/approval-inbox',
            )
          },
        },
      },
    )
  }, [])

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
      decision:   inner?.decision   ?? inner?.action,
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
    // SSE / backfill dedup — drop if we've already seen this signature.
    const sig = sigFor(entry)
    if (seenSigRef.current.has(sig)) return
    seenSigRef.current.add(sig)

    // Fire escalation toast for approval-bearing events. We do this here
    // (not in EventRow) so the toast still surfaces even if the user has
    // filtered the event type out of view.
    if (entry.type === 'llm_proxy_escalate' || entry.type === 'approval_required') {
      fireEscalationToast(entry)
    }

    setEvents((prev) => {
      const next = [entry, ...prev].slice(0, MAX_EVENTS)
      // clear fresh flag after 2s without setState loop
      setTimeout(() => {
        setEvents((p) => p.map((e) => e.id === entry.id ? { ...e, fresh: false } : e))
      }, 2000)
      return next
    })
  }, [sigFor, fireEscalationToast])

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

  // ─── Top-5 employees / models, memoised over the events array. ───────────
  // Counted once per render in a single O(n) pass rather than three.
  const { topEmployees, topModels, typeCounts } = useMemo(() => {
    const empC   = new Map()
    const modC   = new Map()
    const typC   = {}
    for (const e of events) {
      typC[e.type] = (typC[e.type] || 0) + 1
      const emp = e.data?.employee_email
      if (emp) empC.set(emp, (empC.get(emp) || 0) + 1)
      const mod = e.data?.model
      if (mod) modC.set(mod, (modC.get(mod) || 0) + 1)
    }
    const sort5 = (m) => [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, 5)
    return {
      topEmployees: sort5(empC),
      topModels:    sort5(modC),
      typeCounts:   typC,
    }
  }, [events])

  const scopeTypes = SCOPES[scope]?.types || ALL_TYPES
  const scopeSet   = useMemo(() => new Set(scopeTypes), [scopeTypes])

  // Sprint 21 — surface "Last event Ns ago" in the header so the operator can
  // tell whether the SSE stream is actually delivering events or just sitting
  // idle. Newest first, so events[0] is the freshest sample.
  const lastFetchAt = useMemo(() => {
    if (events.length === 0) return null
    return new Date(events[0].ts).toISOString()
  }, [events])

  const visible = useMemo(() => {
    return events.filter((e) => {
      if (!scopeSet.has(e.type)) return false
      if (filterTypes.size > 0 && !filterTypes.has(e.type)) return false
      if (filterEmployees.size > 0 && !filterEmployees.has(e.data?.employee_email)) return false
      if (filterModels.size > 0 && !filterModels.has(e.data?.model)) return false
      return true
    })
  }, [events, scopeSet, filterTypes, filterEmployees, filterModels])

  const toggleSet = (setter) => (value) => {
    setter((prev) => {
      const next = new Set(prev)
      if (next.has(value)) next.delete(value)
      else next.add(value)
      return next
    })
  }
  const toggleFilter    = toggleSet(setFilterTypes)
  const toggleEmployee  = toggleSet(setFilterEmployees)
  const toggleModel     = toggleSet(setFilterModels)

  const investigate = (ev) => {
    // Approval-shaped escalations → operator's Approval Inbox; tool-call
    // events → Forensics with the agent prefilter; otherwise general
    // Forensics landing.
    if (ev.type === 'llm_proxy_escalate' && ev.data?.approval_id) {
      navigate(`/approval-inbox?id=${encodeURIComponent(ev.data.approval_id)}`)
      return
    }
    const agentId = ev.data?.agent_id
    if (agentId) navigate(`/forensics?agent=${agentId}`)
    else navigate('/forensics')
  }

  // ─── EXPORT AS JSON ──────────────────────────────────────────────────────
  // Pure client-side blob download of the currently-filtered feed. Useful
  // for ad-hoc SOC investigations where someone wants to email a snapshot
  // around — no backend round-trip needed.
  const exportJson = useCallback(() => {
    const payload = {
      exported_at:      new Date().toISOString(),
      scope:            selectedAgentId || 'tenant_wide',
      filter_types:     [...filterTypes],
      filter_employees: [...filterEmployees],
      filter_models:    [...filterModels],
      event_count:      visible.length,
      events:           visible,
    }
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
    const url  = URL.createObjectURL(blob)
    const a    = document.createElement('a')
    a.href     = url
    a.download = `aegis-feed-${new Date().toISOString().replace(/[:.]/g, '-')}.json`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }, [visible, selectedAgentId, filterTypes, filterEmployees, filterModels])

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <header className="flex items-start justify-between gap-4">
        <div className="flex items-center gap-3 min-w-0">
          <div className="min-w-0">
            <h1 className="text-2xl font-semibold text-white mb-1 flex items-center gap-2">
              <Radio size={20} className="text-neutral-500" aria-hidden="true" />
              Live Event Feed
            </h1>
            <p className="text-sm text-neutral-400">Real-time security events from the gateway SSE stream.</p>
            <div className="flex items-center gap-2 mt-1.5 flex-wrap">
              <DataFreshness updatedAt={lastFetchAt} prefix="Last event" />
              {selectedAgent ? (
                <span className="inline-flex items-center gap-1.5 text-[10px] px-2 py-0.5 rounded-full bg-white/[0.05] border border-white/10 text-neutral-400">
                  <Filter size={9} /> Scope: {selectedAgent.name || selectedAgentId.slice(0, 8)}
                </span>
              ) : (
                <span className="inline-flex items-center gap-1.5 text-[10px] px-2 py-0.5 rounded-full bg-white/[0.04] border border-white/[0.06] text-neutral-500">
                  <Filter size={9} /> Scope: All agents (workspace-wide)
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
        <div className="flex items-center gap-3 flex-wrap justify-end">
          <ThroughputGauge events={events} />
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
            onClick={exportJson}
            disabled={visible.length === 0}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[var(--border-subtle)] text-xs text-neutral-400 hover:text-white disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            aria-label="Export filtered feed as JSON"
            title={visible.length === 0 ? 'No events to export' : `Export ${visible.length} events as JSON`}
          >
            <Download size={12} /> Export
          </button>
          <button
            onClick={() => { setEvents([]); seenSigRef.current = new Set(); }}
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

      {/* Scope — coarse perspective. Replaces the dedicated Observability
          + SecurityDashboard pages, which showed these views with different framing. */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[10px] uppercase tracking-widest text-neutral-600 mr-1">Scope:</span>
        {Object.entries(SCOPES).map(([id, def]) => {
          const active = scope === id
          return (
            <button
              key={id}
              onClick={() => {
                if (active) return
                setScope(id)
                // Drop per-type chips that fall outside the new scope so the
                // filter strip doesn't lie about what's visible.
                setFilterTypes((prev) => {
                  if (prev.size === 0) return prev
                  const allowed = new Set(def.types)
                  const next = new Set([...prev].filter((t) => allowed.has(t)))
                  return next.size === prev.size ? prev : next
                })
              }}
              className={`text-[10px] px-2.5 py-1 rounded-lg border transition-all ${active ? 'border-white/20 bg-white/[0.07] text-white' : 'border-[var(--border-subtle)] text-neutral-500 hover:text-white'}`}
            >
              {def.label}
            </button>
          )
        })}
      </div>

      {/* Filters — by type (intersected with scope), employee, model */}
      <div className="space-y-2">
        <div className="flex items-center gap-2 flex-wrap">
          <Filter size={12} className="text-neutral-600" aria-hidden="true" />
          <span className="text-[10px] uppercase tracking-wider text-neutral-600 mr-1">Type</span>
          <FilterChip
            active={filterTypes.size === 0}
            onClick={() => setFilterTypes(new Set())}
            label="Show all event types"
          >
            All types
          </FilterChip>
          {scopeTypes.map((t) => {
            const meta = EVENT_META[t]
            const active = filterTypes.has(t)
            return (
              <FilterChip
                key={t}
                active={active}
                onClick={() => toggleFilter(t)}
                color={meta.color}
                label={`Toggle ${meta.label} filter`}
              >
                {meta.label}
                {typeCounts[t] > 0 && <span className="ml-1 opacity-70">({typeCounts[t]})</span>}
              </FilterChip>
            )
          })}
        </div>

        {/* Filters — by employee (top-5 observed) */}
        {topEmployees.length > 0 && (
          <div className="flex items-center gap-2 flex-wrap">
            <User size={12} className="text-neutral-600" aria-hidden="true" />
            <span className="text-[10px] uppercase tracking-wider text-neutral-600 mr-1">Employee</span>
            {topEmployees.map(([emp, count]) => {
              const active = filterEmployees.has(emp)
              return (
                <FilterChip
                  key={emp}
                  active={active}
                  onClick={() => toggleEmployee(emp)}
                  color="text-cyan-300"
                  label={`Toggle filter for employee ${emp}`}
                >
                  {emp} <span className="opacity-70">({count})</span>
                </FilterChip>
              )
            })}
            {filterEmployees.size > 0 && (
              <button
                onClick={() => setFilterEmployees(new Set())}
                className="text-[10px] text-neutral-500 hover:text-white underline-offset-2 hover:underline"
              >
                clear
              </button>
            )}
          </div>
        )}

        {/* Filters — by model (top-5 observed) */}
        {topModels.length > 0 && (
          <div className="flex items-center gap-2 flex-wrap">
            <Cpu size={12} className="text-neutral-600" aria-hidden="true" />
            <span className="text-[10px] uppercase tracking-wider text-neutral-600 mr-1">Model</span>
            {topModels.map(([mod, count]) => {
              const active = filterModels.has(mod)
              return (
                <FilterChip
                  key={mod}
                  active={active}
                  onClick={() => toggleModel(mod)}
                  color="text-purple-300"
                  label={`Toggle filter for model ${mod}`}
                >
                  {mod} <span className="opacity-70">({count})</span>
                </FilterChip>
              )
            })}
            {filterModels.size > 0 && (
              <button
                onClick={() => setFilterModels(new Set())}
                className="text-[10px] text-neutral-500 hover:text-white underline-offset-2 hover:underline"
              >
                clear
              </button>
            )}
          </div>
        )}
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
                ? (filterTypes.size + filterEmployees.size + filterModels.size) > 0
                  ? 'No events match the selected filters.'
                  : 'Waiting for events…'
                : 'Connect to start receiving events.'}
            </p>
          </div>
        ) : (
          <div aria-live="polite" aria-relevant="additions" aria-busy={backfillLoading || undefined}>
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
