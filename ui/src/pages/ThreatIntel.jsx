import React, { useEffect, useState, useCallback } from 'react'
import {
  Shield, Search, Globe, Wifi, AlertTriangle,
  CheckCircle2, XCircle, Loader2, RefreshCw, Info,
  Activity, Lock, FlaskConical, Plus, Trash2, Database, Rss,
} from 'lucide-react'
import { threatIntelService } from '../services/api'
import { useAuth } from '../hooks/useAuth'
import DataTable from '../components/Common/DataTable'
import Modal from '../components/Common/Modal'
import Button from '../components/Common/Button'

const IOC_KINDS = [
  'exfil_host', 'c2_domain', 'offshore_token',
  'destructive_shell', 'malicious_path', 'privilege_token',
]
const IOC_SEVERITIES = ['low', 'medium', 'high', 'critical']

const SEVERITY_BADGE = {
  critical: 'bg-red-500/10 text-red-400 border-red-500/20',
  high:     'bg-amber-500/10 text-amber-400 border-amber-500/20',
  medium:   'bg-yellow-500/10 text-yellow-400 border-yellow-500/20',
  low:      'bg-neutral-500/10 text-neutral-400 border-neutral-500/20',
}

function ScoreBadge({ score }) {
  const s = Number(score) || 0
  const { color, label } =
    s >= 75 ? { color: 'bg-red-500/10 text-red-400 border-red-500/20', label: 'HIGH RISK' } :
    s >= 40 ? { color: 'bg-amber-500/10 text-amber-400 border-amber-500/20', label: 'MEDIUM' } :
    s >= 10 ? { color: 'bg-yellow-500/10 text-yellow-400 border-yellow-500/20', label: 'LOW' } :
              { color: 'bg-green-500/10 text-green-400 border-green-500/20', label: 'CLEAN' }
  return (
    <span className={`inline-flex items-center gap-1 text-[10px] font-semibold px-2 py-0.5 rounded-full border ${color}`}>
      {s} — {label}
    </span>
  )
}

function ResultCard({ result, type }) {
  if (!result) return null
  if (result.status === 'error') {
    return (
      <div className="flex items-center gap-2 p-3 bg-red-500/10 border border-red-500/20 rounded-lg text-sm text-red-400">
        <XCircle size={14} /> {result.error || 'Enrichment failed'}
      </div>
    )
  }

  const score = result.abuse_score ?? result.score ?? 0

  return (
    <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl p-4 space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          {type === 'ip' ? <Wifi size={14} className="text-neutral-400" /> : <Globe size={14} className="text-neutral-400" />}
          <span className="text-sm font-mono text-white">{result.ip || result.domain}</span>
        </div>
        <ScoreBadge score={score} />
      </div>
      <div className="grid grid-cols-2 gap-3">
        {result.country && (
          <div className="bg-white/[0.03] rounded-lg p-2.5">
            <div className="text-[10px] text-neutral-600 mb-0.5">Country</div>
            <div className="text-xs text-white">{result.country}</div>
          </div>
        )}
        {result.isp && (
          <div className="bg-white/[0.03] rounded-lg p-2.5">
            <div className="text-[10px] text-neutral-600 mb-0.5">ISP</div>
            <div className="text-xs text-white truncate">{result.isp}</div>
          </div>
        )}
        {result.reports != null && (
          <div className="bg-white/[0.03] rounded-lg p-2.5">
            <div className="text-[10px] text-neutral-600 mb-0.5">Abuse Reports</div>
            <div className="text-xs text-white">{result.reports}</div>
          </div>
        )}
        {result.is_tor != null && (
          <div className="bg-white/[0.03] rounded-lg p-2.5">
            <div className="text-[10px] text-neutral-600 mb-0.5">Tor Exit Node</div>
            <div className={`text-xs ${result.is_tor ? 'text-red-400' : 'text-green-400'}`}>
              {result.is_tor ? 'Yes' : 'No'}
            </div>
          </div>
        )}
        {result.categories && (
          <div className="col-span-2 bg-white/[0.03] rounded-lg p-2.5">
            <div className="text-[10px] text-neutral-600 mb-0.5">Categories</div>
            <div className="flex flex-wrap gap-1">
              {(result.categories || []).map(c => (
                <span key={c} className="text-[10px] px-1.5 py-0.5 rounded bg-white/[0.06] text-neutral-400">{c}</span>
              ))}
            </div>
          </div>
        )}
      </div>
      <div className="flex items-center gap-1.5 text-[10px] text-neutral-600">
        <Info size={10} />
        Source: {result.source}
        {result.source === 'demo' && ' (demo data — configure API keys for live enrichment)'}
      </div>
    </div>
  )
}

