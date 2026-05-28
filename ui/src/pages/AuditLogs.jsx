import React, { useState, useEffect, useCallback, useRef, useContext } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import { auditService, auditExportService } from '../services/api'
import { useAuth } from '../hooks/useAuth'
import { AgentContext } from '../context/AgentContext'
import { eventBus } from '../lib/eventBus'
import {
  ShieldCheck, ShieldAlert, AlertCircle, Search, RefreshCw,
  ChevronLeft, ChevronRight, ChevronDown, ChevronUp,
  Activity, FileText, Hash, Clock, Filter, Eye,
  ToggleLeft, ToggleRight, CheckCircle2, XCircle, Download,
  HelpCircle, Loader2, AlertTriangle, MessageSquare, Plus, Send,
} from 'lucide-react'
import Card from '../components/Common/Card'
import Button from '../components/Common/Button'
import SkeletonLoader from '../components/Common/SkeletonLoader'

/* ── Decision badge ────────────────────────────────────────────────────────── */
const DECISION_STYLES = {
  allow:    'text-green-400  bg-green-500/10  border-green-500/20',
  deny:     'text-red-400    bg-red-500/10    border-red-500/20',
  monitor:  'text-blue-400   bg-blue-500/10   border-blue-500/20',
  throttle: 'text-amber-400  bg-amber-500/10  border-amber-500/20',
  escalate: 'text-orange-400 bg-orange-500/10 border-orange-500/20',
  kill:     'text-red-600    bg-red-900/20    border-red-800/40',
}

function DecisionBadge({ decision }) {
  const d = (decision || 'unknown').toLowerCase()
  return (
    <span className={`status-badge ${DECISION_STYLES[d] ?? 'text-neutral-400 bg-white/5 border-white/10'}`}>
      {d.toUpperCase()}
    </span>
  )
}

/* ── Risk pill ─────────────────────────────────────────────────────────────── */
function RiskPill({ score }) {
  const n = Number(score) || 0
  const color =
    n < 30 ? 'text-green-400 bg-green-500/10 border-green-500/20' :
    n < 70 ? 'text-amber-400 bg-amber-500/10 border-amber-500/20' :
             'text-red-400   bg-red-500/10   border-red-500/20'
  return (
    <span className={`status-badge ${color}`}>
      <Activity size={10} aria-hidden="true" />
      {n}
    </span>
  )
}

/* ── Expanded row ──────────────────────────────────────────────────────────── */
function ExplainPanel({ auditId }) {
  const [loading, setLoading] = useState(false)
  const [data,    setData]    = useState(null)
  const [error,   setError]   = useState('')

  const load = async () => {
    setLoading(true); setError('')
    try {
      const res = await auditService.explainDecision(auditId)
      setData(res?.data || res)
    } catch (e) { setError(e?.message || 'Failed to load explanation') }
    finally { setLoading(false) }
  }

  if (!data && !loading && !error) {
    return (
      <button
        onClick={load}
        className="flex items-center gap-1.5 text-[11px] px-3 py-1.5 rounded-lg border border-indigo-500/30 text-indigo-400 hover:border-indigo-500/60 hover:bg-indigo-500/[0.06] transition-colors"
      >
        <HelpCircle size={12} /> Explain this decision
      </button>
    )
  }

  if (loading) return <div className="flex items-center gap-2 text-xs text-neutral-500"><Loader2 size={12} className="animate-spin" /> Loading explanation…</div>
  if (error)   return <p className="text-xs text-red-400 flex items-center gap-1"><AlertTriangle size={11} />{error}</p>
  if (!data)   return null

  const isDeny = (data.decision || '').toLowerCase() === 'deny'

  return (
    <div className={`rounded-xl border p-4 space-y-3 text-xs ${isDeny ? 'bg-red-500/[0.04] border-red-500/20' : 'bg-white/[0.02] border-white/[0.06]'}`}>
      <div className="flex items-start gap-2">
        {isDeny
          ? <AlertTriangle size={14} className="text-red-400 shrink-0 mt-0.5" />
          : <ShieldCheck size={14} className="text-green-400 shrink-0 mt-0.5" />}
        <p className="text-neutral-200 leading-relaxed">{data.explanation}</p>
      </div>

      {data.signals?.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-[10px] text-neutral-600 uppercase tracking-widest">Triggered Findings</p>
          <div className="flex flex-wrap gap-1.5">
            {data.signals.map((s, i) => (
              <span key={i} className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-red-500/10 border border-red-500/20 text-red-300 text-[10px] font-mono">
                {s.finding}
              </span>
            ))}
          </div>
        </div>
      )}

      {data.timeline?.length > 0 && (
        <div className="space-y-1">
          <p className="text-[10px] text-neutral-600 uppercase tracking-widest">Recent Agent Decisions</p>
          <div className="space-y-1 max-h-36 overflow-y-auto pr-1">
            {data.timeline.map((t, i) => {
              const d = (t.decision || '').toLowerCase()
              const cls = d === 'deny' ? 'text-red-400' : d === 'allow' ? 'text-green-400' : 'text-neutral-400'
              return (
                <div key={i} className="flex items-center gap-2 text-[10px] text-neutral-500">
                  <span className={`font-semibold ${cls} w-12 shrink-0`}>{d.toUpperCase()}</span>
                  <span className="font-mono text-neutral-600">{t.tool || '—'}</span>
                  <span className="ml-auto text-neutral-700">
                    {t.timestamp ? new Date(t.timestamp).toLocaleTimeString() : ''}
                  </span>
                </div>
              )
            })}
          </div>
        </div>
      )}

      <p className="text-[10px] text-neutral-700">
        Policy: {data.policy_context?.framework} {data.policy_context?.version}
      </p>
    </div>
  )
}

