import React, { useState, useEffect, useCallback, useContext, useMemo, useRef } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  AlertTriangle, Shield, Clock, CheckCircle2, XCircle,
  RefreshCw, Filter, ChevronRight, Zap, User,
  Activity, TrendingDown, Eye, Lock, Slash, ArrowUpRight,
  Download, Crosshair, PlayCircle,
} from 'lucide-react';
import Card from '../components/Common/Card';
import Button from '../components/Common/Button';
import SkeletonLoader from '../components/Common/SkeletonLoader';
import Modal from '../components/Common/Modal';
import DataTable from '../components/Common/DataTable';
import ErrorBoundary from '../components/Common/ErrorBoundary';
import { incidentService, socService } from '../services/api';
import { AgentContext } from '../context/AgentContext';
import { useSSE } from '../hooks/useSSE';
// Sprint 5 — orphan-endpoint surfacing inside the incident detail modal.
import BlastRadiusCard from '../components/incidents/BlastRadiusCard';
import RemediationPanel from '../components/incidents/RemediationPanel';
import ForensicsDrawer from '../components/incidents/ForensicsDrawer';

async function _exportIncidentPdf(incidentId, incidentNumber) {
  const blob = await incidentService.exportPdf(incidentId)
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `aegis-incident-${incidentNumber || incidentId.slice(0, 8)}.pdf`
  a.click()
  URL.revokeObjectURL(url)
}
import { AuthContext } from '../context/AuthContext';
import { eventBus } from '../lib/eventBus';

// ── Severity helpers ──────────────────────────────────────────────────────────

const SEV_CONFIG = {
  CRITICAL: { cls: 'text-red-400 bg-red-500/10 border-red-500/30',   dot: 'bg-red-500',    label: 'Critical' },
  HIGH:     { cls: 'text-orange-400 bg-orange-500/10 border-orange-500/30', dot: 'bg-orange-500', label: 'High' },
  MEDIUM:   { cls: 'text-amber-400 bg-amber-500/10 border-amber-500/30',  dot: 'bg-amber-500',  label: 'Medium' },
  LOW:      { cls: 'text-green-400 bg-green-500/10 border-green-500/30',   dot: 'bg-green-500',  label: 'Low' },
};

const STATUS_CONFIG = {
  OPEN:          { cls: 'text-red-400 bg-red-500/10 border-red-500/20',       label: 'Open' },
  INVESTIGATING: { cls: 'text-amber-400 bg-amber-500/10 border-amber-500/20', label: 'Investigating' },
  ESCALATED:     { cls: 'text-purple-400 bg-purple-500/10 border-purple-500/20', label: 'Escalated' },
  MITIGATED:     { cls: 'text-blue-400 bg-blue-500/10 border-blue-500/20',    label: 'Mitigated' },
  RESOLVED:      { cls: 'text-green-400 bg-green-500/10 border-green-500/20', label: 'Resolved' },
};

// Must mirror backend _ALLOWED_TRANSITIONS exactly — used as fallback until
// the live /incidents/transitions response replaces it at runtime.
const VALID_TRANSITIONS_FALLBACK = {
  OPEN:          ['INVESTIGATING'],
  INVESTIGATING: ['MITIGATED', 'ESCALATED', 'RESOLVED'],
  ESCALATED:     ['MITIGATED', 'RESOLVED'],
  MITIGATED:     ['RESOLVED'],
  RESOLVED:      [],
};

const ACTION_TYPES = [
  { value: 'KILL_AGENT',  label: 'Kill Agent',    icon: Slash,    cls: 'text-red-400' },
  { value: 'BLOCK_AGENT', label: 'Block Agent',   icon: Lock,     cls: 'text-orange-400' },
  { value: 'ISOLATE',     label: 'Isolate Agent', icon: Shield,   cls: 'text-amber-400' },
  { value: 'ESCALATE',    label: 'Escalate',      icon: ArrowUpRight, cls: 'text-blue-400' },
  { value: 'REASSIGN',    label: 'Reassign',      icon: User,     cls: 'text-purple-400' },
  { value: 'NOTE',        label: 'Add Note',      icon: Eye,      cls: 'text-neutral-400' },
];

function SeverityBadge({ severity }) {
  const cfg = SEV_CONFIG[severity] || SEV_CONFIG.LOW;
  return (
    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium border ${cfg.cls}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${cfg.dot}`} />
      {cfg.label}
    </span>
  );
}

function StatusBadge({ status }) {
  const cfg = STATUS_CONFIG[status] || STATUS_CONFIG.OPEN;
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border ${cfg.cls}`}>
      {cfg.label}
    </span>
  );
}

