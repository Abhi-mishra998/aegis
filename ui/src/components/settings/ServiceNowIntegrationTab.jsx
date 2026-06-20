import React, { useCallback, useEffect, useState } from 'react'
import {
  CheckCircle2, RefreshCw, ExternalLink, Trash2, ShieldCheck, AlertCircle,
} from 'lucide-react'
import Button from '../Common/Button'
import { integrationsService } from '../../services/api'
import InboundWebhookSection from './_InboundWebhookSection'

// Sprint EI-6 (2026-06-20) — ServiceNow Table API connection.
//
// Sister tab to JiraIntegrationTab. Same shape: one form, Save / Test /
// Remove. The password field is write-only — once stored, the GET surface
// returns has_password: true but never the value. Re-saving requires
// re-entering the password.
//
// Default urgency/impact are 1=High, 2=Medium, 3=Low (SNOW convention).
// The auto-create-on-incident path in incident_watcher.py maps Aegis
// severity (CRITICAL/HIGH/MEDIUM/LOW) to those numbers independently of
// the defaults set here.
export default function ServiceNowIntegrationTab() {
  const [config, setConfig] = useState(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')

  const [instanceUrl, setInstanceUrl] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [urgency, setUrgency] = useState(2)
  const [impact, setImpact] = useState(2)
  const [category, setCategory] = useState('')
  const [assignmentGroup, setAssignmentGroup] = useState('')
  const [enabled, setEnabled] = useState(true)
  const [autoCreate, setAutoCreate] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const resp = await integrationsService.getServiceNow()
      const data = resp?.data || null
      setConfig(data)
      if (data) {
        setInstanceUrl(data.instance_url || '')
        setUsername(data.username || '')
        setUrgency(data.default_urgency || 2)
        setImpact(data.default_impact || 2)
        setCategory(data.default_category || '')
        setAssignmentGroup(data.default_assignment_group || '')
        setEnabled(!!data.enabled)
        setAutoCreate(!!data.auto_create_on_incident)
        // password never returned — leave blank; re-entering required to re-save
        setPassword('')
      }
      setError('')
    } catch (e) {
      setError(e?.message || 'Failed to load ServiceNow config')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const save = async () => {
    setBusy(true); setError(''); setSuccess('')
    try {
      if (!password) {
        setError('Password is required when saving (we do not store the existing one in the browser).')
        setBusy(false); return
      }
      await integrationsService.setServiceNow({
        instance_url: instanceUrl.trim(),
        username: username.trim(),
        password,
        default_urgency: Number(urgency),
        default_impact: Number(impact),
        default_category: category || null,
        default_assignment_group: assignmentGroup || null,
        enabled,
        auto_create_on_incident: autoCreate,
      })
      setSuccess('ServiceNow connection saved.')
      setPassword('')
      await load()
    } catch (e) {
      setError(e?.message || 'Save failed')
    } finally {
      setBusy(false)
    }
  }

  const test = async () => {
    setBusy(true); setError(''); setSuccess('')
    try {
      const resp = await integrationsService.testServiceNow()
      const data = resp?.data || resp
      if (data?.status === 'created') {
        setSuccess(`Test incident created: ${data.number}. Check ServiceNow to confirm.`)
      } else if (data?.status === 'skipped') {
        setError(`Test skipped: ${data.reason}`)
      } else {
        setError(`Test failed: ${data?.reason || JSON.stringify(data)}`)
      }
    } catch (e) {
      setError(e?.message || 'Test failed')
    } finally {
      setBusy(false)
    }
  }

  const remove = async () => {
    if (!confirm('Remove ServiceNow integration? Aegis will stop opening incidents in SNOW for new events.')) return
    setBusy(true); setError(''); setSuccess('')
    try {
      await integrationsService.deleteServiceNow()
      setSuccess('ServiceNow integration removed.')
      setConfig(null)
      setInstanceUrl(''); setUsername(''); setPassword('')
      setUrgency(2); setImpact(2); setCategory(''); setAssignmentGroup('')
      setEnabled(true); setAutoCreate(true)
    } catch (e) {
      setError(e?.message || 'Delete failed')
    } finally {
      setBusy(false)
    }
  }

  if (loading) {
    return <div className="text-sm text-neutral-500">Loading…</div>
  }

  return (
    <div className="space-y-6 max-w-2xl">
      <header>
        <h2 className="text-base font-semibold text-white flex items-center gap-2">
          <ShieldCheck size={14} className="text-blue-400" /> ServiceNow
        </h2>
        <p className="text-xs text-neutral-500 mt-1">
          Every new Aegis incident automatically opens a ServiceNow Incident in
          your instance using the dedicated service account. Aegis maps
          severity to SNOW urgency/impact (CRITICAL → 1/1, HIGH → 1/2,
          MEDIUM → 2/2, LOW → 3/3) and uses the Aegis incident_id as
          ServiceNow's <code className="text-neutral-400">correlation_id</code> so
          retries never open duplicate tickets.
        </p>
      </header>

      {error && (
        <div className="flex items-start gap-2 text-xs text-red-300 bg-red-500/[0.06] border border-red-500/20 rounded-lg p-3">
          <AlertCircle size={12} className="shrink-0 mt-0.5" />
          <span>{error}</span>
        </div>
      )}
      {success && (
        <div className="flex items-start gap-2 text-xs text-green-300 bg-green-500/[0.06] border border-green-500/20 rounded-lg p-3">
          <CheckCircle2 size={12} className="shrink-0 mt-0.5" />
          <span>{success}</span>
        </div>
      )}

      <div className="space-y-3">
        <Field label="Instance URL" hint="e.g. https://acme.service-now.com"
          value={instanceUrl} onChange={setInstanceUrl}
          placeholder="https://your-org.service-now.com" />
        <Field label="Service account username"
          hint="The SNOW user whose password Aegis will use to open incidents."
          value={username} onChange={setUsername} placeholder="aegis_bot" />
        <Field label="Password"
          hint={config?.has_password
            ? 'A password is already saved. Re-enter to update; leave blank to keep using the stored one (you cannot leave blank when first saving).'
            : 'The SNOW user password. Generate a strong one and rotate per your SNOW policy.'}
          value={password} onChange={setPassword} placeholder="••••••••" mono password />

        <div className="grid grid-cols-2 gap-3">
          <Selectish label="Default urgency"
            value={urgency} onChange={(v) => setUrgency(Number(v))}>
            <option value={1}>1 — High</option>
            <option value={2}>2 — Medium</option>
            <option value={3}>3 — Low</option>
          </Selectish>
          <Selectish label="Default impact"
            value={impact} onChange={(v) => setImpact(Number(v))}>
            <option value={1}>1 — High</option>
            <option value={2}>2 — Medium</option>
            <option value={3}>3 — Low</option>
          </Selectish>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Default category (optional)"
            value={category} onChange={setCategory} placeholder="software" />
          <Field label="Default assignment group sys_id (optional)"
            value={assignmentGroup} onChange={setAssignmentGroup}
            placeholder="287ebd7da9fe1981000…" mono />
        </div>

        <label className="flex items-center gap-2 text-xs text-neutral-300">
          <input type="checkbox" checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)} />
          Integration enabled
        </label>
        <label className="flex items-center gap-2 text-xs text-neutral-300">
          <input type="checkbox" checked={autoCreate}
            onChange={(e) => setAutoCreate(e.target.checked)} />
          Auto-create a ServiceNow incident for every new Aegis incident
        </label>
      </div>

      <div className="flex flex-wrap gap-2 pt-2 border-t border-white/[0.06]">
        <Button onClick={save} disabled={busy} variant="primary">
          {busy ? <RefreshCw size={12} className="animate-spin" /> : null}
          Save
        </Button>
        <Button onClick={test} disabled={busy || !config?.has_password}
          variant="secondary"
          title={!config?.has_password ? 'Save the config first' : 'Create one test incident'}>
          <ExternalLink size={12} /> Test connection
        </Button>
        {config && (
          <Button onClick={remove} disabled={busy} variant="danger">
            <Trash2 size={12} /> Remove
          </Button>
        )}
      </div>

      {config && (
        <InboundWebhookSection
          vendor="ServiceNow"
          docHref="/docs/security/servicenow-itsm-setup.md"
          hasSecret={!!config.has_webhook_secret}
          onRotate={integrationsService.rotateServiceNowWebhookSecret}
          disabled={busy}
        />
      )}
    </div>
  )
}


function Field({ label, hint, value, onChange, placeholder, mono, password }) {
  return (
    <div>
      <label className="block text-[11px] uppercase tracking-wide text-neutral-500 mb-1">
        {label}
      </label>
      <input
        type={password ? 'password' : 'text'}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className={`w-full px-3 py-2 text-sm rounded-lg bg-white/[0.04] border border-white/[0.08]
          text-white placeholder-neutral-600 focus:outline-none focus:border-white/[0.2]
          ${mono ? 'font-mono' : ''}`}
      />
      {hint && <p className="text-[10px] text-neutral-500 mt-1 leading-relaxed">{hint}</p>}
    </div>
  )
}


function Selectish({ label, value, onChange, children }) {
  return (
    <div>
      <label className="block text-[11px] uppercase tracking-wide text-neutral-500 mb-1">
        {label}
      </label>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full px-3 py-2 text-sm rounded-lg bg-white/[0.04] border border-white/[0.08]
          text-white focus:outline-none focus:border-white/[0.2]"
      >
        {children}
      </select>
    </div>
  )
}
