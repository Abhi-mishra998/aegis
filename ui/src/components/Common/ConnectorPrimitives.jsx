import { useState } from 'react'
import { CheckCircle2, XCircle, Eye, EyeOff } from 'lucide-react'

/**
 * Shared primitives for connector-style settings pages (SSO, SIEM,
 * Webhooks). Extracted in the sprint-5 audit cleanup from three
 * near-identical implementations in
 *   ui/src/pages/SsoSettings.jsx
 *   ui/src/pages/SiemSettings.jsx
 *   ui/src/pages/WebhookSettings.jsx
 *
 * Each page declared its own SecretInput / StatusBadge /
 * IntegrationCard with the same structural shape but minor style
 * tweaks; this module is the single source of truth. Pass props for
 * the page-specific bits (status terminology, card accent colour).
 */

/* ── SecretInput ─────────────────────────────────────────────────────
 *  - text-mode (default):   single-line <input name="input" type=password> with an
 *                           eye-toggle on the right
 *  - textarea-mode (rows≥1): multi-line textarea with a Show/Hide
 *                           toggle in the label row; masks with •••
 *                           when hidden (used for SAML certs)
 */
export function SecretInput({ id, label, placeholder, value, onChange, rows }) {
  const [show, setShow] = useState(false)

  if (rows) {
    return (
      <div>
        <div className="flex items-center justify-between mb-1">
          <label htmlFor={id} className="text-xs text-neutral-400">{label}</label>
          <button
            type="button"
            onClick={() => setShow(v => !v)}
            className="text-[10px] text-neutral-500 hover:text-white flex items-center gap-1"
          >
            {show ? <EyeOff size={11} /> : <Eye size={11} />} {show ? 'Hide' : 'Show'}
          </button>
        </div>
        <textarea
          id={id}
          rows={rows}
          value={show ? value : value ? '•'.repeat(Math.min(value.length, 40)) : ''}
          onChange={e => onChange(e.target.value)}
          onFocus={() => setShow(true)}
          placeholder={placeholder}
          className="w-full bg-white/[0.04] border border-[var(--border-subtle)] rounded-lg px-3 py-2 text-xs text-white placeholder-neutral-600 focus:outline-none focus:border-white/20 font-mono resize-none"
        />
      </div>
    )
  }

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
          className="w-full bg-white/[0.04] border border-[var(--border-subtle)] rounded-lg px-3 py-2 pr-9 text-sm text-white placeholder-neutral-600 focus:outline-none focus:border-white/20"
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

/* ── StatusBadge ─────────────────────────────────────────────────────
 *  Renders the result of a connectivity probe.
 *
 *  Props:
 *    result      — the probe response: {status, http_status?, reason?}
 *    successText — defaults to the SIEM-style "Sent (HTTP nnn)" when
 *                  http_status is present, else "Sent"
 *
 *  Recognised result.status values:
 *    'sent' | 'ok'     → green check
 *    'skipped'         → neutral dash (e.g. provider not configured)
 *    anything else     → red X with reason fallback
 */
export function StatusBadge({ result, successText }) {
  if (!result) return null
  const status = result.status
  const ok = status === 'sent' || status === 'ok'
  const skipped = status === 'skipped'
  const chipClass = ok
    ? 'bg-green-500/10 text-green-400'
    : skipped
      ? 'bg-neutral-500/10 text-neutral-500'
      : 'bg-red-500/10 text-red-400'

  const label = ok
    ? (successText ?? (result.http_status ? `Sent (HTTP ${result.http_status})` : 'Sent'))
    : (result.reason || status)

  return (
    <span className={`inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full ${chipClass}`}>
      {ok ? <CheckCircle2 size={11} /> : <XCircle size={11} />}
      {label}
    </span>
  )
}

/* ── IntegrationCard ─────────────────────────────────────────────────
 *  Container shell for a single connector (Splunk, Datadog, Slack,
 *  PagerDuty, …). All three settings pages used the same wrapper —
 *  rounded card, icon chip top-left, title + description beside it.
 *
 *  Props:
 *    icon, title, description
 *    color    — optional bg class for the icon chip (e.g. "bg-blue-500/20").
 *               When omitted, defaults to the neutral chip the webhook
 *               settings page used.
 *    children — the form body inside the card
 */
export function IntegrationCard({ icon: Icon, title, description, color, children }) {
  const chipClass = color || 'bg-white/[0.06]'
  const iconClass = color ? 'text-white' : 'text-neutral-300'
  return (
    <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl p-5">
      <div className="flex items-center gap-3 mb-4">
        <div className={`w-8 h-8 rounded-lg ${chipClass} flex items-center justify-center`}>
          <Icon size={16} className={iconClass} />
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
