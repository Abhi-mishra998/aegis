// Sprint 3.4 — Decision Explorer
//
// Renders one /execute decision as a React-Flow span graph. The data source
// is /flight/decision/{request_id}/graph (Sprint 3.3) which already returns
// nodes in canonical pipeline order, edges between consecutive stages, and
// the token/USD totals for the Trace Overview panel.
//
// Three views (Omni-style toggle):
//   * Graph     — React Flow visualization, nodes coloured by outcome
//   * Timeline  — vertical list of stages with latency + risk
//   * JSON      — the raw API payload (for copy-paste into a ticket)
//
// The page accepts ?request_id=... in the URL so it deep-links from
// other pages (Flight Recorder, Forensics, Live Feed).

import React, { useEffect, useMemo, useState, useCallback } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import ReactFlow, {
  Background,
  Controls,
  MarkerType,
} from 'reactflow'
import 'reactflow/dist/style.css'
import { flightService } from '../services/api'

const VIEWS = ['Graph', 'Timeline', 'JSON']

// Outcome → Tailwind colour. Kept in one place so a future stage palette
// change touches a single map.
const OUTCOME_COLORS = {
  allow:     { fg: 'text-emerald-100', bg: 'bg-emerald-900/70', border: 'border-emerald-500' },
  deny:      { fg: 'text-rose-100',    bg: 'bg-rose-900/80',    border: 'border-rose-500'    },
  throttle:  { fg: 'text-amber-100',   bg: 'bg-amber-900/70',   border: 'border-amber-500'   },
  escalate:  { fg: 'text-violet-100',  bg: 'bg-violet-900/70',  border: 'border-violet-500'  },
  kill:      { fg: 'text-rose-50',     bg: 'bg-rose-950',       border: 'border-rose-700'    },
  skipped:   { fg: 'text-neutral-300', bg: 'bg-neutral-800',    border: 'border-neutral-600' },
  default:   { fg: 'text-neutral-100', bg: 'bg-neutral-800/80', border: 'border-neutral-600' },
}

function outcomeStyle(outcome) {
  return OUTCOME_COLORS[outcome?.toLowerCase()] || OUTCOME_COLORS.default
}

// Render one stage as a React-Flow node.
function StageNode({ data }) {
  const style = outcomeStyle(data.outcome)
  return (
    <div
      className={`rounded-md ${style.bg} ${style.border} ${style.fg} border-2 px-3 py-2 min-w-[180px] shadow-md`}
    >
      <div className="text-xs uppercase tracking-wide opacity-70">{data.label}</div>
      <div className="text-sm font-semibold mt-1">
        {data.outcome ? data.outcome.toUpperCase() : data.status}
      </div>
      <div className="text-xs mt-1 flex justify-between gap-3">
        <span>risk {Number.isFinite(data.riskScore) ? data.riskScore.toFixed(2) : '—'}</span>
        <span>{Number.isFinite(data.latencyMs) ? `${data.latencyMs}ms` : '—'}</span>
      </div>
      {data.summary && (
        <div className="text-[10px] mt-1 opacity-80 truncate" title={data.summary}>
          {data.summary}
        </div>
      )}
    </div>
  )
}

const nodeTypes = { stage: StageNode }

// Lay the present stages out left-to-right at a fixed x-stride so the
// React Flow viewport doesn't need autolayout.
function layoutNodes(apiNodes) {
  const X_STRIDE = 240
  const Y = 100
  return apiNodes.map((n, i) => ({
    id: n.id,
    type: 'stage',
    position: { x: i * X_STRIDE, y: Y },
    data: {
      label:      n.label,
      outcome:    n.outcome,
      status:     n.status,
      riskScore:  n.risk_score,
      latencyMs:  n.latency_ms,
      summary:    n.summary,
    },
  }))
}

function layoutEdges(apiEdges) {
  return apiEdges.map((e, i) => ({
    id:    `e${i}`,
    source: e.source,
    target: e.target,
    label:  e.signal || '',
    style:  { stroke: '#737373' },
    labelStyle: { fill: '#a3a3a3', fontSize: 10 },
    markerEnd: { type: MarkerType.ArrowClosed, color: '#737373' },
  }))
}

