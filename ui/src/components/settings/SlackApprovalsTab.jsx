import React, { useCallback, useEffect, useState } from 'react'
import { CheckCircle2, MessagesSquare, RefreshCw, ExternalLink, Trash2 } from 'lucide-react'
import Button from '../Common/Button'
import { workspaceService } from '../../services/api'

// Sprint 21 — Slack approvals settings.
//
// One field: the Slack incoming-webhook URL. Submit it; the backend
// auto-generates a signing secret + stores both on the tenant row.
// Future escalations on /v1/messages POST a Block-Kit card to that
// URL with HMAC-signed Approve / Reject links pointing at the
// gateway. Clicking either lands an override exactly like the
// in-app inbox.
//
// The signing secret never leaves the backend — even this page never
// sees it.
export default function SlackApprovalsTab() {
  const [config, setConfig] = useState(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const [draftUrl, setDraftUrl] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const resp = await workspaceService.getSlackConfig()
      const data = resp?.data || resp
      setConfig(data || null)
      setDraftUrl(data?.webhook_url || '')
      setError('')
    } catch (e) {
      setError(e?.message || 'Failed to load Slack config')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const save = async (rotate = false) => {
    setBusy(true); setError(''); setSuccess('')
    try {
      await workspaceService.setSlackConfig({
        webhook_url: draftUrl.trim(),
        rotate_secret: rotate,
      })
      setSuccess(rotate ? 'Signing secret rotated.' : 'Slack config saved.')
      await load()
    } catch (e) {
      setError(e?.message || 'Save failed')
    } finally {
      setBusy(false)
    }
  }

  const disable = async () => {
    if (!window.confirm('Disable Slack approvals? The webhook URL and signing secret will be cleared.')) return
    setBusy(true); setError(''); setSuccess('')
    try {
      await workspaceService.setSlackConfig({ webhook_url: '' })
      setSuccess('Slack approvals disabled.')
      setDraftUrl('')
      await load()
    } catch (e) {
      setError(e?.message || 'Disable failed')
    } finally {
      setBusy(false)
    }
  }

  const configured = Boolean(config?.configured)
  const urlChanged = (config?.webhook_url || '') !== draftUrl.trim()
  const isValidSlack = draftUrl.trim() === '' || draftUrl.trim().startsWith('https://hooks.slack.com/')

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-4 space-y-2">
        <div className="flex items-center gap-2 text-xs font-bold text-white">
          <MessagesSquare size={13} className="text-neutral-400" />
          Slack approvals
        </div>
        <p className="text-[11px] text-neutral-400 leading-snug max-w-2xl">
          When the LLM proxy escalates a prompt (wire transfer above $100k,
          kubectl-delete-prod, bulk PII export, …), Aegis posts an Approve /
          Reject card to this Slack channel. The buttons are HMAC-signed
          callback links — clicking either lands the same audit row the
          in-app Approval Inbox would. Re-using your existing Slack workspace
          means no Slack app install.
        </p>
        <p className="text-[10px] text-neutral-600 max-w-2xl">
          Create an incoming webhook in <em>Slack &rarr; Integrations &rarr; Incoming Webhooks</em>,
          point it at the channel where your CFO/CISO triages approvals, then
          paste the URL below.
          <a
            href="https://api.slack.com/messaging/webhooks"
            target="_blank" rel="noreferrer"
            className="ml-1 inline-flex items-center gap-1 text-neutral-400 hover:text-white underline-offset-2 underline"
          >
            Slack docs <ExternalLink size={9} />
          </a>
        </p>
      </div>

      {loading ? (
        <div className="text-xs text-neutral-500 py-6 text-center">Loading…</div>
      ) : (
        <div className="rounded-xl border border-white/[0.07] bg-[#0a0a0a] p-4 space-y-4">
          <div className="flex items-center justify-between">
            <div className="space-y-1">
              <div className="text-[11px] uppercase tracking-widest text-neutral-500">Status</div>
              <div className="flex items-center gap-2">
                {configured ? (
                  <>
                    <CheckCircle2 size={13} className="text-green-400" />
                    <span className="text-xs text-green-300">Active — escalations post to Slack</span>
                  </>
                ) : (
                  <>
                    <span className="w-1.5 h-1.5 rounded-full bg-neutral-600" />
                    <span className="text-xs text-neutral-400">Not configured — escalations only show in the in-app Inbox</span>
                  </>
                )}
              </div>
            </div>
          </div>

          <div className="space-y-1.5">
            <label className="label-standard" htmlFor="slackUrl">Incoming webhook URL</label>
            <input
              id="slackUrl"
              type="url"
              spellCheck={false}
              autoComplete="off"
              className={
                'input-standard h-10 font-mono text-[12px] ' +
                (isValidSlack ? '' : 'border-red-500/40')
              }
              placeholder="https://hooks.slack.com/services/T…/B…/…"
              value={draftUrl}
              onChange={(e) => setDraftUrl(e.target.value)}
            />
            {!isValidSlack && (
              <p className="text-[10px] text-red-400">
                Must start with <code>https://hooks.slack.com/</code>.
              </p>
            )}
          </div>

          {error && (
            <div className="text-xs text-red-400 bg-red-500/[0.06] border border-red-500/20 rounded-xl p-3">
              {error}
            </div>
          )}
          {success && (
            <div className="text-xs text-green-300 bg-green-500/[0.06] border border-green-500/20 rounded-xl p-3">
              {success}
            </div>
          )}

          <div className="flex justify-between gap-2 flex-wrap">
            <div className="flex gap-2">
              {configured && (
                <>
                  <Button variant="secondary" size="sm" onClick={() => save(true)} disabled={busy}>
                    <RefreshCw size={12} /> Rotate signing secret
                  </Button>
                  <Button variant="secondary" size="sm" onClick={disable} disabled={busy}>
                    <Trash2 size={12} /> Disable
                  </Button>
                </>
              )}
            </div>
            <Button onClick={() => save(false)} disabled={busy || (!isValidSlack) || (!urlChanged && configured)} size="sm">
              {configured && !urlChanged ? 'Saved' : 'Save'}
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
