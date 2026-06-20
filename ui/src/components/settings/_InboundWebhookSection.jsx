import React, { useState } from 'react'
import {
  AlertTriangle, CheckCircle2, Copy, KeyRound, RefreshCw, Webhook,
} from 'lucide-react'
import Button from '../Common/Button'

/**
 * Sprint EI-18 (2026-06-21). Reusable "Inbound webhook" section shared
 * between JiraIntegrationTab + ServiceNowIntegrationTab. Renders:
 *  - the per-tenant webhook URL the operator pastes into Jira/SNOW
 *  - a Generate / Rotate secret button (POSTs to the rotate endpoint)
 *  - the one-time plaintext banner (mirrors EI-3 SCIM token UX)
 *
 * Props:
 *   vendor          'Jira' | 'ServiceNow' — label only
 *   docHref         link to docs/security/<vendor>-itsm-setup.md
 *   hasSecret       boolean — toggles Generate vs Rotate verb
 *   onRotate        async () => { plaintext, webhook_url, ... } — calls
 *                   integrationsService.rotate{Jira,ServiceNow}WebhookSecret
 *   disabled        outer-busy flag from the parent tab
 */
export default function InboundWebhookSection ({
  vendor, docHref, hasSecret, onRotate, disabled,
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
      )}
    </div>
  )
}
