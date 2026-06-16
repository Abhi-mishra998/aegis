import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  AlertOctagon,
  ArrowUpRight,
  FileSearch,
  Layers,
} from 'lucide-react';
import { forensicsService } from '../../services/api';
import Card from '../Common/Card';
import Button from '../Common/Button';

/**
 * Sprint 5 — ForensicsDrawer
 *
 * Shows a tight summary of the forensics blast-radius (depth-3 by
 * default) so the operator sees the kill-chain edges without leaving
 * the incident detail. The "Open in Forensics" link deep-links into
 * the full /forensics page scoped to this agent.
 */
export default function ForensicsDrawer({ incident }) {
  const agentId = incident?.agent_id;
  const incidentNumber = incident?.incident_number;
  const navigate = useNavigate();
  const [blast, setBlast] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!agentId) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    forensicsService
      .getBlastRadius(agentId, 3)
      .then((resp) => {
        if (cancelled) return;
        setBlast(resp?.data || resp || null);
        setLoading(false);
      })
      .catch((err) => {
        if (cancelled) return;
        const msg = err?.message || '';
        if (/404/.test(msg)) {
          setError('No forensic graph for this agent yet — run some traffic first.');
        } else {
          setError(msg || 'Failed to load forensics summary');
        }
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [agentId]);

  if (!agentId) {
    return (
      <Card title="Forensics" icon={FileSearch}>
        <p className="text-[11px] text-neutral-500 italic">
          This incident is not bound to an agent — forensics view is unavailable.
        </p>
      </Card>
    );
  }

  return (
    <Card title="Forensics" icon={FileSearch}>
      <div className="flex justify-end -mt-3 mb-2">
        <Button
          size="sm"
          variant="ghost"
          onClick={() => navigate(`/forensics?agent=${encodeURIComponent(agentId)}`)}
        >
          Open in Forensics
          <ArrowUpRight size={11} aria-hidden="true" />
        </Button>
      </div>
      {loading ? (
        <p className="text-[11px] text-neutral-500">Loading…</p>
      ) : error ? (
        <div className="flex items-start gap-2 text-[11px] text-amber-300/80">
          <AlertOctagon size={12} className="mt-0.5 shrink-0" aria-hidden="true" />
          <span>{error}</span>
        </div>
      ) : blast ? (
        <div className="grid grid-cols-3 gap-3 text-center">
          <div className="space-y-0.5">
            <div className="text-2xl font-bold text-white">{blast.nodes_total ?? blast.node_count ?? blast.total_nodes ?? '—'}</div>
            <div className="text-[10px] uppercase tracking-widest text-neutral-500">Nodes</div>
          </div>
          <div className="space-y-0.5">
            <div className="text-2xl font-bold text-white">{blast.edges_total ?? blast.edge_count ?? blast.total_edges ?? '—'}</div>
            <div className="text-[10px] uppercase tracking-widest text-neutral-500">Edges</div>
          </div>
          <div className="space-y-0.5">
            <div className="text-2xl font-bold text-amber-400 flex items-center justify-center gap-1">
              <Layers size={16} aria-hidden="true" />
              {blast.depth ?? 3}
            </div>
            <div className="text-[10px] uppercase tracking-widest text-neutral-500">Depth</div>
          </div>
        </div>
      ) : (
        <p className="text-[11px] text-neutral-500 italic">No forensics data.</p>
      )}
      <div className="mt-3 text-[10px] text-neutral-600">
        Agent: <span className="font-mono">{agentId.slice(0, 12)}…</span>
        {incidentNumber && (
          <span className="ml-2">
            Incident: <span className="font-mono">{incidentNumber}</span>
          </span>
        )}
      </div>
    </Card>
  );
}
