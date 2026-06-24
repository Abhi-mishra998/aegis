import React, { useState, useEffect, useId } from 'react';
import { registryService } from '../services/api';
import { Link, useNavigate } from 'react-router-dom';
import { useAgents } from '../hooks/useAgents';
import { useAuth } from '../hooks/useAuth';
import { eventBus } from '../lib/eventBus';
import {
  Bot,
  Plus,
  AlertCircle,
  Search,
  Trash2,
  Activity,
  ExternalLink,
  CheckCircle2,
  RefreshCw,
  User,
  ShieldAlert,
  RotateCcw,
} from 'lucide-react';
import Card from '../components/Common/Card';
import Button from '../components/Common/Button';
import DataTable from '../components/Common/DataTable';
import SkeletonLoader from '../components/Common/SkeletonLoader';
import Modal from '../components/Common/Modal';
import ConfirmDialog from '../components/Common/ConfirmDialog';

/* ── Risk badge helper ─────────────────────────────────────────────────────── */
function RiskBadge({ score }) {
  const n = Number(score) || 0;
  const style =
    n < 30  ? 'text-green-400 bg-green-500/10 border-green-500/20' :
    n < 70  ? 'text-amber-400 bg-amber-500/10 border-amber-500/20' :
              'text-red-400   bg-red-500/10   border-red-500/20';
  return (
    <span className={`status-badge ${style}`}>
      <Activity size={10} aria-hidden="true" />
      {n}/100
    </span>
  );
}

/* ── Status badge ──────────────────────────────────────────────────────────── */
function StatusBadge({ status }) {
  const s = (status || 'unknown').toLowerCase();
  const style =
    s === 'active'      ? 'text-green-400 bg-green-500/10 border-green-500/20' :
    s === 'quarantined' ? 'text-amber-400 bg-amber-500/10 border-amber-500/20' :
    s === 'terminated'  ? 'text-red-400   bg-red-500/10   border-red-500/20' :
                          'text-neutral-400 bg-white/5 border-white/10';
  return <span className={`status-badge ${style}`}>{s}</span>;
}

