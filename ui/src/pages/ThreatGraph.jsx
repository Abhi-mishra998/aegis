import React, { useEffect, useMemo, useState, useContext } from 'react';
import ReactFlow, {
  Background,
  Controls,
  MarkerType,
  MiniMap,
  ReactFlowProvider,
} from 'reactflow';
import 'reactflow/dist/style.css';
import { AlertOctagon, Network, RefreshCw, Target } from 'lucide-react';

import { iagService } from '../services/api';
import { AgentContext } from '../context/AgentContext';
import Button from '../components/Common/Button';
import Card from '../components/Common/Card';
import SkeletonLoader from '../components/Common/SkeletonLoader';
import MitreCoverageGrid from '../components/security/MitreCoverageGrid';

/**
 * Sprint 7 — ThreatGraph
 *
 * Full-page IAG visualisation. When an agent is in scope, fetches
 * /iag/agents/{id} and renders Agent → Touched → Untouched as a
 * three-column ReactFlow graph. The IAG response payload's
 * `touched_resources` + `untouched_resources` + `by_kind` are the
 * source of truth — the graph is a faithful visual projection.
 *
 * On the right panel, MitreCoverageGrid renders the 36-signal /
 * 9-tactic registry from /iag/mitre-coverage.
 */

const NODE_KIND_COLORS = {
  agent:     { bg: '#ffffff', text: '#000000', border: '#ffffff' },
  touched:   { bg: '#7f1d1d', text: '#fecaca', border: '#dc2626' },
  untouched: { bg: '#92400e', text: '#fde68a', border: '#d97706' },
};

function buildFlowGraph(iag) {
  if (!iag) return { nodes: [], edges: [] };

  const nodes = [];
  const edges = [];

  // Agent root node
  const agentId = iag.agent_id || 'agent';
  nodes.push({
    id: agentId,
    position: { x: 0, y: 0 },
    data: {
      label: (
        <div className="text-xs font-semibold">
          Agent
          <div className="font-mono text-[9px] opacity-70">{agentId.slice(0, 16)}…</div>
        </div>
      ),
    },
    style: {
      background: NODE_KIND_COLORS.agent.bg,
      color: NODE_KIND_COLORS.agent.text,
      border: `2px solid ${NODE_KIND_COLORS.agent.border}`,
      borderRadius: 12,
      padding: 6,
      width: 160,
    },
  });

  const touched = Array.isArray(iag.touched_resources) ? iag.touched_resources : [];
  const untouched = Array.isArray(iag.untouched_resources) ? iag.untouched_resources : [];

  // Touched column on the right
  const colTouchedX = 320;
  touched.slice(0, 24).forEach((rid, i) => {
    const id = `touched-${i}`;
    nodes.push({
      id,
      position: { x: colTouchedX, y: i * 64 },
      data: {
        label: (
          <div className="text-[10px] font-mono leading-tight truncate" title={rid}>
            {rid.length > 28 ? rid.slice(0, 27) + '…' : rid}
          </div>
        ),
      },
      style: {
        background: NODE_KIND_COLORS.touched.bg,
        color: NODE_KIND_COLORS.touched.text,
        border: `1px solid ${NODE_KIND_COLORS.touched.border}`,
        borderRadius: 6,
        padding: 4,
        width: 220,
      },
    });
    edges.push({
      id: `e-${agentId}-${id}`,
      source: agentId,
      target: id,
      animated: true,
      style: { stroke: NODE_KIND_COLORS.touched.border, strokeWidth: 1.5 },
      markerEnd: { type: MarkerType.ArrowClosed, color: NODE_KIND_COLORS.touched.border },
    });
  });

  // Untouched column further right
  const colUntouchedX = 640;
  untouched.slice(0, 24).forEach((rid, i) => {
    const id = `untouched-${i}`;
    nodes.push({
      id,
      position: { x: colUntouchedX, y: i * 64 },
      data: {
        label: (
          <div className="text-[10px] font-mono leading-tight truncate" title={rid}>
            {rid.length > 28 ? rid.slice(0, 27) + '…' : rid}
          </div>
        ),
      },
      style: {
        background: NODE_KIND_COLORS.untouched.bg,
        color: NODE_KIND_COLORS.untouched.text,
        border: `1px dashed ${NODE_KIND_COLORS.untouched.border}`,
        borderRadius: 6,
        padding: 4,
        width: 220,
      },
    });
    edges.push({
      id: `e-${agentId}-${id}`,
      source: agentId,
      target: id,
      style: { stroke: NODE_KIND_COLORS.untouched.border, strokeWidth: 0.8, strokeDasharray: '4 3' },
      markerEnd: { type: MarkerType.ArrowClosed, color: NODE_KIND_COLORS.untouched.border },
    });
  });

  return { nodes, edges };
}

