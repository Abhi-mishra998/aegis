import React, { useCallback, useEffect, useState } from 'react'
import {
  KeyRound, Plus, Trash2, RefreshCw, AlertTriangle, CheckCircle2, Copy,
} from 'lucide-react'
import Button from '../Common/Button'
import { scimService } from '../../services/api'

// Sprint EI-3 (2026-06-20) — Okta SCIM bearer-token management.
//
// One label per token. Issuing returns the plaintext exactly once — we
// surface a "copy now" banner with a one-click Copy and hold the value
// until the operator dismisses the banner. After dismiss the plaintext
// is gone from the UI state and can never be retrieved again.
//
// Revoke is one-click + confirm (irreversible — Okta will need a new
// token to keep provisioning).
export default function ScimTokensTab() {
  const [tokens, setTokens] = useState([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [label, setLabel] = useState('Okta')
  const [revealed, setRevealed] = useState(null)  // {plaintext, label, prefix}

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const resp = await scimService.listTokens()
      setTokens(resp?.data || [])
      setError('')
    } catch (e) {
      setError(e?.message || 'Failed to load SCIM tokens')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const issue = async () => {
    if (!label.trim()) { setError('Label is required'); return }
    setBusy(true); setError('')
    try {
      const resp = await scimService.createToken(label.trim())
      const data = resp?.data
      if (data?.plaintext) {
        setRevealed({
          plaintext: data.plaintext,
          label: data.label,
          prefix: data.token_prefix,
        })
        setLabel('Okta')
      }
      await load()
    } catch (e) {
      setError(e?.message || 'Failed to issue token')
    } finally {
      setBusy(false)
    }
  }

  const revoke = async (tokenId, lbl) => {
    if (!confirm(`Revoke "${lbl}"? Okta provisioning using this token will fail until you paste in a new one.`)) {
      return
    }
    setBusy(true); setError('')
    try {
      await scimService.revokeToken(tokenId)
      await load()
    } catch (e) {
      setError(e?.message || 'Revoke failed')
    } finally {
      setBusy(false)
    }
  }

  const copy = async () => {
    if (!revealed?.plaintext) return
    try {
      await navigator.clipboard.writeText(revealed.plaintext)
    } catch {
      // No clipboard — operator can still select + copy manually.
    }
  }

  if (loading) return <div className="text-sm text-neutral-500">Loading…</div>

  return (
    <div className="space-y-6 max-w-3xl">
      <header>
        <h2 className="text-base font-semibold text-white flex items-center gap-2">
          <KeyRound size={14} className="text-blue-400" /> SCIM provisioning tokens
        </h2>
        <p className="text-xs text-neutral-500 mt-1">
          Issue one bearer token, paste it into Okta → App → Provisioning →
          Authentication. Okta then mirrors User + Group changes into Aegis
          via <code className="text-neutral-400">/scim/v2/</code>. Setup walkthrough at{' '}
          <a className="text-white underline hover:no-underline"
             href="/docs/security/okta-scim-setup">
            docs/security/okta-scim-setup.md
          </a>.
        </p>
      </header>

      {error && (
        <div className="text-xs text-red-300 bg-red-500/[0.06] border border-red-500/20 rounded-lg p-3">
          {error}
        </div>
      )}

      {revealed && (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/[0.06] p-4">
          <div className="flex items-start gap-2 mb-2">
            <AlertTriangle size={14} className="text-amber-300 shrink-0 mt-0.5" />
            <div>
              <p className="text-sm text-white font-semibold">Copy this token now</p>
              <p className="text-xs text-amber-200/80 leading-relaxed mt-1">
                Aegis does not store the plaintext and cannot show it again. Paste it
                into Okta and dismiss this banner.
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2 mt-3">
            <code className="flex-1 px-3 py-2 text-sm font-mono bg-black/40 border border-white/[0.08] rounded text-white break-all">
              {revealed.plaintext}
            </code>
            <Button onClick={copy} variant="secondary">
              <Copy size={12} /> Copy
            </Button>
          </div>
          <div className="mt-3 flex justify-end">
            <Button onClick={() => setRevealed(null)} variant="primary">
              I've copied it
            </Button>
          </div>
        </div>
      )}

      <div className="flex items-end gap-2">
        <div className="flex-1">
          <label className="block text-[11px] uppercase tracking-wide text-neutral-500 mb-1">
            Label (helps identify which Okta app this belongs to)
          </label>
          <input
            type="text"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="Okta-prod"
            className="w-full px-3 py-2 text-sm rounded-lg bg-white/[0.04] border border-white/[0.08]
              text-white placeholder-neutral-600 focus:outline-none focus:border-white/[0.2]"
          />
        </div>
        <Button onClick={issue} disabled={busy} variant="primary">
          {busy ? <RefreshCw size={12} className="animate-spin" /> : <Plus size={12} />}
          Issue token
        </Button>
      </div>

      <div className="rounded-xl border border-white/[0.08] bg-white/[0.02] divide-y divide-white/[0.05]">
        {tokens.length === 0 ? (
          <div className="p-6 text-center text-sm text-neutral-500">
            No SCIM tokens issued. Click <b>Issue token</b> above to create your first one.
          </div>
        ) : (
          tokens.map((t) => (
            <div key={t.id} className="flex items-center gap-3 px-4 py-3">
              <div className={`w-2 h-2 rounded-full shrink-0
                ${t.active ? 'bg-green-400' : 'bg-neutral-600'}`} />
              <div className="flex-1 min-w-0">
                <div className="text-sm text-white truncate">{t.label}</div>
                <div className="text-[11px] text-neutral-500 font-mono">{t.token_prefix}</div>
              </div>
              <div className="text-[11px] text-neutral-500 hidden sm:block">
                {t.last_used_at ? `last used ${new Date(t.last_used_at).toLocaleString()}` : 'never used'}
              </div>
              {t.active ? (
                <Button onClick={() => revoke(t.id, t.label)} disabled={busy} variant="danger">
                  <Trash2 size={12} /> Revoke
                </Button>
              ) : (
                <span className="text-[11px] text-neutral-500 flex items-center gap-1">
                  <CheckCircle2 size={11} /> revoked
                </span>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  )
}