function TimelineView({ graph }) {
  if (!graph) return null
  return (
    <ol className="space-y-2">
      {graph.nodes.map((n, idx) => {
        const style = outcomeStyle(n.outcome)
        return (
          <li
            key={n.id}
            className={`flex items-start gap-3 rounded-md ${style.bg} ${style.border} ${style.fg} border px-3 py-2`}
          >
            <span className="text-xs font-mono opacity-70 w-8">{idx}</span>
            <div className="flex-1">
              <div className="flex justify-between">
                <span className="font-semibold">{n.label}</span>
                <span className="text-xs">
                  {Number.isFinite(n.latency_ms) ? `${n.latency_ms}ms` : '—'}
                  {' · '}
                  risk {Number.isFinite(n.risk_score) ? n.risk_score.toFixed(2) : '—'}
                </span>
              </div>
              <div className="text-xs opacity-80 mt-1">
                {n.outcome ? `outcome: ${n.outcome}` : `status: ${n.status}`}
              </div>
              {n.summary && (
                <div className="text-xs mt-1 opacity-70">{n.summary}</div>
              )}
            </div>
          </li>
        )
      })}
    </ol>
  )
}

function JsonView({ graph }) {
  return (
    <pre className="text-xs bg-neutral-950 text-neutral-200 p-3 rounded-md overflow-auto max-h-[60vh] whitespace-pre-wrap break-all">
      {JSON.stringify(graph, null, 2)}
    </pre>
  )
}

function TraceOverview({ graph }) {
  if (!graph) return null
  const overview = [
    { label: 'Decision',     value: graph.timeline?.final_decision || '—' },
    { label: 'Final risk',   value: graph.timeline?.final_risk?.toFixed(2) ?? '—' },
    { label: 'Total latency', value: graph.total_latency_ms != null ? `${graph.total_latency_ms} ms` : '—' },
    { label: 'Stages',       value: graph.nodes.length },
    { label: 'Tokens in',    value: graph.tokens_in ?? '—' },
    { label: 'Tokens out',   value: graph.tokens_out ?? '—' },
    { label: 'Est. USD',     value: graph.estimated_usd != null ? `$${graph.estimated_usd.toFixed(4)}` : '—' },
  ]
  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-2 mb-4">
      {overview.map((o) => (
        <div key={o.label} className="rounded-md border border-neutral-800 bg-neutral-900/60 px-3 py-2">
          <div className="text-[10px] uppercase tracking-wide text-neutral-400">{o.label}</div>
          <div className="text-sm font-semibold text-neutral-100 mt-1">{o.value}</div>
        </div>
      ))}
    </div>
  )
}

// R2 — make the enforcement=audit-as-one-act claim visible. Pulls the
// decision stage's completed_at and the audit stage's started_at from
// the same span graph and surfaces the gap in milliseconds. The point:
// the policy decision and the signed audit row are written in the same
// request lifecycle — there is no window where an action happened but
// the record didn't.
function EnforcementAuditPanel({ graph }) {
  if (!graph || !Array.isArray(graph.nodes)) return null

  const findStage = (...names) => {
    const lower = (s) => String(s || '').toLowerCase()
    for (const n of graph.nodes) {
      const nm = lower(n.stage || n.name || n.type || '')
      if (names.some(want => nm.includes(want))) return n
    }
    return null
  }
  const decisionNode = findStage('decision', 'policy')
  const auditNode    = findStage('audit')
  if (!decisionNode || !auditNode) return null

  const dEnd = decisionNode.completed_at || decisionNode.ended_at
  const aStart = auditNode.started_at
  if (!dEnd || !aStart) return null

  let gapMs = null
  try {
    gapMs = Math.max(0, Math.round(new Date(aStart).getTime() - new Date(dEnd).getTime()))
  } catch { /* leave null */ }

  return (
    <div className="rounded-lg border border-emerald-900/60 bg-emerald-950/30 px-4 py-3 mb-4">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <div className="text-xs uppercase tracking-wider text-emerald-300 font-semibold">
            Enforcement = audit (one act)
          </div>
          <div className="text-sm text-neutral-200 mt-1 leading-relaxed">
            Decision <span className="text-emerald-200 font-mono">{dEnd.slice(11, 23)}</span>{' '}→{' '}
            audit row sealed <span className="text-emerald-200 font-mono">{aStart.slice(11, 23)}</span>{' '}
            {gapMs != null && (
              <>(gap <span className="text-emerald-200 font-mono">{gapMs} ms</span>)</>
            )}
          </div>
          <div className="text-xs text-neutral-400 mt-1">
            No window where the action happened but no signed record was written. This is
            EU AI Act Article 12 evidence; verify the chain offline with{' '}
            <code className="text-neutral-200">aegis-verify --bundle …</code>.
          </div>
        </div>
      </div>
    </div>
  )
}

