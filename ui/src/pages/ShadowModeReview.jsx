import React, { useEffect, useMemo, useState } from 'react';
import {
  AlertOctagon,
  AlertTriangle,
  CheckCircle2,
  Clock,
  Eye,
  RefreshCw,
  Shield,
  ShieldOff,
} from 'lucide-react';
import { auditService, workspaceService } from '../services/api';
import { useSSE } from '../hooks/useSSE';
import { useRole } from '../hooks/useRole';
import Button from '../components/Common/Button';
import Card from '../components/Common/Card';
import ConfirmDialog from '../components/Common/ConfirmDialog';

function StatusPill({ active, daysLeft }) {
  if (!active) {
    return (
      <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-red-500/10 text-red-400 text-[11px] font-semibold uppercase tracking-wider border border-red-500/30">
        <ShieldOff size={12} aria-hidden="true" />
        Enforce mode
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-amber-500/10 text-amber-400 text-[11px] font-semibold uppercase tracking-wider border border-amber-500/30">
      <Shield size={12} aria-hidden="true" />
      Shadow · {daysLeft ?? '?'}d left
    </span>
  );
}

function originalActionBadge(original) {
  const map = {
    deny: { color: 'text-red-400', bg: 'bg-red-500/[0.07]', label: 'Would deny' },
    escalate: { color: 'text-amber-400', bg: 'bg-amber-500/[0.07]', label: 'Would escalate' },
  };
  const c = map[original?.toLowerCase()] || { color: 'text-neutral-300', bg: 'bg-white/[0.04]', label: original || 'unknown' };
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-[10px] font-mono uppercase tracking-wider ${c.color} ${c.bg}`}>
      {c.label}
    </span>
  );
}

function snippet(value, max = 80) {
  if (value == null) return '';
  const str = typeof value === 'string' ? value : JSON.stringify(value);
  if (str.length <= max) return str;
  return str.slice(0, max - 1) + '…';
}

function formatRelative(iso) {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    const delta = (Date.now() - d.getTime()) / 1000;
    if (delta < 60) return `${Math.floor(delta)}s ago`;
    if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
    if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
    return d.toISOString().slice(0, 16).replace('T', ' ');
  } catch {
    return iso;
  }
}

export default function ShadowModeReview() {
  const { isOwner, canExitShadowMode } = useRole();

  const [workspace, setWorkspace] = useState(null);
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [exiting, setExiting] = useState(false);
  const [showExitDialog, setShowExitDialog] = useState(false);
  const [refreshTick, setRefreshTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([
      workspaceService.me().catch(() => null),
      auditService.getShadowEvents(100, 0).catch(() => null),
    ]).then(([wsResp, evResp]) => {
      if (cancelled) return;
      setWorkspace(wsResp?.data || wsResp || null);
      const items = evResp?.data?.items || evResp?.data || evResp?.items || [];
      setEvents(Array.isArray(items) ? items : []);
      setLoading(false);
      setError('');
    }).catch((err) => {
      if (cancelled) return;
      setError(err?.message || 'Failed to load shadow events');
      setLoading(false);
    });
    return () => { cancelled = true; };
  }, [refreshTick]);

  // Live append on SSE would_have_blocked event so the operator sees new
  // events without manual refresh.
  useSSE({
    enabled: true,
    onMessage: (evt) => {
      if (evt?.type !== 'would_have_blocked') return;
      const data = evt?.data || {};
      setEvents((prev) => [
        {
          id: data.request_id || `sse-${Date.now()}`,
          timestamp: new Date(((evt?.ts || Date.now()/1000) * 1000)).toISOString(),
          action: 'would_have_blocked',
          agent_id: data.agent_id,
          metadata: data,
          // matches the auditService row shape close enough for the table
          reason: data.reasons?.[0] || data.original_action,
          tool_name: data.tool,
          via_sse: true,
        },
        ...prev,
      ].slice(0, 200));
    },
  });

  const handleExit = async () => {
    setExiting(true);
    try {
      await workspaceService.exitShadowMode();
      setShowExitDialog(false);
      setRefreshTick((t) => t + 1);
    } catch (err) {
      setError(err?.message || 'Exit shadow mode failed');
    } finally {
      setExiting(false);
    }
  };

  const summary = useMemo(() => {
    const denyCount = events.filter((e) => (e.metadata?.original_action || '').toLowerCase() === 'deny').length;
    const escalateCount = events.filter((e) => (e.metadata?.original_action || '').toLowerCase() === 'escalate').length;
    const uniqueAgents = new Set(events.map((e) => e.agent_id).filter(Boolean)).size;
    return { denyCount, escalateCount, uniqueAgents, total: events.length };
  }, [events]);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div className="space-y-1">
          <h1 className="text-2xl font-bold tracking-tight text-white flex items-center gap-3">
            Shadow Mode Review
            <StatusPill
              active={!!workspace?.shadow_mode_active}
              daysLeft={workspace?.shadow_mode_days_left}
            />
          </h1>
          <p className="text-xs text-neutral-400 max-w-xl">
            While shadow mode is active, every deny/escalate decision is logged as a{' '}
            <span className="font-semibold text-amber-300">would_have_blocked</span> event —
            no agent traffic is actually blocked. Review the events below before flipping
            to enforce.
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            variant="ghost"
            size="sm"
            aria-label="Refresh"
            onClick={() => setRefreshTick((t) => t + 1)}
          >
            <RefreshCw size={14} aria-hidden="true" />
          </Button>
          {workspace?.shadow_mode_active && (
            <Button
              size="sm"
              variant="danger"
              disabled={!canExitShadowMode || exiting}
              loading={exiting}
              onClick={() => setShowExitDialog(true)}
            >
              <ShieldOff size={14} aria-hidden="true" />
              Exit shadow mode
            </Button>
          )}
        </div>
      </div>

      {/* Status / metric tiles */}
      <div className="grid grid-cols-1 sm:grid-cols-4 gap-3">
        <Card>
          <div className="space-y-1">
            <div className="text-[10px] uppercase tracking-widest text-neutral-500">Would-have-blocked</div>
            <div className="text-2xl font-bold text-white">{summary.total}</div>
            <div className="text-[11px] text-neutral-500">Total in window</div>
          </div>
        </Card>
        <Card>
          <div className="space-y-1">
            <div className="text-[10px] uppercase tracking-widest text-neutral-500">Would deny</div>
            <div className="text-2xl font-bold text-red-400">{summary.denyCount}</div>
            <div className="text-[11px] text-neutral-500">Hard policy blocks</div>
          </div>
        </Card>
        <Card>
          <div className="space-y-1">
            <div className="text-[10px] uppercase tracking-widest text-neutral-500">Would escalate</div>
            <div className="text-2xl font-bold text-amber-400">{summary.escalateCount}</div>
            <div className="text-[11px] text-neutral-500">Approval requests</div>
          </div>
        </Card>
        <Card>
          <div className="space-y-1">
            <div className="text-[10px] uppercase tracking-widest text-neutral-500">Agents flagged</div>
            <div className="text-2xl font-bold text-white">{summary.uniqueAgents}</div>
            <div className="text-[11px] text-neutral-500">Distinct agents</div>
          </div>
        </Card>
      </div>

      {error && (
        <div className="error-banner" role="alert">
          <div className="flex items-center gap-3">
            <AlertOctagon size={15} className="text-red-400 shrink-0" aria-hidden="true" />
            <p className="text-xs text-red-400">{error}</p>
          </div>
        </div>
      )}

      {/* Events table */}
      <Card>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-[10px] uppercase tracking-widest text-neutral-500 border-b border-white/[0.06]">
              <tr>
                <th className="px-3 py-2">When</th>
                <th className="px-3 py-2">Original</th>
                <th className="px-3 py-2">Agent</th>
                <th className="px-3 py-2">Tool</th>
                <th className="px-3 py-2">Reason</th>
                <th className="px-3 py-2">Risk</th>
                <th className="px-3 py-2">Policy</th>
              </tr>
            </thead>
            <tbody>
              {loading && (
                <tr>
                  <td className="px-3 py-6 text-center text-neutral-500" colSpan={7}>
                    Loading…
                  </td>
                </tr>
              )}
              {!loading && events.length === 0 && (
                <tr>
                  <td className="px-3 py-10 text-center" colSpan={7}>
                    <div className="flex flex-col items-center gap-3 text-neutral-500">
                      <CheckCircle2 size={28} className="text-green-400/50" aria-hidden="true" />
                      <div className="text-sm">No would-have-blocked events yet.</div>
                      <div className="text-xs max-w-md">
                        Run some agent traffic. If any deny/escalate fires while you're in
                        shadow mode, it'll show up here for review.
                      </div>
                    </div>
                  </td>
                </tr>
              )}
              {!loading && events.map((e, idx) => {
                const meta = e.metadata || {};
                return (
                  <tr key={e.id || idx} className="border-b border-white/[0.04] hover:bg-white/[0.02] transition-colors">
                    <td className="px-3 py-2 text-xs text-neutral-300 align-top whitespace-nowrap">
                      <div className="inline-flex items-center gap-1.5">
                        <Clock size={11} className="text-neutral-600" aria-hidden="true" />
                        {formatRelative(e.timestamp || e.created_at)}
                      </div>
                      {e.via_sse && (
                        <span className="ml-2 inline-block text-[9px] uppercase tracking-widest text-green-400">live</span>
                      )}
                    </td>
                    <td className="px-3 py-2 align-top">{originalActionBadge(meta.original_action)}</td>
                    <td className="px-3 py-2 align-top font-mono text-[11px] text-neutral-300">
                      {snippet(e.agent_id, 14) || '—'}
                    </td>
                    <td className="px-3 py-2 align-top text-xs text-neutral-200">{e.tool_name || meta.tool || '—'}</td>
                    <td className="px-3 py-2 align-top text-xs text-neutral-400">
                      {snippet(e.reason || (meta.reasons && meta.reasons[0]) || '—', 60)}
                    </td>
                    <td className="px-3 py-2 align-top text-xs text-neutral-300">
                      {(meta.risk_score ?? '—').toString().slice(0, 6)}
                    </td>
                    <td className="px-3 py-2 align-top text-[11px] font-mono text-neutral-500">
                      {snippet(meta.policy_id, 18)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>

      <ConfirmDialog
        isOpen={showExitDialog}
        onClose={() => setShowExitDialog(false)}
        onConfirm={handleExit}
        title="Exit shadow mode?"
        description={
          'From the next request onwards, deny/escalate decisions from the ' +
          'policy engine will actually block your agents’ tool calls. ' +
          'Make sure you have reviewed the would-have-blocked feed above — ' +
          'this action is only reversible by an Aegis operator.'
        }
        confirmLabel="Exit shadow mode"
        variant="danger"
      />

      {!isOwner && (
        <div className="text-[11px] text-neutral-500 flex items-center gap-2">
          <Eye size={12} aria-hidden="true" />
          Only the workspace OWNER can exit shadow mode.
        </div>
      )}
    </div>
  );
}
