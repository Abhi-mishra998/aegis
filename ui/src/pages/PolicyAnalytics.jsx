import React, { useEffect, useState, useCallback, useRef } from 'react'
import { Link } from 'react-router-dom'
import {
  GitMerge, BarChart2, TrendingUp, AlertTriangle,
  CheckCircle2, XCircle, RefreshCw, Clock, Zap,
  ArrowRight, Info, Wrench, Activity, PlayCircle,
} from 'lucide-react'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Cell, ComposedChart, Line, LineChart, AreaChart, Area, ReferenceLine,
} from 'recharts'
import { policyService, auditService } from '../services/api'
import { useSSE } from '../hooks/useSSE'
import SkeletonLoader from '../components/Common/SkeletonLoader'

const RISK_COLOR = { low: '#22c55e', medium: '#f59e0b', high: '#ef4444', critical: '#7c3aed' }

function KpiCard({ icon: Icon, label, value, sub, accent = '' }) {
  return (
    <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl p-4">
      <div className="flex items-center gap-2 mb-2">
        <Icon size={13} className="text-neutral-500" />
        <span className="text-[10px] uppercase tracking-wider text-neutral-500">{label}</span>
      </div>
      <div className={`text-2xl font-semibold ${accent || 'text-white'}`}>{value ?? '—'}</div>
      {sub && <div className="text-xs text-neutral-600 mt-1">{sub}</div>}
    </div>
  )
}

function PolicyRow({ policy, rank }) {
  const hitRate = policy.total > 0 ? ((policy.triggered / policy.total) * 100).toFixed(1) : '0.0'
  const fpRate  = policy.triggered > 0 ? ((policy.false_positives / policy.triggered) * 100).toFixed(1) : '0.0'
  const statusColor = parseFloat(fpRate) > 15 ? 'text-amber-400' : parseFloat(hitRate) === 0 ? 'text-neutral-600' : 'text-green-400'
  const tag = parseFloat(hitRate) === 0 ? 'unused' : parseFloat(fpRate) > 15 ? 'noisy' : 'healthy'
  const tagStyle = { unused: 'bg-neutral-500/10 text-neutral-500', noisy: 'bg-amber-500/10 text-amber-400', healthy: 'bg-green-500/10 text-green-400' }

  return (
    <tr className={rank % 2 === 0 ? '' : 'bg-white/[0.02]'}>
      <td className="px-4 py-3 text-xs text-neutral-500 w-8">{rank + 1}</td>
      <td className="px-4 py-3">
        <div className="text-xs text-white font-mono">{policy.name}</div>
        <div className="text-[10px] text-neutral-600">{policy.description || 'No description'}</div>
      </td>
      <td className="px-4 py-3 text-xs text-neutral-400 text-right">{(policy.triggered ?? 0).toLocaleString()}</td>
      <td className="px-4 py-3 text-xs text-right">
        <span className={statusColor}>{hitRate}%</span>
      </td>
      <td className="px-4 py-3 text-xs text-right">
        <span className={parseFloat(fpRate) > 15 ? 'text-amber-400' : 'text-neutral-400'}>{fpRate}%</span>
      </td>
      <td className="px-4 py-3">
        <span className={`text-[10px] px-2 py-0.5 rounded-full ${tagStyle[tag]}`}>{tag}</span>
      </td>
    </tr>
  )
}

const CustomTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null
  return (
    <div className="bg-neutral-900 border border-white/10 rounded-lg p-2.5 text-xs shadow-xl">
      <div className="text-neutral-400 mb-1">{label}</div>
      {payload.map(p => (
        <div key={p.name} className="flex items-center gap-2">
          <span className="font-medium" style={{ color: p.fill }}>{p.name}</span>
          <span className="text-white">{p.value}</span>
        </div>
      ))}
    </div>
  )
}

