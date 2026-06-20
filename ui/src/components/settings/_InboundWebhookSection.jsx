import React, { useState } from 'react'
import {
  AlertCircle, AlertTriangle, CheckCircle2, Copy, KeyRound,
  MinusCircle, RefreshCw, Webhook,
} from 'lucide-react'
import Button from '../Common/Button'

/**
 * Sprint EI-18 (2026-06-21). Reusable "Inbound webhook" section shared
 * between JiraIntegrationTab + ServiceNowIntegrationTab. Renders:
 *  - the per-tenant webhook URL the operator pastes into Jira/SNOW
 *  - a Generate / Rotate secret button (POSTs to the rotate endpoint)
 *  - the one-time plaintext banner (mirrors EI-3 SCIM token UX)
 *  - Sprint EI-20: a deliverability "Last received <ts> ago — status:
 *    <status>" line so the operator can verify the round-trip is alive
 *    without grepping gateway logs.
 *
 * Props:
 *   vendor          'Jira' | 'ServiceNow' — label only
 *   docHref         link to docs/security/<vendor>-itsm-setup.md
 *   hasSecret       boolean — toggles Generate vs Rotate verb
 *   lastReceivedAt  ISO-8601 string or null (EI-20)
 *   lastStatus      WEBHOOK_STATUS_VOCAB string or null (EI-20)
 *   onRotate        async () => { plaintext, webhook_url, ... } — calls
 *                   integrationsService.rotate{Jira,ServiceNow}WebhookSecret
 *   disabled        outer-busy flag from the parent tab
 */
// Sprint EI-20 — must match services/gateway/routers/itsm_webhooks.py:
// WEBHOOK_STATUS_VOCAB. Adding a new value upstream means adding it
// here too.
const OK_STATUSES = new Set(['closed', 'already_closed', 'ignored'])
const WARN_STATUSES = new Set([
  'bad_signature', 'unknown_issue_key', 'unknown_sys_id',
  'no_issue_key', 'no_sys_id', 'no_config', 'patch_failed',
])

function _relativeTime (iso) {
  if (!iso) return null
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return null
  const diffSec = Math.floor((Date.now() - then) / 1000)
  if (diffSec < 0)       return 'in the future'
  if (diffSec < 60)      return `${diffSec}s ago`
  if (diffSec < 3600)    return `${Math.floor(diffSec / 60)}m ago`
  if (diffSec < 86400)   return `${Math.floor(diffSec / 3600)}h ago`
  return `${Math.floor(diffSec / 86400)}d ago`
}

