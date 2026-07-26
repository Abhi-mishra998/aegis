import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Activity, AlertTriangle, ArrowRight, CheckCircle2, Download,
  History, Loader2, RefreshCw, ShieldCheck, Skull,
} from 'lucide-react'
import Card from '../components/Common/Card'
import Button from '../components/Common/Button'
import ConfirmDialog from '../components/Common/ConfirmDialog'
import SkeletonLoader from '../components/Common/SkeletonLoader'
import { lifecycleService, auditService } from '../services/api'
import { useRole } from '../hooks/useRole'

// Sprint UI-4 — ATF §14.5 deployment lifecycle admin.
//
// Every transition is itself a C3 ledger event (`lifecycle_{state}`),
// so this page is BOTH the driver of the state machine AND the read
// surface for its ledger. OWNER-only writes; anyone can read the
// current state + audit trail.
//
// DESTROY is a terminal transition — when the /lifecycle/transition
// response comes back with `destruction_certificate`, we surface a
// download link immediately. The customer keeps that JSON forever as
// proof of what existed and when it was destroyed (§14.5 line 3).

const STATE_ORDER = [
  'INSTALL',
  'BOOTSTRAP',
  'ENFORCE',
  'ROTATE',
  'UPGRADE',
  'ROLLBACK',
  'DECOMMISSION',
  'DESTROY',
]

const STATE_META = {
  INSTALL:      { color: 'text-neutral-400', bg: 'bg-neutral-500/10',  border: 'border-neutral-500/30',
                  blurb: 'Bundle installed. Nothing enforced yet — bootstrap Aegis before any policy fires.' },
  BOOTSTRAP:    { color: 'text-blue-400',    bg: 'bg-blue-500/10',     border: 'border-blue-500/30',
                  blurb: 'Keys minted, tenants provisioned, transparency log initialized. Ready for enforce.' },
  ENFORCE:      { color: 'text-green-400',   bg: 'bg-green-500/10',    border: 'border-green-500/30',
                  blurb: 'Live enforcement. Policies gate every action; audit chain is anchored.' },
  ROTATE:       { color: 'text-amber-400',   bg: 'bg-amber-500/10',    border: 'border-amber-500/30',
                  blurb: 'Signing keys being rotated. Cross-signed with old key for continuity — no chain break.' },
  UPGRADE:      { color: 'text-purple-400',  bg: 'bg-purple-500/10',   border: 'border-purple-500/30',
                  blurb: 'Rolling out new binaries. Old & new share the ledger; roll back if regressions surface.' },
  ROLLBACK:     { color: 'text-orange-400',  bg: 'bg-orange-500/10',   border: 'border-orange-500/30',
                  blurb: 'Reverting to the previous release. Audit chain preserved; ledger continues.' },
  DECOMMISSION: { color: 'text-amber-400',   bg: 'bg-amber-500/10',    border: 'border-amber-500/30',
                  blurb: 'Enforcement paused, data being drained. Next stop is DESTROY — this is the point of no return.' },
  DESTROY:      { color: 'text-red-400',     bg: 'bg-red-500/10',      border: 'border-red-500/30',
                  blurb: 'Terminal state. A signed destruction certificate is minted from the final anchor.' },
}

const TRANSITION_TIP = {
  BOOTSTRAP:    'Provisions signing keys + transparency log. Reversible only by re-installing.',
  ENFORCE:      'Live policies start gating every /execute call. Reversible via ROLLBACK.',
  ROTATE:       'Rotates transparency signing keys with cross-signing (no chain break).',
  UPGRADE:      'Rolls out a new bundle version. Roll back if health drops.',
  ROLLBACK:     'Returns to the previous binary; ledger unchanged.',
  DECOMMISSION: 'Pauses enforcement + starts data drain. Next transition is DESTROY.',
  DESTROY:      'TERMINAL. Mints a destruction certificate; you cannot re-enter any other state.',
}

