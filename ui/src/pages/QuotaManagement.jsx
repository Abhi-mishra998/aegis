import React, { useEffect, useState, useCallback } from 'react'
import {
  Gauge, RefreshCw, AlertTriangle, CheckCircle2,
  TrendingUp, Clock, Zap, Database,
} from 'lucide-react'
import { tenantService } from '../services/api'

function ProgressBar({ value, max, color = 'bg-white' }) {
  const pct = max > 0 ? Math.min((value / max) * 100, 100) : 0
  const barColor = pct >= 90 ? 'bg-red-500' : pct >= 75 ? 'bg-amber-500' : color
  return (
    <div className="h-1.5 bg-white/[0.06] rounded-full overflow-hidden">
      <div className={`h-full ${barColor} rounded-full transition-all`} style={{ width: `${pct}%` }} />
    </div>
  )
}

function QuotaRow({ label, icon: Icon, used, limit, unit = '' }) {
  const pct = limit > 0 ? Math.min(Math.round((used / limit) * 100), 100) : 0
  const statusColor = pct >= 90 ? 'text-red-400' : pct >= 75 ? 'text-amber-400' : 'text-green-400'
  const statusText  = pct >= 90 ? 'Critical' : pct >= 75 ? 'Warning' : 'OK'

  return (
    <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Icon size={14} className="text-neutral-500" />
          <span className="text-sm font-medium text-white">{label}</span>
        </div>
        <span className={`text-xs font-medium ${statusColor} flex items-center gap-1`}>
          {pct >= 90 ? <AlertTriangle size={11} /> : <CheckCircle2 size={11} />}
          {statusText}
        </span>
      </div>
      <ProgressBar value={used} max={limit} />
      <div className="flex items-center justify-between mt-2 text-xs text-neutral-500">
        <span>{(used ?? 0).toLocaleString()}{unit} used</span>
        <span className="font-medium text-neutral-400">{pct}% of {(limit ?? 0).toLocaleString()}{unit}</span>
      </div>
    </div>
  )
}

function StatCard({ icon: Icon, label, value, sub }) {
  return (
    <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl p-4">
      <div className="flex items-center gap-2 mb-2">
        <Icon size={13} className="text-neutral-500" />
        <span className="text-[10px] uppercase tracking-wider text-neutral-500">{label}</span>
      </div>
      <div className="text-2xl font-semibold text-white">{value ?? '—'}</div>
      {sub && <div className="text-xs text-neutral-600 mt-1">{sub}</div>}
    </div>
  )
}

