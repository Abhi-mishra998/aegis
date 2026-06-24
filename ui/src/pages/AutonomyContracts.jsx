import React, { useState, useEffect, useCallback, useRef } from 'react'
import { Link } from 'react-router-dom'
import { RefreshCw, Shield, AlertTriangle, UserCheck, Plus, Trash2, Lock, ArrowRight, PlayCircle } from 'lucide-react'
import { autonomyService } from '../services/api'
import Modal from '../components/Common/Modal'
import ConfirmDialog from '../components/Common/ConfirmDialog'
import { useAuth } from '../hooks/useAuth'

const defaultContract = {
  agent_id: '',
  name: '',
  enabled: true,
  allowed_actions: [],
  denied_actions: [],
  approval_required: [],
  max_runtime_seconds: 30,
  max_tool_calls: 10,
  max_cost_usd: 50,
  max_autonomy_level: 2,
  escalation_triggers: [],
  notes: '',
}

function CSV({ value, onChange, placeholder }) {
  return (
    <input name="input"
      type="text"
      value={(value || []).join(', ')}
      onChange={(e) => onChange(
        e.target.value.split(',').map((s) => s.trim()).filter(Boolean),
      )}
      placeholder={placeholder}
      className="w-full bg-black border border-white/10 rounded px-2 py-1 text-xs text-white font-mono"
    />
  )
}

