import React, { useState, useEffect, useMemo, useCallback } from 'react'
import { killSwitchService, auditService, parseApiError } from '../services/api'
import {
  AlertTriangle, Power, ShieldCheck, AlertOctagon,
  ShieldAlert, Zap, Lock, RefreshCw, Clock,
} from 'lucide-react'
import Card from '../components/Common/Card'
import Button from '../components/Common/Button'
import ConfirmDialog from '../components/Common/ConfirmDialog'
import SkeletonLoader from '../components/Common/SkeletonLoader'
import { useAuth } from '../hooks/useAuth'
import { useRole } from '../hooks/useRole'
import { useSSE } from '../hooks/useSSE'
import { eventBus } from '../lib/eventBus'

/* ── Status row ─────────────────────────────────────────────────────────────── */
function StatusRow({ label, isActive, activeLabel, idleLabel }) {
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-xs text-neutral-500">{label}</span>
        <span className={`text-xs font-bold ${isActive ? 'text-red-400' : 'text-green-400'}`}>
          {isActive ? activeLabel : idleLabel}
        </span>
      </div>
      <div className="h-1 bg-white/[0.05] rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-700 ${isActive ? 'bg-red-500 w-full' : 'bg-green-500 w-1/4'}`}
        />
      </div>
    </div>
  )
}

