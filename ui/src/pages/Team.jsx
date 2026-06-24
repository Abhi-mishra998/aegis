import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import {
  Activity,
  AlertTriangle,
  Building2,
  Check,
  Copy,
  DollarSign,
  Eye,
  Loader2,
  Plus,
  RefreshCw,
  Shield,
  ShieldCheck,
  Trash2,
  TrendingUp,
  Users,
} from 'lucide-react'
import { teamService } from '../services/api'
import { useAuth } from '../hooks/useAuth'
import Button from '../components/Common/Button'
import Card from '../components/Common/Card'
import TabErrorBoundary from '../components/Common/TabErrorBoundary'

/* ───────── shared helpers ──────────────────────────────────────────── */

// Same idiom as services/api.js + Layout/ClerkAuthBridge.jsx, but resolved
// to an absolute URL because we display it to operators as a copy-paste
// SDK base_url. window.location.origin keeps the same bundle reusable on
// dev / staging / prod — the URL we surface tracks whatever host the
// operator is already pointed at.
const API_BASE_URL =
  import.meta.env?.VITE_GATEWAY_URL ||
  (typeof window !== 'undefined' ? window.location.origin : '')

function fmtUSD(n) {
  if (n == null) return '—'
  const v = Number(n) || 0
  if (v >= 1000) return `$${(v / 1000).toFixed(1)}K`
  if (v >= 1)    return `$${v.toFixed(2)}`
  return `$${v.toFixed(4)}`
}

function fmtInt(n) {
  if (n == null) return '—'
  const v = Number(n) || 0
  if (v >= 1000) return `${(v / 1000).toFixed(1)}K`
  return v.toLocaleString()
}

const SUGGESTED_DEPARTMENTS = ['Engineering', 'Finance', 'Legal', 'Sales', 'Support']

const RISK_LABEL_STYLES = {
  Low:      'text-green-400  bg-green-500/10  border-green-500/20',
  Moderate: 'text-blue-400   bg-blue-500/10   border-blue-500/20',
  Elevated: 'text-amber-400  bg-amber-500/10  border-amber-500/20',
  High:     'text-red-400    bg-red-500/10    border-red-500/20',
}

function RiskLabel({ label, score, reason }) {
  const style = RISK_LABEL_STYLES[label] || 'text-neutral-500 bg-white/[0.03] border-white/[0.06]'
  return (
    <span
      className={`status-badge ${style}`}
      title={reason || `Risk score ${(Number(score) || 0).toFixed(2)} (0–1 scale; >0.4 = Elevated, >0.7 = High)`}
    >
      {label || '—'}
    </span>
  )
}

