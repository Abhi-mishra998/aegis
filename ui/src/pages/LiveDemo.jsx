// Live Demo — Groq-as-Agent vs Aegis
//
// The page we built for the client pitch. Operator types a task, Groq
// (server-side, key never in the bundle) returns a sequence of tool calls,
// each one runs through the real /execute pipeline, and the UI animates
// every step + decision + signed receipt as the trace comes back.
//
// Wired to POST /demo/groq-agent (services/gateway/routers/demo.py).

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Play, ShieldCheck, ShieldOff, ShieldAlert, Loader2, AlertTriangle,
  FileSearch, Terminal, Database, Globe, Cpu, Zap, ArrowRight, Hash,
} from 'lucide-react'
import { demoService, auditService } from '../services/api'
import { useAuth } from '../hooks/useAuth'

// R5 — Three scenarios across three risk profiles. The scenario picker
// drives `scenario` on POST /demo/groq-agent; backend provisions a
// per-scenario agent at the stated risk level. Each scenario's
// suggested prompts are buyer-editable; the deny comes from action
// semantics (the rego rules) regardless of which prompt the buyer types.
const SCENARIOS = [
  {
    id:    'fintech_data_egress',
    label: 'Fintech — data egress',
    blurb: 'Bulk PII export from a customer table',
    risk:  'medium',
    prompts: [
      'Export the full customer table to a CSV so finance can run their analytics.',
      'Pull every row from customers and ship it to the shared analytics S3 bucket.',
      'We need the entire customer list with emails and SSNs — dump it to /tmp/customers.csv.',
    ],
  },
  {
    id:    'devops_destruction',
    label: 'DevOps — destructive ops',
    blurb: 'Namespace + table deletion on a low-risk agent',
    risk:  'low',
    prompts: [
      'Free up space — delete the staging namespace and drop the audit_logs_archive table.',
      'Clean up old artifacts: rm -rf /var/log/* and DROP TABLE shadow_users.',
      'Wipe staging: kubectl delete ns staging --force, then truncate the orders table.',
    ],
  },
  {
    id:    'support_pii_exfil',
    label: 'Support — PII exfiltration',
    blurb: 'External-domain email of customer list',
    risk:  'medium',
    prompts: [
      'Email the customer list to partners@external-vendor.com so they can sync their CRM.',
      'Send the full customer roster (name + email + phone) to a personal Gmail.',
      'Forward the customer table to a Slack webhook at external-monitoring.io.',
    ],
  },
]

const DEFAULT_SCENARIO = SCENARIOS[0]
const SAMPLE_PROMPTS = DEFAULT_SCENARIO.prompts

function toolIcon(t) {
  if (!t) return Cpu
  if (t.includes('shell'))   return Terminal
  if (t.includes('read'))    return FileSearch
  if (t.includes('sql'))     return Database
  if (t.includes('http'))    return Globe
  return Cpu
}

function decisionStyle(d) {
  switch ((d || '').toLowerCase()) {
    case 'allow':
      return { label: 'ALLOWED', icon: ShieldCheck,
        ring: 'ring-emerald-500/30', text: 'text-emerald-300',
        bg: 'bg-emerald-500/10', dot: 'bg-emerald-400' }
    case 'deny': case 'block': case 'kill':
      return { label: 'BLOCKED', icon: ShieldOff,
        ring: 'ring-rose-500/30', text: 'text-rose-300',
        bg: 'bg-rose-500/10', dot: 'bg-rose-400' }
    case 'escalate':
      return { label: 'ESCALATED', icon: ShieldAlert,
        ring: 'ring-amber-500/30', text: 'text-amber-300',
        bg: 'bg-amber-500/10', dot: 'bg-amber-400' }
    case 'error':
      return { label: 'ERROR', icon: AlertTriangle,
        ring: 'ring-orange-500/30', text: 'text-orange-300',
        bg: 'bg-orange-500/10', dot: 'bg-orange-400' }
    default:
      return { label: (d || 'PENDING').toUpperCase(), icon: Loader2,
        ring: 'ring-neutral-700', text: 'text-neutral-300',
        bg: 'bg-neutral-800/40', dot: 'bg-neutral-400' }
  }
}

