import React, { useEffect, useState, useCallback } from 'react'
import {
  Users, Activity, ShieldAlert, TrendingUp,
  RefreshCw, AlertTriangle, CheckCircle2, Clock,
  Database, Cpu, Zap, Globe,
} from 'lucide-react'
import { auditService, dashboardService, adminService } from '../services/api'

const REFRESH_MS = 30_000

function KpiCard({ icon: Icon, label, value, sub, color = 'text-white' }) {
  return (
    <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl p-4">
      <div className="flex items-center gap-2 mb-3">
        <Icon size={14} className="text-neutral-500" />
        <span className="text-xs text-neutral-500">{label}</span>
      </div>
      <div className={`text-2xl font-semibold ${color}`}>{value ?? '—'}</div>
      {sub && <div className="text-xs text-neutral-500 mt-1">{sub}</div>}
    </div>
  )
}

function TenantRow({ tenant, index }) {
  const health = tenant.health || 'healthy'
  const healthColor = health === 'healthy' ? 'text-green-400' : health === 'degraded' ? 'text-amber-400' : 'text-red-400'
  const healthDot = health === 'healthy' ? 'bg-green-500' : health === 'degraded' ? 'bg-amber-500' : 'bg-red-500'

  return (
    <tr className={index % 2 === 0 ? '' : 'bg-white/[0.02]'}>
      <td className="px-4 py-3 text-xs text-white font-mono">{tenant.name || tenant.tenant_id?.slice(0, 8) || '—'}</td>
      <td className="px-4 py-3 text-xs text-neutral-400 font-mono">{tenant.tenant_id?.slice(0, 8)}…</td>
      <td className="px-4 py-3">
        <span className={`inline-flex items-center gap-1.5 text-xs ${healthColor}`}>
          <span className={`w-1.5 h-1.5 rounded-full ${healthDot}`} />
          {health}
        </span>
      </td>
      <td className="px-4 py-3 text-xs text-neutral-400 text-right">{(tenant.requests_today ?? 0).toLocaleString()}</td>
      <td className="px-4 py-3 text-xs text-neutral-400 text-right">{(tenant.agents ?? 0)}</td>
      <td className="px-4 py-3 text-xs text-right">
        <span className={`${(tenant.block_rate ?? 0) > 20 ? 'text-amber-400' : 'text-neutral-400'}`}>
          {(tenant.block_rate ?? 0).toFixed(1)}%
        </span>
      </td>
      <td className="px-4 py-3 text-xs text-neutral-500 text-right">{tenant.plan || 'free'}</td>
    </tr>
  )
}

function HeatCell({ value, max }) {
  const pct = max > 0 ? value / max : 0
  const opacity = 0.05 + pct * 0.85
  return (
    <td
      className="w-8 h-6 text-center text-[10px] text-white/70"
      style={{ backgroundColor: `rgba(99,102,241,${opacity.toFixed(2)})` }}
      title={value}
    >
      {value > 0 ? value : ''}
    </td>
  )
}

