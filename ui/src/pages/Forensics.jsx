import React, { useState, useEffect, useRef, useContext } from 'react'
import { useLocation, useNavigate, Link } from 'react-router-dom'
import { forensicsService } from '../services/api'
import { AgentContext } from '../context/AgentContext'
import {
  Search, BrainCircuit, Activity, ShieldAlert,
  FileText, ChevronRight, Database, Fingerprint,
  Zap, ShieldCheck, AlertTriangle, Clock,
  TrendingDown, TrendingUp, ArrowLeft, Play, ListChecks,
} from 'lucide-react'
import Card from '../components/Common/Card'
import Button from '../components/Common/Button'
import SkeletonLoader from '../components/Common/SkeletonLoader'

const DECISION_STYLES = {
  allow:    { cls: 'text-green-400  bg-green-500/10  border-green-500/20',  glow: 'rgba(34,197,94,0.3)'   },
  deny:     { cls: 'text-red-400    bg-red-500/10    border-red-500/20',    glow: 'rgba(239,68,68,0.4)'   },
  monitor:  { cls: 'text-blue-400   bg-blue-500/10   border-blue-500/20',   glow: 'rgba(59,130,246,0.3)'  },
  throttle: { cls: 'text-amber-400  bg-amber-500/10  border-amber-500/20',  glow: 'rgba(245,158,11,0.3)'  },
  escalate: { cls: 'text-purple-400 bg-purple-500/10 border-purple-500/20', glow: 'rgba(168,85,247,0.3)'  },
  kill:     { cls: 'text-red-300    bg-red-900/20    border-red-700/30',    glow: 'rgba(239,68,68,0.5)'   },
}

function DecisionBadge({ decision }) {
  const d    = (decision || 'unknown').toLowerCase()
  const meta = DECISION_STYLES[d] ?? { cls: 'text-neutral-400 bg-white/5 border-white/10', glow: 'transparent' }
  return (
    <span
      className={`status-badge ${meta.cls}`}
      style={d === 'deny' || d === 'kill' ? { boxShadow: `0 0 10px ${meta.glow}` } : undefined}
    >
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
      <span className="text-xs text-neutral-500 tabular-nums w-8 text-right">{(score ?? 0).toFixed(2)}</span>
    </div>
  )
}

function TimelineEvent({ event, isLast, idx }) {
  const d    = (event.decision || 'unknown').toLowerCase()
  const meta = DECISION_STYLES[d] ?? { cls: 'text-neutral-400 bg-white/5 border-white/10', glow: 'transparent' }
  const isDanger = d === 'deny' || d === 'kill' || d === 'escalate'

  // Support both `findings` (new) and `reasons` (legacy/deprecated)
  const findingsList = Array.isArray(event.findings) && event.findings.length > 0
    ? event.findings
    : Array.isArray(event.reasons) && event.reasons.length > 0
      ? event.reasons
      : []

  return (
    <div className="flex gap-4">
      {/* Spine */}
      <div className="flex flex-col items-center">
        <div
          className={`w-8 h-8 rounded-full border-2 flex items-center justify-center shrink-0 text-xs font-bold transition-all
            ${isDanger
              ? 'border-red-500/50 bg-red-500/10 text-red-400'
              : 'border-[var(--border-default)] bg-[var(--bg-surface-elevated)] text-neutral-500'
            }`}
          style={isDanger ? { boxShadow: `0 0 16px ${meta.glow}` } : undefined}
          aria-hidden="true"
        >
          {idx + 1}
        </div>
        {!isLast && (
          <div className={`w-px flex-1 mt-2 ${isDanger ? 'bg-red-500/20' : 'bg-[var(--border-subtle)]'}`} aria-hidden="true" />
        )}
      </div>

      {/* Content */}
      <div className={`
        flex-1 mb-4 p-4 rounded-xl border transition-all
        ${isDanger
          ? 'bg-red-500/[0.04] border-red-500/15 hover:border-red-500/25'
          : 'bg-[var(--bg-surface)] border-[var(--border-subtle)] hover:border-[var(--border-default)]'
        }
      `}>
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <div className="flex items-center gap-2 flex-wrap">
            <DecisionBadge decision={event.decision} />
            <span className="text-xs font-bold text-white font-mono">{event.tool || '—'}</span>
          </div>
          <div className="flex items-center gap-1.5 text-xs text-neutral-600 font-mono">
            <Clock size={10} aria-hidden="true" />
            {event.timestamp ? new Date(event.timestamp).toLocaleString('en-US', {
              month: 'short', day: '2-digit',
              hour: '2-digit', minute: '2-digit', second: '2-digit',
            }) : '—'}
          </div>
        </div>

        <div className="mt-3 flex items-center gap-4 flex-wrap">
          <div className="flex items-center gap-2">
            <span className="text-[10px] text-neutral-600 uppercase tracking-wide">Risk</span>
            <RiskBar score={event.risk_score} />
          </div>
        </div>

        {findingsList.length > 0 && (
          <div className="mt-3 space-y-1">
            {findingsList.slice(0, 5).map((r, i) => (
              <div key={i} className="flex items-start gap-2">
                <ChevronRight size={10} className="text-neutral-600 mt-0.5 shrink-0" aria-hidden="true" />
                <span className="text-[11px] text-neutral-500 italic leading-relaxed">{r}</span>
              </div>
            ))}
          </div>
        )}

        {isDanger && (
          <div
            className="mt-3 text-[10px] font-bold uppercase tracking-widest text-red-400"
            style={{ textShadow: '0 0 12px rgba(239,68,68,0.4)' }}
          >
            ⚠ Threat Detected
          </div>
        )}
      </div>
    </div>
  )
}

