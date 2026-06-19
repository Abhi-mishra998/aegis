// Sprint S5 (2026-06-19) — TeamSettings page. Create/rename/move teams,
// assign managers, set per-team budget caps. The Team page (/team) keeps
// the per-employee + per-department rollups; this page is the
// administrative companion under Settings → Teams.

import React, { useCallback, useEffect, useMemo, useState } from 'react'
import {
  UsersRound, Plus, Trash2, Save, Loader2, AlertCircle,
  ChevronRight, RefreshCw, Settings2,
} from 'lucide-react'
import { teamsService } from '../services/api'
import { useRole } from '../hooks/useRole'

export default function TeamSettings() {
  const { isOwner, isAdmin } = useRole()
  const canMutate = isOwner || isAdmin

  const [teams, setTeams] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')
  const [newParent, setNewParent] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const r = await teamsService.list()
      setTeams((r?.data || r || []).slice())
    } catch (err) {
      setError(err.message || 'Failed to load teams.')
    } finally {
      setLoading(false)
    }
  }, [])
  useEffect(() => { load() }, [load])

  const createTeam = useCallback(async () => {
    if (!newName.trim()) return
    setCreating(true)
    setError('')
    try {
      await teamsService.create({
        name: newName.trim(),
        parent_team_id: newParent || null,
      })
      setNewName('')
      setNewParent('')
      load()
    } catch (err) {
      setError(err.message || 'Create failed.')
    } finally {
      setCreating(false)
    }
  }, [newName, newParent, load])

  const removeTeam = useCallback(async (teamId) => {
    if (!window.confirm('Delete this team? Members get un-assigned; sub-teams get un-parented.')) return
    try {
      await teamsService.remove(teamId)
      load()
    } catch (err) {
      setError(err.message || 'Delete failed.')
    }
  }, [load])

  // Build a flat-rendered tree by parent_id grouping.
  const tree = useMemo(() => buildTree(teams), [teams])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="animate-spin text-neutral-500" size={24} />
      </div>
    )
  }

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-white mb-1 flex items-center gap-2">
            <UsersRound size={22} /> Teams
          </h1>
          <p className="text-sm text-neutral-500">
            Hierarchical teams replace the free-text department field. A CFO
            sees All; an Engineering Lead sees only Engineering + sub-teams.
            Per-team budget caps gate runaway agents at the team boundary.
          </p>
        </div>
        <button
          onClick={load}
          className="flex items-center gap-1 px-2 py-1.5 rounded-lg text-xs text-neutral-500 hover:text-neutral-300"
          aria-label="Refresh"
        >
          <RefreshCw size={11} />
        </button>
      </header>

      {!canMutate && (
        <div className="bg-neutral-900 border border-[var(--border-subtle)] rounded-lg p-3 text-xs text-neutral-400">
          Teams can only be configured by Owners or Admins. You can still view the tree below.
        </div>
      )}

      {error && (
        <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-3 text-xs text-red-300 flex items-center gap-2">
          <AlertCircle size={12} /> {error}
        </div>
      )}

      {canMutate && (
        <div className="bg-neutral-950 border border-[var(--border-subtle)] rounded-lg p-4 space-y-3">
          <div className="flex items-center gap-2 text-sm text-neutral-200">
            <Plus size={14} /> New team
          </div>
          <div className="flex items-center gap-2">
            <input
              type="text"
              placeholder="Team name (e.g. Engineering / Frontend)"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              className="flex-1 bg-neutral-900 border border-[var(--border-subtle)] rounded-md px-2 py-1.5 text-xs text-neutral-200 placeholder:text-neutral-600"
            />
            <select
              value={newParent}
              onChange={(e) => setNewParent(e.target.value)}
              className="bg-neutral-900 border border-[var(--border-subtle)] rounded-md px-2 py-1.5 text-xs text-neutral-200"
            >
              <option value="">No parent</option>
              {teams.map((t) => (
                <option key={t.id} value={t.id}>{t.name}</option>
              ))}
            </select>
            <button
              onClick={createTeam}
              disabled={creating || !newName.trim()}
              className="px-3 py-1.5 rounded-lg bg-emerald-600/20 border border-emerald-600/40 text-xs text-emerald-200 hover:bg-emerald-600/30 disabled:opacity-40"
            >
              {creating ? 'Creating…' : 'Create'}
            </button>
          </div>
        </div>
      )}

      <div className="space-y-2">
        {tree.length === 0 ? (
          <div className="text-sm text-neutral-500 text-center py-8 border border-dashed border-[var(--border-subtle)] rounded-lg">
            No teams yet. Create your first team above.
          </div>
        ) : (
          tree.map((node) => (
            <TeamRow
              key={node.team.id}
              node={node}
              depth={0}
              canMutate={canMutate}
              onRemove={removeTeam}
            />
          ))
        )}
      </div>
    </div>
  )
}

function TeamRow({ node, depth, canMutate, onRemove }) {
  return (
    <>
      <div
        className="flex items-center gap-3 px-3 py-2 rounded-lg border border-[var(--border-subtle)] bg-neutral-950 hover:bg-neutral-900"
        style={{ marginLeft: depth * 20 }}
      >
        <Settings2 size={14} className="text-neutral-500" />
        <span className="text-sm text-neutral-200">{node.team.name}</span>
        {node.team.daily_budget_usd_cap !== null && (
          <span className="text-[11px] text-neutral-500">
            ${node.team.daily_budget_usd_cap}/day
          </span>
        )}
        {node.team.monthly_budget_usd_cap !== null && (
          <span className="text-[11px] text-neutral-500">
            ${node.team.monthly_budget_usd_cap}/mo
          </span>
        )}
        <span className="ml-auto" />
        {canMutate && (
          <button
            onClick={() => onRemove(node.team.id)}
            className="flex items-center gap-1 px-2 py-1 rounded text-[11px] text-red-300 hover:bg-red-500/10"
            aria-label="Delete team"
          >
            <Trash2 size={11} />
          </button>
        )}
      </div>
      {node.children.map((child) => (
        <TeamRow
          key={child.team.id}
          node={child}
          depth={depth + 1}
          canMutate={canMutate}
          onRemove={onRemove}
        />
      ))}
    </>
  )
}

function buildTree(teams) {
  const byId = new Map(teams.map((t) => [t.id, { team: t, children: [] }]))
  const roots = []
  for (const node of byId.values()) {
    const parent = node.team.parent_team_id
    if (parent && byId.has(parent)) {
      byId.get(parent).children.push(node)
    } else {
      roots.push(node)
    }
  }
  return roots
}
