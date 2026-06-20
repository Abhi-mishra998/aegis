import React, { useCallback, useEffect, useState } from 'react'
import {
  CheckCircle2, Shield, ShieldCheck, AlertTriangle, RefreshCw, Activity,
} from 'lucide-react'
import Button from '../Common/Button'
import { workspaceService } from '../../services/api'

// Sprint 23 — Compliance Policy Packs.
//
// Five sales-grade packs (SOC 2 / PCI / HIPAA / Finance / DevOps). Each
// extends the base escalation rule set with framework-specific
// patterns (e.g. PCI escalates 'show me the full card number' to the
// CISO Inbox). Enabled packs are stamped on the audit row's
// metadata.framework_controls so the Compliance page can badge each
// control as 'enforced by the SOC2 Pack' etc.
//
// The catalog comes from the backend so adding a new pack is one
// `services/policy/packs.py` diff — no UI redeploy required.
export default function PolicyPacksTab() {
  const [catalog, setCatalog] = useState([])
  const [enabled, setEnabled] = useState([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [catResp, enabledResp] = await Promise.all([
        workspaceService.policyPacksCatalog(),
        workspaceService.getPolicyPacks(),
      ])
      setCatalog(catResp?.data || catResp || [])
      const en = (enabledResp?.data || enabledResp)?.enabled || []
      setEnabled(Array.isArray(en) ? en : [])
      setError('')
    } catch (e) {
      setError(e?.message || 'Failed to load policy packs')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const toggle = (id) => {
    setEnabled((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    )
  }

  const save = async () => {
    setBusy(true); setError(''); setSuccess('')
    try {
      await workspaceService.setPolicyPacks({ enabled })
      setSuccess(
        enabled.length === 0
          ? 'All packs disabled.'
          : `${enabled.length} pack${enabled.length === 1 ? '' : 's'} active: ${enabled.join(', ')}.`,
      )
      await load()
    } catch (e) {
      setError(e?.message || 'Save failed')
    } finally {
      setBusy(false)
    }
  }

  const dirty = JSON.stringify([...enabled].sort()) !== JSON.stringify(
    (catalog && catalog.length ? [...enabled].sort() : []),
  )
  // simpler dirty check: compare against last-loaded
  const [savedSnapshot, setSavedSnapshot] = useState('[]')
  useEffect(() => {
    if (!loading) setSavedSnapshot(JSON.stringify([...enabled].sort()))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading])
  const isDirty = JSON.stringify([...enabled].sort()) !== savedSnapshot

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-4 space-y-2">
        <div className="flex items-center gap-2 text-xs font-bold text-white">
          <ShieldCheck size={13} className="text-neutral-400" />
          Compliance Policy Packs
        </div>
        <p className="text-[11px] text-neutral-400 leading-snug max-w-2xl">
          Each pack extends Aegis's default escalation rule set with the
          framework-specific behaviours an auditor expects to see: SOC 2
          routes audit-log mutations to the CISO Inbox, PCI catches full
          PAN reads, HIPAA covers PHI export, Finance tightens money-
          movement thresholds, DevOps gates production change. Toggle
          one or more and click Save — every subsequent <code>/v1/messages</code> /
          <code>/v1/chat/completions</code> call is matched against the union.
        </p>
        <p className="text-[10px] text-neutral-600 max-w-2xl">
          Once a pack is on, every blocked / escalated decision lands in
          the audit log tagged with the framework controls it covers,
          so the <em>Prove → Compliance</em> page shows real enforcement
          evidence per control.
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
          Loading catalog…
        </div>
      ) : (
        <>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            {catalog.map((pack) => {
              const on = enabled.includes(pack.id)
              return (
                <button
                  type="button"
                  key={pack.id}
                  onClick={() => toggle(pack.id)}
                  className={
                    'text-left rounded-xl border p-4 transition-all space-y-3 ' +
                    (on
                      ? 'border-white bg-white/[0.06]'
                      : 'border-white/[0.07] bg-[#0a0a0a] hover:border-white/20')
                  }
                >
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="flex items-center gap-2 mb-1">
                        <Shield size={13} className="text-neutral-400" />
                        <span className="text-sm font-semibold text-white">{pack.label}</span>
                        {on && <CheckCircle2 size={13} className="text-green-400" />}
                      </div>
                      <p className="text-[11px] text-neutral-400 leading-snug max-w-md">
                        {pack.blurb}
                      </p>
                    </div>
                    <span
                      className={
                        'status-badge ' +
                        (on
                          ? 'text-green-400 bg-green-500/10 border-green-500/20'
                          : 'text-neutral-500 bg-white/[0.03] border-white/[0.06]')
                      }
                    >
                      {on ? 'Active' : 'Off'}
                    </span>
                  </div>

                  {/* Controls badges */}
                  <div className="flex flex-wrap gap-1.5">
                    {(pack.framework_controls || []).map((c) => (
                      <span
                        key={c}
                        className="inline-flex items-center gap-1 text-[10px] text-neutral-300 px-2 py-0.5 rounded-md bg-white/[0.04] border border-white/[0.06] font-mono"
                      >
                        {c}
                      </span>
                    ))}
                  </div>

                  {/* Extra escalations */}
                  <div className="space-y-1">
                    <div className="text-[10px] uppercase tracking-widest text-neutral-500 flex items-center gap-1">
                      <Activity size={10} aria-hidden="true" />
                      <span>Adds {pack.extra_escalations?.length || 0} escalation rule
                        {pack.extra_escalations?.length === 1 ? '' : 's'}</span>
                    </div>
                    <ul className="text-[11px] text-neutral-400 space-y-0.5">
                      {(pack.extra_escalations || []).map((ep) => (
                        <li key={ep.id} className="leading-snug">
                          <span className="text-neutral-500">•</span>{' '}
                          <span className="text-neutral-300 font-mono text-[10px]">{ep.approver_role}</span>{' '}
                          — {ep.label}
                        </li>
                      ))}
                    </ul>
                  </div>
                </button>
              )
            })}
          </div>

          <div className="flex items-center justify-between flex-wrap gap-2 pt-2">
            <div className="text-[11px] text-neutral-500 inline-flex items-center gap-1">
              <AlertTriangle size={11} className="text-amber-400" />
              Changes take effect on the NEXT escalation — there's no
              redeploy. Existing pending approvals are unaffected.
            </div>
            <Button onClick={save} disabled={busy || !isDirty} size="sm">
              {isDirty ? 'Save pack selection' : 'Saved'}
            </Button>
          </div>
        </>
      )}
    </div>
  )
}
