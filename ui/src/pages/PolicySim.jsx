import React, { useState, useCallback, useEffect } from 'react'
import { Link } from 'react-router-dom'
import {
  Play, Plus, Trash2, ChevronDown, ChevronUp,
  Shield, AlertTriangle, CheckCircle2, Loader2,
  ArrowRight, BarChart2, FlaskConical, GitMerge,
} from 'lucide-react'
import { policyService, registryService } from '../services/api'
import { useAgents } from '../hooks/useAgents'

const FIELDS    = ['risk_score', 'tool', 'inference_risk', 'behavior_risk', 'anomaly_score']
const OPERATORS = ['gt', 'gte', 'lt', 'lte', 'eq', 'neq']
const ACTIONS   = ['DENY', 'ALLOW', 'MONITOR', 'THROTTLE', 'ESCALATE']
const TIME_RANGES = ['1h', '6h', '24h', '7d']

const OP_LABEL = { gt: '>', gte: '≥', lt: '<', lte: '≤', eq: '=', neq: '≠' }

const ACTION_COLORS = {
  DENY:     'text-red-400 bg-red-500/10 border-red-500/20',
  ALLOW:    'text-green-400 bg-green-500/10 border-green-500/20',
  MONITOR:  'text-blue-400 bg-blue-500/10 border-blue-500/20',
  THROTTLE: 'text-amber-400 bg-amber-500/10 border-amber-500/20',
  ESCALATE: 'text-orange-400 bg-orange-500/10 border-orange-500/20',
}

function newCondition() { return { field: 'risk_score', operator: 'gt', value: '0.7' } }
function newRule() { return { conditions: [newCondition()], action: 'DENY', description: '' } }

function ConditionRow({ cond, onChange, onRemove }) {
  return (
    <div className="flex items-center gap-2 flex-wrap">
      <select name="field"
        value={cond.field}
        onChange={e => onChange({ ...cond, field: e.target.value })}
        className="bg-white/[0.04] border border-[var(--border-subtle)] rounded-lg px-2 py-1 text-xs text-white focus:outline-none"
      >
        {FIELDS.map(f => <option key={f} value={f}>{f}</option>)}
      </select>
      <select name="operator"
        value={cond.operator}
        onChange={e => onChange({ ...cond, operator: e.target.value })}
        className="bg-white/[0.04] border border-[var(--border-subtle)] rounded-lg px-2 py-1 text-xs text-white focus:outline-none w-16"
      >
        {OPERATORS.map(op => <option key={op} value={op}>{OP_LABEL[op]}</option>)}
      </select>
      <input name="value"
        value={cond.value}
        onChange={e => onChange({ ...cond, value: e.target.value })}
        placeholder="value"
        className="bg-white/[0.04] border border-[var(--border-subtle)] rounded-lg px-2 py-1 text-xs text-white w-20 focus:outline-none font-mono"
      />
      <button onClick={onRemove} className="text-neutral-600 hover:text-red-400 transition-colors">
        <Trash2 size={12} />
      </button>
    </div>
  )
}