function ScoreGauge({ score }) {
  const n = Number(score) || 0;
  const color = n >= 80 ? 'text-green-400' : n >= 60 ? 'text-amber-400' : 'text-red-400';
  const ring  = n >= 80 ? 'stroke-green-500' : n >= 60 ? 'stroke-amber-500' : 'stroke-red-500';
  const r = 28, circ = 2 * Math.PI * r;
  const dash = (n / 100) * circ;
  return (
    <div className="relative inline-flex items-center justify-center w-20 h-20">
      <svg className="-rotate-90" width="72" height="72" viewBox="0 0 72 72">
        <circle cx="36" cy="36" r={r} fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth="6" />
        <circle cx="36" cy="36" r={r} fill="none" className={ring} strokeWidth="6"
          strokeDasharray={`${dash} ${circ}`} strokeLinecap="round" />
      </svg>
      <div className="absolute flex flex-col items-center">
        <span className={`text-lg font-bold ${color}`}>{n}</span>
        <span className="text-[9px] text-neutral-500 font-mono -mt-0.5">SCORE</span>
      </div>
    </div>
  );
}

// ── Detail Modal ──────────────────────────────────────────────────────────────

function IncidentDetail({ incident, onClose, onRefresh, validTransitions }) {
  const { addToast } = useContext(AuthContext);
  const navigate = useNavigate();
  const [loading,    setLoading]    = useState(false);
  const [actionType, setActionType] = useState('NOTE');
  const [note,       setNote]       = useState('');
  const [by,         setBy]         = useState('');
  const [exporting,  setExporting]  = useState(false);

  const transitions = validTransitions[incident.status] || [];

  const handleStatus = async (newStatus) => {
    try {
      setLoading(true);
      await incidentService.update(incident.id, { status: newStatus, note: `Status changed to ${newStatus}` });
      addToast(`Incident moved to ${newStatus}`, 'success');
      onRefresh();
      onClose();
    } catch (err) {
      const detail = err?.response?.data?.detail || err?.message || 'Transition not allowed';
      addToast(`Failed: ${detail}`, 'error');
    } finally {
      setLoading(false);
    }
  };

  const handleAction = async () => {
    if (!by.trim()) { addToast('Enter responder name', 'error'); return; }
    try {
      setLoading(true);
      await incidentService.addAction(incident.id, { type: actionType, by: by.trim(), note: note.trim() });
      addToast('Action recorded', 'success');
      setNote(''); setBy('');
      onRefresh();
    } catch {
      addToast('Failed to record action', 'error');
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal isOpen onClose={onClose} title={`${incident.incident_number} — Detail`} size="xl">
      <div className="space-y-5">
        {/* Header */}
        <div className="flex items-start justify-between gap-4">
          <div>
            <h3 className="text-sm font-semibold text-white">{incident.title}</h3>
            <p className="text-xs text-neutral-500 mt-0.5 font-mono">
              Agent {incident.agent_id?.slice(0, 8)} · {incident.tool || 'N/A'} · Risk {((incident.risk_score ?? 0) * 100).toFixed(0)}%
            </p>
            {incident.agent_id && (
              <button
                onClick={() => { onClose(); navigate(`/agents/${incident.agent_id}/profile`) }}
                className="mt-1.5 flex items-center gap-1 text-[11px] text-indigo-400 hover:text-white transition-colors"
              >
                <ArrowUpRight size={11} aria-hidden="true" /> View Agent Profile
              </button>
            )}
            {incident.explanation && (
              <p className="text-xs text-neutral-400 mt-2 leading-relaxed bg-white/[0.03] rounded px-2 py-1.5 border border-white/[0.06]">
                {incident.explanation}
              </p>
            )}
            {/* SLA timestamps */}
            <div className="flex gap-4 mt-2 text-[10px] text-neutral-600 font-mono">
              {incident.acknowledged_at && <span>ACK {new Date(incident.acknowledged_at).toLocaleTimeString()}</span>}
              {incident.mitigated_at    && <span>MIT {new Date(incident.mitigated_at).toLocaleTimeString()}</span>}
              {incident.resolved_at     && <span>RES {new Date(incident.resolved_at).toLocaleTimeString()}</span>}
              {incident.violation_count > 1 && (
                <span className="text-amber-600">{incident.violation_count}x repeated</span>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0 flex-wrap">
            <SeverityBadge severity={incident.severity} />
            <StatusBadge status={incident.status} />
            <button
              onClick={async () => {
                setExporting(true)
                try {
                  await _exportIncidentPdf(incident.id, incident.incident_number)
                  addToast('Forensic PDF downloaded', 'success')
                } catch (err) {
                  addToast(err.message || 'Export failed', 'error')
                } finally {
                  setExporting(false)
                }
              }}
              disabled={exporting}
              className="flex items-center gap-1.5 px-2.5 py-1 text-[11px] text-neutral-400 bg-white/[0.02] border border-[var(--border-subtle)] rounded-lg hover:text-white hover:border-white/[0.12] disabled:opacity-40 transition-colors"
            >
              <Download size={11} aria-hidden="true" />
              {exporting ? 'Generating…' : 'Export PDF'}
            </button>
          </div>
        </div>

        {/* Status transitions */}
        {transitions.length > 0 && (
          <div>
            <p className="text-xs text-neutral-500 mb-2 font-medium">Move Status</p>
            <div className="flex gap-2 flex-wrap">
              {transitions.map((s) => (
                <Button key={s} size="sm" variant="secondary" onClick={() => handleStatus(s)} disabled={loading}>
                  {STATUS_CONFIG[s]?.label || s}
                </Button>
              ))}
            </div>
          </div>
        )}

        {/* Sprint 5 — Blast Radius + Remediation + Forensics panels.
            Each loads independently and tolerates 404 / 409 gracefully so a
            partially-instrumented incident never blanks the whole modal. */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          <BlastRadiusCard incidentId={incident.id} />
          <RemediationPanel incidentId={incident.id} />
        </div>
        <ForensicsDrawer incident={incident} />

        {/* Timeline */}
        {incident.timeline?.length > 0 && (
          <div>
            <p className="text-xs text-neutral-500 mb-2 font-medium">Timeline</p>
            <div className="space-y-2 max-h-40 overflow-y-auto pr-1">
              {[...incident.timeline].reverse().map((e, i) => (
                <div key={i} className="flex gap-2.5 text-xs">
                  <span className="text-neutral-600 font-mono shrink-0 mt-0.5">
                    {new Date(e.ts || e.timestamp).toLocaleTimeString()}
                  </span>
                  <div>
                    <span className="text-neutral-300">{e.event || e.action}</span>
                    {e.note && <span className="text-neutral-500 ml-1.5">— {e.note}</span>}
                    {e.by  && <span className="text-neutral-600 ml-1.5">by {e.by}</span>}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Response action */}
        <div className="border border-white/[0.06] rounded-lg p-4 space-y-3">
          <p className="text-xs font-medium text-neutral-400">Add Response Action</p>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs text-neutral-500 block mb-1">Action</label>
              <select name="select"
                value={actionType}
                onChange={(e) => setActionType(e.target.value)}
                className="input-standard input-compact w-full text-xs"
              >
                {ACTION_TYPES.map((a) => (
                  <option key={a.value} value={a.value}>{a.label}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="text-xs text-neutral-500 block mb-1">Responder</label>
              <input name="your_name"
                type="text"
                placeholder="Your name"
                value={by}
                onChange={(e) => setBy(e.target.value)}
                className="input-standard input-compact w-full text-xs"
              />
            </div>
          </div>
          <div>
            <label className="text-xs text-neutral-500 block mb-1">Note (optional)</label>
            <textarea name="what_was_done"
              rows={2}
              placeholder="What was done..."
              value={note}
              onChange={(e) => setNote(e.target.value)}
              className="input-standard w-full text-xs resize-none"
            />
          </div>
          <Button size="sm" onClick={handleAction} disabled={loading}>
            {loading ? 'Saving…' : 'Record Action'}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

// ── SOC Feed ──────────────────────────────────────────────────────────────────

const SOC_SEV = {
  CRITICAL: { cls: 'text-red-400',    bar: 'bg-red-500',    dot: 'bg-red-500' },
  HIGH:     { cls: 'text-orange-400', bar: 'bg-orange-500', dot: 'bg-orange-500' },
  MEDIUM:   { cls: 'text-amber-400',  bar: 'bg-amber-500',  dot: 'bg-amber-500' },
  LOW:      { cls: 'text-neutral-400',bar: 'bg-neutral-600',dot: 'bg-neutral-600' },
};

const SOC_TYPE_LABEL = {
  agent_kill:  'Agent Kill',
  escalation:  'Escalation',
  policy_deny: 'Policy Deny',
  high_risk:   'High Risk',
};

function SocFeed() {
  const { addToast } = useContext(AuthContext);
  const [events,  setEvents]  = useState([]);
  const [loading, setLoading] = useState(true);
  const [limit,   setLimit]   = useState(60);

  const fetchFeed = useCallback(async () => {
    setLoading(true);
    try {
      const res = await socService.getTimeline(limit);
      setEvents(res?.data || []);
    } catch {
      addToast('Failed to load SOC feed', 'error');
    } finally {
      setLoading(false);
    }
  }, [limit, addToast]);

  useEffect(() => { fetchFeed(); }, [fetchFeed]);

  useEffect(() => {
    const u1 = eventBus.on('policy_decision', fetchFeed);
    const u2 = eventBus.on('incident_updated', fetchFeed);
    return () => { u1(); u2(); };
  }, [fetchFeed]);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Activity size={14} className="text-purple-400" />
          <span className="text-xs text-neutral-400">Live security event feed — deny, kill, escalation, high-risk</span>
        </div>
        <div className="flex items-center gap-2">
          <select name="select"
            value={limit}
            onChange={(e) => setLimit(Number(e.target.value))}
            className="input-standard input-compact text-xs w-24"
          >
            {[30, 60, 100, 200].map(n => <option key={n} value={n}>Last {n}</option>)}
          </select>
          <Button variant="secondary" size="sm" onClick={fetchFeed} disabled={loading}>
            <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
          </Button>
        </div>
      </div>

      <Card>
        {loading ? (
          <div className="p-4"><SkeletonLoader count={8} /></div>
        ) : events.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16">
            <Shield size={32} className="text-neutral-700 mb-3" />
            <p className="text-sm text-neutral-500">No security events in the selected window</p>
          </div>
        ) : (
          <div className="divide-y divide-white/[0.04]">
            {events.map((ev, i) => {
              const sev = SOC_SEV[ev.severity] || SOC_SEV.LOW;
              return (
                <div key={ev.id || i} className="flex items-start gap-3 px-4 py-3 hover:bg-white/[0.02] transition-colors">
                  {/* Severity bar */}
                  <div className={`w-0.5 self-stretch rounded-full shrink-0 ${sev.bar}`} />

                  {/* Dot + type */}
                  <div className="shrink-0 pt-0.5">
                    <span className={`inline-flex items-center gap-1 text-[10px] font-medium ${sev.cls}`}>
                      <span className={`w-1.5 h-1.5 rounded-full ${sev.dot}`} />
                      {ev.severity}
                    </span>
                  </div>

                  {/* Content */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-0.5">
                      <span className="text-[10px] text-neutral-500 font-mono">
                        {SOC_TYPE_LABEL[ev.type] || ev.type}
                      </span>
                      <span className="text-[10px] text-neutral-700 font-mono">
                        agent {ev.agent_id?.slice(0, 8)}
                      </span>
                      {ev.tool && (
                        <span className="text-[10px] px-1.5 py-0 rounded border border-white/[0.06] text-neutral-600 font-mono">
                          {ev.tool}
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-neutral-300 leading-snug truncate">{ev.message}</p>
                  </div>

                  {/* Risk + time */}
                  <div className="shrink-0 text-right">
                    <p className={`text-[11px] font-mono font-bold ${(ev.risk_score ?? 0) >= 0.9 ? 'text-red-400' : (ev.risk_score ?? 0) >= 0.7 ? 'text-orange-400' : 'text-neutral-500'}`}>
                      {((ev.risk_score ?? 0) * 100).toFixed(0)}%
                    </p>
                    <p className="text-[10px] text-neutral-700 font-mono mt-0.5">
                      {ev.timestamp ? new Date(ev.timestamp).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '—'}
                    </p>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </Card>
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────────

const SEVERITY_OPTIONS = ['', 'CRITICAL', 'HIGH', 'MEDIUM', 'LOW'];
const STATUS_OPTIONS   = ['', 'OPEN', 'INVESTIGATING', 'ESCALATED', 'MITIGATED', 'RESOLVED'];

function _relTime(iso) {
  if (!iso) return '—';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '—';
  const diffSec = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (diffSec < 60)    return `${diffSec}s ago`;
  if (diffSec < 3600)  return `${Math.round(diffSec / 60)}m ago`;
  if (diffSec < 86400) return `${Math.round(diffSec / 3600)}h ago`;
  return `${Math.round(diffSec / 86400)}d ago`;
}

function IncidentsPage() {
  const { addToast } = useContext(AuthContext);
  const { selectedAgentId, selectedAgent } = useContext(AgentContext);

  const [activeTab, setActiveTab] = useState('incidents');

  const [validTransitions, setValidTransitions] = useState(VALID_TRANSITIONS_FALLBACK);
  const [summary,    setSummary]    = useState(null);
  const [items,      setItems]      = useState([]);
  const [total,      setTotal]      = useState(0);
  const [loading,    setLoading]    = useState(true);
  // Track whether the first load has resolved so we never render
  // "0 incidents" before the API has actually responded.
  const [hasLoaded,  setHasLoaded]  = useState(false);
  const [selected,   setSelected]   = useState(null);

  const [filterStatus,   setFilterStatus]   = useState('');
  const [filterSeverity, setFilterSeverity] = useState('');
  const [page,           setPage]           = useState(0);
  const PAGE_SIZE = 25;

  // Bulk-resolve selection — set of incident ids checked in the table.
  const [selectedIds,   setSelectedIds]   = useState(() => new Set());
  const [bulkResolving, setBulkResolving] = useState(false);

  const clearSelection = useCallback(() => setSelectedIds(new Set()), []);

  const toggleId = useCallback((id) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }, []);

  // Background refresh: refetch silently after the first load so the
  // table doesn't unmount its rows into skeleton tiles every time SSE
  // says "something changed." Only the FIRST fetch on mount sets
  // loading=true (which gates the skeleton). Subsequent refetches
  // swap data in place — the operator only sees row counts update,
  // never a full re-blank.
  const hasLoadedRef = useRef(false);
  const fetchAll = useCallback(async () => {
    if (!hasLoadedRef.current) setLoading(true);
    try {
      const [sumRes, listRes] = await Promise.all([
        incidentService.getSummary(selectedAgentId),
        incidentService.list({
          status:   filterStatus   || undefined,
          severity: filterSeverity || undefined,
          limit:    PAGE_SIZE,
          offset:   page * PAGE_SIZE,
          agentId:  selectedAgentId || undefined,
        }),
      ]);
      setSummary(sumRes?.data || sumRes || null);
      const listData = listRes?.data || listRes || {};
      setItems(listData.items || []);
      setTotal(listData.total || 0);
    } catch (e) {
      addToast('Failed to load incidents', 'error');
    } finally {
      setLoading(false);
      setHasLoaded(true);
      hasLoadedRef.current = true;
    }
  }, [filterStatus, filterSeverity, page, selectedAgentId, addToast]);

  // Bulk resolve — issues a PATCH per selected id sequentially. Failures are
  // collected per-id so operators see exactly which transition was rejected;
  // successes still surface in a single aggregate toast.
  const handleBulkResolve = useCallback(async () => {
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;
    setBulkResolving(true);
    let ok = 0;
    const failures = [];
    for (const id of ids) {
      try {
        await incidentService.update(id, {
          status: 'RESOLVED',
          note:   'Bulk resolved from Incidents grid',
        });
        ok += 1;
      } catch (err) {
        const detail = err?.response?.data?.detail || err?.message || 'unknown';
        failures.push(`${id.slice(0, 8)}: ${detail}`);
      }
    }
    if (ok > 0) addToast(`Resolved ${ok} incident${ok === 1 ? '' : 's'}`, 'success');
    for (const f of failures) addToast(`Failed — ${f}`, 'error');
    clearSelection();
    setBulkResolving(false);
    await fetchAll();
  }, [selectedIds, addToast, clearSelection, fetchAll]);

  useEffect(() => {
    fetchAll();
    incidentService.getTransitions().then((res) => {
      const t = res?.data?.transitions ?? res?.transitions;
      if (t && typeof t === 'object') setValidTransitions(t);
    }).catch((err) => {
      // Non-fatal: falls back to VALID_TRANSITIONS_FALLBACK.
      console.warn('[Incidents] transitions fetch failed', err);
      addToast('Could not load status workflow — using bundled transitions list', 'info');
    });
  }, [fetchAll, addToast]);

  // SSE refresh is debounced to one fetchAll per 6 s. Without this gate the
  // live-traffic worker's ~3 events/s storm of escalate events triggered a
  // fetchAll storm against the 50-rps tenant bucket → 429s → page flapped on
  // skeletons. Same bug Dashboard had on 2026-06-24 — same fix.
  //
  // DECLARATION ORDER MATTERS: the useEffect + useMemo below put
  // debouncedRefresh in their dep arrays, which are evaluated immediately
  // when the component body runs top-to-bottom. `const` is in TDZ until
  // its declaration line is reached, so declaring debouncedRefresh AFTER
  // those consumers threw `ReferenceError: Cannot access 'O' before
  // initialization` in production (incident 664fb8d5, 2026-06-24 21:47).
  const lastSseRefreshRef = useRef(0);
  const SSE_REFRESH_DEBOUNCE_MS = 6_000;
  const debouncedRefresh = useCallback(() => {
    const now = Date.now();
    if (now - lastSseRefreshRef.current >= SSE_REFRESH_DEBOUNCE_MS) {
      lastSseRefreshRef.current = now;
      fetchAll();
    }
  }, [fetchAll]);

  // Real-time updates: SSE events + 30-second polling fallback.
  // eventBus paths use the SAME 6 s debounce as the useSSE paths so
  // policy_decision events (which fire ~3/s under the live-traffic
  // demo) don't bypass the throttle and storm the gateway with
  // fetchAll calls. The 30 s background poll uses fetchAll directly
  // (it has its own gap-based throttling).
  useEffect(() => {
    const interval = setInterval(fetchAll, 30_000);
    const u1 = eventBus.on('incident_updated', debouncedRefresh);
    const u2 = eventBus.on('policy_decision',  debouncedRefresh);
    return () => { clearInterval(interval); u1(); u2(); };
  }, [fetchAll, debouncedRefresh]);

  // Direct SSE channel subscription for incident-relevant events so the
  // page reacts even if AgentContext isn't translating that event type
  // into the eventBus. Re-pulls the list on any incident/escalation
  // touchpoint. Falls back silently if SSE is disconnected — polling
  // above still covers the gap.
  const sseChannels = useMemo(() => ({
    incident_updated: debouncedRefresh,
    approval_required: debouncedRefresh,
    approval_resolved: debouncedRefresh,
  }), [debouncedRefresh]);
  useSSE({
    channels: sseChannels,
    onMessage: (evt) => {
      const t = String(evt?.type || '').toLowerCase();
      if (t.includes('incident') || t.includes('escalate') || t.includes('approval')) {
        debouncedRefresh();
      }
    },
  });

  const s = summary || {};
  const secScore  = Number(s.security_score ?? 100);
  const openCrit  = Number(s.critical  ?? 0);
  const openHigh  = Number(s.high      ?? 0);
  const openCount = Number(s.open      ?? 0);
  const mttr      = Number(s.mttr_hours ?? 0).toFixed(1);
  const mtta      = Number(s.mtta_hours ?? 0).toFixed(1);
  const trend     = s.trend || 'stable';
  const trendIcon = trend === 'improving' ? '↑' : trend === 'degrading' ? '↓' : '→';
  const trendCls  = trend === 'improving' ? 'text-green-400' : trend === 'degrading' ? 'text-red-400' : 'text-neutral-500';

  // Header-row checkbox state: indeterminate when partial, checked when all.
  const allOnPageSelected = items.length > 0 && items.every((i) => selectedIds.has(i.id));
  const someOnPageSelected = items.some((i) => selectedIds.has(i.id));

  const toggleAllOnPage = () => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (allOnPageSelected) {
        for (const i of items) next.delete(i.id);
      } else {
        for (const i of items) next.add(i.id);
      }
      return next;
    });
  };

  // DataTable columns. The select column owns its own header checkbox; the
  // body cells stopPropagation so a checkbox click does not also fire the
  // row's onRowClick (which would open the IncidentOverlay modal).
  const columns = useMemo(() => [
    {
      key: '_select',
      width: '36px',
      label: (
        <input name="select_all_incidents_on_this_pag"
          type="checkbox"
          aria-label="Select all incidents on this page"
          checked={allOnPageSelected}
          ref={(el) => { if (el) el.indeterminate = !allOnPageSelected && someOnPageSelected; }}
          onChange={toggleAllOnPage}
          onClick={(e) => e.stopPropagation()}
          className="accent-indigo-500 cursor-pointer"
        />
      ),
      render: (_v, row) => (
        <input name="has"
          type="checkbox"
          aria-label={`Select incident ${row.incident_number || row.id}`}
          checked={selectedIds.has(row.id)}
          onChange={() => toggleId(row.id)}
          onClick={(e) => e.stopPropagation()}
          className="accent-indigo-500 cursor-pointer"
        />
      ),
    },
    {
      key: 'status',
      label: 'Status',
      width: '140px',
      render: (status) => <StatusBadge status={status} />,
    },
    {
      key: 'title',
      label: 'Title',
      render: (_t, row) => (
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-[10px] font-mono text-neutral-600 shrink-0">
              {row.incident_number}
            </span>
            <span className="text-xs text-white font-medium truncate">{row.title}</span>
          </div>
          <p className="text-[10px] text-neutral-600 mt-0.5 font-mono truncate">
            {row.tool || 'N/A'} · Risk {((row.risk_score ?? 0) * 100).toFixed(0)}%
          </p>
        </div>
      ),
    },
    {
      key: 'agent_id',
      label: 'Agent',
      width: '120px',
      render: (agentId) => (
        <code className="text-[11px] font-mono text-neutral-400">
          {agentId ? agentId.slice(0, 8) : '—'}
        </code>
      ),
    },
    {
      key: 'created_at',
      label: 'Opened',
      width: '110px',
      render: (createdAt) => (
        <span className="text-[11px] text-neutral-500" title={createdAt ? new Date(createdAt).toLocaleString() : ''}>
          {_relTime(createdAt)}
        </span>
      ),
    },
    {
      key: 'severity',
      label: 'Severity',
      width: '110px',
      render: (severity) => <SeverityBadge severity={severity} />,
    },
    {
      key: 'owner',
      label: 'Owner',
      width: '110px',
      render: (_o, row) => (
        <span className="text-[11px] text-neutral-500">
          {row.owner || row.assigned_to || '—'}
        </span>
      ),
    },
  ], [selectedIds, allOnPageSelected, someOnPageSelected, items, toggleId]);

  return (
    <div className="space-y-6">
      {/* Page header */}
      <div className="flex items-center justify-between">
        <div>
          <div className="flex items-center gap-2 flex-wrap">
            <h1 className="text-lg font-semibold text-white">Incident Management</h1>
            {selectedAgent && (
              <span className="inline-flex items-center gap-1.5 text-[10px] px-2 py-0.5 rounded-full bg-white/[0.05] border border-white/10 text-neutral-400">
                <Filter size={9} /> Scope: {selectedAgent.name || selectedAgentId.slice(0, 8)}
              </span>
            )}
          </div>
          <p className="text-xs text-neutral-500 mt-0.5">All security events — open and resolved</p>
        </div>
        <Button variant="secondary" size="sm" onClick={fetchAll} disabled={loading}>
          <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
          Refresh
        </Button>
      </div>

      {/* Tab navigation */}
      <div className="flex gap-1 p-1 bg-white/[0.02] border border-white/[0.06] rounded-lg w-fit">
        {[
          { id: 'incidents', label: 'Incidents', icon: AlertTriangle },
          { id: 'soc',       label: 'SOC Feed',  icon: Activity },
        ].map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => setActiveTab(id)}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium transition-colors ${
              activeTab === id
                ? 'bg-white/[0.08] text-white'
                : 'text-neutral-500 hover:text-neutral-300'
            }`}
          >
            <Icon size={12} />
            {label}
          </button>
        ))}
      </div>

      {/* SOC Feed tab */}
      {activeTab === 'soc' && <SocFeed />}

      {/* Incidents tab content */}
      {activeTab !== 'soc' && <>

      {/* Summary cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <Card className="p-4 flex items-center gap-4">
          <ScoreGauge score={secScore} />
          <div>
            <p className="text-xs text-neutral-500">Security Score</p>
            <p className="text-[10px] text-neutral-600 mt-0.5">
              {secScore >= 80 ? 'Healthy' : secScore >= 60 ? 'At Risk' : 'Critical'}
            </p>
            <p className={`text-[10px] font-mono mt-0.5 ${trendCls}`}>{trendIcon} {trend}</p>
          </div>
        </Card>

        <Card className="p-4">
          <div className="flex items-center gap-2 mb-3">
            <AlertTriangle size={14} className="text-red-400" />
            <p className="text-xs text-neutral-500">Open Incidents</p>
          </div>
          <p className="text-2xl font-bold text-white">{openCount}</p>
          {openCrit > 0 && (
            <p className="text-[10px] text-red-400 mt-1">{openCrit} critical</p>
          )}
        </Card>

        <Card className="p-4">
          <div className="flex items-center gap-2 mb-3">
            <Activity size={14} className="text-amber-400" />
            <p className="text-xs text-neutral-500">High Severity</p>
          </div>
          <p className="text-2xl font-bold text-white">{openHigh}</p>
          <p className="text-[10px] text-neutral-600 mt-1">open high-risk</p>
        </Card>

        <Card className="p-4">
          <div className="flex items-center gap-2 mb-3">
            <Clock size={14} className="text-blue-400" />
            <p className="text-xs text-neutral-500">MTTR / MTTA</p>
          </div>
          <p className="text-2xl font-bold text-white">{mttr}h</p>
          <p className="text-[10px] text-neutral-600 mt-1">ack in {mtta}h · resolve in {mttr}h</p>
        </Card>
      </div>

      {/* Filters */}
      <Card className="p-4">
        <div className="flex flex-wrap items-center gap-3">
          <Filter size={13} className="text-neutral-500" />
          <select name="select"
            value={filterStatus}
            onChange={(e) => { setFilterStatus(e.target.value); setPage(0); }}
            className="input-standard input-compact text-xs w-36"
          >
            {STATUS_OPTIONS.map((s) => (
              <option key={s} value={s}>{s || 'All Statuses'}</option>
            ))}
          </select>
          <select name="select"
            value={filterSeverity}
            onChange={(e) => { setFilterSeverity(e.target.value); setPage(0); }}
            className="input-standard input-compact text-xs w-36"
          >
            {SEVERITY_OPTIONS.map((s) => (
              <option key={s} value={s}>{s || 'All Severities'}</option>
            ))}
          </select>
          {(filterStatus || filterSeverity) && (
            <button
              onClick={() => { setFilterStatus(''); setFilterSeverity(''); setPage(0); }}
              className="text-xs text-neutral-500 hover:text-white transition-colors"
            >
              Clear filters
            </button>
          )}
          <span className="ml-auto text-xs text-neutral-600">{total} total</span>
        </div>
      </Card>

      {/* Cross-agent correlation (Gap 6) */}
      {items.length > 0 && (() => {
        const agentMap = {};
        for (const inc of items) {
          if (!agentMap[inc.agent_id]) agentMap[inc.agent_id] = { id: inc.agent_id, incidents: [], maxSev: 'LOW' };
          agentMap[inc.agent_id].incidents.push(inc);
          const sevOrd = { CRITICAL: 4, HIGH: 3, MEDIUM: 2, LOW: 1 };
          if ((sevOrd[inc.severity] || 0) > (sevOrd[agentMap[inc.agent_id].maxSev] || 0)) {
            agentMap[inc.agent_id].maxSev = inc.severity;
          }
        }
        const correlated = Object.values(agentMap).filter(a => a.incidents.length > 1).sort((a, b) => b.incidents.length - a.incidents.length).slice(0, 5);
        // 2026-05-14: always render the section. Previously `return null` hid
        // the entire panel when there were no cross-agent correlations, which
        // looked identical to a broken page. Empty state is now explicit.
        return (
          <Card className="p-4">
            <div className="flex items-center gap-2 mb-3">
              <Activity size={13} className="text-purple-400" />
              <p className="text-xs font-medium text-neutral-300">Cross-Agent Correlation</p>
              <span className="text-[10px] text-neutral-600 ml-auto">
                {correlated.length > 0 ? `${correlated.length} agents with multiple incidents` : 'no agents with repeat incidents'}
              </span>
            </div>
            {correlated.length === 0 && (
              <div className="py-4 text-center text-xs text-neutral-600">
                No cross-agent correlations in the current window.
              </div>
            )}
            <div className="space-y-2">
              {correlated.map(a => (
                <div key={a.id} className="flex items-center gap-3 py-1.5 border-b border-white/[0.04] last:border-0">
                  <div className={`w-1.5 h-1.5 rounded-full shrink-0 ${SEV_CONFIG[a.maxSev]?.dot || 'bg-neutral-600'}`} />
                  <code className="text-xs text-neutral-400 font-mono">{a.id.slice(0, 8)}</code>
                  <div className="flex gap-1 flex-wrap flex-1">
                    {a.incidents.map(inc => (
                      <button key={inc.id} onClick={() => setSelected(inc)}
                        className="text-[10px] px-1.5 py-0.5 rounded border border-white/[0.08] text-neutral-500 hover:text-white hover:border-white/20 transition-colors font-mono">
                        {inc.incident_number}
                      </button>
                    ))}
                  </div>
                  <span className="text-[10px] text-neutral-600 shrink-0">{a.incidents.length} incidents</span>
                </div>
              ))}
            </div>
          </Card>
        );
      })()}

      {/* Bulk-actions toolbar — appears only when one or more incidents are
          checked. Resolve issues a PATCH per id; clearing wipes the set. */}
      {selectedIds.size > 0 && (
        <Card className="px-4 py-2.5 flex items-center gap-3 border-indigo-500/30 bg-indigo-500/[0.04]">
          <span className="text-xs text-neutral-300">
            <strong className="text-white">{selectedIds.size}</strong> incident{selectedIds.size === 1 ? '' : 's'} selected
          </span>
          <Button size="sm" onClick={handleBulkResolve} disabled={bulkResolving}>
            {bulkResolving ? 'Resolving…' : 'Mark resolved'}
          </Button>
          <button
            type="button"
            onClick={clearSelection}
            disabled={bulkResolving}
            className="text-xs text-neutral-500 hover:text-white transition-colors disabled:opacity-40"
          >
            Clear selection
          </button>
        </Card>
      )}

      {/* Incident list — DataTable replaces the custom div list. The
          empty-state hint that used to live inline now sits below the table
          when there are zero rows. The full system-healthy CTA only renders
          once the first load resolves so we never flash "0 incidents"
          before the API responds. */}
      {!hasLoaded ? (
        <Card>
          <div className="p-4">
            <SkeletonLoader variant="row" count={5} />
          </div>
        </Card>
      ) : (
        <DataTable
          columns={columns}
          data={items}
          isLoading={loading}
          onRowClick={(row) => setSelected(row)}
          emptyMessage="No incidents in this window."
        />
      )}

      {hasLoaded && !loading && items.length === 0 && (
        <Card className="px-6 py-10">
          <div className="flex flex-col items-center text-center max-w-md mx-auto gap-3">
            <div className="w-12 h-12 rounded-2xl bg-green-500/10 border border-green-500/20 flex items-center justify-center">
              <CheckCircle2 size={22} className="text-green-400" aria-hidden="true" />
            </div>
            <div>
              <p className="text-sm text-neutral-200 font-medium">No incidents — system healthy</p>
              <p className="text-xs text-neutral-500 mt-1 leading-relaxed">
                The Incidents grid lights up when the gateway denies, kills, or
                escalates a tool call. Trigger one from the Live Demo page (try
                the <code className="font-mono text-neutral-400">cat /etc/passwd</code> prompt) to populate this view.
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2 justify-center mt-1">
              <Link
                to="/dashboard"
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-neutral-200 bg-white/[0.04] border border-white/[0.08] hover:border-white/20 hover:text-white transition-colors"
              >
                <Activity size={12} aria-hidden="true" /> Back to Dashboard
              </Link>
              <Link
                to="/live-demo"
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-purple-300 bg-purple-500/[0.08] border border-purple-500/20 hover:border-purple-500/40 transition-colors"
              >
                <PlayCircle size={12} aria-hidden="true" /> Try Live Demo
              </Link>
            </div>
          </div>
        </Card>
      )}

      {/* Pagination */}
      {total > PAGE_SIZE && (
        <Card className="flex items-center justify-between px-4 py-3">
          <Button size="sm" variant="secondary" onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={page === 0}>
            Previous
          </Button>
          <span className="text-xs text-neutral-500">
            {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, total)} of {total}
          </span>
          <Button size="sm" variant="secondary" onClick={() => setPage((p) => p + 1)} disabled={(page + 1) * PAGE_SIZE >= total}>
            Next
          </Button>
        </Card>
      )}

      </> /* end incidents tab */}

      {/* Detail modal — shown regardless of active tab */}
      {selected && (
        <IncidentDetail
          incident={selected}
          onClose={() => setSelected(null)}
          onRefresh={fetchAll}
          validTransitions={validTransitions}
        />
      )}
    </div>
  );
}

export default function Incidents() {
  return (
    <ErrorBoundary>
      <IncidentsPage />
    </ErrorBoundary>
  );
}