function _relTime(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  const diff = Date.now() - d.getTime()
  if (diff < 60_000) return 'just now'
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`
  return d.toLocaleString()
}

function _downloadJson(obj, filename) {
  const blob = new Blob([JSON.stringify(obj, null, 2)], { type: 'application/json' })
  const url  = URL.createObjectURL(blob)
  const a    = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

export default function LifecycleAdmin() {
  const { isOwner } = useRole()

  const [current, setCurrent] = useState(null)   // 'INSTALL' | 'BOOTSTRAP' | …
  const [nextStates, setNextStates] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const [audit, setAudit] = useState([])
  const [auditLoading, setAuditLoading] = useState(true)

  const [pendingTarget, setPendingTarget] = useState(null)
  const [reason, setReason] = useState('')
  const [transitioning, setTransitioning] = useState(false)
  const [transitionError, setTransitionError] = useState('')
  const [lastCert, setLastCert] = useState(null)
  const [certReissuing, setCertReissuing] = useState(false)

  const fetchState = useCallback(async () => {
    try {
      const resp = await lifecycleService.get()
      const data = resp?.data || {}
      setCurrent(data.state || 'INSTALL')
      setNextStates(data.next || [])
      setError('')
    } catch (e) {
      setError(e?.message || 'Failed to load lifecycle state')
    } finally {
      setLoading(false)
    }
  }, [])

  const fetchAudit = useCallback(async () => {
    setAuditLoading(true)
    try {
      // The audit endpoint indexes on action, and only the specific
      // transition strings ever get written — so a wildcard search would
      // be more elegant, but doing 8 tiny parallel queries keeps the
      // filter server-side and avoids pulling every audit row.
      const results = await Promise.all(
        STATE_ORDER.map((s) =>
          auditService.searchLogs({ action: `lifecycle_${s.toLowerCase()}`, limit: 20 }),
        ),
      )
      const rows = results
        .flatMap((r) => r?.data?.items || r?.items || r?.data || [])
        .sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''))
        .slice(0, 40)
      setAudit(rows)
    } catch {
      setAudit([])
    } finally {
      setAuditLoading(false)
    }
  }, [])

  useEffect(() => { fetchState(); fetchAudit(); }, [fetchState, fetchAudit])

  const openConfirm = (target) => {
    setReason('')
    setTransitionError('')
    setPendingTarget(target)
  }

  const performTransition = async () => {
    if (!pendingTarget) return
    setTransitioning(true)
    setTransitionError('')
    try {
      const resp = await lifecycleService.transition(pendingTarget, reason)
      const data = resp?.data || {}
      // DESTROY response also carries the cert — surface it for immediate download.
      if (data.destruction_certificate) {
        setLastCert(data.destruction_certificate)
      }
      setPendingTarget(null)
      await Promise.all([fetchState(), fetchAudit()])
    } catch (e) {
      const msg = e?.message || 'Transition failed'
      setTransitionError(
        msg.includes('403') || /forbidden/i.test(msg)
          ? 'OWNER role required to change lifecycle state.'
          : msg.includes('409') || /not permitted/i.test(msg)
            ? `Illegal transition: ${current} → ${pendingTarget}. State may have changed — refresh.`
            : msg,
      )
    } finally {
      setTransitioning(false)
    }
  }

  const reissueCert = async () => {
    setCertReissuing(true)
    try {
      const resp = await auditService.issueDestructionCertificate()
      setLastCert(resp?.data || resp)
    } catch (e) {
      setTransitionError(e?.message || 'Certificate reissue failed')
    } finally {
      setCertReissuing(false)
    }
  }

  const meta = current ? STATE_META[current] : null

  const timelineStates = useMemo(() => {
    // Linear happy-path display: INSTALL → BOOTSTRAP → ENFORCE → DECOMMISSION → DESTROY.
    // ROTATE/UPGRADE/ROLLBACK are "orbits" around ENFORCE, shown as a badge on ENFORCE.
    const happyPath = ['INSTALL', 'BOOTSTRAP', 'ENFORCE', 'DECOMMISSION', 'DESTROY']
    const currentIdx = happyPath.indexOf(current)
    // "Orbit" states are reachable only from ENFORCE and return to it —
    // treat them as "past ENFORCE" so the timeline doesn't backtrack.
    const displayIdx = ['ROTATE', 'UPGRADE', 'ROLLBACK'].includes(current)
      ? happyPath.indexOf('ENFORCE')
      : currentIdx
    return happyPath.map((s, i) => ({
      state:   s,
      isCurrent: s === current,
      isPast:  i < displayIdx,
    }))
  }, [current])

  return (
    <div className="space-y-5">
      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between">
        <div className="space-y-1">
          <h1 className="text-2xl font-bold tracking-tight text-white flex items-center gap-2">
            <ShieldCheck size={20} className="text-neutral-400" />
            Deployment lifecycle
          </h1>
          <p className="text-xs text-neutral-500">
            ATF §14.5 — INSTALL → BOOTSTRAP → ENFORCE → ROTATE/UPGRADE/ROLLBACK
            → DECOMMISSION → DESTROY. Every transition is a C3 ledger event.
          </p>
        </div>
        <Button variant="secondary" size="sm" onClick={() => { fetchState(); fetchAudit(); }} disabled={loading}>
          <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
          Refresh
        </Button>
      </div>

      {error && (
        <div className="text-xs text-red-400 bg-red-500/[0.06] border border-red-500/20 rounded-xl p-3">
          {error}
        </div>
      )}

      {loading ? (
        <SkeletonLoader count={4} />
      ) : (
        <>
          {/* ── Current state card ─────────────────────────────────────────── */}
          <Card className="p-5 space-y-4">
            <div className="flex items-start justify-between gap-4 flex-wrap">
              <div className="min-w-0">
                <p className="text-[10px] uppercase tracking-widest text-neutral-500 mb-1">
                  Current state
                </p>
                <div className="flex items-center gap-3">
                  <span className={`inline-flex items-center gap-2 px-3 py-1 rounded-md border font-mono text-sm font-bold ${meta?.color} ${meta?.bg} ${meta?.border}`}>
                    <span className={`w-2 h-2 rounded-full ${meta?.color?.replace('text-', 'bg-')}`} />
                    {current}
                  </span>
                  {['ROTATE', 'UPGRADE', 'ROLLBACK'].includes(current) && (
                    <span className="text-[10px] px-2 py-1 rounded border border-white/[0.08] text-neutral-400">
                      returns to ENFORCE on completion
                    </span>
                  )}
                </div>
                <p className="text-xs text-neutral-400 mt-3 max-w-2xl leading-relaxed">
                  {meta?.blurb}
                </p>
              </div>
            </div>

            {/* Happy-path timeline */}
            <div className="pt-2 border-t border-white/[0.06]">
              <div className="flex items-center gap-2 flex-wrap">
                {timelineStates.map((t, i) => {
                  const stMeta = STATE_META[t.state]
                  return (
                    <div key={t.state} className="flex items-center gap-2">
                      <div className={`flex items-center gap-1.5 px-2 py-1 rounded-md text-[11px] font-mono ${
                        t.isCurrent
                          ? `${stMeta.color} ${stMeta.bg} border ${stMeta.border} font-bold`
                          : t.isPast
                            ? 'text-neutral-500 line-through'
                            : 'text-neutral-600'
                      }`}>
                        {t.isCurrent && <span className={`w-1.5 h-1.5 rounded-full ${stMeta.color?.replace('text-', 'bg-')}`} />}
                        {t.state}
                      </div>
                      {i < timelineStates.length - 1 && (
                        <ArrowRight size={10} className="text-neutral-700" />
                      )}
                    </div>
                  )
                })}
              </div>
            </div>
          </Card>

          {/* ── Transition actions ─────────────────────────────────────────── */}
          <Card className="p-5 space-y-4">
            <div className="flex items-center gap-2">
              <Activity size={14} className="text-neutral-400" />
              <span className="text-sm font-semibold text-white">Available transitions</span>
              {!isOwner && (
                <span className="ml-auto text-[10px] text-amber-400 uppercase tracking-widest">
                  OWNER role required to transition
                </span>
              )}
            </div>

            {nextStates.length === 0 ? (
              <div className="text-xs text-neutral-500 italic bg-white/[0.02] border border-white/[0.06] rounded-lg p-4">
                {current === 'DESTROY'
                  ? 'DESTROY is terminal. No transitions remain — the destruction certificate below is your permanent proof of the final anchor.'
                  : 'No legal transitions from this state.'}
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {nextStates.map((t) => {
                  const tMeta = STATE_META[t]
                  const isDestroy = t === 'DESTROY'
                  const isDecomm  = t === 'DECOMMISSION'
                  return (
                    <button
                      type="button"
                      key={t}
                      disabled={!isOwner}
                      onClick={() => openConfirm(t)}
                      className={`text-left rounded-xl border p-4 space-y-2 transition-all disabled:opacity-40 disabled:cursor-not-allowed ${
                        isDestroy
                          ? 'border-red-500/20 bg-red-500/[0.03] hover:border-red-500/40 hover:bg-red-500/[0.06]'
                          : isDecomm
                            ? 'border-amber-500/20 bg-amber-500/[0.03] hover:border-amber-500/40'
                            : 'border-white/[0.08] bg-[#0a0a0a] hover:border-white/20'
                      }`}
                    >
                      <div className="flex items-center gap-2">
                        {isDestroy && <Skull size={14} className="text-red-400" />}
                        {isDecomm  && <AlertTriangle size={14} className="text-amber-400" />}
                        <span className={`text-sm font-bold font-mono ${tMeta?.color}`}>
                          {current} <ArrowRight size={12} className="inline text-neutral-500 mx-1" /> {t}
                        </span>
                      </div>
                      <p className="text-[11px] text-neutral-400 leading-snug">
                        {TRANSITION_TIP[t] || tMeta?.blurb}
                      </p>
                    </button>
                  )
                })}
              </div>
            )}
          </Card>

          {/* ── Destruction certificate (Q24) ─────────────────────────────── */}
          {(current === 'DESTROY' || lastCert) && (
            <Card className="p-5 space-y-3 border-red-500/20 bg-red-500/[0.03]">
              <div className="flex items-center gap-2">
                <Skull size={14} className="text-red-400" />
                <span className="text-sm font-semibold text-white">Destruction certificate</span>
                {lastCert && (
                  <span className="ml-auto text-[10px] uppercase tracking-widest text-green-400 inline-flex items-center gap-1">
                    <CheckCircle2 size={10} /> minted
                  </span>
                )}
              </div>
              <p className="text-xs text-neutral-400 leading-relaxed max-w-2xl">
                ATF §14.5 line 3: destruction produces a signed certificate
                referencing the final anchor. Keep this JSON forever — it's
                your permanent proof of what existed and when it was destroyed.
                Re-issuable while audit rows remain on disk.
              </p>
              {lastCert && (
                <div className="rounded-lg border border-white/[0.08] bg-black/40 p-3 text-[10px] font-mono text-neutral-400 max-h-40 overflow-auto">
                  <pre className="whitespace-pre-wrap break-all">{JSON.stringify(lastCert, null, 2)}</pre>
                </div>
              )}
              <div className="flex gap-2 flex-wrap">
                {lastCert && (
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => _downloadJson(lastCert,
                      `aegis-destruction-certificate-${new Date().toISOString().slice(0, 10)}.json`)}
                  >
                    <Download size={12} /> Download JSON
                  </Button>
                )}
                {current === 'DESTROY' && (
                  <Button size="sm" variant="secondary" onClick={reissueCert} disabled={certReissuing}>
                    {certReissuing ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
                    {lastCert ? 'Re-issue' : 'Issue certificate'}
                  </Button>
                )}
              </div>
            </Card>
          )}

          {/* ── Audit trail ────────────────────────────────────────────────── */}
          <Card className="p-5 space-y-3">
            <div className="flex items-center gap-2">
              <History size={14} className="text-neutral-400" />
              <span className="text-sm font-semibold text-white">Transition ledger</span>
              <span className="ml-auto text-[10px] text-neutral-500">
                {audit.length} recent event{audit.length === 1 ? '' : 's'}
              </span>
            </div>

            {auditLoading ? (
              <SkeletonLoader count={4} />
            ) : audit.length === 0 ? (
              <p className="text-xs text-neutral-500 italic py-6 text-center">
                No lifecycle transitions ledgered yet.
              </p>
            ) : (
              <div className="divide-y divide-white/[0.04] -mx-5">
                {audit.map((row, i) => {
                  const stateName = (row.action || '').replace('lifecycle_', '').toUpperCase()
                  const stMeta = STATE_META[stateName] || STATE_META.INSTALL
                  const md = row.metadata || {}
                  return (
                    <div key={row.id || i} className="flex items-start gap-3 px-5 py-3 hover:bg-white/[0.02] transition-colors">
                      <span className={`inline-flex items-center gap-1.5 shrink-0 px-2 py-0.5 rounded font-mono text-[10px] font-bold ${stMeta.color} ${stMeta.bg} border ${stMeta.border}`}>
                        {stateName}
                      </span>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 text-[11px] text-neutral-400">
                          <span className="font-mono">{md.from_state || '?'}</span>
                          <ArrowRight size={10} className="text-neutral-600" />
                          <span className="font-mono text-neutral-300">{md.to_state || stateName}</span>
                          {md.action_class && (
                            <span className="text-[10px] px-1.5 py-0 rounded border border-white/[0.08] text-neutral-500">
                              {md.action_class}
                            </span>
                          )}
                        </div>
                        {md.reason && (
                          <p className="text-[11px] text-neutral-500 mt-0.5 leading-snug truncate">
                            {md.reason}
                          </p>
                        )}
                      </div>
                      <span className="shrink-0 text-[10px] text-neutral-600 font-mono">
                        {_relTime(row.created_at)}
                      </span>
                    </div>
                  )
                })}
              </div>
            )}
          </Card>
        </>
      )}

      {/* ── Confirm dialog with reason field ─────────────────────────────── */}
      <ConfirmDialog
        isOpen={!!pendingTarget}
        title={pendingTarget ? `Transition to ${pendingTarget}?` : ''}
        variant={pendingTarget === 'DESTROY' || pendingTarget === 'DECOMMISSION' ? 'danger' : 'default'}
        confirmLabel={
          transitioning
            ? 'Transitioning…'
            : pendingTarget === 'DESTROY'
              ? 'Destroy permanently'
              : `Transition to ${pendingTarget}`
        }
        onConfirm={performTransition}
        onClose={() => { if (!transitioning) setPendingTarget(null) }}
        icon={pendingTarget === 'DESTROY' ? <Skull className="text-red-400 shrink-0 mt-0.5" size={18} /> : undefined}
        description={
          <div className="space-y-3">
            <p className="text-xs text-neutral-300 leading-relaxed">
              {pendingTarget && (TRANSITION_TIP[pendingTarget] || STATE_META[pendingTarget]?.blurb)}
            </p>
            {pendingTarget === 'DESTROY' && (
              <p className="text-xs text-red-300 leading-relaxed bg-red-500/[0.06] border border-red-500/20 rounded p-2">
                DESTROY is terminal. A signed destruction certificate will be
                minted from the final anchor — download it immediately and
                keep it forever. You cannot re-enter any other state after this.
              </p>
            )}
            <label className="block">
              <span className="text-[10px] uppercase tracking-widest text-neutral-500">Reason (recorded in ledger)</span>
              <input
                ref={(el) => el?.focus()}
                type="text"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder={pendingTarget === 'DESTROY' ? 'e.g. contract terminated 2026-08-01' : 'e.g. scheduled quarterly key rotation'}
                className="input-standard mt-1 text-xs w-full"
              />
            </label>
            {transitionError && (
              <p className="text-xs text-red-400 bg-red-500/[0.06] border border-red-500/20 rounded p-2">
                {transitionError}
              </p>
            )}
          </div>
        }
      />
    </div>
  )
}
