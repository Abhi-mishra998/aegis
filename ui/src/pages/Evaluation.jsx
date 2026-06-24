// Sprint 5 — Attack Evaluation Suite dashboard.
//
// In-product dashboard for the OWASP attack corpus replay:
//   * Headline KPIs: detection rate / FP rate / cases evaluated / last run
//   * Per-OWASP-category detection breakdown (bar)
//   * Per-rule efficacy table — the "biggest evaluator score changes"
//     panel from Sprint 5 ("which detector quietly weakened today?")
//   * Recent jobs with status + drill-down to per-case results
//
// Data sources:
//   GET  /audit/evaluation/efficacy/overview
//   GET  /audit/evaluation/efficacy/trend
//   GET  /audit/evaluation/jobs
//   GET  /audit/evaluation/datasets
//   GET  /audit/evaluation/evaluators
//   POST /audit/evaluation/jobs  (enqueue)
//
// All data tenant-scoped at the backend via JWT. No mocked arrays.

import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  ShieldCheck, AlertTriangle, ListChecks, Clock, PlayCircle, BookOpen,
} from 'lucide-react'
import {
  ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip, CartesianGrid,
  LineChart, Line,
} from 'recharts'
import { evaluationService } from '../services/api'

function unwrap(resp) { return resp?.data ?? resp }

function fmtPct(x) {
  if (x == null || Number.isNaN(x)) return '—'
  return `${(x * 100).toFixed(2)}%`
}

function fmtNum(x) {
  if (x == null) return '—'
  if (x >= 1_000_000) return `${(x / 1_000_000).toFixed(1)}M`
  if (x >= 1_000)     return `${(x / 1_000).toFixed(1)}k`
  return String(x)
}

function fmtTs(iso) {
  if (!iso) return '—'
  try { return new Date(iso).toLocaleString() } catch { return iso }
}

function KpiCard({ icon: Icon, label, value, accent = 'text-neutral-100', sub }) {
  return (
    <div className="rounded-lg border border-neutral-800 bg-neutral-900/60 px-4 py-3 flex flex-col gap-1">
      <div className="flex items-center gap-2 text-xs text-neutral-400">
        <Icon size={14} />
        <span>{label}</span>
      </div>
      <div className={`text-2xl font-semibold tabular-nums ${accent}`}>{value}</div>
      {sub && <div className="text-[10px] text-neutral-500">{sub}</div>}
    </div>
  )
}

function statusPill(status) {
  const c = {
    queued:    'bg-amber-950 text-amber-200 border-amber-800',
    running:   'bg-sky-950   text-sky-200   border-sky-800',
    completed: 'bg-emerald-950 text-emerald-200 border-emerald-800',
    failed:    'bg-rose-950  text-rose-200  border-rose-800',
    cancelled: 'bg-neutral-800 text-neutral-300 border-neutral-700',
  }[status] || 'bg-neutral-800 text-neutral-300 border-neutral-700'
  return (
    <span className={`px-2 py-0.5 text-[10px] rounded-md border ${c}`}>
      {status}
    </span>
  )
}