export default function AutonomyContracts() {
  const { addToast } = useAuth()
  const [contracts, setContracts]     = useState([])
  const [violations, setViolations]   = useState([])
  const [overrides, setOverrides]     = useState([])
  const [loading, setLoading]         = useState(true)
  const [editing, setEditing]         = useState(null)
  const [saving, setSaving]           = useState(false)
  const [error, setError]             = useState('')
  const [disableTarget, setDisableTarget] = useState(null)
  // First load = full skeleton; subsequent SSE/poll refetches swap data silently.
  const hasLoadedRef = useRef(false)

  const fetchAll = useCallback(async () => {
    if (!hasLoadedRef.current) setLoading(true)
    setError('')
    try {
      const [c, v, o] = await Promise.all([
        autonomyService.listContracts(),
        autonomyService.listViolations(1440),
        autonomyService.listOverrides({ minutes: 10080, limit: 100 }),
      ])
      setContracts(c?.data || [])
      setViolations(v?.data || [])
      setOverrides(o?.data || [])
    } catch (e) {
      // 2026-05-14: surface failure instead of console.warn — autonomy is a
      // security-critical surface; operators must know if it's unreachable.
      setError(e?.message || 'Autonomy service unreachable')
    }
    finally {
      setLoading(false)
      hasLoadedRef.current = true
    }
  }, [])

  useEffect(() => {
    fetchAll()
    const t = setInterval(fetchAll, 30_000)
    return () => clearInterval(t)
  }, [fetchAll])

  const save = async () => {
    if (!editing) return
    setSaving(true)
    try {
      if (editing.id) {
        await autonomyService.updateContract(editing.id, editing)
      } else {
        await autonomyService.createContract(editing)
      }
      setEditing(null)
      await fetchAll()
    } catch (e) { addToast(e?.message || 'Save failed', 'error') }
    finally { setSaving(false) }
  }

  const disable = (id) => setDisableTarget(id)

  const confirmDisable = async () => {
    try {
      await autonomyService.disableContract(disableTarget)
      addToast('Contract disabled', 'success')
      await fetchAll()
    } catch (e) {
      addToast(e?.message || 'Failed to disable contract', 'error')
    }
  }

  return (
    <div className="space-y-4 animate-fade-in">
      <div className="page-header">
        <div>
          <h1 className="text-2xl font-bold text-white flex items-center gap-2"><Lock size={20} /> Autonomy Contracts</h1>
          <p className="text-xs text-neutral-500 mt-1">Bounded autonomy · runtime ceilings · approval chains · human override log</p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => setEditing({ ...defaultContract })}
            className="flex items-center gap-2 px-3 py-2 rounded-lg bg-blue-500/20 border border-blue-500/30 text-blue-200 text-xs font-bold hover:bg-blue-500/30">
            <Plus size={13} />
            New Contract
          </button>
          <button onClick={fetchAll} disabled={loading}
            className="flex items-center gap-2 px-3 py-2 rounded-lg bg-white/5 border border-white/10 text-xs text-neutral-300 hover:bg-white/10 disabled:opacity-50">
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
            Refresh
          </button>
        </div>
      </div>

      {error && (
        <div className="px-3 py-2 rounded-lg border border-red-500/30 bg-red-500/10 text-xs text-red-400 flex items-center justify-between" role="alert">
          <span>Autonomy: {error}</span>
          <button onClick={fetchAll} className="text-red-300 underline">Retry</button>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2 rounded-2xl border border-white/10 bg-white/[0.02] p-4">
          <div className="flex items-center gap-2 mb-3">
            <Shield size={13} className="text-emerald-400" />
            <span className="text-sm font-semibold text-white">Active Contracts</span>
            <span className="ml-auto text-[10px] font-mono text-neutral-600">{contracts.length}</span>
          </div>
          {loading && !contracts.length ? (
            <div className="space-y-2 py-2" role="status" aria-label="Loading contracts">
              {[0, 1, 2].map((i) => (
                <div key={i} className="flex items-center gap-3 py-2 animate-pulse">
                  <div className="h-3 bg-white/[0.06] rounded w-24" />
                  <div className="h-3 bg-white/[0.04] rounded w-32" />
                  <div className="h-3 bg-white/[0.04] rounded flex-1" />
                  <div className="h-4 w-16 bg-white/[0.04] rounded-full" />
                </div>
              ))}
            </div>
          ) : !contracts.length ? (
            <div className="rounded-xl border border-dashed border-white/10 bg-white/[0.015] p-6 text-center space-y-3">
              <Lock size={22} className="text-neutral-500 mx-auto" aria-hidden="true" />
              <div className="text-xs text-neutral-300 font-semibold">No autonomy contracts yet</div>
              <p className="text-xs text-neutral-500 max-w-md mx-auto">
                Autonomy contracts define hard ceilings on what an agent may attempt — allowed/denied actions, runtime caps, cost caps, approval triggers.
              </p>
              <div className="flex items-center justify-center gap-2 flex-wrap pt-2">
                <button
                  onClick={() => setEditing({ ...defaultContract })}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-blue-500/20 border border-blue-500/40 text-blue-100 text-xs font-medium hover:bg-blue-500/30"
                >
                  <Plus size={11} aria-hidden="true" />
                  New Contract
                </button>
                <Link
                  to="/policies?tab=editor"
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-white/10 text-neutral-200 text-xs hover:bg-white/[0.04]"
                >
                  <ArrowRight size={11} aria-hidden="true" />
                  Policy Editor
                </Link>
              </div>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-white/10 text-[10px] uppercase tracking-wider text-neutral-500">
                    <th className="text-left py-2 pr-3">Contract</th>
                    <th className="text-left py-2 pr-3">Agent</th>
                    <th className="text-left py-2 pr-3">Allowed Actions</th>
                    <th className="text-left py-2 pr-3">Status</th>
                    <th className="text-left py-2 pr-3">Expires</th>
                    <th className="text-left py-2 pr-3">Created</th>
                    <th className="text-right py-2"></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/5">
                  {contracts.map((c) => (
                    <tr key={c.id}>
                      <td className="py-2 pr-3 font-mono text-neutral-300" title={c.id}>{(c.id || '').slice(0, 8)}…</td>
                      <td className="py-2 pr-3 font-mono text-neutral-400" title={c.agent_id}>{(c.agent_id || '').slice(0, 10)}…</td>
                      <td className="py-2 pr-3 text-neutral-400 max-w-[220px] truncate" title={(c.allowed_actions || []).join(', ')}>
                        {(c.allowed_actions || []).slice(0, 4).join(', ') || <span className="text-neutral-700">—</span>}
                      </td>
                      <td className="py-2 pr-3">
                        <span className={`inline-flex items-center gap-1 text-[10px] font-semibold px-2 py-0.5 rounded-full border ${c.enabled ? 'bg-green-500/10 text-green-400 border-green-500/20' : 'bg-neutral-500/10 text-neutral-400 border-neutral-500/20'}`}>
                          <span className={`w-1.5 h-1.5 rounded-full ${c.enabled ? 'bg-green-500' : 'bg-neutral-600'}`} />
                          {c.enabled ? 'active' : 'disabled'}
                        </span>
                      </td>
                      <td className="py-2 pr-3 text-neutral-500">{c.expires_at ? new Date(c.expires_at).toLocaleDateString() : <span className="text-neutral-700">never</span>}</td>
                      <td className="py-2 pr-3 text-neutral-500">{c.created_at ? new Date(c.created_at).toLocaleDateString() : '—'}</td>
                      <td className="py-2 text-right whitespace-nowrap">
                        <button onClick={() => setEditing({ ...c })} className="text-blue-400 hover:text-blue-300 px-2">Edit</button>
                        <button onClick={() => disable(c.id)} className="text-red-400 hover:text-red-300 px-2 inline-flex items-center gap-1">
                          <Trash2 size={11} /> Disable
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="space-y-4">
          <div className="rounded-2xl border border-red-500/15 bg-red-500/[0.04] p-4">
            <div className="flex items-center gap-2 mb-3">
              <AlertTriangle size={13} className="text-red-400" />
              <span className="text-sm font-semibold text-white">Recent Violations (24h)</span>
              <span className="ml-auto text-[10px] font-mono text-neutral-600">{violations.length}</span>
            </div>
            <div className="max-h-[200px] overflow-y-auto divide-y divide-white/5">
              {violations.slice(0, 30).map((v) => (
                <div key={v.id} className="py-1.5 text-[11px] font-mono">
                  <div className="flex justify-between">
                    <span className="text-red-300">{v.rule}</span>
                    <span className="text-neutral-600">{new Date(v.detected_at).toLocaleTimeString()}</span>
                  </div>
                  <p className="text-neutral-500 truncate">agent {v.agent_id?.slice(0, 12)} · req {v.request_id?.slice(0, 14)}</p>
                </div>
              ))}
              {!violations.length && <p className="text-xs text-neutral-600 text-center py-2">no violations</p>}
            </div>
          </div>

          <div className="rounded-2xl border border-purple-500/15 bg-purple-500/[0.04] p-4">
            <div className="flex items-center gap-2 mb-3">
              <UserCheck size={13} className="text-purple-300" />
              <span className="text-sm font-semibold text-white">Human Override Timeline (7d)</span>
              <span className="ml-auto text-[10px] font-mono text-neutral-600">{overrides.length}</span>
            </div>
            <div className="max-h-[260px] overflow-y-auto divide-y divide-white/5">
              {overrides.slice(0, 30).map((o) => (
                <div key={o.id} className="py-1.5 text-[11px] font-mono">
                  <div className="flex justify-between">
                    <span className="text-purple-300">{o.event_type}</span>
                    <span className="text-neutral-600">{new Date(o.occurred_at).toLocaleString()}</span>
                  </div>
                  <p className="text-neutral-300 truncate">{o.actor} · {o.target_kind} → {o.target_id?.slice(0, 12)}</p>
                  {o.reason && <p className="text-neutral-500 truncate">{o.reason}</p>}
                </div>
              ))}
              {!overrides.length && <p className="text-xs text-neutral-600 text-center py-2">no overrides logged</p>}
            </div>
          </div>
        </div>
      </div>

      <ConfirmDialog
        isOpen={disableTarget !== null}
        title="Disable Contract"
        description="Disable this contract? The agent will lose its guardrails until a new contract is created."
        confirmLabel="Disable"
        variant="danger"
        onConfirm={confirmDisable}
        onClose={() => setDisableTarget(null)}
        onError={(e) => { addToast(e?.message || 'Disable failed', 'error'); setDisableTarget(null) }}
      />

      {/*
        New / Edit Contract dialog.
        2026-05-15: migrated from a bespoke `fixed inset-0` div to the
        portaled <Modal> primitive. The bespoke version rendered inside the
        page subtree and could collide with ancestor stacking contexts,
        causing the form to appear glued to the navbar with its bottom
        clipped. The Modal primitive portals to document.body, centers via
        flex on the viewport, traps focus, locks body scroll, and scrolls
        its body region internally so even tall forms always fit.
      */}
      <Modal
        isOpen={!!editing}
        onClose={() => setEditing(null)}
        title={editing?.id ? 'Edit Contract' : 'New Contract'}
        description="Set bounded autonomy guardrails — allowed/denied actions, runtime ceilings, and approval triggers."
        size="2xl"
        footer={
          <>
            <button
              type="button"
              onClick={() => setEditing(null)}
              className="w-full sm:w-auto px-4 py-2 rounded-lg bg-white/[0.04] border border-white/10 text-xs font-semibold text-neutral-200 hover:bg-white/[0.08] focus-visible:ring-2 focus-visible:ring-white/30 transition-colors"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={save}
              disabled={saving}
              className="w-full sm:w-auto px-4 py-2 rounded-lg bg-blue-500/20 border border-blue-500/40 text-xs font-bold text-blue-100 hover:bg-blue-500/30 focus-visible:ring-2 focus-visible:ring-blue-400/40 disabled:opacity-50 disabled:cursor-not-allowed transition-colors inline-flex items-center justify-center gap-2"
            >
              {saving && (
                <span
                  className="inline-block w-3 h-3 rounded-full border-2 border-current border-r-transparent animate-spin"
                  aria-hidden="true"
                />
              )}
              {saving ? 'Saving…' : 'Save contract'}
            </button>
          </>
        }
      >
        {editing && (
          <form
            className="grid grid-cols-1 sm:grid-cols-2 gap-4"
            onSubmit={(e) => { e.preventDefault(); save() }}
          >
            <div className="flex flex-col gap-1.5">
              <label className="text-[10px] font-bold uppercase tracking-[0.12em] text-neutral-500">
                Agent ID (uuid)
              </label>
              <input name="agent_id"
                value={editing.agent_id}
                onChange={(e) => setEditing({ ...editing, agent_id: e.target.value })}
                placeholder="00000000-0000-0000-0000-000000000000"
                className="input-standard font-mono"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <label className="text-[10px] font-bold uppercase tracking-[0.12em] text-neutral-500">
                Name
              </label>
              <input name="name"
                value={editing.name}
                onChange={(e) => setEditing({ ...editing, name: e.target.value })}
                placeholder="prod_safety_contract"
                className="input-standard"
              />
            </div>
            <div className="flex flex-col gap-1.5 sm:col-span-2">
              <label className="text-[10px] font-bold uppercase tracking-[0.12em] text-neutral-500">
                Allowed actions <span className="text-neutral-600 normal-case font-medium tracking-normal">(comma-separated; * = wildcard)</span>
              </label>
              <CSV
                value={editing.allowed_actions}
                onChange={(v) => setEditing({ ...editing, allowed_actions: v })}
                placeholder="read_*, summarize, query"
              />
            </div>
            <div className="flex flex-col gap-1.5 sm:col-span-2">
              <label className="text-[10px] font-bold uppercase tracking-[0.12em] text-neutral-500">
                Denied actions
              </label>
              <CSV
                value={editing.denied_actions}
                onChange={(v) => setEditing({ ...editing, denied_actions: v })}
                placeholder="external_http_calls, delete_*"
              />
            </div>
            <div className="flex flex-col gap-1.5 sm:col-span-2">
              <label className="text-[10px] font-bold uppercase tracking-[0.12em] text-neutral-500">
                Approval required
              </label>
              <CSV
                value={editing.approval_required}
                onChange={(v) => setEditing({ ...editing, approval_required: v })}
                placeholder="payment_above_10000"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <label className="text-[10px] font-bold uppercase tracking-[0.12em] text-neutral-500">
                Max runtime (s)
              </label>
              <input name="max_runtime_seconds"
                type="number"
                inputMode="numeric"
                value={editing.max_runtime_seconds || ''}
                onChange={(e) => setEditing({ ...editing, max_runtime_seconds: Number(e.target.value) || null })}
                className="input-standard font-mono"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <label className="text-[10px] font-bold uppercase tracking-[0.12em] text-neutral-500">
                Max tool calls
              </label>
              <input name="max_tool_calls"
                type="number"
                inputMode="numeric"
                value={editing.max_tool_calls || ''}
                onChange={(e) => setEditing({ ...editing, max_tool_calls: Number(e.target.value) || null })}
                className="input-standard font-mono"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <label className="text-[10px] font-bold uppercase tracking-[0.12em] text-neutral-500">
                Max cost (USD)
              </label>
              <input name="max_cost_usd"
                type="number"
                inputMode="decimal"
                step="0.01"
                value={editing.max_cost_usd || ''}
                onChange={(e) => setEditing({ ...editing, max_cost_usd: Number(e.target.value) || null })}
                className="input-standard font-mono"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <label className="text-[10px] font-bold uppercase tracking-[0.12em] text-neutral-500">
                Max autonomy level <span className="text-neutral-600 normal-case font-medium tracking-normal">(1–5)</span>
              </label>
              <input name="max_autonomy_level"
                type="number"
                inputMode="numeric"
                min={1}
                max={5}
                value={editing.max_autonomy_level}
                onChange={(e) => setEditing({ ...editing, max_autonomy_level: Number(e.target.value) })}
                className="input-standard font-mono"
              />
            </div>
            <div className="flex flex-col gap-1.5 sm:col-span-2">
              <label className="text-[10px] font-bold uppercase tracking-[0.12em] text-neutral-500">
                Notes
              </label>
              <textarea name="notes"
                rows={3}
                value={editing.notes || ''}
                onChange={(e) => setEditing({ ...editing, notes: e.target.value })}
                placeholder="Optional context for auditors."
                className="input-standard resize-y min-h-[72px]"
              />
            </div>
            {/* Hidden submit so Enter from any input triggers save (matches the
                visible Save button in the footer). */}
            <button type="submit" className="hidden" tabIndex={-1} aria-hidden="true" />
          </form>
        )}
      </Modal>
    </div>
  )
}
