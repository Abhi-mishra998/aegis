import React, { useEffect, useState, useCallback } from 'react'
import {
  Calendar, Plus, Trash2, Play, ToggleLeft, ToggleRight,
  Clock, Mail, FileText, ChevronDown, ChevronUp, Loader2, X, CheckCircle2,
  AlertCircle, History,
} from 'lucide-react'
import { scheduledReportsService } from '../services/api'

const DELIVERY_STATUS_STYLE = {
  success: { color: 'text-green-400',  bg: 'bg-green-500/10',  border: 'border-green-500/20',  icon: CheckCircle2 },
  failed:  { color: 'text-red-400',    bg: 'bg-red-500/10',    border: 'border-red-500/20',    icon: AlertCircle  },
  skipped: { color: 'text-amber-400',  bg: 'bg-amber-500/10',  border: 'border-amber-500/20',  icon: Clock        },
  queued:  { color: 'text-blue-400',   bg: 'bg-blue-500/10',   border: 'border-blue-500/20',   icon: Loader2      },
}

function DeliveryHistory({ reportId }) {
  const [open, setOpen] = useState(false)
  const [deliveries, setDeliveries] = useState([])
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await scheduledReportsService.getHistory(reportId, 10)
      setDeliveries(res?.data || res || [])
    } catch { setDeliveries([]) }
    setLoading(false)
  }, [reportId])

  const toggle = () => {
    if (!open) load()
    setOpen(o => !o)
  }

  return (
    <div className="mt-3 border-t border-white/[0.04] pt-3">
      <button
        onClick={toggle}
        className="flex items-center gap-1.5 text-[10px] text-neutral-500 hover:text-neutral-300"
      >
        <History size={10} />
        Delivery history
        {open ? <ChevronUp size={10} /> : <ChevronDown size={10} />}
      </button>

      {open && (
        <div className="mt-2 space-y-1.5">
          {loading && <div className="text-[10px] text-neutral-600">Loading…</div>}
          {!loading && deliveries.length === 0 && (
            <div className="text-[10px] text-neutral-600">No deliveries recorded yet.</div>
          )}
          {deliveries.map((d) => {
            const s = DELIVERY_STATUS_STYLE[d.status] || DELIVERY_STATUS_STYLE.skipped
            const Icon = s.icon
            return (
              <div key={d.id} className={`flex items-start gap-2 px-2.5 py-1.5 rounded-lg border text-[10px] ${s.bg} ${s.border}`}>
                <Icon size={10} className={`${s.color} mt-0.5 shrink-0`} />
                <div className="flex-1 min-w-0">
                  <div className={`font-medium ${s.color}`}>
                    {d.status.toUpperCase()} — {d.triggered_by}
                    {d.duration_ms != null && <span className="text-neutral-500 ml-1">({d.duration_ms}ms)</span>}
                  </div>
                  {d.error_message && (
                    <div className="text-neutral-500 truncate">{d.error_message}</div>
                  )}
                  {d.recipients?.length > 0 && (
                    <div className="text-neutral-500 truncate">→ {d.recipients.join(', ')}</div>
                  )}
                </div>
                <div className="text-neutral-600 shrink-0 ml-1">
                  {d.created_at ? new Date(d.created_at).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—'}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

const REPORT_TYPES = [
  { value: 'board',      label: 'Board Report',       desc: 'Executive PDF — decisions, block rate, cost savings' },
  { value: 'eu-ai-act',  label: 'EU AI Act',          desc: 'Article 13/16/61 evidence bundle' },
  { value: 'nist',       label: 'NIST AI RMF',        desc: 'GOVERN / MAP / MEASURE / MANAGE controls' },
  { value: 'soc2',       label: 'SOC 2',              desc: 'CC6/CC7 compliance evidence' },
  { value: 'llm_cost',   label: 'LLM Cost Digest',    desc: 'Weekly per-agent LLM inference cost breakdown email' },
]

const SCHEDULE_OPTIONS = [
  { value: 'daily',   label: 'Daily',   sub: 'Sent every day at 07:00 UTC' },
  { value: 'weekly',  label: 'Weekly',  sub: 'Sent every Monday at 07:00 UTC' },
  { value: 'monthly', label: 'Monthly', sub: 'Sent on the 1st of each month' },
]

function Modal({ open, onClose, title, children }) {
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={onClose} />
      <div className="relative z-10 w-full max-w-lg bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-2xl shadow-2xl">
        <div className="flex items-center justify-between px-6 py-4 border-b border-[var(--border-subtle)]">
          <h2 className="text-sm font-semibold text-white">{title}</h2>
          <button onClick={onClose} className="text-neutral-500 hover:text-white"><X size={16} /></button>
        </div>
        <div className="px-6 py-5">{children}</div>
      </div>
    </div>
  )
}

function ReportCard({ report, onToggle, onDelete, onRunNow, running }) {
  const [deleting, setDeleting] = useState(false)
  const scheduleLabel = SCHEDULE_OPTIONS.find(s => s.value === report.schedule)?.label || report.schedule
  const typeLabel     = REPORT_TYPES.find(t => t.value === report.report_type)?.label || report.report_type

  const handleDelete = async () => {
    setDeleting(true)
    await onDelete(report.id)
  }

  return (
    <div className={`bg-[var(--bg-surface)] border rounded-xl p-4 transition-all ${report.is_active ? 'border-[var(--border-subtle)]' : 'border-white/[0.04] opacity-60'}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-sm font-medium text-white truncate">{report.name}</span>
            <span className="shrink-0 text-[10px] px-1.5 py-0.5 rounded-full bg-white/[0.06] text-neutral-400">{typeLabel}</span>
          </div>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-neutral-500">
            <span className="flex items-center gap-1"><Calendar size={11} />{scheduleLabel}</span>
            <span className="flex items-center gap-1"><Mail size={11} />{(report.recipients || []).join(', ') || 'No recipients'}</span>
            {report.last_run_at && (
              <span className="flex items-center gap-1"><Clock size={11} />Last: {new Date(report.last_run_at).toLocaleDateString()}</span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          <button
            onClick={() => onRunNow(report.id)}
            disabled={running === report.id}
            title="Run now"
            className="p-1.5 rounded-lg text-neutral-500 hover:text-white hover:bg-white/[0.06] disabled:opacity-40"
          >
            {running === report.id ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
          </button>
          <button
            onClick={() => onToggle(report)}
            title={report.is_active ? 'Pause' : 'Activate'}
            className="p-1.5 rounded-lg text-neutral-500 hover:text-white hover:bg-white/[0.06]"
          >
            {report.is_active ? <ToggleRight size={14} className="text-green-400" /> : <ToggleLeft size={14} />}
          </button>
          <button
            onClick={handleDelete}
            disabled={deleting}
            title="Delete"
            className="p-1.5 rounded-lg text-neutral-500 hover:text-red-400 hover:bg-red-500/10 disabled:opacity-40"
          >
            {deleting ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />}
          </button>
        </div>
      </div>
      {report.next_run_at && (
        <div className="mt-2 text-[10px] text-neutral-600 flex items-center gap-1">
          <Clock size={10} /> Next: {new Date(report.next_run_at).toLocaleString()}
        </div>
      )}
      <DeliveryHistory reportId={report.id} />
    </div>
  )
}

function CreateModal({ open, onClose, onCreate }) {
  const [form, setForm] = useState({ name: '', report_type: 'board', schedule: 'monthly', recipients: '' })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const submit = async () => {
    if (!form.name.trim()) { setError('Name is required.'); return }
    const emails = form.recipients.split(',').map(e => e.trim()).filter(Boolean)
    if (emails.length === 0) { setError('At least one recipient email is required.'); return }
    setSaving(true)
    setError('')
    try {
      await onCreate({ ...form, recipients: emails })
      setForm({ name: '', report_type: 'board', schedule: 'monthly', recipients: '' })
      onClose()
    } catch {
      setError('Failed to create scheduled report.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="New Scheduled Report">
      <div className="space-y-4">
        {error && (
          <div className="flex items-center gap-2 p-2.5 bg-red-500/10 border border-red-500/20 rounded-lg text-xs text-red-400">
            <AlertCircle size={12} /> {error}
          </div>
        )}
        <div>
          <label className="block text-xs text-neutral-400 mb-1">Report name</label>
          <input
            type="text"
            value={form.name}
            onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
            placeholder="Monthly Board Report"
            className="w-full bg-white/[0.04] border border-[var(--border-subtle)] rounded-lg px-3 py-2 text-sm text-white placeholder-neutral-600 focus:outline-none focus:border-white/20"
          />
        </div>
        <div>
          <label className="block text-xs text-neutral-400 mb-1">Report type</label>
          <div className="grid grid-cols-2 gap-2">
            {REPORT_TYPES.map(t => (
              <button
                key={t.value}
                onClick={() => setForm(f => ({ ...f, report_type: t.value }))}
                className={`text-left p-3 rounded-lg border text-xs transition-all ${form.report_type === t.value ? 'border-white/30 bg-white/[0.06] text-white' : 'border-[var(--border-subtle)] text-neutral-500 hover:border-white/20'}`}
              >
                <div className="font-medium">{t.label}</div>
                <div className="text-[10px] mt-0.5 text-neutral-600">{t.desc}</div>
              </button>
            ))}
          </div>
        </div>
        <div>
          <label className="block text-xs text-neutral-400 mb-1">Delivery schedule</label>
          <div className="space-y-1.5">
            {SCHEDULE_OPTIONS.map(s => (
              <button
                key={s.value}
                onClick={() => setForm(f => ({ ...f, schedule: s.value }))}
                className={`w-full flex items-center justify-between p-2.5 rounded-lg border text-xs transition-all ${form.schedule === s.value ? 'border-white/30 bg-white/[0.06] text-white' : 'border-[var(--border-subtle)] text-neutral-500 hover:border-white/20'}`}
              >
                <span className="font-medium">{s.label}</span>
                <span className="text-[10px] text-neutral-600">{s.sub}</span>
              </button>
            ))}
          </div>
        </div>
        <div>
          <label className="block text-xs text-neutral-400 mb-1">Recipients (comma-separated)</label>
          <input
            type="text"
            value={form.recipients}
            onChange={e => setForm(f => ({ ...f, recipients: e.target.value }))}
            placeholder="ceo@company.com, cfo@company.com"
            className="w-full bg-white/[0.04] border border-[var(--border-subtle)] rounded-lg px-3 py-2 text-sm text-white placeholder-neutral-600 focus:outline-none focus:border-white/20"
          />
        </div>
        <div className="flex gap-2 pt-1">
          <button onClick={onClose} className="flex-1 px-4 py-2 rounded-lg border border-[var(--border-subtle)] text-sm text-neutral-400 hover:text-white">Cancel</button>
          <button onClick={submit} disabled={saving} className="flex-1 px-4 py-2 rounded-lg bg-white text-black text-sm font-medium hover:bg-neutral-200 disabled:opacity-50 flex items-center justify-center gap-2">
            {saving ? <Loader2 size={14} className="animate-spin" /> : null}
            Create
          </button>
        </div>
      </div>
    </Modal>
  )
}

export default function ScheduledReports() {
  const [reports, setReports]   = useState([])
  const [loading, setLoading]   = useState(true)
  const [showCreate, setCreate] = useState(false)
  const [running, setRunning]   = useState(null)
  const [toast, setToast]       = useState('')

  const showToast = (msg) => { setToast(msg); setTimeout(() => setToast(''), 3000) }

  const load = useCallback(async () => {
    try {
      const res = await scheduledReportsService.list()
      setReports(res?.data || res || [])
    } catch { setReports([]) }
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [load])

  const handleCreate = async (data) => {
    await scheduledReportsService.create(data)
    await load()
    showToast('Report scheduled.')
  }

  const handleToggle = async (report) => {
    await scheduledReportsService.update(report.id, { is_active: !report.is_active })
    await load()
  }

  const handleDelete = async (id) => {
    await scheduledReportsService.remove(id)
    await load()
  }

  const handleRunNow = async (id) => {
    setRunning(id)
    try {
      await scheduledReportsService.runNow(id)
      showToast('Report queued for immediate delivery.')
    } catch {
      showToast('Failed to trigger report.')
    } finally {
      setRunning(null)
    }
  }

  const active   = reports.filter(r => r.is_active)
  const inactive = reports.filter(r => !r.is_active)

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      {toast && (
        <div className="fixed top-4 right-4 z-50 flex items-center gap-2 px-4 py-2.5 bg-neutral-800 border border-white/10 rounded-xl text-sm text-white shadow-xl">
          <CheckCircle2 size={14} className="text-green-400" /> {toast}
        </div>
      )}

      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-white mb-1">Scheduled Reports</h1>
          <p className="text-sm text-neutral-400">
            Deliver board-level and compliance PDFs to recipients on a recurring schedule.
          </p>
        </div>
        <button
          onClick={() => setCreate(true)}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-white text-black text-sm font-medium hover:bg-neutral-200"
        >
          <Plus size={14} /> New Schedule
        </button>
      </header>

      {loading ? (
        <div className="flex items-center justify-center h-32">
          <Loader2 className="animate-spin text-neutral-500" size={24} />
        </div>
      ) : reports.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 text-center">
          <div className="w-12 h-12 rounded-xl bg-white/[0.04] flex items-center justify-center mb-4">
            <Calendar size={20} className="text-neutral-500" />
          </div>
          <div className="text-sm font-medium text-neutral-400 mb-1">No scheduled reports</div>
          <div className="text-xs text-neutral-600 mb-4">Create a schedule to automatically deliver PDFs to stakeholders.</div>
          <button onClick={() => setCreate(true)} className="flex items-center gap-2 px-4 py-2 rounded-lg bg-white text-black text-sm font-medium">
            <Plus size={14} /> Create first schedule
          </button>
        </div>
      ) : (
        <div className="space-y-6">
          {active.length > 0 && (
            <div>
              <h2 className="text-[11px] uppercase tracking-wider text-neutral-500 mb-3">Active ({active.length})</h2>
              <div className="space-y-3">
                {active.map(r => (
                  <ReportCard key={r.id} report={r} onToggle={handleToggle} onDelete={handleDelete} onRunNow={handleRunNow} running={running} />
                ))}
              </div>
            </div>
          )}
          {inactive.length > 0 && (
            <div>
              <h2 className="text-[11px] uppercase tracking-wider text-neutral-500 mb-3">Paused ({inactive.length})</h2>
              <div className="space-y-3">
                {inactive.map(r => (
                  <ReportCard key={r.id} report={r} onToggle={handleToggle} onDelete={handleDelete} onRunNow={handleRunNow} running={running} />
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      <div className="p-4 bg-white/[0.02] border border-[var(--border-subtle)] rounded-xl text-xs text-neutral-500 leading-relaxed">
        <div className="flex items-start gap-2">
          <FileText size={13} className="shrink-0 text-neutral-600 mt-0.5" />
          <div>
            <strong className="text-neutral-400">Delivery note:</strong> Reports are queued to Redis and picked up by the report worker. Configure{' '}
            <code className="text-neutral-400">SMTP_HOST</code>, <code className="text-neutral-400">SMTP_USER</code>, and{' '}
            <code className="text-neutral-400">SMTP_PASS</code> environment variables to enable email delivery.
            The "Run Now" button queues immediate generation regardless of schedule.
          </div>
        </div>
      </div>

      <CreateModal open={showCreate} onClose={() => setCreate(false)} onCreate={handleCreate} />
    </div>
  )
}
