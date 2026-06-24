import React, { useState, useEffect, useCallback } from 'react'
import { Link } from 'react-router-dom'
import {
  Users, Plus, RefreshCw, Loader2, Check, X,
  Shield, ChevronDown, Mail, UserCheck, UserX, Trash2,
  UserPlus,
} from 'lucide-react'
import { userService } from '../services/api'
import SkeletonLoader from '../components/Common/SkeletonLoader'

// Canonical role vocabulary — matches the platform's RBAC enum
// (OWNER/ADMIN/SECURITY_ANALYST/AUDITOR/OPERATOR/AGENT).
// VIEWER kept as a no-op fallback for legacy rows the backend hasn't
// migrated yet.
const ROLES = ['OWNER', 'ADMIN', 'SECURITY_ANALYST', 'AUDITOR', 'OPERATOR', 'AGENT']

const ROLE_COLORS = {
  OWNER:            'text-red-400    bg-red-500/10    border-red-500/20',
  ADMIN:            'text-red-400    bg-red-500/10    border-red-500/20',
  SECURITY_ANALYST: 'text-orange-400 bg-orange-500/10 border-orange-500/20',
  AUDITOR:          'text-purple-400 bg-purple-500/10 border-purple-500/20',
  OPERATOR:         'text-blue-400   bg-blue-500/10   border-blue-500/20',
  AGENT:            'text-cyan-400   bg-cyan-500/10   border-cyan-500/20',
  // Legacy labels still appearing on older rows.
  SECURITY:         'text-orange-400 bg-orange-500/10 border-orange-500/20',
  ANALYST:          'text-blue-400   bg-blue-500/10   border-blue-500/20',
  VIEWER:           'text-neutral-400 bg-white/[0.04] border-white/[0.08]',
}

