// Sprint 4 — Fleet Home dashboard
//
// In-product KPI dashboard + time-series for tokens / errors / latency
// / deny-rate. All data is tenant-scoped at the backend via the
// JWT-derived tenant_id. No mocked arrays.
//
// Data sources:
//   GET /audit/fleet/kpis              — KPI card payload
//   GET /audit/fleet/timeseries        — per-metric time-series

import React, { useEffect, useMemo, useState, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { Activity, ShieldOff, AlertTriangle, Users, Wrench, Clock } from 'lucide-react'
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid,
} from 'recharts'
import { fleetService } from '../services/api'

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
  if (x == null || Number.isNaN(x)) return '—'
  return `${(x * 100).toFixed(2)}%`
}

function fmtNum(x) {
  if (x == null) return '—'
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

  const fetchAll = useCallback(async () => {
    setLoading(true)
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
    }
  }, [minutes, metric])

  useEffect(() => { fetchAll() }, [fetchAll])

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
          <select
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

      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 px-6 py-4">
        <KpiCard icon={Activity}        label="Decisions"     value={fmtNum(kpis?.decisions)} />
        <KpiCard icon={ShieldOff}       label="Deny rate"     value={fmtPct(kpis?.deny_rate)}
                  accent={kpis?.deny_rate > 0.10 ? 'text-rose-200' : 'text-emerald-200'}
                  sub={`${fmtNum(kpis?.denied)} denied`} />
        <KpiCard icon={AlertTriangle}   label="Error rate"    value={fmtPct(kpis?.error_rate)}
                  accent={kpis?.error_rate > 0.05 ? 'text-rose-200' : 'text-neutral-100'}
                  sub={`${fmtNum(kpis?.errors)} errors`} />
        <KpiCard icon={Users}           label="Active agents" value={fmtNum(kpis?.active_agents)} />
        <KpiCard icon={Wrench}          label="Distinct tools" value={fmtNum(kpis?.distinct_tools)} />
        <Link to="/agent-health" className="rounded-lg border border-neutral-800 bg-neutral-900/60 hover:bg-neutral-900 px-4 py-3 flex items-center justify-center text-sm text-emerald-400">
          Open Agent Health ↗
        </Link>
      </div>

      <div className="px-6 pb-4">
        <div className="flex items-center justify-between mb-2">
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
        {(!chartData || chartData.length === 0) && !loading && (
          <p className="text-xs text-neutral-500 mt-2">
            No activity in the selected window. Try a longer range or check
            the gateway is receiving traffic.
          </p>
        )}
      </div>

      <div className="px-6 pb-6">
        <div className="rounded-lg border border-neutral-800 bg-neutral-900/60 p-3 text-xs text-neutral-400 flex justify-between">
          <span>Tenant-scoped via JWT. No cross-tenant aggregation.</span>
          <span>
            <Link to="/agent-cost"     className="text-emerald-400 hover:underline mr-3">FinOps burn-down →</Link>
            <Link to="/agent-topology" className="text-emerald-400 hover:underline">Agent Topology →</Link>
          </span>
        </div>
      </div>
    </div>
  )
}
