import React, { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  ArrowLeft, Bot, Crosshair, Flag, RefreshCw, Shield, User,
  CheckCircle2, XCircle, AlertTriangle, Loader2,
} from 'lucide-react'
import Button from '../components/Common/Button'
import Card from '../components/Common/Card'
import { replayService } from '../services/api'

// Sprint 15 — Unified replay.
//
// One URL per audit trail: /replay/<request_id>. The page draws a
// 5-stage horizontal stepper —
//
//   User request → Agent decision → Tool / proxy call →
//   Aegis evaluation → Outcome
//
// — sourced from a single backend join across audit_logs +
// human_override_events. Every operator surface (Incidents, Approval
// Inbox, Team employee profile) deep-links here so a SOC analyst
// reaches the full story in <5 seconds without reading docs.
//
// Visual language deliberately mirrors the rest of Aegis: the same
// status-badge colours, the same Card primitives, the same mono
// timestamps. Nothing in the page requires an external chart lib.

const STAGE_ICON = { user: User, bot: Bot, crosshair: Crosshair, shield: Shield, flag: Flag }

const DECISION_BADGE = {
  allow:    'text-green-400  bg-green-500/10  border-green-500/20',
  deny:     'text-red-400    bg-red-500/10    border-red-500/20',
  block:    'text-red-400    bg-red-500/10    border-red-500/20',
  kill:     'text-red-400    bg-red-500/10    border-red-500/20',
  escalate: 'text-amber-400  bg-amber-500/10  border-amber-500/20',
  error:    'text-red-400    bg-red-500/10    border-red-500/20',
}

const RESOLUTION_BADGE = {
  approved: 'text-green-400  bg-green-500/10  border-green-500/20',
  rejected: 'text-red-400    bg-red-500/10    border-red-500/20',
  stop:     'text-red-400    bg-red-500/10    border-red-500/20',
}

function fmtTs(ts) {
  if (!ts) return '—'
  try { return new Date(ts).toLocaleString() } catch { return String(ts) }
}

function fmtUSD(n) {
  if (n == null) return '—'
  const v = Number(n) || 0
  if (v >= 1)   return `$${v.toFixed(2)}`
  if (v > 0)    return `$${v.toFixed(4)}`
  return '$0'
}

function fmtInt(n) {
  if (n == null) return '—'
  return Number(n).toLocaleString()
}

function StageCard({ stage, isLast }) {
  const Icon = STAGE_ICON[stage.icon] || Shield
  return (
    <div className="flex-1 min-w-[220px] relative">
      <div className="rounded-xl border border-white/[0.07] bg-[#0a0a0a] p-3 space-y-2 h-full">
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-md bg-white/[0.05] flex items-center justify-center text-neutral-300">
            <Icon size={13} />
          </div>
          <span className="text-[10px] uppercase tracking-widest text-neutral-500">{stage.label}</span>
        </div>
        <StageBody stage={stage} />
      </div>
      {!isLast && (
        <div className="hidden lg:block absolute top-1/2 -right-3 -translate-y-1/2 text-neutral-700">
          <span aria-hidden="true">›</span>
        </div>
      )}
    </div>
  )
}

