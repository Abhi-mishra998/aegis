import React, { useEffect, useState } from 'react'
import {
  Webhook, Slack, Bell, Globe, CheckCircle2, XCircle,
  Save, Play, Eye, EyeOff, Loader2, AlertCircle,
} from 'lucide-react'
import { webhookService } from '../services/api'

function StatusBadge({ result }) {
  if (!result) return null
  const ok = result.status === 'sent' || result.status === 'ok'
  return (
    <span className={`inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full ${
      ok ? 'bg-green-500/10 text-green-400' : 'bg-red-500/10 text-red-400'
    }`}>
      {ok ? <CheckCircle2 size={11} /> : <XCircle size={11} />}
      {ok ? 'Sent' : result.reason || result.status}
    </span>
  )
}

function SecretInput({ id, label, placeholder, value, onChange }) {
  const [show, setShow] = useState(false)
  return (
    <div>
      <label htmlFor={id} className="block text-xs text-neutral-400 mb-1">{label}</label>
      <div className="relative">
        <input
          id={id}
          type={show ? 'text' : 'password'}
          value={value}
          onChange={e => onChange(e.target.value)}
          placeholder={placeholder}
          className="
            w-full bg-white/[0.04] border border-[var(--border-subtle)]
            rounded-lg px-3 py-2 pr-9 text-sm text-white placeholder-neutral-600
            focus:outline-none focus:border-white/20
          "
        />
        <button
          type="button"
          onClick={() => setShow(v => !v)}
          className="absolute right-2.5 top-1/2 -translate-y-1/2 text-neutral-500 hover:text-white"
        >
          {show ? <EyeOff size={14} /> : <Eye size={14} />}
        </button>
      </div>
    </div>
  )
}

function ChannelCard({ icon: Icon, title, description, children }) {
  return (
    <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl p-5">
      <div className="flex items-center gap-3 mb-4">
        <div className="w-8 h-8 rounded-lg bg-white/[0.06] flex items-center justify-center">
          <Icon size={16} className="text-neutral-300" />
        </div>
        <div>
          <div className="text-sm font-medium text-white">{title}</div>
          <div className="text-xs text-neutral-500">{description}</div>
        </div>
      </div>
      {children}
    </div>
  )
}

export default function WebhookSettings() {
  const [cfg, setCfg] = useState({ slack_url: '', pagerduty_key: '', generic_url: '' })
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [loading, setLoading] = useState(true)
  const [testing, setTesting] = useState({})
  const [results, setResults] = useState({})
  const [error, setError] = useState('')

  useEffect(() => {
    webhookService.getConfig()
      .then(d => {
        const c = d?.data || d || {}
        setCfg({
          slack_url: c.slack_url || '',
          pagerduty_key: c.pagerduty_key || '',
          generic_url: c.generic_url || '',
        })
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  const save = async () => {
    setSaving(true)
    setError('')
    try {
      await webhookService.saveConfig(cfg)
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
        <button
          onClick={save}
          disabled={saving}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-white text-black text-sm font-medium hover:bg-neutral-200 disabled:opacity-50"
        >
          {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
          {saved ? 'Saved!' : 'Save Changes'}
        </button>
      </header>

      {error && (
        <div className="flex items-center gap-2 p-3 bg-red-500/10 border border-red-500/20 rounded-lg text-sm text-red-400">
          <AlertCircle size={14} /> {error}
        </div>
      )}

      <ChannelCard icon={Slack} title="Slack" description="Post alerts to a Slack channel via incoming webhook">
        <div className="space-y-3">
          <SecretInput
            id="slack_url"
            label="Incoming Webhook URL"
            placeholder="https://hooks.slack.com/services/T…/B…/…"
            value={cfg.slack_url}
            onChange={v => setCfg(c => ({ ...c, slack_url: v }))}
          />
          <div className="flex items-center gap-3">
            <button
              onClick={() => test('slack')}
              disabled={!cfg.slack_url || testing.slack}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[var(--border-subtle)] text-xs text-neutral-300 hover:border-white/20 disabled:opacity-40"
            >
              {testing.slack ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
              Send test message
            </button>
            <StatusBadge result={results.slack} />
          </div>
          <p className="text-xs text-neutral-600">
            The test fires a real Slack block-kit message to the configured URL.
            Create an incoming webhook at <span className="text-neutral-400">api.slack.com/apps</span>.
          </p>
        </div>
      </ChannelCard>

      <ChannelCard icon={Bell} title="PagerDuty" description="Trigger PagerDuty incidents via Events API v2">
        <div className="space-y-3">
          <SecretInput
            id="pd_key"
            label="Integration Routing Key"
            placeholder="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
            value={cfg.pagerduty_key}
            onChange={v => setCfg(c => ({ ...c, pagerduty_key: v }))}
          />
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
      </ChannelCard>

      <ChannelCard icon={Globe} title="Generic Webhook" description="Send JSON payloads to any HTTP endpoint">
        <div className="space-y-3">
          <div>
            <label htmlFor="generic_url" className="block text-xs text-neutral-400 mb-1">Endpoint URL</label>
            <input
              id="generic_url"
              type="url"
              value={cfg.generic_url}
              onChange={e => setCfg(c => ({ ...c, generic_url: e.target.value }))}
              placeholder="https://your-service.example.com/hook"
              className="
                w-full bg-white/[0.04] border border-[var(--border-subtle)]
                rounded-lg px-3 py-2 text-sm text-white placeholder-neutral-600
                focus:outline-none focus:border-white/20
              "
            />
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
      </ChannelCard>

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