function ReplayStep({ step, idx, isActive, onClick }) {
  const statusColor = step.step_status === 'ok' || step.step_status === 'success'
    ? 'bg-green-500'
    : step.step_status === 'error' || step.step_status === 'failed'
      ? 'bg-red-500'
      : 'bg-amber-400'

  return (
    <button
      onClick={onClick}
      className={`w-full text-left rounded-lg px-3 py-2 border transition-all ${
        isActive
          ? 'bg-indigo-500/10 border-indigo-500/30'
          : 'bg-white/[0.02] border-white/5 hover:bg-white/[0.04] hover:border-white/10'
      }`}
    >
      <div className="flex items-center gap-2">
        <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${statusColor}`} />
        <span className="text-[10px] font-mono text-neutral-400 w-5">{idx + 1}</span>
        <span className="text-[11px] font-semibold text-white truncate flex-1">
          {step.step_name || step.tool || '—'}
        </span>
        <span className={`text-[10px] font-mono ${
          step.decision === 'deny' ? 'text-red-400' :
          step.decision === 'allow' ? 'text-green-400' : 'text-neutral-600'
        }`}>
          {step.decision || '—'}
        </span>
      </div>
      {step.findings && step.findings.length > 0 && (
        <p className="text-[10px] text-neutral-600 truncate mt-0.5 pl-5">
          {step.findings.slice(0, 2).join(', ')}
        </p>
      )}
    </button>
  )
}

export default function Forensics() {
  const location = useLocation()
  const navigate = useNavigate()
  const { selectedAgentId, selectedAgent } = useContext(AgentContext)
  const [agentId, setAgentId]         = useState('')
  const [profile, setProfile]         = useState(null)
  const [loading, setLoading]         = useState(false)
  const [error,   setError]           = useState('')
  const [recentList, setRecentList]   = useState([])
  const [recentLoading, setRecentLoading] = useState(true)
  const [recentError, setRecentError] = useState('')
  const [replay, setReplay]           = useState(null)
  const [replayLoading, setReplayLoading] = useState(false)
  const [replayError, setReplayError] = useState('')
  const [activeReplayStep, setActiveReplayStep] = useState(null)
  const mountedRef = useRef(true)

  useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false }
  }, [])

  // Auto-load recent high-risk events on mount
  useEffect(() => {
    const loadRecent = async () => {
      setRecentLoading(true)
      setRecentError('')
      try {
        const res = await forensicsService.listInvestigations({ min_risk: 0.5, limit: 20 })
        if (mountedRef.current) {
          const data = res?.data || res
          setRecentList(data?.events || [])
        }
      } catch (err) {
        if (mountedRef.current) setRecentError(err.message || 'Failed to load recent investigations.')
      } finally {
        if (mountedRef.current) setRecentLoading(false)
      }
    }
    loadRecent()
  }, [])

  // Auto-investigate when navigated here with ?agent=<id>, OR when the
  // user selects an agent in the Topbar/Sidebar scope picker. URL takes
  // precedence so deep links work; context selection seeds the field when
  // no URL param is present so "click agent → see forensics" works.
  useEffect(() => {
    const params = new URLSearchParams(location.search)
    const id = params.get('agent') || selectedAgentId
    if (id) { setAgentId(id); triggerInvestigation(id) }
  }, [location.search, selectedAgentId]) // eslint-disable-line react-hooks/exhaustive-deps

  const triggerInvestigation = async (id) => {
    if (!id?.trim()) return
    setLoading(true)
    setError('')
    setProfile(null)
    setReplay(null)
    setReplayError('')
    setActiveReplayStep(null)
    try {
      const res = await forensicsService.getInvestigation(id)
      if (mountedRef.current) setProfile(res.data || res)
    } catch (err) {
      if (mountedRef.current) setError(err.message || 'Investigation failed — no data found for this agent.')
    } finally {
      if (mountedRef.current) setLoading(false)
    }
  }

  const triggerReplay = async (id) => {
    if (!id?.trim()) return
    setReplayLoading(true)
    setReplayError('')
    setReplay(null)
    setActiveReplayStep(null)
    try {
      const res = await forensicsService.getReplay(id)
      if (mountedRef.current) {
        const data = res?.data || res
        setReplay(data)
        if (data?.events?.length) setActiveReplayStep(0)
      }
    } catch (err) {
      if (mountedRef.current) setReplayError(err.message || 'Replay unavailable for this agent.')
    } finally {
      if (mountedRef.current) setReplayLoading(false)
    }
  }

  const handleSearch = (e) => {
    e.preventDefault()
    triggerInvestigation(agentId)
  }

  const handleRecentClick = (event) => {
    const id = event.agent_id
    if (!id) return
    setAgentId(id)
    triggerInvestigation(id)
  }

  const hasFromParam = !!new URLSearchParams(location.search).get('agent')

  const events        = profile?.recent_high_risk_events || []
  const decisionBreak = profile?.decision_breakdown || {}
  const totalEvents   = profile?.total_events ?? 0
  const avgRisk       = profile?.avg_risk_score ?? 0
  const denyCount     = decisionBreak.deny || 0
  const allowCount    = decisionBreak.allow || 0
  const topFindings   = profile?.top_findings || []

  const currentReplayStep = replay?.events?.[activeReplayStep] ?? null

  return (
    <div className="space-y-6 animate-fade-in">
      {/* Header */}
      <div className="page-header">
        <div className="flex items-center gap-3">
          {hasFromParam && (
            <button
              onClick={() => navigate(-1)}
              aria-label="Go back"
              className="p-1.5 rounded-lg text-neutral-500 hover:text-white hover:bg-white/[0.05] transition-colors"
            >
              <ArrowLeft size={16} aria-hidden="true" />
            </button>
          )}
          <BrainCircuit size={22} className="text-neutral-400" aria-hidden="true" />
          <div>
            <h1 className="text-2xl font-bold text-white tracking-tight">Behavioral Forensics</h1>
            <p className="text-xs text-neutral-500 mt-0.5">Click-to-drill-down incident investigation</p>
          </div>
        </div>
      </div>

      {/* Recent high-risk events list */}
      <Card title="Recent High-Risk Denials" icon={ShieldAlert}>
        {recentLoading && (
          <div className="space-y-2">
            {[...Array(4)].map((_, i) => <SkeletonLoader key={i} variant="row" />)}
          </div>
        )}
        {recentError && !recentLoading && (
          <p className="text-xs text-neutral-500 italic">{recentError}</p>
        )}
        {!recentLoading && recentList.length === 0 && !recentError && (
          <div className="py-10 text-center flex flex-col items-center gap-3 max-w-md mx-auto">
            <Fingerprint size={28} className="text-neutral-600 opacity-40" aria-hidden="true" />
            <p className="text-sm text-neutral-300 font-medium">No investigations yet</p>
            <p className="text-xs text-neutral-500 leading-relaxed">
              Investigations open automatically from an incident — or paste
              an agent UUID below to drill into recent decisions.
            </p>
            <div className="flex items-center gap-2 flex-wrap justify-center">
              <Link
                to="/incidents"
                className="inline-flex items-center gap-1.5 text-[11px] px-3 py-1.5 rounded-lg border border-indigo-500/30 text-indigo-300 hover:border-indigo-500/60 hover:bg-indigo-500/[0.08] transition-colors"
              >
                <ShieldAlert size={11} aria-hidden="true" />
                Open incidents
              </Link>
              <Link
                to="/audit-logs"
                className="inline-flex items-center gap-1.5 text-[11px] px-3 py-1.5 rounded-lg border border-white/10 text-neutral-400 hover:border-white/20 hover:text-white transition-colors"
              >
                <FileText size={11} aria-hidden="true" />
                Browse audit logs
              </Link>
            </div>
          </div>
        )}
        {!recentLoading && recentList.length > 0 && (
          <div className="divide-y divide-white/5">
            {recentList.map((event, i) => (
              <button
                key={event.id || i}
                onClick={() => handleRecentClick(event)}
                className="w-full text-left px-2 py-2 hover:bg-white/[0.03] transition-colors rounded-lg flex items-center gap-3 flex-wrap"
              >
                <DecisionBadge decision={event.decision || event.action} />
                <span className="text-xs font-mono text-neutral-400 truncate max-w-[120px]" title={event.agent_id}>
                  {event.agent_id?.slice(0, 12)}…
                </span>
                <span className="text-xs font-bold text-white">{event.tool || '—'}</span>
                <RiskBar score={event.risk_score} />
                <span className="text-[10px] text-neutral-600 font-mono ml-auto">
                  {event.timestamp ? new Date(event.timestamp).toLocaleString('en-US', {
                    month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit',
                  }) : '—'}
                </span>
              </button>
            ))}
          </div>
        )}
      </Card>

      {/* Search */}
      <Card title="Forensic Recall" icon={Search}>
        <form onSubmit={handleSearch} className="flex flex-col sm:flex-row gap-3">
          <div className="relative flex-1">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-neutral-600" aria-hidden="true" />
            <input name="input"
              type="text"
              value={agentId}
              onChange={(e) => setAgentId(e.target.value)}
              placeholder="Agent UUID — or click a row above to auto-populate"
              aria-label="Agent ID to investigate"
              className="input-standard pl-9 h-10 font-mono"
            />
          </div>
          <Button type="submit" loading={loading} disabled={!agentId.trim()}>
            <ChevronRight size={14} aria-hidden="true" />
            Investigate
          </Button>
        </form>
      </Card>

      {error && (
        <div className="error-banner" role="alert">
          <div className="flex items-center gap-2">
            <AlertTriangle size={14} className="text-red-400 shrink-0" aria-hidden="true" />
            <p className="text-xs text-red-400">{error}</p>
          </div>
        </div>
      )}

      {loading && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {[...Array(3)].map((_, i) => <SkeletonLoader key={i} variant="card" />)}
        </div>
      )}

      {profile && (
        <div className="space-y-6 animate-fade-in">
          {/* Profile KPIs */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            <Card
              title="Total Events"
              value={totalEvents.toLocaleString()}
              icon={Activity}
              subtitle="All recorded actions"
            />
            <Card
              title="Avg Risk Score"
              value={avgRisk.toFixed(3)}
              icon={Zap}
              subtitle="Across all executions"
            />
            <Card
              title="Threats Blocked"
              value={denyCount.toLocaleString()}
              icon={ShieldAlert}
              subtitle="Deny decisions"
            />
            <Card
              title="Allowed Actions"
              value={allowCount.toLocaleString()}
              icon={ShieldCheck}
              subtitle="Approved executions"
            />
          </div>

          {/* Quick navigation */}
          <div className="flex items-center gap-2 flex-wrap">
            <button
              onClick={() => navigate(`/agents/${profile.agent_id}/profile`)}
              className="text-[11px] font-semibold text-indigo-400 hover:text-white transition-colors flex items-center gap-1"
            >
              View Agent Profile →
            </button>
            <span className="text-neutral-700 text-xs">·</span>
            <button
              onClick={() => navigate(`/incidents?agent=${profile.agent_id}`)}
              className="text-[11px] font-semibold text-indigo-400 hover:text-white transition-colors flex items-center gap-1"
            >
              View Incidents →
            </button>
          </div>

          {/* Decision breakdown */}
          {Object.keys(decisionBreak).length > 0 && (
            <Card title="Decision Breakdown" icon={Activity}>
              <div className="flex items-center gap-3 flex-wrap">
                {Object.entries(decisionBreak).map(([decision, count]) => {
                  const d    = decision.toLowerCase()
                  const meta = DECISION_STYLES[d] ?? { cls: 'text-neutral-400 bg-white/5 border-white/10' }
                  return (
                    <div key={decision} className={`px-3 py-2 rounded-xl border ${meta.cls} flex flex-col items-center`}>
                      <span className="text-xl font-black">{count}</span>
                      <span className="text-[10px] font-bold uppercase tracking-wide opacity-70">{decision}</span>
                    </div>
                  )
                })}
              </div>
            </Card>
          )}

          {/* Top findings frequency table */}
          {topFindings.length > 0 && (
            <Card title="Top Findings" icon={ListChecks}>
              <div className="divide-y divide-white/5">
                {topFindings.map((f, i) => {
                  const maxCount = topFindings[0]?.count || 1
                  const pct = Math.round((f.count / maxCount) * 100)
                  return (
                    <div key={i} className="py-2 flex items-center gap-3">
                      <span className="text-[10px] text-neutral-600 w-4 shrink-0 text-right">{i + 1}</span>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center justify-between gap-2 mb-1">
                          <span className="text-xs text-neutral-300 truncate font-mono">{f.finding}</span>
                          <span className="text-xs font-bold text-white shrink-0">{f.count}</span>
                        </div>
                        <div className="h-1 bg-white/[0.06] rounded-full overflow-hidden">
                          <div
                            className="h-full rounded-full bg-red-500/60"
                            style={{ width: `${pct}%` }}
                          />
                        </div>
                      </div>
                    </div>
                  )
                })}
              </div>
            </Card>
          )}

          {/* Forensic Timeline */}
          <div>
            <div className="section-header mb-4">
              <Clock size={14} className="text-neutral-600" aria-hidden="true" />
              Forensic Timeline
              <span className="text-neutral-600 text-[10px] font-normal ml-1">(most recent high-risk events)</span>
            </div>

            {events.length === 0 ? (
              <div className="py-16 text-center border border-dashed border-[var(--border-subtle)] rounded-xl text-xs text-neutral-600">
                <Fingerprint size={28} className="mx-auto mb-3 opacity-20" aria-hidden="true" />
                No high-risk events recorded for this agent.
              </div>
            ) : (
              <div role="list" aria-label="Forensic timeline">
                {events.map((event, idx) => (
                  <TimelineEvent
                    key={event.id || idx}
                    event={event}
                    idx={idx}
                    isLast={idx === events.length - 1}
                  />
                ))}
              </div>
            )}
          </div>

          {/* Replay section */}
          <Card title="Execution Replay" icon={Play}>
            <div className="mb-4 flex items-center gap-3">
              <Button
                onClick={() => triggerReplay(profile.agent_id)}
                loading={replayLoading}
                disabled={replayLoading}
                variant="secondary"
              >
                <Play size={13} aria-hidden="true" />
                Load Replay
              </Button>
              <span className="text-xs text-neutral-600">
                Fetch step-by-step execution replay for agent{' '}
                <span className="font-mono text-neutral-500">{profile?.agent_id?.slice(0, 12)}…</span>
              </span>
            </div>

            {replayError && (
              <div className="mb-3 px-3 py-2 rounded-lg border border-red-500/20 bg-red-500/5 text-xs text-red-400 flex items-center gap-2">
                <AlertTriangle size={12} className="shrink-0" />
                {replayError}
              </div>
            )}

            {replayLoading && (
              <div className="space-y-2">
                {[...Array(3)].map((_, i) => <SkeletonLoader key={i} variant="row" />)}
              </div>
            )}

            {replay && !replayLoading && (
              <div className="space-y-4 animate-fade-in">
                <div className="flex items-center gap-4 text-[11px] text-neutral-500">
                  <span>
                    <span className="font-bold text-white">{replay.event_count ?? replay.events?.length ?? 0}</span> events
                  </span>
                  <span className="text-neutral-700">·</span>
                  <span>tenant <span className="font-mono text-neutral-400">{replay.tenant_id?.slice(0, 8)}…</span></span>
                </div>

                {replay.events && replay.events.length > 0 ? (
                  <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                    {/* Step list */}
                    <div className="space-y-1.5 max-h-[400px] overflow-y-auto">
                      {replay.events.map((step, i) => (
                        <ReplayStep
                          key={step.event_id || i}
                          step={step}
                          idx={i}
                          isActive={activeReplayStep === i}
                          onClick={() => setActiveReplayStep(i)}
                        />
                      ))}
                    </div>

                    {/* Step detail */}
                    <div className="rounded-xl bg-black/40 border border-white/5 p-4 max-h-[400px] overflow-auto">
                      {currentReplayStep ? (
                        <div className="space-y-3">
                          <div className="flex items-center gap-2 flex-wrap">
                            <DecisionBadge decision={currentReplayStep.decision} />
                            <span className="text-xs font-bold text-white font-mono">
                              {currentReplayStep.tool || currentReplayStep.step_name || '—'}
                            </span>
                            <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded ${
                              currentReplayStep.step_status === 'ok' || currentReplayStep.step_status === 'success'
                                ? 'bg-green-500/10 text-green-400'
                                : 'bg-red-500/10 text-red-400'
                            }`}>
                              {currentReplayStep.step_status || '—'}
                            </span>
                          </div>

                          <div className="flex items-center gap-2">
                            <span className="text-[10px] text-neutral-600 uppercase">Risk</span>
                            <RiskBar score={currentReplayStep.risk_score} />
                          </div>

                          {currentReplayStep.timestamp && (
                            <p className="text-[10px] font-mono text-neutral-600">
                              {new Date(currentReplayStep.timestamp).toLocaleString()}
                            </p>
                          )}

                          {Array.isArray(currentReplayStep.findings) && currentReplayStep.findings.length > 0 && (
                            <div className="space-y-1">
                              <p className="text-[10px] text-neutral-600 uppercase tracking-wide">Findings</p>
                              {currentReplayStep.findings.map((f, fi) => (
                                <div key={fi} className="flex items-start gap-1.5">
                                  <ChevronRight size={9} className="text-neutral-600 mt-0.5 shrink-0" />
                                  <span className="text-[11px] text-neutral-400 italic">{f}</span>
                                </div>
                              ))}
                            </div>
                          )}

                          {currentReplayStep.request_id && (
                            <p className="text-[10px] font-mono text-neutral-700">
                              req <span className="text-neutral-500">{currentReplayStep.request_id?.slice(0, 20)}…</span>
                            </p>
                          )}
                        </div>
                      ) : (
                        <p className="text-xs text-neutral-600">Select a step to inspect.</p>
                      )}
                    </div>
                  </div>
                ) : (
                  <div className="py-8 text-center text-xs text-neutral-600">
                    <Play size={20} className="mx-auto mb-2 opacity-20" />
                    No replay steps available.
                  </div>
                )}
              </div>
            )}
          </Card>

          {/* Archive status */}
          <div className="p-4 bg-white/[0.01] border border-white/5 rounded-xl flex items-center gap-3">
            <Database size={14} className="text-neutral-500 shrink-0" aria-hidden="true" />
            <span className="text-xs text-neutral-500">
              Investigation covers last {events.length} high-risk events of {totalEvents.toLocaleString()} total for agent{' '}
              <span className="font-mono text-neutral-400">{profile?.agent_id?.slice(0, 12)}…</span>
            </span>
          </div>
        </div>
      )}
    </div>
  )
}
