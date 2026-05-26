import React, { useState, useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { Zap, AlertTriangle, TrendingUp, Brain, RefreshCw, ExternalLink } from 'lucide-react'
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, BarChart, Bar, Cell } from 'recharts'
import Card from '../components/Common/Card'
import SkeletonLoader from '../components/Common/SkeletonLoader'
import { riskService, auditService } from '../services/api'
import { useAuth } from '../hooks/useAuth'
import { eventBus } from '../lib/eventBus'

/* ── Risk badge ────────────────────────────────────────────────────────────── */
const RISK_LEVEL_STYLES = {
  CRITICAL: 'text-red-400    bg-red-500/10    border-red-500/20',
  HIGH:     'text-orange-400 bg-orange-500/10 border-orange-500/20',
  MEDIUM:   'text-amber-400  bg-amber-500/10  border-amber-500/20',
  MONITOR:  'text-blue-400   bg-blue-500/10   border-blue-500/20',
  LOW:      'text-green-400  bg-green-500/10  border-green-500/20',
}

function RiskBadge({ level }) {
  const l = (level ?? 'MONITOR').toUpperCase()
  return (
    <span className={`status-badge ${RISK_LEVEL_STYLES[l] ?? RISK_LEVEL_STYLES.MONITOR}`}>
      {l}
    </span>
  )
}

/* ── Confidence color ──────────────────────────────────────────────────────── */
const CONF_COLOR = { HIGH: 'text-green-400', MEDIUM: 'text-amber-400', LOW: 'text-red-400' }

/* ── Weight table ──────────────────────────────────────────────────────────── */
const WEIGHTS = [
  { label: 'Inference',   key: 'inference',   pct: 35, color: '#ef4444' },
  { label: 'Behavior',    key: 'behavior',    pct: 30, color: '#f97316' },
  { label: 'Anomaly',     key: 'anomaly',     pct: 15, color: '#eab308' },
  { label: 'Cost',        key: 'cost',        pct: 10, color: '#3b82f6' },
  { label: 'Cross-Agent', key: 'cross_agent', pct: 10, color: '#8b5cf6' },
]

/* ── Top Findings bar chart (inline SVG) ──────────────────────────────────── */
const DECISION_BADGE = {
  deny:     'text-red-400    bg-red-500/10    border-red-500/20',
  kill:     'text-red-300    bg-red-900/20    border-red-700/30',
  escalate: 'text-purple-400 bg-purple-500/10 border-purple-500/20',
  monitor:  'text-blue-400   bg-blue-500/10   border-blue-500/20',
  allow:    'text-green-400  bg-green-500/10  border-green-500/20',
}