export default function DecisionExplorer() {
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()
  const initialRid = searchParams.get('request_id') || ''
  const [requestId, setRequestId] = useState(initialRid)
  const [graph, setGraph] = useState(null)
  const [view, setView] = useState('Graph')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const fetchGraph = useCallback(async (rid) => {
    if (!rid) return
    setLoading(true)
    setError('')
    try {
      const resp = await flightService.getDecisionGraph(rid)
      // The backend wraps responses in { success, data }; api.js may unwrap.
      // Be defensive either way.
      const payload = resp?.data ?? resp
      setGraph(payload)
    } catch (e) {
      setGraph(null)
      setError(e?.message || 'Failed to load decision graph')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { if (initialRid) fetchGraph(initialRid) }, [initialRid, fetchGraph])

  const onSubmit = (ev) => {
    ev.preventDefault()
    if (!requestId) return
    setSearchParams({ request_id: requestId })
    fetchGraph(requestId)
  }

  const flowNodes = useMemo(() => graph ? layoutNodes(graph.nodes) : [], [graph])
  const flowEdges = useMemo(() => graph ? layoutEdges(graph.edges) : [], [graph])

  return (
    <div className="p-6 text-neutral-100">
      <header className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-4">
        <div>
          <h1 className="text-xl font-semibold">Decision Explorer</h1>
          <p className="text-sm text-neutral-400 mt-1">
            Render any <code>/execute</code> decision as a span graph — stages, signals, signed receipt.
          </p>
        </div>
        <form onSubmit={onSubmit} className="flex gap-2">
          <input
            type="text"
            placeholder="request_id"
            value={requestId}
            onChange={(e) => setRequestId(e.target.value)}
            className="px-3 py-2 bg-neutral-900 border border-neutral-700 rounded-md text-sm w-64"
          />
          <button
            type="submit"
            className="px-3 py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-md text-sm"
            disabled={loading}
          >
            {loading ? 'Loading…' : 'Open'}
          </button>
        </form>
      </header>

      {error && (
        <div className="mb-3 text-sm bg-rose-950 border border-rose-700 text-rose-100 px-3 py-2 rounded">
          {error}
        </div>
      )}

      {graph && (
        <>
          <EnforcementAuditPanel graph={graph} />
          <TraceOverview graph={graph} />

          <div className="mb-3 inline-flex border border-neutral-700 rounded-md overflow-hidden">
            {VIEWS.map((v) => (
              <button
                key={v}
                onClick={() => setView(v)}
                className={`px-3 py-1 text-sm ${view === v ? 'bg-neutral-700 text-white' : 'bg-neutral-900 text-neutral-300 hover:bg-neutral-800'}`}
              >
                {v}
              </button>
            ))}
            {graph.receipt_url && (
              <button
                onClick={() => navigate(graph.receipt_url)}
                className="px-3 py-1 text-sm bg-neutral-900 text-emerald-400 hover:bg-neutral-800"
                title="Open signed receipt"
              >
                Receipt ↗
              </button>
            )}
          </div>

          {view === 'Graph' && (
            <div className="h-[60vh] rounded-md border border-neutral-800 bg-neutral-950">
              <ReactFlow
                nodes={flowNodes}
                edges={flowEdges}
                nodeTypes={nodeTypes}
                fitView
                fitViewOptions={{ padding: 0.2 }}
                proOptions={{ hideAttribution: true }}
              >
                <Background color="#262626" gap={16} />
                <Controls position="bottom-right" />
              </ReactFlow>
            </div>
          )}

          {view === 'Timeline' && <TimelineView graph={graph} />}
          {view === 'JSON'     && <JsonView graph={graph} />}
        </>
      )}

      {!graph && !loading && !error && (
        <div className="text-sm text-neutral-400 space-y-3">
          <p>
            Enter a <code>request_id</code> to load the decision graph. Tip: the
            Flight Recorder, Live Feed, and Forensics pages now deep-link here.
          </p>
          <p className="text-xs text-neutral-500">
            Need a fresh decision to inspect?{' '}
            <a href="/live-demo" className="text-indigo-400 hover:text-indigo-300 underline">
              Run the live demo
            </a>{' '}
            — each step there carries its own <code>request_id</code> you can paste above.
          </p>
        </div>
      )}
    </div>
  )
}
