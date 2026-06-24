// Approval Inbox — `agies-refractor.md §6 #4` + §8 Days 70-90.
//
// Operator surface for ESCALATEd /execute decisions. Backend already
// emits decision="escalate" with a structured `approval_required`
// payload (services/gateway/middleware.py:1148-1167); this page
// queues them, lets the on-call decide, and records the decision in
// human_override_events via POST /autonomy/overrides.

import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Inbox, CheckCircle2, XCircle, Clock, AlertTriangle, RefreshCw, User, PlayCircle, Zap,
} from 'lucide-react'
import { auditService, autonomyService, playgroundService } from '../services/api'
import ErrorBoundary from '../components/Common/ErrorBoundary'
import SkeletonLoader from '../components/Common/SkeletonLoader'
import { useSSE } from '../hooks/useSSE'
import { eventBus } from '../lib/eventBus'

function unwrap(resp) { return resp?.data ?? resp }
function fmtTs(s) { if (!s) return '—'; try { return new Date(s).toLocaleString() } catch { return s } }

const WINDOWS = [
  { label: 'Last 1h',  minutes: 60 },
  { label: 'Last 24h', minutes: 1440 },
  { label: 'Last 7d',  minutes: 10080 },
  { label: 'Last 30d', minutes: 43200 },
]

function severityBadge(risk) {
  const r = typeof risk === 'number' ? risk : 0
  if (r >= 0.85) return { label: 'CRITICAL', cls: 'bg-rose-950 text-rose-200 border-rose-800' }
  if (r >= 0.6)  return { label: 'HIGH',     cls: 'bg-amber-950 text-amber-200 border-amber-800' }
  if (r >= 0.3)  return { label: 'MEDIUM',   cls: 'bg-sky-950 text-sky-200 border-sky-800' }
  return { label: 'LOW',  cls: 'bg-neutral-800 text-neutral-300 border-neutral-700' }
}