/* ── Main component ────────────────────────────────────────────────────────── */
export default function Agents() {
  const navigate = useNavigate();
  const formId = useId();
  const { refreshAgents, setSelectedAgentId } = useAgents();
  const { addToast } = useAuth();

  const [agents,  setAgents]  = useState([]);
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState('');
  const [creating, setCreating] = useState(false);
  const [success,  setSuccess]  = useState('');
  const [search,   setSearch]   = useState('');

  /* Create form state */
  const [newName, setNewName] = useState('');
  const [newDesc, setNewDesc] = useState('');

  /* Delete confirmation modal */
  const [deleteTarget,     setDeleteTarget]     = useState(null); // { id, name }
  const [deleting,         setDeleting]         = useState(false);

  /* Quarantine confirmation */
  const [quarantineTarget, setQuarantineTarget] = useState(null); // { id, name }

  /* Reactivation confirmation */
  const [reactivateTarget, setReactivateTarget] = useState(null); // { id, name }

  const fetchAgents = async () => {
    setLoading(true);
    try {
      const [res, sumRes] = await Promise.allSettled([
        registryService.listAgents(),
        registryService.getSummary(),
      ]);
      if (res.status === 'fulfilled') {
        // Canonical shape from gateway: { success, data: { items: [...] } }.
        const r = res.value;
        setAgents(r?.data?.items || []);
        setError('');
      } else {
        setError(res.reason?.message || 'Failed to reach identity module.');
      }
      if (sumRes.status === 'fulfilled') {
        setSummary(sumRes.value?.data || sumRes.value || null);
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchAgents();
    const interval = setInterval(fetchAgents, 30_000);
    // React to SSE-driven agent changes — AgentContext drives the source SSE
    // connection and republishes 'agent_changed' on the in-process eventBus.
    const unsubscribe = eventBus.on('agent_changed', () => {
      fetchAgents();
    });
    return () => {
      clearInterval(interval);
      unsubscribe?.();
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleCreate = async (e) => {
    e.preventDefault();
    const name = newName.trim();
    const desc = newDesc.trim();
    if (!name) return;
    if (!/^[a-z0-9][a-z0-9_-]{1,98}[a-z0-9]$/.test(name)) {
      setError('Name must be 3–100 chars, lowercase letters/digits/hyphen/underscore, start and end with a letter or digit.');
      return;
    }
    if (desc.length > 0 && desc.length < 10) {
      setError('Description must be at least 10 characters (or leave it blank).');
      return;
    }
    setCreating(true);
    setError('');
    setSuccess('');
    try {
      const res = await registryService.createAgent({ name, description: desc || 'No description provided.', owner_id: 'sys_admin' });
      setNewName('');
      setNewDesc('');
      setSuccess(`Agent "${name}" deployed successfully.`);
      // Auto-select the new agent globally
      const newId = res?.data?.id || res?.id;
      if (newId) setSelectedAgentId(newId);
      await fetchAgents();
      await refreshAgents(); // sync Topbar selector
    } catch (err) {
      setError(err.message || 'Deployment failed.');
    } finally {
      setCreating(false);
    }
  };

  const confirmDelete = (agent) => setDeleteTarget({ id: agent.id, name: agent.name });

  const handleDelete = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await registryService.deleteAgent(deleteTarget.id);
      setDeleteTarget(null);
      await fetchAgents();
      await refreshAgents(); // sync Topbar selector
    } catch (err) {
      setError(err.message || 'Decommission failed.');
      setDeleteTarget(null);
    } finally {
      setDeleting(false);
    }
  };

  const handleQuarantine = async () => {
    try {
      await registryService.updateAgent(quarantineTarget.id, { status: 'QUARANTINED' });
      await fetchAgents();
      await refreshAgents();
      addToast(`Agent "${quarantineTarget.name}" quarantined`, 'info');
    } catch (err) {
      addToast(err?.message || 'Quarantine failed', 'error');
    }
  };

  const handleReactivate = async () => {
    try {
      await registryService.updateAgent(reactivateTarget.id, { status: 'ACTIVE' });
      await fetchAgents();
      await refreshAgents();
      addToast(`Agent "${reactivateTarget.name}" reactivated`, 'success');
    } catch (err) {
      addToast(err?.message || 'Reactivation failed', 'error');
    }
  };

  const filtered = agents.filter((a) =>
    (a.name ?? '').toLowerCase().includes(search.toLowerCase()) ||
    (a.id   ?? '').toLowerCase().includes(search.toLowerCase())
  );

  const columns = [
    {
      key: 'name',
      label: 'Agent',
      render: (val, row) => (
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-white/[0.04] border border-white/[0.06] flex items-center justify-center shrink-0">
            <Bot size={15} className="text-neutral-400" aria-hidden="true" />
          </div>
          <div className="min-w-0">
            <p className="text-xs font-semibold text-white truncate">{val}</p>
            <p className="text-xs text-neutral-600 font-mono truncate">{row.id?.slice(0, 16)}…</p>
          </div>
        </div>
      ),
    },
    {
      key: 'description',
      label: 'Description',
      render: (val) => (
        <span className="text-xs text-neutral-400 italic truncate block max-w-[200px]">
          {val || '—'}
        </span>
      ),
    },
    {
      key: 'status',
      label: 'Status',
      render: (val) => <StatusBadge status={val} />,
    },
    {
      key: 'risk_score',
      label: 'Risk',
      render: (val) => <RiskBadge score={val} />,
    },
    {
      key: 'actions',
      label: '',
      width: '80px',
      render: (_, row) => (
        <div className="flex items-center gap-1 justify-end">
          <Button
            variant="ghost"
            size="icon"
            aria-label={`View profile for ${row.name}`}
            title="Agent profile"
            onClick={(e) => { e.stopPropagation(); navigate(`/agents/${row.id}/profile`); }}
          >
            <User size={13} aria-hidden="true" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            aria-label={`Inspect forensics for ${row.name}`}
            title="Forensics"
            onClick={(e) => { e.stopPropagation(); navigate(`/forensics?agent=${row.id}`); }}
          >
            <ExternalLink size={13} aria-hidden="true" />
          </Button>
          {(row.status ?? '').toLowerCase() !== 'quarantined' && (row.status ?? '').toLowerCase() !== 'terminated' && (
            <Button
              variant="ghost"
              size="icon"
              aria-label={`Quarantine agent ${row.name}`}
              title="Quarantine"
              className="hover:text-amber-400 hover:bg-amber-500/10"
              onClick={(e) => { e.stopPropagation(); setQuarantineTarget({ id: row.id, name: row.name }); }}
            >
              <ShieldAlert size={13} aria-hidden="true" />
            </Button>
          )}
          {['quarantined', 'inactive', 'suspended'].includes((row.status ?? '').toLowerCase()) && (
            <Button
              variant="ghost"
              size="icon"
              aria-label={`Reactivate agent ${row.name}`}
              title="Reactivate"
              className="hover:text-green-400 hover:bg-green-500/10"
              onClick={(e) => { e.stopPropagation(); setReactivateTarget({ id: row.id, name: row.name }); }}
            >
              <RotateCcw size={13} aria-hidden="true" />
            </Button>
          )}
          <Button
            variant="ghost"
            size="icon"
            aria-label={`Delete agent ${row.name}`}
            className="hover:text-red-400 hover:bg-red-500/10"
            onClick={(e) => { e.stopPropagation(); confirmDelete(row); }}
          >
            <Trash2 size={13} aria-hidden="true" />
          </Button>
        </div>
      ),
    },
  ];

  return (
    <div className="space-y-8 animate-fade-in">
      {/* ── Page header ── */}
      <div className="page-header">
        <div className="space-y-1">
          <h1 className="text-2xl font-bold tracking-tight text-white">Agent Registry</h1>
          <p className="text-xs text-neutral-500">Identity management and behavioral posturing</p>
        </div>
        <div className="flex items-center gap-3 flex-wrap">
          {/* Search */}
          <div className="relative group">
            <Search
              size={14}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-neutral-600 group-focus-within:text-neutral-400 transition-colors"
              aria-hidden="true"
            />
            <input name="search_agents"
              type="search"
              placeholder="Search agents…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              aria-label="Search agents by name or ID"
              className="input-standard h-9 pl-9 w-full sm:w-56"
            />
          </div>

          <Button
            variant="ghost"
            size="sm"
            aria-label="Refresh agent list"
            onClick={fetchAgents}
          >
            <RefreshCw size={14} aria-hidden="true" />
          </Button>
        </div>
      </div>

      {/* ── Alerts ── */}
      {error && (
        <div className="error-banner" role="alert">
          <div className="flex items-center gap-3">
            <AlertCircle size={15} className="text-red-400 shrink-0" aria-hidden="true" />
            <p className="text-xs text-red-400">{error}</p>
          </div>
          <Button variant="danger" size="sm" onClick={fetchAgents}>Retry</Button>
        </div>
      )}

      {success && (
        <div className="flex items-center gap-3 p-3.5 rounded-xl bg-green-500/[0.07] border border-green-500/20 animate-scale-in" role="status">
          <CheckCircle2 size={15} className="text-green-400 shrink-0" aria-hidden="true" />
          <p className="text-xs text-green-400">{success}</p>
        </div>
      )}

      {/* ── Sprint 2 — Onboarding Wizard CTA ── */}
      <Link
        to="/onboarding"
        className="block group rounded-2xl border border-white/[0.07] bg-gradient-to-r from-white/[0.04] to-white/[0.01] hover:border-white/20 hover:from-white/[0.07] transition-all p-5"
        aria-label="Start the Aegis Onboarding Wizard"
      >
        <div className="flex items-center gap-4">
          <div className="w-11 h-11 rounded-xl bg-white text-black flex items-center justify-center shadow-[0_0_24px_rgba(255,255,255,0.15)] shrink-0">
            <Plus size={20} aria-hidden="true" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="text-base font-semibold text-white flex items-center gap-2">
              Add an agent via the Onboarding Wizard
              <span className="text-[10px] uppercase tracking-widest text-neutral-500 border border-white/10 rounded px-1.5 py-0.5">Recommended</span>
            </div>
            <p className="text-xs text-neutral-400 mt-1">
              Pick your SDK → name your agent → copy the install snippet. ~60 seconds.
              Your LLM key never touches Aegis.
            </p>
          </div>
          <div className="text-xs text-neutral-500 group-hover:text-white transition-colors hidden sm:flex items-center gap-1">
            Start
            <ExternalLink size={12} aria-hidden="true" />
          </div>
        </div>
      </Link>

      {/* ── Deploy + fleet status ── */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
        {/* Deploy form */}
        <div className="xl:col-span-2">
          <Card title="Deploy Security Node" icon={Plus}>
            <form
              id={formId}
              onSubmit={handleCreate}
              className="grid grid-cols-1 sm:grid-cols-2 gap-4"
            >
              <div className="space-y-1.5">
                <label htmlFor="agentName" className="label-standard">
                  Agent Name <span className="text-red-400" aria-hidden="true">*</span>
                </label>
                <input
                  id="agentName"
                  type="text"
                  required
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  placeholder="e.g. customer-agent-01"
                  className="input-standard h-10"
                />
              </div>
              <div className="space-y-1.5">
                <label htmlFor="agentDesc" className="label-standard">
                  Description
                </label>
                <input
                  id="agentDesc"
                  type="text"
                  value={newDesc}
                  onChange={(e) => setNewDesc(e.target.value)}
                  placeholder="Optional scope or purpose"
                  className="input-standard h-10"
                />
              </div>
              <div className="sm:col-span-2 flex justify-end">
                <Button
                  type="submit"
                  loading={creating}
                  disabled={creating || !newName.trim()}
                  size="sm"
                >
                  <Plus size={14} aria-hidden="true" />
                  Deploy Agent
                </Button>
              </div>
            </form>
          </Card>
        </div>

        {/* Fleet summary */}
        <Card title="Fleet Status">
          {loading ? (
            <SkeletonLoader variant="text" count={1} />
          ) : (
            <div className="space-y-4">
              {(() => {
                const total = summary?.total ?? agents.length;
                const active = summary?.active ?? agents.filter((a) => (a.status ?? '').toLowerCase() === 'active').length;
                const quarantined = summary?.quarantined ?? agents.filter((a) => (a.status ?? '').toLowerCase() === 'quarantined').length;
                const highRisk = summary?.high_risk ?? agents.filter((a) => ['high', 'critical'].includes((a.risk_level ?? '').toLowerCase())).length;
                return (
                  <>
                    <div className="metric-row">
                      <span className="text-xs text-neutral-500">Total Agents</span>
                      <span className="text-sm font-bold text-white">{total > 0 ? total : '—'}</span>
                    </div>
                    <div className="metric-row">
                      <span className="text-xs text-neutral-500">Active</span>
                      <span className={`text-sm font-semibold ${active > 0 ? 'text-green-400' : 'text-neutral-500'}`}>
                        {active > 0 ? active : '—'}
                      </span>
                    </div>
                    <div className="metric-row">
                      <span className="text-xs text-neutral-500">Quarantined</span>
                      <span className={`text-sm font-semibold ${quarantined > 0 ? 'text-amber-400' : 'text-neutral-500'}`}>
                        {quarantined > 0 ? quarantined : '—'}
                      </span>
                    </div>
                    <div className="metric-row">
                      <span className="text-xs text-neutral-500">High Risk</span>
                      <span className={`text-sm font-semibold ${highRisk > 0 ? 'text-red-400' : 'text-neutral-500'}`}>
                        {highRisk > 0 ? highRisk : '—'}
                      </span>
                    </div>
                    <div className="flex items-center gap-2 pt-2">
                      <span className="w-1.5 h-1.5 rounded-full bg-green-500" aria-hidden="true" />
                      <span className="text-xs text-neutral-500">Drift detection active</span>
                    </div>
                  </>
                );
              })()}
            </div>
          )}
        </Card>
      </div>

      {/* ── Agent table ── */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <div className="section-header">
            <Activity size={14} className="text-neutral-600" aria-hidden="true" />
            Active Identity Inventory
          </div>
          <span className="text-xs text-neutral-600">
            {filtered.length} agent{filtered.length !== 1 ? 's' : ''}
            {search ? ` matching "${search}"` : ''}
          </span>
        </div>

        {loading ? (
          <SkeletonLoader variant="row" count={5} />
        ) : filtered.length === 0 && !search ? (
          <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-10 text-center space-y-4">
            <div className="w-12 h-12 mx-auto rounded-2xl bg-white/[0.04] border border-white/[0.06] flex items-center justify-center">
              <Bot size={22} className="text-neutral-500" aria-hidden="true" />
            </div>
            <div className="space-y-1">
              <p className="text-sm font-semibold text-neutral-200">No agents registered</p>
              <p className="text-xs text-neutral-500 max-w-md mx-auto">
                Go to the Onboarding Wizard to register your first agent. Pick your SDK,
                copy the install snippet, and Aegis will start governing in under a minute.
              </p>
            </div>
            <div>
              <Link
                to="/onboarding"
                className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-white text-black text-xs font-semibold hover:bg-neutral-200 transition-colors"
              >
                <Plus size={13} aria-hidden="true" />
                Register your first agent
              </Link>
            </div>
          </div>
        ) : (
          <DataTable
            columns={columns}
            data={filtered}
            emptyMessage={search ? `No agents match "${search}".` : 'No agents registered yet.'}
          />
        )}
      </div>

      {/* ── Quarantine confirmation ── */}
      <ConfirmDialog
        isOpen={quarantineTarget !== null}
        title="Quarantine Agent"
        description={`Quarantine "${quarantineTarget?.name}"? The agent will be blocked from executing tools until reactivated.`}
        confirmLabel="Quarantine"
        variant="danger"
        onConfirm={handleQuarantine}
        onClose={() => setQuarantineTarget(null)}
        onError={(e) => { addToast(e?.message || 'Quarantine failed', 'error'); setQuarantineTarget(null); }}
      />

      {/* ── Reactivate confirmation ── */}
      <ConfirmDialog
        isOpen={reactivateTarget !== null}
        title="Reactivate Agent"
        description={`Reactivate "${reactivateTarget?.name}"? The agent will resume normal execution under its existing policy rules.`}
        confirmLabel="Reactivate"
        variant="default"
        onConfirm={handleReactivate}
        onClose={() => setReactivateTarget(null)}
        onError={(e) => { addToast(e?.message || 'Reactivation failed', 'error'); setReactivateTarget(null); }}
      />

      {/* ── Delete confirmation modal ── */}
      <Modal
        isOpen={!!deleteTarget}
        title="Confirm Decommission"
        onClose={() => setDeleteTarget(null)}
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setDeleteTarget(null)}>
              Cancel
            </Button>
            <Button
              variant="danger"
              size="sm"
              loading={deleting}
              onClick={handleDelete}
            >
              Decommission
            </Button>
          </>
        }
      >
        <p className="text-sm text-neutral-300">
          Permanently decommission agent{' '}
          <span className="font-bold text-white">"{deleteTarget?.name}"</span>?
        </p>
        <p className="text-xs text-neutral-500 mt-2">
          This action cannot be undone. All associated permissions and audit events will be preserved.
        </p>
      </Modal>
    </div>
  );
}
