// Sprint 4 — Agent Health
//
// Two panels: a ranked Agent Health table on the left, a Recent Denied /
// Errored events table on the right. Click-through to Decision Explorer
// for any recent event.
//
// Data: GET /audit/fleet/agent-health, GET /audit/fleet/recent-events.

import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Plus, HeartPulse } from 'lucide-react'
import { fleetService } from '../services/api'
import { eventBus } from '../lib/eventBus'
import SkeletonLoader from '../components/Common/SkeletonLoader'

const RANK_OPTIONS = [
  { id: 'deny_rate',  label: 'Deny rate' },
  { id: 'error_rate', label: 'Error rate' },
  { id: 'avg_risk',   label: 'Avg risk' },
  { id: 'volume',     label: 'Volume' },
]

const KIND_OPTIONS = [
  { id: 'denied', label: 'Denied' },
  { id: 'errors', label: 'Errors' },
  { id: 'any',    label: 'All' },
]

function unwrap(r) { return r?.data ?? r }

function fmtPct(x) {
  if (x == null) return '—'
  return `${(x * 100).toFixed(1)}%`
}

function fmtTs(s) {
  if (!s) return '—'
  return new Date(s).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

function rateClass(rate) {
  if (rate == null) return 'text-neutral-300'
  if (rate >= 0.20) return 'text-rose-300'
  if (rate >= 0.05) return 'text-amber-300'
  return 'text-emerald-300'
}

function AgentHealthTable({ rows, rankBy, onRankBy }) {
  return (
    <div className="rounded-lg border border-neutral-800 bg-neutral-950">
      <div className="p-3 flex items-center justify-between border-b border-neutral-800">
        <h2 className="text-sm font-semibold text-neutral-200">Agent Health</h2>
        <div className="inline-flex border border-neutral-700 rounded-md overflow-hidden">
          {RANK_OPTIONS.map((o) => (
            <button
              key={o.id}
              onClick={() => onRankBy(o.id)}
              className={`px-2 py-1 text-xs ${rankBy === o.id ? 'bg-neutral-700 text-white' : 'bg-neutral-900 text-neutral-300 hover:bg-neutral-800'}`}
            >
              {o.label}
            </button>
          ))}
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="bg-neutral-950 text-neutral-500 uppercase tracking-wide">
            <tr>
              <th className="px-3 py-2 text-left">Agent</th>
              <th className="px-2 py-2 text-right">Volume</th>
              <th className="px-2 py-2 text-right">Denied</th>
              <th className="px-2 py-2 text-right">Errors</th>
              <th className="px-2 py-2 text-right">Deny%</th>
              <th className="px-2 py-2 text-right">Error%</th>
              <th className="px-2 py-2 text-right">Avg risk</th>
              <th className="px-2 py-2 text-left">Last seen</th>
            </tr>
          </thead>
          <tbody>
            {(rows || []).map((r) => (
              <tr key={r.agent_id} className="border-t border-neutral-800 hover:bg-neutral-900">
                <td className="px-3 py-2 font-mono">
                  <Link to={`/agent-cost?agent_id=${encodeURIComponent(r.agent_id)}`}
                        className="text-emerald-400 hover:underline" title={r.agent_id}>
                    {r.agent_id?.slice(0, 8)}…
                  </Link>
                </td>
                <td className="px-2 py-2 text-right tabular-nums">{r.volume}</td>
                <td className="px-2 py-2 text-right tabular-nums">{r.denied}</td>
                <td className="px-2 py-2 text-right tabular-nums">{r.errors}</td>
                <td className={`px-2 py-2 text-right tabular-nums ${rateClass(r.deny_rate)}`}>{fmtPct(r.deny_rate)}</td>
                <td className={`px-2 py-2 text-right tabular-nums ${rateClass(r.error_rate)}`}>{fmtPct(r.error_rate)}</td>
                <td className="px-2 py-2 text-right tabular-nums">{r.avg_risk?.toFixed(2) ?? '—'}</td>
                <td className="px-3 py-2 text-neutral-400">{fmtTs(r.last_seen)}</td>
              </tr>
            ))}
            {(!rows || rows.length === 0) && (
              <tr>
                <td colSpan={8} className="px-3 py-4 text-center text-neutral-500">
                  No agents have decisions in the window.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function RecentEventsTable({ events, kind, onKind }) {
  return (
    <div className="rounded-lg border border-neutral-800 bg-neutral-950">
      <div className="p-3 flex items-center justify-between border-b border-neutral-800">
        <h2 className="text-sm font-semibold text-neutral-200">Recent Activity</h2>
        <div className="inline-flex border border-neutral-700 rounded-md overflow-hidden">
          {KIND_OPTIONS.map((o) => (
            <button
              key={o.id}
              onClick={() => onKind(o.id)}
              className={`px-2 py-1 text-xs ${kind === o.id ? 'bg-neutral-700 text-white' : 'bg-neutral-900 text-neutral-300 hover:bg-neutral-800'}`}
            >
              {o.label}
            </button>
          ))}
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="bg-neutral-950 text-neutral-500 uppercase tracking-wide">
            <tr>
              <th className="px-3 py-2 text-left">Timestamp</th>
              <th className="px-2 py-2 text-left">Agent</th>
              <th className="px-2 py-2 text-left">Tool</th>
              <th className="px-2 py-2 text-left">Reason</th>
              <th className="px-2 py-2 text-right">Risk</th>
              <th className="px-2 py-2 text-left">Decision</th>
            </tr>
          </thead>
          <tbody>
            {(events || []).map((e) => (
              <tr key={e.audit_id} className="border-t border-neutral-800 hover:bg-neutral-900">
                <td className="px-3 py-2 text-neutral-400">{fmtTs(e.timestamp)}</td>
                <td className="px-2 py-2 font-mono">{e.agent_id?.slice(0, 8) || '—'}</td>
                <td className="px-2 py-2">{e.tool || '—'}</td>
                <td className="px-2 py-2 truncate max-w-[260px]" title={e.reason}>{e.reason || '—'}</td>
                <td className="px-2 py-2 text-right tabular-nums">{e.risk_score?.toFixed(2) ?? '—'}</td>
                <td className="px-2 py-2">
                  {e.request_id ? (
                    <Link to={`/decision-explorer?request_id=${encodeURIComponent(e.request_id)}`}
                          className="text-emerald-400 hover:underline">
                      {e.decision} ↗
                    </Link>
                  ) : (e.decision || '—')}
                </td>
              </tr>
            ))}
            {(!events || events.length === 0) && (
              <tr>
                <td colSpan={6} className="px-3 py-4 text-center text-neutral-500">
                  Nothing in the selected window.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export default function AgentHealth() {
  const [rankBy, setRankBy] = useState('deny_rate')
  const [windowMinutes, setWindow] = useState(60)
  const [kind, setKind] = useState('denied')
  const [agents, setAgents] = useState([])
  const [events, setEvents] = useState([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const fetchAll = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [a, e] = await Promise.all([
        fleetService.agentHealth({ rankBy, windowMinutes, limit: 50 }),
        fleetService.recentEvents({ kind, limit: 50 }),
      ])
      setAgents(unwrap(a) || [])
      setEvents(unwrap(e) || [])
    } catch (err) {
      setError(err?.message || 'Failed to load')
    } finally {
      setLoading(false)
    }
  }, [rankBy, windowMinutes, kind])

  useEffect(() => { fetchAll() }, [fetchAll])

  // SSE — refetch agent-health rankings on registry change so quarantine,
  // reactivation, or new agents land in the table in real time without a
  // manual refresh. AgentContext owns the source EventSource.
  useEffect(() => {
    const off = eventBus.on('agent_changed', () => { fetchAll() })
    return () => { off?.() }
  }, [fetchAll])

  return (
    <div className="text-neutral-100">
      <header className="flex items-center justify-between px-6 py-4 border-b border-neutral-800">
        <div>
          <h1 className="text-xl font-semibold">Agent Health</h1>
          <p className="text-sm text-neutral-400 mt-1">
            Rank every agent by governance signal. Click any agent for the
            burn-down chart; click any recent event for the decision graph.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select name="select"
            value={windowMinutes}
            onChange={(e) => setWindow(Number(e.target.value))}
            className="px-2 py-1 bg-neutral-900 border border-neutral-700 rounded-md text-sm"
          >
            <option value={60}>Last 1h</option>
            <option value={180}>Last 3h</option>
            <option value={1440}>Last 24h</option>
            <option value={10080}>Last 7d</option>
          </select>
          <button
            onClick={fetchAll}
            disabled={loading}
            className="px-3 py-1 bg-neutral-800 hover:bg-neutral-700 rounded-md text-sm"
          >
            {loading ? 'Loading…' : 'Refresh'}
          </button>
        </div>
      </header>

      {error && (
        <div className="mx-6 my-3 text-sm bg-rose-950 border border-rose-700 text-rose-100 px-3 py-2 rounded">
          {error}
        </div>
      )}

      {loading && agents.length === 0 && events.length === 0 ? (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4 p-6">
          <SkeletonLoader variant="card" count={1} />
          <SkeletonLoader variant="card" count={1} />
        </div>
      ) : !loading && agents.length === 0 && events.length === 0 ? (
        <div className="mx-6 my-6 rounded-2xl border border-white/[0.07] bg-white/[0.02] p-8 text-center space-y-3">
          <div className="w-12 h-12 mx-auto rounded-2xl bg-white/[0.04] border border-white/[0.06] flex items-center justify-center">
            <HeartPulse size={22} className="text-neutral-500" aria-hidden="true" />
          </div>
          <p className="text-sm font-semibold text-neutral-200">No agent traffic in window</p>
          <p className="text-xs text-neutral-500 max-w-md mx-auto">
            No agents have produced governance signals in the selected window. Try a longer
            time range, or register an agent in the Onboarding Wizard if your fleet is empty.
          </p>
          <Link
            to="/onboarding"
            className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-white text-black text-xs font-semibold hover:bg-neutral-200 transition-colors"
          >
            <Plus size={13} aria-hidden="true" /> Register your first agent
          </Link>
        </div>
      ) : (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4 p-4 lg:p-6">
          <AgentHealthTable rows={agents} rankBy={rankBy} onRankBy={setRankBy} />
          <RecentEventsTable events={events} kind={kind} onKind={setKind} />
        </div>
      )}
    </div>
  )
}
