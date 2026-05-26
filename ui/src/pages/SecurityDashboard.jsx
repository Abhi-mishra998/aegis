import React, { useState, useEffect, useRef, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { Shield, AlertTriangle, Activity, BarChart2, RefreshCw, Eye, Zap, CheckCircle2, XCircle, Info } from 'lucide-react'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell,
  ComposedChart, AreaChart, Area, Line, LineChart, CartesianGrid, ReferenceLine,
} from 'recharts'
import Card from '../components/Common/Card'
import SkeletonLoader from '../components/Common/SkeletonLoader'
import { auditService, riskService, incidentService, securityService } from '../services/api'
import { eventBus } from '../lib/eventBus'

const DECISION_META = {
  allow:    'text-green-400  bg-green-500/10  border-green-500/20',
  monitor:  'text-blue-400   bg-blue-500/10   border-blue-500/20',
  throttle: 'text-amber-400  bg-amber-500/10  border-amber-500/20',
  escalate: 'text-purple-400 bg-purple-500/10 border-purple-500/20',
  deny:     'text-red-400    bg-red-500/10    border-red-500/20',
  kill:     'text-red-300    bg-red-900/20    border-red-700/30',
}

function DecisionBadge({ decision }) {
  const d = (decision ?? 'unknown').toLowerCase()
  return (
    <span className={`status-badge ${DECISION_META[d] ?? 'text-neutral-400 bg-white/5 border-white/10'}`}>
      {d.toUpperCase()}
    </span>
  )
}

function RiskBar({ score }) {
  const pct   = Math.min(100, Math.round((score ?? 0) * 100))
  const color = pct >= 70 ? 'bg-red-500' : pct >= 40 ? 'bg-amber-500' : 'bg-green-500'
  return (
    <div className="flex items-center gap-2 min-w-[80px]">
      <div className="flex-1 h-1 bg-white/[0.06] rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs text-neutral-500 tabular-nums w-8 text-right">
        {(score ?? 0).toFixed(2)}
      </span>
    </div>
  )
}

const RISK_COLORS = {
  CRITICAL: '#ef4444',
  HIGH:     '#f97316',
  MEDIUM:   '#eab308',
  MONITOR:  '#3b82f6',
  LOW:      '#22c55e',
}

/* ── Anomaly Heatmap ─────────────────────────────────────────────────────────── */
function AnomalyHeatmap({ logs }) {
  const navigate = useNavigate()

  const TOOLS = ['read_file', 'write_file', 'delete_file', 'execute_command', 'network_request', 'database_query']

  const agentIds = [...new Set(logs.map(l => l.agent_id).filter(Boolean))].slice(0, 8)

  const cells = {}
  for (const log of logs) {
    const tool = log.tool || 'unknown'
    const aid  = log.agent_id
    if (!aid) continue
    const key = `${aid}:${tool}`
    if (!cells[key]) cells[key] = { count: 0, totalRisk: 0 }
    cells[key].count++
    cells[key].totalRisk += log.metadata_json?.risk_score ?? log.risk_score ?? 0
  }

  const getRisk = (agentId, tool) => {
    const c = cells[`${agentId}:${tool}`]
    if (!c || c.count === 0) return null
    return c.totalRisk / c.count
  }

  const getCellColor = (risk) => {
    if (risk === null) return 'bg-white/[0.02] border-white/[0.04]'
    if (risk >= 0.7)   return 'bg-red-500/30 border-red-500/40'
    if (risk >= 0.4)   return 'bg-amber-500/25 border-amber-500/35'
    return 'bg-green-500/15 border-green-500/25'
  }

  if (agentIds.length === 0) return (
    <div className="flex items-center justify-center h-32 text-xs text-neutral-600">
      Insufficient data for heatmap
    </div>
  )

  return (
    <div className="overflow-x-auto">
      <div className="min-w-[480px]">
        {/* X-axis: agents */}
        <div className="flex mb-1 ml-24">
          {agentIds.map(aid => (
            <div key={aid} className="flex-1 text-center">
              <button
                onClick={() => navigate(`/forensics?agent=${aid}`)}
                className="text-[9px] font-mono text-neutral-600 hover:text-blue-400 transition-colors truncate max-w-full inline-block"
                title={`Investigate ${aid}`}
                aria-label={`Investigate agent ${aid.slice(0, 8)}`}
              >
                {aid.slice(0, 8)}
              </button>
            </div>
          ))}
        </div>

        {/* Rows: tools */}
        {TOOLS.map(tool => (
          <div key={tool} className="flex items-center mb-1">
            <div className="w-24 text-[9px] font-mono text-neutral-600 truncate shrink-0 pr-2 text-right">
              {tool}
            </div>
            {agentIds.map(aid => {
              const risk = getRisk(aid, tool)
              const c = cells[`${aid}:${tool}`]
              return (
                <div key={aid} className="flex-1 px-0.5">
                  <button
                    onClick={() => navigate(`/forensics?agent=${aid}`)}
                    className={`
                      w-full h-7 rounded border text-[8px] font-bold tabular-nums
                      transition-all hover:scale-105 hover:z-10 relative
                      ${getCellColor(risk)}
                    `}
                    title={risk !== null ? `Risk: ${risk.toFixed(2)}, Count: ${c?.count}` : 'No data'}
                    aria-label={`Agent ${aid.slice(0, 8)}, tool ${tool}, risk ${risk?.toFixed(2) ?? 'N/A'}`}
                  >
                    {risk !== null ? risk.toFixed(1) : ''}
                  </button>
                </div>
              )
            })}
          </div>
        ))}

        {/* Legend */}
        <div className="flex items-center gap-4 mt-3 ml-24">
          <div className="flex items-center gap-1.5">
            <div className="w-3 h-3 rounded bg-green-500/15 border border-green-500/25" aria-hidden="true" />
            <span className="text-[10px] text-neutral-600">Low (&lt;0.4)</span>
          </div>
          <div className="flex items-center gap-1.5">
            <div className="w-3 h-3 rounded bg-amber-500/25 border border-amber-500/35" aria-hidden="true" />
            <span className="text-[10px] text-neutral-600">Medium (0.4–0.7)</span>
          </div>
          <div className="flex items-center gap-1.5">
            <div className="w-3 h-3 rounded bg-red-500/30 border border-red-500/40" aria-hidden="true" />
            <span className="text-[10px] text-neutral-600">High (&gt;0.7)</span>
          </div>
        </div>
      </div>
    </div>
  )
}

