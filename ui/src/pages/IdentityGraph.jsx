import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { RefreshCw, Shield, AlertTriangle, Activity, Eye, Zap, Users, X } from 'lucide-react'
import { graphService } from '../services/api'

// ── Lightweight force-directed layout (no new deps) ─────────────────────────
// Layout produces {x, y} per node id, run once per render of nodes/edges set.
function layoutGraph(nodes, edges, w = 760, h = 480) {
  const pos = {}
  // seed: golden-angle spiral, then a few relaxation passes
  const n = nodes.length || 1
  const golden = Math.PI * (3 - Math.sqrt(5))
  nodes.forEach((node, i) => {
    const r = Math.sqrt((i + 0.5) / n) * Math.min(w, h) * 0.38
    const a = i * golden
    pos[node.id] = { x: w / 2 + r * Math.cos(a), y: h / 2 + r * Math.sin(a), vx: 0, vy: 0 }
  })
  const passes = 60
  for (let it = 0; it < passes; it++) {
    // node repulsion
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = pos[nodes[i].id], b = pos[nodes[j].id]
        const dx = b.x - a.x, dy = b.y - a.y
        const d2 = dx * dx + dy * dy + 1
        const k = 4500 / d2
        const f = k / Math.sqrt(d2)
        a.vx -= dx * f; a.vy -= dy * f
        b.vx += dx * f; b.vy += dy * f
      }
    }
    // edge attraction
    edges.forEach((e) => {
      const a = pos[e.src_node_id], b = pos[e.dst_node_id]
      if (!a || !b) return
      const dx = b.x - a.x, dy = b.y - a.y
      const f = 0.005
      a.vx += dx * f; a.vy += dy * f
      b.vx -= dx * f; b.vy -= dy * f
    })
    // damping + clamp
    nodes.forEach((node) => {
      const p = pos[node.id]
      p.vx *= 0.85; p.vy *= 0.85
      p.x += p.vx; p.y += p.vy
      p.x = Math.max(20, Math.min(w - 20, p.x))
      p.y = Math.max(20, Math.min(h - 20, p.y))
    })
  }
  return pos
}

const TYPE_COLOR = {
  agent:    '#60a5fa',
  human:    '#a78bfa',
  tool:     '#34d399',
  resource: '#fbbf24',
  tenant:   '#94a3b8',
}

const STATUS_COLOR = (outcome) =>
  outcome === 'deny' ? '#ef4444'
  : outcome === 'error' ? '#f97316'
  : '#404040'

function trustColor(score) {
  if (score < 0.3) return '#ef4444'
  if (score < 0.6) return '#f97316'
  if (score < 0.85) return '#eab308'
  return '#22c55e'
}