function GraphPanel({ iag, loading, error, onReload }) {
  const { nodes, edges } = useMemo(() => buildFlowGraph(iag), [iag]);

  // h-[60vh] with min/max so the force-directed canvas always sizes to the
  // container. Avoids the "graph collapses to 0px" trap on narrow viewports
  // and the "graph overflows below the fold" issue on 4K.
  const panelClass = 'h-[60vh] min-h-[420px] max-h-[680px] w-full';

  if (loading) {
    return (
      <div className={`${panelClass} flex items-center justify-center`}>
        <SkeletonLoader variant="card" />
      </div>
    );
  }
  if (error) {
    return (
      <div className={`${panelClass} flex flex-col items-center justify-center gap-2 text-[11px] text-amber-300/80`}>
        <AlertOctagon size={20} aria-hidden="true" />
        <span className="max-w-md text-center">{error}</span>
        <Button size="sm" variant="ghost" onClick={onReload}>
          <RefreshCw size={12} aria-hidden="true" /> Retry
        </Button>
      </div>
    );
  }
  if (!iag) {
    return (
      <div className={`${panelClass} rounded-xl border border-white/[0.06] bg-neutral-950 flex flex-col items-center justify-center gap-4 p-6 text-center`}>
        <div className="w-12 h-12 rounded-full bg-white/[0.04] flex items-center justify-center">
          <Target size={20} className="text-neutral-500" aria-hidden="true" />
        </div>
        <div className="space-y-1">
          <h3 className="text-sm font-semibold text-white">Graph empty</h3>
          <p className="text-xs text-neutral-400 max-w-sm">
            Generated from incident clusters. Trigger sample via{' '}
            <a href="/agents/playground" className="text-emerald-400 hover:underline">
              /agents/playground
            </a>{' '}
            or pick an agent from the topbar to load its IAG.
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap justify-center">
          <a
            href="/agents/playground"
            className="px-3 py-1.5 text-xs rounded-md bg-emerald-600 hover:bg-emerald-500 text-white"
          >
            Open Playground
          </a>
          <a
            href="/incidents"
            className="px-3 py-1.5 text-xs rounded-md border border-neutral-700 text-neutral-300 hover:bg-neutral-900"
          >
            Incidents
          </a>
        </div>
      </div>
    );
  }
  if (nodes.length <= 1) {
    return (
      <div className={`${panelClass} rounded-xl border border-white/[0.06] bg-neutral-950 flex flex-col items-center justify-center gap-3 p-6 text-center`}>
        <div className="w-12 h-12 rounded-full bg-white/[0.04] flex items-center justify-center">
          <Network size={20} className="text-neutral-500" aria-hidden="true" />
        </div>
        <div className="space-y-1">
          <h3 className="text-sm font-semibold text-white">No accessible resources yet</h3>
          <p className="text-xs text-neutral-400 max-w-sm">
            No traffic recorded for this agent. Trigger a sample tool call via{' '}
            <a href="/agents/playground" className="text-emerald-400 hover:underline">
              /agents/playground
            </a>{' '}
            and refresh.
          </p>
        </div>
        <Button size="sm" variant="ghost" onClick={onReload}>
          <RefreshCw size={12} aria-hidden="true" /> Refresh
        </Button>
      </div>
    );
  }
  return (
    <div className={`${panelClass} border border-white/[0.06] rounded-xl overflow-hidden`}>
      <ReactFlow nodes={nodes} edges={edges} fitView panOnScroll>
        <Background gap={20} size={0.7} color="rgba(255,255,255,0.04)" />
        <MiniMap pannable zoomable className="!bg-black/40" maskColor="rgba(0,0,0,0.6)" />
        <Controls className="!bg-black/40 !border-white/10" />
      </ReactFlow>
    </div>
  );
}

export default function ThreatGraph() {
  const { selectedAgentId } = useContext(AgentContext);
  const [iag, setIag] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [refreshTick, setRefreshTick] = useState(0);

  useEffect(() => {
    if (!selectedAgentId) {
      setIag(null);
      setLoading(false);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    iagService
      .getAgent(selectedAgentId)
      .then((resp) => {
        if (cancelled) return;
        setIag(resp?.data || resp || null);
        setLoading(false);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err?.message || 'Failed to load IAG for this agent');
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedAgentId, refreshTick]);

  return (
    <div className="space-y-5">
      <div className="flex items-start justify-between flex-wrap gap-4">
        <div className="space-y-1">
          <h1 className="text-2xl font-bold tracking-tight text-white flex items-center gap-2">
            <Network size={20} aria-hidden="true" /> Threat Graph
          </h1>
          <p className="text-xs text-neutral-400 max-w-2xl">
            Identity &amp; Access graph plus MITRE ATT&amp;CK coverage on one screen.
            Touched (solid) vs reachable-but-untouched (dashed) resources surface the
            blast radius your agent could have hit but didn't.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="ghost"
            onClick={async () => {
              try {
                await iagService.refreshGraph(30);
              } catch (e) {
                // Best-effort — the user always gets the regular Refresh too.
              }
              setRefreshTick((t) => t + 1);
            }}
            title="Re-ingest the tenant's tool surface from the audit log"
          >
            <RefreshCw size={12} aria-hidden="true" />
            Re-ingest
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setRefreshTick((t) => t + 1)}
            disabled={loading || !selectedAgentId}
          >
            <RefreshCw size={12} className={loading ? 'animate-spin' : ''} aria-hidden="true" />
            Refresh
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-5 gap-4">
        <div className="xl:col-span-3">
          <Card title="IAG graph" icon={Network}>
            <ReactFlowProvider>
              <GraphPanel
                iag={iag}
                loading={loading}
                error={error}
                onReload={() => setRefreshTick((t) => t + 1)}
              />
            </ReactFlowProvider>
            {iag && (
              <div className="mt-3 text-[10px] text-neutral-500 flex flex-wrap gap-3">
                <span>Touched: <span className="text-red-300 font-bold">{iag.touched_resources?.length || 0}</span></span>
                <span>Reachable: <span className="text-amber-300 font-bold">{iag.untouched_resources?.length || 0}</span></span>
                <span>Criticality: <span className="text-white font-bold">{iag.criticality_score ?? 0}</span></span>
                {iag.last_ingest_ts > 0 && (
                  <span className="font-mono">
                    Ingest: {new Date(iag.last_ingest_ts * 1000).toISOString().slice(0, 16).replace('T', ' ')}
                  </span>
                )}
              </div>
            )}
          </Card>
        </div>
        <div className="xl:col-span-2">
          <MitreCoverageGrid compact agentId={selectedAgentId} days={7} />
        </div>
      </div>
    </div>
  );
}