export default function QuotaManagement() {
  const [quota, setQuota]       = useState(null)
  const [loading, setLoading]   = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [lastRefresh, setLastRefresh] = useState(null)
  const [fetchError, setFetchError] = useState(false)

  const load = useCallback(async () => {
    setRefreshing(true)
    try {
      const res = await tenantService.getQuota()
      setQuota(res?.data || res)
      setLastRefresh(new Date())
      setFetchError(false)
    } catch {
      setFetchError(true)
    }
    setRefreshing(false)
    setLoading(false)
  }, [])

  useEffect(() => {
    load()
    const id = setInterval(load, 30_000)
    return () => clearInterval(id)
  }, [load])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <RefreshCw className="animate-spin text-neutral-500" size={24} />
      </div>
    )
  }

  const limits = quota?.limits || {}
  const usage  = quota?.usage  || {}

  const rps         = limits.requests_per_second ?? 0
  const burst       = limits.burst ?? 0
  const dailyCap    = limits.daily_request_cap ?? 0
  const monthlyCap  = limits.monthly_request_cap ?? 0
  const dailyUsed   = usage.requests_today ?? 0
  const monthlyUsed = usage.requests_this_month ?? 0
  const costCap     = limits.daily_inference_cost_usd ?? 0
  const costUsed    = usage.inference_cost_today_usd ?? 0

  const monthlyPct = monthlyCap > 0 ? Math.round((monthlyUsed / monthlyCap) * 100) : 0
  const resetAt    = quota?.reset_at || quota?.daily_reset_at

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-white mb-1">Quota Management</h1>
          <p className="text-sm text-neutral-400">
            Real-time request limits and inference cost caps for this workspace.
          </p>
        </div>
        <div className="flex items-center gap-3">
          {lastRefresh && (
            <span className="text-xs text-neutral-600 flex items-center gap-1">
              <Clock size={11} /> {lastRefresh.toLocaleTimeString()}
            </span>
          )}
          <button
            onClick={load}
            disabled={refreshing}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[var(--border-subtle)] text-xs text-neutral-300 hover:border-white/20"
          >
            <RefreshCw size={12} className={refreshing ? 'animate-spin' : ''} />
            Refresh
          </button>
        </div>
      </header>

      {/* Fallback banner — quota fetch failed */}
      {fetchError && (
        <div className="flex items-start gap-3 p-4 rounded-xl border bg-amber-500/10 border-amber-500/20 text-amber-400">
          <AlertTriangle size={16} className="shrink-0 mt-0.5" />
          <div>
            <div className="text-sm font-medium">Live quota unavailable. Showing default limits — may not match enforcement.</div>
            <div className="text-xs mt-0.5 opacity-80">
              Could not reach the workspace quota service. Values shown below are fallback defaults; actual rate limiting may differ.
            </div>
          </div>
        </div>
      )}

      {/* Alert banner */}
      {!fetchError && monthlyPct >= 80 && (
        <div className={`flex items-start gap-3 p-4 rounded-xl border ${monthlyPct >= 100 ? 'bg-red-500/10 border-red-500/20 text-red-400' : 'bg-amber-500/10 border-amber-500/20 text-amber-400'}`}>
          <AlertTriangle size={16} className="shrink-0 mt-0.5" />
          <div>
            <div className="text-sm font-medium">{monthlyPct >= 100 ? 'Monthly quota exhausted' : `Monthly quota ${monthlyPct}% used`}</div>
            <div className="text-xs mt-0.5 opacity-80">
              {monthlyPct >= 100
                ? 'New execute requests are being rejected. Upgrade your plan or wait for the monthly reset.'
                : 'Approaching monthly limit. Consider upgrading your plan.'}
            </div>
          </div>
        </div>
      )}

      {/* Stat strip */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard icon={Zap}      label="Rate Limit"    value={`${rps} r/s`} sub={`burst: ${burst}`} />
        <StatCard icon={TrendingUp} label="Today"       value={(dailyUsed).toLocaleString()} sub={`of ${dailyCap.toLocaleString()} cap`} />
        <StatCard icon={Database}  label="This Month"   value={(monthlyUsed).toLocaleString()} sub={`${monthlyPct}% of ${monthlyCap.toLocaleString()}`} />
        <StatCard icon={Gauge}     label="Inference $"  value={`$${Number(costUsed).toFixed(2)}`} sub={`cap: $${Number(costCap).toFixed(2)}/day`} />
      </div>

      {/* Progress bars */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <QuotaRow
          label="Daily Requests"
          icon={TrendingUp}
          used={dailyUsed}
          limit={dailyCap}
        />
        <QuotaRow
          label="Monthly Requests"
          icon={Database}
          used={monthlyUsed}
          limit={monthlyCap}
        />
        <QuotaRow
          label="Daily Inference Cost"
          icon={Gauge}
          used={Number(costUsed) * 100}
          limit={Number(costCap) * 100}
          unit="¢"
        />
        <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl p-4">
          <div className="flex items-center gap-2 mb-3">
            <Clock size={14} className="text-neutral-500" />
            <span className="text-sm font-medium text-white">Limits Configuration</span>
          </div>
          <div className="space-y-2">
            {[
              ['Requests/second', `${rps} r/s (burst: ${burst})`],
              ['Daily cap', dailyCap.toLocaleString()],
              ['Monthly cap', monthlyCap.toLocaleString()],
              ['Inference $/day', `$${Number(costCap).toFixed(2)}`],
            ].map(([k, v]) => (
              <div key={k} className="flex items-center justify-between text-xs">
                <span className="text-neutral-500">{k}</span>
                <span className="text-white font-mono">{v}</span>
              </div>
            ))}
          </div>
          {resetAt && (
            <div className="mt-3 text-[10px] text-neutral-600 border-t border-[var(--border-subtle)] pt-2">
              Daily reset at: {new Date(resetAt).toLocaleString()}
            </div>
          )}
        </div>
      </div>

      {/* Self-host CTA */}
      <div className="flex items-center justify-between p-4 bg-white/[0.02] border border-[var(--border-subtle)] rounded-xl">
        <div>
          <div className="text-sm font-medium text-white">Need higher limits?</div>
          <div className="text-xs text-neutral-500 mt-0.5">Aegis is Apache 2.0. Self-host on your own infrastructure to set limits to whatever your hardware can handle.</div>
        </div>
        <a href="/open-source" className="shrink-0 flex items-center gap-2 px-4 py-2 rounded-lg bg-white text-black text-sm font-medium hover:bg-neutral-200">
          Self-host
        </a>
      </div>
    </div>
  )
}