function StageBody({ stage }) {
  if (stage.kind === 'user_request') {
    return (
      <div className="space-y-2">
        <div className="text-xs font-semibold text-white truncate" title={stage.employee_email}>
          {stage.employee_email || 'unknown user'}
        </div>
        {stage.prompt_excerpt && (
          <div className="text-[11px] text-neutral-400 leading-snug line-clamp-3">
            “{stage.prompt_excerpt}”
          </div>
        )}
        <div className="text-[10px] text-neutral-600 font-mono">{fmtTs(stage.at)}</div>
      </div>
    )
  }
  if (stage.kind === 'agent_decision') {
    return (
      <div className="space-y-1">
        <div className="text-xs font-semibold text-white font-mono truncate">
          {stage.model || '—'}
        </div>
        <div className="text-[10px] text-neutral-500">
          provider <span className="text-neutral-300">{stage.upstream_provider || '—'}</span>
        </div>
        <div className="text-[10px] text-neutral-500 font-mono">
          in {fmtInt(stage.input_tokens)} · out {fmtInt(stage.output_tokens)} · {fmtUSD(stage.cost_usd)}
        </div>
      </div>
    )
  }
  if (stage.kind === 'tool_request') {
    return (
      <div className="space-y-1">
        <div className="text-xs font-semibold text-white font-mono truncate">
          {stage.tool || '—'}
        </div>
        <div className="text-[10px] text-neutral-500">
          action <span className="text-neutral-300 font-mono">{stage.action || '—'}</span>
        </div>
      </div>
    )
  }
  if (stage.kind === 'aegis_evaluation') {
    const cls = DECISION_BADGE[stage.decision] || 'text-neutral-400 bg-white/[0.03] border-white/[0.07]'
    return (
      <div className="space-y-2">
        <span className={`status-badge ${cls}`}>{(stage.decision || '—').toUpperCase()}</span>
        {stage.matched_pattern && (
          <div className="text-[11px] text-neutral-400">
            <div className="text-[10px] uppercase tracking-widest text-neutral-600">Pattern</div>
            <div className="font-mono">{stage.matched_pattern}</div>
          </div>
        )}
        {stage.approver_role && (
          <div className="text-[11px] text-amber-300">
            <div className="text-[10px] uppercase tracking-widest text-amber-500/80">Approver</div>
            <div className="font-mono">{stage.approver_role}</div>
          </div>
        )}
        {stage.policy_pack && (
          <div className="text-[11px] text-neutral-400">
            <div className="text-[10px] uppercase tracking-widest text-neutral-600">Pack</div>
            <div className="font-mono">{stage.policy_pack}</div>
            {(stage.framework_controls || []).length > 0 && (
              <div className="flex flex-wrap gap-1 mt-1">
                {(stage.framework_controls || []).map((c) => (
                  <span key={c} className="inline-flex items-center text-[9px] text-neutral-300 px-1.5 py-0.5 rounded-md bg-white/[0.04] border border-white/[0.06] font-mono">
                    {c}
                  </span>
                ))}
              </div>
            )}
          </div>
        )}
        {(stage.findings || []).length > 0 && (
          <div className="text-[11px] text-neutral-400">
            <div className="text-[10px] uppercase tracking-widest text-neutral-600">Findings</div>
            <div className="flex flex-wrap gap-1">
              {stage.findings.map((f) => (
                <span key={f} className="font-mono text-[10px]">{f}</span>
              ))}
            </div>
          </div>
        )}
        {stage.latency_ms ? (
          <div className="text-[10px] text-neutral-600 font-mono">{stage.latency_ms}ms</div>
        ) : null}
      </div>
    )
  }
  if (stage.kind === 'outcome') {
    const resCls = RESOLUTION_BADGE[stage.resolution] || 'text-neutral-400 bg-white/[0.03] border-white/[0.07]'
    return (
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <span className="text-[10px] uppercase tracking-widest text-neutral-500">HTTP</span>
          <span className="text-sm font-mono text-white">{stage.status_code ?? '—'}</span>
        </div>
        {stage.resolution && (
          <span className={`status-badge ${resCls}`}>{stage.resolution.toUpperCase()}</span>
        )}
        {(stage.override_events || []).length > 0 && (
          <div className="space-y-1">
            <div className="text-[10px] uppercase tracking-widest text-neutral-600">Operator events</div>
            {stage.override_events.map((o, i) => (
              <div key={i} className="text-[10px] text-neutral-400 leading-snug">
                <span className="font-mono">{o.event_type}</span>
                {o.actor_role ? ` · ${o.actor_role}` : ''}
                {o.actor      ? ` · ${o.actor}`      : ''}
                {o.reason     ? <div className="text-neutral-500 italic">“{o.reason}”</div> : null}
              </div>
            ))}
          </div>
        )}
      </div>
    )
  }
  return null
}

