import React, { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Database, Server, Play,
  Save, Loader2, AlertCircle, Upload,
  RefreshCw, AlertTriangle,
} from 'lucide-react'
import { z } from 'zod'
import { siemService } from '../services/api'
import { SecretInput, StatusBadge, IntegrationCard } from '../components/Common/ConnectorPrimitives'
import { useRole } from '../hooks/useRole'
import useUnsavedChanges from '../hooks/useUnsavedChanges'

const DATADOG_SITES = ['datadoghq.com', 'us3.datadoghq.com', 'us5.datadoghq.com', 'datadoghq.eu', 'ddog-gov.com']

const INITIAL_CFG = {
  splunk_url: '', splunk_token: '',
  datadog_key: '', datadog_site: 'datadoghq.com',
}

const isMasked = (v) => typeof v === 'string' && v.startsWith('***')

const optionalUrl = z.union([
  z.literal(''),
  z.string().trim().url('Must be a valid URL'),
])

const siemSchema = z.object({
  splunk_url: optionalUrl,
  splunk_token: z.string().trim(),
  datadog_key: z.union([
    z.literal(''),
    z.string().trim().min(32, 'API key must be at least 32 characters'),
  ]),
  datadog_site: z.enum(DATADOG_SITES, { message: 'Pick a valid Datadog site' }),
}).superRefine((val, ctx) => {
  if (val.splunk_url && !val.splunk_token) {
    ctx.addIssue({ code: 'custom', path: ['splunk_token'], message: 'Required when Splunk URL is set' })
  }
  if (val.splunk_token && !val.splunk_url) {
    ctx.addIssue({ code: 'custom', path: ['splunk_url'], message: 'Required when Splunk token is set' })
  }
})