function BudgetBar({ spent, budget }) {
  if (budget == null || budget <= 0) {
    return <span className="text-[10px] text-neutral-600">no cap</span>
  }
  const pct = Math.min(100, (Number(spent) / Number(budget)) * 100)
  const color =
    pct >= 95 ? 'bg-red-500/70' : pct >= 70 ? 'bg-amber-400/70' : 'bg-green-500/70'
  return (
    <div className="w-28 space-y-1">
      <div className="h-1 rounded-full bg-white/[0.04] overflow-hidden">
        <div className={`h-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <div className="text-[10px] text-neutral-500">
        {fmtUSD(spent)} <span className="text-neutral-700">/</span> {fmtUSD(budget)}
      </div>
    </div>
  )
}

function MetricTile({ label, value, sublabel, accent = 'text-white', icon: Icon }) {
  return (
    <Card>
      <div className="space-y-1">
        <div className="flex items-center gap-2 text-[10px] uppercase tracking-widest text-neutral-500">
          {Icon && <Icon size={11} aria-hidden="true" />}
          <span>{label}</span>
        </div>
        <div className={`text-3xl font-bold ${accent}`}>{value}</div>
        {sublabel && <div className="text-[11px] text-neutral-500">{sublabel}</div>}
      </div>
    </Card>
  )
}

/* ───────── Add-employee modal ───────────────────────────────────────── */

function AddEmployeeModal({ onClose, onMinted, knownDepartments }) {
  const [email, setEmail] = useState('')
  const [name, setName] = useState('')
  const [department, setDepartment] = useState('')
  const [dailyBudget, setDailyBudget] = useState('')
  const [monthlyBudget, setMonthlyBudget] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [minted, setMinted] = useState(null)
  const [copied, setCopied] = useState(false)

  const submit = async (e) => {
    e?.preventDefault?.()
    if (!email.trim()) { setError('Email is required.'); return }
    setBusy(true); setError('')
    try {
      const payload = {
        email: email.trim().toLowerCase(),
        name: name.trim() || undefined,
        department: department.trim() || undefined,
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

  const departmentOptions = useMemo(() => {
    const set = new Set([...SUGGESTED_DEPARTMENTS, ...(knownDepartments || [])])
    return Array.from(set).filter((d) => d && d !== 'Unassigned')
  }, [knownDepartments])

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

              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1">
                  <label className="label-standard" htmlFor="emp-name">Display name</label>
                  <input
                    id="emp-name"
                    type="text"
                    className="input-standard h-10"
                    placeholder="Alice Liu"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                  />
                </div>
                <div className="space-y-1">
                  <label className="label-standard" htmlFor="emp-dept">Department</label>
                  <input
                    id="emp-dept"
                    list="emp-dept-list"
                    type="text"
                    className="input-standard h-10"
                    placeholder="Engineering"
                    value={department}
                    onChange={(e) => setDepartment(e.target.value)}
                  />
                  <datalist id="emp-dept-list">
                    {departmentOptions.map((d) => (<option key={d} value={d} />))}
                  </datalist>
                </div>
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
                <code className="text-white">{minted.subject_email || minted.email}</code>{' '}
                and ask them to replace their <code>ANTHROPIC_API_KEY</code> + point the
                SDK at <code>{API_BASE_URL}</code>.
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

/* ───────── Tab: Members ─────────────────────────────────────────────── */

function MembersTab({ employees, loading, isAdmin, onRefresh, onAdd, onRevoke }) {
  return (
    <Card title="Members" icon={Users}>
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
            <Button size="sm" onClick={onAdd}>
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
                <th className="py-2 pr-3">Department</th>
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
                    <Link
                      to={`/team/${encodeURIComponent(e.email)}`}
                      className="block group"
                      title="Open employee profile"
                    >
                      <div className="text-neutral-200 font-medium group-hover:text-white group-hover:underline">
                        {e.name || (e.email || '').split('@')[0]}
                      </div>
                      <div className="text-[10px] text-neutral-600 group-hover:text-neutral-400">
                        {e.email}
                      </div>
                    </Link>
                  </td>
                  <td className="py-3 pr-3 text-neutral-300">{e.department || <span className="text-neutral-600 italic">Unassigned</span>}</td>
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
                        onClick={() => onRevoke(e.key_id, e.email)}
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
  )
}

/* ───────── Tab: Departments ─────────────────────────────────────────── */

function DepartmentsTab({ departments, loading }) {
  return (
    <Card title="Department View" icon={Building2}>
      {loading ? (
        <div className="text-xs text-neutral-500 py-10 text-center">
          <Loader2 size={20} className="animate-spin mx-auto mb-2" />
          Aggregating…
        </div>
      ) : departments.length === 0 ? (
        <div className="text-xs text-neutral-500 py-10 text-center space-y-2">
          <Building2 size={26} className="text-neutral-700 mx-auto" />
          <div>No department traffic yet.</div>
          <div className="text-[11px] text-neutral-600 max-w-md mx-auto">
            Tag employees with a department in the Add-employee modal and run any /v1/messages
            calls — this view auto-rolls up by team.
          </div>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-[10px] uppercase tracking-widest text-neutral-500">
              <tr className="text-left border-b border-white/[0.05]">
                <th className="py-2 pr-3">Department</th>
                <th className="py-2 pr-3">Employees</th>
                <th className="py-2 pr-3">AI Requests (30d)</th>
                <th className="py-2 pr-3">Spend (30d)</th>
                <th className="py-2 pr-3">Harmful blocked</th>
                <th className="py-2 pr-3">Compliance enforced</th>
                <th className="py-2 pr-2">Risk</th>
              </tr>
            </thead>
            <tbody>
              {departments.map((d) => (
                <tr key={d.name} className="border-b border-white/[0.04] last:border-b-0">
                  <td className="py-3 pr-3 text-neutral-200 font-medium">{d.name}</td>
                  <td className="py-3 pr-3 text-neutral-300">{d.employees}</td>
                  <td className="py-3 pr-3 font-mono text-neutral-200">{fmtInt(d.requests_30d)}</td>
                  <td className="py-3 pr-3 font-mono text-neutral-200">{fmtUSD(d.spend_30d_usd)}</td>
                  <td className="py-3 pr-3 font-mono text-neutral-200">{d.harmful_blocked_30d}</td>
                  <td className="py-3 pr-3 font-mono text-neutral-200">{d.compliance_enforced_30d}</td>
                  <td className="py-3 pr-2">
                    <RiskLabel
                      label={d.risk_label}
                      score={d.risk_score}
                      reason={
                        d.risk_label === 'High' || d.risk_label === 'Elevated'
                          ? `${d.harmful_blocked_30d} harmful action${d.harmful_blocked_30d === 1 ? '' : 's'} blocked out of ${d.requests_30d} requests this month.`
                          : 'No high-risk activity observed in the last 30 days.'
                      }
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}

/* ───────── Tab: Executive ───────────────────────────────────────────── */

function ExecutiveTab({ kpis, departments, loading }) {
  const focusDept = departments.find((d) => d.risk_label === 'High' || d.risk_label === 'Elevated')
  const totalDepts = departments.length
  return (
    <div className="space-y-4">
      <Card title="Executive Summary" icon={ShieldCheck}>
        {loading ? (
          <div className="text-xs text-neutral-500 py-8 text-center">
            <Loader2 size={20} className="animate-spin mx-auto mb-2" />
            Building summary…
          </div>
        ) : (
          <div className="space-y-4 text-sm text-neutral-300 leading-relaxed">
            <p>
              In the last 30 days, your organisation routed{' '}
              <strong className="text-white">{fmtInt(kpis?.ai_requests_30d || 0)} AI requests</strong>{' '}
              through Aegis across{' '}
              <strong className="text-white">{kpis?.active_employees || 0}</strong> active employees in{' '}
              <strong className="text-white">{totalDepts}</strong> department{totalDepts === 1 ? '' : 's'}.
              Total spend reached <strong className="text-white">{fmtUSD(kpis?.monthly_spend_usd)}</strong>.
            </p>
            <p>
              Aegis blocked{' '}
              <strong className="text-white">{fmtInt(kpis?.harmful_actions_blocked_30d || 0)} harmful action{kpis?.harmful_actions_blocked_30d === 1 ? '' : 's'}</strong>{' '}
              and enforced{' '}
              <strong className="text-white">{fmtInt(kpis?.compliance_violations_prevented_30d || 0)} compliance control{kpis?.compliance_violations_prevented_30d === 1 ? '' : 's'}</strong>{' '}
              over the same period.
              {focusDept ? (
                <>{' '}The highest-risk team this month is{' '}
                <strong className="text-white">{focusDept.name}</strong>{' '}
                ({focusDept.harmful_blocked_30d} harmful action{focusDept.harmful_blocked_30d === 1 ? '' : 's'} blocked out of{' '}
                {focusDept.requests_30d} requests).</>
              ) : (
                <> No team currently exceeds the Elevated risk threshold.</>
              )}
            </p>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 pt-2">
              <MetricTile label="Active employees" value={kpis?.active_employees || 0} icon={Users} />
              <MetricTile label="AI requests / 30d" value={fmtInt(kpis?.ai_requests_30d || 0)} icon={Activity} />
              <MetricTile label="Spend / 30d" value={fmtUSD(kpis?.monthly_spend_usd)} icon={DollarSign} />
              <MetricTile
                label="Harmful blocked"
                value={fmtInt(kpis?.harmful_actions_blocked_30d || 0)}
                accent={(kpis?.harmful_actions_blocked_30d || 0) > 0 ? 'text-amber-400' : 'text-white'}
                icon={Shield}
              />
            </div>
          </div>
        )}
      </Card>

      {departments.length > 0 && (
        <Card title="Where attention is needed" icon={Eye}>
          <ul className="space-y-2 text-xs">
            {departments.slice(0, 5).map((d) => (
              <li key={d.name} className="flex items-center justify-between gap-3 border-b border-white/[0.04] last:border-b-0 py-2">
                <div className="flex items-center gap-3">
                  <RiskLabel label={d.risk_label} score={d.risk_score} />
                  <span className="text-neutral-200">{d.name}</span>
                </div>
                <span className="text-neutral-500">
                  {d.requests_30d} requests · {fmtUSD(d.spend_30d_usd)} · {d.harmful_blocked_30d} blocked
                </span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  )
}

/* ───────── /team page ───────────────────────────────────────────────── */

const TABS = [
  { id: 'members',     label: 'Members',     icon: Users      },
  { id: 'departments', label: 'Departments', icon: Building2  },
  { id: 'executive',   label: 'Executive',   icon: ShieldCheck },
]
const DEFAULT_TAB = TABS[0].id
const VALID_TAB_IDS = new Set(TABS.map((t) => t.id))

export default function Team() {
  const { role } = useAuth() || {}
  const isAdmin = role === 'OWNER' || role === 'ADMIN'

  const [searchParams, setSearchParams] = useSearchParams()
  const activeTab = useMemo(() => {
    const p = searchParams.get('tab')
    return p && VALID_TAB_IDS.has(p) ? p : DEFAULT_TAB
  }, [searchParams])
  const handleTabClick = (id) => setSearchParams({ tab: id }, { replace: true })

  const [employees, setEmployees] = useState([])
  const [overview,  setOverview]  = useState(null)
  const [loading,   setLoading]   = useState(true)
  const [error,     setError]     = useState('')
  const [showAdd,   setShowAdd]   = useState(false)

  const load = useCallback(async () => {
    setLoading(true); setError('')
    try {
      const [empResp, ovResp] = await Promise.allSettled([
        teamService.listEmployees(),
        teamService.overview(),
      ])
      if (empResp.status === 'fulfilled') {
        const rows = empResp.value?.data || empResp.value || []
        setEmployees(Array.isArray(rows) ? rows : [])
      } else {
        setError(empResp.reason?.message || 'Failed to load team.')
      }
      if (ovResp.status === 'fulfilled') {
        setOverview(ovResp.value?.data || ovResp.value || null)
      }
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  // Sprint 20 UX pass — refresh the team view every 30s so a CIO
  // watching the page sees per-employee spend tick up as their
  // engineers fire prompts. /team/employees + /team/overview are
  // cheap GETs (single audit-aggregate + a single api-keys list).
  useEffect(() => {
    const id = setInterval(() => { load() }, 30_000)
    return () => clearInterval(id)
  }, [load])

  const revoke = async (keyId, email) => {
    if (!window.confirm(`Revoke ${email}'s Aegis key? Their Anthropic SDK calls will start failing immediately.`)) return
    try {
      await teamService.revokeKey(keyId)
      await load()
    } catch (err) {
      setError(err?.message || 'Revoke failed.')
    }
  }

  // Sprint 17.5 KPIs prefer the audit-log rollup (30-day window).
  // Fall back to in-memory aggregates for first-render before /team/overview lands.
  const kpis = useMemo(() => {
    if (overview?.kpis) return overview.kpis
    const todayTotal = employees.reduce((s, e) => s + (Number(e.today_usd) || 0), 0)
    const monthTotal = employees.reduce((s, e) => s + (Number(e.month_usd) || 0), 0)
    return {
      active_employees:                      employees.filter((e) => e.is_active).length,
      ai_requests_30d:                       0,
      monthly_spend_usd:                     monthTotal || todayTotal,
      harmful_actions_blocked_30d:           0,
      compliance_violations_prevented_30d:   0,
      highest_risk_department:               null,
    }
  }, [overview, employees])

  const knownDepartments = useMemo(
    () => Array.from(new Set(employees.map((e) => e.department).filter(Boolean))),
    [employees],
  )

  const tabBody = (() => {
    if (activeTab === 'departments') return <DepartmentsTab departments={overview?.departments || []} loading={loading} />
    if (activeTab === 'executive')   return <ExecutiveTab kpis={kpis} departments={overview?.departments || []} loading={loading} />
    return (
      <MembersTab
        employees={employees}
        loading={loading}
        isAdmin={isAdmin}
        onRefresh={load}
        onAdd={() => setShowAdd(true)}
        onRevoke={revoke}
      />
    )
  })()

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div className="space-y-1">
          <h1 className="text-2xl font-bold tracking-tight text-white flex items-center gap-2">
            <Users size={20} /> Team
          </h1>
          <p className="text-xs text-neutral-400 max-w-xl">
            Per-employee Claude usage routed through Aegis. Each employee uses an
            <code className="mx-1 text-neutral-300">acp_emp_…</code> virtual key — the
            corporate Anthropic key never leaves your workspace, and every call lands in
            the same cryptographic audit chain as your production agents.
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

      {/* Hero KPIs */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        <MetricTile label="Active employees"  value={kpis.active_employees}                          icon={Users} />
        <MetricTile label="AI requests / 30d" value={fmtInt(kpis.ai_requests_30d)}                   icon={Activity} />
        <MetricTile label="Monthly spend"     value={fmtUSD(kpis.monthly_spend_usd)}                 icon={DollarSign} />
        <MetricTile
          label="Harmful blocked"
          value={fmtInt(kpis.harmful_actions_blocked_30d)}
          accent={(kpis.harmful_actions_blocked_30d || 0) > 0 ? 'text-amber-400' : 'text-white'}
          icon={Shield}
        />
        <MetricTile
          label="Compliance enforced"
          value={fmtInt(kpis.compliance_violations_prevented_30d)}
          icon={ShieldCheck}
        />
        <MetricTile
          label="Highest-risk team"
          value={kpis.highest_risk_department || '—'}
          sublabel={kpis.highest_risk_department ? 'review in the Departments tab' : 'no high-risk teams'}
          accent={kpis.highest_risk_department ? 'text-amber-400' : 'text-white'}
          icon={TrendingUp}
        />
      </div>

      {error && (
        <div className="error-banner" role="alert">
          <div className="flex items-center gap-3">
            <AlertTriangle size={15} className="text-red-400 shrink-0" />
            <p className="text-xs text-red-400">{error}</p>
          </div>
        </div>
      )}

      {/* Tab bar */}
      <div className="flex gap-1 overflow-x-auto pb-1 border-b border-white/[0.06]" role="tablist">
        {TABS.map(({ id, label, icon: Icon }) => {
          const isActive = id === activeTab
          return (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={isActive}
              onClick={() => handleTabClick(id)}
              className={
                'flex items-center gap-1.5 px-3 h-9 rounded-t-md text-xs font-medium transition-all whitespace-nowrap ' +
                (isActive
                  ? 'bg-white/[0.08] text-white border border-white/[0.1] border-b-transparent -mb-px'
                  : 'text-neutral-400 hover:text-white hover:bg-white/[0.04]')
              }
            >
              <Icon size={13} aria-hidden="true" />
              {label}
            </button>
          )
        })}
      </div>

      <TabErrorBoundary tabId={activeTab}>
        {tabBody}
      </TabErrorBoundary>

      <p className="text-[10px] text-neutral-700 leading-relaxed">
        Each employee replaces their <code className="text-neutral-500">ANTHROPIC_API_KEY</code> with
        the minted <code className="text-neutral-500">acp_emp_…</code> virtual key and points the
        Anthropic SDK at <code className="text-neutral-500">{API_BASE_URL}</code> via the
        <code className="text-neutral-500"> base_url</code> parameter. From the SDK's perspective
        nothing else changes — but every message lands in your audit chain, gets attributed back
        to the employee, and counts toward their daily + monthly USD cap.
      </p>

      {showAdd && (
        <AddEmployeeModal
          onClose={() => setShowAdd(false)}
          onMinted={load}
          knownDepartments={knownDepartments}
        />
      )}
    </div>
  )
}
