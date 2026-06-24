import React, { useCallback, useEffect, useState } from 'react'
import {
  Database, Server, Play,
  Save, Loader2, AlertCircle, Upload,
  RefreshCw, AlertTriangle,
} from 'lucide-react'
import { siemService } from '../services/api'
import { SecretInput, StatusBadge, IntegrationCard } from '../components/Common/ConnectorPrimitives'

export default function SiemSettings() {
  const [cfg, setCfg] = useState({
    splunk_url: '', splunk_token: '',
    datadog_key: '', datadog_site: 'datadoghq.com',
  })
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [loading, setLoading] = useState(true)
  const [testing, setTesting] = useState({})
  const [results, setResults] = useState({})
  const [pushLimit, setPushLimit] = useState(100)
  const [pushing, setPushing] = useState(false)
  const [pushResult, setPushResult] = useState(null)
  const [error, setError] = useState('')
  const [loadError, setLoadError] = useState(false)

  const loadConfig = useCallback(() => {
    setLoading(true)
    setLoadError(false)
    siemService.getConfig()
      .then(d => {
        const c = d?.data || d || {}
        setCfg(prev => ({
          ...prev,
          splunk_url: c.splunk_url ?? prev.splunk_url,
          splunk_token: c.splunk_token ?? prev.splunk_token,
          datadog_key: c.datadog_key ?? prev.datadog_key,
          datadog_site: c.datadog_site ?? prev.datadog_site,
        }))
      })
      .catch(() => setLoadError(true))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { loadConfig() }, [loadConfig])

  const save = async () => {
    setSaving(true)
    setError('')
    try {
      // Strip masked values (***...) so the round-trip from GET → POST
      // never overwrites the real stored secret with the placeholder.
      const payload = Object.fromEntries(
        Object.entries(cfg).filter(([, v]) => !String(v).startsWith('***'))
      )
      await siemService.saveConfig(payload)
      setSaved(true)
      setTimeout(() => setSaved(false), 3000)
    } catch {
      setError('Failed to save configuration.')
    } finally {
      setSaving(false)
    }
  }

  const test = async (target) => {
    setTesting(v => ({ ...v, [target]: true }))
    setResults(v => ({ ...v, [target]: null }))
    try {
      const res = target === 'splunk'
        ? await siemService.testSplunk({ splunk_url: cfg.splunk_url, splunk_token: cfg.splunk_token })
        : await siemService.testDatadog({ datadog_key: cfg.datadog_key, datadog_site: cfg.datadog_site })
      setResults(v => ({ ...v, [target]: res?.data || res }))
    } catch (err) {
      setResults(v => ({ ...v, [target]: { status: 'error', reason: err.message } }))
    } finally {
      setTesting(v => ({ ...v, [target]: false }))
    }
  }

  const pushNow = async () => {
    setPushing(true)
    setPushResult(null)
    try {
      const res = await siemService.push({ limit: pushLimit })
      setPushResult(res?.data || res)
    } catch (err) {
      setPushResult({ status: 'error', reason: err.message })
    } finally {
      setPushing(false)
    }
  }

  if (loading) {
    return (
      <div className="max-w-3xl mx-auto space-y-6 animate-pulse" aria-label="Loading SIEM settings">
        <div className="h-7 w-56 bg-white/[0.05] rounded" />
        <div className="h-3 w-72 bg-white/[0.03] rounded" />
        {[0,1,2].map(i => (
          <div key={i} className="h-40 bg-white/[0.03] border border-white/[0.04] rounded-xl" />
        ))}
      </div>
    )
  }

  // Unit 9 (2026-06-23): SIEM is not connected if neither Splunk URL+token
  // nor Datadog API key is set. Surface a 3-tile picker that anchors to the
  // form below so the operator knows where to start.
  const splunkConfigured  = Boolean(cfg.splunk_url && cfg.splunk_token)
  const datadogConfigured = Boolean(cfg.datadog_key)
  const noSiem = !splunkConfigured && !datadogConfigured

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-white mb-1">SIEM Integration</h1>
          <p className="text-sm text-neutral-400">
            Push audit events to your SIEM in real time — Splunk HEC or Datadog Logs.
          </p>
        </div>
        <button
          onClick={save}
          disabled={saving}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-white text-black text-sm font-medium hover:bg-neutral-200 disabled:opacity-50"
        >
          {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
          {saved ? 'Saved!' : 'Save'}
        </button>
      </header>

      {error && (
        <div className="flex items-center gap-2 p-3 bg-red-500/10 border border-red-500/20 rounded-lg text-sm text-red-400">
          <AlertCircle size={14} /> {error}
        </div>
      )}

      {loadError && (
        <div className="flex items-center justify-between gap-3 p-3 bg-amber-500/10 border border-amber-500/20 rounded-lg text-sm text-amber-400">
          <div className="flex items-center gap-2">
            <AlertTriangle size={14} />
            <span>Failed to load configuration. Form fields may be stale.</span>
          </div>
          <button
            type="button"
            onClick={loadConfig}
            className="flex items-center gap-1.5 px-3 py-1 rounded-md border border-amber-500/30 text-xs text-amber-300 hover:bg-amber-500/10"
          >
            <RefreshCw size={11} /> Retry
          </button>
        </div>
      )}

      {/* Unit 9 — vendor tile picker. Visible when nothing is connected.
          Each tile is a deep link to its config row below. Elastic is a
          third tile pointing to the audit-export NDJSON section since the
          codebase currently ships Splunk + Datadog connectors and Elastic
          ingests via the audit export feed. */}
      {noSiem && !loadError && (
        <div className="p-4 bg-white/[0.02] border border-white/[0.06] rounded-xl">
          <div className="flex items-start gap-3 mb-3">
            <Database size={18} className="text-neutral-500 shrink-0 mt-0.5" />
            <div>
              <div className="text-sm font-medium text-neutral-200">Not connected — pick a SIEM vendor</div>
              <p className="text-xs text-neutral-500 mt-1">
                Audit events stream in real time once a target is configured. Pick a tile to jump to its form.
              </p>
            </div>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <a
              href="#splunk_url"
              className="p-3 rounded-lg border border-white/10 hover:border-white/25 transition-colors bg-white/[0.02]"
            >
              <div className="flex items-center gap-2 mb-1">
                <span className="w-2 h-2 rounded-full bg-[#65a637]" />
                <span className="text-sm font-medium text-neutral-200">Splunk</span>
              </div>
              <p className="text-[11px] text-neutral-500">HTTP Event Collector (HEC)</p>
            </a>
            <a
              href="#dd_key"
              className="p-3 rounded-lg border border-white/10 hover:border-white/25 transition-colors bg-white/[0.02]"
            >
              <div className="flex items-center gap-2 mb-1">
                <span className="w-2 h-2 rounded-full bg-[#632ca6]" />
                <span className="text-sm font-medium text-neutral-200">Datadog</span>
              </div>
              <p className="text-[11px] text-neutral-500">Logs Intake API v2</p>
            </a>
            <a
              href="#audit-stream-doc"
              className="p-3 rounded-lg border border-white/10 hover:border-white/25 transition-colors bg-white/[0.02]"
            >
              <div className="flex items-center gap-2 mb-1">
                <span className="w-2 h-2 rounded-full bg-[#00bfb3]" />
                <span className="text-sm font-medium text-neutral-200">Elastic (ECS)</span>
              </div>
              <p className="text-[11px] text-neutral-500">via /audit/export NDJSON</p>
            </a>
          </div>
        </div>
      )}

      <IntegrationCard icon={Server} title="Splunk" description="HTTP Event Collector (HEC)" color="bg-[#65a637]/80">
        <div className="space-y-3">
          <div>
            <label htmlFor="splunk_url" className="block text-xs text-neutral-400 mb-1">HEC URL</label>
            <input
              id="splunk_url"
              type="url"
              value={cfg.splunk_url}
              onChange={e => setCfg(c => ({ ...c, splunk_url: e.target.value }))}
              placeholder="https://splunk.company.com:8088/services/collector/event"
              className="w-full bg-white/[0.04] border border-[var(--border-subtle)] rounded-lg px-3 py-2 text-sm text-white placeholder-neutral-600 focus:outline-none focus:border-white/20"
            />
          </div>
          <SecretInput
            id="splunk_token"
            label="HEC Token"
            placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
            value={cfg.splunk_token}
            onChange={v => setCfg(c => ({ ...c, splunk_token: v }))}
          />
          <div className="flex items-center gap-3">
            <button
              onClick={() => test('splunk')}
              disabled={!cfg.splunk_url || !cfg.splunk_token || testing.splunk}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[var(--border-subtle)] text-xs text-neutral-300 hover:border-white/20 disabled:opacity-40"
            >
              {testing.splunk ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
              Send test event
            </button>
            <StatusBadge result={results.splunk} />
          </div>
          <p className="text-xs text-neutral-600">
            Events are sent with <code className="text-neutral-400">sourcetype: acp:audit</code>.
            Enable the HEC at Settings → Data Inputs → HTTP Event Collector in Splunk.
          </p>
        </div>
      </IntegrationCard>

      <IntegrationCard icon={Database} title="Datadog" description="Logs Intake API v2" color="bg-[#632ca6]/80">
        <div className="space-y-3">
          <SecretInput
            id="dd_key"
            label="API Key"
            placeholder="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
            value={cfg.datadog_key}
            onChange={v => setCfg(c => ({ ...c, datadog_key: v }))}
          />
          <div>
            <label htmlFor="dd_site" className="block text-xs text-neutral-400 mb-1">Datadog Site</label>
            <select
              id="dd_site"
              value={cfg.datadog_site}
              onChange={e => setCfg(c => ({ ...c, datadog_site: e.target.value }))}
              className="w-full bg-white/[0.04] border border-[var(--border-subtle)] rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-white/20"
            >
              {['datadoghq.com', 'us3.datadoghq.com', 'us5.datadoghq.com', 'datadoghq.eu', 'ddog-gov.com'].map(s => (
                <option key={s} value={s} className="bg-neutral-900">{s}</option>
              ))}
            </select>
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={() => test('datadog')}
              disabled={!cfg.datadog_key || testing.datadog}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[var(--border-subtle)] text-xs text-neutral-300 hover:border-white/20 disabled:opacity-40"
            >
              {testing.datadog ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
              Send test log
            </button>
            <StatusBadge result={results.datadog} />
          </div>
          <p className="text-xs text-neutral-600">
            Logs are tagged with <code className="text-neutral-400">ddsource:acp</code> and <code className="text-neutral-400">service:acp-audit</code> for easy filtering.
          </p>
        </div>
      </IntegrationCard>

      {/* Manual push */}
      <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl p-5">
        <h3 className="text-sm font-medium text-white mb-1 flex items-center gap-2">
          <Upload size={14} className="text-neutral-500" />
          Manual Push
        </h3>
        <p className="text-xs text-neutral-500 mb-4">
          Push the most recent audit events to all configured SIEM targets immediately.
          Use this for backfill or to verify connectivity.
        </p>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2">
            <label className="text-xs text-neutral-400">Events:</label>
            <select name="select"
              value={pushLimit}
              onChange={e => setPushLimit(Number(e.target.value))}
              className="bg-white/[0.04] border border-[var(--border-subtle)] rounded-lg px-2 py-1 text-sm text-white focus:outline-none"
            >
              {[50, 100, 250, 500, 1000].map(n => (
                <option key={n} value={n} className="bg-neutral-900">{n}</option>
              ))}
            </select>
          </div>
          <button
            onClick={pushNow}
            disabled={pushing}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white/10 text-xs text-white hover:bg-white/20 disabled:opacity-40"
          >
            {pushing ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
            Push now
          </button>
          {pushResult && (
            <span className={`text-xs ${pushResult.status === 'error' ? 'text-red-400' : 'text-green-400'}`}>
              {pushResult.status === 'error' ? pushResult.reason : `Pushed ${pushResult.sent ?? 0} events`}
            </span>
          )}
        </div>
      </div>

      <div id="audit-stream-doc" className="p-4 bg-white/[0.02] border border-[var(--border-subtle)] rounded-xl text-xs text-neutral-500 leading-relaxed">
        <strong className="text-neutral-300">Continuous streaming:</strong> The audit export endpoint at{' '}
        <code className="text-neutral-400">GET /audit/export</code> streams the full chain as NDJSON for direct Splunk
        Heavy Forwarder, Datadog Agent, or Elastic Filebeat ingestion. See{' '}
        <code className="text-neutral-400">docs/integrations/siem.md</code> for pipeline config.
      </div>
    </div>
  )
}
