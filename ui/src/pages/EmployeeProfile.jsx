import React, { useEffect, useState, useMemo, useCallback } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  ArrowLeft,
  Building2,
  CalendarClock,
  DollarSign,
  Gauge,
  Loader2,
  PlayCircle,
  RefreshCw,
  Shield,
  ShieldCheck,
  AlertTriangle,
  Activity,
  Key,
} from 'lucide-react'
import { teamService } from '../services/api'
import Button from '../components/Common/Button'
import Card from '../components/Common/Card'

/* ───────── shared helpers (mirror Team.jsx) ─────────────────────────── */

function fmtUSD(n) {
  if (n == null) return '—'
  const v = Number(n) || 0
  if (v >= 1000) return `$${(v / 1000).toFixed(1)}K`
  if (v >= 1)    return `$${v.toFixed(2)}`
  return `$${v.toFixed(4)}`
}

function fmtInt(n) {
  if (n == null) return '—'
  const v = Number(n) || 0
  if (v >= 1000) return `${(v / 1000).toFixed(1)}K`
  return v.toLocaleString()
}

function fmtTs(ts) {
  if (!ts) return '—'
  try {
    const d = new Date(ts)
    return d.toLocaleString(undefined, {
      year: 'numeric', month: 'short', day: '2-digit',
      hour: '2-digit', minute: '2-digit',
    })
  } catch { return String(ts).slice(0, 19).replace('T', ' ') }
}

const RISK_LABEL_STYLES = {
  Low:      'text-green-400  bg-green-500/10  border-green-500/20',
  Moderate: 'text-blue-400   bg-blue-500/10   border-blue-500/20',
  Elevated: 'text-amber-400  bg-amber-500/10  border-amber-500/20',
  High:     'text-red-400    bg-red-500/10    border-red-500/20',
}

function RiskLabel({ label, score }) {
  const style = RISK_LABEL_STYLES[label] || 'text-neutral-500 bg-white/[0.03] border-white/[0.06]'
  return (
    <span className={`status-badge ${style}`}
          title={`Risk score ${(Number(score) || 0).toFixed(2)} (0–1 scale)`}>
      {label || '—'}
    </span>
  )
}

function BudgetBar({ label, spent, budget, pct }) {
  if (budget == null || budget <= 0) {
    return (
      <div className="space-y-1">
        <div className="text-[10px] uppercase tracking-widest text-neutral-500">{label}</div>
        <div className="text-xs text-neutral-500">no cap configured</div>
      </div>
    )
  }
  const pctClamped = Math.min(100, Number(pct) || 0)
  const color = pctClamped >= 95
    ? 'bg-red-500/70'
    : pctClamped >= 70
      ? 'bg-amber-400/70'
      : 'bg-green-500/70'
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-[10px] uppercase tracking-widest text-neutral-500">
        <span>{label}</span>
        <span className="text-neutral-400">{pctClamped.toFixed(1)}%</span>
      </div>
      <div className="h-2 rounded-full bg-white/[0.04] overflow-hidden">
        <div className={`h-full ${color}`} style={{ width: `${pctClamped}%` }} />
      </div>
      <div className="text-[11px] text-neutral-500 font-mono">
        {fmtUSD(spent)} <span className="text-neutral-700">/</span> {fmtUSD(budget)}
      </div>
    </div>
  )
}

function MetricTile({ label, value, sublabel, accent = 'text-white', icon: Icon }) {
  return (
    <Card>
      <div className="space-y-1">
        <div className="flex items-center gap-2 text-[10px] uppercase tracking-widest text-neutral-500">
          {Icon && <Icon size={11} aria-hidden="true" />}
          <span>{label}</span>
        </div>
        <div className={`text-2xl font-bold ${accent}`}>{value}</div>
        {sublabel && <div className="text-[11px] text-neutral-500">{sublabel}</div>}
      </div>
    </Card>
  )
}

/* ───────── 30-day spend sparkline (no external chart lib) ───────────── */

function SpendSparkline({ trend }) {
  const series = trend || []
  const max = Math.max(0.000001, ...series.map((t) => Number(t.spend_usd) || 0))
  const points = series.map((t, i) => {
    const x = (i / Math.max(1, series.length - 1)) * 100
    const y = 100 - ((Number(t.spend_usd) || 0) / max) * 100
    return `${x.toFixed(2)},${y.toFixed(2)}`
  }).join(' ')
  return (
    <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="w-full h-24">
      <polyline
        points={points}
        fill="none"
        stroke="rgb(120, 170, 255)"
        strokeWidth="0.8"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  )
}

