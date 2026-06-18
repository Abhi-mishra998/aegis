import React, { useEffect, useState, useCallback, useMemo } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import {
  ArrowLeft, Bot, ShieldAlert, CheckCircle2, Activity,
  TrendingUp, AlertTriangle, RefreshCw, Clock, Zap,
  BarChart2, GitMerge, ExternalLink,
} from 'lucide-react'
import {
  LineChart, Line, BarChart, Bar, Cell, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine,
} from 'recharts'
import { registryService, auditService } from '../services/api'

const DRIFT_LEVEL_STYLE = {
  low:      { color: 'text-green-400',  bg: 'bg-green-500/10',  border: 'border-green-500/20' },
  medium:   { color: 'text-amber-400',  bg: 'bg-amber-500/10',  border: 'border-amber-500/20' },
  high:     { color: 'text-orange-400', bg: 'bg-orange-500/10', border: 'border-orange-500/20' },
  critical: { color: 'text-red-400',    bg: 'bg-red-500/10',    border: 'border-red-500/20'   },
}

function DriftBar({ label, value }) {
  const pct = Math.round(Math.min(1, value) * 100)
  const color = pct >= 70 ? '#ef4444' : pct >= 45 ? '#f97316' : pct >= 20 ? '#f59e0b' : '#22c55e'
  return (
    <div className="flex items-center gap-3">
      <span className="text-[10px] text-neutral-500 w-24 capitalize">{label.replace('_drift', '').replace('_', ' ')}</span>
      <div className="flex-1 h-1.5 bg-white/[0.05] rounded-full overflow-hidden">
        <div className="h-full rounded-full transition-all duration-500" style={{ width: `${pct}%`, background: color }} />
      </div>
      <span className="text-[10px] font-mono text-neutral-400 w-8 text-right">{(value * 100).toFixed(0)}%</span>
    </div>
  )
}

/* ── Peer Benchmark ─────────────────────────────────────────────────────────── */
const BENCHMARK_METRICS = [
  { key: 'deny_rate',   label: 'Deny Rate',    refKey: 'deny_rate',   fmt: v => (v * 100).toFixed(1) + '%' },
  { key: 'avg_risk',    label: 'Avg Risk',     refKey: 'avg_risk',    fmt: v => (v * 100).toFixed(1) + '%' },
  { key: 'call_volume', label: 'Call Volume',  refKey: 'call_volume', fmt: v => v.toLocaleString() },
]

const AGENT_FINDING_COLORS = {
  policy_violation:     '#ef4444',
  pii_detected:         '#f97316',
  prompt_injection:     '#a855f7',
  data_exfiltration:    '#ec4899',
  credential_exposure:  '#f59e0b',
  privilege_escalation: '#eab308',
  anomalous_behavior:   '#6366f1',
  rate_limit_exceeded:  '#14b8a6',
}

function AgentFindingFrequency({ agentFindings }) {
  const findings = agentFindings?.findings || []
  if (findings.length === 0) return (
    <div className="flex items-center justify-center h-20 text-neutral-600 text-xs">No findings recorded in this window</div>
  )
  const maxCount = Math.max(...findings.map(f => f.count), 1)
  return (
    <div className="space-y-2">
      {findings.map((f, i) => {
        const color = AGENT_FINDING_COLORS[f.finding] || '#6366f1'
        return (
          <div key={i} className="space-y-0.5">
            <div className="flex items-center justify-between text-xs">
              <span className="font-mono truncate max-w-[200px]" style={{ color }}>{f.finding}</span>
              <div className="flex items-center gap-2 shrink-0 text-[10px] font-mono ml-2">
                <span className="text-neutral-300">{f.count}</span>
                <span className="text-neutral-600 w-9 text-right">{f.pct}%</span>
              </div>
            </div>
            <div className="h-1 bg-white/[0.04] rounded-full overflow-hidden">
              <div
                className="h-full rounded-full transition-all duration-500"
                style={{ width: `${Math.round(f.count / maxCount * 100)}%`, background: color }}
              />
            </div>
          </div>
        )
      })}
    </div>
  )
}

