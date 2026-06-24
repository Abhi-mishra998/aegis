// Sprint 4 — Fleet Home dashboard
//
// In-product KPI dashboard + time-series for tokens / errors / latency
// / deny-rate. All data is tenant-scoped at the backend via the
// JWT-derived tenant_id. No mocked arrays.
//
// Data sources:
//   GET /audit/fleet/kpis              — KPI card payload
//   GET /audit/fleet/timeseries        — per-metric time-series

import React, { useEffect, useMemo, useRef, useState, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { Activity, ShieldOff, AlertTriangle, Users, Wrench, Clock, Plus, Bot } from 'lucide-react'
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid,
} from 'recharts'
import { fleetService } from '../services/api'
import { eventBus } from '../lib/eventBus'
import SkeletonLoader from '../components/Common/SkeletonLoader'

const WINDOWS = [
  { label: 'Last 1h',  minutes: 60 },
  { label: 'Last 3h',  minutes: 180 },
  { label: 'Last 24h', minutes: 1440 },
  { label: 'Last 7d',  minutes: 10080 },
]

const METRICS = [
  { id: 'decisions',  label: 'Decisions' },
  { id: 'denied',     label: 'Denied' },
  { id: 'errors',     label: 'Errors' },
  { id: 'latency_ms', label: 'Avg latency (ms)' },
]

function unwrap(resp) { return resp?.data ?? resp }

function fmtPct(x) {
  if (x == null || Number.isNaN(x) || x === 0) return '—'
  return `${(x * 100).toFixed(2)}%`
}

function fmtNum(x) {
  if (x == null || x === 0) return '—'
  if (x >= 1_000_000) return `${(x / 1_000_000).toFixed(1)}M`
  if (x >= 1_000)     return `${(x / 1_000).toFixed(1)}k`
  return String(x)
}

function KpiCard({ icon: Icon, label, value, accent = 'text-neutral-100', sub }) {
  return (
    <div className="rounded-lg border border-neutral-800 bg-neutral-900/60 px-4 py-3 flex flex-col gap-1">
      <div className="flex items-center gap-2 text-xs text-neutral-400">
        <Icon size={14} />
        <span>{label}</span>
      </div>
      <div className={`text-2xl font-semibold tabular-nums ${accent}`}>{value}</div>
      {sub && <div className="text-[10px] text-neutral-500">{sub}</div>}
    </div>
  )
}