function KpiTile({ icon: Icon, label, value, accent }) {
  return (
    <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl p-4 flex items-center gap-3">
      <div className="w-8 h-8 rounded-lg bg-white/[0.04] flex items-center justify-center shrink-0">
        <Icon size={15} className="text-neutral-500" />
      </div>
      <div>
        <div className={`text-xl font-semibold ${accent || 'text-white'}`}>{value ?? '—'}</div>
        <div className="text-[10px] text-neutral-600">{label}</div>
      </div>
    </div>
  )
}

function IocCreateModal({ isOpen, onClose, onCreated }) {
  const { addToast } = useAuth()
  const [kind, setKind]         = useState(IOC_KINDS[0])
  const [value, setValue]       = useState('')
  const [severity, setSeverity] = useState('high')
  const [saving, setSaving]     = useState(false)

  const reset = () => { setKind(IOC_KINDS[0]); setValue(''); setSeverity('high') }

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!value.trim()) return
    setSaving(true)
    try {
      await threatIntelService.createIoc({ kind, value: value.trim(), severity })
      addToast('IOC added', 'success')
      reset()
      onCreated?.()
      onClose?.()
    } catch (err) {
      addToast(err?.message || 'Failed to add IOC', 'error')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Add IOC"
      description="Tenant-scoped indicator of compromise. Substring match for most kinds; destructive_shell takes a Python regex."
      size="md"
      footer={
        <>
          <Button variant="ghost" size="sm" onClick={onClose} disabled={saving}>Cancel</Button>
          <Button variant="primary" size="sm" onClick={handleSubmit} loading={saving} disabled={!value.trim()}>Add IOC</Button>
        </>
      }
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label className="text-[10px] uppercase tracking-widest text-neutral-500 block mb-1.5">Kind</label>
          <select
            value={kind}
            onChange={e => setKind(e.target.value)}
            className="w-full bg-white/[0.04] border border-[var(--border-subtle)] rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-white/20"
          >
            {IOC_KINDS.map(k => <option key={k} value={k}>{k}</option>)}
          </select>
        </div>
        <div>
          <label className="text-[10px] uppercase tracking-widest text-neutral-500 block mb-1.5">
            Value {kind === 'destructive_shell' && <span className="text-amber-400">(regex)</span>}
          </label>
          <input
            type="text"
            value={value}
            onChange={e => setValue(e.target.value)}
            placeholder={kind === 'destructive_shell' ? 'rm\\s+-rf\\s+/' : 'evil-host.com'}
            className="w-full bg-white/[0.04] border border-[var(--border-subtle)] rounded-lg px-3 py-2 text-sm font-mono text-white placeholder-neutral-600 focus:outline-none focus:border-white/20"
            autoFocus
          />
        </div>
        <div>
          <label className="text-[10px] uppercase tracking-widest text-neutral-500 block mb-1.5">Severity</label>
          <select
            value={severity}
            onChange={e => setSeverity(e.target.value)}
            className="w-full bg-white/[0.04] border border-[var(--border-subtle)] rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-white/20"
          >
            {IOC_SEVERITIES.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>
      </form>
    </Modal>
  )
}