function AgentDailyDecisionsChart({ series }) {
  if (!series || series.length === 0) return (
    <div className="flex items-center justify-center h-28 text-neutral-600 text-xs">No decision data in window</div>
  )
  const ticks = series.filter((_, i) => i % Math.ceil(series.length / 6) === 0).map(d => d.date)
  return (
    <ResponsiveContainer width="100%" height={110}>
      <BarChart data={series} margin={{ top: 4, right: 8, bottom: 0, left: -20 }} barSize={4}>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" vertical={false} />
        <XAxis dataKey="date" ticks={ticks} tick={{ fontSize: 9, fill: '#525252' }} tickFormatter={d => d.slice(5)} />
        <YAxis allowDecimals={false} tick={{ fontSize: 9, fill: '#525252' }} />
        <Tooltip
          contentStyle={{ background: '#1a1a1a', border: '1px solid #333', borderRadius: 8, fontSize: 11 }}
          labelStyle={{ color: '#999' }}
        />
        <Bar dataKey="allow" stackId="d" fill="#22c55e" name="Allow" />
        <Bar dataKey="deny"  stackId="d" fill="#ef4444" name="Deny/Kill" />
      </BarChart>
    </ResponsiveContainer>
  )
}

function AgentToolUsageTable({ toolUsage }) {
  const tools = toolUsage?.tools || []
  if (tools.length === 0) {
    return <div className="text-xs text-neutral-600">No tool activity recorded in this window.</div>
  }
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="text-[10px] uppercase tracking-wider text-neutral-600 border-b border-white/[0.06]">
          <th className="text-left pb-2 font-medium">Tool</th>
          <th className="text-right pb-2 font-medium">Calls</th>
          <th className="text-right pb-2 font-medium">Deny Rate</th>
          <th className="text-right pb-2 font-medium">Avg Risk</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-white/[0.04]">
        {tools.map((t, i) => (
          <tr key={i} className="py-1.5">
            <td className="py-1.5 font-mono text-neutral-300 truncate max-w-[180px]">{t.tool}</td>
            <td className="py-1.5 text-right font-mono text-neutral-400">{t.calls}</td>
            <td className={`py-1.5 text-right font-mono ${t.deny_rate > 50 ? 'text-red-400' : t.deny_rate > 20 ? 'text-amber-400' : 'text-neutral-400'}`}>
              {t.deny_rate.toFixed(1)}%
            </td>
            <td className={`py-1.5 text-right font-mono ${t.avg_risk > 0.7 ? 'text-red-400' : t.avg_risk > 0.4 ? 'text-amber-400' : 'text-neutral-400'}`}>
              {t.avg_risk.toFixed(3)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function PeerBenchmarkGauge({ label, percentile, agentValue, refs, fmt }) {
  const pct = Math.min(100, Math.max(0, percentile ?? 50))
  const color = pct >= 90 ? '#ef4444' : pct >= 70 ? '#f97316' : pct >= 50 ? '#f59e0b' : '#22c55e'
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-[10px]">
        <span className="text-neutral-500 uppercase tracking-wider">{label}</span>
        <span className="font-mono text-neutral-300">{fmt(agentValue ?? 0)}</span>
      </div>
      <div className="relative h-2 bg-white/[0.05] rounded-full overflow-hidden">
        <div className="absolute inset-y-0 left-0 rounded-full transition-all duration-500" style={{ width: `${pct}%`, background: color }} />
        {[refs?.p50, refs?.p75, refs?.p95].map((v, i) => {
          const refPct = 100 * ([50, 75, 95][i]) / 100
          return v != null
            ? <div key={i} className="absolute top-0 h-full w-px bg-white/20" style={{ left: `${refPct}%` }} />
            : null
        })}
      </div>
      <div className="flex items-center justify-between text-[10px] text-neutral-600">
        <span>0th</span>
        <span className="font-semibold" style={{ color }}>{pct}th percentile</span>
        <span>100th</span>
      </div>
    </div>
  )
}

function PeerBenchmarkPanel({ benchmark }) {
  if (!benchmark) return null
  const { percentiles, agent_stats, references, peer_count } = benchmark
  if (!percentiles || !agent_stats) return null

  return (
    <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl p-5 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-medium text-white flex items-center gap-2">
          <BarChart2 size={14} className="text-neutral-500" />
          Peer Benchmark
        </h2>
        <span className="text-[10px] text-neutral-600">vs. {peer_count} agent{peer_count !== 1 ? 's' : ''} in workspace</span>
      </div>
      <div className="space-y-4">
        {BENCHMARK_METRICS.map(({ key, label, refKey, fmt }) => (
          <PeerBenchmarkGauge
            key={key}
            label={label}
            percentile={percentiles[key]}
            agentValue={
              key === 'call_volume' ? agent_stats.total_calls :
              key === 'deny_rate'  ? agent_stats.deny_rate   :
              agent_stats.avg_risk
            }
            refs={references?.[refKey]}
            fmt={fmt}
          />
        ))}
      </div>
      <p className="text-[10px] text-neutral-700">
        Tick marks: workspace p50 · p75 · p95. Higher percentile = more extreme than peers.
      </p>
    </div>
  )
}

/* ── Risk trend sparkline (inline SVG, no third-party chart lib) ────────────── */
function RiskTrendChart({ series }) {
  if (!series?.length) return <p className="text-xs text-neutral-600">No trend data available.</p>

  const W = 480, H = 80, PAD = 4
  const maxRisk = Math.max(...series.map(p => p.avg_risk), 0.01)
  const pts = series.map((p, i) => {
    const x = PAD + (i / (series.length - 1)) * (W - PAD * 2)
    const y = PAD + (1 - p.avg_risk / maxRisk) * (H - PAD * 2)
    return `${x.toFixed(1)},${y.toFixed(1)}`
  })
  const polyline = pts.join(' ')

  // gradient-fill polygon: close path along the bottom
  const first = pts[0], last = pts[pts.length - 1]
  const fillPoly = `${polyline} ${(W - PAD).toFixed(1)},${(H - PAD).toFixed(1)} ${PAD},${(H - PAD).toFixed(1)}`

  return (
    <div className="space-y-2">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: 80 }} aria-hidden="true">
        <defs>
          <linearGradient id="riskGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%"   stopColor="#6366f1" stopOpacity="0.25" />
            <stop offset="100%" stopColor="#6366f1" stopOpacity="0"    />
          </linearGradient>
        </defs>
        <polygon points={fillPoly} fill="url(#riskGrad)" />
        <polyline points={polyline} fill="none" stroke="#818cf8" strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
      </svg>
      <div className="flex justify-between text-[10px] text-neutral-600 font-mono px-1">
        <span>{series[0]?.date?.slice(5, 10)}</span>
        <span>{series[Math.floor(series.length / 2)]?.date?.slice(5, 10)}</span>
        <span>{series[series.length - 1]?.date?.slice(5, 10)}</span>
      </div>
    </div>
  )
}

function KpiCard({ icon: Icon, label, value, accent, sub }) {
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

const CustomTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null
  return (
    <div className="bg-neutral-900 border border-white/10 rounded-lg p-2.5 text-xs shadow-xl">
      <div className="text-neutral-400 mb-1">{label}</div>
      <div className="text-white">{payload[0]?.value?.toFixed(3)}</div>
    </div>
  )
}

export default function AgentProfile() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [agent, setAgent]         = useState(null)
  const [profile, setProfile]     = useState(null)
  const [recentLogs, setRecentLogs] = useState([])
  const [drift,     setDrift]     = useState(null)
  const [trend,     setTrend]     = useState(null)
  const [benchmark, setBenchmark] = useState(null)
  const [toolUsage,          setToolUsage]          = useState(null)
  const [dailyDecisions,     setDailyDecisions]     = useState(null)
  const [agentFindings,      setAgentFindings]      = useState(null)
  const [loading, setLoading]     = useState(true)
  const [refreshing, setRefreshing] = useState(false)

  const load = useCallback(async () => {
    setRefreshing(true)
    try {
      const [agentRes, profileRes, logsRes, driftRes, trendRes, benchRes, toolRes, dailyRes, findRes] = await Promise.allSettled([
        registryService.getAgent(id),
        registryService.getProfile(id),
        auditService.getAgentLogs(id, 15),
        auditService.getDriftReport(id),
        auditService.getRiskTrend(id),
        auditService.getPeerBenchmark(id),
        auditService.getAgentToolUsage(id),
        auditService.getAgentDailyDecisions(id),
        auditService.getAgentFindings(id),
      ])
      if (agentRes.status === 'fulfilled') {
        setAgent(agentRes.value?.data || agentRes.value)
      }
      if (profileRes.status === 'fulfilled') {
        setProfile(profileRes.value?.data || profileRes.value)
      }
      if (logsRes.status === 'fulfilled') {
        const logsData = logsRes.value?.data || logsRes.value || {}
        setRecentLogs(logsData.items || [])
      }
      if (driftRes.status === 'fulfilled') {
        setDrift(driftRes.value?.data || driftRes.value)
      }
      if (trendRes.status === 'fulfilled') {
        setTrend(trendRes.value?.data || trendRes.value)
      }
      if (benchRes.status === 'fulfilled') {
        setBenchmark(benchRes.value?.data || benchRes.value)
      }
      if (toolRes.status === 'fulfilled') {
        setToolUsage(toolRes.value?.data || toolRes.value)
      }
      if (dailyRes.status === 'fulfilled') {
        setDailyDecisions(dailyRes.value?.data || dailyRes.value)
      }
      if (findRes.status === 'fulfilled') {
        setAgentFindings(findRes.value?.data || findRes.value)
      }
    } catch {}
    setRefreshing(false)
    setLoading(false)
  }, [id])

  useEffect(() => { load() }, [load])

  const p = profile || {}
  const a = agent || {}
  const riskTrend = useMemo(
    () => (p.risk_trend || []).map((v, i) => ({ day: `D-${6 - i}`, risk: Number(v.toFixed(3)) })),
    [profile], // eslint-disable-line react-hooks/exhaustive-deps
  )
  const avgRisk = useMemo(
    () => riskTrend.reduce((s, r) => s + r.risk, 0) / Math.max(riskTrend.length, 1),
    [riskTrend],
  )
  const blockRate = Number(p.block_rate || 0).toFixed(1)
  const isDrifting = p.behavioral_drift === true

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <RefreshCw className="animate-spin text-neutral-500" size={24} />
      </div>
    )
  }

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center gap-4">
        <button
          onClick={() => navigate('/agents')}
          className="flex items-center gap-1.5 text-xs text-neutral-500 hover:text-white"
        >
          <ArrowLeft size={13} /> Agents
        </button>
        <div className="flex-1">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-white/[0.06] flex items-center justify-center">
              <Bot size={16} className="text-neutral-300" />
            </div>
            <div>
              <h1 className="text-xl font-semibold text-white">{a.name || id}</h1>
              <div className="text-xs text-neutral-500 font-mono">{id}</div>
            </div>
            {isDrifting && (
              <span className="flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-amber-500/10 text-amber-400 border border-amber-500/20">
                <AlertTriangle size={11} /> Behavioral drift
              </span>
            )}
            {a.status && (
              <span className={`text-xs px-2 py-0.5 rounded-full ${a.status === 'active' ? 'bg-green-500/10 text-green-400' : 'bg-neutral-500/10 text-neutral-500'}`}>
                {a.status}
              </span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={load} disabled={refreshing} className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[var(--border-subtle)] text-xs text-neutral-300 hover:border-white/20">
            <RefreshCw size={12} className={refreshing ? 'animate-spin' : ''} /> Refresh
          </button>
          <Link to={`/forensics?agent=${id}`} className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white text-black text-xs font-medium hover:bg-neutral-200">
            <ExternalLink size={12} /> Forensics
          </Link>
        </div>
      </div>

      {/* Drift alert */}
      {isDrifting && (
        <div className="flex items-start gap-3 p-4 bg-amber-500/10 border border-amber-500/20 rounded-xl text-amber-400">
          <AlertTriangle size={16} className="shrink-0 mt-0.5" />
          <div>
            <div className="text-sm font-medium">Behavioral drift detected</div>
            <div className="text-xs mt-0.5 opacity-80">
              Today's risk score is significantly above the 7-day baseline (anomaly score: {Number(p.anomaly_score || 0).toFixed(2)}). Review recent decisions in Forensics.
            </div>
          </div>
        </div>
      )}

      {/* KPIs */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <KpiCard icon={Activity}    label="Total Decisions" value={(p.total_decisions ?? 0).toLocaleString()} />
        <KpiCard icon={CheckCircle2} label="Allowed"        value={(p.allowed ?? 0).toLocaleString()} accent="text-green-400" />
        <KpiCard icon={ShieldAlert} label="Blocked"        value={(p.blocked ?? 0).toLocaleString()} accent={parseFloat(blockRate) > 20 ? 'text-red-400' : 'text-amber-400'} sub={`${blockRate}% block rate`} />
        <KpiCard icon={Zap}         label="Avg Risk Score" value={Number(p.avg_risk_score || 0).toFixed(3)} accent={Number(p.avg_risk_score) > 0.5 ? 'text-red-400' : Number(p.avg_risk_score) > 0.3 ? 'text-amber-400' : 'text-green-400'} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Risk trend chart */}
        <div className="lg:col-span-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl p-5">
          <h2 className="text-sm font-medium text-white mb-4 flex items-center gap-2">
            <TrendingUp size={14} className="text-neutral-500" />
            7-Day Risk Trend
          </h2>
          {riskTrend.length === 0 ? (
            <div className="h-40 flex items-center justify-center text-xs text-neutral-600">No trend data available.</div>
          ) : (
            <ResponsiveContainer width="100%" height={160}>
              <LineChart data={riskTrend} margin={{ left: 0, right: 8, top: 4, bottom: 4 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
                <XAxis dataKey="day" tick={{ fill: '#6b7280', fontSize: 10 }} axisLine={false} tickLine={false} />
                <YAxis domain={[0, 1]} tick={{ fill: '#6b7280', fontSize: 10 }} axisLine={false} tickLine={false} />
                <Tooltip content={<CustomTooltip />} />
                <ReferenceLine y={avgRisk} stroke="rgba(255,255,255,0.15)" strokeDasharray="4 4" />
                <Line type="monotone" dataKey="risk" stroke="#f59e0b" strokeWidth={2} dot={{ r: 3, fill: '#f59e0b' }} />
              </LineChart>
            </ResponsiveContainer>
          )}
          <div className="text-[10px] text-neutral-600 mt-2">Dashed line = 7-day average ({avgRisk.toFixed(3)})</div>
        </div>

        {/* Top tools */}
        <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl p-5">
          <h2 className="text-sm font-medium text-white mb-4 flex items-center gap-2">
            <BarChart2 size={14} className="text-neutral-500" />
            Top Tools
          </h2>
          {(p.top_tools || []).length === 0 ? (
            <div className="text-xs text-neutral-600">No tool data.</div>
          ) : (
            <div className="space-y-2.5">
              {(p.top_tools || []).map((t, i) => {
                const max = p.top_tools[0]?.count || 1
                const pct = (t.count / max) * 100
                return (
                  <div key={t.tool}>
                    <div className="flex items-center justify-between text-xs mb-1">
                      <span className="text-neutral-300 font-mono truncate">{t.tool}</span>
                      <span className="text-neutral-500 ml-2">{t.count}</span>
                    </div>
                    <div className="h-1 bg-white/[0.06] rounded-full">
                      <div className="h-full bg-indigo-400 rounded-full" style={{ width: `${pct}%` }} />
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>

      {/* Agent metadata */}
      <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl p-5">
        <h2 className="text-sm font-medium text-white mb-4 flex items-center gap-2">
          <GitMerge size={14} className="text-neutral-500" />
          Agent Metadata
        </h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {[
            ['Model', a.model || '—'],
            ['Autonomy Level', a.autonomy_level ?? '—'],
            ['Created', a.created_at ? new Date(a.created_at).toLocaleDateString() : '—'],
            ['Last Active', p.last_active ? new Date(p.last_active).toLocaleString() : '—'],
            ['Max Risk', a.max_risk_score ?? '—'],
            ['Anomaly Score', Number(p.anomaly_score || 0).toFixed(3)],
            ['Tenant', a.tenant_id?.slice(0, 8) + '…' || '—'],
            ['Agent ID', id?.slice(0, 8) + '…'],
          ].map(([k, v]) => (
            <div key={k} className="bg-white/[0.03] rounded-lg p-3">
              <div className="text-[10px] text-neutral-600 mb-1">{k}</div>
              <div className="text-xs text-white font-mono truncate">{v}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Behavioral Drift Report */}
      {drift && (
        <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl p-5">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-medium text-white flex items-center gap-2">
              <Activity size={14} className="text-neutral-500" />
              Behavioral Drift Analysis
            </h2>
            {(() => {
              const s = DRIFT_LEVEL_STYLE[drift.drift_level] || DRIFT_LEVEL_STYLE.low
              return (
                <span className={`flex items-center gap-1 text-xs px-2 py-0.5 rounded-full border ${s.bg} ${s.color} ${s.border}`}>
                  {drift.drift_level === 'critical' || drift.drift_level === 'high'
                    ? <AlertTriangle size={10} />
                    : <CheckCircle2 size={10} />}
                  {drift.drift_level?.toUpperCase()} — {(drift.drift_score * 100).toFixed(0)}% drift
                </span>
              )
            })()}
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {/* Drift metric bars */}
            <div>
              <div className="text-[10px] text-neutral-500 uppercase tracking-wider mb-3">Drift by Signal</div>
              <div className="space-y-2.5">
                {Object.entries(drift.metrics || {}).map(([k, v]) => (
                  <DriftBar key={k} label={k} value={v} />
                ))}
              </div>
            </div>

            {/* Baseline vs recent comparison */}
            <div>
              <div className="text-[10px] text-neutral-500 uppercase tracking-wider mb-3">
                Baseline (7d) vs Recent (24h)
              </div>
              <div className="space-y-2">
                {[
                  ['Avg Risk',     (drift.baseline?.avg_risk  || 0).toFixed(3), (drift.recent?.avg_risk  || 0).toFixed(3)],
                  ['Deny Rate',    ((drift.baseline?.deny_rate || 0) * 100).toFixed(1) + '%', ((drift.recent?.deny_rate || 0) * 100).toFixed(1) + '%'],
                  ['Calls',        drift.baseline?.total ?? 0, drift.recent?.total ?? 0],
                  ['Unique Tools', drift.baseline?.unique_tools ?? 0, drift.recent?.unique_tools ?? 0],
                ].map(([label, base, recent]) => (
                  <div key={label} className="flex items-center gap-2 text-xs">
                    <span className="text-neutral-500 w-24">{label}</span>
                    <span className="text-neutral-400 font-mono w-16 text-right">{base}</span>
                    <span className="text-neutral-600 text-[10px]">→</span>
                    <span className="font-mono w-16 text-right text-white">{recent}</span>
                  </div>
                ))}
              </div>
              <div className="text-[10px] text-neutral-600 mt-3">
                Computed {drift.computed_at ? new Date(drift.computed_at).toLocaleString() : '—'}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Risk Score Trend */}
      <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl p-5">
        <h2 className="text-sm font-medium text-white mb-1 flex items-center gap-2">
          <TrendingUp size={14} className="text-indigo-400" />
          30-Day Risk Score Trend
        </h2>
        {trend?.summary && (
          <div className="flex gap-4 mb-3 mt-2">
            {[
              ['Peak Risk',  (trend.summary.max_risk * 100).toFixed(1) + '%'],
              ['Avg Risk',   (trend.summary.avg_risk * 100).toFixed(1) + '%'],
              ['Denials',    trend.summary.total_denials],
              ['Active Days', trend.summary.active_days],
            ].map(([label, val]) => (
              <div key={label} className="space-y-0.5">
                <div className="text-[10px] text-neutral-600 uppercase tracking-wider">{label}</div>
                <div className="text-sm font-mono text-white">{val}</div>
              </div>
            ))}
          </div>
        )}
        <RiskTrendChart series={trend?.series} />
      </div>

      {/* Peer Benchmark */}
      <PeerBenchmarkPanel benchmark={benchmark} />

      {/* Tool Usage Profile */}
      <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl p-5">
        <h2 className="text-sm font-medium text-white mb-4">Tool Usage Profile</h2>
        <AgentToolUsageTable toolUsage={toolUsage} />
      </div>

      {/* Daily Decision Volume */}
      <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl p-5">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-medium text-white">Daily Decision Volume (30 days)</h2>
          {dailyDecisions?.total_calls != null && (
            <span className="text-[10px] font-mono text-neutral-600">
              {dailyDecisions.total_calls} calls · {dailyDecisions.total_deny} denied
            </span>
          )}
        </div>
        <AgentDailyDecisionsChart series={dailyDecisions?.series} />
        <div className="flex items-center gap-4 mt-2">
          {[['Allow', '#22c55e'], ['Deny / Kill', '#ef4444']].map(([label, color]) => (
            <div key={label} className="flex items-center gap-1.5 text-[10px] text-neutral-500">
              <div className="w-2.5 h-2.5 rounded-sm" style={{ background: color }} />
              {label}
            </div>
          ))}
        </div>
      </div>

      {/* Finding Frequency */}
      <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl p-5">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-medium text-white">Finding Frequency</h2>
          {agentFindings?.total != null && (
            <span className="text-[10px] font-mono text-neutral-600">
              {agentFindings.total} findings · {agentFindings.days}d
            </span>
          )}
        </div>
        <AgentFindingFrequency agentFindings={agentFindings} />
      </div>

      {/* Recent Decisions */}
      <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl p-5">
        <h2 className="text-sm font-medium text-white mb-4 flex items-center gap-2">
          <Clock size={14} className="text-neutral-500" />
          Recent Decisions
        </h2>
        {recentLogs.length === 0 ? (
          <div className="text-xs text-neutral-600">No recent decisions recorded for this agent.</div>
        ) : (
          <div className="divide-y divide-white/[0.04]">
            {recentLogs.map((log, i) => {
              const isAllow = log.action === 'allow' || log.action === 'execute_tool'
              const risk = Number(log.risk_score || 0)
              return (
                <div key={log.id || i} className="flex items-center gap-3 py-2 text-xs">
                  <span className={`shrink-0 px-1.5 py-0.5 rounded text-[10px] font-medium border ${
                    isAllow
                      ? 'text-green-400 bg-green-500/10 border-green-500/20'
                      : 'text-red-400 bg-red-500/10 border-red-500/20'
                  }`}>
                    {(log.action || '—').toUpperCase()}
                  </span>
                  <span className="text-neutral-400 font-mono truncate flex-1">{log.tool || '—'}</span>
                  <span className={`shrink-0 font-mono text-[10px] ${risk > 0.7 ? 'text-red-400' : risk > 0.4 ? 'text-amber-400' : 'text-neutral-500'}`}>
                    {risk.toFixed(2)}
                  </span>
                  <span className="shrink-0 text-neutral-600 font-mono text-[10px]">
                    {log.timestamp ? new Date(log.timestamp).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }) : '—'}
                  </span>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