export default function Fleet() {
  const [minutes, setMinutes] = useState(180)
  const [metric, setMetric] = useState('decisions')
  const [kpis, setKpis] = useState(null)
  const [series, setSeries] = useState([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  // First load = full skeleton; subsequent SSE/poll refetches swap data silently.
  const hasLoadedRef = useRef(false)

  const fetchAll = useCallback(async () => {
    if (!hasLoadedRef.current) setLoading(true)
    setError('')
    try {
      const [k, s] = await Promise.all([
        fleetService.kpis(minutes),
        fleetService.timeseries({ metric, windowMinutes: minutes, bucketMinutes: minutes >= 1440 ? 60 : 5 }),
      ])
      setKpis(unwrap(k))
      setSeries(unwrap(s) || [])
    } catch (e) {
      setError(e?.message || 'Failed to load fleet data')
    } finally {
      setLoading(false)
      hasLoadedRef.current = true
    }
  }, [minutes, metric])

  useEffect(() => { fetchAll() }, [fetchAll])

  // SSE — repull KPIs + timeseries when the registry mutates so quarantine
  // / decommission / new-agent events reflect in the dashboard live.
  useEffect(() => {
    const off = eventBus.on('agent_changed', () => { fetchAll() })
    return () => { off?.() }
  }, [fetchAll])

  const chartData = useMemo(
    () => (series || []).map((p) => ({ t: new Date(p.t).getTime(), v: p.v })),
    [series],
  )

  return (
    <div className="text-neutral-100">
      <header className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 px-6 py-4 border-b border-neutral-800">
        <div>
          <h1 className="text-xl font-semibold">Fleet</h1>
          <p className="text-sm text-neutral-400 mt-1">
            Live KPIs across every agent in your tenant. The same data the
            audit log carries — no nightly batch, no cross-tenant aggregation.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select name="select"
            value={minutes}
            onChange={(e) => setMinutes(Number(e.target.value))}
            className="px-2 py-1 bg-neutral-900 border border-neutral-700 rounded-md text-sm"
          >
            {WINDOWS.map((w) => (
              <option key={w.minutes} value={w.minutes}>{w.label}</option>
            ))}
          </select>
          <button
            onClick={fetchAll}
            className="px-3 py-1 bg-neutral-800 hover:bg-neutral-700 rounded-md text-sm"
            disabled={loading}
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

      {loading && !kpis ? (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 px-4 lg:px-6 py-4">
          <SkeletonLoader variant="card" count={1} />
          <SkeletonLoader variant="card" count={1} />
          <SkeletonLoader variant="card" count={1} />
          <SkeletonLoader variant="card" count={1} />
          <SkeletonLoader variant="card" count={1} />
          <SkeletonLoader variant="card" count={1} />
        </div>
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 px-4 lg:px-6 py-4">
          <KpiCard icon={Activity}        label="Decisions"
                    value={fmtNum(kpis?.decisions)}
                    accent={(kpis?.decisions ?? 0) === 0 ? 'text-neutral-500' : 'text-neutral-100'}
                    sub={(kpis?.decisions ?? 0) === 0 ? 'No traffic in window' : null} />
          <KpiCard icon={ShieldOff}       label="Deny rate"     value={fmtPct(kpis?.deny_rate)}
                    accent={kpis?.deny_rate > 0.10 ? 'text-rose-200' : ((kpis?.deny_rate ?? 0) === 0 ? 'text-neutral-500' : 'text-emerald-200')}
                    sub={(kpis?.denied ?? 0) === 0 ? null : `${fmtNum(kpis?.denied)} denied`} />
          <KpiCard icon={AlertTriangle}   label="Error rate"    value={fmtPct(kpis?.error_rate)}
                    accent={kpis?.error_rate > 0.05 ? 'text-rose-200' : ((kpis?.error_rate ?? 0) === 0 ? 'text-neutral-500' : 'text-neutral-100')}
                    sub={(kpis?.errors ?? 0) === 0 ? null : `${fmtNum(kpis?.errors)} errors`} />
          <KpiCard icon={Users}           label="Active agents"
                    value={fmtNum(kpis?.active_agents)}
                    accent={(kpis?.active_agents ?? 0) === 0 ? 'text-neutral-500' : 'text-neutral-100'} />
          <KpiCard icon={Wrench}          label="Distinct tools"
                    value={fmtNum(kpis?.distinct_tools)}
                    accent={(kpis?.distinct_tools ?? 0) === 0 ? 'text-neutral-500' : 'text-neutral-100'} />
          <Link to="/agent-health" className="rounded-lg border border-neutral-800 bg-neutral-900/60 hover:bg-neutral-900 px-4 py-3 flex items-center justify-center text-sm text-emerald-400">
            Open Agent Health ↗
          </Link>
        </div>
      )}

      {/* Fleet-empty CTA: no agents AND no traffic — surface the wizard. */}
      {!loading && kpis && (kpis.active_agents ?? 0) === 0 && (kpis.decisions ?? 0) === 0 && (
        <div className="mx-4 lg:mx-6 mb-2 rounded-2xl border border-white/[0.07] bg-white/[0.02] p-6 text-center space-y-3">
          <div className="w-12 h-12 mx-auto rounded-2xl bg-white/[0.04] border border-white/[0.06] flex items-center justify-center">
            <Bot size={22} className="text-neutral-500" aria-hidden="true" />
          </div>
          <p className="text-sm font-semibold text-neutral-200">No agents registered yet</p>
          <p className="text-xs text-neutral-500 max-w-md mx-auto">
            Register your first agent via the Onboarding Wizard. KPIs fill in
            as soon as the agent makes its first <code className="text-neutral-400">/execute</code> call.
          </p>
          <Link
            to="/onboarding"
            className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-white text-black text-xs font-semibold hover:bg-neutral-200 transition-colors"
          >
            <Plus size={13} aria-hidden="true" /> Register your first agent
          </Link>
        </div>
      )}

      <div className="px-4 lg:px-6 pb-4">
        <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
          <h2 className="text-sm font-semibold text-neutral-200">Time series</h2>
          <div className="inline-flex border border-neutral-700 rounded-md overflow-hidden">
            {METRICS.map((m) => (
              <button
                key={m.id}
                onClick={() => setMetric(m.id)}
                className={`px-3 py-1 text-sm ${metric === m.id ? 'bg-neutral-700 text-white' : 'bg-neutral-900 text-neutral-300 hover:bg-neutral-800'}`}
              >
                {m.label}
              </button>
            ))}
          </div>
        </div>

        {loading && (!chartData || chartData.length === 0) ? (
          <SkeletonLoader variant="card" className="h-64" count={1} />
        ) : (
          <div className="h-64 rounded-lg border border-neutral-800 bg-neutral-950 p-2">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#262626" />
                <XAxis
                  dataKey="t"
                  type="number"
                  domain={['auto', 'auto']}
                  scale="time"
                  stroke="#737373"
                  fontSize={10}
                  tickFormatter={(ts) => new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                />
                <YAxis stroke="#737373" fontSize={10} />
                <Tooltip
                  contentStyle={{ background: '#171717', border: '1px solid #404040', fontSize: 11 }}
                  labelFormatter={(ts) => new Date(ts).toLocaleString()}
                  formatter={(v) => [Number(v).toFixed(2), METRICS.find((m) => m.id === metric)?.label]}
                />
                <Line type="monotone" dataKey="v" stroke="#10b981" strokeWidth={2} dot={false} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
        {(!chartData || chartData.length === 0) && !loading && (
          <p className="text-xs text-neutral-500 mt-2">
            No activity in the selected window. Try a longer range or check
            the gateway is receiving traffic.
          </p>
        )}
      </div>

      <div className="px-4 lg:px-6 pb-6">
        <div className="rounded-lg border border-neutral-800 bg-neutral-900/60 p-3 text-xs text-neutral-400 flex flex-col sm:flex-row sm:justify-between gap-2">
          <span>Tenant-scoped via JWT. No cross-tenant aggregation.</span>
          <span className="flex flex-wrap gap-x-3">
            <Link to="/agent-cost"     className="text-emerald-400 hover:underline">FinOps burn-down →</Link>
            <Link to="/agent-topology" className="text-emerald-400 hover:underline">Agent Topology →</Link>
          </span>
        </div>
      </div>
    </div>
  )
}
