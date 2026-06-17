import React, { useState, useEffect } from 'react'
import { Shield, ShieldCheck, Download, FileText, CheckCircle2, AlertTriangle, Clock, RefreshCw, Activity, ExternalLink } from 'lucide-react'
import { Link } from 'react-router-dom'
import Card from '../components/Common/Card'
import Button from '../components/Common/Button'
import { auditService, complianceService } from '../services/api'

const FRAMEWORKS = [
  {
    id:    'EU_AI_ACT',
    label: 'EU AI Act',
    desc:  'Articles 13 (Transparency), 16 (Record-keeping), 61 (Post-market monitoring)',
    color: 'blue',
  },
  {
    id:    'NIST_AI_RMF',
    label: 'NIST AI RMF',
    desc:  'GOVERN · MAP · MEASURE · MANAGE risk management functions',
    color: 'purple',
  },
  {
    id:    'SOC2',
    label: 'SOC 2 Type II',
    desc:  'CC6 (Logical Access) · CC7 (Monitoring) · CC8 (Change Management)',
    color: 'amber',
  },
]

const COLOR = {
  blue:   { badge: 'text-blue-400 bg-blue-500/10 border-blue-500/20',   btn: 'text-blue-400 bg-blue-500/[0.06] border-blue-500/20 hover:border-blue-500/40' },
  purple: { badge: 'text-purple-400 bg-purple-500/10 border-purple-500/20', btn: 'text-purple-400 bg-purple-500/[0.06] border-purple-500/20 hover:border-purple-500/40' },
  amber:  { badge: 'text-amber-400 bg-amber-500/10 border-amber-500/20',  btn: 'text-amber-400 bg-amber-500/[0.06] border-amber-500/20 hover:border-amber-500/40' },
}

function iso(date) { return date.toISOString().split('T')[0] }

