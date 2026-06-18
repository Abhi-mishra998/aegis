// Sprint 6 — Shadow-mode policies + would-have-denied report.
//
// The pilot on-ramp screen: deploy a policy in shadow, watch what it
// WOULD have blocked, promote to enforce when the false-positive count
// is acceptable. Zero effect on the live enforcement decision — the
// gateway runs shadow eval in a fire-and-forget background task.
//
// Data sources:
//   GET    /audit/shadow/policies
//   POST   /audit/shadow/policies
//   POST   /audit/shadow/policies/{id}/promote
//   POST   /audit/shadow/policies/{id}/rollback
//   GET    /audit/shadow/policies/{id}/would-have-denied
//   GET    /audit/shadow/policies/{id}/decisions
//   GET    /audit/shadow/online-eval, PUT same

import React, { useCallback, useEffect, useMemo, useState } from 'react'
import {
  EyeOff, ShieldOff, Activity, Hash, Beaker, Save, RotateCcw, Trash2,
  CheckCircle, AlertTriangle, Settings as Cog,
} from 'lucide-react'
import { shadowService } from '../services/api'

function unwrap(resp) { return resp?.data ?? resp }
function fmtPct(x) { return x == null || Number.isNaN(x) ? '—' : `${(x * 100).toFixed(2)}%` }
function fmtNum(x) {
  if (x == null) return '—'
  if (x >= 1_000_000) return `${(x / 1_000_000).toFixed(1)}M`
  if (x >= 1_000)     return `${(x / 1_000).toFixed(1)}k`
  return String(x)
}
function fmtTs(s) { if (!s) return '—'; try { return new Date(s).toLocaleString() } catch { return s } }

function modePill(mode) {
  const c = {
    draft:    'bg-amber-950 text-amber-200 border-amber-800',
    shadow:   'bg-sky-950 text-sky-200 border-sky-800',
    enforce:  'bg-emerald-950 text-emerald-200 border-emerald-800',
    archived: 'bg-neutral-800 text-neutral-300 border-neutral-700',
  }[mode] || 'bg-neutral-800 text-neutral-300 border-neutral-700'
  return <span className={`px-2 py-0.5 text-[10px] rounded-md border ${c}`}>{mode}</span>
}

function actionPill(a) {
  const c = {
    allow:    'text-emerald-300',
    deny:     'text-rose-300',
    throttle: 'text-amber-300',
    escalate: 'text-amber-300',
    error:    'text-neutral-400',
  }[a] || 'text-neutral-200'
  return <span className={`font-mono ${c}`}>{a}</span>
}

const DEFAULT_DRAFT_RULES = `[
  {
    "conditions": [
      { "field": "tool", "operator": "eq", "value": "tool.shell" },
      { "field": "payload_substring", "operator": "contains", "value": "rm -rf" }
    ],
    "action": "deny",
    "description": "Block destructive shell removal"
  }
]`

