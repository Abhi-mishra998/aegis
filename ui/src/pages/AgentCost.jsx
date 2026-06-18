// Sprint 4 — Agent Cost (Agent FinOps burn-down)
//
// "You don't just chart cost — you stop it."
//
// Reads the same Redis counters the gateway's inference-cost limiter
// enforces against, so the chart number is the same number the cap
// uses. No two-source-of-truth drift. The page accepts ?agent_id=...
// from URL deep-links (the Agent Health table sends operators here).

import React, { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  ResponsiveContainer, RadialBarChart, RadialBar, PolarAngleAxis, Tooltip,
} from 'recharts'
import { fleetService } from '../services/api'

function unwrap(r) { return r?.data ?? r }

function statusClass(status) {
  switch (status) {
    case 'over':      return ['bg-rose-950',   'border-rose-700',    'text-rose-100']
    case 'critical':  return ['bg-amber-950',  'border-amber-700',   'text-amber-100']
    case 'warning':   return ['bg-yellow-950', 'border-yellow-700',  'text-yellow-100']
    case 'ok':        return ['bg-emerald-950','border-emerald-700', 'text-emerald-100']
    default:          return ['bg-neutral-900','border-neutral-700', 'text-neutral-100']
  }
}

function fmtUsd(x) {
  if (x == null) return '—'
  return `$${Number(x).toFixed(4)}`
}

function fmtPct(x) {
  if (x == null) return '—'
  return `${(x * 100).toFixed(1)}%`
}

function BurnDownGauge({ percent, status }) {
  // RadialBar gauge for 0-100% (clamped). Recharts wants 0-360 for full
  // circle; we use 270 so the gauge looks like an arc.
  const pct = percent == null ? 0 : Math.min(1.5, Math.max(0, percent))
  const [bg, border, fg] = statusClass(status)
  const data = [{ name: 'used', value: pct * 100, fill: '#10b981' }]
  return (
    <div className={`rounded-lg border ${border} ${bg} p-4`}>
      <div className="h-40">
        <ResponsiveContainer width="100%" height="100%">
          <RadialBarChart
            data={data}
            innerRadius="65%"
            outerRadius="100%"
            startAngle={225}
            endAngle={-45}
          >
            <PolarAngleAxis type="number" domain={[0, 150]} angleAxisId={0} tick={false} />
            <RadialBar
              background={{ fill: '#262626' }}
              dataKey="value"
              cornerRadius={8}
              fill={status === 'over' ? '#fb7185' : status === 'critical' ? '#fbbf24' : status === 'warning' ? '#facc15' : '#10b981'}
            />
            <Tooltip
              contentStyle={{ background: '#171717', border: '1px solid #404040', fontSize: 11 }}
              formatter={(v) => [`${v.toFixed(1)}%`, 'Used']}
            />
          </RadialBarChart>
        </ResponsiveContainer>
      </div>
      <div className={`text-center ${fg} text-2xl font-semibold tabular-nums mt-2`}>
        {percent != null ? `${(percent * 100).toFixed(1)}%` : 'No cap'}
      </div>
      <div className="text-center text-xs uppercase tracking-wide text-neutral-400 mt-1">
        {status}
      </div>
    </div>
  )
}

function ScopePanel({ title, scope }) {
  if (!scope) {
    return (
      <div className="rounded-lg border border-neutral-800 bg-neutral-900/40 p-4 text-sm text-neutral-400">
        {title}: no agent_id passed.
      </div>
    )
  }
  return (
    <div className="space-y-2">
      <h2 className="text-sm font-semibold text-neutral-200">{title}</h2>
      <BurnDownGauge percent={scope.percent_used} status={scope.status} />
      <div className="grid grid-cols-3 gap-2 text-xs">
        <div className="rounded border border-neutral-800 bg-neutral-900/60 px-3 py-2">
          <div className="text-[10px] uppercase tracking-wide text-neutral-500">Used</div>
          <div className="text-sm font-semibold mt-1 tabular-nums">{fmtUsd(scope.used_usd)}</div>
        </div>
        <div className="rounded border border-neutral-800 bg-neutral-900/60 px-3 py-2">
          <div className="text-[10px] uppercase tracking-wide text-neutral-500">Cap</div>
          <div className="text-sm font-semibold mt-1 tabular-nums">{fmtUsd(scope.cap_usd)}</div>
        </div>
        <div className="rounded border border-neutral-800 bg-neutral-900/60 px-3 py-2">
          <div className="text-[10px] uppercase tracking-wide text-neutral-500">Remaining</div>
          <div className="text-sm font-semibold mt-1 tabular-nums">{fmtUsd(scope.remaining_usd)}</div>
        </div>
      </div>
    </div>
  )
}

export default function AgentCost() {
  const [searchParams, setSearchParams] = useSearchParams()
  const initialAgent = searchParams.get('agent_id') || ''
  const [agentId, setAgentId] = useState(initialAgent)
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const fetchBurnDown = useCallback(async (aid) => {
    setLoading(true)
    setError('')
    try {
      const resp = await fleetService.burnDown(aid || undefined)
      setData(unwrap(resp))
    } catch (e) {
      setError(e?.message || 'Failed to load burn-down')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchBurnDown(agentId) }, [agentId, fetchBurnDown])

  const onSubmit = (e) => {
    e.preventDefault()
    setSearchParams(agentId ? { agent_id: agentId } : {})
    fetchBurnDown(agentId)
  }

  return (
    <div className="text-neutral-100">
      <header className="flex flex-col md:flex-row md:items-center md:justify-between gap-3 px-6 py-4 border-b border-neutral-800">
        <div>
          <h1 className="text-xl font-semibold">Agent FinOps — Burn-Down</h1>
          <p className="text-sm text-neutral-400 mt-1">
            Current-period USD usage against caps. Reads the exact same
            counters the gateway's <code>InferenceCostLimiter</code>
            enforces against — no drift.
          </p>
        </div>
        <form onSubmit={onSubmit} className="flex gap-2">
          <input
            type="text"
            placeholder="agent_id (optional)"
            value={agentId}
            onChange={(e) => setAgentId(e.target.value)}
            className="px-3 py-1 bg-neutral-900 border border-neutral-700 rounded-md text-sm w-64 font-mono"
          />
          <button
            type="submit"
            disabled={loading}
            className="px-3 py-1 bg-emerald-600 hover:bg-emerald-500 rounded-md text-sm text-white"
          >
            {loading ? 'Loading…' : 'Refresh'}
          </button>
        </form>
      </header>

      {error && (
        <div className="mx-6 my-3 text-sm bg-rose-950 border border-rose-700 text-rose-100 px-3 py-2 rounded">
          {error}
        </div>
      )}

      {data && (
        <>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6 p-6">
            <ScopePanel title="Workspace" scope={data.tenant} />
            <ScopePanel title="Agent"  scope={data.agent} />
          </div>
          <div className="px-6 pb-6">
            <div className="rounded-lg border border-neutral-800 bg-neutral-900/60 p-3 text-xs text-neutral-400 flex justify-between">
              <span>Period: {data.period || '—'}</span>
              <span>Resets at: {data.resets_at || '—'}</span>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
