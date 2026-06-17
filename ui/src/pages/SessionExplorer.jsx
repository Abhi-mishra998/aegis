// Sprint 3.5 — Session Explorer
//
// Lists active sessions for the tenant in the last window and shows a
// risk-trajectory sparkline per session. Clicking a row drills into the
// session — every timeline in order with a click-through to the
// Decision Explorer.
//
// Data sources (Sprint 3.3):
//   GET /flight/sessions             — list (tenant-scoped, last N minutes)
//   GET /flight/sessions/{id}         — drill-down

import React, { useEffect, useMemo, useState, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { LineChart, Line, ResponsiveContainer, YAxis, Tooltip } from 'recharts'
import { flightService } from '../services/api'

// Recharts wants an array of { x, y } objects.
function trajectoryData(risks = []) {
  return risks.map((r, i) => ({ x: i + 1, y: r }))
}

// Risk colour scaling — rising risk across a session is a governance signal
// the audit (C19) called out as missing from the older Trace UI.
function riskBadge(r) {
  if (r == null) return ['text-neutral-300', 'bg-neutral-800']
  if (r >= 0.8)  return ['text-rose-100',    'bg-rose-900']
  if (r >= 0.5)  return ['text-amber-100',   'bg-amber-900']
  if (r >= 0.2)  return ['text-yellow-100',  'bg-yellow-900']
  return ['text-emerald-100', 'bg-emerald-900']
}

function fmtTime(ts) {
  if (!ts) return '—'
  const d = new Date(ts)
  return d.toLocaleString(undefined, {
    month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

function SessionRow({ s, isActive, onSelect }) {
  const [fg, bg] = riskBadge(s.max_risk)
  return (
    <button
      onClick={() => onSelect(s.session_id)}
      className={`text-left w-full grid grid-cols-12 gap-2 items-center px-3 py-2 border-b border-neutral-800 hover:bg-neutral-900 ${isActive ? 'bg-neutral-900' : ''}`}
    >
      <div className="col-span-3 truncate">
        <div className="text-sm font-mono text-neutral-200 truncate" title={s.session_id}>
          {s.session_id}
        </div>
        <div className="text-[10px] text-neutral-400">last seen {fmtTime(s.last_seen_at)}</div>
      </div>
      <div className="col-span-2 text-xs text-neutral-300">
        {s.decision_count} decisions
        <div className="text-[10px] text-neutral-500">
          {s.distinct_agents} agent{s.distinct_agents === 1 ? '' : 's'}
          {' · '}
          {s.distinct_tools} tool{s.distinct_tools === 1 ? '' : 's'}
        </div>
      </div>
      <div className="col-span-2">
        <span className={`text-[11px] px-2 py-0.5 rounded ${fg} ${bg}`}>
          max risk {s.max_risk != null ? s.max_risk.toFixed(2) : '—'}
        </span>
      </div>
      <div className="col-span-5 h-10">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={trajectoryData(s.risk_trajectory)}>
            <YAxis hide domain={[0, 1]} />
            <Tooltip contentStyle={{ background: '#171717', border: '1px solid #404040', fontSize: 10 }} />
            <Line type="monotone" dataKey="y" stroke="#f59e0b" strokeWidth={2} dot={false} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </button>
  )
}

function SessionDetail({ detail }) {
  if (!detail) {
    return (
      <div className="p-4 text-sm text-neutral-400">
        Select a session on the left to see the decision trajectory.
      </div>
    )
  }
  // Defensive: the backend can legitimately return a session row with no
  // timelines materialised yet (the consumer worker hasn't caught up).
  // Without the fallback, `detail.timelines.length` and `.map()` both
  // TypeError the whole page off-screen.
  const timelines = Array.isArray(detail.timelines) ? detail.timelines : []
  return (
    <div className="p-4">
      <h2 className="text-sm font-semibold text-neutral-200 mb-3">
        {detail.session_id}
        <span className="ml-2 text-xs text-neutral-500">
          {timelines.length} decision{timelines.length === 1 ? '' : 's'}
        </span>
      </h2>

      <div className="h-32 mb-4 rounded-md bg-neutral-950 border border-neutral-800 p-2">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={trajectoryData(detail.risk_trajectory)}>
            <YAxis domain={[0, 1]} fontSize={10} stroke="#737373" />
            <Tooltip contentStyle={{ background: '#171717', border: '1px solid #404040', fontSize: 10 }} />
            <Line type="monotone" dataKey="y" stroke="#f59e0b" strokeWidth={2} dot={{ r: 3 }} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>

      <ol className="space-y-1">
        {timelines.map((t, idx) => {
          const [fg, bg] = riskBadge(t.final_risk)
          return (
            <li key={t.id} className="flex items-center gap-2 px-2 py-1 border-b border-neutral-800">
              <span className="text-[10px] font-mono text-neutral-500 w-8">{idx + 1}</span>
              <span className="text-xs flex-1 truncate font-mono" title={t.request_id}>
                {t.request_id}
              </span>
              <span className="text-[10px] text-neutral-400">{t.tool || 'no tool'}</span>
              <span className={`text-[10px] px-2 py-0.5 rounded ${fg} ${bg}`}>
                {t.final_decision || t.status} · risk {t.final_risk != null ? t.final_risk.toFixed(2) : '—'}
              </span>
              <Link
                to={`/decision-explorer?request_id=${encodeURIComponent(t.request_id)}`}
                className="text-[10px] text-emerald-400 hover:underline"
              >
                graph ↗
              </Link>
            </li>
          )
        })}
      </ol>
    </div>
  )
}

export default function SessionExplorer() {
  const [sessions, setSessions] = useState([])
  const [activeSessionId, setActiveSessionId] = useState(null)
  const [detail, setDetail] = useState(null)
  const [minutes, setMinutes] = useState(1440)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const refresh = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const resp = await flightService.listSessions({ minutes, limit: 100 })
      const payload = resp?.data ?? resp
      setSessions(payload || [])
    } catch (e) {
      setError(e?.message || 'Failed to load sessions')
    } finally {
      setLoading(false)
    }
  }, [minutes])

  useEffect(() => { refresh() }, [refresh])

  const onSelect = async (sid) => {
    setActiveSessionId(sid)
    try {
      const resp = await flightService.getSession(sid)
      setDetail(resp?.data ?? resp)
    } catch (e) {
      setError(e?.message || 'Failed to load session detail')
    }
  }

  const empty = !loading && sessions.length === 0

  return (
    <div className="text-neutral-100">
      <header className="flex items-center justify-between px-6 py-4 border-b border-neutral-800">
        <div>
          <h1 className="text-xl font-semibold">Session Explorer</h1>
          <p className="text-sm text-neutral-400 mt-1">
            Conversations grouped by <code>X-Session-ID</code>. Watch the risk
            trajectory rise across multi-turn agent loops.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <select
            value={minutes}
            onChange={(e) => setMinutes(Number(e.target.value))}
            className="px-2 py-1 bg-neutral-900 border border-neutral-700 rounded-md text-sm"
          >
            <option value={60}>Last 1 hour</option>
            <option value={360}>Last 6 hours</option>
            <option value={1440}>Last 24 hours</option>
            <option value={10080}>Last 7 days</option>
          </select>
          <button
            onClick={refresh}
            className="px-3 py-1 bg-neutral-800 hover:bg-neutral-700 rounded-md text-sm"
          >
            Refresh
          </button>
        </div>
      </header>

      {error && (
        <div className="mx-6 my-3 text-sm bg-rose-950 border border-rose-700 text-rose-100 px-3 py-2 rounded">
          {error}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 min-h-[70vh]">
        <div className="border-r border-neutral-800">
          <div className="grid grid-cols-12 px-3 py-2 text-[10px] uppercase text-neutral-500 tracking-wide bg-neutral-950">
            <span className="col-span-3">Session</span>
            <span className="col-span-2">Activity</span>
            <span className="col-span-2">Max risk</span>
            <span className="col-span-5">Trajectory</span>
          </div>
          {empty && (
            <div className="p-6 text-sm text-neutral-400 space-y-3">
              <div>
                No sessions in the selected window. Sessions appear once a
                client emits an <code>X-Session-ID</code> header on{' '}
                <code>/execute</code>.
              </div>
              <div className="text-xs text-neutral-500">
                Want to see this populate live?{' '}
                <a href="/onboarding" className="text-indigo-400 hover:text-indigo-300 underline">
                  Open onboarding
                </a>{' '}
                — the guided flow walks you through registering an agent and
                firing a session-tagged <code>/execute</code> call you'll see
                land here within seconds.
              </div>
            </div>
          )}
          {sessions.map((s) => (
            <SessionRow
              key={s.session_id}
              s={s}
              isActive={s.session_id === activeSessionId}
              onSelect={onSelect}
            />
          ))}
        </div>
        <SessionDetail detail={detail} />
      </div>
    </div>
  )
}