export default function ShadowMode() {
  const [policies, setPolicies] = useState([])
  const [selected, setSelected] = useState(null)
  const [report, setReport]     = useState(null)
  const [versions, setVersions] = useState([])
  const [config, setConfig]     = useState(null)
  const [creating, setCreating] = useState(false)
  const [draftName, setDraftName] = useState('')
  const [draftRules, setDraftRules] = useState(DEFAULT_DRAFT_RULES)
  const [error, setError]   = useState('')
  const [msg, setMsg]       = useState('')
  const [loading, setLoading] = useState(false)

  const fetchPolicies = useCallback(async () => {
    setLoading(true); setError('')
    try {
      const resp = await shadowService.listPolicies()
      const items = unwrap(resp) || []
      setPolicies(items)
      const cfg = unwrap(await shadowService.getOnlineEval())
      setConfig(cfg)
      if (selected && !items.find((p) => p.id === selected.id)) {
        setSelected(null)
      }
    } catch (e) {
      setError(e?.message || 'Failed to load shadow policies')
    } finally {
      setLoading(false)
    }
  }, [selected])

  useEffect(() => { fetchPolicies() }, [fetchPolicies])

  const openPolicy = useCallback(async (p) => {
    setSelected(p); setReport(null); setVersions([]); setMsg(''); setError('')
    try {
      const r = unwrap(await shadowService.wouldHaveDenied(p.id, 24, 50))
      const v = unwrap(await shadowService.listVersions(p.id))
      setReport(r)
      setVersions(v || [])
    } catch (e) {
      setError(e?.message || 'Failed to load policy detail')
    }
  }, [])

  const createDraft = async () => {
    setMsg(''); setError('')
    let parsed
    try { parsed = JSON.parse(draftRules) } catch (e) {
      setError(`rules_json invalid: ${e.message}`); return
    }
    if (!Array.isArray(parsed)) { setError('rules_json must be an array'); return }
    try {
      await shadowService.createPolicy({
        name: draftName || 'untitled shadow draft',
        rules_json: parsed,
        sample_rate: 1.0,
      })
      setCreating(false); setDraftName(''); setDraftRules(DEFAULT_DRAFT_RULES)
      setMsg(`Draft created. Promote to 'shadow' to start evaluating live traffic.`)
      await fetchPolicies()
    } catch (e) {
      setError(e?.message || 'Create failed')
    }
  }

  const promote = async (target) => {
    if (!selected) return
    setMsg(''); setError('')
    try {
      await shadowService.promotePolicy(selected.id, target)
      setMsg(`Promoted to '${target}'. Gateway will pick up within 1 request.`)
      await fetchPolicies()
      await openPolicy({ ...selected })
    } catch (e) { setError(e?.message || 'Promotion failed') }
  }

  const rollback = async (version) => {
    if (!selected) return
    setMsg(''); setError('')
    try {
      await shadowService.rollbackPolicy(selected.id, version)
      setMsg(`Rolled back to version ${version}.`)
      await fetchPolicies()
      await openPolicy({ ...selected })
    } catch (e) { setError(e?.message || 'Rollback failed') }
  }

  const archive = async () => {
    if (!selected) return
    if (!window.confirm(`Archive shadow policy '${selected.name}'? Gateway stops evaluating it on the next request.`)) return
    setMsg(''); setError('')
    try {
      await shadowService.archivePolicy(selected.id)
      setMsg('Archived.')
      setSelected(null)
      await fetchPolicies()
    } catch (e) { setError(e?.message || 'Archive failed') }
  }

  const saveConfig = async (next) => {
    setMsg(''); setError('')
    try {
      const out = unwrap(await shadowService.putOnlineEval(next))
      setConfig(out)
      setMsg('Online-eval config saved.')
    } catch (e) { setError(e?.message || 'Save failed') }
  }

  const fpHeadline = useMemo(() => {
    if (!report || report.real_allow_count === 0) return null
    return report.would_have_blocked_benign_count / report.real_allow_count
  }, [report])

  return (
    <div className="text-neutral-100">
      <header className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 px-6 py-4 border-b border-neutral-800">
        <div>
          <h1 className="text-xl font-semibold inline-flex items-center gap-2">
            <EyeOff size={18} /> Shadow Mode
          </h1>
          <p className="text-sm text-neutral-400 mt-1">
            Evaluate a candidate policy on 100% of live <code>/execute</code> traffic without
            changing what the pipeline actually decides. Promote only after the
            "would-have-blocked-benign" number reaches zero. This is the on-ramp
            that converts an evaluation into a production pilot.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setCreating((v) => !v)}
            className="px-3 py-1.5 bg-sky-700 hover:bg-sky-600 rounded-md text-sm inline-flex items-center gap-2"
          >
            <Beaker size={14} />
            {creating ? 'Cancel' : 'New draft'}
          </button>
          <button
            onClick={fetchPolicies}
            className="px-3 py-1.5 bg-neutral-800 hover:bg-neutral-700 rounded-md text-sm"
            disabled={loading}
          >
            {loading ? 'Loading…' : 'Refresh'}
          </button>
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

      {creating && (
        <div className="px-6 py-3 border-b border-neutral-800 bg-neutral-950">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <div>
              <label className="text-xs text-neutral-400">Name</label>
              <input
                value={draftName} onChange={(e) => setDraftName(e.target.value)}
                placeholder="block-rm-rf"
                className="mt-1 w-full bg-neutral-900 border border-neutral-700 rounded-md px-2 py-1 text-sm"
              />
            </div>
            <div className="md:col-span-2">
              <label className="text-xs text-neutral-400">rules_json (PolicyRule[] — same shape /policy/simulate uses)</label>
              <textarea
                value={draftRules} onChange={(e) => setDraftRules(e.target.value)}
                className="mt-1 w-full bg-neutral-900 border border-neutral-700 rounded-md px-2 py-1 text-xs font-mono"
                rows={8}
              />
            </div>
          </div>
          <div className="mt-3 flex justify-end">
            <button
              onClick={createDraft}
              className="px-3 py-1.5 bg-emerald-700 hover:bg-emerald-600 rounded-md text-sm inline-flex items-center gap-2"
            >
              <Save size={14} /> Save draft
            </button>
          </div>
        </div>
      )}

      <div className="grid grid-cols-12 gap-3 px-6 py-4">
        <aside className="col-span-12 lg:col-span-4 rounded-lg border border-neutral-800 bg-neutral-950 overflow-hidden">
          <div className="px-3 py-2 border-b border-neutral-800 text-xs text-neutral-400 inline-flex items-center gap-2">
            <Hash size={12} /> Policies ({policies.length})
          </div>
          <div className="divide-y divide-neutral-900">
            {policies.length === 0 && (
              <div className="p-4 text-sm text-neutral-500">No shadow policies yet.</div>
            )}
            {policies.map((p) => (
              <button
                key={p.id}
                onClick={() => openPolicy(p)}
                className={`w-full text-left px-3 py-2 hover:bg-neutral-900 ${selected?.id === p.id ? 'bg-neutral-900' : ''}`}
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="truncate">
                    <div className="text-sm text-neutral-200 truncate">{p.name}</div>
                    <div className="text-[10px] text-neutral-500 truncate">{p.id.slice(0, 8)}… · v{p.version} · {p.agent_id ? `agent ${p.agent_id.slice(0,8)}…` : 'workspace-wide'}</div>
                  </div>
                  {modePill(p.mode)}
                </div>
              </button>
            ))}
          </div>
        </aside>

        <section className="col-span-12 lg:col-span-8 space-y-3">
          {selected ? (
            <>
              <div className="rounded-lg border border-neutral-800 bg-neutral-950 p-4">
                <div className="flex items-center justify-between gap-2 mb-3">
                  <div>
                    <h2 className="text-lg font-semibold">{selected.name}</h2>
                    <p className="text-xs text-neutral-500">{selected.description || 'No description.'}</p>
                  </div>
                  <div className="flex items-center gap-2">
                    {modePill(selected.mode)}
                    {selected.mode === 'draft' && (
                      <button onClick={() => promote('shadow')} className="px-2 py-1 text-xs bg-sky-700 hover:bg-sky-600 rounded">draft → shadow</button>
                    )}
                    {selected.mode === 'shadow' && (
                      <>
                        <button onClick={() => promote('draft')} className="px-2 py-1 text-xs bg-amber-700 hover:bg-amber-600 rounded">shadow → draft</button>
                        <button onClick={() => promote('enforce')} className="px-2 py-1 text-xs bg-emerald-700 hover:bg-emerald-600 rounded inline-flex items-center gap-1">
                          <CheckCircle size={12} /> shadow → enforce
                        </button>
                      </>
                    )}
                    {selected.mode === 'enforce' && (
                      <button onClick={() => promote('shadow')} className="px-2 py-1 text-xs bg-amber-700 hover:bg-amber-600 rounded">enforce → shadow</button>
                    )}
                    <button onClick={archive} className="px-2 py-1 text-xs bg-neutral-800 hover:bg-neutral-700 rounded inline-flex items-center gap-1">
                      <Trash2 size={12} /> archive
                    </button>
                  </div>
                </div>
                <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
                  <KpiTile icon={Activity} label="Decisions seen" value={fmtNum(report?.decisions_seen)} sub="last 24h" />
                  <KpiTile icon={ShieldOff} label="Drift count" value={fmtNum(report?.drift_count)}
                           accent={(report?.drift_count ?? 0) > 0 ? 'text-amber-200' : 'text-emerald-200'}
                           sub="real ≠ shadow" />
                  <KpiTile icon={AlertTriangle} label="FP rate" value={fpHeadline == null ? '—' : fmtPct(fpHeadline)}
                           accent={(fpHeadline ?? 0) > 0 ? 'text-rose-200' : 'text-emerald-200'}
                           sub={`${report?.would_have_blocked_benign_count ?? 0} blocked-benign / ${report?.real_allow_count ?? 0} allowed`} />
                  <KpiTile icon={CheckCircle} label="Would-have-denied" value={fmtNum(report?.would_have_denied_count)}
                           sub="all shadow blocks (incl. agreed)" />
                  <KpiTile icon={Hash} label="Version" value={`v${selected.version}`}
                           sub={selected.promoted_at ? `promoted ${fmtTs(selected.promoted_at)}` : ''} />
                </div>
              </div>

              <div className="rounded-lg border border-neutral-800 bg-neutral-950 overflow-hidden">
                <div className="px-3 py-2 border-b border-neutral-800 text-sm text-neutral-300">Sample drift (real ≠ shadow)</div>
                <div className="max-h-80 overflow-y-auto">
                  <table className="min-w-full text-xs">
                    <thead className="bg-neutral-900 text-neutral-400">
                      <tr>
                        <th className="text-left px-3 py-2">When</th>
                        <th className="text-left px-3 py-2">Tool</th>
                        <th className="text-left px-3 py-2">Real</th>
                        <th className="text-left px-3 py-2">Shadow</th>
                        <th className="text-left px-3 py-2">Matched rule</th>
                        <th className="text-right px-3 py-2">Eval ms</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(report?.sample_drift || []).length === 0 && (
                        <tr><td colSpan={6} className="px-3 py-6 text-center text-neutral-500">No drift yet — shadow agrees with the live pipeline on every sampled request.</td></tr>
                      )}
                      {(report?.sample_drift || []).map((d) => (
                        <tr key={d.id} className="border-t border-neutral-900">
                          <td className="px-3 py-1 text-neutral-400 whitespace-nowrap">{fmtTs(d.created_at)}</td>
                          <td className="px-3 py-1 font-mono text-neutral-300">{d.tool}</td>
                          <td className="px-3 py-1">{actionPill(d.real_action)}</td>
                          <td className="px-3 py-1">{actionPill(d.shadow_action)}</td>
                          <td className="px-3 py-1 text-neutral-400 max-w-xs truncate" title={d.matched_rule_description || ''}>{d.matched_rule_description || ''}</td>
                          <td className="px-3 py-1 text-right tabular-nums text-neutral-400">{d.eval_latency_ms.toFixed(2)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              <div className="rounded-lg border border-neutral-800 bg-neutral-950 overflow-hidden">
                <div className="px-3 py-2 border-b border-neutral-800 text-sm text-neutral-300 inline-flex items-center gap-2">
                  <RotateCcw size={14} /> Version history
                </div>
                <table className="min-w-full text-xs">
                  <thead className="bg-neutral-900 text-neutral-400">
                    <tr>
                      <th className="text-left px-3 py-2">v</th>
                      <th className="text-left px-3 py-2">Change</th>
                      <th className="text-left px-3 py-2">Mode</th>
                      <th className="text-left px-3 py-2">When</th>
                      <th className="text-right px-3 py-2">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {versions.length === 0 && (
                      <tr><td colSpan={5} className="px-3 py-4 text-center text-neutral-500">No prior versions.</td></tr>
                    )}
                    {versions.map((v) => (
                      <tr key={v.id} className="border-t border-neutral-900">
                        <td className="px-3 py-1 tabular-nums">{v.version}</td>
                        <td className="px-3 py-1 text-neutral-300">{v.change_kind}</td>
                        <td className="px-3 py-1 text-neutral-300">{v.mode_before || '∅'} → {v.mode_after}</td>
                        <td className="px-3 py-1 text-neutral-400">{fmtTs(v.changed_at)}</td>
                        <td className="px-3 py-1 text-right">
                          <button
                            onClick={() => rollback(v.version)}
                            className="px-2 py-0.5 bg-neutral-800 hover:bg-neutral-700 rounded text-[10px]"
                          >Rollback</button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          ) : (
            <div className="rounded-lg border border-neutral-800 bg-neutral-950 p-8 text-center text-neutral-500">
              Pick a policy on the left, or create a draft.
            </div>
          )}

          <OnlineEvalConfigCard config={config} onSave={saveConfig} />
        </section>
      </div>
    </div>
  )
}

function KpiTile({ icon: Icon, label, value, accent = 'text-neutral-100', sub }) {
  return (
    <div className="rounded-lg border border-neutral-800 bg-neutral-900/60 px-3 py-2">
      <div className="flex items-center gap-2 text-xs text-neutral-400">
        <Icon size={12} /><span>{label}</span>
      </div>
      <div className={`text-lg font-semibold tabular-nums ${accent}`}>{value}</div>
      {sub && <div className="text-[10px] text-neutral-500">{sub}</div>}
    </div>
  )
}

function OnlineEvalConfigCard({ config, onSave }) {
  const [enabled, setEnabled] = useState(true)
  const [sampleRate, setSampleRate] = useState(0.05)
  const [fpThreshold, setFpThreshold] = useState(0.05)
  const [poll, setPoll] = useState(900)
  useEffect(() => {
    if (config) {
      setEnabled(!!config.enabled)
      setSampleRate(Number(config.sample_rate) || 0.05)
      setFpThreshold(Number(config.fp_threshold) || 0.05)
      setPoll(Number(config.poll_interval_seconds) || 900)
    }
  }, [config])
  return (
    <div className="rounded-lg border border-neutral-800 bg-neutral-950 p-4">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-sm font-semibold inline-flex items-center gap-2">
          <Cog size={14} /> Online evaluation
        </h3>
        <button
          onClick={() => onSave({ enabled, sample_rate: sampleRate, fp_threshold: fpThreshold, poll_interval_seconds: poll })}
          className="px-3 py-1 bg-emerald-700 hover:bg-emerald-600 rounded text-xs"
        >Save</button>
      </div>
      <p className="text-xs text-neutral-500 mb-3">
        Background worker samples recent shadow decisions every {poll}s and fires a
        notification when any policy's would-have-blocked-benign rate crosses
        {' '}<span className="text-neutral-300">{(fpThreshold * 100).toFixed(1)}%</span>.
      </p>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
        <label className="flex flex-col">
          <span className="text-neutral-400">Enabled</span>
          <select value={enabled ? '1' : '0'} onChange={(e) => setEnabled(e.target.value === '1')}
                  className="mt-1 bg-neutral-900 border border-neutral-700 rounded-md px-2 py-1">
            <option value="1">on</option>
            <option value="0">off</option>
          </select>
        </label>
        <label className="flex flex-col">
          <span className="text-neutral-400">Sample rate (0–1)</span>
          <input type="number" step="0.01" min="0" max="1" value={sampleRate}
                 onChange={(e) => setSampleRate(Number(e.target.value))}
                 className="mt-1 bg-neutral-900 border border-neutral-700 rounded-md px-2 py-1" />
        </label>
        <label className="flex flex-col">
          <span className="text-neutral-400">FP threshold (0–1)</span>
          <input type="number" step="0.01" min="0" max="1" value={fpThreshold}
                 onChange={(e) => setFpThreshold(Number(e.target.value))}
                 className="mt-1 bg-neutral-900 border border-neutral-700 rounded-md px-2 py-1" />
        </label>
        <label className="flex flex-col">
          <span className="text-neutral-400">Poll interval (s)</span>
          <input type="number" min="60" max="86400" step="60" value={poll}
                 onChange={(e) => setPoll(Number(e.target.value))}
                 className="mt-1 bg-neutral-900 border border-neutral-700 rounded-md px-2 py-1" />
        </label>
      </div>
    </div>
  )
}