export default function Evaluation() {
  const [overview, setOverview]   = useState(null)
  const [trend, setTrend]         = useState([])
  const [jobs, setJobs]           = useState([])
  const [datasets, setDatasets]   = useState([])
  const [evaluators, setEvaluators] = useState([])
  const [error, setError]         = useState('')
  const [loading, setLoading]     = useState(false)
  const [enqueueing, setEnqueueing] = useState(false)
  const [enqueueMsg, setEnqueueMsg] = useState('')

  const fetchAll = useCallback(async () => {
    setLoading(true); setError('')
    try {
      const [ov, tr, js, ds, ev] = await Promise.all([
        evaluationService.overview(),
        evaluationService.trend({ days: 30 }),
        evaluationService.listJobs({ limit: 20 }),
        evaluationService.listDatasets(),
        evaluationService.listEvaluators(),
      ])
      setOverview(unwrap(ov) || null)
      setTrend(unwrap(tr) || [])
      setJobs(unwrap(js) || [])
      setDatasets(unwrap(ds) || [])
      setEvaluators(unwrap(ev) || [])
    } catch (e) {
      setError(e?.message || 'Failed to load evaluation data')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchAll() }, [fetchAll])

  const owaspData = useMemo(() => {
    const cats = overview?.per_owasp_category || {}
    return Object.entries(cats)
      .map(([cat, bucket]) => ({
        category: cat,
        detection_pct: (bucket.detection_rate ?? 0) * 100,
        caught: bucket.caught ?? 0,
        total: bucket.total ?? 0,
      }))
      .sort((a, b) => a.category.localeCompare(b.category))
  }, [overview])

  const ruleRows = useMemo(() => {
    const rules = overview?.per_rule || {}
    return Object.entries(rules)
      .map(([ruleId, bucket]) => ({
        rule_id: ruleId,
        efficacy: bucket.efficacy ?? 0,
        hits: bucket.hits ?? 0,
      }))
      .sort((a, b) => b.hits - a.hits)
      .slice(0, 25)
  }, [overview])

  const trendByRule = useMemo(() => {
    const grouped = new Map()
    for (const p of trend) {
      if (!p.rule_id) continue
      if (!grouped.has(p.rule_id)) grouped.set(p.rule_id, [])
      grouped.get(p.rule_id).push({
        t: new Date(p.snapshot_date).getTime(),
        v: p.score * 100,
      })
    }
    return grouped
  }, [trend])

  const enqueueDefault = async () => {
    setEnqueueMsg('')
    if (!datasets.length) { setEnqueueMsg('No datasets — seed the OWASP corpus first.'); return }
    setEnqueueing(true)
    try {
      const evalIds = (evaluators || []).map((e) => e.id)
      await evaluationService.enqueueJob({
        dataset_id: datasets[0].id,
        evaluator_ids: evalIds,
        schedule: 'manual',
      })
      setEnqueueMsg(`Enqueued. Polling for results — refresh in ~30s.`)
      await fetchAll()
    } catch (e) {
      setEnqueueMsg(e?.message || 'Enqueue failed')
    } finally {
      setEnqueueing(false)
    }
  }

  return (
    <div className="text-neutral-100">
      <header className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 px-6 py-4 border-b border-neutral-800">
        <div>
          <h1 className="text-xl font-semibold">Evaluation</h1>
          <p className="text-sm text-neutral-400 mt-1">
            560-case OWASP attack corpus replayed against the live decision
            pipeline. Detection rate, false-positive rate, per-rule efficacy
            — the same numbers we publish in the benchmark doc.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={enqueueDefault}
            disabled={enqueueing || !datasets.length}
            className="px-3 py-1.5 bg-emerald-700 hover:bg-emerald-600 disabled:bg-neutral-700 disabled:text-neutral-400 rounded-md text-sm inline-flex items-center gap-2"
          >
            <PlayCircle size={14} />
            {enqueueing ? 'Enqueuing…' : 'Run nightly corpus'}
          </button>
          <button
            onClick={fetchAll}
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
      {enqueueMsg && (
        <div className="mx-6 my-3 text-sm bg-sky-950 border border-sky-700 text-sky-100 px-3 py-2 rounded">
          {enqueueMsg}
        </div>
      )}

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 px-6 py-4">
        <KpiCard
          icon={ShieldCheck}
          label="Detection rate"
          value={fmtPct(overview?.detection_rate)}
          accent={(overview?.detection_rate ?? 0) >= 0.95 ? 'text-emerald-200' : 'text-amber-200'}
          sub={`${fmtNum(overview?.attack_cases)} attack cases`}
        />
        <KpiCard
          icon={AlertTriangle}
          label="False-positive rate"
          value={fmtPct(overview?.fp_rate)}
          accent={(overview?.fp_rate ?? 0) > 0.05 ? 'text-rose-200' : 'text-emerald-200'}
          sub={`${fmtNum(overview?.benign_cases)} benign cases`}
        />
        <KpiCard
          icon={ListChecks}
          label="Cases evaluated"
          value={fmtNum(overview?.cases_evaluated)}
          sub="from the most recent completed run"
        />
        <KpiCard
          icon={Clock}
          label="Last run"
          value={fmtTs(overview?.last_run_at)}
        />
      </div>

      <div className="px-6 pb-4">
        <h2 className="text-sm font-semibold text-neutral-200 mb-2">Detection rate by OWASP category</h2>
        <div className="h-56 rounded-lg border border-neutral-800 bg-neutral-950 p-2">
          {owaspData.length ? (
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={owaspData}>
                <CartesianGrid stroke="#262626" />
                <XAxis dataKey="category" stroke="#737373" tick={{ fontSize: 11 }} />
                <YAxis stroke="#737373" tick={{ fontSize: 11 }} unit="%" />
                <Tooltip
                  contentStyle={{ background: '#0a0a0a', border: '1px solid #262626' }}
                  formatter={(v) => [`${v.toFixed(2)}%`, 'Detection']}
                />
                <Bar dataKey="detection_pct" fill="#10b981" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <div className="flex items-center justify-center h-full text-sm text-neutral-500">
              No completed runs yet — kick off the nightly corpus to populate this chart.
            </div>
          )}
        </div>
      </div>

      <div className="px-6 pb-4">
        <h2 className="text-sm font-semibold text-neutral-200 mb-2">
          Per-rule efficacy <span className="text-neutral-500">(top 25 by sample count)</span>
        </h2>
        <div className="rounded-lg border border-neutral-800 bg-neutral-950 overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead className="bg-neutral-900 text-neutral-400 text-xs uppercase">
              <tr>
                <th className="text-left px-3 py-2">Rule</th>
                <th className="text-right px-3 py-2">Hits</th>
                <th className="text-right px-3 py-2">Efficacy</th>
                <th className="px-3 py-2 w-44">30-day trend</th>
              </tr>
            </thead>
            <tbody>
              {ruleRows.length === 0 && (
                <tr><td colSpan={4} className="px-3 py-6 text-center text-neutral-500">No rule attribution yet — runs need to land first.</td></tr>
              )}
              {ruleRows.map((row) => {
                const series = trendByRule.get(row.rule_id) || []
                const accent = row.efficacy >= 0.9 ? 'text-emerald-200' : row.efficacy >= 0.6 ? 'text-amber-200' : 'text-rose-200'
                return (
                  <tr key={row.rule_id} className="border-t border-neutral-800">
                    <td className="px-3 py-2 font-mono text-xs text-neutral-200">{row.rule_id}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{fmtNum(row.hits)}</td>
                    <td className={`px-3 py-2 text-right tabular-nums ${accent}`}>{fmtPct(row.efficacy)}</td>
                    <td className="px-3 py-2 h-10">
                      {series.length > 1 ? (
                        <ResponsiveContainer width="100%" height={28}>
                          <LineChart data={series}>
                            <Line type="monotone" dataKey="v" stroke="#10b981" strokeWidth={1.4} dot={false} />
                          </LineChart>
                        </ResponsiveContainer>
                      ) : <span className="text-neutral-600 text-xs">—</span>}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>

      <div className="px-6 pb-8">
        <h2 className="text-sm font-semibold text-neutral-200 mb-2">Recent jobs</h2>
        <div className="rounded-lg border border-neutral-800 bg-neutral-950 overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead className="bg-neutral-900 text-neutral-400 text-xs uppercase">
              <tr>
                <th className="text-left px-3 py-2">Job</th>
                <th className="text-left px-3 py-2">Status</th>
                <th className="text-left px-3 py-2">Schedule</th>
                <th className="text-right px-3 py-2">Cases (done/total)</th>
                <th className="text-left px-3 py-2">Queued</th>
                <th className="text-left px-3 py-2">Finished</th>
                <th className="text-left px-3 py-2">Error</th>
              </tr>
            </thead>
            <tbody>
              {jobs.length === 0 && (
                <tr>
                  <td colSpan={7} className="px-3 py-10 text-center">
                    <div
                      className="flex flex-col items-center gap-3 max-w-md mx-auto"
                      role="status"
                      aria-live="polite"
                    >
                      <PlayCircle size={26} className="text-neutral-600 opacity-40" aria-hidden="true" />
                      <p className="text-sm text-neutral-300 font-medium">No evaluations queued</p>
                      <p className="text-xs text-neutral-500 leading-relaxed">
                        Run <code className="text-neutral-300 bg-white/[0.05] px-1 py-0.5 rounded">/evaluation/start</code>{' '}
                        — or seed the OWASP corpus from the staging tab to enqueue
                        the attack suite against the live decision pipeline.
                      </p>
                      <div className="flex items-center gap-2 flex-wrap justify-center">
                        <Link
                          to="/policies?tab=staging"
                          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white text-black text-xs font-medium hover:bg-neutral-200 transition-colors"
                        >
                          <PlayCircle size={11} aria-hidden="true" />
                          Seed OWASP corpus
                        </Link>
                        <Link
                          to="/policies"
                          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-white/10 text-xs text-neutral-300 hover:bg-white/[0.04] transition-colors"
                        >
                          <BookOpen size={11} aria-hidden="true" />
                          Or browse policy library
                        </Link>
                      </div>
                      <p className="text-[10px] text-neutral-600 leading-relaxed max-w-xs">
                        Seeding stages 560 OWASP attack cases as shadow policies — the next nightly run will replay them and populate this table.
                      </p>
                      <button
                        onClick={enqueueDefault}
                        disabled={enqueueing || !datasets.length}
                        className="inline-flex items-center gap-1.5 text-[11px] px-3 py-1.5 rounded-lg border border-emerald-500/30 text-emerald-300 hover:border-emerald-500/60 hover:bg-emerald-500/[0.08] disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                      >
                        <PlayCircle size={11} aria-hidden="true" />
                        {enqueueing ? 'Enqueueing…' : datasets.length ? 'Run nightly corpus now' : 'Seed OWASP corpus first'}
                      </button>
                    </div>
                  </td>
                </tr>
              )}
              {jobs.map((j) => (
                <tr key={j.id} className="border-t border-neutral-800">
                  <td className="px-3 py-2 font-mono text-xs text-neutral-400">{j.id.slice(0, 8)}…</td>
                  <td className="px-3 py-2">{statusPill(j.status)}</td>
                  <td className="px-3 py-2 text-xs text-neutral-300">{j.schedule}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-neutral-300">{j.cases_done} / {j.cases_total}</td>
                  <td className="px-3 py-2 text-xs text-neutral-400">{fmtTs(j.queued_at)}</td>
                  <td className="px-3 py-2 text-xs text-neutral-400">{fmtTs(j.finished_at)}</td>
                  <td className="px-3 py-2 text-xs text-rose-300 max-w-xs truncate" title={j.error_message || ''}>{j.error_message || ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
