// Sprint 7 — Policy Playground.
//
// Edit a candidate policy as rules_json, see the compiled Rego live,
// validate it through OPA before publishing, replay against any
// historical window of real audit_logs, and see exactly which past
// decisions change (newly_denied / newly_allowed) plus the Sprint-5
// detection/FP scores. Publish lands as a Sprint-6 ShadowPolicy in
// draft or shadow mode — promotion to enforce stays on the Sprint-6
// shadow page so the on-ramp narrative stays linear.
//
// Data sources:
//   POST /audit/playground/validate
//   POST /audit/playground/replay
//   POST /audit/playground/publish

import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  ShieldCheck, AlertTriangle, Eye, Beaker, History, Save,
  CheckCircle2, XCircle, Sigma, GitCompare, ArrowRight, EyeOff, GitMerge,
} from 'lucide-react'
import { policyPlaygroundService } from '../services/api'

function unwrap(resp) { return resp?.data ?? resp }
function fmtPct(x) { return x == null || Number.isNaN(x) ? '—' : `${(x * 100).toFixed(2)}%` }
function fmtNum(x) {
  if (x == null) return '—'
  if (x >= 1_000_000) return `${(x / 1_000_000).toFixed(1)}M`
  if (x >= 1_000)     return `${(x / 1_000).toFixed(1)}k`
  return String(x)
}
function fmtTs(s) { if (!s) return '—'; try { return new Date(s).toLocaleString() } catch { return s } }
function actionPill(a) {
  const c = {
    allow:    'text-emerald-300',
    deny:     'text-rose-300',
    throttle: 'text-amber-300',
    escalate: 'text-amber-300',
    monitor:  'text-emerald-300',
    error:    'text-neutral-400',
  }[a] || 'text-neutral-200'
  return <span className={`font-mono ${c}`}>{a}</span>
}

const STARTER_RULES = `[
  {
    "conditions": [
      { "field": "tool", "operator": "eq", "value": "tool.shell" },
      { "field": "payload_substring", "operator": "contains", "value": "rm -rf" }
    ],
    "action": "deny",
    "description": "Block destructive shell removal"
  },
  {
    "conditions": [
      { "field": "risk_score", "operator": "gte", "value": "0.85" }
    ],
    "action": "escalate",
    "description": "High-risk requests require human approval"
  }
]`