function RiskPercentileTrendChart({ series }) {
  if (!series || series.length === 0) return (
    <div className="flex items-center justify-center h-32 text-neutral-600 text-xs">No scored events in window</div>
  )
  const fmt = (v) => v != null ? v.toFixed(2) : '—'
  const ticks = series.filter((_, i) => i % Math.ceil(series.length / 6) === 0).map(d => d.date)
  return (
    <ResponsiveContainer width="100%" height={160}>
      <LineChart data={series} margin={{ top: 8, right: 16, bottom: 8, left: -16 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
        <XAxis dataKey="date" ticks={ticks} tick={{ fontSize: 9, fill: '#525252' }} tickFormatter={d => d.slice(5)} />
        <YAxis domain={[0, 1]} tick={{ fontSize: 9, fill: '#525252' }} tickFormatter={v => v.toFixed(1)} />
        <Tooltip
          contentStyle={{ background: '#1a1a1a', border: '1px solid #333', borderRadius: 8, fontSize: 11 }}
          labelStyle={{ color: '#999' }}
          formatter={(v, name) => [fmt(v), name.toUpperCase()]}
        />
        <Line type="monotone" dataKey="p50" stroke="#6366f1" strokeWidth={1.5} dot={false} connectNulls name="p50" />
        <Line type="monotone" dataKey="p75" stroke="#f59e0b" strokeWidth={1.5} dot={false} connectNulls name="p75" />
        <Line type="monotone" dataKey="p95" stroke="#ef4444" strokeWidth={1.5} dot={false} connectNulls name="p95" />
      </LineChart>
    </ResponsiveContainer>
  )
}

function ToolRiskLeaderboard({ toolRisk }) {
  const tools = toolRisk?.tools || []
  if (tools.length === 0) return (
    <div className="flex items-center justify-center h-16 text-neutral-600 text-xs">No tool data in window</div>
  )
  const maxDeny = Math.max(...tools.map(t => t.deny_count), 1)
  return (
    <div className="space-y-2">
      {tools.map((t, i) => (
        <div key={i} className="space-y-1">
          <div className="flex items-center justify-between text-xs">
            <span className="font-mono text-neutral-300 truncate max-w-[220px]">{t.tool}</span>
            <div className="flex items-center gap-3 shrink-0 text-[10px] font-mono">
              <span className="text-neutral-500">{t.calls} calls</span>
              <span className={t.deny_rate > 50 ? 'text-red-400' : t.deny_rate > 20 ? 'text-amber-400' : 'text-neutral-400'}>
                {t.deny_rate.toFixed(1)}% denied
              </span>
              <span className={t.avg_risk > 0.7 ? 'text-red-400' : t.avg_risk > 0.4 ? 'text-amber-400' : 'text-neutral-500'}>
                risk {t.avg_risk.toFixed(2)}
              </span>
              <span className="text-neutral-600">{t.agent_count} agent{t.agent_count !== 1 ? 's' : ''}</span>
            </div>
          </div>
          <div className="h-1 bg-white/[0.04] rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full transition-all duration-500 ${t.deny_rate > 50 ? 'bg-red-500' : t.deny_rate > 20 ? 'bg-amber-500' : 'bg-indigo-500'}`}
              style={{ width: `${Math.round(t.deny_count / maxDeny * 100)}%` }}
            />
          </div>
        </div>
      ))}
    </div>
  )
}

function HighRiskEventFeed({ events, navigate }) {
  if (!events || events.length === 0) return (
    <div className="flex items-center justify-center h-24 text-neutral-600 text-xs">
      No high-risk events in window
    </div>
  )

  return (
    <div className="space-y-2">
      {events.map((ev, i) => {
        const pct = Math.min(100, Math.round((ev.risk_score ?? 0) * 100))
        const barColor = pct >= 90 ? 'bg-red-500' : pct >= 70 ? 'bg-orange-500' : 'bg-amber-500'
        const d = (ev.decision ?? 'unknown').toLowerCase()
        const badgeCls = DECISION_BADGE[d] ?? 'text-neutral-400 bg-white/5 border-white/10'
        return (
          <div key={ev.id ?? i}
            className="flex items-start gap-3 p-3 rounded-lg bg-white/[0.02] border border-white/[0.05] hover:border-white/[0.08] transition-colors">
            {/* Risk score bar */}
            <div className="flex flex-col items-center gap-1 shrink-0 w-10">
              <span className={`text-sm font-bold tabular-nums ${pct >= 90 ? 'text-red-400' : 'text-orange-400'}`}>
                {ev.risk_score?.toFixed(2)}
              </span>
              <div className="w-full h-1 bg-white/[0.06] rounded-full overflow-hidden">
                <div className={`h-full rounded-full ${barColor}`} style={{ width: `${pct}%` }} />
              </div>
            </div>
            {/* Event details */}
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded border ${badgeCls}`}>
                  {d.toUpperCase()}
                </span>
                {ev.agent_id && (
                  <button
                    onClick={() => navigate(`/forensics?agent=${ev.agent_id}`)}
                    className="text-xs font-mono text-neutral-400 hover:text-blue-400 transition-colors truncate max-w-[120px]"
                    aria-label={`Investigate agent ${ev.agent_id.slice(0, 8)}`}
                  >
                    {ev.agent_id.slice(0, 12)}
                  </button>
                )}
                {ev.tool && (
                  <span className="text-xs text-neutral-500 truncate">{ev.tool}</span>
                )}
              </div>
              {ev.findings && ev.findings.length > 0 && (
                <div className="flex flex-wrap gap-1 mt-1.5">
                  {ev.findings.map((f, fi) => (
                    <span key={fi}
                      className="text-[9px] px-1.5 py-0.5 rounded bg-red-500/10 text-red-400 border border-red-500/20 font-mono">
                      {String(f).replace(/_/g, ' ')}
                    </span>
                  ))}
                </div>
              )}
            </div>
            {/* Timestamp */}
            <span className="text-[10px] text-neutral-600 font-mono shrink-0">
              {ev.timestamp ? new Date(ev.timestamp).toLocaleTimeString() : '—'}
            </span>
          </div>
        )
      })}
    </div>
  )
}

function RiskHistogram({ buckets }) {
  if (!buckets || buckets.length === 0) return (
    <div className="flex items-center justify-center h-40 text-neutral-600 text-xs">No distribution data</div>
  )

  const getColor = (low) => {
    if (low >= 0.7) return '#ef4444'
    if (low >= 0.4) return '#f97316'
    return '#22c55e'
  }

  const formatted = buckets.map(b => ({ ...b, fill: getColor(b.low) }))

  return (
    <ResponsiveContainer width="100%" height={160}>
      <BarChart data={formatted} margin={{ top: 4, right: 4, bottom: 0, left: -24 }}>
        <XAxis dataKey="bin" tick={{ fill: '#525252', fontSize: 9 }} axisLine={false} tickLine={false} />
        <YAxis tick={{ fill: '#525252', fontSize: 10 }} axisLine={false} tickLine={false} />
        <Tooltip
          contentStyle={{ background: '#111', border: '1px solid rgba(255,255,255,0.06)', borderRadius: 8, fontSize: 11 }}
          labelStyle={{ color: '#fff', fontWeight: 600 }}
          itemStyle={{ color: '#a3a3a3' }}
          formatter={(v) => [v.toLocaleString(), 'Events']}
        />
        <Bar dataKey="count" radius={[2, 2, 0, 0]}>
          {formatted.map((b, i) => <Cell key={i} fill={b.fill} />)}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}

function TopFindingsChart({ findings }) {
  if (!findings?.length) {
    return <p className="text-xs text-neutral-600 py-2">No findings recorded in window.</p>
  }
  const max = Math.max(...findings.map(f => f.count), 1)
  return (
    <div className="space-y-2">
      {findings.map((f, i) => {
        const pct = Math.round((f.count / max) * 100)
        const color = i < 3 ? '#ef4444' : i < 7 ? '#f97316' : '#6366f1'
        const label = f.finding.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
        return (
          <div key={f.finding} className="flex items-center gap-3">
            <span className="text-[10px] text-neutral-400 w-44 truncate shrink-0" title={label}>{label}</span>
            <div className="flex-1 h-1.5 bg-white/[0.05] rounded-full overflow-hidden">
              <div className="h-full rounded-full transition-all duration-500" style={{ width: `${pct}%`, background: color }} />
            </div>
            <span className="text-[10px] font-mono text-neutral-500 w-10 text-right shrink-0">{f.count.toLocaleString()}</span>
          </div>
        )
      })}
    </div>
  )
}

/* ── Component ─────────────────────────────────────────────────────────────── */
export default function RiskEngine() {
  // Page is ProtectedRoute-gated, but other pages also explicitly read auth so
  // an unmounted context cannot silently return a stale tenant on the first
  // render. Calling useAuth() ensures the context is established before any
  // request fires from the effect below.
  useAuth()
  const navigate = useNavigate()
  const mounted  = useRef(true)

  const [summary,      setSummary]     = useState(null)
  const [timeline,     setTimeline]    = useState([])
  const [threats,      setThreats]     = useState([])
  const [insights,     setInsights]    = useState([])
  const [topFindings,   setTopFindings]  = useState(null)
  const [riskHistogram,  setRiskHistogram]  = useState(null)
  const [highRiskEvents, setHighRiskEvents] = useState(null)
  const [toolRisk,           setToolRisk]           = useState(null)
  const [riskPercentileTrend, setRiskPercentileTrend] = useState(null)
  const [loading,      setLoading]     = useState(true)
  const [error,        setError]       = useState(null)
  const [lastRefresh,  setLastRefresh] = useState(null)

  const load = async () => {
    try {
      const [sumRes, timeRes, threatRes, insightRes, findingsRes, histRes, highRes, toolRiskRes, pctTrendRes] = await Promise.allSettled([
        riskService.getSummary(),
        riskService.getTimeline(),
        riskService.getTopThreats(),
        riskService.getInsights(),
        auditService.getTopFindings(),
        auditService.getRiskHistogram(),
        auditService.getHighRiskEvents(),
        auditService.getToolRisk(),
        auditService.getRiskPercentileTrend(),
      ])
      if (!mounted.current) return

      if (sumRes.status     === 'fulfilled') setSummary(sumRes.value?.data || sumRes.value)
      if (timeRes.status    === 'fulfilled') setTimeline(timeRes.value?.data || timeRes.value || [])
      if (threatRes.status  === 'fulfilled') setThreats(threatRes.value?.data || threatRes.value || [])
      if (insightRes.status === 'fulfilled') {
        const ins = insightRes.value
        const list = ins?.data?.insights || (Array.isArray(ins?.data) ? ins.data : null) || ins?.insights || []
        setInsights(Array.isArray(list) ? list : [])
      }
      if (findingsRes.status === 'fulfilled') {
        setTopFindings(findingsRes.value?.data || findingsRes.value)
      }
      if (histRes.status === 'fulfilled') {
        setRiskHistogram(histRes.value?.data || histRes.value)
      }
      if (highRes.status === 'fulfilled') {
        setHighRiskEvents(highRes.value?.data || highRes.value)
      }
      if (toolRiskRes.status === 'fulfilled') {
        setToolRisk(toolRiskRes.value?.data || toolRiskRes.value)
      }
      if (pctTrendRes.status === 'fulfilled') {
        setRiskPercentileTrend(pctTrendRes.value?.data || pctTrendRes.value)
      }

      setLastRefresh(new Date())
      setError(null)
    } catch (err) {
      if (mounted.current) setError(err.message)
    } finally {
      if (mounted.current) setLoading(false)
    }
  }

  useEffect(() => {
    mounted.current = true
    load()
    const interval = setInterval(load, 30_000)
    return () => { mounted.current = false; clearInterval(interval) }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Trigger an immediate refresh when live decisions arrive via SSE.
  // Debounced to 2s so a burst of events causes a single load, not N.
  useEffect(() => {
    let debounceTimer = null
    const trigger = () => {
      clearTimeout(debounceTimer)
      debounceTimer = setTimeout(load, 2_000)
    }
    const u1 = eventBus.on('policy_decision', trigger)
    const u2 = eventBus.on('tool_executed', trigger)
    return () => { clearTimeout(debounceTimer); u1(); u2() }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  if (loading) return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        {[...Array(3)].map((_, i) => <SkeletonLoader key={i} variant="card" />)}
      </div>
      <SkeletonLoader variant="card" />
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <SkeletonLoader variant="card" />
        <SkeletonLoader variant="card" />
      </div>
    </div>
  )

  const highRisk = threats.filter((t) => (t.avg_risk ?? 0) >= 0.7)

  return (
    <div className="space-y-6 animate-fade-in">
      {/* ── Header ── */}
      <div className="page-header">
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight">Risk Engine</h1>
          <p className="text-xs text-neutral-500 mt-0.5">Weighted risk scoring, AI insights, and threat intelligence</p>
        </div>
        <div className="flex items-center gap-3">
          {lastRefresh && (
            <span className="text-xs text-neutral-600 font-mono" aria-live="polite">
              Synced {lastRefresh.toLocaleTimeString()}
            </span>
          )}
          <button
            onClick={load}
            aria-label="Refresh risk data"
            className="p-2 rounded-lg text-neutral-500 hover:text-white hover:bg-white/[0.05] transition-colors"
          >
            <RefreshCw size={15} aria-hidden="true" />
          </button>
        </div>
      </div>

      {/* ── Error ── */}
      {error && (
        <div className="error-banner" role="alert">
          <div className="flex items-center gap-2">
            <AlertTriangle size={14} className="text-red-400 shrink-0" aria-hidden="true" />
            <p className="text-xs text-red-400">{error}</p>
          </div>
          <button onClick={load} className="text-xs text-red-400 underline">Retry</button>
        </div>
      )}

      {/* ── Summary KPIs ── */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <Card
          title="Threats Blocked"
          value={summary?.threats_blocked?.toLocaleString() ?? '—'}
          icon={Zap}
          subtitle="Total blocked actions"
        />
        <Card
          title="High Risk Agents"
          value={summary?.high_risk_agents?.toLocaleString() ?? '—'}
          icon={AlertTriangle}
          subtitle="Score ≥ 0.70"
        />
        <Card
          title="Avg Risk Score"
          value={summary?.avg_risk_score != null ? summary.avg_risk_score.toFixed(3) : '—'}
          icon={TrendingUp}
          subtitle="Rolling average"
        />
      </div>

      {/* ── Timeline chart — Risk + Executions Behavioral Flow ── */}
      <Card title="Behavioral Flow — 7-Day Risk & Execution Trend" icon={TrendingUp}>
        {(() => {
          // 2026-05-13: Pad single-day data so AreaChart renders a slope.
          const padded =
            timeline.length === 1
              ? [
                  { date: 'prev', count: 0, threats: 0, avg_risk: 0 },
                  ...timeline.map((t) => ({
                    ...t,
                    // Pretty short label for the X axis
                    date: typeof t.date === 'string' && t.date.includes('T')
                      ? new Date(t.date).toLocaleDateString('en-US', { weekday: 'short' })
                      : t.date,
                  })),
                ]
              : timeline.map((t) => ({
                  ...t,
                  date: typeof t.date === 'string' && t.date.includes('T')
                    ? new Date(t.date).toLocaleDateString('en-US', { weekday: 'short' })
                    : t.date,
                }))
          return padded.length > 0 ? (
            <ResponsiveContainer width="100%" height={240}>
              <LineChart data={padded} margin={{ top: 8, right: 24, bottom: 8, left: -16 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.03)" />
                <XAxis
                  dataKey="date"
                  tick={{ fill: '#525252', fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  yAxisId="left"
                  orientation="left"
                  domain={[0, 1]}
                  tick={{ fill: '#525252', fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                  tickFormatter={(v) => v.toFixed(1)}
                />
                <YAxis
                  yAxisId="right"
                  orientation="right"
                  tick={{ fill: '#525252', fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                  tickFormatter={(v) =>
                    v >= 1000 ? `${(v / 1000).toFixed(1)}k` : String(v)
                  }
                />
                <Tooltip
                  contentStyle={{
                    background: '#111',
                    border: '1px solid rgba(255,255,255,0.06)',
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                  labelStyle={{ color: '#fff' }}
                />
                <Line
                  yAxisId="left"
                  type="monotone"
                  dataKey="avg_risk"
                  stroke="#ef4444"
                  strokeWidth={2}
                  dot={{ fill: '#ef4444', r: 3 }}
                  name="Avg Risk"
                />
                <Line
                  yAxisId="right"
                  type="monotone"
                  dataKey="count"
                  stroke="#3b82f6"
                  strokeWidth={2}
                  dot={{ fill: '#3b82f6', r: 3 }}
                  name="Executions"
                />
                <Line
                  yAxisId="right"
                  type="monotone"
                  dataKey="threats"
                  stroke="#f97316"
                  strokeWidth={2}
                  dot={{ fill: '#f97316', r: 3 }}
                  name="Threats"
                />
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <div className="flex items-center justify-center h-40 text-neutral-600 text-xs">
              No timeline data
            </div>
          )
        })()}
        <div className="flex items-center gap-4 mt-2 text-[10px] font-mono text-neutral-500">
          <span className="inline-flex items-center gap-1">
            <span className="w-2 h-0.5 bg-red-500" /> avg risk (0–1, left)
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="w-2 h-0.5 bg-blue-500" /> executions (right)
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="w-2 h-0.5 bg-orange-500" /> threats (right)
          </span>
        </div>
      </Card>

      {/* ── Agents + formula ── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* High risk agents */}
        <Card title={`High Risk Agents (≥ 0.70)`} icon={AlertTriangle}>
          {highRisk.length > 0 ? (
            <div className="space-y-2">
              {highRisk.slice(0, 8).map((t, i) => (
                <div
                  key={i}
                  className="flex items-center gap-3 p-2.5 rounded-lg bg-white/[0.02] border border-white/[0.04] group"
                >
                  <div className="flex-1 min-w-0">
                    <p className="text-xs font-mono text-white truncate">{t.agent_id?.slice(0, 18) ?? '—'}</p>
                    <p className="text-xs text-neutral-600 mt-0.5">{t.threat_count ?? 0} events</p>
                  </div>
                  <RiskBadge level={
                    t.risk_level ??
                    ((t.avg_risk ?? 0) >= 0.9 ? 'CRITICAL' : (t.avg_risk ?? 0) >= 0.7 ? 'HIGH' : 'MEDIUM')
                  } />
                  <button
                    onClick={() => navigate(`/forensics?agent=${t.agent_id}`)}
                    aria-label={`View forensics for agent ${t.agent_id?.slice(0, 8)}`}
                    className="p-1.5 rounded text-neutral-600 hover:text-white hover:bg-white/[0.05] transition-colors opacity-0 group-hover:opacity-100"
                  >
                    <ExternalLink size={12} aria-hidden="true" />
                  </button>
                </div>
              ))}
            </div>
          ) : (
            <div className="flex items-center justify-center h-32 text-neutral-600 text-xs">
              No high-risk agents detected
            </div>
          )}
        </Card>

        {/* Risk formula */}
        <Card title="Risk Scoring Formula" icon={Brain}>
          <div className="space-y-3">
            {WEIGHTS.map((w) => (
              <div key={w.key} className="space-y-1.5">
                <div className="flex justify-between">
                  <span className="text-xs text-neutral-400">{w.label}</span>
                  <span className="text-xs font-bold" style={{ color: w.color }}>{w.pct}%</span>
                </div>
                <div className="h-1.5 bg-white/[0.05] rounded-full overflow-hidden">
                  <div
                    className="h-full rounded-full"
                    style={{ width: `${w.pct}%`, backgroundColor: w.color }}
                    role="progressbar"
                    aria-valuenow={w.pct}
                    aria-valuemin={0}
                    aria-valuemax={100}
                  />
                </div>
              </div>
            ))}
            <div className="pt-3 border-t border-white/[0.05] space-y-1 font-mono text-xs text-neutral-600">
              <p>Signal ≥ 0.95 → floor score at 0.95</p>
              <p>Policy DENY → floor score at 0.70</p>
              <p>Learning discount: up to −0.20 (FP rate)</p>
            </div>
          </div>
        </Card>
      </div>

      {/* ── Top Security Findings ── */}
      <Card title="Top Security Findings (30 days)" icon={AlertTriangle}>
        <TopFindingsChart findings={topFindings?.findings} />
        {topFindings?.total_events != null && (
          <p className="text-[10px] text-neutral-700 mt-3">
            {topFindings.total_events.toLocaleString()} finding events · last {topFindings.days} days
          </p>
        )}
      </Card>

      {/* ── Risk Score Distribution ── */}
      <Card title="Risk Score Distribution (30 days)" icon={TrendingUp}>
        <p className="text-xs text-neutral-600 mb-4">
          Frequency of risk scores across all decisions — green (&lt;0.4), orange (0.4–0.7), red (&gt;0.7).
        </p>
        <RiskHistogram buckets={riskHistogram?.buckets} />
        {riskHistogram?.total != null && (
          <p className="text-[10px] text-neutral-700 mt-3">
            {riskHistogram.total.toLocaleString()} scored events · last {riskHistogram.days} days
          </p>
        )}
      </Card>

      {/* ── AI Insights — always render so operators always see the panel ── */}
      {/* ── High Risk Event Feed ── */}
      <Card title="High Risk Event Feed (7 days, score ≥ 0.7)" icon={AlertTriangle}>
        <HighRiskEventFeed events={highRiskEvents?.events} navigate={navigate} />
        {highRiskEvents?.events != null && (
          <p className="text-[10px] text-neutral-700 mt-3">
            {highRiskEvents.events.length} events · threshold {highRiskEvents.threshold}
          </p>
        )}
      </Card>

      {/* ── Tool Risk Leaderboard ── */}
      <Card title="Tool Risk Leaderboard (30 days)" icon={AlertTriangle}>
        <ToolRiskLeaderboard toolRisk={toolRisk} />
        {toolRisk?.tools != null && (
          <p className="text-[10px] text-neutral-700 mt-3">
            {toolRisk.tools.length} tools · last {toolRisk.days} days
          </p>
        )}
      </Card>

      {/* ── Risk Score Percentile Trend ── */}
      <Card title="Risk Score Percentile Trend (30 days)" icon={AlertTriangle}>
        <div className="flex items-center gap-4 mb-3">
          {[['p50','#6366f1'], ['p75','#f59e0b'], ['p95','#ef4444']].map(([label, color]) => (
            <div key={label} className="flex items-center gap-1.5 text-[10px] text-neutral-500">
              <div className="w-5 h-px" style={{ background: color }} />
              {label.toUpperCase()}
            </div>
          ))}
        </div>
        <RiskPercentileTrendChart series={riskPercentileTrend?.series} />
      </Card>

      <Card title="AI Threat Insights (Groq)" icon={Brain}>
        {insights.length > 0 ? (
          <div className="space-y-3">
            {insights.slice(0, 5).map((ins, i) => (
              <div key={i} className="p-4 rounded-xl bg-white/[0.02] border border-white/[0.05] space-y-2 hover:border-white/[0.08] transition-colors">
                <div className="flex items-center justify-between gap-3 flex-wrap">
                  <span className="text-xs font-bold text-white uppercase tracking-wide">
                    {ins.threat_classification ?? ins.source ?? 'Unknown'}
                  </span>
                  <span className={`text-xs font-bold ${CONF_COLOR[(ins.confidence ?? '').toUpperCase()] ?? 'text-neutral-400'}`}>
                    {ins.confidence ?? '—'} Confidence
                  </span>
                </div>
                <p className="text-xs text-neutral-400 leading-relaxed">
                  {ins.narrative ?? ins.recommendation ?? ins.summary ?? '—'}
                </p>
                {ins.root_cause && (
                  <p className="text-xs text-neutral-600">
                    Root cause: {ins.root_cause}
                  </p>
                )}
                {ins.agent_id && (
                  <p className="text-[10px] font-mono text-neutral-600">
                    agent: {String(ins.agent_id).slice(0, 18)}
                    {ins.tool ? ` · tool: ${ins.tool}` : ''}
                  </p>
                )}
              </div>
            ))}
          </div>
        ) : (
          <div className="flex flex-col items-center gap-2 py-6 text-neutral-500 text-xs">
            <Brain size={20} className="text-neutral-700" aria-hidden="true" />
            <p>No AI-generated threat insights yet.</p>
            <p className="text-[10px] text-neutral-600">
              Insights appear here as the Groq worker analyzes recent high-risk decisions.
            </p>
          </div>
        )}
      </Card>
    </div>
  )
}