export default function InboundWebhookSection ({
  vendor, docHref, hasSecret, onRotate, disabled,
  lastReceivedAt, lastStatus,
}) {
  const [revealed, setRevealed] = useState(null)   // { plaintext, webhook_url }
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const rotate = async () => {
    const verb = hasSecret ? 'Rotate' : 'Generate'
    if (hasSecret && !confirm(
      `Rotate the ${vendor} webhook secret? The old secret stops working ` +
      `immediately; you'll need to paste the new one into ${vendor}'s Automation/Business Rule.`,
    )) return
    setBusy(true); setError('')
    try {
      const resp = await onRotate()
      const data = resp?.data || resp
      if (data?.plaintext) {
        setRevealed({
          plaintext:   data.plaintext,
          webhook_url: data.webhook_url || '',
        })
      } else {
        setError(`${verb} failed: no plaintext returned`)
      }
    } catch (e) {
      setError(e?.message || `${verb} failed`)
    } finally {
      setBusy(false)
    }
  }

  const copy = async (txt) => {
    if (!txt) return
    try { await navigator.clipboard.writeText(txt) } catch (_) {}
  }

  return (
    <div className="rounded-xl border border-white/[0.08] bg-white/[0.02] p-5 space-y-3">
      <header className="flex items-center gap-2">
        <Webhook size={14} className="text-blue-400" />
        <h3 className="text-sm font-semibold text-white">Inbound webhook</h3>
      </header>
      <p className="text-xs text-neutral-500 leading-relaxed">
        Aegis can close the originating Aegis incident when you resolve the
        upstream {vendor} ticket. Set this up once: generate the secret here,
        paste it into a {vendor === 'Jira' ? 'Jira Automation rule' : 'ServiceNow Business Rule'},
        and the round-trip is live. Full walkthrough at{' '}
        <a className="text-white underline hover:no-underline" href={docHref}>
          {docHref}
        </a>.
      </p>

      {error && (
        <div className="text-xs text-red-300 bg-red-500/[0.06] border border-red-500/20 rounded-lg p-3">
          {error}
        </div>
      )}

      {revealed && (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/[0.06] p-4 space-y-3">
          <div className="flex items-start gap-2">
            <AlertTriangle size={14} className="text-amber-300 shrink-0 mt-0.5" />
            <div>
              <p className="text-sm text-white font-semibold">Copy this secret now</p>
              <p className="text-xs text-amber-200/80 leading-relaxed mt-1">
                Aegis does not store the plaintext anywhere you can retrieve
                it again. Paste it into {vendor} and dismiss this banner.
              </p>
            </div>
          </div>
          <div>
            <label className="block text-[10px] uppercase tracking-wide text-amber-300/80 mb-1">
              Webhook URL (paste into {vendor})
            </label>
            <div className="flex items-center gap-2">
              <code className="flex-1 px-3 py-2 text-[12px] font-mono bg-black/40 border border-white/[0.08] rounded text-white break-all">
                {revealed.webhook_url}
              </code>
              <Button onClick={() => copy(revealed.webhook_url)} variant="secondary">
                <Copy size={12} />
              </Button>
            </div>
          </div>
          <div>
            <label className="block text-[10px] uppercase tracking-wide text-amber-300/80 mb-1">
              Secret (HMAC key)
            </label>
            <div className="flex items-center gap-2">
              <code className="flex-1 px-3 py-2 text-[12px] font-mono bg-black/40 border border-white/[0.08] rounded text-white break-all">
                {revealed.plaintext}
              </code>
              <Button onClick={() => copy(revealed.plaintext)} variant="secondary">
                <Copy size={12} />
              </Button>
            </div>
          </div>
          <div className="flex justify-end">
            <Button onClick={() => setRevealed(null)} variant="primary">
              I've copied it
            </Button>
          </div>
        </div>
      )}

      {!revealed && (
        <div className="space-y-3">
          {/* EI-20 — deliverability indicator. Always renders so the
              operator can tell at a glance whether Aegis has ever
              received a webhook from this vendor + how it was handled. */}
          <ActivityRow lastReceivedAt={lastReceivedAt} lastStatus={lastStatus} />

          <div className="flex items-center justify-between gap-3 flex-wrap">
            <div className="text-xs text-neutral-400 flex items-center gap-2">
              {hasSecret ? (
                <>
                  <CheckCircle2 size={12} className="text-green-400" />
                  Secret configured — rotate any time to invalidate the old one.
                </>
              ) : (
                <>
                  <KeyRound size={12} className="text-neutral-500" />
                  No secret yet — generate one to enable upstream-resolve close.
                </>
              )}
            </div>
            <Button onClick={rotate} disabled={disabled || busy}
              variant={hasSecret ? 'secondary' : 'primary'}>
              {busy ? <RefreshCw size={12} className="animate-spin" /> : <KeyRound size={12} />}
              {hasSecret ? 'Rotate secret' : 'Generate secret'}
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}


function ActivityRow ({ lastReceivedAt, lastStatus }) {
  // Three visual states: never (no events yet), ok (last event was a
  // success/idempotent outcome), warn (last event was rejected /
  // unknown / failed). Operator should see the warn class red enough
  // to investigate but not so loud it screams during normal ignored
  // events (e.g. SNOW Business Rule firing on every comment).
  const rel = _relativeTime(lastReceivedAt)
  let Icon = MinusCircle
  let colour = 'text-neutral-500'
  let label  = 'No webhook events received yet'

  if (rel && lastStatus) {
    label = `Last received ${rel} — status: ${lastStatus}`
    if (OK_STATUSES.has(lastStatus)) {
      Icon = CheckCircle2
      colour = 'text-green-400'
    } else if (WARN_STATUSES.has(lastStatus)) {
      Icon = AlertCircle
      colour = 'text-amber-400'
    } else {
      // Status word we don't recognise — keep it neutral but show the
      // raw word so the operator can grep for it.
      Icon = AlertCircle
      colour = 'text-amber-400'
    }
  }

  return (
    <div className={`text-xs flex items-center gap-2 ${colour}`}>
      <Icon size={12} className="shrink-0" aria-hidden="true" />
      <span title={lastReceivedAt || 'never received'}>{label}</span>
    </div>
  )
}