function UsageHeatmap({ data }) {
  const hours = Array.from({ length: 24 }, (_, i) => i)
  const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
  const max = data ? Math.max(...Object.values(data).flat()) : 1

  if (!data) {
    return (
      <div className="h-28 flex items-center justify-center text-xs text-neutral-600">
        Loading heatmap…
      </div>
    )
  }

  return (
    <div className="overflow-x-auto">
      <table className="text-[10px]">
        <thead>
          <tr>
            <td className="w-10" />
            {hours.map(h => (
              <td key={h} className="w-8 text-center text-neutral-600 pb-1">
                {h % 6 === 0 ? `${h}h` : ''}
              </td>
            ))}
          </tr>
        </thead>
        <tbody>
          {days.map(day => (
            <tr key={day}>
              <td className="pr-2 text-neutral-600 text-right w-10">{day}</td>
              {hours.map(h => (
                <HeatCell key={h} value={(data[day] || [])[h] || 0} max={max} />
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function ServiceRow({ name, status, latency }) {
  const ok = status === 'ok' || status === 'healthy'
  return (
    <div className="flex items-center gap-3 py-2 border-b border-[var(--border-subtle)] last:border-0">
      <span className={`w-2 h-2 rounded-full shrink-0 ${ok ? 'bg-green-500' : 'bg-red-500'}`} />
      <span className="text-xs text-neutral-300 flex-1 font-mono">{name}</span>
      <span className={`text-xs ${ok ? 'text-green-400' : 'text-red-400'}`}>{status}</span>
      {latency != null && (
        <span className="text-xs text-neutral-600">{latency}ms</span>
      )}
    </div>
  )
}

export default function AdminConsole() {
  const [kpis, setKpis] = useState(null)
  const [tenants, setTenants] = useState([])
  const [heatmap, setHeatmap] = useState(null)
  const [services, setServices] = useState([])
  const [lastRefresh, setLastRefresh] = useState(null)
  const [refreshing, setRefreshing] = useState(false)
  // 2026-05-28: per-endpoint error tracking replaces silent Promise.allSettled
  // swallowing. Each key maps to either null (ok) or a short error string
  // surfaced in the banner near the top of the page.
  const [errors, setErrors] = useState({ health: null, summary: null, tenants: null, heatmap: null })

  const errMsg = (reason) => {
    const r = reason
    if (!r) return 'error'
    if (r?.response?.status) return String(r.response.status)
    if (r?.code === 'ECONNABORTED' || /timeout/i.test(r?.message || '')) return 'timeout'
    return r?.message?.slice(0, 60) || 'error'
  }

  const load = useCallback(async () => {
    setRefreshing(true)
    const nextErrors = { health: null, summary: null, tenants: null, heatmap: null }
    const [healthRes, summaryRes, tenantsRes, heatmapRes] = await Promise.allSettled([
      dashboardService.getSystemHealth(),
      auditService.getSummary(),
      adminService.listTenants(),
      auditService.getHeatmap(),
    ])

    if (healthRes.status === 'fulfilled') {
      const h = healthRes.value?.data || healthRes.value || {}
      const svcList = Object.entries(h.services || {}).map(([name, info]) => ({
        name,
        status: typeof info === 'string' ? info : info?.status || 'unknown',
        latency: info?.latency_ms,
      }))
      setServices(svcList)
      setKpis(prev => ({ ...(prev || {}), services_healthy: svcList.filter(s => s.status === 'ok' || s.status === 'healthy').length, services_total: svcList.length }))
    } else {
      nextErrors.health = errMsg(healthRes.reason)
    }

    if (summaryRes.status === 'fulfilled') {
      const s = summaryRes.value?.data || summaryRes.value || {}
      setKpis(prev => ({
        ...(prev || {}),
        total_decisions: s.total ?? 0,
        allowed: s.allowed ?? 0,
        blocked: s.blocked ?? 0,
        block_rate: s.total > 0 ? ((s.blocked / s.total) * 100).toFixed(1) : '0.0',
      }))
    } else {
      nextErrors.summary = errMsg(summaryRes.reason)
    }

    if (tenantsRes.status === 'fulfilled') {
      setTenants(tenantsRes.value?.data || tenantsRes.value || [])
    } else {
      nextErrors.tenants = errMsg(tenantsRes.reason)
    }

    if (heatmapRes.status === 'fulfilled') {
      setHeatmap(heatmapRes.value?.data || heatmapRes.value || null)
    } else {
      nextErrors.heatmap = errMsg(heatmapRes.reason)
    }

    setErrors(nextErrors)
    setLastRefresh(new Date())
    setRefreshing(false)
  }, [])

  useEffect(() => {
    load()
    const id = setInterval(load, REFRESH_MS)
    return () => clearInterval(id)
  }, [load])

  const blockRate = parseFloat(kpis?.block_rate ?? 0)

  return (
    <div className="max-w-7xl mx-auto space-y-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-white mb-1">Admin Console</h1>
          <p className="text-sm text-neutral-400">
            Platform-wide health, tenant activity, and governance metrics.
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

      {/* Per-endpoint error banner — surfaces partial failures instead of
          silently zeroing out tiles. Retry button calls the same loader. */}
      {Object.values(errors).some(Boolean) && (
        <div className="flex items-start gap-3 px-4 py-3 bg-amber-500/10 border border-amber-500/20 rounded-xl text-xs text-amber-300" role="alert">
          <AlertTriangle size={14} className="shrink-0 mt-0.5" />
          <div className="flex-1">
            <strong className="text-amber-200">Some metrics unavailable:</strong>{' '}
            {Object.entries(errors).filter(([, v]) => v).map(([k, v]) => `${k} (${v})`).join(', ')}
          </div>
          <button
            onClick={load}
            disabled={refreshing}
            className="text-amber-200 underline hover:text-amber-100 disabled:opacity-50"
          >
            Retry
          </button>
        </div>
      )}

      {/* KPI strip */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <KpiCard
          icon={Activity}
          label="Total Decisions"
          value={errors.summary ? 'Unavailable' : (kpis?.total_decisions ?? 0).toLocaleString()}
          color={errors.summary ? 'text-neutral-500' : 'text-white'}
          sub={errors.summary ? `audit summary: ${errors.summary}` : 'all time'}
        />
        <KpiCard
          icon={CheckCircle2}
          label="Allowed"
          value={errors.summary ? 'Unavailable' : (kpis?.allowed ?? 0).toLocaleString()}
          color={errors.summary ? 'text-neutral-500' : 'text-green-400'}
          sub={errors.summary ? `audit summary: ${errors.summary}` : undefined}
        />
        <KpiCard
          icon={ShieldAlert}
          label="Blocked"
          value={errors.summary ? 'Unavailable' : (kpis?.blocked ?? 0).toLocaleString()}
          color={errors.summary ? 'text-neutral-500' : (blockRate > 20 ? 'text-amber-400' : 'text-red-400')}
          sub={errors.summary ? `audit summary: ${errors.summary}` : `${kpis?.block_rate ?? '0.0'}% block rate`}
        />
        <KpiCard
          icon={Database}
          label="Services"
          value={errors.health ? 'Unavailable' : (kpis ? `${kpis.services_healthy}/${kpis.services_total}` : '—')}
          color={errors.health ? 'text-neutral-500' : (kpis && kpis.services_healthy === kpis.services_total ? 'text-green-400' : 'text-amber-400')}
          sub={errors.health ? `system health: ${errors.health}` : 'healthy'}
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Usage heatmap */}
        <div className="lg:col-span-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl p-5">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-medium text-white flex items-center gap-2">
              <TrendingUp size={14} className="text-neutral-500" />
              Request Volume Heatmap
            </h2>
            <span className="text-[10px] text-neutral-600">last 7 days × 24h</span>
          </div>
          <UsageHeatmap data={heatmap} />
          <div className="flex items-center gap-3 mt-3 text-[10px] text-neutral-600">
            <span>Low</span>
            <div className="flex gap-0.5">
              {[0.1, 0.3, 0.5, 0.7, 0.9].map(o => (
                <div key={o} className="w-4 h-3 rounded-sm" style={{ backgroundColor: `rgba(99,102,241,${o})` }} />
              ))}
            </div>
            <span>High</span>
          </div>
        </div>

        {/* Service health */}
        <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl p-5">
          <h2 className="text-sm font-medium text-white flex items-center gap-2 mb-4">
            <Cpu size={14} className="text-neutral-500" />
            Service Health
          </h2>
          {services.length === 0 ? (
            <div className="text-xs text-neutral-600 py-4 text-center">Loading…</div>
          ) : (
            <div>
              {services.map(s => <ServiceRow key={s.name} {...s} />)}
            </div>
          )}
        </div>
      </div>

      {/* Tenant table */}
      <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl overflow-hidden">
        <div className="px-5 py-4 border-b border-[var(--border-subtle)] flex items-center justify-between">
          <h2 className="text-sm font-medium text-white flex items-center gap-2">
            <Globe size={14} className="text-neutral-500" />
            Tenant Activity
          </h2>
          <span className="text-xs text-neutral-500">{tenants.length} tenant{tenants.length !== 1 ? 's' : ''}</span>
        </div>
        {tenants.length === 0 ? (
          <div className="px-5 py-8 text-center text-xs text-neutral-600">No tenant data available.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-[var(--border-subtle)]">
                  {['Name', 'ID', 'Health', 'Requests Today', 'Agents', 'Block Rate', 'Plan'].map((h, i) => (
                    <th
                      key={h}
                      className={`px-4 py-2.5 text-[10px] uppercase tracking-wider text-neutral-600 ${i > 2 ? 'text-right' : 'text-left'}`}
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {tenants.map((t, i) => <TenantRow key={t.tenant_id} tenant={t} index={i} />)}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Quick links */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {[
          { icon: Users, label: 'RBAC Manager', to: '/rbac', desc: 'Roles & permissions' },
          { icon: Activity, label: 'Observability', to: '/observability', desc: 'Metrics & SLOs' },
          { icon: Zap, label: 'Kill Switch', to: '/kill-switch', desc: 'Emergency shutdown' },
          { icon: AlertTriangle, label: 'Incidents', to: '/incidents', desc: 'Active alerts' },
        ].map(({ icon: Icon, label, to, desc }) => (
          <a
            key={to}
            href={to}
            className="group flex items-center gap-3 p-3 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl hover:border-white/20 transition-all"
          >
            <div className="w-8 h-8 rounded-lg bg-white/[0.04] flex items-center justify-center shrink-0">
              <Icon size={14} className="text-neutral-500 group-hover:text-white" />
            </div>
            <div>
              <div className="text-xs font-medium text-white">{label}</div>
              <div className="text-[10px] text-neutral-600">{desc}</div>
            </div>
          </a>
        ))}
      </div>
    </div>
  )
}