/* ── Notes panel ───────────────────────────────────────────────────────────── */
const NOTE_TYPE_STYLE = {
  analysis:         'text-blue-400   bg-blue-500/10   border-blue-500/20',
  false_positive:   'text-amber-400  bg-amber-500/10  border-amber-500/20',
  confirmed_threat: 'text-red-400    bg-red-500/10    border-red-500/20',
  escalated:        'text-orange-400 bg-orange-500/10 border-orange-500/20',
}

const NOTE_TYPES = ['analysis', 'false_positive', 'confirmed_threat', 'escalated']

function NotesPanel({ auditId }) {
  const [notes,     setNotes]     = useState(null)
  const [loading,   setLoading]   = useState(false)
  const [error,     setError]     = useState('')
  const [open,      setOpen]      = useState(false)
  const [body,      setBody]      = useState('')
  const [noteType,  setNoteType]  = useState('analysis')
  const [createdBy, setCreatedBy] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [submitErr,  setSubmitErr]  = useState('')

  const load = async () => {
    setLoading(true); setError('')
    try {
      const res = await auditService.getNotes(auditId)
      setNotes(res?.data ?? res ?? [])
    } catch (e) { setError(e?.message || 'Failed to load notes') }
    finally { setLoading(false) }
  }

  const handleToggle = () => {
    if (!open && notes === null) load()
    setOpen(v => !v)
  }

  const handleAdd = async (e) => {
    e.preventDefault()
    if (!body.trim()) return
    setSubmitting(true); setSubmitErr('')
    try {
      await auditService.addNote(auditId, { note_type: noteType, body: body.trim(), created_by: createdBy.trim() || 'analyst' })
      setBody(''); setCreatedBy(''); setNoteType('analysis')
      await load()
    } catch (e) { setSubmitErr(e?.message || 'Failed to add note') }
    finally { setSubmitting(false) }
  }

  return (
    <div className="space-y-2">
      <button
        onClick={handleToggle}
        className="flex items-center gap-1.5 text-[11px] px-3 py-1.5 rounded-lg border border-violet-500/30 text-violet-400 hover:border-violet-500/60 hover:bg-violet-500/[0.06] transition-colors"
      >
        <MessageSquare size={12} />
        Analyst Notes {notes !== null && <span className="ml-0.5 font-semibold">({notes.length})</span>}
        {open ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
      </button>

      {open && (
        <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4 space-y-4 text-xs">
          {loading && <div className="flex items-center gap-2 text-neutral-500"><Loader2 size={12} className="animate-spin" /> Loading notes…</div>}
          {error   && <p className="text-red-400 flex items-center gap-1"><AlertTriangle size={11} />{error}</p>}

          {notes !== null && notes.length === 0 && !loading && (
            <p className="text-neutral-600 italic">No notes yet. Add one below.</p>
          )}

          {notes !== null && notes.length > 0 && (
            <div className="space-y-3">
              {notes.map(n => (
                <div key={n.id} className="rounded-lg border border-white/[0.05] bg-black/20 p-3 space-y-1.5">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className={`status-badge text-[10px] ${NOTE_TYPE_STYLE[n.note_type] ?? 'text-neutral-400 bg-white/5 border-white/10'}`}>
                      {(n.note_type || 'analysis').replace(/_/g, ' ')}
                    </span>
                    <span className="text-neutral-600">{n.created_by}</span>
                    <span className="ml-auto text-neutral-700">{n.created_at ? new Date(n.created_at).toLocaleString() : ''}</span>
                  </div>
                  <p className="text-neutral-300 leading-relaxed whitespace-pre-wrap">{n.body}</p>
                </div>
              ))}
            </div>
          )}

          <form onSubmit={handleAdd} className="space-y-2 border-t border-white/[0.06] pt-3">
            <div className="flex gap-2">
              <select
                value={noteType}
                onChange={e => setNoteType(e.target.value)}
                className="flex-1 bg-black/30 border border-white/10 rounded-lg px-2 py-1.5 text-xs text-neutral-300 focus:outline-none focus:border-violet-500/50"
              >
                {NOTE_TYPES.map(t => <option key={t} value={t}>{t.replace(/_/g, ' ')}</option>)}
              </select>
              <input
                value={createdBy}
                onChange={e => setCreatedBy(e.target.value)}
                placeholder="Your name (optional)"
                className="flex-1 bg-black/30 border border-white/10 rounded-lg px-2 py-1.5 text-xs text-neutral-300 placeholder-neutral-600 focus:outline-none focus:border-violet-500/50"
              />
            </div>
            <textarea
              value={body}
              onChange={e => setBody(e.target.value)}
              placeholder="Write your analysis note…"
              rows={3}
              className="w-full bg-black/30 border border-white/10 rounded-lg px-3 py-2 text-xs text-neutral-300 placeholder-neutral-600 resize-none focus:outline-none focus:border-violet-500/50"
            />
            {submitErr && <p className="text-red-400 text-[10px]">{submitErr}</p>}
            <button
              type="submit"
              disabled={submitting || !body.trim()}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-violet-600/20 border border-violet-500/30 text-violet-300 text-[11px] hover:bg-violet-600/30 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              {submitting ? <Loader2 size={11} className="animate-spin" /> : <Send size={11} />}
              Add Note
            </button>
          </form>
        </div>
      )}
    </div>
  )
}

function ExpandedRow({ log }) {
  let meta = null
  try { meta = typeof log.metadata_json === 'string' ? JSON.parse(log.metadata_json) : log.metadata_json } catch {}

  return (
    <div className="px-5 py-4 bg-white/[0.015] border-t border-white/5 space-y-4 animate-fade-in">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {[
          { label: 'Request ID',  value: log.request_id },
          { label: 'Event Hash',  value: log.event_hash },
          { label: 'Prev Hash',   value: log.prev_hash },
          { label: 'Reason',      value: log.reason ? `"${log.reason}"` : null, italic: true },
        ].map(({ label, value, italic }) => (
          <div key={label} className="space-y-1.5">
            <p className="label-standard">{label}</p>
            <p className={`font-mono text-xs text-neutral-300 break-all ${italic ? 'italic text-neutral-400' : ''}`}>
              {value || '—'}
            </p>
          </div>
        ))}
      </div>
      {meta && (
        <div className="space-y-1.5">
          <p className="label-standard">Metadata</p>
          <pre className="font-mono text-xs text-neutral-400 bg-black/30 border border-white/5 rounded-xl p-4 overflow-x-auto leading-relaxed">
            {JSON.stringify(meta, null, 2)}
          </pre>
        </div>
      )}
      {log.id && (
        <div className="space-y-1.5">
          <p className="label-standard">Root Cause Analysis</p>
          <ExplainPanel auditId={log.id} />
        </div>
      )}
      {log.id && (
        <div className="space-y-1.5">
          <p className="label-standard">Investigation Notes</p>
          <NotesPanel auditId={log.id} />
        </div>
      )}
    </div>
  )
}

/* ── Constants ─────────────────────────────────────────────────────────────── */
const PAGE_SIZE = 20
const AUTO_REFRESH_MS = 30_000
const DECISION_OPTIONS = ['all', 'allow', 'deny', 'monitor', 'throttle', 'escalate', 'kill']

/* ── Component ─────────────────────────────────────────────────────────────── */
export default function AuditLogs() {
  useAuth()
  const navigate = useNavigate()
  const { selectedAgentId, selectedAgent } = useContext(AgentContext)

  const [searchParams] = useSearchParams()
  const urlAgent = searchParams.get('agent') || ''

  // Resolve the effective agent filter: prefer URL ?agent override; otherwise
  // fall back to AgentContext's selected agent. Unified so the sidebar picker
  // and deep-links both drive the same filter.
  const effectiveAgentId = urlAgent || selectedAgentId || ''

  const [summary,        setSummary]        = useState(null)
  const [summaryLoading, setSummaryLoading] = useState(true)
  const [logs,           setLogs]           = useState([])
  const [logsLoading,    setLogsLoading]    = useState(true)
  const [logsError,      setLogsError]      = useState('')
  const [totalCount,     setTotalCount]     = useState(0)
  const [page,           setPage]           = useState(0)

  const [filterDecision, setFilterDecision] = useState('all')
  const [filterTool,     setFilterTool]     = useState('')
  const [filterFrom,     setFilterFrom]     = useState('')
  const [filterTo,       setFilterTo]       = useState('')
  const [isSearching,    setIsSearching]    = useState(false)
  const [hasSearched,    setHasSearched]    = useState(false)

  const [expandedId,       setExpandedId]       = useState(null)
  const [integrityStatus,  setIntegrityStatus]  = useState(null)
  const [integrityMessage, setIntegrityMessage] = useState('')
  const [autoRefresh,      setAutoRefresh]      = useState(false)
  const [exporting,        setExporting]        = useState(false)

  const autoRefreshRef = useRef(null)
  const mountedRef     = useRef(true)

  const fetchSummary = useCallback(async () => {
    try {
      const res = await auditService.getSummary(effectiveAgentId || undefined)
      if (mountedRef.current) { setSummary(res?.data || res || {}); setSummaryLoading(false) }
    } catch {
      if (mountedRef.current) setSummaryLoading(false)
    }
  }, [effectiveAgentId])

  const fetchLogs = useCallback(async (currentPage = 0) => {
    setLogsLoading(true)
    setLogsError('')
    try {
      const res = await auditService.getLogs(PAGE_SIZE, currentPage * PAGE_SIZE, effectiveAgentId || undefined)
      if (!mountedRef.current) return
      const data  = res?.data || res || {}
      const items = Array.isArray(data) ? data : (data.logs || data.items || [])
      setLogs(items)
      setTotalCount(data.total || items.length)
    } catch (err) {
      if (mountedRef.current) setLogsError(err.message || 'Failed to load audit logs.')
    } finally {
      if (mountedRef.current) setLogsLoading(false)
    }
  }, [effectiveAgentId])

  const handleSearch = useCallback(async (currentPage = 0) => {
    setIsSearching(true)
    setLogsError('')
    setHasSearched(true)
    try {
      const params = { limit: PAGE_SIZE, offset: currentPage * PAGE_SIZE }
      if (effectiveAgentId)          params.agent_id   = effectiveAgentId
      if (filterDecision !== 'all')  params.decision   = filterDecision
      if (filterTool.trim())         params.tool       = filterTool.trim()
      if (filterFrom)                params.start_date = filterFrom
      if (filterTo)                  params.end_date   = filterTo

      const res = await auditService.searchLogs(params)
      if (!mountedRef.current) return
      const data  = res?.data || res || {}
      const items = Array.isArray(data) ? data : (data.logs || data.items || [])
      setLogs(items)
      setTotalCount(data.total || items.length)
    } catch (err) {
      if (mountedRef.current) setLogsError(err.message || 'Search failed.')
    } finally {
      if (mountedRef.current) setIsSearching(false)
    }
  }, [effectiveAgentId, filterDecision, filterTool, filterFrom, filterTo])

  const handleVerifyIntegrity = async () => {
    setIntegrityStatus('checking')
    try {
      const res = await auditService.verifyIntegrity()
      const d = res?.data || res || {}
      // 2026-05-13: backend returns `is_integrous` (canonical); older code paths
      // returned `valid`. Accept both so the UI doesn't false-positive "Chain
      // Broken" on a valid chain. Also surface row count + violation summary.
      const isValid =
        d.valid === true || d.is_integrous === true ||
        (d.success === true && d.details && /no logs/i.test(d.details))
      const violationCount = Array.isArray(d.violations) ? d.violations.length : (d.error_count || 0)
      const friendly = isValid
        ? (d.processed_count != null
            ? `Verified ${d.processed_count.toLocaleString()} entries.`
            : (d.details || ''))
        : (d.error || (violationCount > 0
            ? `${violationCount} chain violation(s) detected.`
            : 'Verification failed.'))
      if (mountedRef.current) {
        setIntegrityStatus(isValid ? 'valid' : 'broken')
        setIntegrityMessage(friendly)
      }
    } catch (err) {
      if (mountedRef.current) {
        setIntegrityStatus('broken')
        setIntegrityMessage(err.message || 'Verification failed.')
      }
    }
  }

  const handlePageChange = (newPage) => {
    setPage(newPage)
    hasSearched ? handleSearch(newPage) : fetchLogs(newPage)
    setExpandedId(null)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  const refreshTick = useCallback(() => {
    fetchSummary()
    hasSearched ? handleSearch(page) : fetchLogs(page)
  }, [fetchSummary, fetchLogs, handleSearch, hasSearched, page])

  useEffect(() => {
    if (autoRefresh) { autoRefreshRef.current = setInterval(refreshTick, AUTO_REFRESH_MS) }
    else { clearInterval(autoRefreshRef.current) }
    return () => clearInterval(autoRefreshRef.current)
  }, [autoRefresh, refreshTick])

  useEffect(() => {
    mountedRef.current = true
    fetchSummary()
    if (urlAgent) { setHasSearched(true) } else { fetchLogs(0) }
    return () => { mountedRef.current = false }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (urlAgent) handleSearch(0)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Re-fire fetches when the agent scope from AgentContext (or URL) changes.
  useEffect(() => {
    fetchSummary()
    setPage(0)
    if (effectiveAgentId) { setHasSearched(true); handleSearch(0) } else { fetchLogs(0) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [effectiveAgentId])

  // Real-time: refresh summary + latest page when the SSE bus fires a new decision/tool event
  useEffect(() => {
    const refresh = () => {
      fetchSummary()
      if (!hasSearched) fetchLogs(0)
    }
    const u1 = eventBus.on('tool_executed',    refresh)
    const u2 = eventBus.on('policy_decision',  refresh)
    const u3 = eventBus.on('risk_updated',     () => fetchSummary())
    return () => { u1(); u2(); u3() }
  }, [fetchSummary, fetchLogs, hasSearched])

  /* ── Loading skeleton ── */
  if (summaryLoading && logsLoading) return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
        {[...Array(4)].map((_, i) => <SkeletonLoader key={i} variant="card" />)}
      </div>
      <SkeletonLoader variant="card" />
      <SkeletonLoader variant="row" count={6} />
    </div>
  )

  const handleExport = async (format) => {
    setExporting(true)
    try {
      const params = {
        format,
        ...(effectiveAgentId ? { agent_id: effectiveAgentId } : {}),
        ...(filterDecision && filterDecision !== 'all' ? { action: filterDecision } : {}),
        ...(filterFrom     ? { start_date: filterFrom    } : {}),
        ...(filterTo       ? { end_date:   filterTo      } : {}),
        limit: 5000,
      }
      const res = await auditExportService.export(params)
      const blob = new Blob([typeof res === 'string' ? res : JSON.stringify(res, null, 2)], {
        type: format === 'csv' ? 'text/csv' : 'application/json',
      })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `acp-audit-${new Date().toISOString().slice(0, 10)}.${format}`
      a.click()
      URL.revokeObjectURL(url)
    } catch { /* silently fail */ }
    setExporting(false)
  }

  const s           = summary || {}
  const totalCalls  = s.total_calls   ?? 0
  const totalDenials = s.total_denials ?? 0
  const activeAgents = s.active_agents ?? 0
  const avgRiskScore = typeof s.avg_risk_score === 'number' ? s.avg_risk_score.toFixed(1) : '—'
  const totalPages   = Math.max(1, Math.ceil(totalCount / PAGE_SIZE))

  return (
    <div className="space-y-6 animate-fade-in">

      {/* ── Header ── */}
      <div className="page-header">
        <div>
          <div className="flex items-center gap-2 flex-wrap">
            <h1 className="text-2xl font-bold text-white tracking-tight">Audit Logs</h1>
            {selectedAgent && (
              <span className="inline-flex items-center gap-1.5 text-[10px] px-2 py-0.5 rounded-full bg-white/[0.05] border border-white/10 text-neutral-400">
                <Filter size={9} /> Scope: {selectedAgent.name || selectedAgentId.slice(0, 8)}
              </span>
            )}
          </div>
          <p className="text-xs text-neutral-500 mt-0.5">Immutable chain-verified event stream</p>
        </div>
        <div className="flex items-center gap-3 flex-wrap">
          {/* Auto-refresh toggle */}
          <button
            type="button"
            onClick={() => setAutoRefresh(v => !v)}
            aria-pressed={autoRefresh}
            aria-label={autoRefresh ? 'Disable auto-refresh' : 'Enable auto-refresh'}
            className="flex items-center gap-2 px-3 py-2 rounded-lg bg-white/[0.02] border border-white/5 hover:border-white/[0.12] transition-colors text-xs text-neutral-400"
          >
            {autoRefresh
              ? <ToggleRight size={16} className="text-green-400" aria-hidden="true" />
              : <ToggleLeft  size={16} className="text-neutral-600" aria-hidden="true" />}
            Auto-Refresh
          </button>

          {/* Chain integrity */}
          <button
            type="button"
            onClick={handleVerifyIntegrity}
            disabled={integrityStatus === 'checking'}
            aria-label="Verify audit chain integrity"
            className={`flex items-center gap-2 px-3 py-2 rounded-lg border text-xs transition-colors ${
              integrityStatus === 'valid'
                ? 'bg-green-500/10 border-green-500/20 text-green-400'
                : integrityStatus === 'broken'
                ? 'bg-red-500/10 border-red-500/20 text-red-400'
                : 'bg-white/[0.02] border-white/5 text-neutral-400 hover:border-white/[0.12] hover:text-white'
            }`}
          >
            {integrityStatus === 'checking' ? (
              <RefreshCw size={13} className="animate-spin" aria-hidden="true" />
            ) : integrityStatus === 'valid' ? (
              <CheckCircle2 size={13} aria-hidden="true" />
            ) : integrityStatus === 'broken' ? (
              <XCircle size={13} aria-hidden="true" />
            ) : (
              <ShieldCheck size={13} aria-hidden="true" />
            )}
            {integrityStatus === 'checking' ? 'Verifying…'  :
             integrityStatus === 'valid'    ? 'Chain Valid'  :
             integrityStatus === 'broken'   ? 'Chain Broken' :
             'Verify Integrity'}
          </button>
          {integrityStatus === 'broken' && integrityMessage && (
            <span className="text-xs text-red-400 font-mono" role="alert">{integrityMessage}</span>
          )}

          {/* Export */}
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => handleExport('csv')}
              disabled={exporting}
              className="flex items-center gap-1.5 px-3 py-2 rounded-l-lg border border-r-0 border-white/5 bg-white/[0.02] text-xs text-neutral-400 hover:text-white hover:border-white/[0.12] disabled:opacity-40 transition-colors"
            >
              <Download size={13} aria-hidden="true" />
              {exporting ? 'Exporting…' : 'CSV'}
            </button>
            <button
              type="button"
              onClick={() => handleExport('json')}
              disabled={exporting}
              className="flex items-center gap-1.5 px-3 py-2 rounded-r-lg border border-white/5 bg-white/[0.02] text-xs text-neutral-400 hover:text-white hover:border-white/[0.12] disabled:opacity-40 transition-colors"
            >
              JSON
            </button>
          </div>
        </div>
      </div>

      {/* ── Summary KPIs ── */}
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
        <Card title="Total API Calls"  value={totalCalls.toLocaleString()}  icon={Activity}   subtitle="All logged gateway events" />
        <Card title="Total Denials"    value={totalDenials.toLocaleString()} icon={ShieldAlert} subtitle="Blocked request events" />
        <Card title="Active Agents"    value={activeAgents.toLocaleString()} icon={FileText}    subtitle="Distinct agent identities" />
        <Card title="Avg Risk Score"   value={avgRiskScore}                  icon={Hash}        subtitle="Across all audit events" />
      </div>

      {/* ── Filter / Search ── */}
      <Card title="Search &amp; Filter" icon={Filter}>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-4">
          <div className="space-y-1.5">
            <label className="label-standard">Agent Scope</label>
            <div className="input-standard h-9 font-mono flex items-center text-xs text-neutral-300">
              {effectiveAgentId
                ? (selectedAgent?.name || effectiveAgentId.slice(0, 8) + '…')
                : <span className="text-neutral-600">all agents (use sidebar to scope)</span>}
            </div>
          </div>
          <div className="space-y-1.5">
            <label htmlFor="filter-decision" className="label-standard">Decision</label>
            <select
              id="filter-decision"
              value={filterDecision}
              onChange={e => setFilterDecision(e.target.value)}
              className="input-standard h-9 uppercase"
            >
              {DECISION_OPTIONS.map(d => (
                <option key={d} value={d} className="bg-[#080808]">{d}</option>
              ))}
            </select>
          </div>
          <div className="space-y-1.5">
            <label htmlFor="filter-tool" className="label-standard">Tool</label>
            <input
              id="filter-tool"
              type="text"
              value={filterTool}
              onChange={e => setFilterTool(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleSearch(0)}
              placeholder="Tool name…"
              className="input-standard h-9 font-mono"
            />
          </div>
          <div className="space-y-1.5">
            <label htmlFor="filter-from" className="label-standard">From</label>
            <input
              id="filter-from"
              type="date"
              value={filterFrom}
              onChange={e => setFilterFrom(e.target.value)}
              className="input-standard h-9"
            />
          </div>
          <div className="space-y-1.5">
            <label htmlFor="filter-to" className="label-standard">To</label>
            <input
              id="filter-to"
              type="date"
              value={filterTo}
              onChange={e => setFilterTo(e.target.value)}
              className="input-standard h-9"
            />
          </div>
        </div>

        <div className="flex items-center gap-3 pt-4 mt-2 border-t border-white/5">
          <Button
            size="sm"
            loading={isSearching}
            onClick={() => { setPage(0); handleSearch(0) }}
          >
            <Search size={13} aria-hidden="true" />
            Search
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setFilterDecision('all')
              setFilterTool('')
              setFilterFrom('')
              setFilterTo('')
              setHasSearched(false)
              setPage(0)
              fetchLogs(0)
            }}
          >
            Clear Filters
          </Button>
          <span className="ml-auto text-xs text-neutral-600">
            {totalCount.toLocaleString()} record{totalCount !== 1 ? 's' : ''}
          </span>
        </div>
      </Card>

      {/* ── Error ── */}
      {logsError && (
        <div className="error-banner" role="alert">
          <div className="flex items-center gap-2">
            <AlertCircle size={14} className="text-red-400 shrink-0" aria-hidden="true" />
            <p className="text-xs text-red-400">{logsError}</p>
          </div>
          <button
            type="button"
            onClick={() => hasSearched ? handleSearch(page) : fetchLogs(page)}
            className="text-xs text-red-400 underline"
          >
            Retry
          </button>
        </div>
      )}

      {/* ── Log table ── */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <div className="section-header">
            <Clock size={14} className="text-neutral-600" aria-hidden="true" />
            Event Stream
            {autoRefresh && (
              <div className="flex items-center gap-1.5 ml-2">
                <div className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" aria-hidden="true" />
                <span className="text-xs text-green-500">Live</span>
              </div>
            )}
          </div>
          <span className="text-xs text-neutral-600">
            Page {page + 1} / {totalPages}
          </span>
        </div>

        {logsLoading || isSearching ? (
          <SkeletonLoader variant="row" count={8} />
        ) : (
          <div className="table-container" role="table" aria-label="Audit log entries">
            <div className="table-scroll">
              <table className="table-base min-w-[700px]">
                <thead>
                  <tr>
                    {['Timestamp', 'Agent ID', 'Tool', 'Decision', 'Risk', 'Reason', 'Request ID', '', ''].map((h, i) => (
                      <th key={i} className="table-th first:pl-5">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {logs.length === 0 ? (
                    <tr>
                      <td colSpan={9} className="py-14 text-center">
                        <div className="flex flex-col items-center gap-3 opacity-40">
                          <FileText size={32} className="text-neutral-700" aria-hidden="true" />
                          <p className="text-xs text-neutral-500">No audit records found</p>
                        </div>
                      </td>
                    </tr>
                  ) : (
                    logs.map((log, idx) => {
                      const isExpanded = expandedId === (log.id || idx)
                      const ts = log.timestamp
                        ? new Date(log.timestamp).toLocaleString('en-US', { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' })
                        : '—'
                      const agentShort = log.agent_id
                        ? `${log.agent_id.slice(0, 8)}…${log.agent_id.slice(-4)}`
                        : '—'
                      const reqShort = log.request_id ? `${log.request_id.slice(0, 8)}…` : '—'
                      const rowId = log.id || idx

                      return (
                        <React.Fragment key={rowId}>
                          <tr
                            className="table-row cursor-pointer"
                            onClick={() => setExpandedId(isExpanded ? null : rowId)}
                            onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setExpandedId(isExpanded ? null : rowId) } }}
                            tabIndex={0}
                            aria-expanded={isExpanded}
                            role="row"
                          >
                            <td className="table-td first:pl-5 font-mono whitespace-nowrap">{ts}</td>
                            <td className="table-td font-mono">{agentShort}</td>
                            <td className="table-td font-bold">{log.tool || '—'}</td>
                            <td className="table-td"><DecisionBadge decision={log.decision} /></td>
                            <td className="table-td"><RiskPill score={log.risk_score} /></td>
                            <td className="table-td max-w-[160px] truncate text-neutral-500 italic">
                              {log.reason ? `"${log.reason}"` : '—'}
                            </td>
                            <td className="table-td font-mono text-neutral-600">{reqShort}</td>
                            <td className="table-td pr-2">
                              {log.agent_id && (
                                <button
                                  onClick={(e) => { e.stopPropagation(); navigate(`/forensics?agent=${log.agent_id}`) }}
                                  aria-label={`Investigate agent ${agentShort}`}
                                  className="flex items-center gap-1 text-[10px] text-neutral-600 hover:text-blue-400 transition-colors px-2 py-1 rounded border border-transparent hover:border-blue-500/20 hover:bg-blue-500/[0.04]"
                                >
                                  <Eye size={11} aria-hidden="true" /> Investigate
                                </button>
                              )}
                            </td>
                            <td className="table-td pr-4 text-neutral-500">
                              {isExpanded
                                ? <ChevronUp size={13} aria-hidden="true" />
                                : <ChevronDown size={13} aria-hidden="true" />}
                            </td>
                          </tr>
                          {isExpanded && (
                            <tr role="row">
                              <td colSpan={9} className="p-0">
                                <ExpandedRow log={log} />
                              </td>
                            </tr>
                          )}
                        </React.Fragment>
                      )
                    })
                  )}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* ── Pagination ── */}
        {totalPages > 1 && !logsLoading && !isSearching && (
          <div className="flex items-center justify-between pt-1">
            <Button
              variant="ghost"
              size="sm"
              disabled={page === 0}
              onClick={() => handlePageChange(page - 1)}
              aria-label="Previous page"
            >
              <ChevronLeft size={14} aria-hidden="true" /> Prev
            </Button>

            <div className="flex items-center gap-1" role="navigation" aria-label="Pagination">
              {Array.from({ length: Math.min(7, totalPages) }, (_, i) => {
                let p
                if (totalPages <= 7)       p = i
                else if (page < 4)         p = i
                else if (page > totalPages - 5) p = totalPages - 7 + i
                else                       p = page - 3 + i
                return (
                  <button
                    key={p}
                    type="button"
                    onClick={() => handlePageChange(p)}
                    aria-label={`Page ${p + 1}`}
                    aria-current={p === page ? 'page' : undefined}
                    className={`w-7 h-7 rounded text-xs font-bold transition-colors ${
                      p === page
                        ? 'bg-white text-black'
                        : 'text-neutral-600 hover:text-white hover:bg-white/[0.05]'
                    }`}
                  >
                    {p + 1}
                  </button>
                )
              })}
            </div>

            <Button
              variant="ghost"
              size="sm"
              disabled={page >= totalPages - 1}
              onClick={() => handlePageChange(page + 1)}
              aria-label="Next page"
            >
              Next <ChevronRight size={14} aria-hidden="true" />
            </Button>
          </div>
        )}
      </div>
    </div>
  )
}