/* ── Posture Score Trend ─────────────────────────────────────────────────── */
function PostureScoreTrendChart({ series, avgScore }) {
  if (!series || series.length === 0) return (
    <div className="flex items-center justify-center h-32 text-neutral-600 text-xs">No decision data in window</div>
  )
  const ticks = series.filter((_, i) => i % Math.ceil(series.length / 6) === 0).map(d => d.date)
  const scoreColor = avgScore == null ? '#6366f1' : avgScore >= 90 ? '#22c55e' : avgScore >= 70 ? '#f59e0b' : '#ef4444'
  return (
    <ResponsiveContainer width="100%" height={140}>
      <LineChart data={series} margin={{ top: 8, right: 16, bottom: 8, left: -16 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
        <XAxis dataKey="date" ticks={ticks} tick={{ fontSize: 9, fill: '#525252' }} tickFormatter={d => d.slice(5)} />
        <YAxis domain={[0, 100]} tick={{ fontSize: 9, fill: '#525252' }} tickFormatter={v => `${v}%`} />
        <ReferenceLine y={90} stroke="#22c55e" strokeDasharray="4 3" strokeOpacity={0.4} />
        <Tooltip
          contentStyle={{ background: '#1a1a1a', border: '1px solid #333', borderRadius: 8, fontSize: 11 }}
          labelStyle={{ color: '#999' }}
          formatter={(v) => [v != null ? `${v}%` : '—', 'Posture Score']}
        />
        <Line
          type="monotone"
          dataKey="posture_score"
          stroke={scoreColor}
          strokeWidth={2}
          dot={false}
          connectNulls
          name="Posture Score"
        />
      </LineChart>
    </ResponsiveContainer>
  )
}

/* ── Daily Active Agents Chart ───────────────────────────────────────────── */
function DailyActiveAgentsChart({ series }) {
  if (!series || series.length === 0) return (
    <div className="flex items-center justify-center h-32 text-neutral-600 text-xs">No agent activity in window</div>
  )
  const ticks = series.filter((_, i) => i % Math.ceil(series.length / 6) === 0).map(d => d.date)
  return (
    <ResponsiveContainer width="100%" height={120}>
      <AreaChart data={series} margin={{ top: 4, right: 8, bottom: 0, left: -20 }}>
        <defs>
          <linearGradient id="activeAgentsGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%"  stopColor="#6366f1" stopOpacity={0.3} />
            <stop offset="95%" stopColor="#6366f1" stopOpacity={0.02} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
        <XAxis dataKey="date" ticks={ticks} tick={{ fontSize: 9, fill: '#525252' }} tickFormatter={d => d.slice(5)} />
        <YAxis allowDecimals={false} tick={{ fontSize: 9, fill: '#525252' }} />
        <Tooltip
          contentStyle={{ background: '#1a1a1a', border: '1px solid #333', borderRadius: 8, fontSize: 11 }}
          labelStyle={{ color: '#999' }}
          formatter={(v, name) => [v, name === 'active_agents' ? 'Active Agents' : 'Total Calls']}
        />
        <Area type="monotone" dataKey="active_agents" stroke="#6366f1" fill="url(#activeAgentsGrad)" strokeWidth={1.5} dot={false} />
      </AreaChart>
    </ResponsiveContainer>
  )
}

/* ── Agent Activity Summary Table ───────────────────────────────────────── */
function AgentActivityTable({ agents, navigate }) {
  if (!agents || agents.length === 0) return (
    <div className="flex items-center justify-center h-24 text-neutral-600 text-xs">No agent data</div>
  )

  const riskColor = (r) => r >= 0.7 ? 'text-red-400' : r >= 0.4 ? 'text-amber-400' : 'text-green-400'
  const denyColor = (d) => d >= 30 ? 'text-red-400' : d >= 10 ? 'text-amber-400' : 'text-neutral-400'

  const fmtDate = (iso) => {
    if (!iso) return '—'
    const d = new Date(iso)
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
  }

  return (
    <div className="table-scroll -mx-5">
      <table className="table-base min-w-[640px]" role="table">
        <thead>
          <tr>
            {['Agent', 'First Seen', 'Last Seen', 'Calls', 'Deny Rate', 'Avg Risk', ''].map(h => (
              <th key={h} className="table-th first:pl-5">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {agents.map((a) => (
            <tr key={a.agent_id} className="table-row">
              <td className="table-td first:pl-5 font-mono text-xs">
                <button
                  onClick={() => navigate(`/forensics?agent=${a.agent_id}`)}
                  className="hover:text-blue-400 transition-colors"
                  aria-label={`Investigate ${a.agent_id.slice(0, 8)}`}
                >
                  {a.agent_id.slice(0, 12)}
                </button>
              </td>
              <td className="table-td text-neutral-500 text-xs">{fmtDate(a.first_seen)}</td>
              <td className="table-td text-neutral-400 text-xs">{fmtDate(a.last_seen)}</td>
              <td className="table-td text-xs tabular-nums">{a.total_calls.toLocaleString()}</td>
              <td className={`table-td text-xs tabular-nums font-medium ${denyColor(a.deny_rate)}`}>
                {a.deny_rate.toFixed(1)}%
              </td>
              <td className={`table-td text-xs tabular-nums font-medium ${riskColor(a.avg_risk)}`}>
                {a.avg_risk.toFixed(3)}
              </td>
              <td className="table-td pr-4">
                <button
                  onClick={() => navigate(`/forensics?agent=${a.agent_id}`)}
                  className="text-[10px] text-neutral-600 hover:text-blue-400 transition-colors"
                  aria-label={`Investigate ${a.agent_id.slice(0, 8)}`}
                >
                  <Eye size={12} aria-hidden="true" />
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/* ── Weekly Activity Heatmap ─────────────────────────────────────────────── */
function WeeklyHeatmap({ cells, maxCount }) {
  if (!cells || cells.length === 0) return (
    <div className="flex items-center justify-center h-24 text-neutral-600 text-xs">
      Insufficient data for weekly heatmap
    </div>
  )

  const DAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
  const cellMap = new Map(cells.map(c => [`${c.day}:${c.hour}`, c]))

  const getCellColor = (pct) => {
    if (pct === 0)   return 'bg-white/[0.02] border-white/[0.04]'
    if (pct >= 75)   return 'bg-indigo-500/50 border-indigo-500/60'
    if (pct >= 50)   return 'bg-indigo-500/30 border-indigo-500/40'
    if (pct >= 25)   return 'bg-indigo-500/15 border-indigo-500/25'
    return 'bg-indigo-500/07 border-indigo-500/15'
  }

  return (
    <div className="overflow-x-auto">
      <div className="min-w-[520px]">
        {/* Hour axis labels */}
        <div className="flex mb-1 ml-10">
          {Array.from({ length: 24 }, (_, h) => (
            <div key={h} className="flex-1 text-center text-[8px] text-neutral-700 font-mono">
              {h % 6 === 0 ? (h === 0 ? '12a' : h < 12 ? `${h}a` : h === 12 ? '12p' : `${h - 12}p`) : ''}
            </div>
          ))}
        </div>

        {DAY_LABELS.map((day, d) => (
          <div key={day} className="flex items-center mb-0.5">
            <div className="w-10 text-[9px] font-mono text-neutral-600 shrink-0 pr-1.5 text-right">{day}</div>
            {Array.from({ length: 24 }, (_, h) => {
              const cell = cellMap.get(`${d}:${h}`)
              return (
                <div key={h} className="flex-1 px-px">
                  <div
                    className={`w-full h-5 rounded-sm border ${getCellColor(cell?.pct ?? 0)}`}
                    title={cell?.count ? `${day} ${h}:00 — ${cell.count} requests` : 'No data'}
                  />
                </div>
              )
            })}
          </div>
        ))}

        {/* Legend */}
        <div className="flex items-center gap-4 mt-3 ml-10">
          {[['bg-white/[0.02] border-white/[0.04]', 'None'], ['bg-indigo-500/07 border-indigo-500/15', '<25%'], ['bg-indigo-500/15 border-indigo-500/25', '25–50%'], ['bg-indigo-500/30 border-indigo-500/40', '50–75%'], ['bg-indigo-500/50 border-indigo-500/60', '>75%']].map(([cls, label]) => (
            <div key={label} className="flex items-center gap-1.5">
              <div className={`w-3 h-3 rounded-sm border ${cls}`} aria-hidden="true" />
              <span className="text-[10px] text-neutral-600">{label}</span>
            </div>
          ))}
          {maxCount != null && (
            <span className="text-[10px] text-neutral-700 ml-auto">peak: {maxCount.toLocaleString()} req</span>
          )}
        </div>
      </div>
    </div>
  )
}

/* ── Tenant 30-Day Security Trend ────────────────────────────────────────── */
function TenantTrendChart({ data }) {
  if (!data || data.length === 0) return (
    <div className="flex items-center justify-center h-40 text-neutral-600 text-xs">
      No trend data available
    </div>
  )

  const formatted = data.map(d => ({
    ...d,
    label: d.date ? d.date.slice(5) : '',
  }))

  return (
    <ResponsiveContainer width="100%" height={200}>
      <ComposedChart data={formatted} margin={{ top: 4, right: 8, bottom: 0, left: -20 }}>
        <defs>
          <linearGradient id="totalGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%"  stopColor="#6366f1" stopOpacity={0.25} />
            <stop offset="95%" stopColor="#6366f1" stopOpacity={0.02} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke="rgba(255,255,255,0.04)" vertical={false} />
        <XAxis
          dataKey="label"
          tick={{ fill: '#525252', fontSize: 10 }}
          axisLine={false}
          tickLine={false}
          interval={Math.floor(formatted.length / 6)}
        />
        <YAxis
          yAxisId="left"
          tick={{ fill: '#525252', fontSize: 10 }}
          axisLine={false}
          tickLine={false}
        />
        <YAxis
          yAxisId="right"
          orientation="right"
          domain={[0, 1]}
          tick={{ fill: '#525252', fontSize: 10 }}
          axisLine={false}
          tickLine={false}
          tickFormatter={v => v.toFixed(1)}
        />
        <Tooltip
          contentStyle={{ background: '#111', border: '1px solid rgba(255,255,255,0.06)', borderRadius: 8, fontSize: 11 }}
          labelStyle={{ color: '#fff', fontWeight: 600 }}
          itemStyle={{ color: '#a3a3a3' }}
        />
        <Area
          yAxisId="left"
          type="monotone"
          dataKey="count"
          name="Total Decisions"
          stroke="#6366f1"
          strokeWidth={1.5}
          fill="url(#totalGrad)"
          dot={false}
        />
        <Line
          yAxisId="left"
          type="monotone"
          dataKey="threats"
          name="Threats"
          stroke="#ef4444"
          strokeWidth={1.5}
          dot={false}
        />
        <Line
          yAxisId="right"
          type="monotone"
          dataKey="avg_risk"
          name="Avg Risk"
          stroke="#f97316"
          strokeWidth={1.5}
          strokeDasharray="3 2"
          dot={false}
        />
      </ComposedChart>
    </ResponsiveContainer>
  )
}

/* ── Component ─────────────────────────────────────────────────────────────── */
export default function SecurityDashboard() {
  const navigate    = useNavigate()
  const mounted     = useRef(true)

  const [summary,         setSummary]         = useState(null)
  const [logs,            setLogs]            = useState([])
  const [threats,         setThreats]         = useState([])
  const [incidentSummary, setIncidentSummary] = useState(null)
  const [posture,         setPosture]         = useState(null)
  const [trends,          setTrends]          = useState([])
  const [weeklyHeatmap,   setWeeklyHeatmap]   = useState(null)
  const [agentActivity,       setAgentActivity]       = useState(null)
  const [dailyActiveAgents,   setDailyActiveAgents]   = useState(null)
  const [postureScoreTrend,   setPostureScoreTrend]   = useState(null)
  const [loading,         setLoading]         = useState(true)
  const [error,           setError]           = useState(null)
  const [lastRefresh,     setLastRefresh]     = useState(null)

  const load = useCallback(async () => {
    try {
      const [sumRes, logsRes, threatRes, incRes, postureRes, trendsRes, weeklyRes, agentRes, daaRes, pstRes] = await Promise.allSettled([
        auditService.getSummary(),
        auditService.getLogs(30, 0),
        riskService.getTopThreats(),
        incidentService.getSummary(),
        securityService.getPosture(),
        auditService.getAnomalyTrends(30),
        auditService.getWeeklyHeatmap(28),
        auditService.getAgentActivity(),
        auditService.getDailyActiveAgents(),
        auditService.getPostureScoreTrend(),
      ])
      if (!mounted.current) return

      if (sumRes.status    === 'fulfilled') setSummary(sumRes.value?.data || sumRes.value)
      if (logsRes.status   === 'fulfilled') setLogs(logsRes.value?.data?.items || logsRes.value?.items || [])
      if (threatRes.status === 'fulfilled') setThreats(threatRes.value?.data || threatRes.value || [])
      if (incRes.status    === 'fulfilled') setIncidentSummary(incRes.value?.data || incRes.value || null)
      if (postureRes.status === 'fulfilled') setPosture(postureRes.value?.data || postureRes.value || null)
      if (trendsRes.status === 'fulfilled') {
        const t = trendsRes.value?.data || trendsRes.value || []
        setTrends(Array.isArray(t) ? t : [])
      }
      if (weeklyRes.status === 'fulfilled') {
        setWeeklyHeatmap(weeklyRes.value?.data || weeklyRes.value || null)
      }
      if (agentRes.status === 'fulfilled') {
        setAgentActivity(agentRes.value?.data || agentRes.value || null)
      }
      if (daaRes.status === 'fulfilled') {
        setDailyActiveAgents(daaRes.value?.data || daaRes.value || null)
      }
      if (pstRes.status === 'fulfilled') {
        setPostureScoreTrend(pstRes.value?.data || pstRes.value || null)
      }

      setLastRefresh(new Date())
      setError(null)
    } catch (err) {
      if (mounted.current) setError(err.message)
    } finally {
      if (mounted.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    mounted.current = true
    load()
    // 30-second polling fallback in addition to SSE events
    const interval = setInterval(load, 30_000)
    let debounceTimer = null
    const trigger = () => { clearTimeout(debounceTimer); debounceTimer = setTimeout(load, 2_000) }
    const unsubRisk  = eventBus.on('risk_updated',    trigger)
    const unsubTool  = eventBus.on('tool_executed',   trigger)
    const unsubAlert = eventBus.on('policy_decision', trigger)
    return () => {
      mounted.current = false
      clearInterval(interval)
      clearTimeout(debounceTimer)
      unsubRisk()
      unsubTool()
      unsubAlert()
    }
  }, [load])

  if (loading) return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {[...Array(4)].map((_, i) => <SkeletonLoader key={i} variant="card" />)}
      </div>
      <SkeletonLoader variant="card" />
      <SkeletonLoader variant="card" />
    </div>
  )

  if (error && !summary) return (
    <div className="flex flex-col items-center gap-4 py-20">
      <AlertTriangle className="text-red-400" size={28} aria-hidden="true" />
      <p className="text-sm text-neutral-400">Failed to load security data: {error}</p>
      <button onClick={load} className="px-4 py-2 text-xs font-medium text-white bg-white/[0.04] border border-white/[0.08] rounded-lg hover:bg-white/[0.08] transition-colors">
        Retry
      </button>
    </div>
  )

  const riskDist = summary?.risk_distribution
    ? Object.entries(summary.risk_distribution).map(([name, value]) => ({
        name, value, fill: RISK_COLORS[name] ?? '#6b7280',
      }))
    : []

  const alerts = logs.filter((l) => ['deny', 'kill', 'escalate'].includes((l.decision ?? '').toLowerCase()))

  return (
    <div className="space-y-6 animate-fade-in">
      {/* Header */}
      <div className="page-header">
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight">Security Operations</h1>
          <p className="text-xs text-neutral-500 mt-0.5">Real-time threat monitoring — SSE-powered</p>
        </div>
        <div className="flex items-center gap-3">
          {lastRefresh && (
            <span className="text-xs text-neutral-600 font-mono" aria-live="polite">
              Synced {lastRefresh.toLocaleTimeString()}
            </span>
          )}
          <button
            onClick={load}
            aria-label="Refresh security data"
            className="p-2 rounded-lg text-neutral-500 hover:text-white hover:bg-white/[0.05] transition-colors"
          >
            <RefreshCw size={15} aria-hidden="true" />
          </button>
        </div>
      </div>

      {/* KPI cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <Card title="Total Requests"  value={summary?.total_calls?.toLocaleString() ?? '—'} icon={Activity} subtitle="All decisions" />
        <Card title="Threats Blocked" value={(summary?.threats_blocked ?? summary?.total_denials)?.toLocaleString() ?? '—'} icon={Shield} subtitle="Deny + Kill actions" />
        <Card title="Active Agents"   value={summary?.active_agents_count?.toLocaleString() ?? '—'} icon={Eye} subtitle="Currently monitored" />
        <Card title="Avg Risk Score"  value={summary?.avg_risk_score != null ? summary.avg_risk_score.toFixed(3) : '—'} icon={BarChart2} subtitle="Across all requests" />
      </div>

      {/* Security Posture Checklist */}
      {posture && (
        <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl overflow-hidden">
          <div className="px-4 py-3 border-b border-[var(--border-subtle)] flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Shield size={13} className="text-neutral-500" />
              <h2 className="text-xs font-medium text-white">Security Posture</h2>
              <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full border ${
                posture.posture_score >= 80 ? 'text-green-400 bg-green-500/10 border-green-500/20' :
                posture.posture_score >= 60 ? 'text-amber-400 bg-amber-500/10 border-amber-500/20' :
                'text-red-400 bg-red-500/10 border-red-500/20'
              }`}>{posture.posture_score}/100</span>
            </div>
            <span className="text-[10px] text-neutral-600">Chain: {posture.chain_status || 'unknown'}</span>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-0 divide-y sm:divide-y-0 sm:divide-x divide-[var(--border-subtle)]">
            {(posture.items || []).map((item) => {
              const Icon = item.status === 'ok' ? CheckCircle2 : item.status === 'warning' ? AlertTriangle : item.status === 'error' ? XCircle : Info
              const color = item.status === 'ok' ? 'text-green-400' : item.status === 'warning' ? 'text-amber-400' : item.status === 'error' ? 'text-red-400' : 'text-blue-400'
              return (
                <div key={item.label} className="flex items-start gap-3 px-4 py-3">
                  <Icon size={13} className={`${color} shrink-0 mt-0.5`} />
                  <div className="min-w-0">
                    <div className="text-xs font-medium text-white">{item.label}</div>
                    <div className="text-[11px] text-neutral-500 mt-0.5">{item.detail}</div>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Security posture widget */}
      {incidentSummary && (() => {
        const score   = Number(incidentSummary.security_score ?? 100)
        const crit    = Number(incidentSummary.critical ?? 0)
        const high    = Number(incidentSummary.high     ?? 0)
        const open    = Number(incidentSummary.open     ?? 0)
        const mttr    = Number(incidentSummary.mttr_hours ?? 0).toFixed(1)
        const color   = score >= 80 ? 'text-green-400' : score >= 60 ? 'text-amber-400' : 'text-red-400'
        const ringCls = score >= 80 ? 'stroke-green-500' : score >= 60 ? 'stroke-amber-500' : 'stroke-red-500'
        const r = 22; const circ = 2 * Math.PI * r; const dash = (score / 100) * circ
        return (
          <div className={`flex items-center gap-4 p-4 rounded-xl border ${score >= 80 ? 'border-green-500/15 bg-green-500/[0.04]' : score >= 60 ? 'border-amber-500/15 bg-amber-500/[0.04]' : 'border-red-500/15 bg-red-500/[0.04]'}`}>
            <svg className="-rotate-90 shrink-0" width="56" height="56" viewBox="0 0 56 56">
              <circle cx="28" cy="28" r={r} fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth="5" />
              <circle cx="28" cy="28" r={r} fill="none" className={ringCls} strokeWidth="5"
                strokeDasharray={`${dash} ${circ}`} strokeLinecap="round" />
            </svg>
            <div className="flex-1 min-w-0">
              <div className="flex items-baseline gap-2">
                <span className={`text-2xl font-bold ${color}`}>{score}</span>
                <span className="text-xs text-neutral-500">/ 100 security score</span>
              </div>
              <div className="flex gap-4 mt-1.5 text-xs">
                {crit > 0 && <span className="text-red-400">{crit} critical</span>}
                {high > 0 && <span className="text-orange-400">{high} high</span>}
                <span className="text-neutral-500">{open} open · {mttr}h MTTR</span>
              </div>
            </div>
            <button onClick={() => navigate('/incidents')} className="text-xs text-neutral-500 hover:text-white transition-colors flex items-center gap-1 shrink-0">
              View Incidents <Zap size={11} />
            </button>
          </div>
        )
      })()}

      {/* Active threat alerts */}
      {alerts.length > 0 && (
        <div className="p-4 bg-red-500/[0.05] border border-red-500/15 rounded-xl space-y-3" role="alert">
          <div className="flex items-center gap-2">
            <AlertTriangle size={13} className="text-red-400" aria-hidden="true" />
            <span className="text-xs font-bold text-red-400 uppercase tracking-wide">
              Active Threat Alerts ({alerts.length})
            </span>
          </div>
          <div className="space-y-2">
            {alerts.slice(0, 5).map((a, i) => (
              <div key={i} className="flex items-center gap-3 text-xs font-mono flex-wrap">
                <DecisionBadge decision={a.decision} />
                <button
                  onClick={() => a.agent_id && navigate(`/forensics?agent=${a.agent_id}`)}
                  className="text-neutral-400 hover:text-blue-400 transition-colors font-mono"
                  aria-label={`Investigate agent ${a.agent_id?.slice(0, 8)}`}
                >
                  {a.agent_id?.slice(0, 8) ?? '—'}
                </button>
                <span className="flex-1 text-neutral-400 truncate min-w-0">{a.tool ?? 'unknown'}</span>
                <RiskBar score={a.metadata_json?.risk_score ?? 0} />
                <span className="text-neutral-600 text-xs">
                  {a.timestamp ? new Date(a.timestamp).toLocaleTimeString() : '—'}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Enforcement coverage (Gap 4) */}
      {summary && (() => {
        const total    = summary.total_calls || 0
        const blocked  = (summary.threats_blocked ?? summary.total_denials ?? 0) + (summary.total_kills ?? 0)
        const coverage = total > 0 ? Math.min(100, ((blocked / total) * 100).toFixed(1)) : 0
        const allowed  = summary.total_allowed ?? summary.total_allows ?? 0
        const evalRate = total > 0 ? Math.min(100, (((blocked + allowed) / total) * 100).toFixed(1)) : 0
        return (
          <div className="grid grid-cols-3 gap-3">
            <div className="p-3 bg-white/[0.02] border border-white/[0.06] rounded-xl">
              <p className="text-[10px] text-neutral-500 mb-1 font-medium uppercase tracking-wide">Enforcement Rate</p>
              <p className="text-xl font-bold text-white">{coverage}%</p>
              <p className="text-[10px] text-neutral-600 mt-0.5">{blocked.toLocaleString()} blocked of {total.toLocaleString()}</p>
            </div>
            <div className="p-3 bg-white/[0.02] border border-white/[0.06] rounded-xl">
              <p className="text-[10px] text-neutral-500 mb-1 font-medium uppercase tracking-wide">Decision Coverage</p>
              <p className="text-xl font-bold text-green-400">{evalRate}%</p>
              <p className="text-[10px] text-neutral-600 mt-0.5">all requests evaluated</p>
            </div>
            <div className="p-3 bg-white/[0.02] border border-white/[0.06] rounded-xl">
              <p className="text-[10px] text-neutral-500 mb-1 font-medium uppercase tracking-wide">Avg Risk Score</p>
              <p className="text-xl font-bold text-amber-400">{summary.avg_risk_score?.toFixed(3) ?? '—'}</p>
              <p className="text-[10px] text-neutral-600 mt-0.5">across all decisions</p>
            </div>
          </div>
        )
      })()}

      {/* Charts row */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card title="Risk Distribution" icon={BarChart2}>
          {riskDist.length > 0 ? (
            <ResponsiveContainer width="100%" height={180}>
              <BarChart data={riskDist} margin={{ top: 4, right: 4, bottom: 0, left: -24 }}>
                <XAxis dataKey="name" tick={{ fill: '#525252', fontSize: 11 }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fill: '#525252', fontSize: 11 }} axisLine={false} tickLine={false} />
                <Tooltip
                  contentStyle={{ background: '#111', border: '1px solid rgba(255,255,255,0.06)', borderRadius: 8, fontSize: 12 }}
                  labelStyle={{ color: '#fff', fontWeight: 600 }}
                  itemStyle={{ color: '#a3a3a3' }}
                />
                <Bar dataKey="value" radius={[3, 3, 0, 0]}>
                  {riskDist.map((entry, i) => <Cell key={i} fill={entry.fill} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <div className="flex items-center justify-center h-40 text-neutral-600 text-xs">No risk data</div>
          )}
        </Card>

        <Card title="Top Threat Agents" icon={AlertTriangle}>
          {threats.length > 0 ? (
            <div className="space-y-2">
              {threats.slice(0, 6).map((t, i) => (
                <div
                  key={i}
                  className="flex items-center gap-3 p-2.5 rounded-lg bg-white/[0.02] border border-white/[0.04] hover:border-white/[0.07] transition-colors"
                >
                  <span className="w-5 h-5 rounded bg-red-500/10 flex items-center justify-center text-xs font-bold text-red-400 shrink-0">
                    {i + 1}
                  </span>
                  <div className="flex-1 min-w-0">
                    <button
                      onClick={() => t.agent_id && navigate(`/forensics?agent=${t.agent_id}`)}
                      className="text-xs font-mono text-white hover:text-blue-400 transition-colors truncate block text-left"
                      aria-label={`Investigate agent ${t.agent_id}`}
                    >
                      {t.agent_id?.slice(0, 16) ?? '—'}
                    </button>
                    <p className="text-xs text-neutral-600">
                      {t.threat_count ?? 0} threats · last {t.last_seen ? new Date(t.last_seen).toLocaleDateString() : '—'}
                    </p>
                  </div>
                  <RiskBar score={t.avg_risk ?? 0} />
                </div>
              ))}
            </div>
          ) : (
            <div className="flex items-center justify-center h-32 text-neutral-600 text-xs">No threat data</div>
          )}
        </Card>
      </div>

      {/* Anomaly Heatmap */}
      <Card title="Anomaly Heatmap" icon={Zap}>
        <p className="text-xs text-neutral-600 mb-4">
          Agent × Tool risk matrix — click any cell to investigate. Higher intensity = higher average risk.
        </p>
        <AnomalyHeatmap logs={logs} />
      </Card>

      {/* Weekly Activity Heatmap */}
      <Card title="Weekly Activity Pattern (28 days)" icon={Zap}>
        <p className="text-xs text-neutral-600 mb-4">
          Request volume by day-of-week × hour — darker = more activity relative to peak.
        </p>
        <WeeklyHeatmap cells={weeklyHeatmap?.cells} maxCount={weeklyHeatmap?.max_count} />
      </Card>

      {/* 30-Day Security Trend */}
      <Card title="30-Day Security Trend" icon={Activity}>
        <p className="text-xs text-neutral-600 mb-4">
          Daily decisions (area), threats detected (red line), and avg risk score (orange dashed — right axis).
        </p>
        <TenantTrendChart data={trends} />
      </Card>

      {/* Live decision log */}
      <Card title="Live Decision Log" icon={Activity}>
        <div className="table-scroll -mx-5">
          <table className="table-base min-w-[640px]" role="table">
            <thead>
              <tr>
                {['Time', 'Agent', 'Tool', 'Decision', 'Risk', 'Reason', ''].map((h) => (
                  <th key={h} className="table-th first:pl-5">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {logs.length === 0 ? (
                <tr>
                  <td colSpan={7} className="py-10 text-center text-xs text-neutral-600">No log entries</td>
                </tr>
              ) : (
                logs.slice(0, 15).map((log, i) => (
                  <tr key={i} className="table-row">
                    <td className="table-td first:pl-5 font-mono text-xs whitespace-nowrap">
                      {log.timestamp ? new Date(log.timestamp).toLocaleTimeString() : '—'}
                    </td>
                    <td className="table-td font-mono">
                      <button
                        onClick={() => log.agent_id && navigate(`/forensics?agent=${log.agent_id}`)}
                        className="hover:text-blue-400 transition-colors"
                        aria-label={`Investigate ${log.agent_id?.slice(0, 8)}`}
                      >
                        {log.agent_id?.slice(0, 8) ?? '—'}
                      </button>
                    </td>
                    <td className="table-td max-w-[100px] truncate">{log.tool ?? '—'}</td>
                    <td className="table-td"><DecisionBadge decision={log.decision} /></td>
                    <td className="table-td w-28"><RiskBar score={log.metadata_json?.risk_score ?? 0} /></td>
                    <td className="table-td max-w-[150px] truncate text-neutral-500">{log.reason ?? '—'}</td>
                    <td className="table-td pr-4">
                      {log.agent_id && (
                        <button
                          onClick={() => navigate(`/forensics?agent=${log.agent_id}`)}
                          className="text-[10px] text-neutral-600 hover:text-blue-400 transition-colors"
                          aria-label={`Investigate agent ${log.agent_id?.slice(0, 8)}`}
                        >
                          <Eye size={12} aria-hidden="true" />
                        </button>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </Card>

      {/* Posture Score Trend */}
      <Card title="Tenant Posture Score (30 days)" icon={Shield}>
        <div className="flex items-center justify-between mb-2">
          <p className="text-xs text-neutral-600">Allow % of all decisions · dashed line = 90% target</p>
          {postureScoreTrend?.avg_score != null && (
            <span className={`text-sm font-mono font-bold ${
              postureScoreTrend.avg_score >= 90 ? 'text-green-400' :
              postureScoreTrend.avg_score >= 70 ? 'text-amber-400' : 'text-red-400'
            }`}>
              {postureScoreTrend.avg_score}% avg
            </span>
          )}
        </div>
        <PostureScoreTrendChart series={postureScoreTrend?.series} avgScore={postureScoreTrend?.avg_score} />
      </Card>

      {/* Daily Active Agents */}
      <Card title="Daily Active Agents (30 days)" icon={Activity}>
        <DailyActiveAgentsChart series={dailyActiveAgents?.series} />
        {dailyActiveAgents?.peak_agents != null && (
          <p className="text-[10px] text-neutral-700 mt-2">
            Peak: {dailyActiveAgents.peak_agents} agents in one day
          </p>
        )}
      </Card>

      {/* Agent Activity Summary */}
      <Card title="Agent Activity Registry" icon={Eye}>
        <p className="text-xs text-neutral-600 mb-4">
          All known agents — ordered by most recently active. Click any row to investigate.
        </p>
        <AgentActivityTable agents={agentActivity?.agents} navigate={navigate} />
        {agentActivity?.total != null && (
          <p className="text-[10px] text-neutral-700 mt-3">
            {agentActivity.total} agents shown
          </p>
        )}
      </Card>
    </div>
  )
}