function RoleBadge({ role }) {
  return (
    <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full border ${ROLE_COLORS[role] || ROLE_COLORS.VIEWER}`}>
      {role}
    </span>
  )
}

function InviteModal({ onClose, onInvited }) {
  const [email, setEmail] = useState('')
  const [role,  setRole]  = useState('OPERATOR')
  const [saving, setSaving] = useState(false)
  const [error,  setError]  = useState('')

  const submit = async (e) => {
    e.preventDefault()
    if (!email.trim() || !email.includes('@')) { setError('Valid email required.'); return }
    setSaving(true)
    setError('')
    try {
      await userService.invite({ email: email.trim().toLowerCase(), role })
      onInvited()
      onClose()
    } catch (err) {
      setError(err?.message || 'Failed to invite user.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="Invite user"
    >
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" />
      <form
        onSubmit={submit}
        className="relative bg-[var(--bg-surface-elevated)] border border-[var(--border-default)] rounded-2xl shadow-2xl p-6 w-full max-w-sm mx-4 space-y-4"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-white">Invite User</h2>
          <button type="button" onClick={onClose} className="text-neutral-600 hover:text-white"><X size={16} /></button>
        </div>

        {error && (
          <div className="text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">{error}</div>
        )}

        <div>
          <label className="block text-xs text-neutral-400 mb-1">Email address</label>
          <input name="input"
            type="email"
            value={email}
            onChange={e => setEmail(e.target.value)}
            placeholder="user@company.com"
            autoFocus
            className="w-full bg-white/[0.04] border border-[var(--border-subtle)] rounded-lg px-3 py-2 text-sm text-white placeholder-neutral-600 focus:outline-none focus:border-white/20"
          />
        </div>

        <div>
          <label className="block text-xs text-neutral-400 mb-1">Role</label>
          <div className="relative">
            <select name="select"
              value={role}
              onChange={e => setRole(e.target.value)}
              className="w-full appearance-none bg-white/[0.04] border border-[var(--border-subtle)] rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-white/20"
            >
              {ROLES.map(r => <option key={r} value={r}>{r}</option>)}
            </select>
            <ChevronDown size={14} className="absolute right-3 top-1/2 -translate-y-1/2 text-neutral-500 pointer-events-none" />
          </div>
          <p className="text-[10px] text-neutral-600 mt-1">
            {role === 'OWNER' && 'Tenant owner — full platform access, including billing and SSO.'}
            {role === 'ADMIN' && 'Full platform access including user management and kill switch.'}
            {role === 'SECURITY_ANALYST' && 'Manage policies, incidents, agents, and forensic timelines.'}
            {role === 'AUDITOR' && 'Read-only access to audit chain, receipts, and compliance evidence.'}
            {role === 'OPERATOR' && 'Operate agents, approve escalations, run playbooks.'}
            {role === 'AGENT' && 'Service-account role for machine principals; not for humans.'}
            {role === 'SECURITY' && 'Legacy label — equivalent to SECURITY_ANALYST.'}
            {role === 'ANALYST' && 'Legacy label — read access to audit logs and analytics.'}
            {role === 'VIEWER' && 'Read-only dashboard access.'}
          </p>
        </div>

        <button
          type="submit"
          disabled={saving}
          className="w-full flex items-center justify-center gap-2 py-2.5 rounded-lg bg-white text-black text-sm font-medium hover:bg-neutral-200 disabled:opacity-50"
        >
          {saving ? <Loader2 size={14} className="animate-spin" /> : <Mail size={14} />}
          {saving ? 'Sending invite…' : 'Send invitation'}
        </button>
      </form>
    </div>
  )
}

function UserRow({ user, onUpdate, onDeactivate }) {
  const [editRole, setEditRole] = useState(false)
  const [saving,   setSaving]   = useState(false)

  const changeRole = async (newRole) => {
    setSaving(true)
    try {
      await onUpdate(user.id, { role: newRole })
      setEditRole(false)
    } catch {
      // Parent's onUpdate already surfaces the error; just don't leave the
      // dropdown stuck open in a "still saving" state if the API rejected.
    } finally {
      setSaving(false)
    }
  }

  const toggleActive = async () => {
    setSaving(true)
    try {
      if (user.is_active) await onDeactivate(user.id)
      else await onUpdate(user.id, { is_active: true })
    } catch {
      // onUpdate/onDeactivate surface errors at the parent; clearing the
      // spinner here is what matters so the row doesn't look frozen.
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="flex items-center gap-4 px-4 py-3 border-b border-[var(--border-subtle)] last:border-0 hover:bg-white/[0.02] transition-colors">
      {/* Avatar */}
      <div className="w-8 h-8 rounded-full bg-white/[0.06] flex items-center justify-center shrink-0">
        <span className="text-xs font-bold text-neutral-300">
          {(user.email || '?')[0].toUpperCase()}
        </span>
      </div>

      {/* Email + meta */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm text-white font-medium truncate">{user.email}</span>
          {!user.is_active && (
            <span className="text-[10px] px-1.5 py-0.5 rounded border border-neutral-700 text-neutral-500">Inactive</span>
          )}
        </div>
        <span className="text-[11px] text-neutral-600 font-mono">
          {user.created_at ? new Date(user.created_at).toLocaleDateString() : '—'}
        </span>
      </div>

      {/* Role */}
      <div className="shrink-0">
        {editRole ? (
          <div className="flex items-center gap-1">
            <select name="select"
              autoFocus
              className="bg-white/[0.04] border border-white/20 rounded-lg px-2 py-1 text-xs text-white focus:outline-none"
              defaultValue={user.role}
              onChange={e => changeRole(e.target.value)}
              onBlur={() => setEditRole(false)}
            >
              {ROLES.map(r => <option key={r} value={r}>{r}</option>)}
            </select>
          </div>
        ) : (
          <button onClick={() => setEditRole(true)} title="Click to change role">
            <RoleBadge role={user.role} />
          </button>
        )}
      </div>

      {/* Actions */}
      <div className="flex items-center gap-1 shrink-0">
        {saving ? (
          <Loader2 size={14} className="animate-spin text-neutral-500" />
        ) : (
          <button
            onClick={toggleActive}
            title={user.is_active ? 'Deactivate user' : 'Reactivate user'}
            className={`p-1.5 rounded-lg transition-colors ${user.is_active ? 'text-neutral-600 hover:text-red-400 hover:bg-red-500/10' : 'text-neutral-600 hover:text-green-400 hover:bg-green-500/10'}`}
          >
            {user.is_active ? <UserX size={13} /> : <UserCheck size={13} />}
          </button>
        )}
      </div>
    </div>
  )
}

export default function UserManagement() {
  const [users,    setUsers]    = useState([])
  const [loading,  setLoading]  = useState(true)
  const [inviting, setInviting] = useState(false)
  const [error,    setError]    = useState('')
  const [roleFilter,   setRoleFilter]   = useState('all')
  const [activeFilter, setActiveFilter] = useState('all')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await userService.list()
      setUsers(res?.data || res || [])
    } catch { setUsers([]) }
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [load])

  const handleUpdate = async (id, data) => {
    await userService.update(id, data).catch(err => setError(err?.response?.data?.detail || err?.message || 'Failed to update user'))
    await load()
  }

  const handleDeactivate = async (id) => {
    await userService.deactivate(id).catch(err => setError(err?.response?.data?.detail || err?.message || 'Failed to deactivate user'))
    await load()
  }

  const visible = users.filter(u => {
    if (roleFilter !== 'all' && u.role !== roleFilter) return false
    if (activeFilter === 'active'   && !u.is_active)  return false
    if (activeFilter === 'inactive' && u.is_active)   return false
    return true
  })

  const counts = {
    total:    users.length,
    active:   users.filter(u => u.is_active).length,
    admin:    users.filter(u => u.role === 'ADMIN' || u.role === 'OWNER').length,
  }

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-white mb-1">User Management</h1>
          <p className="text-sm text-neutral-400">Manage team members, roles, and access within your tenant.</p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={load} className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[var(--border-subtle)] text-xs text-neutral-300 hover:border-white/20">
            <RefreshCw size={12} className={loading ? 'animate-spin' : ''} /> Refresh
          </button>
          <button
            onClick={() => setInviting(true)}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-white text-black text-xs font-medium hover:bg-neutral-200"
          >
            <Plus size={13} /> Invite user
          </button>
        </div>
      </header>

      {/* Inline error banner */}
      {error && (
        <div className="flex items-center justify-between gap-3 px-3 py-2 rounded-lg bg-red-500/10 border border-red-500/20 text-xs text-red-400">
          <span>{error}</span>
          <button onClick={() => setError('')} className="shrink-0 hover:text-red-200"><X size={13} /></button>
        </div>
      )}

      {/* Stats */}
      <div className="grid grid-cols-3 gap-3">
        {[
          { label: 'Total members',  value: counts.total },
          { label: 'Active',         value: counts.active },
          { label: 'Owners / admins', value: counts.admin },
        ].map(({ label, value }) => (
          <div key={label} className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl p-4">
            <div className="text-2xl font-semibold text-white">{value}</div>
            <div className="text-xs text-neutral-500 mt-1">{label}</div>
          </div>
        ))}
      </div>

      {/* Filters */}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex gap-1.5">
          {['all', 'active', 'inactive'].map(f => (
            <button
              key={f}
              onClick={() => setActiveFilter(f)}
              className={`text-xs px-3 py-1.5 rounded-lg border transition-all capitalize ${activeFilter === f ? 'border-white/30 bg-white/[0.08] text-white' : 'border-[var(--border-subtle)] text-neutral-500 hover:border-white/20'}`}
            >
              {f}
            </button>
          ))}
        </div>
        <div className="flex gap-1.5">
          {['all', ...ROLES].map(r => (
            <button
              key={r}
              onClick={() => setRoleFilter(r)}
              className={`text-[10px] px-2.5 py-1 rounded-lg border transition-all ${roleFilter === r ? 'border-white/20 bg-white/[0.05] text-white' : 'border-[var(--border-subtle)] text-neutral-600 hover:border-white/10'}`}
            >
              {r === 'all' ? 'All roles' : r}
            </button>
          ))}
        </div>
      </div>

      {/* User list */}
      <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl overflow-hidden">
        {loading ? (
          <div className="p-2">
            <SkeletonLoader variant="row" count={4} />
            <span className="sr-only" role="status">Loading users…</span>
          </div>
        ) : visible.length === 0 ? (
          users.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 px-6 gap-4 text-center">
              <Users size={32} className="text-neutral-700" aria-hidden="true" />
              <div className="space-y-1">
                <p className="text-sm text-neutral-200 font-medium">No users in this workspace yet</p>
                <p className="text-xs text-neutral-500 max-w-md leading-relaxed">
                  The first user is created the moment you complete{' '}
                  <Link to="/signup" className="text-blue-300 hover:text-blue-200 underline underline-offset-2">
                    /signup
                  </Link>{' '}
                  with your work email — or SCIM auto-provisions everyone on their next login once SSO is wired up.
                </p>
              </div>
              <div className="flex items-center justify-center gap-2 flex-wrap">
                <button
                  onClick={() => setInviting(true)}
                  className="flex items-center gap-2 px-4 py-2 rounded-lg bg-white text-black text-xs font-medium hover:bg-neutral-200"
                >
                  <UserPlus size={13} /> Invite a user
                </button>
                <Link
                  to="/sso"
                  className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg border border-[var(--border-subtle)] text-xs text-neutral-300 hover:border-white/20 hover:text-white"
                >
                  <Shield size={12} /> Configure SCIM at /settings/sso
                </Link>
              </div>
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center py-16 gap-3">
              <Users size={28} className="text-neutral-700" />
              <p className="text-sm text-neutral-500">No users match the filters.</p>
              <button
                onClick={() => { setActiveFilter('all'); setRoleFilter('all') }}
                className="text-[11px] text-neutral-400 hover:text-white underline underline-offset-2"
              >
                Clear filters
              </button>
            </div>
          )
        ) : (
          visible.map(u => (
            <UserRow
              key={u.id}
              user={u}
              onUpdate={handleUpdate}
              onDeactivate={handleDeactivate}
            />
          ))
        )}
      </div>

      <p className="text-[11px] text-neutral-700">
        Role changes take effect immediately. Deactivated users cannot log in but their audit history is preserved.
        Click a role badge to change it; click the user icon to toggle active status.
      </p>

      {inviting && <InviteModal onClose={() => setInviting(false)} onInvited={load} />}
    </div>
  )
}