function RuleCard({ rule, idx, onChange, onRemove }) {
  const [open, setOpen] = useState(true)

  const setConditions = (conditions) => onChange({ ...rule, conditions })
  const addCond = () => setConditions([...rule.conditions, newCondition()])
  const updateCond = (i, c) => setConditions(rule.conditions.map((x, j) => j === i ? c : x))
  const removeCond = (i) => setConditions(rule.conditions.filter((_, j) => j !== i))

  return (
    <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl overflow-hidden">
      <div className="flex items-center gap-3 px-4 py-3 border-b border-[var(--border-subtle)]">
        <span className="text-[10px] font-bold text-neutral-600 uppercase">Rule {idx + 1}</span>
        <select name="action"
          value={rule.action}
          onChange={e => onChange({ ...rule, action: e.target.value })}
          className={`text-xs font-bold px-2 py-0.5 rounded-full border bg-transparent focus:outline-none ${ACTION_COLORS[rule.action] || ''}`}
        >
          {ACTIONS.map(a => <option key={a} value={a}>{a}</option>)}
        </select>
        <input name="description"
          value={rule.description}
          onChange={e => onChange({ ...rule, description: e.target.value })}
          placeholder="Rule description (optional)"
          className="flex-1 bg-transparent text-xs text-neutral-400 placeholder-neutral-700 focus:outline-none"
        />
        <div className="flex items-center gap-1">
          <button onClick={() => setOpen(v => !v)} className="text-neutral-600 hover:text-white transition-colors">
            {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          </button>
          <button onClick={onRemove} className="text-neutral-600 hover:text-red-400 transition-colors">
            <Trash2 size={14} />
          </button>
        </div>
      </div>

      {open && (
        <div className="px-4 py-3 space-y-2">
          <p className="text-[10px] text-neutral-600 uppercase tracking-wide">Conditions (ALL must match)</p>
          {rule.conditions.map((c, i) => (
            <ConditionRow
              key={i}
              cond={c}
              onChange={(nc) => updateCond(i, nc)}
              onRemove={() => removeCond(i)}
            />
          ))}
          <button
            onClick={addCond}
            className="flex items-center gap-1 text-[11px] text-neutral-500 hover:text-white transition-colors"
          >
            <Plus size={11} /> Add condition
          </button>
        </div>
      )}
    </div>
  )
}

function DiffTable({ diff }) {
  if (!diff?.length) return (
    <p className="text-xs text-neutral-600 text-center py-6">No decision changes in the sampled events.</p>
  )
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-[10px] text-neutral-600 uppercase tracking-wide border-b border-[var(--border-subtle)]">
            <th className="text-left py-2 px-3">Tool</th>
            <th className="text-left py-2 px-3">Risk</th>
            <th className="text-left py-2 px-3">Was</th>
            <th className="text-left py-2 px-3"></th>
            <th className="text-left py-2 px-3">Would be</th>
            <th className="text-left py-2 px-3">Time</th>
          </tr>
        </thead>
        <tbody>
          {diff.map((d, i) => (
            <tr key={i} className="border-b border-[var(--border-subtle)] hover:bg-white/[0.02]">
              <td className="py-2 px-3 font-mono text-neutral-300">{d.tool}</td>
              <td className="py-2 px-3 font-mono">{Number(d.risk_score).toFixed(3)}</td>
              <td className="py-2 px-3">
                <span className={`px-1.5 py-0.5 rounded text-[10px] border ${ACTION_COLORS[d.old_decision?.toUpperCase()] || 'text-neutral-400 border-neutral-700'}`}>
                  {d.old_decision}
                </span>
              </td>
              <td className="py-2 px-3 text-neutral-600"><ArrowRight size={10} /></td>
              <td className="py-2 px-3">
                <span className={`px-1.5 py-0.5 rounded text-[10px] border ${ACTION_COLORS[d.new_decision?.toUpperCase()] || 'text-neutral-400 border-neutral-700'}`}>
                  {d.new_decision}
                </span>
              </td>
              <td className="py-2 px-3 text-neutral-600 font-mono">
                {d.timestamp ? new Date(d.timestamp).toLocaleString() : '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function PolicySim() {
  const { agents } = useAgents()
  const [agentId,    setAgentId]    = useState('')
  const [timeRange,  setTimeRange]  = useState('24h')
  const [rules,      setRules]      = useState([newRule()])
  const [running,    setRunning]    = useState(false)
  const [result,     setResult]     = useState(null)
  const [error,      setError]      = useState('')

  const updateRule = (i, r) => setRules(rules.map((x, j) => j === i ? r : x))
  const removeRule = (i)    => setRules(rules.filter((_, j) => j !== i))
  const addRule    = ()     => setRules([...rules, newRule()])

  const run = useCallback(async () => {
    if (!agentId.trim()) { setError('Enter an agent ID to simulate against.'); return }
    if (!rules.length)   { setError('Add at least one rule.'); return }
    setError('')
    setRunning(true)
    setResult(null)
    try {
      const res = await policyService.simulate({ policy: rules, agent_id: agentId.trim(), time_range: timeRange })
      setResult(res?.data || res)
    } catch (e) {
      setError(e?.message || 'Simulation failed.')
    } finally {
      setRunning(false)
    }
  }, [agentId, timeRange, rules])

  const total = result?.total_events ?? 0
  const changed = (result?.diff ?? []).length

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <header className="flex items-start justify-between gap-4 flex-wrap">
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold text-white mb-1">Policy Simulation</h1>
          <p className="text-sm text-neutral-400">
            Replay historical audit events through a proposed policy without affecting live traffic.
            No OPA calls, no writes — read-only dry run.
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <Link
            to="/policies?tab=editor"
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-white/10 text-neutral-300 text-xs hover:bg-white/[0.04] transition-colors"
          >
            <GitMerge size={11} aria-hidden="true" />
            Editor
          </Link>
          <Link
            to="/policies?tab=staging"
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-white/10 text-neutral-300 text-xs hover:bg-white/[0.04] transition-colors"
          >
            <FlaskConical size={11} aria-hidden="true" />
            Playground
          </Link>
        </div>
      </header>

      {/* Config */}
      <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl p-5 space-y-4">
        <h2 className="text-xs font-medium text-neutral-400 uppercase tracking-wider">Configuration</h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="block text-xs text-neutral-400 mb-1">Agent</label>
            {agents && agents.length > 0 ? (
              <select name="select"
                value={agentId}
                onChange={e => setAgentId(e.target.value)}
                className="w-full bg-white/[0.04] border border-[var(--border-subtle)] rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-white/20"
              >
                <option value="" className="bg-[#080808]">Select agent…</option>
                {agents.map(a => (
                  <option key={a.id} value={a.id} className="bg-[#080808]">{a.name || a.id}</option>
                ))}
              </select>
            ) : (
              <input name="input"
                value={agentId}
                onChange={e => setAgentId(e.target.value)}
                placeholder="UUID of agent to simulate against"
                className="w-full bg-white/[0.04] border border-[var(--border-subtle)] rounded-lg px-3 py-2 text-sm text-white placeholder-neutral-600 focus:outline-none focus:border-white/20 font-mono"
              />
            )}
          </div>
          <div>
            <label className="block text-xs text-neutral-400 mb-1">Event window</label>
            <div className="flex gap-1.5">
              {TIME_RANGES.map(t => (
                <button
                  key={t}
                  onClick={() => setTimeRange(t)}
                  className={`flex-1 py-2 rounded-lg border text-xs font-mono transition-all ${timeRange === t ? 'border-white/30 bg-white/[0.08] text-white' : 'border-[var(--border-subtle)] text-neutral-500 hover:border-white/20'}`}
                >
                  {t}
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* Rules */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-xs font-medium text-neutral-400 uppercase tracking-wider">Policy Rules</h2>
          <span className="text-[10px] text-neutral-600">Rules are evaluated top-to-bottom; first match wins.</span>
        </div>
        {rules.map((r, i) => (
          <RuleCard key={i} rule={r} idx={i} onChange={(nr) => updateRule(i, nr)} onRemove={() => removeRule(i)} />
        ))}
        <button
          onClick={addRule}
          className="w-full flex items-center justify-center gap-2 py-2.5 rounded-xl border border-dashed border-[var(--border-subtle)] text-xs text-neutral-500 hover:text-white hover:border-white/20 transition-all"
        >
          <Plus size={13} /> Add rule
        </button>
      </div>

      {error && (
        <div className="flex items-center gap-2 p-3 bg-red-500/10 border border-red-500/20 rounded-lg text-sm text-red-400">
          <AlertTriangle size={14} /> {error}
        </div>
      )}

      <button
        onClick={run}
        disabled={running}
        className="flex items-center gap-2 px-6 py-2.5 rounded-lg bg-white text-black text-sm font-medium hover:bg-neutral-200 disabled:opacity-50"
      >
        {running ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
        {running ? 'Simulating…' : 'Run Simulation'}
      </button>

      {/* Loading skeleton while simulating */}
      {running && !result && (
        <div className="space-y-4" aria-label="Simulating">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl p-4 animate-pulse">
                <div className="h-2 bg-white/[0.06] rounded w-2/3 mb-3" />
                <div className="h-7 bg-white/[0.08] rounded w-1/2" />
              </div>
            ))}
          </div>
          <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl p-4 space-y-2 animate-pulse">
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="h-3 bg-white/[0.05] rounded" style={{ width: `${70 + i * 5}%` }} />
            ))}
          </div>
        </div>
      )}

      {/* Results */}
      {result && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {[
              { label: 'Events replayed',  value: total,                      icon: BarChart2,     color: 'text-white' },
              { label: 'Would allow',      value: result.would_allow ?? 0,    icon: CheckCircle2,  color: 'text-green-400' },
              { label: 'Would deny',       value: result.would_deny  ?? 0,    icon: Shield,        color: 'text-red-400' },
              { label: 'Decision changes', value: changed,                     icon: AlertTriangle, color: changed > 0 ? 'text-amber-400' : 'text-neutral-500' },
            ].map(({ label, value, icon: Icon, color }) => (
              <div key={label} className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl p-4">
                <div className="flex items-center gap-1.5 mb-2">
                  <Icon size={12} className="text-neutral-500" />
                  <span className="text-[10px] uppercase tracking-wide text-neutral-500">{label}</span>
                </div>
                <div className={`text-2xl font-semibold ${color}`}>{value.toLocaleString()}</div>
              </div>
            ))}
          </div>

          {changed > 0 && (
            <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl overflow-hidden">
              <div className="px-4 py-3 border-b border-[var(--border-subtle)]">
                <h3 className="text-xs font-medium text-white">Decision Changes (sample of up to 20)</h3>
                <p className="text-[11px] text-neutral-500 mt-0.5">These events would have a different outcome under the proposed policy.</p>
              </div>
              <DiffTable diff={result.diff} />
            </div>
          )}

          {changed === 0 && total > 0 && (
            <div className="flex items-center gap-2 p-4 bg-green-500/10 border border-green-500/20 rounded-xl text-sm text-green-400">
              <CheckCircle2 size={16} />
              No decision changes — this policy produces identical outcomes to the current one for all {total} sampled events.
            </div>
          )}
        </div>
      )}
    </div>
  )
}
