import React, { useState, useEffect, useCallback, useMemo } from 'react'
import { Link } from 'react-router-dom'
import {
  Shield, Play, RefreshCw, Plus, Trash2, History,
  Zap, CheckCircle2, AlertTriangle, Loader2, X,
  ToggleLeft, ToggleRight, Code, Clock,
} from 'lucide-react'
import { playbookService } from '../services/api'
import { useAuth } from '../hooks/useAuth'
import { useSSE } from '../hooks/useSSE'
import { eventBus } from '../lib/eventBus'
import SkeletonLoader from '../components/Common/SkeletonLoader'

const STEP_COLORS = {
  KILL_AGENT:    'text-red-400',
  ISOLATE_AGENT: 'text-orange-400',
  BLOCK_TOOL:    'text-amber-400',
  THROTTLE:      'text-blue-400',
  REVOKE_KEY:    'text-rose-400',
  SEND_ALERT:    'text-purple-400',
  WEBHOOK:       'text-cyan-400',
}

const MODE_STYLE = {
  auto:   'text-green-400 bg-green-500/10 border-green-500/20',
  manual: 'text-amber-400 bg-amber-500/10 border-amber-500/20',
  semi:   'text-blue-400 bg-blue-500/10 border-blue-500/20',
}

function StepPill({ step, idx }) {
  return (
    <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 text-[10px] rounded bg-white/[0.02] border border-white/[0.06] font-mono ${STEP_COLORS[step.action_type] || 'text-neutral-400'}`}>
      <span className="text-neutral-700">{idx + 1}.</span>{step.action_type}
    </span>
  )
}

/* ── Trigger modal ───────────────────────────────────────────────────────────── */
function TriggerModal({ playbook, onClose, onTriggered }) {
  const [ctx,      setCtx]      = useState('{}')
  const [ctxErr,   setCtxErr]   = useState('')
  const [loading,  setLoading]  = useState(false)
  const [error,    setError]    = useState('')

  const trigger = async () => {
    let parsed
    try { parsed = JSON.parse(ctx) } catch { setCtxErr('Invalid JSON'); return }
    setCtxErr('')
    setLoading(true)
    setError('')
    try {
      await playbookService.trigger(playbook.id, parsed)
      onTriggered()
      onClose()
    } catch (e) {
      setError(e?.message || 'Trigger failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center" onClick={onClose} role="dialog" aria-modal="true">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" />
      <div className="relative bg-[var(--bg-surface-elevated)] border border-[var(--border-default)] rounded-2xl shadow-2xl p-6 w-full max-w-md mx-4 space-y-4" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-white">Trigger: {playbook.name}</h2>
          <button onClick={onClose} className="text-neutral-600 hover:text-white"><X size={16} /></button>
        </div>

        {error && (
          <div className="text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">{error}</div>
        )}

        <div>
          <label className="block text-xs text-neutral-400 mb-1.5 flex items-center gap-1.5">
            <Code size={11} /> Context JSON
          </label>
          <textarea name="text"
            value={ctx}
            onChange={e => { setCtx(e.target.value); setCtxErr('') }}
            rows={5}
            spellCheck={false}
            className={`w-full bg-black/40 border rounded-lg px-3 py-2 text-xs font-mono text-green-400 placeholder-neutral-700 focus:outline-none resize-none ${ctxErr ? 'border-red-500/40' : 'border-[var(--border-subtle)] focus:border-white/20'}`}
            placeholder='{ "agent_id": "...", "incident_id": "..." }'
          />
          {ctxErr && <p className="text-[10px] text-red-400 mt-1">{ctxErr}</p>}
          <p className="text-[10px] text-neutral-600 mt-1">Optional context passed to playbook steps.</p>
        </div>

        <div className="flex gap-2">
          <button
            onClick={trigger}
            disabled={loading}
            className="flex-1 flex items-center justify-center gap-2 py-2.5 rounded-lg bg-white text-black text-sm font-medium hover:bg-neutral-200 disabled:opacity-50"
          >
            {loading ? <Loader2 size={14} className="animate-spin" /> : <Zap size={14} />}
            {loading ? 'Triggering…' : 'Trigger Playbook'}
          </button>
          <button onClick={onClose} className="px-4 py-2.5 rounded-lg border border-[var(--border-subtle)] text-sm text-neutral-400 hover:text-white hover:border-white/20">
            Cancel
          </button>
        </div>
      </div>
    </div>
  )
}

/* ── Runs modal ──────────────────────────────────────────────────────────────── */
function RunsModal({ playbookId, onClose }) {
  const [runs,    setRuns]    = useState([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')

  useEffect(() => {
    playbookService.getRuns(playbookId)
      .then(r => { setRuns(r?.data || r || []); setLoadError('') })
      .catch(err => setLoadError(err?.message || 'Failed to load run history'))
      .finally(() => setLoading(false))
  }, [playbookId])

  const statusColor = (s) =>
    s === 'success' ? 'text-green-400' : s === 'failed' ? 'text-red-400' : 'text-amber-400'

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center" onClick={onClose} role="dialog" aria-modal="true">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" />
      <div className="relative bg-[var(--bg-surface-elevated)] border border-[var(--border-default)] rounded-2xl shadow-2xl p-6 w-full max-w-lg mx-4 space-y-4" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-white flex items-center gap-2"><History size={14} /> Run History</h2>
          <button onClick={onClose} className="text-neutral-600 hover:text-white"><X size={16} /></button>
        </div>

        {loading ? (
          <div className="flex items-center justify-center h-32">
            <Loader2 className="animate-spin text-neutral-500" size={22} />
          </div>
        ) : loadError ? (
          <div className="text-center py-10 px-4">
            <p className="text-xs text-red-400">{loadError}</p>
            <p className="text-[10px] text-neutral-600 mt-1">Could not reach the playbook service; try again in a moment.</p>
          </div>
        ) : runs.length === 0 ? (
          <div className="text-center py-12 px-4 space-y-2">
            <History size={24} className="text-neutral-700 mx-auto mb-3" />
            <p className="text-sm text-neutral-500">No runs yet.</p>
            <p className="text-xs text-neutral-600 max-w-xs mx-auto">
              Install a playbook from the Templates tab or trigger one manually
              to populate this history.
            </p>
          </div>
        ) : (
          <div className="space-y-2 max-h-80 overflow-y-auto pr-1">
            {runs.map((r, i) => (
              <div key={r.id || i} className="p-3 rounded-xl border border-[var(--border-subtle)] bg-white/[0.02] space-y-1.5">
                <div className="flex items-center justify-between text-xs">
                  <span className={`font-semibold ${statusColor(r.status)}`}>{r.status?.toUpperCase() || 'UNKNOWN'}</span>
                  <span className="text-neutral-600 font-mono text-[10px]">
                    {r.started_at ? new Date(r.started_at).toLocaleString() : '—'}
                  </span>
                </div>
                <p className="text-[10px] text-neutral-500">by {r.triggered_by || 'manual'}</p>
                {r.steps_executed?.length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {r.steps_executed.map((s, j) => (
                      <span key={j} className="text-[10px] px-1.5 py-0.5 rounded bg-white/[0.02] border border-white/[0.05] text-neutral-500 font-mono">
                        {s.action_type || s}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

/* ── Playbook card ───────────────────────────────────────────────────────────── */
function PlaybookCard({ pb, onTrigger, onDelete, onViewRuns, onToggle, autoStats }) {
  const [deleting,   setDeleting]   = useState(false)
  const [toggling,   setToggling]   = useState(false)

  const handleDelete = async () => {
    setDeleting(true)
    await onDelete(pb.id).finally(() => setDeleting(false))
  }

  const handleToggle = async () => {
    setToggling(true)
    await onToggle(pb.id, !pb.is_active).finally(() => setToggling(false))
  }

  const ToggleIcon = pb.is_active ? ToggleRight : ToggleLeft
  const toggleColor = pb.is_active ? 'text-green-400' : 'text-neutral-600'

  return (
    <div className={`bg-[var(--bg-surface)] border rounded-xl p-4 space-y-3 transition-all ${pb.is_active ? 'border-[var(--border-subtle)]' : 'border-white/[0.04] opacity-60'}`}>
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-0.5 flex-wrap">
            <h3 className="text-sm font-semibold text-white truncate">{pb.name}</h3>
            {pb.mode && (
              <span className={`text-[10px] px-2 py-0.5 rounded-full border font-medium ${MODE_STYLE[pb.mode] || MODE_STYLE.manual}`}>
                {pb.mode}
              </span>
            )}
            {!pb.is_active && (
              <span className="text-[10px] px-2 py-0.5 rounded-full border border-neutral-700 text-neutral-500">inactive</span>
            )}
          </div>
          {pb.description && (
            <p className="text-[11px] text-neutral-500 leading-relaxed">{pb.description}</p>
          )}
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <button
            onClick={handleToggle}
            disabled={toggling}
            title={pb.is_active ? 'Deactivate' : 'Activate'}
            className={`p-1.5 rounded-lg transition-colors ${toggleColor} hover:text-white`}
          >
            {toggling ? <Loader2 size={14} className="animate-spin" /> : <ToggleIcon size={14} />}
          </button>
          <button
            onClick={() => onViewRuns(pb.id)}
            className="p-1.5 rounded-lg text-neutral-600 hover:text-white transition-colors"
            title="View run history"
          >
            <History size={13} />
          </button>
          <button
            onClick={() => onTrigger(pb)}
            disabled={!pb.is_active}
            className="flex items-center gap-1 px-2 py-1 text-[11px] text-purple-400 bg-purple-500/[0.06] border border-purple-500/20 rounded-lg hover:border-purple-500/40 disabled:opacity-40 transition-colors"
          >
            <Zap size={10} /> Trigger
          </button>
          <button
            onClick={handleDelete}
            disabled={deleting}
            className="p-1.5 rounded-lg text-neutral-700 hover:text-red-400 hover:bg-red-500/10 transition-colors"
            title="Remove playbook"
          >
            {deleting ? <Loader2 size={13} className="animate-spin" /> : <Trash2 size={13} />}
          </button>
        </div>
      </div>

      {Array.isArray(pb.steps) && pb.steps.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {pb.steps.map((step, i) => <StepPill key={i} step={step} idx={i} />)}
        </div>
      )}

      <div className="flex items-center gap-3 flex-wrap">
        {pb.run_count != null && (
          <p className="text-[10px] text-neutral-600 flex items-center gap-1">
            <Clock size={10} /> Triggered {pb.run_count} time{pb.run_count !== 1 ? 's' : ''}
          </p>
        )}
        {autoStats && autoStats.auto_count > 0 && (
          <p className="text-[10px] text-green-600 flex items-center gap-1">
            <Zap size={10} /> Auto-fired {autoStats.auto_count}×
            {autoStats.last_auto_at && (
              <span className="text-neutral-600"> · {new Date(autoStats.last_auto_at).toLocaleString()}</span>
            )}
          </p>
        )}
        {pb.mode === 'auto' && (!autoStats || autoStats.auto_count === 0) && (
          <p className="text-[10px] text-neutral-700 flex items-center gap-1">
            <Zap size={10} /> Watching for matching incidents…
          </p>
        )}
      </div>
    </div>
  )
}

/* ── Template card ───────────────────────────────────────────────────────────── */
function TemplateCard({ tmpl, onInstall, installing }) {
  return (
    <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl p-4 space-y-3">
      <div>
        <h3 className="text-sm font-semibold text-white">{tmpl.name}</h3>
        {tmpl.description && (
          <p className="text-[11px] text-neutral-500 mt-0.5 leading-relaxed">{tmpl.description}</p>
        )}
      </div>

      {Array.isArray(tmpl.steps) && tmpl.steps.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {tmpl.steps.map((s, i) => <StepPill key={i} step={s} idx={i} />)}
        </div>
      )}

      {tmpl.trigger_conditions && (
        <p className="text-[10px] text-neutral-600 font-mono">
          trigger: {JSON.stringify(tmpl.trigger_conditions).slice(0, 60)}
        </p>
      )}

      <button
        onClick={() => onInstall(tmpl)}
        disabled={installing}
        className="w-full flex items-center justify-center gap-1.5 py-1.5 text-xs text-purple-400 bg-purple-500/[0.06] border border-purple-500/20 rounded-lg hover:border-purple-500/40 disabled:opacity-50 transition-colors"
      >
        {installing ? <Loader2 size={11} className="animate-spin" /> : <Plus size={11} />}
        {installing ? 'Installing…' : 'Install'}
      </button>
    </div>
  )
}

/* ── Main component ──────────────────────────────────────────────────────────── */
export default function Playbooks() {
  const { addToast } = useAuth()
  const [playbooks,   setPlaybooks]   = useState([])
  const [templates,   setTemplates]   = useState([])
  const [stats,       setStats]       = useState(null)
  const [loading,     setLoading]     = useState(true)
  const [hasLoaded,   setHasLoaded]   = useState(false)
  const [tab,         setTab]         = useState('installed')
  const [triggerFor,   setTriggerFor]   = useState(null)
  const [runsFor,      setRunsFor]      = useState(null)
  const [installing,   setInstalling]   = useState(null)
  const [autoStatsMap, setAutoStatsMap] = useState({})

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [pbRes, tmplRes, statsRes, autoRes] = await Promise.allSettled([
        playbookService.list(),
        playbookService.getTemplates(),
        playbookService.getStats ? playbookService.getStats() : Promise.resolve(null),
        playbookService.getAutotriggerStats(),
      ])
      if (pbRes.status   === 'fulfilled') setPlaybooks(pbRes.value?.data || pbRes.value || [])
      if (tmplRes.status === 'fulfilled') setTemplates(tmplRes.value?.data || tmplRes.value || [])
      if (statsRes.status === 'fulfilled' && statsRes.value) {
        setStats(statsRes.value?.data || statsRes.value)
      }
      if (autoRes.status === 'fulfilled') {
        const rows = autoRes.value?.data || autoRes.value || []
        const map = {}
        for (const r of rows) map[r.playbook_id] = r
        setAutoStatsMap(map)
      }
    } catch {}
    setLoading(false)
    setHasLoaded(true)
  }, [])

  useEffect(() => { load() }, [load])

  // Real-time refresh: react to playbook + ARE + incident events so the
  // run-count + auto-trigger stats update without a manual refresh.
  const sseChannels = useMemo(() => ({
    auto_response_executed: () => load(),
    playbook_run:           () => load(),
    incident_updated:       () => load(),
  }), [load])
  useSSE({
    channels: sseChannels,
    onMessage: (evt) => {
      const t = String(evt?.type || '').toLowerCase()
      if (t.includes('playbook') || t.includes('auto_response') || t.includes('incident')) {
        load()
      }
    },
  })
  useEffect(() => {
    const u = eventBus.on('auto_response_executed', load)
    return u
  }, [load])

  const handleInstall = async (tmpl) => {
    setInstalling(tmpl.name)
    try {
      await playbookService.create(tmpl)
      addToast(`"${tmpl.name}" installed`, 'success')
      await load()
      setTab('installed')
    } catch (e) {
      addToast(e?.message || 'Install failed', 'error')
    } finally {
      setInstalling(null)
    }
  }

  const handleDelete = async (id) => {
    try {
      await playbookService.remove(id)
      addToast('Playbook removed', 'info')
      await load()
    } catch (e) {
      addToast(e?.message || 'Remove failed', 'error')
    }
  }

  const handleToggle = async (id, active) => {
    try {
      await playbookService.update(id, { is_active: active })
      setPlaybooks(prev => prev.map(p => p.id === id ? { ...p, is_active: active } : p))
    } catch (e) {
      addToast(e?.message || 'Update failed', 'error')
    }
  }

  const active   = playbooks.filter(p => p.is_active).length
  const inactive = playbooks.length - active

  const statCards = [
    { label: 'Installed',   value: playbooks.length,                   color: 'text-white' },
    { label: 'Active',      value: active,                             color: 'text-green-400' },
    { label: 'Inactive',    value: inactive,                           color: inactive > 0 ? 'text-amber-400' : 'text-neutral-500' },
    { label: 'Templates',   value: templates.length,                   color: 'text-blue-400' },
    { label: 'Runs (24h)',  value: stats?.triggers_24h ?? '—',         color: 'text-white' },
  ]

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-white mb-1">Incident Playbooks</h1>
          <p className="text-sm text-neutral-400">
            Automated response sequences triggered by policy decisions or manual invocation.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={load}
            disabled={loading}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[var(--border-subtle)] text-xs text-neutral-300 hover:border-white/20"
          >
            <RefreshCw size={12} className={loading ? 'animate-spin' : ''} /> Refresh
          </button>
          <button
            onClick={() => setTab('templates')}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-white text-black text-xs font-medium hover:bg-neutral-200"
          >
            <Plus size={13} /> Install from template
          </button>
        </div>
      </header>

      {/* Stats strip */}
      <div className="grid grid-cols-3 sm:grid-cols-5 gap-3">
        {statCards.map(({ label, value, color }) => (
          <div key={label} className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl p-4">
            <div className={`text-2xl font-semibold ${color}`}>{value}</div>
            <div className="text-xs text-neutral-500 mt-1">{label}</div>
          </div>
        ))}
      </div>

      {/* Tabs */}
      <div className="flex gap-1">
        {['installed', 'templates'].map(t => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-2 text-xs font-medium rounded-lg border transition-all capitalize ${tab === t ? 'border-white/30 bg-white/[0.08] text-white' : 'border-[var(--border-subtle)] text-neutral-500 hover:border-white/20'}`}
          >
            {t === 'installed' ? `Installed (${playbooks.length})` : `Template Library (${templates.length})`}
          </button>
        ))}
      </div>

      {/* Content */}
      {!hasLoaded ? (
        <div className="space-y-3">
          <SkeletonLoader variant="card" count={3} />
        </div>
      ) : tab === 'installed' ? (
        playbooks.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 gap-4">
            <div className="w-14 h-14 rounded-2xl bg-white/[0.04] flex items-center justify-center">
              <Shield size={24} className="text-neutral-600" />
            </div>
            <div className="text-center max-w-sm">
              <p className="text-sm text-neutral-200 mb-1 font-medium">No playbooks installed</p>
              <p className="text-xs text-neutral-500 leading-relaxed">
                Playbooks chain response steps (kill, isolate, revoke key, alert)
                triggered by ARE rules or manually. Install from the template
                library to automate incident response.
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2 justify-center">
              <button
                onClick={() => setTab('templates')}
                className="flex items-center gap-2 px-4 py-2 rounded-lg bg-white text-black text-xs font-medium hover:bg-neutral-200"
              >
                <Plus size={13} /> Browse Templates
              </button>
              <Link
                to="/auto-response"
                className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium text-neutral-200 bg-white/[0.04] border border-white/[0.08] hover:border-white/20 transition-colors"
              >
                <Zap size={12} aria-hidden="true" /> Configure ARE rules
              </Link>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            {playbooks.map(pb => (
              <PlaybookCard
                key={pb.id}
                pb={pb}
                onTrigger={setTriggerFor}
                onDelete={handleDelete}
                onViewRuns={setRunsFor}
                onToggle={handleToggle}
                autoStats={autoStatsMap[pb.id]}
              />
            ))}
          </div>
        )
      ) : (
        templates.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 gap-3">
            <p className="text-sm text-neutral-500">No templates available.</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {templates.map((tmpl, i) => (
              <TemplateCard
                key={i}
                tmpl={tmpl}
                onInstall={handleInstall}
                installing={installing === tmpl.name}
              />
            ))}
          </div>
        )
      )}

      {/* Info footer */}
      <p className="text-[11px] text-neutral-700">
        Auto-mode playbooks trigger when ARE fires matching rules. Manual-mode playbooks require explicit invocation.
        Runs are logged in the audit trail with full step trace.
      </p>

      {triggerFor && (
        <TriggerModal
          playbook={triggerFor}
          onClose={() => setTriggerFor(null)}
          onTriggered={() => { addToast('Playbook triggered', 'success'); load() }}
        />
      )}
      {runsFor && <RunsModal playbookId={runsFor} onClose={() => setRunsFor(null)} />}
    </div>
  )
}