export default function PolicyPlayground() {
  const [policyName, setPolicyName] = useState('candidate_policy')
  const [agentId, setAgentId]       = useState('')
  const [rulesText, setRulesText]   = useState(STARTER_RULES)
  const [validation, setValidation] = useState(null)
  const [windowHours, setWindowHours] = useState(24)
  const [replay, setReplay]         = useState(null)
  const [selectedBucket, setSelectedBucket] = useState('newly_denied')
  const [busy, setBusy]             = useState('')
  const [error, setError]           = useState('')
  const [msg, setMsg]               = useState('')

  const parsedRules = useMemo(() => {
    try {
      const v = JSON.parse(rulesText)
      if (!Array.isArray(v)) throw new Error('rules_json must be an array')
      return { ok: true, rules: v }
    } catch (e) {
      return { ok: false, error: e.message }
    }
  }, [rulesText])

  const validate = useCallback(async () => {
    setMsg(''); setError(''); setValidation(null)
    if (!parsedRules.ok) { setError(parsedRules.error); return }
    setBusy('validate')
    try {
      const out = unwrap(await policyPlaygroundService.validate(parsedRules.rules, policyName))
      setValidation(out)
    } catch (e) {
      setError(e?.message || 'validate failed')
    } finally { setBusy('') }
  }, [parsedRules, policyName])

  const replayNow = useCallback(async () => {
    setMsg(''); setError(''); setReplay(null)
    if (!parsedRules.ok) { setError(parsedRules.error); return }
    setBusy('replay')
    try {
      const body = {
        rules: parsedRules.rules,
        window_hours: windowHours,
        limit: 1000,
        sample_limit: 50,
      }
      if (agentId.trim()) body.agent_id = agentId.trim()
      const out = unwrap(await policyPlaygroundService.replay(body))
      setReplay(out)
    } catch (e) {
      setError(e?.message || 'replay failed')
    } finally { setBusy('') }
  }, [parsedRules, windowHours, agentId])

  const publish = useCallback(async (startIn) => {
    setMsg(''); setError('')
    if (!parsedRules.ok) { setError(parsedRules.error); return }
    setBusy(`publish-${startIn}`)
    try {
      const body = {
        name: policyName,
        rules: parsedRules.rules,
        description: `Published from Policy Playground (window ${windowHours}h)`,
        sample_rate: 1.0,
        start_in: startIn,
      }
      if (agentId.trim()) body.agent_id = agentId.trim()
      const out = unwrap(await policyPlaygroundService.publish(body))
      setMsg(`Published v${out.version} as policy ${out.policy_id} in mode='${out.mode}'. Visit Shadow Mode to promote to enforce.`)
    } catch (e) {
      setError(e?.message || 'publish failed')
    } finally { setBusy('') }
  }, [parsedRules, policyName, agentId, windowHours])

  const driftSample = useMemo(() => {
    if (!replay) return []
    if (selectedBucket === 'drift') return replay.sample_drift || []
    if (selectedBucket === 'newly_denied') return replay.sample_newly_denied || []
    return replay.sample_newly_allowed || []
  }, [replay, selectedBucket])

  return (
    <div className="text-neutral-100">
      <header className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3 px-6 py-4 border-b border-neutral-800">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold inline-flex items-center gap-2">
            <Beaker size={18} /> Policy Playground
          </h1>
          <p className="text-sm text-neutral-400 mt-1">
            Edit a candidate policy, validate it against OPA, replay it
            against real historical traffic, see the exact decisions that
            change, then publish as a shadow policy. No live enforcement
            is touched until you promote on the Shadow Mode page.
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap shrink-0">
          <Link
            to="/policies?tab=editor"
            className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-md border border-neutral-700 text-neutral-200 text-xs hover:bg-neutral-800"
          >
            <GitMerge size={11} aria-hidden="true" />
            Editor
          </Link>
          <Link
            to="/policies?tab=simulator"
            className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-md border border-neutral-700 text-neutral-200 text-xs hover:bg-neutral-800"
          >
            <GitCompare size={11} aria-hidden="true" />
            Simulator
          </Link>
          <Link
            to="/shadow-mode"
            className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-md border border-emerald-700 text-emerald-200 text-xs hover:bg-emerald-900/40"
          >
            <EyeOff size={11} aria-hidden="true" />
            Shadow Mode
          </Link>
        </div>
      </header>

      {error && (
        <div className="mx-6 my-3 text-sm bg-rose-950 border border-rose-700 text-rose-100 px-3 py-2 rounded">
          {error}
        </div>
      )}
      {msg && (
        <div className="mx-6 my-3 text-sm bg-emerald-950 border border-emerald-700 text-emerald-100 px-3 py-2 rounded">
          {msg}
        </div>
      )}

      <div className="grid grid-cols-12 gap-3 px-6 py-4">
        <section className="col-span-12 lg:col-span-6 space-y-3">
          <div className="rounded-lg border border-neutral-800 bg-neutral-950 p-4">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-3">
              <label className="flex flex-col">
                <span className="text-xs text-neutral-400">Policy name</span>
                <input
                  value={policyName} onChange={(e) => setPolicyName(e.target.value)}
                  className="mt-1 bg-neutral-900 border border-neutral-700 rounded-md px-2 py-1 text-sm"
                />
              </label>
              <label className="flex flex-col">
                <span className="text-xs text-neutral-400">Replay window (h)</span>
                <input
                  type="number" min="1" max="720" value={windowHours}
                  onChange={(e) => setWindowHours(Number(e.target.value) || 24)}
                  className="mt-1 bg-neutral-900 border border-neutral-700 rounded-md px-2 py-1 text-sm tabular-nums"
                />
              </label>
              <label className="flex flex-col">
                <span className="text-xs text-neutral-400">Scope to agent (optional)</span>
                <input
                  value={agentId} onChange={(e) => setAgentId(e.target.value)}
                  placeholder="agent_id (uuid)"
                  className="mt-1 bg-neutral-900 border border-neutral-700 rounded-md px-2 py-1 text-sm font-mono"
                />
              </label>
            </div>

            <label className="block">
              <span className="text-xs text-neutral-400 inline-flex items-center gap-1">
                <Sigma size={12} /> rules_json (PolicyRule[] — shared with shadow eval)
              </span>
              <textarea
                value={rulesText}
                onChange={(e) => setRulesText(e.target.value)}
                className="mt-1 w-full bg-neutral-900 border border-neutral-700 rounded-md px-2 py-1 text-xs font-mono"
                rows={18}
              />
            </label>
            {!parsedRules.ok && (
              <div className="mt-2 text-xs text-rose-300 inline-flex items-center gap-1">
                <XCircle size={12} /> {parsedRules.error}
              </div>
            )}

            <div className="mt-3 flex flex-wrap gap-2">
              <button
                onClick={validate}
                disabled={busy === 'validate' || !parsedRules.ok}
                className="px-3 py-1.5 bg-sky-700 hover:bg-sky-600 disabled:bg-neutral-700 rounded-md text-sm inline-flex items-center gap-2"
              >
                <ShieldCheck size={14} /> {busy === 'validate' ? 'Validating…' : 'Validate (OPA)'}
              </button>
              <button
                onClick={replayNow}
                disabled={busy === 'replay' || !parsedRules.ok}
                className="px-3 py-1.5 bg-amber-700 hover:bg-amber-600 disabled:bg-neutral-700 rounded-md text-sm inline-flex items-center gap-2"
              >
                <History size={14} /> {busy === 'replay' ? 'Replaying…' : `Replay last ${windowHours}h`}
              </button>
              <button
                onClick={() => publish('draft')}
                disabled={busy.startsWith('publish') || !parsedRules.ok}
                className="px-3 py-1.5 bg-emerald-700 hover:bg-emerald-600 disabled:bg-neutral-700 rounded-md text-sm inline-flex items-center gap-2"
              >
                <Save size={14} /> Publish to draft
              </button>
              <button
                onClick={() => publish('shadow')}
                disabled={busy.startsWith('publish') || !parsedRules.ok || !(validation?.valid)}
                title={!validation?.valid ? 'Validate first — shadow mode requires valid Rego' : ''}
                className="px-3 py-1.5 bg-emerald-800 hover:bg-emerald-700 disabled:bg-neutral-700 disabled:text-neutral-400 rounded-md text-sm inline-flex items-center gap-2"
              >
                <Save size={14} /> Publish to shadow
              </button>
            </div>
          </div>

          {validation && (
            <div className={`rounded-lg border p-4 ${validation.valid ? 'border-emerald-700 bg-emerald-950/30' : 'border-rose-700 bg-rose-950/30'}`}>
              <div className="flex items-center gap-2 text-sm">
                {validation.valid
                  ? <CheckCircle2 size={16} className="text-emerald-300" />
                  : <XCircle size={16} className="text-rose-300" />}
                <span className="font-semibold">
                  {validation.valid ? 'Rego validated by OPA' : 'Rego validation failed'}
                </span>
                <span className="text-xs text-neutral-400">package <code>{validation.package_name}</code> · {validation.rule_count} rule(s)</span>
              </div>
              {(validation.errors || []).length > 0 && (
                <ul className="mt-2 list-disc list-inside text-xs text-rose-200 space-y-1">
                  {validation.errors.map((e, i) => <li key={i}><code>{e}</code></li>)}
                </ul>
              )}
              {(validation.warnings || []).length > 0 && (
                <ul className="mt-2 list-disc list-inside text-xs text-amber-200 space-y-1">
                  {validation.warnings.map((w, i) => <li key={i} className="inline-flex items-start gap-1"><AlertTriangle size={10} className="mt-0.5" />{w}</li>)}
                </ul>
              )}
              <details className="mt-2">
                <summary className="text-xs text-neutral-400 cursor-pointer">Compiled Rego</summary>
                <pre className="mt-2 p-2 bg-neutral-950 border border-neutral-800 rounded text-[11px] overflow-x-auto font-mono">{validation.rego}</pre>
              </details>
            </div>
          )}
        </section>

        <section className="col-span-12 lg:col-span-6 space-y-3">
          <div className="rounded-lg border border-neutral-800 bg-neutral-950 p-4">
            <h2 className="text-sm font-semibold inline-flex items-center gap-2 mb-3">
              <GitCompare size={14} /> Replay diff
              <span className="text-[10px] text-neutral-500">— compare candidate against the last {windowHours} hours of real traffic</span>
            </h2>
            {busy === 'replay' ? (
              <div className="space-y-3 py-2" role="status" aria-label="Running replay">
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                  {[0, 1, 2, 3].map((i) => (
                    <div key={i} className="rounded-lg border border-neutral-800 bg-neutral-900/60 px-3 py-2 animate-pulse">
                      <div className="h-2 bg-white/[0.06] rounded w-2/3 mb-2" />
                      <div className="h-5 bg-white/[0.08] rounded w-1/2" />
                    </div>
                  ))}
                </div>
                <div className="rounded border border-neutral-800 p-3 space-y-2 animate-pulse">
                  {[0, 1, 2, 3, 4].map((i) => (
                    <div key={i} className="h-2.5 bg-white/[0.05] rounded" style={{ width: `${60 + i * 7}%` }} />
                  ))}
                </div>
              </div>
            ) : !replay ? (
              <div className="py-8 text-center text-sm text-neutral-500 space-y-3">
                <p>
                  Click <strong>Replay last {windowHours}h</strong> to score this candidate against historical audit_logs.
                </p>
                <p className="text-xs text-neutral-600">
                  No historical traffic yet?{' '}
                  <Link to="/playground" className="text-emerald-300 underline hover:text-emerald-200">
                    Generate sample traffic
                  </Link>{' '}
                  via the Agent Playground first.
                </p>
              </div>
            ) : (
              <>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-3">
                  <KpiTile label="Decisions seen" value={fmtNum(replay.total_audits)} sub={`${replay.real_allow_count} allow · ${replay.real_deny_count} deny`} />
                  <KpiTile label="Agreement" value={fmtNum(replay.agreement_count)}
                           accent={replay.agreement_count === replay.total_audits ? 'text-emerald-200' : 'text-neutral-100'} />
                  <KpiTile label="Newly denied" value={fmtNum(replay.newly_denied_count)}
                           accent={replay.newly_denied_count > 0 ? 'text-rose-200' : 'text-emerald-200'}
                           sub="real allowed · draft denies" />
                  <KpiTile label="Newly allowed" value={fmtNum(replay.newly_allowed_count)}
                           accent={replay.newly_allowed_count > 0 ? 'text-amber-200' : 'text-emerald-200'}
                           sub="real denied · draft allows" />
                </div>
                <div className="grid grid-cols-2 gap-3 mb-3">
                  <KpiTile label="Detection rate" value={fmtPct(replay.detection_rate)}
                           accent={(replay.detection_rate ?? 0) >= 0.95 ? 'text-emerald-200' : 'text-amber-200'}
                           sub={`recall on ${replay.real_deny_count} historical denies`} />
                  <KpiTile label="False-positive rate" value={fmtPct(replay.fp_rate)}
                           accent={(replay.fp_rate ?? 0) > 0.05 ? 'text-rose-200' : 'text-emerald-200'}
                           sub={`FP on ${replay.real_allow_count} historical allows`} />
                </div>

                <div className="inline-flex border border-neutral-700 rounded-md overflow-hidden mb-2 text-xs">
                  {[
                    ['newly_denied',  `Newly denied (${replay.newly_denied_count})`],
                    ['newly_allowed', `Newly allowed (${replay.newly_allowed_count})`],
                    ['drift',         `All drift (${replay.drift_count})`],
                  ].map(([id, label]) => (
                    <button
                      key={id}
                      onClick={() => setSelectedBucket(id)}
                      className={`px-2 py-1 ${selectedBucket === id ? 'bg-neutral-700 text-white' : 'bg-neutral-900 text-neutral-300 hover:bg-neutral-800'}`}
                    >{label}</button>
                  ))}
                </div>

                <div className="rounded border border-neutral-800 max-h-80 overflow-y-auto">
                  <table className="min-w-full text-xs">
                    <thead className="bg-neutral-900 text-neutral-400">
                      <tr>
                        <th className="text-left px-2 py-1">When</th>
                        <th className="text-left px-2 py-1">Tool</th>
                        <th className="text-left px-2 py-1">Real</th>
                        <th className="text-left px-2 py-1">Draft</th>
                        <th className="text-left px-2 py-1">Rule</th>
                      </tr>
                    </thead>
                    <tbody>
                      {driftSample.length === 0 && (
                        <tr><td colSpan={5} className="px-2 py-4 text-center text-neutral-500">
                          No rows in this bucket — candidate matches the live pipeline on every sampled audit.
                        </td></tr>
                      )}
                      {driftSample.map((r) => (
                        <tr key={r.audit_id} className="border-t border-neutral-900">
                          <td className="px-2 py-1 text-neutral-400 whitespace-nowrap">{fmtTs(r.timestamp)}</td>
                          <td className="px-2 py-1 font-mono text-neutral-300">{r.tool}</td>
                          <td className="px-2 py-1">{actionPill(r.real_decision)}</td>
                          <td className="px-2 py-1">{actionPill(r.draft_decision)}</td>
                          <td className="px-2 py-1 text-neutral-400 max-w-xs truncate" title={r.matched_rule_description || ''}>{r.matched_rule_description || ''}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </div>

          <div className="rounded-lg border border-neutral-800 bg-neutral-950 p-3 text-xs text-neutral-400 inline-flex items-start gap-2">
            <Eye size={12} className="mt-0.5" />
            <span>
              Publishing to <strong>draft</strong> stores the candidate in version
              history without evaluating live traffic. Publishing to <strong>shadow</strong>
              starts evaluating <em>every</em> live /execute call (zero enforcement effect)
              — head to <a className="text-emerald-300 underline" href="/shadow-mode">Shadow Mode</a>
              to review the would-have-denied report and promote to enforce.
            </span>
          </div>
        </section>
      </div>
    </div>
  )
}

function KpiTile({ label, value, accent = 'text-neutral-100', sub }) {
  return (
    <div className="rounded-lg border border-neutral-800 bg-neutral-900/60 px-3 py-2">
      <div className="text-xs text-neutral-400">{label}</div>
      <div className={`text-lg font-semibold tabular-nums ${accent}`}>{value}</div>
      {sub && <div className="text-[10px] text-neutral-500">{sub}</div>}
    </div>
  )
}