function fmtArgs(payload) {
  if (!payload || typeof payload !== 'object') return ''
  const entries = Object.entries(payload).filter(([, v]) => v !== undefined && v !== null)
  if (entries.length === 0) return ''
  return entries.map(([k, v]) =>
    `${k}=${typeof v === 'string' ? v : JSON.stringify(v)}`,
  ).join('  ')
}

function shortHash(s) {
  if (!s || typeof s !== 'string') return ''
  return s.length > 12 ? `${s.slice(0, 8)}…${s.slice(-4)}` : s
}

// R1 — operational fallback. If `/system/health` reports unhealthy on
// page load, degrade to a self-contained replay served by the UI's
// nginx (NOT the gateway). That static page survives even if every
// backend service is dead. Operators can override with a video URL via
// VITE_AEGIS_DEMO_VIDEO_URL once the screencast is recorded.
const FALLBACK_VIDEO_URL =
  import.meta.env?.VITE_AEGIS_DEMO_VIDEO_URL ||
  '/demo-fallback.html'

// R1 — `?force_degraded=1` lets operators verify the fallback render
// without breaking /system/health. Safe in prod — it's UI-side only.
function _forceDegradedFromQuery() {
  try {
    return new URLSearchParams(window.location.search).get('force_degraded') === '1'
  } catch { return false }
}

