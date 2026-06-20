import React, { useCallback, useEffect, useState } from 'react'
import {
  CheckCircle2, RefreshCw, ExternalLink, Trash2, ShieldCheck, AlertCircle,
} from 'lucide-react'
import Button from '../Common/Button'
import { integrationsService } from '../../services/api'

// Sprint EI-2 (2026-06-20) — Jira Cloud ITSM connection.
//
// One form, four fields the operator fills in once. The API token field is
// write-only — once stored, the GET surface returns has_api_token: true but
// never the value. Re-saving requires re-entering the token.
//
// After save, the Test Connection button creates a real issue in the chosen
// project with summary "Aegis connection test — safe to close" so the
// operator can verify the wiring without opening a fake incident.
export default function JiraIntegrationTab() {
  const [config, setConfig] = useState(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')

  const [baseUrl, setBaseUrl] = useState('')
  const [projectKey, setProjectKey] = useState('')
  const [accountEmail, setAccountEmail] = useState('')
  const [apiToken, setApiToken] = useState('')
  const [issueType, setIssueType] = useState('Bug')
  const [priority, setPriority] = useState('')
  const [enabled, setEnabled] = useState(true)
  const [autoCreate, setAutoCreate] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const resp = await integrationsService.getJira()
      const data = resp?.data || null
      setConfig(data)
      if (data) {
        setBaseUrl(data.base_url || '')
        setProjectKey(data.project_key || '')
        setAccountEmail(data.account_email || '')
        setIssueType(data.default_issue_type || 'Bug')
        setPriority(data.default_priority || '')
        setEnabled(!!data.enabled)
        setAutoCreate(!!data.auto_create_on_incident)
        // api_token never returned — leave blank; re-entering is required to re-save
        setApiToken('')
      }
      setError('')
    } catch (e) {
      setError(e?.message || 'Failed to load Jira config')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const save = async () => {
    setBusy(true); setError(''); setSuccess('')
    try {
      if (!apiToken) {
        setError('API token is required when saving (we do not store the existing one in the browser).')
        setBusy(false); return
      }
      await integrationsService.setJira({
        base_url: baseUrl.trim(),
        project_key: projectKey.trim(),
        account_email: accountEmail.trim(),
        api_token: apiToken,
        default_issue_type: issueType || 'Bug',
        default_priority: priority || null,
        enabled,
        auto_create_on_incident: autoCreate,
      })
      setSuccess('Jira connection saved.')
      setApiToken('')
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
      const resp = await integrationsService.testJira()
      const data = resp?.data || resp
      if (data?.status === 'created') {
        setSuccess(`Test issue created: ${data.issue_key}. Check Jira to confirm.`)
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
    if (!confirm('Remove Jira integration? Aegis will stop creating tickets for new incidents.')) return
    setBusy(true); setError(''); setSuccess('')
    try {
      await integrationsService.deleteJira()
      setSuccess('Jira integration removed.')
      setConfig(null)
      setBaseUrl(''); setProjectKey(''); setAccountEmail('')
      setApiToken(''); setIssueType('Bug'); setPriority('')
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
          <ShieldCheck size={14} className="text-blue-400" /> Jira Cloud
        </h2>
        <p className="text-xs text-neutral-500 mt-1">
          Every new Aegis incident automatically opens a ticket in this project.
          The ticket carries the severity, risk score, agent ID, and findings — your
          on-call engineer triages from Jira; Aegis tracks resolution status back.
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
        <Field label="Atlassian base URL" hint="e.g. https://acme.atlassian.net"
          value={baseUrl} onChange={setBaseUrl} placeholder="https://your-org.atlassian.net" />
        <Field label="Project key" hint="From your Jira project URL — uppercase letters only."
          value={projectKey} onChange={(v) => setProjectKey(v.toUpperCase())}
          placeholder="SEC" mono />
        <Field label="Service account email"
          hint="The Atlassian user whose API token will be used to create issues."
          value={accountEmail} onChange={setAccountEmail} placeholder="aegis-bot@acme.com" />
        <Field label="API token"
          hint={config?.has_api_token
            ? 'A token is already saved. Re-enter to update; leave blank to keep using the stored one (you cannot leave blank when first saving).'
            : 'Generate at id.atlassian.com → Security → API tokens.'}
          value={apiToken} onChange={setApiToken} placeholder="ATATT3xFfGF0…" mono password />

        <div className="grid grid-cols-2 gap-3">
          <Field label="Default issue type" value={issueType} onChange={setIssueType}
            placeholder="Bug" />
          <Field label="Default priority (optional)" value={priority} onChange={setPriority}
            placeholder="High" />
        </div>

        <label className="flex items-center gap-2 text-xs text-neutral-300">
          <input type="checkbox" checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)} />
          Integration enabled
        </label>
        <label className="flex items-center gap-2 text-xs text-neutral-300">
          <input type="checkbox" checked={autoCreate}
            onChange={(e) => setAutoCreate(e.target.checked)} />
          Auto-create a Jira ticket for every new Aegis incident
        </label>
      </div>

      <div className="flex flex-wrap gap-2 pt-2 border-t border-white/[0.06]">
        <Button onClick={save} disabled={busy} variant="primary">
          {busy ? <RefreshCw size={12} className="animate-spin" /> : null}
          Save
        </Button>
        <Button onClick={test} disabled={busy || !config?.has_api_token}
          variant="secondary"
          title={!config?.has_api_token ? 'Save the config first' : 'Create one test issue'}>
          <ExternalLink size={12} /> Test connection
        </Button>
        {config && (
          <Button onClick={remove} disabled={busy} variant="danger">
            <Trash2 size={12} /> Remove
          </Button>
        )}
      </div>
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