export default function IdentityGraph() {
  const [nodes, setNodes] = useState([])
  const [edges, setEdges] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [selected, setSelected] = useState(null)
  const [blast, setBlast] = useState(null)
  const [scenario, setScenario] = useState('stolen_token')
  const [depth, setDepth] = useState(3)
  const [simResult, setSimResult] = useState(null)
  const [simBusy, setSimBusy] = useState(false)
  const [blastError, setBlastError] = useState('')
  const [simError, setSimError] = useState('')

  const fetchAll = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const res = await graphService.listAgents(500)
      setNodes(res?.data?.nodes || [])
      setEdges(res?.data?.edges || [])
    } catch (e) { setError(e.message) }
    finally { setLoading(false) }
  }, [])

  useEffect(() => {
    fetchAll()
    const t = setInterval(fetchAll, 30_000)
    return () => clearInterval(t)
  }, [fetchAll])

  const pos = useMemo(() => layoutGraph(nodes, edges), [nodes, edges])

  const handleNodeClick = async (n) => {
    setSelected(n)
    setBlast(null)
    setBlastError('')
    try {
      const res = await graphService.getBlastRadius(n.id, depth)
      setBlast(res?.data || null)
    } catch (e) {
      // 2026-05-14: surface failures instead of silent console.warn.
      setBlastError(e?.message || 'Blast-radius unavailable')
    }
  }

  const runSimulation = async () => {
    if (!selected) return
    setSimBusy(true)
    setSimError('')
    try {
      const res = await graphService.simulateCompromise({
        actor_node_id: selected.id, scenario, depth,
      })
      setSimResult(res?.data || null)
    } catch (e) {
      setSimError(e?.message || 'Compromise simulation failed')
    }
    finally { setSimBusy(false) }
  }

  return (
    <div className="space-y-4 animate-fade-in">
      <div className="page-header">
        <div>
          <h1 className="text-2xl font-bold text-white">Agent Identity Graph</h1>
          <p className="text-xs text-neutral-500 mt-1">Runtime relationships · trust scores · blast-radius simulation</p>
        </div>
        <button
          onClick={fetchAll}
          disabled={loading}
          className="flex items-center gap-2 px-3 py-2 rounded-lg bg-white/5 border border-white/10 text-xs text-neutral-300 hover:text-white hover:bg-white/10 disabled:opacity-50"
        >
          <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
          {loading ? 'Loading…' : 'Refresh'}
        </button>
      </div>

      {error && (
        <div className="rounded-xl border border-red-500/20 bg-red-500/5 p-4 text-xs text-red-400">
          Failed to load graph: {error}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2 rounded-2xl border border-white/10 bg-[#0a0a0a] p-3">
          <div className="flex items-center gap-2 mb-2 px-2">
            <Activity size={12} className="text-cyan-400" />
            <span className="text-xs font-semibold text-white">Live Graph</span>
            <span className="ml-auto text-[10px] font-mono text-neutral-600">
              {nodes.length} nodes · {edges.length} edges
            </span>
          </div>
          <svg viewBox="0 0 760 480" className="w-full h-[480px]">
            <defs>
              <marker id="arrow" viewBox="0 -5 10 10" refX="22" refY="0" markerWidth="6" markerHeight="6" orient="auto">
                <path d="M0,-4L8,0L0,4" fill="#525252" />
              </marker>
            </defs>
            {edges.map((e) => {
              const a = pos[e.src_node_id], b = pos[e.dst_node_id]
              if (!a || !b) return null
              return (
                <line
                  key={e.id}
                  x1={a.x} y1={a.y} x2={b.x} y2={b.y}
                  stroke={STATUS_COLOR(e.outcome)}
                  strokeOpacity={0.45}
                  strokeWidth={1 + Math.min(2, (e.risk_score || 0) * 2)}
                  markerEnd="url(#arrow)"
                />
              )
            })}
            {nodes.map((n) => {
              const p = pos[n.id]
              if (!p) return null
              const isSelected = selected?.id === n.id
              return (
                <g key={n.id} onClick={() => handleNodeClick(n)} style={{ cursor: 'pointer' }}>
                  <circle
                    cx={p.x} cy={p.y}
                    r={isSelected ? 14 : 9}
                    fill={TYPE_COLOR[n.node_type] || '#525252'}
                    stroke={trustColor(n.trust_score)}
                    strokeWidth={isSelected ? 3 : 2}
                  />
                  <text x={p.x} y={p.y + 22} fontSize="9" fill="#a3a3a3" textAnchor="middle" fontFamily="monospace">
                    {(n.name || n.external_id || '').slice(0, 14)}
                  </text>
                </g>
              )
            })}
          </svg>
          <div className="flex items-center gap-3 mt-2 px-2 text-[10px] font-mono text-neutral-600">
            <span className="inline-flex items-center gap-1"><span className="w-2 h-2 rounded-full" style={{ background: TYPE_COLOR.agent }} /> agent</span>
            <span className="inline-flex items-center gap-1"><span className="w-2 h-2 rounded-full" style={{ background: TYPE_COLOR.tool }} /> tool</span>
            <span className="inline-flex items-center gap-1"><span className="w-2 h-2 rounded-full" style={{ background: TYPE_COLOR.resource }} /> resource</span>
            <span className="ml-4">ring color = trust score</span>
            <span>edge color = outcome (red = deny, orange = error)</span>
          </div>
        </div>

        <div className="space-y-4">
          <div className="rounded-2xl border border-white/10 bg-white/[0.02] p-4">
            <div className="flex items-center gap-2 mb-3">
              <Eye size={13} className="text-blue-400" />
              <span className="text-sm font-semibold text-white">Selected node</span>
            </div>
            {selected ? (
              <div className="space-y-2 text-xs">
                <div className="flex justify-between"><span className="text-neutral-500">name</span><span className="text-white truncate">{selected.name}</span></div>
                <div className="flex justify-between"><span className="text-neutral-500">type</span><span className="text-white">{selected.node_type}</span></div>
                <div className="flex justify-between"><span className="text-neutral-500">trust</span>
                  <span className="font-mono" style={{ color: trustColor(selected.trust_score) }}>{selected.trust_score?.toFixed(3)}</span>
                </div>
                <div className="flex justify-between"><span className="text-neutral-500">drift</span><span className="text-white font-mono">{selected.drift_score?.toFixed(3)}</span></div>
              </div>
            ) : (
              <p className="text-xs text-neutral-600">Click any node in the graph.</p>
            )}
          </div>

          <div className="rounded-2xl border border-white/10 bg-white/[0.02] p-4">
            <div className="flex items-center gap-2 mb-3">
              <Zap size={13} className="text-orange-400" />
              <span className="text-sm font-semibold text-white">Compromise Simulation</span>
            </div>
            <div className="space-y-2">
              <select
                value={scenario}
                onChange={(e) => setScenario(e.target.value)}
                className="w-full bg-black border border-white/10 rounded-lg px-2 py-1.5 text-xs text-white"
              >
                <option value="stolen_token">stolen_token</option>
                <option value="rogue_agent">rogue_agent</option>
                <option value="prompt_injection">prompt_injection</option>
                <option value="malicious_tool">malicious_tool</option>
                <option value="lateral_movement">lateral_movement</option>
                <option value="runaway_autonomy">runaway_autonomy</option>
              </select>
              <div className="flex items-center gap-2 text-xs">
                <span className="text-neutral-500">depth</span>
                <input
                  type="number" min={1} max={6} value={depth}
                  onChange={(e) => setDepth(Number(e.target.value) || 3)}
                  className="w-16 bg-black border border-white/10 rounded px-2 py-1 text-white font-mono"
                />
                <button
                  onClick={runSimulation}
                  disabled={!selected || simBusy}
                  className="ml-auto px-3 py-1 rounded-lg bg-red-500/20 border border-red-500/30 text-red-300 text-xs font-bold hover:bg-red-500/30 disabled:opacity-40"
                >
                  {simBusy ? 'Running…' : 'Run'}
                </button>
              </div>
            </div>
          </div>

          {blast && (
            <div className="rounded-2xl border border-white/10 bg-white/[0.02] p-4 max-h-[280px] overflow-auto">
              <div className="flex items-center gap-2 mb-3">
                <AlertTriangle size={13} className="text-red-400" />
                <span className="text-sm font-semibold text-white">Blast radius (3 hops)</span>
              </div>
              <div className="text-xs space-y-1">
                <div className="flex justify-between"><span className="text-neutral-500">reachable</span><span className="font-mono text-white">{blast.reachable_nodes?.length || 0}</span></div>
                <div className="flex justify-between"><span className="text-neutral-500">affected resources</span><span className="font-mono text-amber-300">{blast.affected_resources}</span></div>
                <div className="flex justify-between"><span className="text-neutral-500">risk</span><span className="font-mono text-red-400">{blast.risk_score?.toFixed(3)}</span></div>
              </div>
            </div>
          )}
          {blastError && (
            <div className="rounded-2xl border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-400" role="alert">
              Blast radius: {blastError}
            </div>
          )}
          {simError && (
            <div className="rounded-2xl border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-400" role="alert">
              Compromise simulation: {simError}
            </div>
          )}
        </div>
      </div>

      {/* Compromise Simulation Result Modal — centered, scrollable, never cut off */}
      {simResult && (
        <div
          role="dialog"
          aria-modal="true"
          className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm"
          onClick={() => setSimResult(null)}
        >
          <div
            className="relative w-full max-w-2xl max-h-[85vh] overflow-y-auto rounded-2xl
                       border border-red-500/30 bg-[#0a0a0a] p-6 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              onClick={() => setSimResult(null)}
              className="absolute right-4 top-4 p-1 rounded-lg text-neutral-500 hover:text-white hover:bg-white/5"
              aria-label="Close"
            >
              <X size={16} />
            </button>

            <div className="flex items-center gap-3 mb-4 pr-8">
              <div className="w-10 h-10 rounded-xl bg-red-500/15 border border-red-500/30 flex items-center justify-center">
                <AlertTriangle size={18} className="text-red-400" />
              </div>
              <div>
                <h2 className="text-lg font-bold text-white">Compromise Simulation</h2>
                <p className="text-xs text-neutral-500 mt-0.5">
                  Scenario: <span className="font-mono text-neutral-300">{simResult.scenario}</span>
                </p>
              </div>
              <span
                className="ml-auto inline-flex items-center gap-1 px-2 py-1 rounded-lg
                           bg-red-500/10 border border-red-500/30 text-red-300 text-[10px] font-mono font-bold"
              >
                {simResult.summary?.risk_classification || 'UNKNOWN'}
              </span>
            </div>

            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5">
              <Kpi label="Blast radius" value={simResult.blast_radius} />
              <Kpi
                label="Risk score"
                value={simResult.risk_score?.toFixed(3)}
                color={simResult.risk_score >= 0.8 ? 'text-red-400'
                      : simResult.risk_score >= 0.6 ? 'text-orange-400'
                      : simResult.risk_score >= 0.4 ? 'text-yellow-400'
                      : 'text-green-400'}
              />
              <Kpi label="Reachable" value={simResult.reachable_nodes?.length || 0} />
              <Kpi label="Workspaces" value={simResult.affected_tenants?.length || 0} />
            </div>

            <div className="rounded-xl border border-white/10 bg-white/[0.02] p-4 mb-4">
              <h3 className="text-xs font-semibold text-neutral-300 mb-2">Reachable nodes</h3>
              <div className="max-h-48 overflow-y-auto divide-y divide-white/5">
                {(simResult.reachable_nodes || []).slice(0, 50).map((n, i) => (
                  <div key={n.id || i} className="py-1.5 flex items-center gap-2 text-[11px] font-mono">
                    <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: TYPE_COLOR[n.type] || '#525252' }} />
                    <span className="text-white truncate flex-1">{n.name || n.id?.slice(0, 18)}</span>
                    <span className="text-neutral-500">{n.type}</span>
                    <span
                      className="font-bold"
                      style={{ color: trustColor(n.trust_score) }}
                    >
                      {Number(n.trust_score || 0).toFixed(2)}
                    </span>
                  </div>
                ))}
                {(!simResult.reachable_nodes || simResult.reachable_nodes.length === 0) && (
                  <p className="text-[11px] text-neutral-600 text-center py-3">No reachable nodes recorded.</p>
                )}
              </div>
            </div>

            <div className="flex justify-end gap-2">
              <button
                onClick={() => setSimResult(null)}
                className="px-4 py-1.5 rounded-lg bg-white/5 border border-white/10 text-xs text-neutral-300 hover:bg-white/10"
              >
                Close
              </button>
              <button
                onClick={runSimulation}
                disabled={simBusy}
                className="px-4 py-1.5 rounded-lg bg-red-500/20 border border-red-500/30 text-red-300 text-xs font-bold hover:bg-red-500/30 disabled:opacity-40"
              >
                {simBusy ? 'Re-running…' : 'Re-run'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function Kpi({ label, value, color = 'text-white' }) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.02] p-3">
      <p className="text-[10px] font-mono text-neutral-500 uppercase tracking-widest">{label}</p>
      <p className={`text-xl font-bold font-mono tabular-nums mt-1 ${color}`}>{value ?? '—'}</p>
    </div>
  )
}