/* ── Escalation Rate Trend ───────────────────────────────────────────────── */
function EscalationRateTrendChart({ series }) {
  if (!series || series.length === 0) return (
    <div className="flex items-center justify-center h-32 text-neutral-600 text-xs">No decision data in window</div>
  )
  const ticks = series.filter((_, i) => i % Math.ceil(series.length / 6) === 0).map(d => d.date)
  return (
    <ResponsiveContainer width="100%" height={140}>
      <LineChart data={series} margin={{ top: 8, right: 16, bottom: 8, left: -16 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
        <XAxis dataKey="date" ticks={ticks} tick={{ fontSize: 9, fill: '#525252' }} tickFormatter={d => d.slice(5)} />
        <YAxis tick={{ fontSize: 9, fill: '#525252' }} tickFormatter={v => `${v}%`} />
        <ReferenceLine y={5} stroke="#f59e0b" strokeDasharray="4 3" strokeOpacity={0.5} label={{ value: '5%', fill: '#f59e0b', fontSize: 9 }} />
        <Tooltip
          contentStyle={{ background: '#1a1a1a', border: '1px solid #333', borderRadius: 8, fontSize: 11 }}
          labelStyle={{ color: '#999' }}
          formatter={(v) => [v != null ? `${v}%` : '—', 'Escalation Rate']}
        />
        <Line
          type="monotone"
          dataKey="escalation_rate"
          stroke="#a855f7"
          strokeWidth={2}
          dot={false}
          connectNulls
          name="Escalation Rate"
        />
      </LineChart>
    </ResponsiveContainer>
  )
}

/* ── Finding Type Breakdown ──────────────────────────────────────────────── */
const FINDING_COLORS = {
  policy_violation:    '#ef4444',
  pii_detected:        '#f97316',
  prompt_injection:    '#a855f7',
  data_exfiltration:   '#ec4899',
  credential_exposure: '#f59e0b',
  privilege_escalation:'#eab308',
  anomalous_behavior:  '#6366f1',
  rate_limit_exceeded: '#14b8a6',
}

function FindingBreakdownChart({ findings }) {
  if (!findings || findings.length === 0) return (
    <div className="flex items-center justify-center h-24 text-neutral-600 text-xs">No finding data</div>
  )
  const maxCount = Math.max(...findings.map(f => f.count), 1)
  return (
    <div className="space-y-2">
      {findings.map((f, i) => {
        const color = FINDING_COLORS[f.finding] || '#6366f1'
        const barPct = Math.round(f.count / maxCount * 100)
        return (
          <div key={i} className="space-y-0.5">
            <div className="flex items-center justify-between text-xs">
              <div className="flex items-center gap-2 min-w-0">
                <span className="shrink-0 text-[10px] font-mono text-neutral-600 w-4 text-right">{i + 1}</span>
                <span className="font-mono truncate" style={{ color }}>{f.finding}</span>
              </div>
              <div className="flex items-center gap-2 shrink-0 text-[10px] font-mono ml-3">
                <span className="text-neutral-300">{f.count.toLocaleString()}</span>
                <span className="text-neutral-600 w-9 text-right">{f.pct}%</span>
              </div>
            </div>
            <div className="h-1 bg-white/[0.04] rounded-full overflow-hidden">
              <div className="h-full rounded-full transition-all duration-500" style={{ width: `${barPct}%`, background: color }} />
            </div>
          </div>
        )
      })}
    </div>
  )
}

/* ── Tool Risk Breakdown table ─────────────────────────────────────────────── */
function DenyReasonsChart({ reasons, totalDenied }) {
  if (!reasons || reasons.length === 0) return (
    <div className="flex items-center justify-center h-24 text-neutral-600 text-xs">No deny reason data</div>
  )

  const maxCount = reasons[0]?.count ?? 1

  return (
    <div className="space-y-2">
      {reasons.map((r, i) => {
        const barPct = Math.round(r.count / maxCount * 100)
        const color = i < 3 ? 'bg-red-500' : i < 6 ? 'bg-orange-500' : 'bg-amber-500'
        const label = r.reason.length > 48 ? r.reason.slice(0, 46) + '…' : r.reason
        return (
          <div key={i} className="flex items-center gap-3">
            <div className="w-4 text-[10px] text-neutral-600 tabular-nums text-right shrink-0">{i + 1}</div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center justify-between mb-0.5">
                <span className="text-xs text-neutral-300 truncate font-mono" title={r.reason}>{label}</span>
                <span className="text-[10px] text-neutral-500 tabular-nums ml-2 shrink-0">
                  {r.count.toLocaleString()} ({r.pct}%)
                </span>
              </div>
              <div className="h-1.5 bg-white/[0.06] rounded-full overflow-hidden">
                <div className={`h-full rounded-full ${color} transition-all`} style={{ width: `${barPct}%` }} />
              </div>
            </div>
          </div>
        )
      })}
      {totalDenied != null && (
        <p className="text-[10px] text-neutral-700 mt-3 text-right">
          {totalDenied.toLocaleString()} total denied events
        </p>
      )}
    </div>
  )
}

const DECISION_COLORS = {
  allow:    '#22c55e',
  monitor:  '#3b82f6',
  escalate: '#a855f7',
  deny:     '#f97316',
  kill:     '#ef4444',
}

function DecisionTrendChart({ series }) {
  if (!series || series.length === 0) return (
    <div className="flex items-center justify-center h-40 text-neutral-600 text-xs">No trend data</div>
  )

  const formatted = series.map(d => ({ ...d, label: d.date ? d.date.slice(5) : '' }))

  return (
    <ResponsiveContainer width="100%" height={200}>
      <AreaChart data={formatted} margin={{ top: 4, right: 4, bottom: 0, left: -20 }}>
        <defs>
          {Object.entries(DECISION_COLORS).map(([key, color]) => (
            <linearGradient key={key} id={`dtGrad_${key}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%"  stopColor={color} stopOpacity={0.3} />
              <stop offset="95%" stopColor={color} stopOpacity={0.02} />
            </linearGradient>
          ))}
        </defs>
        <CartesianGrid stroke="rgba(255,255,255,0.04)" vertical={false} />
        <XAxis dataKey="label" tick={{ fill: '#525252', fontSize: 10 }} axisLine={false} tickLine={false}
          interval={Math.floor(formatted.length / 6)} />
        <YAxis tick={{ fill: '#525252', fontSize: 10 }} axisLine={false} tickLine={false} />
        <Tooltip
          contentStyle={{ background: '#111', border: '1px solid rgba(255,255,255,0.06)', borderRadius: 8, fontSize: 11 }}
          labelStyle={{ color: '#fff', fontWeight: 600 }}
          itemStyle={{ color: '#a3a3a3' }}
        />
        {Object.entries(DECISION_COLORS).map(([key, color]) => (
          <Area key={key} type="monotone" dataKey={key}
            name={key.charAt(0).toUpperCase() + key.slice(1)}
            stroke={color} strokeWidth={1.5}
            fill={`url(#dtGrad_${key})`} stackId="decisions" dot={false} />
        ))}
      </AreaChart>
    </ResponsiveContainer>
  )
}

function HourlyActivityChart({ buckets }) {
  if (!buckets || buckets.length === 0) return (
    <div className="flex items-center justify-center h-40 text-neutral-600 text-xs">No activity data</div>
  )

  const formatted = buckets.map(b => ({
    ...b,
    label: b.hour === 0 ? '12a' : b.hour < 12 ? `${b.hour}a` : b.hour === 12 ? '12p' : `${b.hour - 12}p`,
  }))

  return (
    <ResponsiveContainer width="100%" height={180}>
      <ComposedChart data={formatted} margin={{ top: 4, right: 8, bottom: 0, left: -20 }}>
        <CartesianGrid stroke="rgba(255,255,255,0.04)" vertical={false} />
        <XAxis dataKey="label" tick={{ fill: '#525252', fontSize: 9 }} axisLine={false} tickLine={false} interval={2} />
        <YAxis yAxisId="left" tick={{ fill: '#525252', fontSize: 10 }} axisLine={false} tickLine={false} />
        <YAxis yAxisId="right" orientation="right" domain={[0, 1]}
          tick={{ fill: '#525252', fontSize: 10 }} axisLine={false} tickLine={false}
          tickFormatter={v => v.toFixed(1)} />
        <Tooltip
          contentStyle={{ background: '#111', border: '1px solid rgba(255,255,255,0.06)', borderRadius: 8, fontSize: 11 }}
          labelStyle={{ color: '#fff', fontWeight: 600 }}
          itemStyle={{ color: '#a3a3a3' }}
        />
        <Bar yAxisId="left" dataKey="count" name="Requests" fill="#6366f1" radius={[2, 2, 0, 0]} opacity={0.8} />
        <Bar yAxisId="left" dataKey="deny_count" name="Denials" fill="#ef4444" radius={[2, 2, 0, 0]} opacity={0.8} />
        <Line yAxisId="right" type="monotone" dataKey="avg_risk" name="Avg Risk"
          stroke="#f97316" strokeWidth={1.5} strokeDasharray="3 2" dot={false} />
      </ComposedChart>
    </ResponsiveContainer>
  )
}

function ToolBreakdownTable({ tools }) {
  if (!tools?.length) return <p className="text-xs text-neutral-600 py-2">No tool data in window.</p>
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-white/[0.06]">
            {['Tool', 'Total Calls', 'Denials', 'Deny Rate', 'Avg Risk'].map((h, i) => (
              <th key={h} className={`px-4 py-2.5 text-[10px] uppercase tracking-wider text-neutral-600 ${i > 0 ? 'text-right' : 'text-left'}`}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {tools.map((t, i) => {
            const denyPct = (t.deny_rate * 100).toFixed(1)
            const riskPct = (t.avg_risk * 100).toFixed(1)
            const denyColor = t.deny_rate >= 0.5 ? 'text-red-400' : t.deny_rate >= 0.2 ? 'text-amber-400' : 'text-neutral-400'
            const riskColor = t.avg_risk >= 0.7 ? 'text-red-400' : t.avg_risk >= 0.4 ? 'text-amber-400' : 'text-neutral-400'
            return (
              <tr key={t.tool} className={i % 2 === 0 ? '' : 'bg-white/[0.02]'}>
                <td className="px-4 py-2.5 font-mono text-white">{t.tool}</td>
                <td className="px-4 py-2.5 text-neutral-400 text-right">{t.total_calls.toLocaleString()}</td>
                <td className="px-4 py-2.5 text-right">
                  <span className={t.denied_calls > 0 ? 'text-red-400' : 'text-neutral-600'}>{t.denied_calls.toLocaleString()}</span>
                </td>
                <td className="px-4 py-2.5 text-right">
                  <span className={denyColor}>{denyPct}%</span>
                </td>
                <td className="px-4 py-2.5 text-right">
                  <span className={riskColor}>{riskPct}%</span>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

export default function PolicyAnalytics() {
  const [policies, setPolicies]       = useState([])
  const [kpis, setKpis]               = useState(null)
  const [chartData, setChartData]     = useState([])
  const [toolBreakdown, setToolBreakdown]   = useState(null)
  const [hourlyActivity, setHourlyActivity]   = useState(null)
  const [decisionTrend,  setDecisionTrend]   = useState(null)
  const [denyReasons,       setDenyReasons]      = useState(null)
  const [findingBreakdown,   setFindingBreakdown]   = useState(null)
  const [escalationRateTrend, setEscalationRateTrend] = useState(null)
  const [loading, setLoading]         = useState(true)
  const [refreshing, setRefreshing]   = useState(false)
  const [lastRefresh, setLastRefresh] = useState(null)
  const [liveTick, setLiveTick]       = useState(0)
  const liveTimerRef = useRef(null)

  const load = useCallback(async () => {
    setRefreshing(true)
    try {
      const [summaryRes, logsRes, toolRes, hourlyRes, trendRes, reasonsRes, findingRes, escRateRes] = await Promise.allSettled([
        auditService.getSummary(),
        auditService.getLogs(200, 0),
        auditService.getToolBreakdown(),
        auditService.getHourlyActivity(),
        auditService.getDecisionTrend(),
        auditService.getDenyReasons(),
        auditService.getFindingBreakdown(),
        auditService.getEscalationRateTrend(),
      ])

      let totalDecisions = 0, blocked = 0, allowed = 0

      if (summaryRes.status === 'fulfilled') {
        const s = summaryRes.value?.data || summaryRes.value || {}
        totalDecisions = s.total ?? 0
        blocked = s.blocked ?? 0
        allowed = s.allowed ?? 0
      }

      // Derive per-policy stats from audit logs
      const policyMap = {}
      if (logsRes.status === 'fulfilled') {
        const items = logsRes.value?.data?.items || logsRes.value?.data || []
        items.forEach(log => {
          const pname = log.policy_name || log.metadata?.policy_name || log.tool_name || 'default'
          if (!policyMap[pname]) {
            policyMap[pname] = { name: pname, triggered: 0, total: 0, false_positives: 0, description: '' }
          }
          policyMap[pname].total++
          if (log.action === 'deny' || log.action === 'block' || log.action === 'policy_deny') {
            policyMap[pname].triggered++
          }
        })
      }

      const pList = Object.values(policyMap).sort((a, b) => b.triggered - a.triggered)
      setPolicies(pList)

      const active = pList.filter(p => p.triggered > 0).length
      const unused = pList.filter(p => p.triggered === 0).length
      const blockRate = totalDecisions > 0 ? ((blocked / totalDecisions) * 100).toFixed(1) : '0.0'

      setKpis({ total: totalDecisions, blocked, allowed, active, unused, blockRate })

      // Chart: top 8 triggered policies
      setChartData(
        pList.slice(0, 8).map(p => ({
          name: p.name.length > 16 ? p.name.slice(0, 14) + '…' : p.name,
          Triggered: p.triggered,
          Allowed: p.total - p.triggered,
        }))
      )

      if (toolRes.status === 'fulfilled') {
        const td = toolRes.value?.data || toolRes.value || {}
        setToolBreakdown(td)
      }
      if (hourlyRes.status === 'fulfilled') {
        const ha = hourlyRes.value?.data || hourlyRes.value || {}
        setHourlyActivity(ha)
      }
      if (trendRes.status === 'fulfilled') {
        setDecisionTrend(trendRes.value?.data || trendRes.value || null)
      }
      if (reasonsRes.status === 'fulfilled') {
        setDenyReasons(reasonsRes.value?.data || reasonsRes.value || null)
      }
      if (findingRes.status === 'fulfilled') {
        setFindingBreakdown(findingRes.value?.data || findingRes.value || null)
      }
      if (escRateRes.status === 'fulfilled') {
        setEscalationRateTrend(escRateRes.value?.data || escRateRes.value || null)
      }

      setLastRefresh(new Date())
    } catch {}
    setRefreshing(false)
    setLoading(false)
  }, [])

  useEffect(() => {
    load()
    const id = setInterval(load, 30_000)
    return () => clearInterval(id)
  }, [load])

  // SSE — tick on every policy_decision so the analytics page reflects
  // live activity. Coalesce within a 2s window so a flurry of decisions
  // doesn't hammer the audit endpoints.
  useSSE({
    enabled: true,
    onMessage: (evt) => {
      const type = evt?.type
      if (type !== 'policy_decision' && type !== 'tool_executed' && type !== 'tool_execution') {
        return
      }
      setLiveTick((t) => t + 1)
      if (liveTimerRef.current) return
      liveTimerRef.current = setTimeout(() => {
        liveTimerRef.current = null
        load()
      }, 2_000)
    },
  })

  useEffect(() => () => {
    if (liveTimerRef.current) clearTimeout(liveTimerRef.current)
  }, [])

  if (loading) {
    return (
      <div className="max-w-6xl mx-auto space-y-6">
        <header>
          <h1 className="text-2xl font-semibold text-white mb-1">Policy Analytics</h1>
          <p className="text-sm text-neutral-400">Loading policy decisions…</p>
        </header>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3" aria-label="Loading KPIs">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="p-5 bg-white/[0.02] border border-white/[0.04] rounded-xl space-y-3 animate-pulse">
              <div className="h-2 bg-white/[0.06] rounded w-1/2" />
              <div className="h-6 bg-white/[0.08] rounded w-1/3" />
              <div className="h-2 bg-white/[0.04] rounded w-2/3" />
            </div>
          ))}
        </div>
        <SkeletonLoader variant="card" />
        <SkeletonLoader variant="row" count={5} />
      </div>
    )
  }

  const noisy  = policies.filter(p => p.triggered > 0 && p.false_positives / p.triggered > 0.15)
  const unused = policies.filter(p => p.triggered === 0)

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      <header className="flex items-start justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-white mb-1 flex items-center gap-3">
            Policy Analytics
            <span
              className="inline-flex items-center gap-1 text-[10px] uppercase tracking-widest text-neutral-500"
              title={`${liveTick} live event${liveTick === 1 ? '' : 's'} since mount`}
            >
              <span className={`w-1.5 h-1.5 rounded-full ${liveTick > 0 ? 'bg-green-400 animate-pulse' : 'bg-neutral-700'}`} />
              live
            </span>
          </h1>
          <p className="text-sm text-neutral-400">Hit rates, false positive rates, and coverage gaps across all policies.</p>
        </div>
        <div className="flex items-center gap-3 flex-wrap">
          {lastRefresh && (
            <span className="text-xs text-neutral-600 flex items-center gap-1">
              <Clock size={11} /> {lastRefresh.toLocaleTimeString()}
            </span>
          )}
          <button onClick={load} disabled={refreshing} className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[var(--border-subtle)] text-xs text-neutral-300 hover:border-white/20">
            <RefreshCw size={12} className={refreshing ? 'animate-spin' : ''} />
            Refresh
          </button>
          <Link to="/policies?tab=editor" className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white text-black text-xs font-medium hover:bg-neutral-200">
            Edit Policies <ArrowRight size={12} />
          </Link>
        </div>
      </header>

      {/* Empty-state CTA — no policy hits in current window */}
      {(kpis?.total ?? 0) === 0 && (
        <div className="rounded-xl border border-amber-500/20 bg-amber-500/[0.04] p-5 flex flex-col sm:flex-row sm:items-center gap-4">
          <div className="flex items-start gap-3 min-w-0 flex-1">
            <Activity size={18} className="text-amber-300 shrink-0 mt-0.5" aria-hidden="true" />
            <div className="min-w-0">
              <h2 className="text-sm font-semibold text-white">No policy hits yet</h2>
              <p className="text-xs text-neutral-400 mt-1">
                Generate sample traffic via the Agent Playground to populate hit-rate and FP analytics, or compose a new rule first.
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <Link
              to="/playground"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-amber-500/20 border border-amber-500/40 text-amber-100 text-xs font-medium hover:bg-amber-500/30 transition-colors"
            >
              <PlayCircle size={12} aria-hidden="true" />
              Open Agent Playground
            </Link>
            <Link
              to="/policies?tab=editor"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-white/10 text-neutral-300 text-xs hover:bg-white/[0.04] transition-colors"
            >
              <GitMerge size={12} aria-hidden="true" />
              Build a policy
            </Link>
          </div>
        </div>
      )}

      {/* KPIs */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <KpiCard icon={BarChart2}    label="Total Decisions"  value={(kpis?.total ?? 0).toLocaleString()} />
        <KpiCard icon={CheckCircle2} label="Active Policies"  value={kpis?.active ?? 0} accent="text-green-400" sub="triggered at least once" />
        <KpiCard icon={XCircle}      label="Unused Policies"  value={kpis?.unused ?? 0} accent={kpis?.unused > 0 ? 'text-amber-400' : 'text-neutral-400'} sub="never triggered" />
        <KpiCard icon={TrendingUp}   label="Block Rate"       value={`${kpis?.blockRate ?? '0.0'}%`} accent={parseFloat(kpis?.blockRate) > 20 ? 'text-red-400' : 'text-white'} />
      </div>

      {/* Alerts */}
      {(noisy.length > 0 || unused.length > 0) && (
        <div className="space-y-2">
          {noisy.map(p => (
            <div key={p.name} className="flex items-start gap-2 p-3 bg-amber-500/5 border border-amber-500/20 rounded-lg text-xs text-amber-400">
              <AlertTriangle size={13} className="shrink-0 mt-0.5" />
              <span><strong>{p.name}</strong> — false positive rate {((p.false_positives / p.triggered) * 100).toFixed(1)}%. Consider narrowing the rule conditions.</span>
            </div>
          ))}
          {unused.length > 0 && (
            <div className="flex items-start gap-2 p-3 bg-neutral-500/5 border border-neutral-500/20 rounded-lg text-xs text-neutral-400">
              <Info size={13} className="shrink-0 mt-0.5" />
              <span>{unused.length} {unused.length === 1 ? 'policy has' : 'policies have'} never triggered: {unused.slice(0, 3).map(p => p.name).join(', ')}{unused.length > 3 ? ` +${unused.length - 3} more` : ''}. Review if still needed.</span>
            </div>
          )}
        </div>
      )}

      {/* Bar chart */}
      <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl p-5">
        <h2 className="text-sm font-medium text-white mb-4 flex items-center gap-2">
          <Zap size={14} className="text-neutral-500" />
          Top Triggered Policies
        </h2>
        {chartData.length === 0 ? (
          <div
            className="h-48 flex flex-col items-center justify-center gap-2 px-4 text-center"
            role="status"
            aria-live="polite"
          >
            <p className="text-xs text-neutral-500">No data — run some traffic first.</p>
            <Link
              to="/policies?tab=simulator"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white text-black text-xs font-medium hover:bg-neutral-200 transition-colors"
            >
              <PlayCircle size={12} aria-hidden="true" />
              Run policy simulation
            </Link>
            <p className="text-[10px] text-neutral-600 max-w-xs leading-relaxed">
              Replays a synthetic request through your live policy stack so this chart and the breakdown table light up — usually under two seconds.
            </p>
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={chartData} layout="vertical" margin={{ left: 0, right: 16, top: 4, bottom: 4 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" horizontal={false} />
              <XAxis type="number" tick={{ fill: '#6b7280', fontSize: 10 }} axisLine={false} tickLine={false} />
              <YAxis type="category" dataKey="name" tick={{ fill: '#9ca3af', fontSize: 10 }} axisLine={false} tickLine={false} width={100} />
              <Tooltip content={<CustomTooltip />} />
              <Bar dataKey="Triggered" fill="#ef4444" radius={[0, 3, 3, 0]} maxBarSize={16} />
              <Bar dataKey="Allowed"   fill="#22c55e" radius={[0, 3, 3, 0]} maxBarSize={16} />
            </BarChart>
          </ResponsiveContainer>
        )}
        <div className="flex items-center gap-4 mt-2 text-[10px] text-neutral-600">
          <span className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-sm bg-red-500" />Blocked</span>
          <span className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-sm bg-green-500" />Allowed</span>
        </div>
      </div>

      {/* Policy table */}
      <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl overflow-hidden">
        <div className="px-5 py-4 border-b border-[var(--border-subtle)] flex items-center justify-between">
          <h2 className="text-sm font-medium text-white flex items-center gap-2">
            <GitMerge size={14} className="text-neutral-500" />
            Policy Breakdown
          </h2>
          <span className="text-xs text-neutral-500">{policies.length} policies</span>
        </div>
        {policies.length === 0 ? (
          <div className="px-5 py-8 text-center text-xs text-neutral-500 space-y-2">
            <p>No policy data in the current window.</p>
            <Link
              to="/playground"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-white/10 text-neutral-300 hover:bg-white/[0.04] transition-colors"
            >
              <PlayCircle size={11} aria-hidden="true" />
              Generate sample traffic
            </Link>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-[var(--border-subtle)]">
                  {['#', 'Policy', 'Triggers', 'Hit Rate', 'FP Rate', 'Status'].map((h, i) => (
                    <th key={h} className={`px-4 py-2.5 text-[10px] uppercase tracking-wider text-neutral-600 ${i > 1 && i < 5 ? 'text-right' : 'text-left'}`}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {policies.map((p, i) => <PolicyRow key={p.name} policy={p} rank={i} />)}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Tool Risk Breakdown */}
      <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl overflow-hidden">
        <div className="px-5 py-4 border-b border-[var(--border-subtle)] flex items-center justify-between">
          <h2 className="text-sm font-medium text-white flex items-center gap-2">
            <Wrench size={14} className="text-neutral-500" />
            Tool Risk Breakdown
            <span className="text-[10px] text-neutral-600 font-normal ml-1">last 30 days</span>
          </h2>
          {toolBreakdown?.total_tools != null && (
            <span className="text-xs text-neutral-500">{toolBreakdown.total_tools} tools</span>
          )}
        </div>
        <div className="px-1 py-2">
          <ToolBreakdownTable tools={toolBreakdown?.tools} />
        </div>
      </div>

      {/* Decision Outcome Trend */}
      <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl overflow-hidden">
        <div className="px-5 py-4 border-b border-[var(--border-subtle)] flex items-center justify-between">
          <h2 className="text-sm font-medium text-white flex items-center gap-2">
            <TrendingUp size={14} className="text-neutral-500" />
            Decision Outcome Trend
            <span className="text-[10px] text-neutral-600 font-normal ml-1">last 30 days · stacked</span>
          </h2>
          <div className="flex items-center gap-3">
            {Object.entries(DECISION_COLORS).map(([key, color]) => (
              <span key={key} className="flex items-center gap-1 text-[10px] text-neutral-500">
                <span className="w-2 h-2 rounded-sm inline-block" style={{ background: color }} />
                {key}
              </span>
            ))}
          </div>
        </div>
        <div className="px-4 py-4">
          <DecisionTrendChart series={decisionTrend?.series} />
        </div>
      </div>

      {/* Decision Velocity by Hour */}
      <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl overflow-hidden">
        <div className="px-5 py-4 border-b border-[var(--border-subtle)] flex items-center justify-between">
          <h2 className="text-sm font-medium text-white flex items-center gap-2">
            <Clock size={14} className="text-neutral-500" />
            Decision Velocity by Hour
            <span className="text-[10px] text-neutral-600 font-normal ml-1">last 7 days</span>
          </h2>
          <span className="text-[10px] text-neutral-600">right axis = avg risk</span>
        </div>
        <div className="px-4 py-4">
          <HourlyActivityChart buckets={hourlyActivity?.buckets} />
        </div>
      </div>

      {/* Top Deny Reasons */}
      <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl overflow-hidden">
        <div className="px-5 py-4 border-b border-[var(--border-subtle)] flex items-center justify-between">
          <h2 className="text-sm font-medium text-white flex items-center gap-2">
            <AlertTriangle size={14} className="text-neutral-500" />
            Top Deny Reasons
            <span className="text-[10px] text-neutral-600 font-normal ml-1">last 30 days · deny + kill</span>
          </h2>
          {denyReasons?.total_denied != null && (
            <span className="text-[10px] text-neutral-500">
              {denyReasons.total_denied.toLocaleString()} total
            </span>
          )}
        </div>
        <div className="px-5 py-4">
          <DenyReasonsChart reasons={denyReasons?.reasons} totalDenied={denyReasons?.total_denied} />
        </div>
      </div>

      {/* Finding Type Breakdown */}
      <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl">
        <div className="px-5 pt-5 pb-3 flex items-center justify-between">
          <h2 className="text-sm font-medium text-white">Finding Type Breakdown</h2>
          {findingBreakdown?.total != null && (
            <span className="text-[10px] text-neutral-600 font-mono">
              {findingBreakdown.total.toLocaleString()} total findings
            </span>
          )}
        </div>
        <div className="px-5 py-4">
          <FindingBreakdownChart findings={findingBreakdown?.findings} />
        </div>
      </div>

      {/* Escalation Rate Trend */}
      <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl">
        <div className="px-5 pt-5 pb-3 flex items-center justify-between">
          <h2 className="text-sm font-medium text-white">Escalation Rate Trend</h2>
          <div className="flex items-center gap-3 text-[10px] font-mono">
            {escalationRateTrend?.avg_rate != null && (
              <span className="text-neutral-500">avg {escalationRateTrend.avg_rate}%</span>
            )}
            {escalationRateTrend?.peak_rate != null && (
              <span className="text-purple-400">peak {escalationRateTrend.peak_rate}%</span>
            )}
          </div>
        </div>
        <div className="px-5 py-4">
          <EscalationRateTrendChart series={escalationRateTrend?.series} />
        </div>
      </div>
    </div>
  )
}
