import React, { useCallback, useEffect, useState } from 'react'
import {
  AlertTriangle,
  Check,
  Copy,
  DollarSign,
  Loader2,
  Plus,
  RefreshCw,
  Shield,
  Trash2,
  Users,
} from 'lucide-react'
import { teamService } from '../services/api'
import { useAuth } from '../hooks/useAuth'
import Button from '../components/Common/Button'
import Card from '../components/Common/Card'

/* ───────── helpers ─────────────────────────────────────────────────── */

function fmtUSD(n) {
  if (n == null) return '—'
  const v = Number(n) || 0
  if (v >= 1000) return `$${(v / 1000).toFixed(1)}K`
  return `$${v.toFixed(2)}`
}

function BudgetBar({ spent, budget }) {
  if (budget == null || budget <= 0) {
    return <span className="text-[10px] text-neutral-600">no cap</span>
  }
  const pct = Math.min(100, (Number(spent) / Number(budget)) * 100)
  const color =
    pct >= 95 ? 'bg-red-500/70' : pct >= 70 ? 'bg-amber-400/70' : 'bg-green-500/70'
  return (
    <div className="w-24 space-y-1">
      <div className="h-1 rounded-full bg-white/[0.04] overflow-hidden">
        <div className={`h-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <div className="text-[10px] text-neutral-500">
        {fmtUSD(spent)} <span className="text-neutral-700">/</span> {fmtUSD(budget)}
      </div>
    </div>
  )
}

/* ───────── Add-employee modal ───────────────────────────────────────── */

function AddEmployeeModal({ onClose, onMinted }) {
  const [email, setEmail] = useState('')
  const [name, setName] = useState('')
  const [dailyBudget, setDailyBudget] = useState('')
  const [monthlyBudget, setMonthlyBudget] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [minted, setMinted] = useState(null) // { api_key, key_prefix, email, … }
  const [copied, setCopied] = useState(false)

  const submit = async (e) => {
    e?.preventDefault?.()
    if (!email.trim()) { setError('Email is required.'); return }
    setBusy(true); setError('')
    try {
      const payload = {
        email: email.trim().toLowerCase(),
        name: name.trim() || undefined,
        daily_budget_usd:   dailyBudget   === '' ? null : Number(dailyBudget),
        monthly_budget_usd: monthlyBudget === '' ? null : Number(monthlyBudget),
      }
      const resp = await teamService.mintEmployeeKey(payload)
      const data = resp?.data || resp
      if (!data?.api_key) throw new Error('Backend returned no API key')
      setMinted(data)
      onMinted?.()
    } catch (err) {
      setError(err?.message || 'Failed to mint employee key.')
    } finally {
      setBusy(false)
    }
  }

  const copy = () => {
    if (!minted?.api_key) return
    try {
      navigator.clipboard.writeText(minted.api_key)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch (_) {}
  }

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
    >
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" />
      <div
        onClick={(e) => e.stopPropagation()}
        className="relative bg-[#0a0a0a] border border-white/[0.08] rounded-2xl shadow-2xl p-6 w-full max-w-md mx-4 space-y-4"
      >
        {!minted && (
          <>
            <div>
              <h2 className="text-sm font-semibold text-white flex items-center gap-2">
                <Plus size={14} /> Add employee
              </h2>
              <p className="text-xs text-neutral-500 mt-1">
                Aegis mints a virtual key (<code>acp_emp_…</code>) for this employee. They drop
                it into their Anthropic SDK in place of the corporate key — all calls flow
                through Aegis and roll up here.
              </p>
            </div>

            <form onSubmit={submit} className="space-y-3">
              <div className="space-y-1">
                <label className="label-standard" htmlFor="emp-email">Email <span className="text-red-400">*</span></label>
                <input
                  id="emp-email"
                  type="email"
                  required
                  autoFocus
                  className="input-standard h-10"
                  placeholder="alice@acme.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />
              </div>
              <div className="space-y-1">
                <label className="label-standard" htmlFor="emp-name">Display name (optional)</label>
                <input
                  id="emp-name"
                  type="text"
                  className="input-standard h-10"
                  placeholder="Alice Liu"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1">
                  <label className="label-standard" htmlFor="emp-daily">Daily cap (USD)</label>
                  <input
                    id="emp-daily"
                    type="number"
                    min="0"
                    step="0.01"
                    className="input-standard h-10"
                    placeholder="50.00"
                    value={dailyBudget}
                    onChange={(e) => setDailyBudget(e.target.value)}
                  />
                </div>
                <div className="space-y-1">
                  <label className="label-standard" htmlFor="emp-monthly">Monthly cap (USD)</label>
                  <input
                    id="emp-monthly"
                    type="number"
                    min="0"
                    step="0.01"
                    className="input-standard h-10"
                    placeholder="1000.00"
                    value={monthlyBudget}
                    onChange={(e) => setMonthlyBudget(e.target.value)}
                  />
                </div>
              </div>

              {error && (
                <div className="text-xs text-red-400 bg-red-500/[0.06] border border-red-500/20 rounded-xl p-2.5">
                  {error}
                </div>
              )}

              <div className="flex justify-end gap-2 pt-1">
                <Button variant="ghost" type="button" size="sm" onClick={onClose} disabled={busy}>Cancel</Button>
                <Button type="submit" size="sm" loading={busy} disabled={!email.trim() || busy}>
                  Mint key
                </Button>
              </div>
            </form>
          </>
        )}

        {minted && (
          <>
            <div>
              <h2 className="text-sm font-semibold text-white flex items-center gap-2">
                <Check size={14} className="text-green-400" /> Employee key minted
              </h2>
              <p className="text-xs text-neutral-500 mt-1">
                Copy this key now — it cannot be shown again. Hand it to{' '}
                <code className="text-white">{minted.subject_email || minted.email}</code> and tell them to
                replace their <code>ANTHROPIC_API_KEY</code> + point the SDK at{' '}
                <code>https://ha.aegisagent.in</code>.
              </p>
            </div>

            <div className="relative">
              <div className="border border-white/[0.07] rounded-xl bg-[#050505] font-mono text-[12px] text-neutral-200 px-3 py-3 break-all pr-12">
                {minted.api_key}
              </div>
              <button
                type="button"
                onClick={copy}
                aria-label="Copy key"
                className="absolute top-2 right-2 p-1.5 rounded-md bg-black/40 border border-white/10 text-neutral-300 hover:bg-black/60 hover:text-white"
              >
                {copied ? <Check size={13} /> : <Copy size={13} />}
              </button>
            </div>

            <div className="flex justify-end pt-1">
              <Button size="sm" onClick={onClose}>Done</Button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

/* ───────── /team page ───────────────────────────────────────────────── */

export default function Team() {
  const { role } = useAuth() || {}
  const isAdmin = role === 'OWNER' || role === 'ADMIN'

  const [employees, setEmployees] = useState([])
  const [loading,   setLoading]   = useState(true)
  const [error,     setError]     = useState('')
  const [showAdd,   setShowAdd]   = useState(false)

  const load = useCallback(async () => {
    setLoading(true); setError('')
    try {
      const resp = await teamService.listEmployees()
      const rows = resp?.data || resp || []
      setEmployees(Array.isArray(rows) ? rows : [])
    } catch (err) {
      setError(err?.message || 'Failed to load team.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const revoke = async (keyId, email) => {
    if (!window.confirm(`Revoke ${email}'s Aegis key? Their Anthropic SDK calls will start failing immediately.`)) return
    try {
      await teamService.revokeKey(keyId)
      await load()
    } catch (err) {
      setError(err?.message || 'Revoke failed.')
    }
  }

  const total = employees.length
  const todayTotal = employees.reduce((s, e) => s + (Number(e.today_usd) || 0), 0)
  const monthTotal = employees.reduce((s, e) => s + (Number(e.month_usd) || 0), 0)
  const overBudget = employees.filter(
    (e) =>
      (e.daily_budget_usd   != null && Number(e.today_usd) >= 0.95 * Number(e.daily_budget_usd)) ||
      (e.monthly_budget_usd != null && Number(e.month_usd) >= 0.95 * Number(e.monthly_budget_usd)),
  ).length

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div className="space-y-1">
          <h1 className="text-2xl font-bold tracking-tight text-white flex items-center gap-2">
            <Users size={20} /> Team
          </h1>
          <p className="text-xs text-neutral-400 max-w-xl">
            Per-employee Claude usage routed through Aegis. Each employee uses an
            <code className="mx-1 text-neutral-300">acp_emp_…</code>
            virtual key — their corporate Anthropic key never leaves your workspace.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={load} disabled={loading} aria-label="Refresh">
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          </Button>
          {isAdmin && (
            <Button size="sm" onClick={() => setShowAdd(true)}>
              <Plus size={14} /> Add employee
            </Button>
          )}
        </div>
      </div>

      {/* Aggregate KPIs */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Card>
          <div className="space-y-1">
            <div className="text-[10px] uppercase tracking-widest text-neutral-500">Employees</div>
            <div className="text-3xl font-bold text-white">{total}</div>
          </div>
        </Card>
        <Card>
          <div className="space-y-1">
            <div className="text-[10px] uppercase tracking-widest text-neutral-500">Spend today</div>
            <div className="text-3xl font-bold text-white">{fmtUSD(todayTotal)}</div>
          </div>
        </Card>
        <Card>
          <div className="space-y-1">
            <div className="text-[10px] uppercase tracking-widest text-neutral-500">Spend this month</div>
            <div className="text-3xl font-bold text-white">{fmtUSD(monthTotal)}</div>
          </div>
        </Card>
        <Card>
          <div className="space-y-1">
            <div className="text-[10px] uppercase tracking-widest text-neutral-500">≥95% of cap</div>
            <div className={`text-3xl font-bold ${overBudget > 0 ? 'text-amber-400' : 'text-white'}`}>
              {overBudget}
            </div>
          </div>
        </Card>
      </div>

      {error && (
        <div className="error-banner" role="alert">
          <div className="flex items-center gap-3">
            <AlertTriangle size={15} className="text-red-400 shrink-0" />
            <p className="text-xs text-red-400">{error}</p>
          </div>
        </div>
      )}

      {/* Per-employee table */}
      <Card title="Members" icon={Shield}>
        {loading ? (
          <div className="text-xs text-neutral-500 py-10 text-center">
            <Loader2 size={20} className="animate-spin mx-auto mb-2" />
            Loading team…
          </div>
        ) : employees.length === 0 ? (
          <div className="text-xs text-neutral-500 py-10 text-center space-y-3">
            <Users size={26} className="text-neutral-700 mx-auto" />
            <div>No employees on Aegis yet.</div>
            {isAdmin && (
              <Button size="sm" onClick={() => setShowAdd(true)}>
                <Plus size={14} /> Add your first employee
              </Button>
            )}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-[10px] uppercase tracking-widest text-neutral-500">
                <tr className="text-left border-b border-white/[0.05]">
                  <th className="py-2 pr-3">Employee</th>
                  <th className="py-2 pr-3">Key</th>
                  <th className="py-2 pr-3">Today</th>
                  <th className="py-2 pr-3">This month</th>
                  <th className="py-2 pr-3">Daily cap</th>
                  <th className="py-2 pr-3">Monthly cap</th>
                  {isAdmin && <th className="py-2 pr-2 text-right">Actions</th>}
                </tr>
              </thead>
              <tbody>
                {employees.map((e) => (
                  <tr key={e.key_id} className="border-b border-white/[0.04] last:border-b-0">
                    <td className="py-3 pr-3">
                      <div className="text-neutral-200 font-medium">{e.name || e.email.split('@')[0]}</div>
                      <div className="text-[10px] text-neutral-600">{e.email}</div>
                    </td>
                    <td className="py-3 pr-3 font-mono text-[10px] text-neutral-500">
                      {e.key_prefix}…
                    </td>
                    <td className="py-3 pr-3 font-mono text-neutral-200">
                      <span className="inline-flex items-center gap-1">
                        <DollarSign size={11} className="text-neutral-500" />
                        {Number(e.today_usd).toFixed(2)}
                      </span>
                    </td>
                    <td className="py-3 pr-3 font-mono text-neutral-200">
                      <span className="inline-flex items-center gap-1">
                        <DollarSign size={11} className="text-neutral-500" />
                        {Number(e.month_usd).toFixed(2)}
                      </span>
                    </td>
                    <td className="py-3 pr-3"><BudgetBar spent={e.today_usd} budget={e.daily_budget_usd} /></td>
                    <td className="py-3 pr-3"><BudgetBar spent={e.month_usd} budget={e.monthly_budget_usd} /></td>
                    {isAdmin && (
                      <td className="py-3 pr-2 text-right">
                        <button
                          onClick={() => revoke(e.key_id, e.email)}
                          aria-label={`Revoke ${e.email}`}
                          className="inline-flex items-center gap-1 text-[10px] text-neutral-500 hover:text-red-400 px-2 py-1 rounded border border-transparent hover:border-red-500/20 hover:bg-red-500/[0.04]"
                        >
                          <Trash2 size={11} /> Revoke
                        </button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <p className="text-[10px] text-neutral-700 leading-relaxed">
        Each employee replaces their <code className="text-neutral-500">ANTHROPIC_API_KEY</code> with
        the minted <code className="text-neutral-500">acp_emp_…</code> virtual key, and points the
        Anthropic SDK at <code className="text-neutral-500">https://ha.aegisagent.in</code> via the
        <code className="text-neutral-500"> base_url</code> parameter. From the SDK's perspective
        nothing else changes — but every message lands in your audit chain, gets attributed back to
        the employee, and counts toward their daily + monthly USD cap.
      </p>

      {showAdd && (
        <AddEmployeeModal onClose={() => setShowAdd(false)} onMinted={load} />
      )}
    </div>
  )
}
