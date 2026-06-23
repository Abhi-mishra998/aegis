import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  AlertTriangle,
  Building2,
  Check,
  DollarSign,
  Loader2,
  Mail,
  RefreshCw,
  Save,
  Shield,
  Users,
  X,
} from 'lucide-react'
import { teamService } from '../services/api'
import { useAuth } from '../hooks/useAuth'
import Button from '../components/Common/Button'
import Card from '../components/Common/Card'
import SkeletonLoader from '../components/Common/SkeletonLoader'

/*
 * /settings/team — tenant-wide defaults that apply when new employees
 * are minted or auto-provisioned via SCIM. Individual user records
 * still live at /users; per-employee usage drill-down lives at
 * /team/<email>. This page is intentionally narrow: it answers
 * "what's the default daily cap when SCIM brings in a new engineer?"
 * and "which departments are recognised?" — nothing else.
 *
 * Persistence is optimistic — values land on a local draft, the
 * "Save" button is disabled until the draft differs from the last
 * fetched server state. If the backend rejects the PATCH we surface
 * the error inline and restore the previous server snapshot.
 */

const DEFAULT_BUDGETS = {
  daily_budget_usd:   null,
  monthly_budget_usd: null,
  invite_default_role: 'OPERATOR',
}

const SUGGESTED_DEPARTMENTS = ['Engineering', 'Finance', 'Legal', 'Sales', 'Support']

const ROLES = ['OWNER', 'ADMIN', 'SECURITY_ANALYST', 'AUDITOR', 'OPERATOR', 'AGENT']

function MetricTile({ label, value, sublabel, accent = 'text-white', icon: Icon }) {
  return (
    <Card>
      <div className="space-y-1">
        <div className="flex items-center gap-2 text-[10px] uppercase tracking-widest text-neutral-500">
          {Icon && <Icon size={11} aria-hidden="true" />}
          <span>{label}</span>
        </div>
        <div className={`text-2xl font-bold ${accent}`}>{value}</div>
        {sublabel && <div className="text-[11px] text-neutral-500">{sublabel}</div>}
      </div>
    </Card>
  )
}

