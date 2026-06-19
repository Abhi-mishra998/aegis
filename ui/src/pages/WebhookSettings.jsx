import React, { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Webhook, Slack, Bell, Globe,
  Save, Play, Loader2, AlertCircle,
  AlertTriangle, RefreshCw,
  Link2, Unlink, Check, ArrowRight,
} from 'lucide-react'
import { z } from 'zod'
import { webhookService, slackOAuthService } from '../services/api'
import { SecretInput, StatusBadge, IntegrationCard } from '../components/Common/ConnectorPrimitives'
import { useRole } from '../hooks/useRole'
import useUnsavedChanges from '../hooks/useUnsavedChanges'

const INITIAL_CFG = { slack_url: '', pagerduty_key: '', generic_url: '' }

const isMasked = (v) => typeof v === 'string' && v.startsWith('***')

const optionalUrl = z.union([
  z.literal(''),
  z.string().trim().url('Must be a valid URL'),
])

const webhookSchema = z.object({
  slack_url: z.union([
    z.literal(''),
    z.string()
      .trim()
      .url('Must be a valid URL')
      .refine(v => v.startsWith('https://hooks.slack.com/'), 'Must be a hooks.slack.com URL'),
  ]),
  pagerduty_key: z.union([
    z.literal(''),
    z.string().trim().min(32, 'Routing key must be at least 32 characters'),
  ]),
  generic_url: optionalUrl,
})

