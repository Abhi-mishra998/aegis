import { useCallback, useEffect, useMemo, useState } from 'react'
import { AlertTriangle, RefreshCw, Sliders, RotateCcw } from 'lucide-react'
import Button from '../Common/Button'
import { decisionService } from '../../services/api'
import { useRole } from '../../hooks/useRole'

// Sprint UI-6 — per-tenant risk signal-weight tuning.
//
// The decision engine composes its final risk score as a weighted sum of five
// signals (inference / behavior / anomaly / cost / cross_agent). Backend
// defaults are shipped in `services/decision/engine.py::DEFAULT_WEIGHTS`; this
// tab lets ADMIN/SECURITY operators tune them per tenant (persisted as
// JSON at `acp:signal_weights:{tenant_id}` in Redis, with default fallback
// on any read failure so a bad override can never poison the pipeline).
//
// UX rule: the "sum of weights" is informational only — the backend does NOT
// require weights to sum to 1.0. It scales the final score; a tenant that
// wants to double their sensitivity to behavioral risk can just set behavior=2.
// We surface the sum so the operator understands what they're doing.

const SIGNAL_HINTS = {
  inference:   'LLM output content risk (jailbreaks, unsafe content, prompt injection). Default 1.0.',
  behavior:    'Sequence + velocity heuristics from the behavior service. Default 1.0.',
  anomaly:     'Learning-engine drift / anomaly score. Default 1.0.',
  cost:        'Cost-explosion risk (token budget spikes). Default 1.0.',
  cross_agent: 'Cross-agent correlation from the intelligence engine. Default 1.0.',
}

export default function SignalWeightsTab() {
  const { isAdmin } = useRole()

  const [signals, setSignals] = useState([])   // canonical list from backend
  const [pending, setPending] = useState({})   // {signal_key: number} unsaved
  const [loading, setLoading] = useState(true)
  const [saving, setSaving]   = useState(false)
  const [error, setError]     = useState('')
  const [success, setSuccess] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const resp = await decisionService.getSignalWeights()
      // Backend returns {weights, signals:[{key, label, weight}]}. `signals` is
      // the display order — trust that instead of iterating Object.entries.
      const s = resp?.data?.signals || []
      setSignals(s)
      setPending({})
      setError('')
    } catch (e) {
      setError(e?.message || 'Failed to load signal weights')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  // Map "inference_risk" (the signal.key from backend) → "inference" (the
  // weights map key the PUT endpoint accepts). Backend derives one from the
  // other by stripping "_risk" (see decision/router.py:305-323).
  const _weightKey = (k) => k?.replace(/_risk$/, '')

  const currentValue = (sig) => {
    const wk = _weightKey(sig.key)
    if (wk in pending) return pending[wk]
    return sig.weight
  }

  const setValue = (sig, v) => {
    const wk = _weightKey(sig.key)
    const num = Number.isFinite(+v) ? +v : 0
    setPending((p) => ({ ...p, [wk]: Math.max(0, Math.min(10, num)) }))
    setSuccess('')
  }

  const isDirty = Object.keys(pending).length > 0

  const totalWeight = useMemo(
    () => signals.reduce((acc, s) => acc + currentValue(s), 0),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [signals, pending],
  )

  const resetToDefaults = () => {
    // Backend defaults are 1.0 across the board (see engine.DEFAULT_WEIGHTS).
    // Populate pending with an explicit 1.0 for every signal so the save
    // roundtrips a clean reset instead of relying on omission semantics.
    setPending(Object.fromEntries(signals.map((s) => [_weightKey(s.key), 1.0])))
  }

  const save = async () => {
    if (!isDirty) return
    setSaving(true); setError(''); setSuccess('')
    try {
      const resp = await decisionService.setSignalWeights(pending)
      const saved = resp?.data?.weights || pending
      setSuccess(`Saved: ${Object.entries(saved).map(([k, v]) => `${k}=${v}`).join(', ')}. Effect: next request.`)
      await load()
    } catch (e) {
      const msg = e?.message || 'Save failed'
      setError(msg.includes('403') || /forbidden/i.test(msg)
        ? 'ADMIN or SECURITY role required to tune signal weights.'
        : msg)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-4 space-y-2">
        <div className="flex items-center gap-2 text-xs font-bold text-white">
          <Sliders size={13} className="text-neutral-400" />
          Detection engine signal weights
        </div>
        <p className="text-[11px] text-neutral-400 leading-snug max-w-2xl">
          The final risk score is a weighted sum of these five signals. Defaults
          are 1.0 across the board. Raise a weight to make the engine more
          sensitive to that signal, lower it (or set to 0) to ignore it entirely.
          Changes take effect on the very next request — no redeploy.
        </p>
        <p className="text-[10px] text-neutral-600">
          ADMIN or SECURITY role required to save. Read is unrestricted so
          READ_ONLY analysts can inspect the current configuration.
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
          Loading weights…
        </div>
      ) : (
        <>
          <div className="space-y-3">
            {signals.map((sig) => {
              const wk = _weightKey(sig.key)
              const val = currentValue(sig)
              const dirty = wk in pending
              return (
                <div
                  key={sig.key}
                  className="rounded-xl border border-white/[0.07] bg-[#0a0a0a] p-4"
                >
                  <div className="flex items-center gap-3 mb-2">
                    <span className="text-sm font-semibold text-white">{sig.label}</span>
                    <code className="text-[10px] text-neutral-500 font-mono">{sig.key}</code>
                    {dirty && (
                      <span className="ml-auto text-[10px] uppercase tracking-widest text-amber-400">
                        unsaved
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-3">
                    <input
                      type="range"
                      min="0"
                      max="3"
                      step="0.05"
                      value={Math.min(3, val)}
                      onChange={(e) => setValue(sig, e.target.value)}
                      disabled={!isAdmin}
                      className="flex-1 accent-white"
                      aria-label={`${sig.label} weight`}
                    />
                    <input
                      type="number"
                      min="0"
                      max="10"
                      step="0.05"
                      value={val}
                      onChange={(e) => setValue(sig, e.target.value)}
                      disabled={!isAdmin}
                      className="input-standard input-compact text-xs w-20 text-right font-mono"
                    />
                  </div>
                  <p className="text-[11px] text-neutral-500 mt-2 leading-snug">
                    {SIGNAL_HINTS[wk] || ''}
                  </p>
                </div>
              )
            })}
          </div>

          <div className="flex items-center justify-between flex-wrap gap-2 pt-2">
            <div className="flex items-center gap-4 text-[11px] text-neutral-500">
              <span>
                Sum of weights:{' '}
                <span className="text-neutral-300 font-mono font-bold">{totalWeight.toFixed(2)}</span>
              </span>
              <span className="inline-flex items-center gap-1">
                <AlertTriangle size={11} className="text-amber-400" />
                Sum is informational — backend does not normalize.
              </span>
            </div>
            <div className="flex gap-2">
              <Button
                size="sm"
                variant="secondary"
                onClick={resetToDefaults}
                disabled={!isAdmin || saving}
              >
                <RotateCcw size={12} /> Reset to defaults
              </Button>
              <Button size="sm" onClick={save} disabled={!isAdmin || saving || !isDirty}>
                {saving ? 'Saving…' : isDirty ? 'Save weights' : 'Saved'}
              </Button>
            </div>
          </div>

          {!isAdmin && (
            <p className="text-[10px] text-amber-400 text-right uppercase tracking-widest">
              ADMIN or SECURITY role required to edit
            </p>
          )}
        </>
      )}
    </div>
  )
}