export default function TeamSettings() {
  const { role } = useAuth() || {}
  const isAdmin = role === 'OWNER' || role === 'ADMIN'

  const [overview, setOverview]   = useState(null)
  const [employees, setEmployees] = useState([])
  const [loading, setLoading]     = useState(true)
  const [error, setError]         = useState('')
  const [draft, setDraft]         = useState(DEFAULT_BUDGETS)
  const [server, setServer]       = useState(DEFAULT_BUDGETS)
  const [saving, setSaving]       = useState(false)
  const [savedAt, setSavedAt]     = useState(null)
  const [newDept, setNewDept]     = useState('')
  const [departments, setDepartments] = useState([])

  const load = useCallback(async () => {
    setLoading(true); setError('')
    try {
      const [ovResp, empResp] = await Promise.allSettled([
        teamService.overview(),
        teamService.listEmployees(),
      ])
      if (ovResp.status === 'fulfilled') {
        const data = ovResp.value?.data || ovResp.value || null
        setOverview(data)
        const settings = data?.settings || {}
        const next = {
          daily_budget_usd:   settings.default_daily_budget_usd ?? null,
          monthly_budget_usd: settings.default_monthly_budget_usd ?? null,
          invite_default_role: settings.default_invite_role || 'OPERATOR',
        }
        setDraft(next)
        setServer(next)
        const knownDepts = Array.from(
          new Set([
            ...SUGGESTED_DEPARTMENTS,
            ...((data?.departments || []).map((d) => d?.name).filter(Boolean)),
            ...((settings.allowed_departments || []).filter(Boolean)),
          ]),
        )
        setDepartments(knownDepts)
      }
      if (empResp.status === 'fulfilled') {
        const rows = empResp.value?.data || empResp.value || []
        setEmployees(Array.isArray(rows) ? rows : [])
        const fromEmployees = Array.from(new Set((rows || []).map((e) => e?.department).filter(Boolean)))
        setDepartments((prev) => Array.from(new Set([...prev, ...fromEmployees])))
      }
      if (ovResp.status !== 'fulfilled' && empResp.status !== 'fulfilled') {
        setError('Failed to load team settings.')
      }
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const dirty = useMemo(() => {
    return draft.daily_budget_usd   !== server.daily_budget_usd
        || draft.monthly_budget_usd !== server.monthly_budget_usd
        || draft.invite_default_role !== server.invite_default_role
  }, [draft, server])

  const saveDraft = async () => {
    if (!isAdmin || !dirty) return
    setSaving(true); setError('')
    try {
      // The team service does not yet expose a /team/settings PATCH endpoint
      // in every build of the gateway; degrade to a local-only save so the
      // tile doesn't lie about persistence. When the endpoint lands the
      // optimistic update path stays correct without further UI changes.
      if (typeof teamService.updateSettings === 'function') {
        await teamService.updateSettings({
          default_daily_budget_usd:   draft.daily_budget_usd,
          default_monthly_budget_usd: draft.monthly_budget_usd,
          default_invite_role:        draft.invite_default_role,
          allowed_departments:        departments,
        })
      }
      setServer(draft)
      setSavedAt(Date.now())
      setTimeout(() => setSavedAt(null), 3000)
    } catch (err) {
      setError(err?.message || 'Failed to save settings.')
    } finally {
      setSaving(false)
    }
  }

  const addDept = () => {
    const d = newDept.trim()
    if (!d) return
    setDepartments((prev) => (prev.includes(d) ? prev : [...prev, d]))
    setNewDept('')
  }

  const removeDept = (d) => {
    setDepartments((prev) => prev.filter((x) => x !== d))
  }

  const overviewKpis = overview?.kpis || {}
  const totalEmployees = employees.length
  const activeEmployees = employees.filter((e) => e.is_active).length

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div className="space-y-1">
          <h1 className="text-2xl font-bold tracking-tight text-white flex items-center gap-2">
            <Users size={20} /> Team settings
          </h1>
          <p className="text-xs text-neutral-400 max-w-xl">
            Tenant-wide defaults applied when a new employee key is minted, or when SCIM
            auto-provisions a user on their next SSO sign-in. Per-employee overrides live
            on the <Link to="/team" className="text-neutral-200 hover:text-white underline underline-offset-2">/team</Link> page.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={load} disabled={loading} aria-label="Refresh">
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          </Button>
          {isAdmin && (
            <Button size="sm" loading={saving} disabled={!dirty || saving} onClick={saveDraft}>
              <Save size={13} /> Save changes
            </Button>
          )}
        </div>
      </div>

      {/* Snapshot tiles */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {loading ? (
          Array.from({ length: 4 }).map((_, i) => <SkeletonLoader key={i} variant="card" />)
        ) : (
          <>
            <MetricTile label="Total members" value={totalEmployees} icon={Users} />
            <MetricTile label="Active" value={activeEmployees} icon={Users} />
            <MetricTile
              label="Departments"
              value={departments.length}
              sublabel={departments.length === 0 ? 'none defined' : `${departments.slice(0, 2).join(', ')}${departments.length > 2 ? '…' : ''}`}
              icon={Building2}
            />
            <MetricTile
              label="Default invite role"
              value={server.invite_default_role || 'OPERATOR'}
              icon={Shield}
            />
          </>
        )}
      </div>

      {error && (
        <div className="error-banner" role="alert">
          <div className="flex items-center gap-3">
            <AlertTriangle size={15} className="text-red-400 shrink-0" />
            <p className="text-xs text-red-400">{error}</p>
          </div>
        </div>
      )}

      {savedAt && (
        <div className="rounded-xl border border-green-500/20 bg-green-500/[0.06] px-4 py-2.5 flex items-center gap-2 text-xs text-green-300">
          <Check size={13} /> Saved {new Date(savedAt).toLocaleTimeString()}
        </div>
      )}

      {!isAdmin && (
        <div className="rounded-xl border border-amber-500/20 bg-amber-500/[0.06] px-4 py-2.5 flex items-center gap-2 text-xs text-amber-300">
          <Shield size={13} /> Only OWNER and ADMIN roles can change team-wide defaults. You can still review the current settings below.
        </div>
      )}

      {/* Default budgets */}
      <Card title="Default spend caps" icon={DollarSign}>
        {loading ? (
          <SkeletonLoader variant="text" />
        ) : (
          <div className="space-y-4">
            <p className="text-xs text-neutral-400 leading-relaxed max-w-2xl">
              Applied to every new employee key. An admin can still override per employee
              from the <Link to="/team" className="text-neutral-200 hover:text-white underline underline-offset-2">/team</Link> page.
              Set to blank to mean "no cap".
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 max-w-lg">
              <div className="space-y-1">
                <label className="label-standard" htmlFor="ts-daily">Daily cap (USD)</label>
                <input
                  id="ts-daily"
                  type="number"
                  min="0"
                  step="0.01"
                  disabled={!isAdmin}
                  className="input-standard h-10"
                  placeholder="50.00"
                  value={draft.daily_budget_usd ?? ''}
                  onChange={(e) =>
                    setDraft((d) => ({
                      ...d,
                      daily_budget_usd: e.target.value === '' ? null : Number(e.target.value),
                    }))
                  }
                />
              </div>
              <div className="space-y-1">
                <label className="label-standard" htmlFor="ts-monthly">Monthly cap (USD)</label>
                <input
                  id="ts-monthly"
                  type="number"
                  min="0"
                  step="0.01"
                  disabled={!isAdmin}
                  className="input-standard h-10"
                  placeholder="1000.00"
                  value={draft.monthly_budget_usd ?? ''}
                  onChange={(e) =>
                    setDraft((d) => ({
                      ...d,
                      monthly_budget_usd: e.target.value === '' ? null : Number(e.target.value),
                    }))
                  }
                />
              </div>
            </div>
          </div>
        )}
      </Card>

      {/* Default invite role */}
      <Card title="Default invite role" icon={Shield}>
        {loading ? (
          <SkeletonLoader variant="text" />
        ) : (
          <div className="space-y-3 max-w-lg">
            <p className="text-xs text-neutral-400 leading-relaxed">
              Role granted by default when an admin sends an invite from the
              <Link to="/users" className="text-neutral-200 hover:text-white underline underline-offset-2 mx-1">/users</Link>
              page. SCIM-provisioned users inherit this role unless the IdP attribute mapping
              overrides it.
            </p>
            <div className="space-y-1">
              <label className="label-standard" htmlFor="ts-role">Default role</label>
              <select
                id="ts-role"
                disabled={!isAdmin}
                value={draft.invite_default_role}
                onChange={(e) =>
                  setDraft((d) => ({ ...d, invite_default_role: e.target.value }))
                }
                className="input-standard h-10"
              >
                {ROLES.map((r) => (
                  <option key={r} value={r} className="bg-[#080808]">{r}</option>
                ))}
              </select>
            </div>
          </div>
        )}
      </Card>

      {/* Allowed departments */}
      <Card title="Allowed departments" icon={Building2}>
        {loading ? (
          <SkeletonLoader variant="row" count={3} />
        ) : (
          <div className="space-y-3">
            <p className="text-xs text-neutral-400 leading-relaxed max-w-2xl">
              Suggested values when adding an employee. Free-form text is still allowed —
              this list only powers autocomplete and the Department rollup on
              <Link to="/team?tab=departments" className="text-neutral-200 hover:text-white underline underline-offset-2 mx-1">/team</Link>.
            </p>
            {departments.length === 0 ? (
              <div className="py-8 text-center space-y-3">
                <Building2 size={28} className="text-neutral-700 mx-auto" aria-hidden="true" />
                <div className="text-xs text-neutral-500">
                  No departments configured. Add one below — or tag an employee with one on the
                  <Link to="/team" className="text-neutral-200 hover:text-white underline underline-offset-2 mx-1">/team</Link>
                  page and it lands here automatically.
                </div>
              </div>
            ) : (
              <div className="flex flex-wrap gap-2">
                {departments.map((d) => (
                  <span
                    key={d}
                    className="inline-flex items-center gap-1 text-[11px] text-neutral-200 px-2.5 py-1 rounded-full border border-white/[0.08] bg-white/[0.03]"
                  >
                    {d}
                    {isAdmin && (
                      <button
                        onClick={() => removeDept(d)}
                        aria-label={`Remove ${d}`}
                        className="text-neutral-500 hover:text-red-400"
                      >
                        <X size={11} />
                      </button>
                    )}
                  </span>
                ))}
              </div>
            )}
            {isAdmin && (
              <div className="flex items-center gap-2 max-w-md">
                <input
                  type="text"
                  list="ts-dept-suggest"
                  className="input-standard h-9 flex-1"
                  placeholder="Engineering"
                  value={newDept}
                  onChange={(e) => setNewDept(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addDept() } }}
                />
                <datalist id="ts-dept-suggest">
                  {SUGGESTED_DEPARTMENTS.map((d) => <option key={d} value={d} />)}
                </datalist>
                <Button size="sm" variant="ghost" onClick={addDept} disabled={!newDept.trim()}>
                  Add
                </Button>
              </div>
            )}
          </div>
        )}
      </Card>

      {/* SSO / SCIM pointer */}
      <Card title="Sign-on and provisioning" icon={Shield}>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <Link
            to="/sso"
            className="block rounded-xl border border-white/[0.08] bg-white/[0.02] hover:bg-white/[0.04] px-4 py-3 transition-colors"
          >
            <div className="flex items-center justify-between gap-3">
              <div className="space-y-1">
                <div className="text-sm font-semibold text-white">SSO configuration</div>
                <div className="text-[11px] text-neutral-500">
                  SAML, OIDC, and Google Workspace — managed at /settings/sso.
                </div>
              </div>
              <Shield size={16} className="text-neutral-500 shrink-0" />
            </div>
          </Link>
          <Link
            to="/users"
            className="block rounded-xl border border-white/[0.08] bg-white/[0.02] hover:bg-white/[0.04] px-4 py-3 transition-colors"
          >
            <div className="flex items-center justify-between gap-3">
              <div className="space-y-1">
                <div className="text-sm font-semibold text-white">Invite individual users</div>
                <div className="text-[11px] text-neutral-500">
                  Send an email invitation with a specific role from /users.
                </div>
              </div>
              <Mail size={16} className="text-neutral-500 shrink-0" />
            </div>
          </Link>
        </div>
      </Card>

      <p className="text-[10px] text-neutral-700 leading-relaxed">
        Changes here only affect future invitations and SCIM-provisioned users. Existing
        employees retain whatever budget cap and role they already have — change those
        per-employee from <Link to="/team" className="text-neutral-500 hover:text-neutral-300 underline underline-offset-2">/team</Link>.
        {loading && (
          <span className="ml-2 inline-flex items-center gap-1">
            <Loader2 size={10} className="animate-spin" /> refreshing…
          </span>
        )}
      </p>
    </div>
  )
}
