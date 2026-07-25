import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, RefreshCw, SlidersHorizontal, CheckCircle2 } from 'lucide-react'
import Button from '../Common/Button'
import { tenantSettingsService } from '../../services/api'

// Sprint UI-3 — per-tenant opt-in feature flags.
//
// Two flags today are cost/privacy-sensitive enough that they must be
// tenant-scoped (not process-wide env-vars):
//
//   • c3_sampling — ATF §9.3 consistency sampling. Costs 3× planner
//     latency + tokens on C3-classed actions; BLOCKs inconsistent plans.
//   • behavior_fingerprinting — ATF §9.2 learned behavioral signal.
//     Advisory-only per ADR-002; never authoritative on the Gate.
//
// Backend semantics: an explicit boolean (on/off) overrides the historical
// env-var enable-list; leaving a flag "unset" falls back to that env-var,
// so ops-owned deployments keep working while a tenant admin can flip
// the flag without a redeploy.
export default function FeatureFlagsTab() {
  const [flags, setFlags]   = useState(null)   // {flag: {effective, override}}
  const [pending, setPending] = useState({})   // {flag: bool} — unsaved edits
  const [loading, setLoading] = useState(true)
  const [busy, setBusy]     = useState(false)
  const [error, setError]   = useState('')
  const [success, setSuccess] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const resp = await tenantSettingsService.get()
      setFlags(resp?.data || {})
      setPending({})
      setError('')
    } catch (e) {
      setError(e?.message || 'Failed to load feature flags')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const currentValue = (name) => {
    if (name in pending) return pending[name]
    return flags?.[name]?.effective ?? false
  }

  const toggle = (name) => {
    setPending((p) => ({ ...p, [name]: !currentValue(name) }))
    setSuccess('')
  }

  const isDirty = Object.keys(pending).length > 0

  const save = async () => {
    setBusy(true); setError(''); setSuccess('')
    try {
      const resp = await tenantSettingsService.set(pending)
      const applied = resp?.data?.updated || pending
      const parts = Object.entries(applied).map(([k, v]) => `${k}=${v ? 'on' : 'off'}`)
      setSuccess(`Saved: ${parts.join(', ')}. Effect: next request.`)
      await load()
    } catch (e) {
      // OWNER-only endpoint — surface 403 as an actionable message so a
      // non-owner admin sees why the toggle silently didn't save.
      const msg = e?.message || 'Save failed'
      setError(msg.includes('403') || /forbidden/i.test(msg)
        ? 'OWNER role required to change feature flags.'
        : msg)
    } finally {
      setBusy(false)
    }
  }

  const rows = [
    {
      id: 'c3_sampling',
      label: 'Consistency sampling (C3)',
      envVar: 'ACP_C3_SAMPLING_TENANTS',
      blurb: (
        <>
          ATF §9.3 — plan the same C3 action three times and require a
          2-of-3 quorum before allowing. BLOCKs inconsistent plans; costs
          ~3× planner latency + tokens on C3-classed calls only.
        </>
      ),
      cost: '3× planner cost on C3 actions',
    },
    {
      id: 'behavior_fingerprinting',
      label: 'Behavior fingerprinting',
      envVar: 'ACP_BEHAVIOR_FINGERPRINTING_TENANTS',
      blurb: (
        <>
          ATF §9.2 — learned cross-agent correlation signal. Advisory-only
          per ADR-002: never authoritative on the Gate, only surfaced on
          the SOC dashboard and stamped into audit rows.
        </>
      ),
      cost: 'Advisory-only, no block',
    },
  ]

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-4 space-y-2">
        <div className="flex items-center gap-2 text-xs font-bold text-white">
          <SlidersHorizontal size={13} className="text-neutral-400" />
          Feature flags (per-tenant)
        </div>
        <p className="text-[11px] text-neutral-400 leading-snug max-w-2xl">
          These flags are cost- and privacy-sensitive, so they're OFF for
          every tenant by default. An explicit toggle here overrides the
          historical env-var enable list; leaving a flag "unset" (never
          toggled) falls back to whatever ops configured at deploy time.
        </p>
        <p className="text-[10px] text-neutral-600">
          OWNER role required. Changes take effect on the very next
          request — no redeploy, cached for 60s at each service.
        </p>
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

      {loading ? (
        <div className="text-xs text-neutral-500 py-8 text-center">
          <RefreshCw size={16} className="animate-spin inline mr-2" />
          Loading flags…
        </div>
      ) : (
        <>
          <div className="space-y-3">
            {rows.map((row) => {
              const on       = currentValue(row.id)
              const override = flags?.[row.id]?.override
              const dirty    = row.id in pending
              return (
                <div
                  key={row.id}
                  className="rounded-xl border border-white/[0.07] bg-[#0a0a0a] p-4 space-y-3"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 mb-1">
                        <span className="text-sm font-semibold text-white">{row.label}</span>
                        {on && <CheckCircle2 size={13} className="text-green-400" />}
                        {dirty && (
                          <span className="text-[10px] uppercase tracking-widest text-amber-400">
                            unsaved
                          </span>
                        )}
                      </div>
                      <p className="text-[11px] text-neutral-400 leading-snug max-w-2xl">
                        {row.blurb}
                      </p>
                      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[10px] text-neutral-500">
                        <span>
                          Cost: <span className="text-neutral-300">{row.cost}</span>
                        </span>
                        <span>
                          Env fallback:{' '}
                          <span className="text-neutral-300 font-mono">{row.envVar}</span>
                        </span>
                        <span>
                          Override:{' '}
                          <span className="text-neutral-300">
                            {override === null || override === undefined
                              ? 'unset (env fallback)'
                              : override ? 'true' : 'false'}
                          </span>
                        </span>
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={() => toggle(row.id)}
                      role="switch"
                      aria-checked={on}
                      aria-label={`Toggle ${row.label}`}
                      className={
                        'relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors ' +
                        (on ? 'bg-green-500/70' : 'bg-white/[0.08] border border-white/[0.08]')
                      }
                    >
                      <span
                        className={
                          'inline-block h-4 w-4 rounded-full bg-white transition-transform ' +
                          (on ? 'translate-x-6' : 'translate-x-1')
                        }
                      />
                    </button>
                  </div>
                </div>
              )
            })}
          </div>

          <div className="flex items-center justify-between flex-wrap gap-2 pt-2">
            <div className="text-[11px] text-neutral-500 inline-flex items-center gap-1">
              <AlertTriangle size={11} className="text-amber-400" />
              Consistency sampling adds real latency on C3 actions; leave
              off unless you actually want the 3× planner cost.
            </div>
            <Button onClick={save} disabled={busy || !isDirty} size="sm">
              {isDirty ? 'Save' : 'Saved'}
            </Button>
          </div>
        </>
      )}
    </div>
  )
}