/* ───────── page ─────────────────────────────────────────────────────── */

export default function EmployeeProfile() {
  const { email } = useParams()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchProfile = useCallback(async () => {
    setLoading(true)
    try {
      const resp = await teamService.profile(email)
      const payload = resp?.data || resp
      setData(payload)
      setError(null)
    } catch (e) {
      setError(e?.message || 'Failed to load profile')
      setData(null)
    } finally {
      setLoading(false)
    }
  }, [email])

  useEffect(() => { fetchProfile() }, [fetchProfile])

  const trend = data?.trend_30d || []
  const recent = data?.recent_calls || []
  const totalRequests30d = useMemo(
    () => trend.reduce((acc, t) => acc + (Number(t.requests) || 0), 0),
    [trend],
  )

  if (loading) {
    return (
      <div className="p-6 text-xs text-neutral-500 flex items-center justify-center min-h-[60vh]">
        <Loader2 size={20} className="animate-spin mr-2" /> Loading profile…
      </div>
    )
  }

  if (error || !data) {
    return (
      <div className="p-6 space-y-3">
        <Link to="/team" className="inline-flex items-center gap-1 text-xs text-neutral-400 hover:text-white">
          <ArrowLeft size={14} /> Back to Team
        </Link>
        <Card>
          <div className="text-xs text-neutral-300 py-6 text-center space-y-2">
            <AlertTriangle size={20} className="text-amber-400 mx-auto" />
            <div>{error || 'No data'}</div>
            <Button size="sm" onClick={fetchProfile}>
              <RefreshCw size={12} /> Retry
            </Button>
          </div>
        </Card>
      </div>
    )
  }

  const { employee, kpis } = data

  return (
    <div className="p-4 lg:p-6 space-y-4 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div className="space-y-2">
          <Link to="/team" className="inline-flex items-center gap-1 text-xs text-neutral-400 hover:text-white">
            <ArrowLeft size={14} /> Team
          </Link>
          <div className="flex items-center gap-3 flex-wrap">
            <h1 className="text-2xl font-bold text-white">{employee.name}</h1>
            <RiskLabel label={kpis.risk_label} score={kpis.risk_score} />
            {!employee.is_active && (
              <span className="status-badge text-red-400 bg-red-500/10 border-red-500/20">
                Revoked
              </span>
            )}
          </div>
          <div className="text-xs text-neutral-400 flex items-center gap-3 flex-wrap">
            <span>{employee.email}</span>
            <span className="text-neutral-700">·</span>
            <span className="inline-flex items-center gap-1">
              <Building2 size={11} className="text-neutral-500" />
              {employee.department || <span className="italic text-neutral-500">Unassigned</span>}
            </span>
            <span className="text-neutral-700">·</span>
            <span className="inline-flex items-center gap-1 font-mono text-[11px] text-neutral-500">
              <Key size={11} /> {employee.key_prefix}…
            </span>
          </div>
        </div>
        <Button variant="secondary" size="sm" onClick={fetchProfile}>
          <RefreshCw size={12} /> Refresh
        </Button>
      </div>

      {/* KPI tiles */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
        <MetricTile
          label="Requests (30d)"
          value={fmtInt(kpis.requests_30d)}
          sublabel={`${fmtInt(totalRequests30d)} in trend`}
          icon={Activity}
        />
        <MetricTile
          label="Spend (30d)"
          value={fmtUSD(kpis.spend_30d_usd)}
          icon={DollarSign}
        />
        <MetricTile
          label="Today"
          value={fmtUSD(kpis.spend_today_usd)}
          icon={DollarSign}
        />
        <MetricTile
          label="This month"
          value={fmtUSD(kpis.spend_month_usd)}
          icon={DollarSign}
        />
        <MetricTile
          label="Harmful blocked"
          value={fmtInt(kpis.harmful_blocked_30d)}
          accent={kpis.harmful_blocked_30d > 0 ? 'text-amber-400' : 'text-white'}
          icon={ShieldCheck}
        />
        <MetricTile
          label="Last active"
          value={fmtTs(kpis.last_active)}
          sublabel={`models: ${(kpis.models_used || []).length || 0}`}
          accent="text-neutral-200"
          icon={CalendarClock}
        />
      </div>

      {/* Budgets + sparkline */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
        <Card title="Daily budget" icon={Gauge}>
          <BudgetBar
            label="Today vs daily cap"
            spent={kpis.spend_today_usd}
            budget={employee.daily_budget_usd}
            pct={kpis.daily_budget_used_pct}
          />
        </Card>
        <Card title="Monthly budget" icon={Gauge}>
          <BudgetBar
            label="MTD vs monthly cap"
            spent={kpis.spend_month_usd}
            budget={employee.monthly_budget_usd}
            pct={kpis.monthly_budget_used_pct}
          />
        </Card>
        <Card title="Spend (last 30 days)" icon={DollarSign}>
          {trend.some((t) => Number(t.spend_usd) > 0)
            ? <SpendSparkline trend={trend} />
            : (
                <div className="text-xs text-neutral-500 py-6 text-center">
                  No spend recorded in the last 30 days.
                </div>
              )
          }
          <div className="flex items-center justify-between text-[10px] uppercase tracking-widest text-neutral-600 mt-1">
            <span>{trend[0]?.day}</span>
            <span>{trend[trend.length - 1]?.day}</span>
          </div>
        </Card>
      </div>

      {/* Models used */}
      {(kpis.models_used || []).length > 0 && (
        <Card title="Models used" icon={Shield}>
          <div className="flex flex-wrap gap-2">
            {(kpis.models_used || []).map((m) => (
              <span
                key={m}
                className="inline-flex items-center gap-1 text-[11px] text-neutral-300 px-2 py-1 rounded border border-white/[0.06] bg-white/[0.02] font-mono"
              >
                {m}
              </span>
            ))}
          </div>
        </Card>
      )}

      {/* Recent calls */}
      <Card title="Recent calls" icon={Activity}>
        {recent.length === 0 ? (
          <div className="text-xs text-neutral-500 py-8 text-center">
            No calls recorded in the last 30 days.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-[10px] uppercase tracking-widest text-neutral-500">
                <tr className="text-left border-b border-white/[0.05]">
                  <th className="py-2 pr-3">Time</th>
                  <th className="py-2 pr-3">Model</th>
                  <th className="py-2 pr-3 text-right">In</th>
                  <th className="py-2 pr-3 text-right">Out</th>
                  <th className="py-2 pr-3 text-right">Cost</th>
                  <th className="py-2 pr-3">Decision</th>
                  <th className="py-2 pr-3 text-right">Latency</th>
                  <th className="py-2 pr-2"></th>
                </tr>
              </thead>
              <tbody>
                {recent.map((r, i) => {
                  const isBlock = (r.decision || '').toLowerCase() !== 'allow'
                  return (
                    <tr key={i} className="border-b border-white/[0.04] last:border-b-0">
                      <td className="py-2 pr-3 text-neutral-400 font-mono whitespace-nowrap">{fmtTs(r.ts)}</td>
                      <td className="py-2 pr-3 text-neutral-300 font-mono">{r.model}</td>
                      <td className="py-2 pr-3 text-neutral-300 font-mono text-right">{fmtInt(r.input_tokens)}</td>
                      <td className="py-2 pr-3 text-neutral-300 font-mono text-right">{fmtInt(r.output_tokens)}</td>
                      <td className="py-2 pr-3 text-neutral-300 font-mono text-right">{fmtUSD(r.cost_usd)}</td>
                      <td className="py-2 pr-3">
                        <span className={`status-badge ${
                          isBlock
                            ? 'text-red-400 bg-red-500/10 border-red-500/20'
                            : 'text-green-400 bg-green-500/10 border-green-500/20'
                        }`}>
                          {r.decision || 'allow'}
                        </span>
                      </td>
                      <td className="py-2 pr-3 text-neutral-500 font-mono text-right">
                        {r.latency_ms ? `${r.latency_ms}ms` : '—'}
                      </td>
                      <td className="py-2 pr-2 text-right">
                        {r.request_id ? (
                          <Link
                            to={`/replay/${encodeURIComponent(r.request_id)}`}
                            title="Replay timeline"
                            className="inline-flex items-center gap-1 text-[10px] text-blue-300 hover:text-white px-2 py-0.5 rounded-md border border-blue-500/30 bg-blue-500/[0.06] hover:bg-blue-500/[0.12] transition-colors"
                          >
                            <PlayCircle size={10} /> Replay
                          </Link>
                        ) : null}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}