/* ── Component ──────────────────────────────────────────────────────────────── */
export default function KillSwitch() {
  const { tenant_id, addToast } = useAuth()
  const { canViewKillSwitch } = useRole()
  const [loading,       setLoading]       = useState(true)
  const [hasLoaded,     setHasLoaded]     = useState(false)
  const [actionLoading, setActionLoading] = useState(false)
  const [isActive,      setIsActive]      = useState(false)
  const [redisSynced,   setRedisSynced]   = useState(null)
  const [error,         setError]         = useState('')
  const [confirmOpen,   setConfirmOpen]   = useState(false)
  const [history,       setHistory]       = useState([])

  const fetchStatus = useCallback(async () => {
    if (!tenant_id) return
    try {
      const res = await killSwitchService.getStatus(tenant_id)
      setIsActive(res.data?.status === 'engaged' || res.data?.is_active === true)
      setRedisSynced(true)
      setError('')
    } catch (err) {
      setRedisSynced(false)
      setError(err.message || 'Could not reach kill switch service.')
    } finally {
      setLoading(false)
      setHasLoaded(true)
    }
  }, [tenant_id])

  const fetchHistory = useCallback(async () => {
    try {
      const res = await auditService.getKillSwitchHistory(20)
      const data = res?.data || res || {}
      setHistory(data.items || [])
    } catch {}
  }, [])

  useEffect(() => {
    fetchStatus()
    fetchHistory()
    const interval = setInterval(fetchStatus, 30_000)
    return () => clearInterval(interval)
  }, [tenant_id, fetchStatus, fetchHistory])

  // Real-time SSE — react to the gateway's `kill_switch` channel so the
  // page flips state the instant an operator on another tab engages or
  // disengages isolation. Falls back to the 30s poll above if SSE is
  // momentarily disconnected.
  const sseChannels = useMemo(() => ({
    kill_switch: () => { fetchStatus(); fetchHistory() },
  }), [fetchStatus, fetchHistory])
  useSSE({
    channels: sseChannels,
    onMessage: (evt) => {
      const t = String(evt?.type || '').toLowerCase()
      if (t.includes('kill') || t.includes('isolation')) {
        fetchStatus()
        fetchHistory()
      }
    },
  })
  useEffect(() => {
    const u = eventBus.on('alert', (evt) => {
      const t = String(evt?.type || evt?.action || '').toLowerCase()
      if (t.includes('kill')) { fetchStatus(); fetchHistory() }
    })
    return u
  }, [fetchStatus, fetchHistory])

  // "Last engaged" derived from the activation history. Mirrors the
  // hint we surface in the empty-state CTA when isolation is idle.
  const lastEngaged = useMemo(() => {
    if (!Array.isArray(history) || history.length === 0) return null
    const engage = history.find(r => {
      const a = String(r.action || '').toLowerCase()
      return a === 'kill' || a === 'engage' || a.includes('engage')
    })
    return engage?.timestamp || null
  }, [history])

  const handleToggle = async () => {
    if (!tenant_id) return
    const action = isActive ? 'disengage' : 'engage'
    setActionLoading(true)
    try {
      await killSwitchService.toggle(tenant_id, action)
      setIsActive(action === 'engage')
      addToast?.(
        action === 'engage' ? 'System isolation enforced.' : 'System recovery initialized.',
        action === 'engage' ? 'error' : 'success',
      )
      await fetchStatus()
      await fetchHistory()
    } catch (err) {
      addToast?.(parseApiError(err, 'Kill switch operation failed.'), 'error')
      throw err
    } finally {
      setActionLoading(false)
    }
  }

  if (!canViewKillSwitch) {
    return (
      <div className="space-y-6 animate-fade-in">
        <div className="page-header">
          <div className="flex items-center gap-3">
            <div className="p-2.5 rounded-xl border bg-white/[0.02] border-white/[0.06]">
              <AlertOctagon size={20} className="text-neutral-400" aria-hidden="true" />
            </div>
            <div>
              <h1 className="text-2xl font-bold text-white tracking-tight">Emergency Kill Switch</h1>
              <p className="text-xs text-neutral-500 mt-0.5">Instantly isolate all AI agents in this tenant</p>
            </div>
          </div>
        </div>
        <div className="flex flex-col items-center justify-center py-20 gap-5">
          <div className="w-16 h-16 rounded-2xl bg-red-500/10 border border-red-500/20 flex items-center justify-center">
            <Lock size={24} className="text-red-400" aria-hidden="true" />
          </div>
          <div className="text-center space-y-2">
            <h2 className="text-lg font-bold text-white">Access Restricted</h2>
            <p className="text-sm text-neutral-500 max-w-sm">
              Kill Switch controls are restricted to administrators only.
              Contact your tenant admin if you need emergency access.
            </p>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="max-w-4xl mx-auto space-y-6 animate-fade-in">

      {/* ── Header ── */}
      <div className="page-header">
        <div className="flex items-center gap-3">
          <div className={`p-2.5 rounded-xl border transition-all duration-500 ${
            isActive
              ? 'bg-red-500/10 border-red-500/30'
              : 'bg-white/[0.02] border-white/[0.06]'
          }`}>
            <AlertOctagon size={20} className={isActive ? 'text-red-400' : 'text-neutral-400'} aria-hidden="true" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-white tracking-tight">Emergency Kill Switch</h1>
            <p className="text-xs text-neutral-500 mt-0.5">Instantly isolate all AI agents in this tenant</p>
          </div>
        </div>
        <button
          type="button"
          onClick={fetchStatus}
          aria-label="Refresh kill switch status"
          className="p-2 rounded-lg text-neutral-500 hover:text-white hover:bg-white/[0.05] transition-colors"
        >
          <RefreshCw size={15} aria-hidden="true" />
        </button>
      </div>

      {/* ── Error ── */}
      {error && (
        <div className="error-banner" role="alert">
          <div className="flex items-center gap-2">
            <AlertTriangle size={14} className="text-red-400 shrink-0" aria-hidden="true" />
            <p className="text-xs text-red-400">{error}</p>
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
        {/* ── Main action card ── */}
        <div className="xl:col-span-2">
          <Card className={`relative overflow-hidden transition-all duration-700 ${isActive ? 'border-red-500/20' : ''}`}>
            {isActive && (
              <div className="absolute inset-0 bg-gradient-to-br from-red-500/[0.06] via-transparent to-transparent pointer-events-none" aria-hidden="true" />
            )}

            <div className="relative flex flex-col items-center text-center space-y-8 py-6">
              {/* Status indicator */}
              <div className={`w-32 h-32 rounded-full border-2 flex items-center justify-center transition-all duration-700 ${
                isActive
                  ? 'border-red-500 bg-red-500/15 text-red-400'
                  : 'border-white/[0.08] bg-white/[0.02] text-neutral-600'
              }`}>
                <Power size={52} className={isActive ? 'animate-pulse' : ''} aria-hidden="true" />
              </div>

              {/* Status text */}
              <div className="space-y-3">
                <p className="label-standard">Global Containment Status</p>
                <h2 className={`text-3xl font-black tracking-tight uppercase ${isActive ? 'text-red-400' : 'text-white'}`}>
                  {isActive ? 'System Isolated' : 'All Systems Nominal'}
                </h2>
                <div className="flex items-center justify-center gap-3 flex-wrap">
                  <span className="px-3 py-1 rounded-full bg-white/[0.04] border border-white/[0.06] text-xs text-neutral-400">
                    Tenant: {tenant_id || 'SYS_GLOBAL'}
                  </span>
                  <span className="px-3 py-1 rounded-full bg-white/[0.04] border border-white/[0.06] text-xs text-neutral-400 flex items-center gap-1.5">
                    <Lock size={10} aria-hidden="true" /> AES-256
                  </span>
                </div>
              </div>

              {/* Description */}
              <p className="text-xs text-neutral-500 max-w-md leading-relaxed">
                {isActive
                  ? 'Isolation is active across all microservices. The gateway is returning 403 for all tenant ingress.'
                  : 'Activating the kill switch sends a high-priority interrupt to the OPA cluster, instantly suspending all agent behaviors.'}
              </p>

              {/* Idle CTA — surface the last-engaged timestamp so the
                  operator can see the switch state at a glance. */}
              {!isActive && hasLoaded && (
                <p className="text-[11px] text-neutral-600 font-mono">
                  Kill switch idle — last engaged:{' '}
                  <span className="text-neutral-400">
                    {lastEngaged ? new Date(lastEngaged).toLocaleString() : 'never'}
                  </span>
                </p>
              )}

              {/* Action button */}
              <div className="w-full max-w-xs">
                {loading ? (
                  <div className="h-12 rounded-xl bg-white/[0.04] animate-pulse" />
                ) : (
                  <Button
                    variant={isActive ? 'secondary' : 'danger'}
                    loading={actionLoading}
                    onClick={() => setConfirmOpen(true)}
                    className="w-full h-12 rounded-xl text-sm font-bold"
                  >
                    {isActive ? (
                      <><RefreshCw size={16} aria-hidden="true" /> Restore Systems</>
                    ) : (
                      <><ShieldAlert size={16} aria-hidden="true" /> Trigger Global Isolation</>
                    )}
                  </Button>
                )}
              </div>
            </div>
          </Card>
        </div>

        {/* ── Side panels ── */}
        <div className="space-y-4">
          <Card title="Containment Status">
            <div className="space-y-5">
              <StatusRow label="Edge Gateway"    isActive={isActive} activeLabel="BLOCKED"   idleLabel="NOMINAL" />
              <StatusRow label="Behavior Kernel" isActive={isActive} activeLabel="SUSPENDED" idleLabel="NOMINAL" />
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-xs text-neutral-500">Redis State Sync</span>
                  <div className="flex items-center gap-1.5">
                    {redisSynced === null ? (
                      <div className="w-1.5 h-1.5 rounded-full bg-neutral-500 animate-pulse" aria-hidden="true" />
                    ) : (
                      <div className={`w-1.5 h-1.5 rounded-full ${redisSynced ? 'bg-green-500' : 'bg-red-500'}`} aria-hidden="true" />
                    )}
                    <span className={`text-xs font-bold ${redisSynced === null ? 'text-neutral-500' : redisSynced ? 'text-green-400' : 'text-red-400'}`}>
                      {redisSynced === null ? 'CHECKING…' : redisSynced ? 'SYNCHRONIZED' : 'UNREACHABLE'}
                    </span>
                  </div>
                </div>
              </div>
            </div>
          </Card>

          <Card title="Protocol Integrity">
            <div className="flex items-start gap-3 p-3 bg-blue-500/[0.04] border border-blue-500/10 rounded-xl">
              <Zap size={15} className="text-blue-400 shrink-0 mt-0.5" aria-hidden="true" />
              <p className="text-xs text-blue-400/80 leading-relaxed">
                Isolation status is cryptographically signed and stored in the global audit chain. Any toggle event triggers forensic capture.
              </p>
            </div>
          </Card>
        </div>
      </div>

      {/* ── Activation History ── */}
      <Card title="Activation History">
        {!hasLoaded ? (
          <SkeletonLoader variant="row" count={3} />
        ) : history.length === 0 ? (
          <p className="text-xs text-neutral-600 py-2">No kill-switch activations recorded — last engaged: never.</p>
        ) : (
          <div className="divide-y divide-white/[0.04]">
            {history.map((row, i) => (
              <div key={row.id || i} className="flex items-center gap-3 py-2.5 text-xs">
                <span className={`w-2 h-2 rounded-full shrink-0 ${row.action === 'kill' ? 'bg-red-500' : 'bg-green-500'}`} aria-hidden="true" />
                <span className="text-neutral-300 flex-1 font-mono">{row.action || '—'}</span>
                <span className="text-neutral-500 font-mono">{row.actor || row.user_id?.slice(0, 8) || '—'}</span>
                <span className="text-neutral-600 font-mono">
                  {row.timestamp ? new Date(row.timestamp).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—'}
                </span>
              </div>
            ))}
          </div>
        )}
      </Card>

      {/* ── Confirmation dialog ── Engage is destructive (variant="danger");
          restore uses the default style. ConfirmDialog handles the busy
          spinner + close-on-success, so handleToggle just needs to throw
          on failure for the dialog to stay open. */}
      <ConfirmDialog
        isOpen={confirmOpen}
        title={isActive ? 'Restore Systems?' : 'Trigger Global Isolation?'}
        description={isActive
          ? `This will lift the isolation barrier and restore connectivity for all agents in tenant ${tenant_id || 'SYS_GLOBAL'}. Verify the threat has been resolved before proceeding.`
          : `Confirm global isolation for tenant ${tenant_id || 'SYS_GLOBAL'}. This will immediately block all AI agent traffic across all services and requires admin intervention to reverse. It will be logged in the audit chain.`
        }
        confirmLabel={isActive ? 'Restore Systems' : 'Confirm Isolation'}
        variant={isActive ? 'default' : 'danger'}
        onConfirm={handleToggle}
        onClose={() => setConfirmOpen(false)}
      />
    </div>
  )
}