export default function Compliance() {
  const today = new Date()
  const thirtyDaysAgo = new Date(today); thirtyDaysAgo.setDate(today.getDate() - 30)

  const [startDate,   setStartDate]   = useState(iso(thirtyDaysAgo))
  const [endDate,     setEndDate]     = useState(iso(today))
  const [downloading, setDownloading] = useState({})
  const [evidence,    setEvidence]    = useState({})
  const [loading,     setLoading]     = useState({})
  const [error,       setError]       = useState('')
  // Sprint 16 — Pack enforcement rollup. Pulled from
  // /audit/logs/pack-enforcement; rendered above the framework PDF
  // cards so the CISO sees real control-by-control evidence before
  // they touch the export button.
  const [packEvidence, setPackEvidence] = useState(null)
  const [packsLoading, setPacksLoading] = useState(true)

  const loadEvidence = async (fw) => {
    setLoading(prev => ({ ...prev, [fw]: true }))
    try {
      let data
      if (fw === 'EU_AI_ACT')   data = await complianceService.getEuAiAct({ period_start: startDate, period_end: endDate })
      else if (fw === 'NIST_AI_RMF') data = await complianceService.getNist({ period_start: startDate, period_end: endDate })
      else if (fw === 'SOC2')   data = await complianceService.getSoc2({ period_start: startDate, period_end: endDate })
      setEvidence(prev => ({ ...prev, [fw]: data?.data || data }))
    } catch (err) {
      setError(`Failed to load ${fw} evidence: ${err.message}`)
    } finally {
      setLoading(prev => ({ ...prev, [fw]: false }))
    }
  }

  useEffect(() => {
    FRAMEWORKS.forEach(f => loadEvidence(f.id))
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Sprint 16 — fetch pack enforcement rollup.
  const loadPackEvidence = React.useCallback(async () => {
    setPacksLoading(true)
    try {
      const resp = await auditService.getPackEnforcement(30)
      setPackEvidence(resp?.data || resp || null)
    } catch (err) {
      // Don't surface the error — pack-enforcement is supplementary
      // information; the rest of the page must keep working.
      setPackEvidence(null)
    } finally {
      setPacksLoading(false)
    }
  }, [])
  useEffect(() => { loadPackEvidence() }, [loadPackEvidence])

  const handleExportPdf = async (fw) => {
    setDownloading(prev => ({ ...prev, [fw]: true }))
    setError('')
    try {
      const blob = await complianceService.exportPdf(fw, startDate, endDate)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `aegis-compliance-${fw.toLowerCase()}-${iso(today)}.pdf`
      a.click()
      URL.revokeObjectURL(url)
    } catch (err) {
      setError(err.message || 'PDF export failed.')
    } finally {
      setDownloading(prev => ({ ...prev, [fw]: false }))
    }
  }

  return (
    <div className="space-y-6 animate-fade-in">
      {/* Header */}
      <div className="page-header">
        <div className="flex items-center gap-3">
          <Shield size={22} className="text-neutral-400" aria-hidden="true" />
          <div>
            <h1 className="text-2xl font-bold text-white tracking-tight">Compliance Export</h1>
            <p className="text-xs text-neutral-500 mt-0.5">Generate evidence reports for EU AI Act, NIST AI RMF, and SOC 2</p>
          </div>
        </div>
      </div>

      {/* Date range */}
      <Card title="Reporting Period">
        <div className="flex flex-wrap items-end gap-4">
          <div>
            <label className="text-[10px] text-neutral-500 uppercase tracking-widest block mb-1.5">Start date</label>
            <input
              type="date"
              value={startDate}
              max={endDate}
              onChange={(e) => setStartDate(e.target.value)}
              className="input-standard input-compact h-8 text-xs font-mono w-36"
            />
          </div>
          <div>
            <label className="text-[10px] text-neutral-500 uppercase tracking-widest block mb-1.5">End date</label>
            <input
              type="date"
              value={endDate}
              min={startDate}
              max={iso(today)}
              onChange={(e) => setEndDate(e.target.value)}
              className="input-standard input-compact h-8 text-xs font-mono w-36"
            />
          </div>
          <button
            onClick={() => FRAMEWORKS.forEach(f => loadEvidence(f.id))}
            className="flex items-center gap-2 px-3 py-1.5 text-xs text-neutral-400 bg-white/[0.02] border border-[var(--border-subtle)] rounded-lg hover:text-white hover:border-white/[0.12] transition-colors"
          >
            <RefreshCw size={12} aria-hidden="true" /> Refresh
          </button>
        </div>
      </Card>

      {/* Sprint 16 — Pack enforcement evidence. Sits above the PDF
          cards so the CISO sees real control-by-control numbers before
          they touch the export button. */}
      <Card title="Pack Enforcement — last 30 days" icon={ShieldCheck}>
        {packsLoading ? (
          <div className="text-xs text-neutral-500 py-6 text-center">
            <RefreshCw size={14} className="inline animate-spin mr-2" />
            Loading evidence…
          </div>
        ) : (!packEvidence || (packEvidence.packs || []).length === 0) ? (
          <div className="text-xs text-neutral-500 py-6 text-center space-y-2">
            <ShieldCheck size={22} className="mx-auto text-neutral-700" />
            <div>No pack-tagged escalations in the last 30 days.</div>
            <div className="text-[10px] text-neutral-600 max-w-md mx-auto">
              Enable one or more Compliance Policy Packs in{' '}
              <Link to="/settings?tab=policy-packs" className="underline hover:text-white">
                Settings → Policy packs
              </Link>{' '}
              to start producing per-control evidence here.
            </div>
          </div>
        ) : (
          <div className="space-y-4">
            <div className="text-[11px] text-neutral-500 leading-snug max-w-2xl">
              Every row below is a real audit log entry from this workspace.
              Each control's hit count is the number of times Aegis routed an
              agent action to an approver because that control would have been
              violated otherwise. Click a pack row to expand the per-control
              breakdown.
            </div>
            {(packEvidence.packs || []).map((pack) => (
              <details
                key={pack.pack_id}
                className="group rounded-xl border border-white/[0.07] bg-[#0a0a0a] open:bg-white/[0.02]"
              >
                <summary className="cursor-pointer list-none p-3 flex items-center gap-3">
                  <div className="w-8 h-8 rounded-md bg-white/[0.05] flex items-center justify-center text-neutral-200">
                    <Shield size={14} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-semibold text-white">{pack.pack_id} Pack</span>
                      <span className="status-badge text-green-400 bg-green-500/10 border-green-500/20">
                        {pack.total} escalation{pack.total === 1 ? '' : 's'}
                      </span>
                    </div>
                    <div className="text-[10px] text-neutral-500 mt-0.5">
                      {pack.controls.length} control{pack.controls.length === 1 ? '' : 's'} touched
                    </div>
                  </div>
                  <span className="text-[10px] text-neutral-500 group-open:rotate-90 transition-transform">▶</span>
                </summary>
                <div className="p-3 pt-0 space-y-2">
                  <div className="overflow-x-auto">
                    <table className="w-full text-xs">
                      <thead className="text-[10px] uppercase tracking-widest text-neutral-500">
                        <tr className="text-left border-b border-white/[0.05]">
                          <th className="py-2 pr-3">Control</th>
                          <th className="py-2 pr-3 text-right">Hits</th>
                          <th className="py-2 pr-2">Recent examples</th>
                        </tr>
                      </thead>
                      <tbody>
                        {pack.controls.map((c) => (
                          <tr key={c.id} className="border-b border-white/[0.04] last:border-b-0 align-top">
                            <td className="py-2 pr-3">
                              <span className="inline-flex items-center gap-1 text-[11px] text-neutral-200 px-2 py-0.5 rounded-md bg-white/[0.04] border border-white/[0.06] font-mono">
                                {c.id}
                              </span>
                            </td>
                            <td className="py-2 pr-3 text-right text-neutral-200 font-mono">{c.hits}</td>
                            <td className="py-2 pr-2 space-y-1">
                              {(c.recent || []).map((r, i) => (
                                <div key={i} className="text-[10px] text-neutral-500 leading-snug">
                                  <span className="text-neutral-400 font-mono">{r.matched_pattern}</span>
                                  {' · '}
                                  <span className="text-neutral-400">{r.approver_role}</span>
                                  {r.employee_email && (
                                    <>
                                      {' · '}
                                      <span className="text-neutral-600">{r.employee_email}</span>
                                    </>
                                  )}
                                  <div className="text-[10px] text-neutral-700 font-mono">
                                    {r.ts ? new Date(r.ts).toLocaleString() : '—'}
                                  </div>
                                </div>
                              ))}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              </details>
            ))}
          </div>
        )}
      </Card>

      {error && (
        <div className="flex items-center gap-2 p-3 rounded-xl bg-red-500/[0.06] border border-red-500/15">
          <AlertTriangle size={13} className="text-red-400 shrink-0" aria-hidden="true" />
          <p className="text-xs text-red-400">{error}</p>
          <button onClick={() => setError('')} className="ml-auto text-neutral-600 hover:text-neutral-400 text-xs">✕</button>
        </div>
      )}

      {/* Framework cards */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        {FRAMEWORKS.map(fw => {
          const ev = evidence[fw.id]
          const isLoading = loading[fw.id]
          const isDl = downloading[fw.id]
          const c = COLOR[fw.color]

          return (
            <div
              key={fw.id}
              className="rounded-2xl border border-[var(--border-default)] bg-[var(--bg-surface)] p-5 space-y-4"
            >
              {/* Badge + title */}
              <div className="space-y-1.5">
                <span className={`inline-block status-badge text-[10px] ${c.badge}`}>{fw.id.replace(/_/g, ' ')}</span>
                <h2 className="text-sm font-bold text-white">{fw.label}</h2>
                <p className="text-[11px] text-neutral-500 leading-relaxed">{fw.desc}</p>
              </div>

              {/* Evidence summary */}
              {isLoading && (
                <div className="flex items-center gap-2 text-[11px] text-neutral-500">
                  <Clock size={11} className="animate-spin" aria-hidden="true" />
                  Loading evidence…
                </div>
              )}
              {ev && !isLoading && (
                <div className="space-y-1.5">
                  <EvidenceKpis framework={fw.id} evidence={ev} />
                </div>
              )}
              {!ev && !isLoading && (
                <p className="text-[11px] text-neutral-600">No evidence loaded.</p>
              )}

              {/* Actions */}
              <div className="flex gap-2 pt-1">
                <button
                  onClick={() => loadEvidence(fw.id)}
                  disabled={isLoading}
                  className="flex items-center gap-1.5 px-2.5 py-1.5 text-[11px] text-neutral-400 bg-white/[0.02] border border-[var(--border-subtle)] rounded-lg hover:text-white hover:border-white/[0.12] disabled:opacity-40 transition-colors"
                >
                  <RefreshCw size={11} aria-hidden="true" />
                  Refresh
                </button>
                <button
                  onClick={() => handleExportPdf(fw.id)}
                  disabled={isDl}
                  className={`flex items-center gap-1.5 px-2.5 py-1.5 text-[11px] border rounded-lg disabled:opacity-40 disabled:cursor-not-allowed transition-colors ${c.btn}`}
                >
                  <Download size={11} aria-hidden="true" />
                  {isDl ? 'Generating…' : 'Export PDF'}
                </button>
              </div>
            </div>
          )
        })}
      </div>

      {/* Info footer */}
      <div className="flex items-start gap-3 p-4 rounded-xl bg-white/[0.02] border border-[var(--border-subtle)]">
        <FileText size={14} className="text-neutral-500 shrink-0 mt-0.5" aria-hidden="true" />
        <p className="text-[11px] text-neutral-500 leading-relaxed">
          PDF reports are generated from the live tamper-evident audit chain. Each report includes a
          cryptographic attestation section and is intended as evidence for a qualified compliance officer.
          Reports do not constitute a pass/fail verdict — a human must evaluate sufficiency.
        </p>
      </div>
    </div>
  )
}

function EvidenceKpis({ framework, evidence }) {
  if (framework === 'EU_AI_ACT') {
    const summary = evidence?.tool_call_summary || {}
    const integrity = evidence?.integrity_proof_reference || {}
    return (
      <>
        <KpiRow label="Total tool calls" value={summary.total_calls ?? '—'} />
        <KpiRow label="Denied" value={summary.by_decision?.deny ?? 0} warn />
        <KpiRow label="Chain valid" value={integrity.chain_valid ? 'Yes' : 'No'} ok={integrity.chain_valid} />
        <KpiRow label="Articles covered" value={(evidence?.articles_covered || []).join(', ') || '—'} />
      </>
    )
  }
  if (framework === 'NIST_AI_RMF') {
    const measure = evidence?.MEASURE || {}
    const manage  = evidence?.MANAGE  || {}
    return (
      <>
        <KpiRow label="Total evaluated" value={measure.total_evaluated ?? '—'} />
        <KpiRow label="Avg risk score"  value={measure.avg_risk_score != null ? (measure.avg_risk_score?.toFixed?.(3) ?? measure.avg_risk_score) : '—'} />
        <KpiRow label="Escalations"     value={manage.total_escalations ?? 0} warn />
        <KpiRow label="Functions covered" value={(evidence?.functions_covered || []).join(', ') || '—'} />
      </>
    )
  }
  if (framework === 'SOC2') {
    const cc6 = evidence?.CC6_1 || {}
    const cc6b = evidence?.CC6_6 || {}
    return (
      <>
        <KpiRow label="Access events"   value={cc6.total_access_events ?? '—'} />
        <KpiRow label="Denied tool calls" value={cc6b.total_denied_tool_calls ?? 0} warn />
        <KpiRow label="Controls covered" value={(evidence?.controls_covered || []).join(', ') || '—'} />
      </>
    )
  }
  return null
}

function KpiRow({ label, value, warn, ok }) {
  let cls = 'text-neutral-300'
  if (warn && value > 0) cls = 'text-amber-400'
  if (ok === true)  cls = 'text-green-400'
  if (ok === false) cls = 'text-red-400'
  return (
    <div className="flex items-center justify-between py-1 border-b border-[var(--border-subtle)] last:border-0">
      <span className="text-[10px] text-neutral-600">{label}</span>
      <span className={`text-[11px] font-mono font-medium ${cls}`}>{String(value)}</span>
    </div>
  )
}
