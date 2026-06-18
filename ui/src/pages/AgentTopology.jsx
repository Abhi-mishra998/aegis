// Sprint 4 — Agent Topology
//
// Renders the identity graph as a React-Flow topology — typed nodes
// (agents, tools, resources, tenants) and typed edges (invoke / read /
// write / delegate). Click any node to highlight its blast radius
// (bounded BFS already implemented backend-side in Sprint 1).
//
// Data sources:
//   GET /graph/agents             — list of nodes + edges
//   GET /graph/blast-radius/{id}  — set of nodes reachable from {id}

import React, { useCallback, useEffect, useMemo, useState } from 'react'
import ReactFlow, {
  Background,
  Controls,
  MarkerType,
} from 'reactflow'
import 'reactflow/dist/style.css'
import { graphService } from '../services/api'

// Node-type → palette. Identity graph stores node_type as string ("agent",
// "tool", "resource", "tenant", …). We keep the mapping permissive so new
// node types render with the default palette rather than disappearing.
const NODE_PALETTE = {
  agent:    { bg: '#0f172a', border: '#0ea5e9', fg: '#e0f2fe' },
  tool:     { bg: '#022c22', border: '#10b981', fg: '#d1fae5' },
  resource: { bg: '#2d1b00', border: '#f59e0b', fg: '#fef3c7' },
  tenant:   { bg: '#1e1b4b', border: '#a78bfa', fg: '#ede9fe' },
  default:  { bg: '#171717', border: '#525252', fg: '#e5e5e5' },
}

const EDGE_PALETTE = {
  invokes:   '#22d3ee',
  reads:     '#10b981',
  writes:    '#f59e0b',
  delegates: '#a78bfa',
  escalates: '#fb7185',
  default:   '#737373',
}

function paletteFor(nodeType) {
  return NODE_PALETTE[nodeType?.toLowerCase()] || NODE_PALETTE.default
}

function unwrap(r) { return r?.data ?? r }

// Deterministic, simple grid layout — group nodes by type into columns
// so the topology reads agents-left, tools-middle, resources-right
// without needing a forceful layout engine.
function layout(nodes) {
  const columns = { agent: 0, tool: 1, resource: 2, tenant: 3 }
  const counters = {}
  const X_STRIDE = 260
  const Y_STRIDE = 90
  return nodes.map((n) => {
    const col = columns[n.node_type?.toLowerCase()] ?? 4
    const row = counters[col] = (counters[col] ?? 0) + 1
    return { node: n, x: col * X_STRIDE + 40, y: row * Y_STRIDE }
  })
}

function buildFlow(apiNodes, apiEdges, highlightedIds) {
  const positioned = layout(apiNodes)
  const nodes = positioned.map(({ node, x, y }) => {
    const p = paletteFor(node.node_type)
    const highlighted = highlightedIds.has(node.id)
    return {
      id: node.id,
      data: { label: node.name || node.external_id || node.id?.slice(0, 8) },
      position: { x, y },
      style: {
        background: p.bg,
        border: `2px solid ${highlighted ? '#fbbf24' : p.border}`,
        color: p.fg,
        fontSize: 11,
        padding: 6,
        borderRadius: 6,
        boxShadow: highlighted ? '0 0 12px rgba(251, 191, 36, 0.7)' : undefined,
        minWidth: 140,
      },
    }
  })
  const edges = apiEdges.map((e, i) => ({
    id: `e${i}`,
    source: e.src_node_id,
    target: e.dst_node_id,
    label: e.edge_type || '',
    style: { stroke: EDGE_PALETTE[e.edge_type?.toLowerCase()] || EDGE_PALETTE.default },
    labelStyle: { fill: '#a3a3a3', fontSize: 9 },
    markerEnd: {
      type: MarkerType.ArrowClosed,
      color: EDGE_PALETTE[e.edge_type?.toLowerCase()] || EDGE_PALETTE.default,
    },
  }))
  return { nodes, edges }
}

export default function AgentTopology() {
  const [apiNodes, setApiNodes] = useState([])
  const [apiEdges, setApiEdges] = useState([])
  const [highlightedIds, setHighlightedIds] = useState(new Set())
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [selected, setSelected] = useState(null)

  const fetchTopology = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const resp = await graphService.listAgents(500)
      const payload = unwrap(resp) || {}
      setApiNodes(payload.nodes || [])
      setApiEdges(payload.edges || [])
    } catch (e) {
      setError(e?.message || 'Failed to load topology')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchTopology() }, [fetchTopology])

  const onSelectNode = useCallback(async (_evt, node) => {
    setSelected(node.id)
    try {
      const resp = await graphService.getBlastRadius(node.id, 3)
      const payload = unwrap(resp) || {}
      const reachable = new Set((payload.nodes || []).map((n) => n.node_id || n.id))
      reachable.add(node.id)
      setHighlightedIds(reachable)
    } catch (e) {
      setError(e?.message || 'Failed to compute blast radius')
    }
  }, [])

  const onClearSelection = useCallback(() => {
    setSelected(null)
    setHighlightedIds(new Set())
  }, [])

  const { nodes, edges } = useMemo(
    () => buildFlow(apiNodes, apiEdges, highlightedIds),
    [apiNodes, apiEdges, highlightedIds],
  )

  return (
    <div className="text-neutral-100 h-full">
      <header className="flex items-center justify-between px-6 py-4 border-b border-neutral-800">
        <div>
          <h1 className="text-xl font-semibold">Agent Topology</h1>
          <p className="text-sm text-neutral-400 mt-1">
            Click any node to highlight its blast radius — the set of
            nodes reachable through real recorded traffic (BFS, depth 3).
          </p>
        </div>
        <div className="flex gap-2">
          {selected && (
            <button
              onClick={onClearSelection}
              className="px-3 py-1 bg-neutral-800 hover:bg-neutral-700 rounded-md text-sm"
            >
              Clear selection
            </button>
          )}
          <button
            onClick={fetchTopology}
            disabled={loading}
            className="px-3 py-1 bg-neutral-800 hover:bg-neutral-700 rounded-md text-sm"
          >
            {loading ? 'Loading…' : 'Refresh'}
          </button>
        </div>
      </header>

      {error && (
        <div className="mx-6 my-3 text-sm bg-rose-950 border border-rose-700 text-rose-100 px-3 py-2 rounded">
          {error}
        </div>
      )}

      {apiNodes.length === 0 && !loading && (
        <p className="px-6 py-4 text-sm text-neutral-400">
          No identity-graph nodes for this workspace yet. Nodes are created
          from real <code>/execute</code> traffic — the topology fills in
          as agents call tools.
        </p>
      )}

      <div className="h-[calc(100vh-200px)] bg-neutral-950 border-t border-neutral-800">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodeClick={onSelectNode}
          fitView
          fitViewOptions={{ padding: 0.2 }}
          proOptions={{ hideAttribution: true }}
        >
          <Background color="#262626" gap={16} />
          <Controls position="bottom-right" />
        </ReactFlow>
      </div>
      <div className="px-6 py-3 text-xs text-neutral-500 flex justify-between border-t border-neutral-900">
        <span>{apiNodes.length} nodes · {apiEdges.length} edges</span>
        <span>
          Legend: <span className="text-sky-300">●</span> agent &nbsp;
                  <span className="text-emerald-300">●</span> tool &nbsp;
                  <span className="text-amber-300">●</span> resource &nbsp;
                  <span className="text-violet-300">●</span> workspace
        </span>
      </div>
    </div>
  )
}