export default function SiemSettings() {
  const { isOwner, isAdmin } = useRole()
  const canMutate = isOwner || isAdmin
  const [cfg, setCfg] = useState(INITIAL_CFG)
  const [initialCfg, setInitialCfg] = useState(INITIAL_CFG)
  const [touched, setTouched] = useState({})
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
        const merged = {
          splunk_url: c.splunk_url ?? '',
          splunk_token: c.splunk_token ?? '',
          datadog_key: c.datadog_key ?? '',
          datadog_site: c.datadog_site ?? 'datadoghq.com',
        }
        setCfg(merged)
        setInitialCfg(merged)
        setTouched({})
      })
      .catch(() => setLoadError(true))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { loadConfig() }, [loadConfig])

  const cfgForValidation = useMemo(() => ({
    splunk_url: isMasked(cfg.splunk_url) ? '' : cfg.splunk_url,
    splunk_token: isMasked(cfg.splunk_token) ? 'masked-token' : cfg.splunk_token,
    datadog_key: isMasked(cfg.datadog_key) ? 'x'.repeat(32) : cfg.datadog_key,
    datadog_site: cfg.datadog_site,
  }), [cfg])

  const parsed = siemSchema.safeParse(cfgForValidation)
  const fieldErrors = parsed.success ? {} : parsed.error.flatten().fieldErrors
  const isValid = parsed.success
  const showError = (key) => touched[key] && fieldErrors[key]?.[0]
  const markTouched = (key) => setTouched(t => t[key] ? t : { ...t, [key]: true })

  const dirty = useMemo(
    () => JSON.stringify(cfg) !== JSON.stringify(initialCfg),
    [cfg, initialCfg]
  )
  useUnsavedChanges(dirty && !saving)

  const save = async () => {
    setTouched({ splunk_url: true, splunk_token: true, datadog_key: true, datadog_site: true })
    if (!isValid) return
    setSaving(true)
    setError('')
    try {
      await siemService.saveConfig(cfg)
      setInitialCfg(cfg)
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
      <div className="flex items-center justify-center h-64">
        <Loader2 className="animate-spin text-neutral-500" size={24} />
      </div>
    )
  }

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-white mb-1">SIEM Integration</h1>
          <p className="text-sm text-neutral-400">
            Push audit events to your SIEM in real time — Splunk HEC or Datadog Logs.
          </p>
        </div>
        {canMutate && (
          <button
            onClick={save}
            disabled={saving || !isValid || !dirty}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-white text-black text-sm font-medium hover:bg-neutral-200 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
            {saved ? 'Saved!' : 'Save'}
          </button>
        )}
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

      <IntegrationCard icon={Server} title="Splunk" description="HTTP Event Collector (HEC)" color="bg-[#65a637]/80">
        <div className="space-y-3">
          <div>
            <label htmlFor="splunk_url" className="block text-xs text-neutral-400 mb-1">HEC URL</label>
            <input
              id="splunk_url"
              type="url"
              value={cfg.splunk_url}
              onChange={e => setCfg(c => ({ ...c, splunk_url: e.target.value }))}
              onBlur={() => markTouched('splunk_url')}
              placeholder="https://splunk.company.com:8088/services/collector/event"
              aria-invalid={!!showError('splunk_url')}
              aria-describedby={showError('splunk_url') ? 'splunk_url_err' : undefined}
              className={`w-full bg-white/[0.04] border rounded-lg px-3 py-2 text-sm text-white placeholder-neutral-600 focus:outline-none ${showError('splunk_url') ? 'border-red-500/50 focus:border-red-500/70' : 'border-[var(--border-subtle)] focus:border-white/20'}`}
            />
            {showError('splunk_url') && (
              <p id="splunk_url_err" className="mt-1 flex items-center gap-1 text-[11px] text-red-400">
                <AlertCircle size={11} /> {showError('splunk_url')}
              </p>
            )}
          </div>
          <div>
            <SecretInput
              id="splunk_token"
              label="HEC Token"
              placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
              value={cfg.splunk_token}
              onChange={v => {
                setCfg(c => ({ ...c, splunk_token: v }))
                markTouched('splunk_token')
              }}
            />
            {showError('splunk_token') && (
              <p className="mt-1 flex items-center gap-1 text-[11px] text-red-400">
                <AlertCircle size={11} /> {showError('splunk_token')}
              </p>
            )}
          </div>
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
          <div>
            <SecretInput
              id="dd_key"
              label="API Key"
              placeholder="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
              value={cfg.datadog_key}
              onChange={v => {
                setCfg(c => ({ ...c, datadog_key: v }))
                markTouched('datadog_key')
              }}
            />
            {showError('datadog_key') && (
              <p className="mt-1 flex items-center gap-1 text-[11px] text-red-400">
                <AlertCircle size={11} /> {showError('datadog_key')}
              </p>
            )}
          </div>
          <div>
            <label htmlFor="dd_site" className="block text-xs text-neutral-400 mb-1">Datadog Site</label>
            <select
              id="dd_site"
              value={cfg.datadog_site}
              onChange={e => setCfg(c => ({ ...c, datadog_site: e.target.value }))}
              onBlur={() => markTouched('datadog_site')}
              aria-invalid={!!showError('datadog_site')}
              className={`w-full bg-white/[0.04] border rounded-lg px-3 py-2 text-sm text-white focus:outline-none ${showError('datadog_site') ? 'border-red-500/50 focus:border-red-500/70' : 'border-[var(--border-subtle)] focus:border-white/20'}`}
            >
              {DATADOG_SITES.map(s => (
                <option key={s} value={s} className="bg-neutral-900">{s}</option>
              ))}
            </select>
            {showError('datadog_site') && (
              <p className="mt-1 flex items-center gap-1 text-[11px] text-red-400">
                <AlertCircle size={11} /> {showError('datadog_site')}
              </p>
            )}
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
            <select
              value={pushLimit}
              onChange={e => setPushLimit(Number(e.target.value))}
              className="bg-white/[0.04] border border-[var(--border-subtle)] rounded-lg px-2 py-1 text-sm text-white focus:outline-none"
            >
              {[50, 100, 250, 500, 1000].map(n => (
                <option key={n} value={n} className="bg-neutral-900">{n}</option>
              ))}
            </select>
          </div>
          {canMutate && (
            <button
              onClick={pushNow}
              disabled={pushing}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white/10 text-xs text-white hover:bg-white/20 disabled:opacity-40"
            >
              {pushing ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
              Push now
            </button>
          )}
          {pushResult && (
            <span className={`text-xs ${pushResult.status === 'error' ? 'text-red-400' : 'text-green-400'}`}>
              {pushResult.status === 'error' ? pushResult.reason : `Pushed ${pushResult.sent ?? 0} events`}
            </span>
          )}
        </div>
      </div>

      <div className="p-4 bg-white/[0.02] border border-[var(--border-subtle)] rounded-xl text-xs text-neutral-500 leading-relaxed">
        <strong className="text-neutral-300">Continuous streaming:</strong> The audit export endpoint at{' '}
        <code className="text-neutral-400">GET /audit/export</code> streams the full chain as NDJSON for direct Splunk
        Heavy Forwarder or Datadog Agent ingestion. See{' '}
        <code className="text-neutral-400">docs/integrations/siem.md</code> for pipeline config.
      </div>
    </div>
  )
}