export default function WebhookSettings() {
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
  const [error, setError] = useState('')
  const [loadError, setLoadError] = useState(false)

  // Sprint S2 — Slack OAuth status
  const [slackStatus, setSlackStatus] = useState({ connected: false, workspace_id: '', channel_id: '' })
  const [slackLoading, setSlackLoading] = useState(true)

  const loadSlackStatus = useCallback(() => {
    setSlackLoading(true)
    slackOAuthService.status()
      .then((d) => setSlackStatus(d?.data || d || { connected: false }))
      .catch(() => setSlackStatus({ connected: false }))
      .finally(() => setSlackLoading(false))
  }, [])
  useEffect(() => { loadSlackStatus() }, [loadSlackStatus])

  const connectSlack = useCallback(() => {
    // Full-page redirect — Slack's OAuth screen does not embed in iframes.
    window.location.assign('/sso/slack/initiate?return_to=/webhook-settings')
  }, [])

  const disconnectSlack = useCallback(async () => {
    await slackOAuthService.disconnect()
    loadSlackStatus()
  }, [loadSlackStatus])

  const loadConfig = useCallback(() => {
    setLoading(true)
    setLoadError(false)
    webhookService.getConfig()
      .then(d => {
        const c = d?.data || d || {}
        const merged = {
          slack_url: c.slack_url ?? '',
          pagerduty_key: c.pagerduty_key ?? '',
          generic_url: c.generic_url ?? '',
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
    slack_url: isMasked(cfg.slack_url) ? '' : cfg.slack_url,
    pagerduty_key: isMasked(cfg.pagerduty_key) ? 'x'.repeat(32) : cfg.pagerduty_key,
    generic_url: isMasked(cfg.generic_url) ? '' : cfg.generic_url,
  }), [cfg])

  const parsed = webhookSchema.safeParse(cfgForValidation)
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
    setTouched({ slack_url: true, pagerduty_key: true, generic_url: true })
    if (!isValid) return
    setSaving(true)
    setError('')
    try {
      await webhookService.saveConfig(cfg)
      setInitialCfg(cfg)
      setSaved(true)
      setTimeout(() => setSaved(false), 3000)
    } catch {
      setError('Failed to save configuration.')
    } finally {
      setSaving(false)
    }
  }

  const test = async (channel) => {
    setTesting(v => ({ ...v, [channel]: true }))
    setResults(v => ({ ...v, [channel]: null }))
    try {
      let result
      if (channel === 'slack') {
        result = await webhookService.testSlack({ webhook_url: cfg.slack_url })
      } else if (channel === 'pagerduty') {
        result = await webhookService.testPagerduty({ routing_key: cfg.pagerduty_key })
      } else {
        result = await webhookService.testWebhook({ url: cfg.generic_url })
      }
      setResults(v => ({ ...v, [channel]: result?.data || result }))
    } catch (err) {
      setResults(v => ({ ...v, [channel]: { status: 'error', reason: err.message } }))
    } finally {
      setTesting(v => ({ ...v, [channel]: false }))
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
          <h1 className="text-2xl font-semibold text-white mb-1">Webhook Settings</h1>
          <p className="text-sm text-neutral-400">
            Configure alert delivery for playbook SEND_ALERT and WEBHOOK steps.
          </p>
        </div>
        {canMutate && (
          <button
            onClick={save}
            disabled={saving || !isValid || !dirty}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-white text-black text-sm font-medium hover:bg-neutral-200 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
            {saved ? 'Saved!' : 'Save Changes'}
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

      <IntegrationCard icon={Slack} title="Slack" description="One-click connect. Approvals land in your chosen channel.">
        <SlackConnectSection
          slackStatus={slackStatus}
          slackLoading={slackLoading}
          onRefreshStatus={loadSlackStatus}
          onConnect={connectSlack}
          onDisconnect={disconnectSlack}
          onTestMessage={() => test('slack')}
          testing={testing.slack}
          testResult={results.slack}
          showError={showError}
        />
      </IntegrationCard>

      <IntegrationCard icon={Bell} title="PagerDuty" description="Trigger PagerDuty incidents via Events API v2">
        <div className="space-y-3">
          <div>
            <SecretInput
              id="pd_key"
              label="Integration Routing Key"
              placeholder="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
              value={cfg.pagerduty_key}
              onChange={v => {
                setCfg(c => ({ ...c, pagerduty_key: v }))
                markTouched('pagerduty_key')
              }}
            />
            {showError('pagerduty_key') && (
              <p className="mt-1 flex items-center gap-1 text-[11px] text-red-400">
                <AlertCircle size={11} /> {showError('pagerduty_key')}
              </p>
            )}
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={() => test('pagerduty')}
              disabled={!cfg.pagerduty_key || testing.pagerduty}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[var(--border-subtle)] text-xs text-neutral-300 hover:border-white/20 disabled:opacity-40"
            >
              {testing.pagerduty ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
              Trigger test alert
            </button>
            <StatusBadge result={results.pagerduty} />
          </div>
          <p className="text-xs text-neutral-600">
            Get the routing key from your PagerDuty service → Integrations → Events API v2.
          </p>
        </div>
      </IntegrationCard>

      <IntegrationCard icon={Globe} title="Generic Webhook" description="Send JSON payloads to any HTTP endpoint">
        <div className="space-y-3">
          <div>
            <label htmlFor="generic_url" className="block text-xs text-neutral-400 mb-1">Endpoint URL</label>
            <input
              id="generic_url"
              type="url"
              value={cfg.generic_url}
              onChange={e => setCfg(c => ({ ...c, generic_url: e.target.value }))}
              onBlur={() => markTouched('generic_url')}
              placeholder="https://your-service.example.com/hook"
              aria-invalid={!!showError('generic_url')}
              aria-describedby={showError('generic_url') ? 'generic_url_err' : undefined}
              className={`w-full bg-white/[0.04] border rounded-lg px-3 py-2 text-sm text-white placeholder-neutral-600 focus:outline-none ${showError('generic_url') ? 'border-red-500/50 focus:border-red-500/70' : 'border-[var(--border-subtle)] focus:border-white/20'}`}
            />
            {showError('generic_url') && (
              <p id="generic_url_err" className="mt-1 flex items-center gap-1 text-[11px] text-red-400">
                <AlertCircle size={11} /> {showError('generic_url')}
              </p>
            )}
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={() => test('generic')}
              disabled={!cfg.generic_url || testing.generic}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[var(--border-subtle)] text-xs text-neutral-300 hover:border-white/20 disabled:opacity-40"
            >
              {testing.generic ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
              Send test payload
            </button>
            <StatusBadge result={results.generic} />
          </div>
          <div className="bg-white/[0.03] rounded-lg p-3 font-mono text-[11px] text-neutral-500">
            {`POST ${cfg.generic_url || '<url>'}\nContent-Type: application/json\n\n{"event":"aegis.test","source":"acp","timestamp":"<iso>"}`}
          </div>
        </div>
      </IntegrationCard>

      <div className="p-4 bg-white/[0.02] border border-[var(--border-subtle)] rounded-xl">
        <div className="flex items-start gap-3">
          <Webhook size={16} className="text-neutral-500 shrink-0 mt-0.5" />
          <div>
            <div className="text-xs font-medium text-neutral-300 mb-1">How playbooks use these settings</div>
            <p className="text-xs text-neutral-500 leading-relaxed">
              When a playbook step has <code className="text-neutral-400">action_type: SEND_ALERT</code> with <code className="text-neutral-400">channel: slack</code>, Aegis uses the Slack URL saved here. Steps can also override the URL per-step via <code className="text-neutral-400">params.webhook_url</code>. Generic WEBHOOK steps use the URL specified in the step params, not the global setting.
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}


// Sprint S2 — Slack OAuth connect block. Renders either the Connect
// button + setup hints (disconnected state) or the "Connected to
// {workspace}" badge with Disconnect + Send Test Message actions.
function SlackConnectSection({
  slackStatus, slackLoading, onRefreshStatus,
  onConnect, onDisconnect, onTestMessage,
  testing, testResult, showError,
}) {
  if (slackLoading) {
    return (
      <div className="flex items-center gap-2 text-xs text-neutral-500">
        <Loader2 size={12} className="animate-spin" /> Checking Slack connection…
      </div>
    )
  }

  if (!slackStatus.connected) {
    return (
      <div className="space-y-3">
        <button
          onClick={onConnect}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-[#4A154B] text-white text-sm font-medium hover:bg-[#611f63] transition-colors"
        >
          <Slack size={16} /> Connect Slack
          <ArrowRight size={14} />
        </button>
        <p className="text-xs text-neutral-500 leading-relaxed">
          One click → Slack consent screen → pick a channel → done. No api.slack.com
          tab, no openssl rand, no paste-fields.
        </p>
        {showError('slack_url') && (
          <p className="flex items-center gap-1 text-[11px] text-red-400">
            <AlertCircle size={11} /> {showError('slack_url')}
          </p>
        )}
      </div>
    )
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-sm text-emerald-400">
        <Check size={16} className="bg-emerald-500/20 rounded-full p-0.5" />
        Connected
        {slackStatus.workspace_id && (
          <span className="text-xs text-neutral-500">
            workspace <code className="text-neutral-400">{slackStatus.workspace_id}</code>
          </span>
        )}
        {slackStatus.channel_id && (
          <span className="text-xs text-neutral-500">
            channel <code className="text-neutral-400">{slackStatus.channel_id}</code>
          </span>
        )}
      </div>
      <div className="flex items-center gap-3">
        <button
          onClick={onTestMessage}
          disabled={testing}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[var(--border-subtle)] text-xs text-neutral-300 hover:border-white/20 disabled:opacity-40"
        >
          {testing ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
          Send test message
        </button>
        <StatusBadge result={testResult} />
        <button
          onClick={onDisconnect}
          className="ml-auto flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-red-500/30 text-xs text-red-300 hover:bg-red-500/10"
        >
          <Unlink size={11} /> Disconnect
        </button>
        <button
          onClick={onRefreshStatus}
          className="flex items-center gap-1 px-2 py-1.5 rounded-lg text-xs text-neutral-500 hover:text-neutral-300"
          aria-label="Refresh Slack status"
        >
          <RefreshCw size={11} />
        </button>
      </div>
      <p className="text-xs text-neutral-600">
        Approval cards land in this channel the moment an agent triggers an
        escalation. To swap channels, disconnect and reconnect — Slack will
        offer the channel picker again.
      </p>
    </div>
  )
}