function ApprovalInboxPage() {
  const [windowMinutes, setWindowMinutes] = useState(1440)
  const [escalations, setEscalations] = useState([])
  const [overrides, setOverrides] = useState([])
  const [selected, setSelected]   = useState(null)
  const [reason, setReason]       = useState('')
  const [busy, setBusy]           = useState('')
  const [error, setError]         = useState('')
  const [msg, setMsg]             = useState('')
  const [loading, setLoading]     = useState(true)
  // Don't reveal the empty-state CTA until at least one fetch resolves —
  // we never want to flash "no pending approvals" before the API replies.
  const [hasLoaded, setHasLoaded] = useState(false)
  const [triggering, setTriggering] = useState(false)

  const fetchAll = useCallback(async () => {
    setLoading(true); setError('')
    try {
      // 1. Pull escalated audit rows (the queue of pending approvals).
      const esc = await auditService.searchLogs({
        decision: 'escalate',
        limit: 200,
      })
      const data = unwrap(esc)
      const escItems = Array.isArray(data) ? data : (data?.items || [])
      setEscalations(escItems)

      // 2. Pull human override events so we know which escalations have
      // already been actioned. We index by request_id; an event with
      // event_type=approval OR event_type=override on a given request_id
      // means the queue should hide it.
      const ovrs = unwrap(await autonomyService.listOverrides({
        minutes: windowMinutes, limit: 500,
      })) || []
      setOverrides(Array.isArray(ovrs) ? ovrs : [])
    } catch (e) {
      setError(e?.message || 'Failed to load approvals')
    } finally {
      setLoading(false)
      setHasLoaded(true)
    }
  }, [windowMinutes])

  useEffect(() => { fetchAll() }, [fetchAll])

  // Sprint 20 UX pass — the Approval Inbox is the founder's "Pending
  // CFO approval" surface. If a new escalation lands and the inbox
  // doesn't auto-refresh, the operator wouldn't notice until they
  // manually hit Refresh. Poll every 8s while the page is mounted —
  // cheap (single GET + one GET on the overrides table).
  useEffect(() => {
    const id = setInterval(() => { fetchAll() }, 8_000)
    return () => clearInterval(id)
  }, [fetchAll])

  // Real-time SSE wiring — react to approval_required / approval_resolved
  // server events so a new pending approval appears the instant the
  // gateway emits one (instead of waiting up to 8s for the next poll).
  // Also listens via the eventBus in case AgentContext re-emits these
  // as `policy_decision`.
  const sseChannels = useMemo(() => ({
    approval_required: () => fetchAll(),
    approval_resolved: () => fetchAll(),
    incident_updated:  () => fetchAll(),
  }), [fetchAll])
  useSSE({
    channels: sseChannels,
    onMessage: (evt) => {
      const t = String(evt?.type || '').toLowerCase()
      if (t.includes('approval') || t.includes('escalate') || t.includes('override')) {
        fetchAll()
      }
    },
  })
  useEffect(() => {
    const u1 = eventBus.on('policy_decision', fetchAll)
    const u2 = eventBus.on('alert',           fetchAll)
    return () => { u1(); u2() }
  }, [fetchAll])

  // Operator-facing demo affordance — fires a synthetic high-risk
  // /execute call that the gateway will return ESCALATE on, which
  // materialises an audit row with decision="escalate". The inbox
  // re-polls after a short delay so the new row appears in the
  // pending list without an explicit refresh.
  const triggerSampleEscalate = useCallback(async () => {
    setTriggering(true)
    setError('')
    setMsg('')
    try {
      await playgroundService.execute(
        'inbox-demo-agent',
        'demo.escalate',
        {
          // A wire-transfer above the policy soft cap deterministically
          // triggers ESCALATE in the canonical policy. The agent id
          // above is a synthetic name so it doesn't pollute the agent
          // registry view.
          amount_usd: 250000,
          recipient_kind: 'external',
          reason: 'approval-inbox sample trigger',
        },
      ).catch((err) => {
        // ESCALATE comes back as HTTP 403 with body {error:"approval_required"}.
        // That is the success path here — we want the row, not the data.
        const msg = err?.message || ''
        if (!/approval|escalate|403/i.test(msg)) throw err
      })
      setMsg('Sample ESCALATE submitted — a new pending approval should land within a few seconds.')
      // Two staggered refreshes — the first picks up the audit row,
      // the second handles any out-of-order override write.
      setTimeout(fetchAll, 600)
      setTimeout(fetchAll, 2000)
    } catch (e) {
      setError(e?.message || 'Sample trigger failed')
    } finally {
      setTriggering(false)
    }
  }, [fetchAll])

  const resolvedRequestIds = useMemo(() => {
    const set = new Set()
    for (const o of overrides) {
      if (!o.request_id) continue
      if (o.event_type === 'approval' || o.event_type === 'override') {
        set.add(o.request_id)
      }
    }
    return set
  }, [overrides])

  const pending = useMemo(() => {
    return (escalations || [])
      .filter((r) => r.request_id && !resolvedRequestIds.has(r.request_id))
      .sort((a, b) => new Date(b.timestamp || 0) - new Date(a.timestamp || 0))
  }, [escalations, resolvedRequestIds])

  const resolved = useMemo(() => {
    return (escalations || [])
      .filter((r) => r.request_id && resolvedRequestIds.has(r.request_id))
      .sort((a, b) => new Date(b.timestamp || 0) - new Date(a.timestamp || 0))
      .slice(0, 25)
  }, [escalations, resolvedRequestIds])

  const decide = async (decision /* 'approval' | 'override' */) => {
    if (!selected) return
    setBusy(decision); setMsg(''); setError('')
    try {
      // The `actor` / `actor_role` body fields below are placeholders required
      // by the OverrideIn schema. The autonomy router prefers the gateway-
      // injected `X-ACP-Actor` / `X-ACP-Role` headers (sourced from the
      // validated JWT's `sub` / `role`). A browser cannot impersonate another
      // operator — the body values are ignored whenever the request flows
      // through the gateway.
      await autonomyService.addOverride({
        actor:        'ui-approval-inbox',
        actor_role:   'human-in-the-loop',
        event_type:   decision,
        target_kind:  'request',
        target_id:    selected.request_id,
        request_id:   selected.request_id,
        reason:       reason.trim() || (decision === 'approval' ? 'Operator approved' : 'Operator rejected'),
        metadata: {
          audit_id: selected.id,
          tool:     selected.tool,
          agent_id: selected.agent_id,
          via:      'approval-inbox-ui',
        },
      })
      setMsg(`Request ${selected.request_id} recorded as ${decision === 'approval' ? 'APPROVED' : 'REJECTED'}.`)
      setReason('')
      setSelected(null)
      await fetchAll()
    } catch (e) {
      setError(e?.message || 'Failed to record decision')
    } finally {
      setBusy('')
    }
  }

  return (
    <div className="text-neutral-100">
      <header className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 px-6 py-4 border-b border-neutral-800">
        <div>
          <h1 className="text-xl font-semibold inline-flex items-center gap-2">
            <Inbox size={18} /> Approval Inbox
          </h1>
          <p className="text-xs text-neutral-400 mt-1">
            Pending escalations awaiting human approval
          </p>
          <p className="text-sm text-neutral-400 mt-1">
            Decisions the pipeline ESCALATEd — autonomy contracts said a
            human must approve before this action runs. Approve or reject
            once; the choice lands in <code>human_override_events</code>
            with the same ed25519 chain as every other audit row.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select name="select"
            value={windowMinutes}
            onChange={(e) => setWindowMinutes(Number(e.target.value))}
            className="px-2 py-1 bg-neutral-900 border border-neutral-700 rounded-md text-sm"
          >
            {WINDOWS.map((w) => (
              <option key={w.minutes} value={w.minutes}>{w.label}</option>
            ))}
          </select>
          <button
            onClick={fetchAll}
            disabled={loading}
            className="px-3 py-1.5 bg-neutral-800 hover:bg-neutral-700 rounded-md text-sm inline-flex items-center gap-2"
          >
            <RefreshCw size={14} /> {loading ? 'Loading…' : 'Refresh'}
          </button>
        </div>
      </header>

      {error && (
        <div className="mx-6 my-3 text-sm bg-rose-950 border border-rose-700 text-rose-100 px-3 py-2 rounded">
          {error}
        </div>
      )}
      {msg && (
        <div className="mx-6 my-3 text-sm bg-emerald-950 border border-emerald-700 text-emerald-100 px-3 py-2 rounded">
          {msg}
        </div>
      )}

      <div className="grid grid-cols-12 gap-3 px-6 py-4">
        <aside className="col-span-12 lg:col-span-5 rounded-lg border border-neutral-800 bg-neutral-950 overflow-hidden">
          <div className="px-3 py-2 border-b border-neutral-800 text-xs text-neutral-400 inline-flex items-center gap-2">
            <Clock size={12} /> Pending ({pending.length})
          </div>
          <div className="divide-y divide-neutral-900 max-h-[60vh] overflow-y-auto">
            {!hasLoaded ? (
              <div className="p-4">
                <SkeletonLoader variant="row" count={4} />
              </div>
            ) : pending.length === 0 ? (
              <div className="p-6 text-center space-y-3">
                <CheckCircle2 size={22} className="text-green-400 mx-auto" aria-hidden="true" />
                <div>
                  <p className="text-sm text-neutral-200 font-medium">No pending approvals</p>
                  <p className="text-xs text-neutral-500 mt-1 leading-relaxed">
                    When an agent triggers ESCALATE it appears here. Decisions
                    land in <code>human_override_events</code> with the same
                    ed25519 chain as the rest of the audit log.
                  </p>
                </div>
                <button
                  onClick={triggerSampleEscalate}
                  disabled={triggering}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium text-purple-300 bg-purple-500/[0.08] border border-purple-500/20 hover:border-purple-500/40 disabled:opacity-50 transition-colors"
                >
                  <Zap size={11} aria-hidden="true" />
                  {triggering ? 'Triggering sample…' : 'Trigger sample ESCALATE'}
                </button>
              </div>
            ) : null}
            {pending.map((row) => {
              const sev = severityBadge(row.metadata_json?.risk_score)
              const isSel = selected?.id === row.id
              return (
                <button
                  key={row.id}
                  onClick={() => setSelected(row)}
                  className={`w-full text-left px-3 py-2 hover:bg-neutral-900 ${isSel ? 'bg-neutral-900' : ''}`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <div className="truncate">
                      <div className="text-sm text-neutral-200 truncate font-mono">{row.tool || '<no-tool>'}</div>
                      <div className="text-[10px] text-neutral-500 truncate">
                        {row.request_id?.slice(0, 12)}… · {fmtTs(row.timestamp)}
                      </div>
                    </div>
                    <span className={`px-2 py-0.5 text-[10px] rounded-md border ${sev.cls}`}>{sev.label}</span>
                  </div>
                </button>
              )
            })}
          </div>

          {resolved.length > 0 && (
            <>
              <div className="px-3 py-2 border-t border-b border-neutral-800 text-xs text-neutral-400 inline-flex items-center gap-2">
                <CheckCircle2 size={12} /> Recently resolved ({resolved.length})
              </div>
              <div className="divide-y divide-neutral-900 max-h-[30vh] overflow-y-auto">
                {resolved.map((row) => (
                  <div key={row.id} className="px-3 py-2 text-xs text-neutral-500">
                    <span className="font-mono">{row.tool}</span> ·{' '}
                    <span className="text-neutral-600">{row.request_id?.slice(0, 12)}…</span>
                  </div>
                ))}
              </div>
            </>
          )}
        </aside>

        <section className="col-span-12 lg:col-span-7 space-y-3">
          {selected ? (
            <>
              <div className="rounded-lg border border-neutral-800 bg-neutral-950 p-4">
                <div className="flex items-center justify-between mb-3 gap-3 flex-wrap">
                  <div>
                    <div className="text-xs text-neutral-400">request_id</div>
                    <div className="font-mono text-sm text-neutral-200">{selected.request_id}</div>
                  </div>
                  <div className="flex items-center gap-3">
                    <Link
                      to={`/replay/${encodeURIComponent(selected.request_id)}`}
                      className="inline-flex items-center gap-1 text-xs text-blue-300 hover:text-white px-2 py-1 rounded-md border border-blue-500/30 bg-blue-500/[0.06] hover:bg-blue-500/[0.12] transition-colors"
                    >
                      <PlayCircle size={12} /> Replay
                    </Link>
                    <div className="text-right">
                      <div className="text-xs text-neutral-400">when</div>
                      <div className="text-sm text-neutral-200">{fmtTs(selected.timestamp)}</div>
                    </div>
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-3 mb-3">
                  <Field label="Agent"     value={selected.agent_id} />
                  <Field label="Tool"      value={selected.tool} mono />
                  <Field label="Decision"  value={selected.decision} mono />
                  <Field label="Action"    value={selected.action} mono />
                  <Field label="Risk"      value={selected.metadata_json?.risk_score ?? '—'} />
                  <Field label="Findings"  value={(selected.metadata_json?.findings || []).join(', ') || '—'} />
                </div>

                {selected.reason && (
                  <div className="mb-3">
                    <div className="text-xs text-neutral-400 mb-1 inline-flex items-center gap-1">
                      <AlertTriangle size={11} /> Why the pipeline escalated
                    </div>
                    <div className="text-sm text-neutral-200 bg-neutral-900 rounded p-2 border border-neutral-800">
                      {selected.reason}
                    </div>
                  </div>
                )}

                {selected.metadata_json && Object.keys(selected.metadata_json).length > 0 && (
                  <details className="mb-3">
                    <summary className="text-xs text-neutral-400 cursor-pointer">Full request metadata</summary>
                    <pre className="mt-2 p-2 bg-neutral-950 border border-neutral-800 rounded text-[11px] overflow-x-auto font-mono">
                      {JSON.stringify(selected.metadata_json, null, 2)}
                    </pre>
                  </details>
                )}

                <div className="mt-3">
                  <label className="text-xs text-neutral-400 inline-flex items-center gap-1">
                    <User size={11} /> Operator note (recorded with the override)
                  </label>
                  <textarea name="text"
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                    placeholder="Approved after CSR confirmed the customer requested this action / Rejected — fits the exfiltration pattern in INC-2026-014."
                    className="mt-1 w-full bg-neutral-900 border border-neutral-700 rounded-md px-2 py-1 text-sm"
                    rows={3}
                  />
                </div>

                <div className="mt-3 flex justify-end gap-2">
                  <button
                    onClick={() => decide('override')}
                    disabled={!!busy}
                    className="px-3 py-1.5 bg-rose-700 hover:bg-rose-600 disabled:bg-neutral-700 rounded-md text-sm inline-flex items-center gap-2"
                  >
                    <XCircle size={14} /> {busy === 'override' ? 'Rejecting…' : 'Reject'}
                  </button>
                  <button
                    onClick={() => decide('approval')}
                    disabled={!!busy}
                    className="px-3 py-1.5 bg-emerald-700 hover:bg-emerald-600 disabled:bg-neutral-700 rounded-md text-sm inline-flex items-center gap-2"
                  >
                    <CheckCircle2 size={14} /> {busy === 'approval' ? 'Approving…' : 'Approve'}
                  </button>
                </div>
              </div>

              <div className="rounded-lg border border-neutral-800 bg-neutral-950 p-3 text-xs text-neutral-400">
                Your decision becomes a <code>human_override_events</code>
                row chained into the audit log. The signed receipt for
                this request keeps its original ESCALATE outcome — the
                override is an APPEND, never a rewrite. Sprint-1 chain
                verification still passes.
              </div>
            </>
          ) : (
            <div className="rounded-lg border border-neutral-800 bg-neutral-950 p-8 text-center text-neutral-500">
              Pick a pending approval on the left.
            </div>
          )}
        </section>
      </div>
    </div>
  )
}

function Field({ label, value, mono = false }) {
  return (
    <div>
      <div className="text-xs text-neutral-400">{label}</div>
      <div className={`text-sm text-neutral-200 ${mono ? 'font-mono' : ''}`}>
        {value === null || value === undefined || value === '' ? '—' : String(value)}
      </div>
    </div>
  )
}

export default function ApprovalInbox() {
  return (
    <ErrorBoundary>
      <ApprovalInboxPage />
    </ErrorBoundary>
  )
}