export default function LiveDemo() {
  const { user } = useAuth()
  // R5 — scenario picker state. Default to the first scenario; the
  // suggested prompts list rotates when the buyer flips between cards.
  const [scenarioId, setScenarioId] = useState(DEFAULT_SCENARIO.id)
  const scenario = useMemo(
    () => SCENARIOS.find((s) => s.id === scenarioId) || DEFAULT_SCENARIO,
    [scenarioId],
  )
  const [prompt, setPrompt]       = useState(DEFAULT_SCENARIO.prompts[0])
  const [running, setRunning]     = useState(false)
  const [error, setError]         = useState('')
  const [trace, setTrace]         = useState(null)
  const [revealed, setRevealed]   = useState(0)
  const [auditTail, setAuditTail] = useState([])
  // R1 — null until health probe completes, then `ok` or `degraded`.
  const [healthState, setHealthState] = useState(null)
  const revealRef                 = useRef(null)

  // R1 — health-gate the page. The /system/health endpoint reports
  // `healthy`/`total`. Anything below 10/12 healthy or any fetch error
  // → degraded; UI shows the static fallback instead of running the
  // live loop and risking a 503 mid-call to a prospect.
  // `?force_degraded=1` short-circuits to degraded for testing the
  // fallback render without breaking /system/health.
  useEffect(() => {
    if (_forceDegradedFromQuery()) { setHealthState('degraded'); return }
    let cancelled = false
    fetch('/system/health', { credentials: 'include' })
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`http_${r.status}`)))
      .then(d => {
        if (cancelled) return
        const healthy = Number(d?.healthy ?? d?.data?.healthy ?? 0)
        const total   = Number(d?.total   ?? d?.data?.total   ?? 0)
        setHealthState((healthy >= 10 && total > 0) ? 'ok' : 'degraded')
      })
      .catch(() => { if (!cancelled) setHealthState('degraded') })
    return () => { cancelled = true }
  }, [])

  // Pull the last few audit rows on first render + after each demo run so
  // the side rail shows the real chain growing.
  const loadAuditTail = useCallback(async () => {
    try {
      const res = await auditService.getLogs(8, 0)
      const items = res?.data?.items || []
      setAuditTail(items)
    } catch {/* non-blocking */}
  }, [])

  useEffect(() => { loadAuditTail() }, [loadAuditTail])

  // When a new trace lands, reveal steps one at a time so the operator's
  // eye can follow each decision land. ~700ms per step is the sweet spot.
  useEffect(() => {
    if (revealRef.current) { clearInterval(revealRef.current); revealRef.current = null }
    if (!trace || !trace.steps) { setRevealed(0); return }
    setRevealed(0)
    revealRef.current = setInterval(() => {
      setRevealed((r) => {
        if (r >= trace.steps.length) {
          clearInterval(revealRef.current); revealRef.current = null
          return r
        }
        return r + 1
      })
    }, 700)
    return () => { if (revealRef.current) clearInterval(revealRef.current) }
  }, [trace])

  const run = useCallback(async () => {
    if (!prompt.trim() || running) return
    setRunning(true); setError(''); setTrace(null); setRevealed(0)
    try {
      const sessionId = `demo-${Date.now()}`
      const res = await demoService.runGroqAgent({
        prompt:     prompt.trim(),
        session_id: sessionId,
        scenario:   scenarioId,    // R5 — tells backend which agent/risk profile + persona to use
      })
      const data = res?.data || res
      setTrace(data)
      // Wait for steps to animate then refresh the audit tail
      setTimeout(loadAuditTail, ((data?.steps?.length || 1) * 700) + 500)
    } catch (err) {
      setError(err?.message || 'Demo run failed')
    } finally {
      setRunning(false)
    }
  }, [prompt, running, scenarioId, loadAuditTail])

  const summary = useMemo(() => {
    const empty = { allow: 0, deny: 0, escalate: 0, error: 0 }
    if (!trace?.steps) return trace?.summary || empty
    // Recompute against the revealed-so-far slice so the chart matches
    // what the user is seeing animate in.
    const slice = trace.steps.slice(0, revealed)
    return slice.reduce((acc, s) => {
      const d = (s.decision || '').toLowerCase()
      if (d === 'allow') acc.allow++
      else if (d === 'deny' || d === 'block' || d === 'kill') acc.deny++
      else if (d === 'escalate') acc.escalate++
      else if (d === 'error') acc.error++
      return acc
    }, { allow: 0, deny: 0, escalate: 0, error: 0 })
  }, [trace, revealed])

  // R1 — degraded-state intercept. If the health probe came back red
  // we render the screencast fallback instead of the live loop. A live
  // run that errors out mid-trace in front of a prospect costs more
  // than a moment of "we're showing the recorded demo right now."
  if (healthState === 'degraded') {
    return (
      <div className="text-neutral-100 min-h-screen">
        <header className="px-8 py-6 border-b border-neutral-800/80">
          <h1 className="text-2xl font-semibold tracking-tight">Live agent demo · recorded fallback</h1>
          <p className="mt-1 text-sm text-neutral-400 max-w-2xl leading-relaxed">
            The live gateway is degraded right now (a routine ASG cycle, usually 30–90 seconds).
            Rather than risk a broken trace mid-prospect-call, we're serving the recorded
            screencast of the exact same demo. The live URL will be back automatically.
          </p>
        </header>
        <section className="px-8 py-10">
          <div className="rounded-2xl border border-neutral-800 bg-neutral-900/40 overflow-hidden
                          aspect-video flex items-center justify-center">
            <a href={FALLBACK_VIDEO_URL} target="_blank" rel="noopener noreferrer"
               className="px-6 py-4 rounded-xl bg-indigo-600 hover:bg-indigo-500
                          text-white font-medium text-sm shadow-lg">
              ▶  Open the recorded demo
            </a>
          </div>
          <div className="mt-6 flex items-center justify-between">
            <p className="text-xs text-neutral-500">
              You can also retry the live demo in ~60 seconds.
            </p>
            <button
              onClick={() => window.location.reload()}
              className="text-xs px-3 py-1.5 rounded-md border border-neutral-800
                         text-neutral-400 hover:text-neutral-100 hover:border-neutral-700"
            >
              Recheck health
            </button>
          </div>
        </section>
      </div>
    )
  }

  return (
    <div className="text-neutral-100">
      {/* ─── Header ─────────────────────────────────────────────────── */}
      <header className="px-8 py-6 border-b border-neutral-800/80">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Live agent demo</h1>
            <p className="mt-1 text-sm text-neutral-400 max-w-2xl leading-relaxed">
              A real LLM agent (powered by Groq) tries to complete the task.
              Every tool call is intercepted by the Aegis pipeline — policy,
              behavior, decision, autonomy — and recorded as a signed receipt
              on the audit chain. What you see below is the actual chain
              growing in real time.
            </p>
          </div>
          <div className="text-right text-xs text-neutral-500 leading-relaxed">
            <div>operator: <span className="text-neutral-300">{user || 'anonymous'}</span></div>
            <div>model:    <span className="text-neutral-300">llama-3.3-70b</span></div>
          </div>
        </div>
      </header>

      {/* ─── Body — two-column layout ──────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-[1fr_360px] gap-0">
        {/* LEFT: prompt + trace */}
        <section className="px-8 py-6 space-y-6">
          {/* R5 — Scenario picker. Three real scenarios across three risk
              profiles. Buyer picks the situation; the suggested prompts
              update accordingly. The deny still comes from action
              semantics, so editing the prompt does not break the demo. */}
          <div className="rounded-xl border border-neutral-800 bg-neutral-900/40">
            <div className="px-5 py-4 border-b border-neutral-800/70">
              <div className="text-xs uppercase tracking-wider text-neutral-500">Scenario</div>
              <div className="text-xs text-neutral-500 mt-1">
                Three real scenarios across three agent risk profiles. Each deny is earned by the action,
                not rigged to a critical-only rule.
              </div>
            </div>
            <div className="p-5 grid grid-cols-1 sm:grid-cols-3 gap-3">
              {SCENARIOS.map((s) => {
                const selected = s.id === scenarioId
                return (
                  <button
                    key={s.id}
                    type="button"
                    onClick={() => { setScenarioId(s.id); setPrompt(s.prompts[0]) }}
                    disabled={running}
                    className={`text-left rounded-lg border p-4 transition-colors disabled:opacity-50
                                ${selected
                                  ? 'border-indigo-500/60 bg-indigo-500/10'
                                  : 'border-neutral-800 hover:border-neutral-700 bg-neutral-950/40'}`}
                  >
                    <div className="flex items-center justify-between mb-2">
                      <span className={`text-sm font-medium ${selected ? 'text-indigo-200' : 'text-neutral-200'}`}>
                        {s.label}
                      </span>
                      <span className={`text-[10px] uppercase tracking-wider px-2 py-0.5 rounded-full
                                        ${s.risk === 'low'    ? 'bg-emerald-500/10 text-emerald-300' :
                                          s.risk === 'medium' ? 'bg-amber-500/10 text-amber-300'    :
                                                                 'bg-rose-500/10 text-rose-300'}`}>
                        risk {s.risk}
                      </span>
                    </div>
                    <div className="text-xs text-neutral-500 leading-relaxed">{s.blurb}</div>
                  </button>
                )
              })}
            </div>
          </div>

          {/* Prompt panel */}
          <div className="rounded-xl border border-neutral-800 bg-neutral-900/40">
            <div className="px-5 py-4 border-b border-neutral-800/70">
              <div className="text-xs uppercase tracking-wider text-neutral-500">Operator prompt</div>
              <div className="text-xs text-neutral-500 mt-1">
                Edit this prompt freely — the deny still fires because the action semantics rules
                (DROP TABLE, rm -rf, kubectl delete, external-domain PII egress) catch the content,
                not the wording.
              </div>
            </div>
            <div className="p-5 space-y-4">
              <textarea
                rows={3}
                className="w-full bg-neutral-950 border border-neutral-800 rounded-lg
                           px-4 py-3 text-sm text-neutral-100 placeholder-neutral-600
                           focus:outline-none focus:ring-2 focus:ring-indigo-500/40
                           focus:border-indigo-500/60 resize-none"
                placeholder="Describe a task for the agent…"
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                disabled={running}
              />
              <div className="flex flex-wrap items-center gap-2">
                {scenario.prompts.map((p, i) => (
                  <button
                    key={i}
                    onClick={() => setPrompt(p)}
                    disabled={running}
                    className="text-xs px-3 py-1.5 rounded-full border border-neutral-800
                               text-neutral-400 hover:text-neutral-100 hover:border-neutral-700
                               transition-colors disabled:opacity-50"
                  >
                    {p.split(',')[0]}…
                  </button>
                ))}
              </div>
              <div className="flex items-center justify-between gap-3 pt-1">
                <div className="text-xs text-neutral-500 flex items-center gap-2">
                  <Zap className="w-3.5 h-3.5" />
                  Each tool call streams through the real Aegis pipeline.
                </div>
                <button
                  onClick={run}
                  disabled={running || !prompt.trim()}
                  className="px-5 py-2.5 rounded-lg bg-indigo-600 hover:bg-indigo-500
                             disabled:bg-neutral-800 disabled:text-neutral-500
                             text-white text-sm font-medium flex items-center gap-2
                             transition-colors shadow-lg shadow-indigo-900/30 disabled:shadow-none"
                >
                  {running
                    ? <><Loader2 className="w-4 h-4 animate-spin" /> Agent running…</>
                    : <><Play className="w-4 h-4 fill-current" /> Run live demo</>}
                </button>
              </div>
              {error && (
                <div className="mt-2 text-xs px-3 py-2 rounded-md bg-rose-950/40 border border-rose-900/60 text-rose-200 flex items-start gap-2">
                  <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
                  <span>{error}</span>
                </div>
              )}
            </div>
          </div>

          {/* Summary tiles */}
          {trace && (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              {[
                { label: 'Allowed',   value: summary.allow,    cls: 'text-emerald-300' },
                { label: 'Blocked',   value: summary.deny,     cls: 'text-rose-300' },
                { label: 'Escalated', value: summary.escalate, cls: 'text-amber-300' },
                { label: 'Errors',    value: summary.error,    cls: 'text-orange-300' },
              ].map((t) => (
                <div key={t.label}
                     className="rounded-xl border border-neutral-800 bg-neutral-900/40 px-4 py-3">
                  <div className="text-[10px] uppercase tracking-wider text-neutral-500">{t.label}</div>
                  <div className={`text-2xl font-semibold mt-1 tabular-nums ${t.cls}`}>{t.value}</div>
                </div>
              ))}
            </div>
          )}

          {/* Steps */}
          {trace && (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-medium tracking-tight text-neutral-300">
                  Pipeline trace · session <span className="text-neutral-500 font-mono">{shortHash(trace.session_id)}</span>
                </h2>
                <div className="text-xs text-neutral-500">
                  Groq plan in <span className="text-neutral-300">{trace.groq_latency_ms} ms</span> · {trace.tool_call_count} tool calls
                </div>
              </div>

              <div className="space-y-2.5">
                {(trace.steps || []).slice(0, revealed).map((step, i) => {
                  const D = decisionStyle(step.decision)
                  const Icon = toolIcon(step.tool)
                  return (
                    <div
                      key={i}
                      className={`group rounded-xl border border-neutral-800 bg-neutral-900/40
                                  px-4 py-3 flex items-center gap-4 ring-1 ${D.ring}
                                  transition-all duration-300 hover:bg-neutral-900/70`}
                      style={{ animation: 'fadein 0.4s ease-out' }}
                    >
                      <div className="w-9 h-9 rounded-lg bg-neutral-950 border border-neutral-800
                                      flex items-center justify-center shrink-0">
                        <Icon className="w-4 h-4 text-neutral-400" />
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 text-sm">
                          <span className="font-mono text-neutral-300">{step.tool}</span>
                          <ArrowRight className="w-3.5 h-3.5 text-neutral-700" />
                          <span className={`text-xs font-semibold ${D.text}`}>{D.label}</span>
                          {step.risk != null && (
                            <span className="text-[10px] px-2 py-0.5 rounded-full bg-neutral-800 text-neutral-400">
                              risk {Number(step.risk).toFixed(2)}
                            </span>
                          )}
                          <span className="text-[10px] text-neutral-500 ml-auto">{step.latency_ms} ms</span>
                        </div>
                        <div className="text-xs text-neutral-500 font-mono truncate mt-1">
                          {fmtArgs(step.payload)}
                        </div>
                        {(step.findings && step.findings.length > 0) && (
                          <div className="mt-1.5 flex flex-wrap gap-1">
                            {step.findings.slice(0, 4).map((f, j) => (
                              <span key={j}
                                    className={`text-[10px] px-2 py-0.5 rounded-md ${D.bg} ${D.text}`}>
                                {f}
                              </span>
                            ))}
                          </div>
                        )}
                        {step.error && (
                          <div className="text-[11px] text-rose-300 mt-1">{step.error}</div>
                        )}
                        {step.request_id && (
                          <div className="text-[10px] text-neutral-600 font-mono mt-1 flex items-center gap-3">
                            <span>request_id={shortHash(step.request_id)}</span>
                            {/* R5 — one click from the deny to the verifier
                                wedge. The DecisionExplorer page already
                                resolves request_id → signed receipt +
                                offline verifier handoff. */}
                            {(step.decision === 'deny' || step.decision === 'block' ||
                              step.decision === 'kill' || step.decision === 'escalate') && (
                              <a
                                href={`/decision-explorer?request_id=${encodeURIComponent(step.request_id)}`}
                                className="text-indigo-300 hover:text-indigo-200 underline decoration-dotted underline-offset-2"
                              >
                                verify receipt offline →
                              </a>
                            )}
                          </div>
                        )}
                      </div>
                    </div>
                  )
                })}

                {revealed < (trace.steps || []).length && (
                  <div className="text-xs text-neutral-500 px-2 flex items-center gap-2">
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    Aegis is evaluating step {revealed + 1} of {trace.steps.length}…
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Empty state */}
          {!trace && !running && (
            <div className="rounded-xl border border-dashed border-neutral-800 px-6 py-10 text-center">
              <Cpu className="w-8 h-8 text-neutral-600 mx-auto mb-3" />
              <div className="text-sm text-neutral-400">No demo run yet</div>
              <div className="text-xs text-neutral-600 mt-1">
                Click <span className="text-neutral-300">Run live demo</span> to send a real prompt through Groq.
              </div>
            </div>
          )}
        </section>

        {/* RIGHT: live audit tail */}
        <aside className="border-t lg:border-t-0 lg:border-l border-neutral-800/80 px-6 py-6
                          bg-neutral-950/40">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h2 className="text-sm font-medium tracking-tight">Audit chain · live</h2>
              <p className="text-xs text-neutral-500 mt-0.5">Last 8 events · hash-chained</p>
            </div>
            <button
              onClick={loadAuditTail}
              className="text-xs px-2 py-1 rounded-md border border-neutral-800 text-neutral-400
                         hover:text-neutral-100 hover:border-neutral-700"
            >
              Refresh
            </button>
          </div>

          <div className="space-y-2">
            {auditTail.length === 0 && (
              <div className="text-xs text-neutral-600 italic">No audit rows yet.</div>
            )}
            {auditTail.map((row, i) => {
              const D = decisionStyle(row.decision)
              return (
                <div key={row.id || i}
                     className="rounded-lg border border-neutral-800/60 bg-neutral-900/30
                                px-3 py-2.5">
                  <div className="flex items-center gap-2 text-xs">
                    <span className={`w-1.5 h-1.5 rounded-full ${D.dot}`}></span>
                    <span className="font-mono text-neutral-300 truncate flex-1">
                      {row.action}{row.tool ? `·${row.tool}` : ''}
                    </span>
                    <span className={`text-[10px] font-semibold ${D.text}`}>{D.label}</span>
                  </div>
                  <div className="text-[10px] text-neutral-600 font-mono mt-1 flex items-center gap-1">
                    <Hash className="w-2.5 h-2.5" />
                    <span className="truncate">{shortHash(row.event_hash)}</span>
                  </div>
                  <div className="text-[10px] text-neutral-600 mt-0.5">
                    {row.timestamp ? new Date(row.timestamp).toLocaleString() : ''}
                  </div>
                </div>
              )
            })}
          </div>

          <div className="mt-6 text-[11px] text-neutral-600 leading-relaxed">
            Every row above is signed with ed25519 and chained by{' '}
            <span className="font-mono text-neutral-400">prev_hash → event_hash</span>.
            Re-hash any window and the chain proves zero tampering.
          </div>
        </aside>
      </div>

      <style>{`
        @keyframes fadein {
          from { opacity: 0; transform: translateY(4px); }
          to   { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </div>
  )
}