export default function ThreatIntel() {
  const { addToast } = useAuth()
  const [query, setQuery]     = useState('')
  const [mode, setMode]       = useState('ip')
  const [loading, setLoading] = useState(false)
  const [result, setResult]   = useState(null)
  const [summary, setSummary] = useState(null)
  const [history, setHistory] = useState([])

  // IOC management state
  const [iocs, setIocs]               = useState([])
  const [iocsLoading, setIocsLoading] = useState(false)
  const [showIocModal, setShowIocModal] = useState(false)

  // Feed status state
  const [feeds, setFeeds]             = useState([])
  const [lastRefreshTs, setLastRefreshTs] = useState(null)
  const [feedsLoading, setFeedsLoading]   = useState(false)
  const [refreshing, setRefreshing]   = useState(false)

  const loadSummary = useCallback(async () => {
    try {
      const res = await threatIntelService.getSummary()
      setSummary(res?.data || res)
    } catch {}
  }, [])

  const loadIocs = useCallback(async () => {
    setIocsLoading(true)
    try {
      const res = await threatIntelService.listIocs({ limit: 200 })
      const payload = res?.data || res
      setIocs(payload?.items || [])
    } catch (err) {
      addToast(err?.message || 'Failed to load IOCs', 'error')
    } finally {
      setIocsLoading(false)
    }
  }, [addToast])

  const loadFeeds = useCallback(async () => {
    setFeedsLoading(true)
    try {
      const res = await threatIntelService.listFeeds()
      const payload = res?.data || res
      const items = payload?.feeds || []
      // feeds may be { name: { url, enabled, ... } } object or array
      const list = Array.isArray(items)
        ? items
        : Object.entries(items).map(([name, cfg]) => ({ name, ...(cfg || {}) }))
      setFeeds(list)
      setLastRefreshTs(payload?.last_refresh_ts ?? null)
    } catch (err) {
      addToast(err?.message || 'Failed to load feeds', 'error')
    } finally {
      setFeedsLoading(false)
    }
  }, [addToast])

  const handleDeleteIoc = async (row) => {
    if (!row?.id) return
    try {
      await threatIntelService.deleteIoc(row.id)
      addToast('IOC removed', 'info')
      loadIocs()
    } catch (err) {
      addToast(err?.message || 'Failed to delete IOC', 'error')
    }
  }

  const handleRefreshFeeds = async () => {
    setRefreshing(true)
    try {
      await threatIntelService.refresh()
      addToast('Feed refresh triggered', 'success')
      loadFeeds()
      loadSummary()
    } catch (err) {
      addToast(err?.message || 'Refresh failed', 'error')
    } finally {
      setRefreshing(false)
    }
  }

  useEffect(() => { loadSummary() }, [loadSummary])
  useEffect(() => { loadIocs() }, [loadIocs])
  useEffect(() => { loadFeeds() }, [loadFeeds])

  const isIp = (v) => /^\d{1,3}(\.\d{1,3}){3}$/.test(v.trim())

  const enrich = async () => {
    const val = query.trim()
    if (!val) return
    const detectedMode = isIp(val) ? 'ip' : 'domain'
    setMode(detectedMode)
    setLoading(true)
    setResult(null)
    try {
      const res = detectedMode === 'ip'
        ? await threatIntelService.enrichIp(val)
        : await threatIntelService.enrichDomain(val)
      const data = res?.data || res
      setResult(data)
      setHistory(h => [{ value: val, type: detectedMode, score: data?.abuse_score ?? data?.score ?? 0, ts: new Date() }, ...h.slice(0, 9)])
      loadSummary()
    } catch (err) {
      setResult({ status: 'error', error: err.message })
    } finally {
      setLoading(false)
    }
  }

  const handleKey = (e) => { if (e.key === 'Enter') enrich() }

  const highRisk = history.filter(h => h.score >= 75).length
  const isDemoMode = result?.demo_mode || summary?.demo_mode

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <header>
        <h1 className="text-2xl font-semibold text-white mb-1">Threat Intelligence</h1>
        <p className="text-sm text-neutral-400">
          Enrich IPs and domains against external threat feeds. Enter an IP address or domain name.
        </p>
      </header>

      {isDemoMode && (
        <div className="flex items-start gap-2.5 px-4 py-3 bg-amber-500/10 border border-amber-500/20 rounded-xl text-xs text-amber-400">
          <FlaskConical size={13} className="shrink-0 mt-0.5" />
          <span>
            <strong>Demo mode</strong> — no API keys detected. Results are deterministic hashed data,
            not live threat feeds. Set <code className="font-mono">ABUSEIPDB_API_KEY</code> and{' '}
            <code className="font-mono">OTX_API_KEY</code> to enable real enrichment.
          </span>
        </div>
      )}

      {/* KPIs */}
      <div className="grid grid-cols-3 gap-3">
        <KpiTile icon={Wifi}          label="IPs checked"      value={summary?.ips_checked ?? 0} />
        <KpiTile icon={Globe}         label="Domains checked"  value={summary?.domains_checked ?? 0} />
        <KpiTile icon={AlertTriangle} label="High risk found"  value={summary?.high_risk_count ?? highRisk} accent={highRisk > 0 ? 'text-red-400' : 'text-white'} />
      </div>

      {/* Summary feed — renders array fields from /threat-intel/summary or
          falls back to a friendly awaiting message when the backend has
          nothing yet. Iterates any array on `summary` defensively. */}
      <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl p-5">
        <h2 className="text-sm font-medium text-white flex items-center gap-2 mb-3">
          <Activity size={13} className="text-neutral-500" /> Threat Intel Feed
          {summary && (
            <span className="ml-auto text-[10px] font-mono text-neutral-600">
              {Object.keys(summary || {}).length} field{Object.keys(summary || {}).length !== 1 ? 's' : ''}
            </span>
          )}
        </h2>
        {(!summary || Object.keys(summary).length === 0) ? (
          <p className="text-xs text-neutral-600 text-center py-6">Awaiting threat intel data — run a lookup or wait for the next refresh.</p>
        ) : (
          <div className="space-y-3">
            {Object.entries(summary)
              .filter(([, v]) => Array.isArray(v) && v.length > 0)
              .map(([key, arr]) => (
                <div key={key} className="bg-white/[0.03] rounded-lg p-3">
                  <div className="text-[10px] uppercase tracking-wider text-neutral-500 mb-2 flex items-center gap-1.5">
                    <AlertTriangle size={10} /> {key.replace(/_/g, ' ')} <span className="text-neutral-600">({arr.length})</span>
                  </div>
                  <div className="divide-y divide-white/5">
                    {arr.slice(0, 8).map((item, i) => (
                      <div key={i} className="py-1.5 flex items-center justify-between gap-2 text-[11px] font-mono">
                        <span className="text-neutral-300 truncate">
                          {typeof item === 'object' ? (item?.value || item?.ip || item?.domain || item?.indicator || JSON.stringify(item)) : String(item)}
                        </span>
                        {typeof item === 'object' && (item?.score != null || item?.abuse_score != null) && (
                          <ScoreBadge score={item.score ?? item.abuse_score} />
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            {!Object.values(summary).some(v => Array.isArray(v) && v.length > 0) && (
              <p className="text-xs text-neutral-600 text-center py-4">No IOC lists in summary — counters only.</p>
            )}
          </div>
        )}
      </div>

      {/* Search */}
      <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl p-5">
        <div className="flex gap-2">
          <div className="relative flex-1">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-neutral-500" />
            <input
              type="text"
              value={query}
              onChange={e => setQuery(e.target.value)}
              onKeyDown={handleKey}
              placeholder="8.8.8.8 or evil-domain.com"
              className="w-full bg-white/[0.04] border border-[var(--border-subtle)] rounded-lg pl-9 pr-3 py-2.5 text-sm text-white placeholder-neutral-600 focus:outline-none focus:border-white/20"
            />
          </div>
          <button
            onClick={enrich}
            disabled={!query.trim() || loading}
            className="flex items-center gap-2 px-4 py-2.5 rounded-lg bg-white text-black text-sm font-medium hover:bg-neutral-200 disabled:opacity-50"
          >
            {loading ? <Loader2 size={14} className="animate-spin" /> : <Shield size={14} />}
            Enrich
          </button>
        </div>
        <div className="flex gap-2 mt-3">
          {['ip', 'domain'].map(m => (
            <button
              key={m}
              onClick={() => setMode(m)}
              className={`text-xs px-3 py-1 rounded-full border ${mode === m ? 'border-white/30 bg-white/[0.06] text-white' : 'border-[var(--border-subtle)] text-neutral-600'}`}
            >
              {m === 'ip' ? 'IP Address' : 'Domain'}
            </button>
          ))}
          <span className="text-xs text-neutral-600 self-center ml-1">— auto-detected from input</span>
        </div>
      </div>

      {/* Result */}
      {result && <ResultCard result={result} type={mode} />}

      {/* Recent lookups */}
      {history.length > 0 && (
        <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl overflow-hidden">
          <div className="px-4 py-3 border-b border-[var(--border-subtle)] flex items-center justify-between">
            <h2 className="text-sm font-medium text-white flex items-center gap-2">
              <Activity size={13} className="text-neutral-500" /> Recent Lookups
            </h2>
            <button onClick={() => setHistory([])} className="text-xs text-neutral-600 hover:text-white">Clear</button>
          </div>
          <div className="divide-y divide-[var(--border-subtle)]">
            {history.map((h, i) => (
              <button
                key={i}
                onClick={() => { setQuery(h.value); setMode(h.type) }}
                className="w-full flex items-center justify-between px-4 py-2.5 hover:bg-white/[0.02] text-left"
              >
                <div className="flex items-center gap-2">
                  {h.type === 'ip' ? <Wifi size={12} className="text-neutral-600" /> : <Globe size={12} className="text-neutral-600" />}
                  <span className="text-xs font-mono text-neutral-300">{h.value}</span>
                  <span className="text-[10px] text-neutral-600">{h.ts.toLocaleTimeString()}</span>
                </div>
                <ScoreBadge score={h.score} />
              </button>
            ))}
          </div>
        </div>
      )}

      {/* IOC Management */}
      <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl overflow-hidden">
        <div className="px-4 py-3 border-b border-[var(--border-subtle)] flex items-center justify-between">
          <h2 className="text-sm font-medium text-white flex items-center gap-2">
            <Database size={13} className="text-neutral-500" /> IOC Management
            <span className="text-[10px] font-mono text-neutral-600 ml-2">{iocs.length}</span>
          </h2>
          <div className="flex items-center gap-2">
            <button
              onClick={loadIocs}
              disabled={iocsLoading}
              className="flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-lg border border-[var(--border-subtle)] text-neutral-400 hover:text-white hover:border-white/20 disabled:opacity-40"
            >
              <RefreshCw size={11} className={iocsLoading ? 'animate-spin' : ''} /> Refresh
            </button>
            <button
              onClick={() => setShowIocModal(true)}
              className="flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-lg bg-white text-black hover:bg-neutral-200"
            >
              <Plus size={11} /> Add IOC
            </button>
          </div>
        </div>
        <DataTable
          columns={[
            { key: 'kind',     label: 'Kind',     width: 140, render: (v) => <span className="text-[11px] font-mono text-neutral-300">{v ?? '—'}</span> },
            { key: 'value',    label: 'Value',    render: (v) => <span className="text-[11px] font-mono text-white truncate block max-w-md">{v ?? '—'}</span> },
            { key: 'severity', label: 'Severity', width: 100, render: (v) => (
              <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full border ${SEVERITY_BADGE[v] || SEVERITY_BADGE.low}`}>
                {v ?? '—'}
              </span>
            )},
            { key: 'source',   label: 'Source',   width: 120, render: (v) => <span className="text-[11px] text-neutral-400">{v ?? '—'}</span> },
            { key: 'id',       label: '',         width: 60, render: (_v, row) => (
              <button
                onClick={(e) => { e.stopPropagation(); handleDeleteIoc(row) }}
                aria-label="Delete IOC"
                className="p-1 rounded text-neutral-500 hover:text-red-400 hover:bg-red-500/10"
              >
                <Trash2 size={12} />
              </button>
            )},
          ]}
          data={iocs}
          isLoading={iocsLoading}
          emptyMessage="No IOCs configured — add one or refresh feeds to seed defaults."
        />
      </div>

      {/* Feed Status */}
      <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl overflow-hidden">
        <div className="px-4 py-3 border-b border-[var(--border-subtle)] flex items-center justify-between">
          <h2 className="text-sm font-medium text-white flex items-center gap-2">
            <Rss size={13} className="text-neutral-500" /> Feed Status
            {lastRefreshTs && (
              <span className="text-[10px] font-mono text-neutral-600 ml-2">
                Last refresh: {new Date(Number(lastRefreshTs) * 1000).toLocaleString()}
              </span>
            )}
          </h2>
          <button
            onClick={handleRefreshFeeds}
            disabled={refreshing}
            className="flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-lg bg-white text-black hover:bg-neutral-200 disabled:opacity-40"
          >
            {refreshing ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />}
            Refresh feeds
          </button>
        </div>
        {feedsLoading ? (
          <div className="p-6 text-center text-xs text-neutral-600">
            <Loader2 size={14} className="animate-spin inline mr-2" /> Loading feeds…
          </div>
        ) : feeds.length === 0 ? (
          <p className="px-4 py-6 text-xs text-neutral-600 text-center">No feeds configured.</p>
        ) : (
          <div className="divide-y divide-[var(--border-subtle)]">
            {feeds.map((f) => (
              <div key={f.name} className="px-4 py-3 flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-medium text-white">{f.name}</span>
                    <span className={`text-[10px] px-1.5 py-0.5 rounded-full border ${
                      f.enabled
                        ? 'bg-green-500/10 text-green-400 border-green-500/20'
                        : 'bg-neutral-500/10 text-neutral-500 border-neutral-500/20'
                    }`}>
                      {f.enabled ? 'enabled' : 'disabled'}
                    </span>
                  </div>
                  {f.url && (
                    <div className="text-[10px] text-neutral-600 font-mono truncate mt-0.5">{f.url}</div>
                  )}
                </div>
                <div className="text-[10px] text-neutral-600 shrink-0">
                  every {f.refresh_seconds ? `${f.refresh_seconds}s` : '—'}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Config note */}
      <div className="p-4 bg-white/[0.02] border border-[var(--border-subtle)] rounded-xl text-xs text-neutral-500 leading-relaxed">
        <div className="flex items-start gap-2">
          <Lock size={12} className="shrink-0 text-neutral-600 mt-0.5" />
          <div>
            <strong className="text-neutral-400">Live enrichment:</strong> Set{' '}
            <code className="text-neutral-400">ABUSEIPDB_API_KEY</code> for real IP scores (free tier: 1,000 checks/day) and{' '}
            <code className="text-neutral-400">OTX_API_KEY</code> for AlienVault OTX domain/domain enrichment.
            Without keys, Aegis returns deterministic demo data based on input hashing.
          </div>
        </div>
      </div>

      <IocCreateModal
        isOpen={showIocModal}
        onClose={() => setShowIocModal(false)}
        onCreated={loadIocs}
      />
    </div>
  )
}