export default function Replay() {
  const { request_id } = useParams()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const resp = await replayService.get(request_id)
      setData(resp?.data || resp)
      setError('')
    } catch (e) {
      setError(e?.message || 'Failed to load replay')
      setData(null)
    } finally {
      setLoading(false)
    }
  }, [request_id])
  useEffect(() => { load() }, [load])

  if (loading) {
    return (
      <div className="p-6 text-xs text-neutral-500 flex items-center justify-center min-h-[60vh]">
        <Loader2 size={20} className="animate-spin mr-2" /> Loading replay…
      </div>
    )
  }
  if (error || !data) {
    return (
      <div className="p-6 space-y-3">
        <Link to="/incidents" className="inline-flex items-center gap-1 text-xs text-neutral-400 hover:text-white">
          <ArrowLeft size={14} /> Back to Incidents
        </Link>
        <Card>
          <div className="text-xs text-neutral-300 py-6 text-center space-y-2">
            <AlertTriangle size={20} className="text-amber-400 mx-auto" />
            <div>{error || 'No replay data'}</div>
            <Button size="sm" onClick={load}><RefreshCw size={12} /> Retry</Button>
          </div>
        </Card>
      </div>
    )
  }

  const aegis = data.stages.find((s) => s.kind === 'aegis_evaluation') || {}
  const outcome = data.stages.find((s) => s.kind === 'outcome') || {}

  return (
    <div className="p-4 lg:p-6 space-y-4 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div className="space-y-2">
          <Link to="/incidents" className="inline-flex items-center gap-1 text-xs text-neutral-400 hover:text-white">
            <ArrowLeft size={14} /> Incidents
          </Link>
          <div className="flex items-center gap-3 flex-wrap">
            <h1 className="text-2xl font-bold text-white">Replay</h1>
            {aegis.decision && (
              <span className={`status-badge ${DECISION_BADGE[aegis.decision] || ''}`}>
                {(aegis.decision || '').toUpperCase()}
              </span>
            )}
            {outcome.resolution && (
              <span className={`status-badge ${RESOLUTION_BADGE[outcome.resolution] || ''}`}>
                {(outcome.resolution || '').toUpperCase()}
              </span>
            )}
          </div>
          <div className="text-[11px] text-neutral-500 font-mono">request_id {request_id}</div>
        </div>
        <Button variant="secondary" size="sm" onClick={load}><RefreshCw size={12} /> Refresh</Button>
      </div>

      {/* The 5-stage stepper */}
      <div className="flex flex-col lg:flex-row gap-3 lg:gap-6 items-stretch">
        {data.stages.map((s, i) => (
          <StageCard key={s.kind} stage={s} isLast={i === data.stages.length - 1} />
        ))}
      </div>

      {/* Raw audit + override JSON dropdown — for the analyst who wants the bytes */}
      <Card title="Raw audit rows" icon={Shield}>
        <details className="text-xs">
          <summary className="cursor-pointer text-neutral-400 hover:text-white">
            {(data.audit_rows || []).length} audit row{((data.audit_rows || []).length === 1 ? '' : 's')} ·
            {' '}{(data.override_events || []).length} override event{((data.override_events || []).length === 1 ? '' : 's')}
            {' '}— click to expand
          </summary>
          <pre className="mt-3 p-3 bg-[#050505] border border-white/[0.06] rounded-xl text-[10px] font-mono text-neutral-300 overflow-x-auto whitespace-pre-wrap">
{JSON.stringify({ audit_rows: data.audit_rows, override_events: data.override_events }, null, 2)}
          </pre>
        </details>
      </Card>
    </div>
  )
}
