import React, { Suspense, lazy, useEffect, useMemo, useState } from 'react';
import { useSearchParams, useParams, useLocation, Link, useNavigate } from 'react-router-dom';
import {
  ArrowLeft,
  Bot,
  DollarSign,
  HeartPulse,
  Network,
  Share2,
  User,
} from 'lucide-react';
import TabErrorBoundary from '../components/Common/TabErrorBoundary';
import { registryService } from '../services/api';

const AgentProfile  = lazy(() => import('./AgentProfile'));
const AgentHealth   = lazy(() => import('./AgentHealth'));
const AgentCost     = lazy(() => import('./AgentCost'));
const AgentTopology = lazy(() => import('./AgentTopology'));

const TABS = [
  { id: 'overview', label: 'Overview', icon: User,        Component: AgentProfile  },
  { id: 'health',   label: 'Health',   icon: HeartPulse,  Component: AgentHealth   },
  { id: 'cost',     label: 'Cost',     icon: DollarSign,  Component: AgentCost     },
  { id: 'topology', label: 'Topology', icon: Share2,      Component: AgentTopology },
];
const DEFAULT_TAB_ID = TABS[0].id;
const VALID_TAB_IDS = new Set(TABS.map((t) => t.id));

/**
 * Sprint 6 — AgentSnapshot tab router.
 *
 * Single page rendered at /agents/:id that exposes 4 lazy-loaded
 * legacy pages (AgentProfile, AgentHealth, AgentCost, AgentTopology)
 * as tabs. URL state lives in `?tab=…`. The bare path
 * /agents/:id/profile is preserved as a redirect from App.jsx so
 * analyst bookmarks keep working.
 *
 * URL via React Router 6's useSearchParams is the single source of
 * truth — see Settings.jsx / Policies.jsx for the same pattern. The
 * previous useState mirror + navigate('?tab=…') sequence could let an
 * unrelated background re-render unmount the child tab mid-render,
 * which users perceived as "tabs blink one time, no content shows."
 */
// Sidebar entry-point routes (no :id in URL) map to a default tab so
// the user lands on the section they clicked. Without this, every
// agentless route fell into "overview" → AgentProfile.useParams() →
// id=undefined → 9 backend requests to /agents/undefined/profile → 422
// across the board and the page rendered "Agent …" with no content.
const PATH_TO_DEFAULT_TAB = {
  '/agent-health':   'health',
  '/agent-cost':     'cost',
  '/agent-topology': 'topology',
};

export default function AgentSnapshot() {
  const { id } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const { pathname } = useLocation();
  const navigate = useNavigate();

  const pathDefaultTab = PATH_TO_DEFAULT_TAB[pathname];

  const activeTab = useMemo(() => {
    const param = searchParams.get('tab');
    if (param && VALID_TAB_IDS.has(param)) return param;
    if (pathDefaultTab) return pathDefaultTab;
    return DEFAULT_TAB_ID;
  }, [searchParams, pathDefaultTab]);

  const handleTabClick = (tabId) => {
    setSearchParams({ tab: tabId }, { replace: true });
  };

  const ActiveComponent = useMemo(() => {
    const tab = TABS.find((t) => t.id === activeTab) || TABS[0];
    return tab.Component;
  }, [activeTab]);

  // The legacy AgentCost / AgentHealth pages handle agentId=undefined
  // themselves (they show a fleet-wide rollup); AgentProfile + AgentTopology
  // do NOT — they fire /agents/${id}/* with id=undefined, get 422 from the
  // gateway, and render a broken page. Block those two tabs with an
  // agent-picker when there's no :id segment in the URL.
  const tabHandlesNoId = activeTab === 'cost' || activeTab === 'health';
  const needsAgentPicker = !id && !tabHandlesNoId;

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="space-y-1">
          <Link
            to="/agents"
            className="inline-flex items-center gap-1.5 text-[11px] text-neutral-500 hover:text-neutral-300"
          >
            <ArrowLeft size={12} aria-hidden="true" />
            Back to agents
          </Link>
          <h1 className="text-2xl font-bold tracking-tight text-white">
            {id ? (
              <>Agent <span className="font-mono text-base text-neutral-400">{id.slice(0, 12)}…</span></>
            ) : (
              pathDefaultTab ? `Agent ${pathDefaultTab}` : 'Agent snapshot'
            )}
          </h1>
        </div>
        <div className="flex items-center gap-2 text-[10px] text-neutral-600">
          <Network size={12} aria-hidden="true" />
          <span>Snapshot · {activeTab}</span>
        </div>
      </div>

      <div className="flex gap-1 overflow-x-auto pb-1 border-b border-white/[0.06]" role="tablist">
        {TABS.map(({ id: tabId, label, icon: Icon }) => {
          const isActive = tabId === activeTab;
          return (
            <button
              key={tabId}
              type="button"
              role="tab"
              aria-selected={isActive}
              onClick={() => handleTabClick(tabId)}
              className={
                'flex items-center gap-1.5 px-3 h-9 rounded-t-md text-xs font-medium transition-all whitespace-nowrap ' +
                (isActive
                  ? 'bg-white/[0.08] text-white border border-white/[0.1] border-b-transparent -mb-px'
                  : 'text-neutral-400 hover:text-white hover:bg-white/[0.04]')
              }
            >
              <Icon size={13} aria-hidden="true" />
              {label}
            </button>
          );
        })}
      </div>

      <TabErrorBoundary tabId={activeTab}>
        <Suspense
          fallback={
            <div className="text-xs text-neutral-500 py-8 text-center">Loading {activeTab}…</div>
          }
        >
          {needsAgentPicker ? (
            <AgentPicker
              activeTab={activeTab}
              onPick={(agentId) => navigate(`/agents/${agentId}?tab=${activeTab}`)}
            />
          ) : (
            <ActiveComponent />
          )}
        </Suspense>
      </TabErrorBoundary>
    </div>
  );
}

// Minimal agent picker shown when the user lands on a per-agent tab
// (overview / topology) without an :id in the URL. Lists active agents
// from the workspace and navigates into the right tab. Keeps the page
// useful instead of firing /agents/undefined/profile and rendering a
// broken state.
function AgentPicker({ activeTab, onPick }) {
  const [agents, setAgents] = useState(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let cancelled = false;
    registryService.listAgents()
      .then((resp) => {
        if (cancelled) return;
        const items = resp?.data?.items || resp?.data || resp?.items || [];
        setAgents(Array.isArray(items) ? items : []);
      })
      .catch((e) => { if (!cancelled) setError(e?.message || 'Failed to load agents'); });
    return () => { cancelled = true; };
  }, []);

  if (error) {
    return (
      <div className="text-xs text-rose-300 p-4 border border-rose-900/40 rounded">
        Could not list agents: {error}
      </div>
    );
  }
  if (agents === null) {
    return <div className="text-xs text-neutral-500 py-6 text-center">Loading agents…</div>;
  }
  if (agents.length === 0) {
    return (
      <div className="space-y-3 text-center py-10 text-neutral-400">
        <Bot size={20} className="mx-auto text-neutral-600" aria-hidden="true" />
        <div className="text-sm text-white font-medium">No agents registered yet</div>
        <div className="text-xs text-neutral-500 max-w-sm mx-auto">
          The {activeTab} tab is per-agent. Onboard your first agent through
          the wizard, then come back here.
        </div>
        <Link to="/onboarding" className="inline-block mt-1 text-emerald-400 hover:text-emerald-300 text-xs">
          Open Onboarding Wizard →
        </Link>
      </div>
    );
  }
  return (
    <div className="space-y-2">
      <div className="text-[10px] uppercase tracking-widest text-neutral-500">
        Pick an agent to view its {activeTab}
      </div>
      <ul className="divide-y divide-white/[0.06] rounded border border-white/[0.06]">
        {agents.map((a) => (
          <li key={a.id || a.agent_id}>
            <button
              type="button"
              onClick={() => onPick(a.id || a.agent_id)}
              className="w-full flex items-center justify-between gap-3 px-3 py-2 text-left hover:bg-white/[0.03]"
            >
              <div className="flex items-center gap-2 min-w-0">
                <Bot size={13} className="text-neutral-500 shrink-0" aria-hidden="true" />
                <span className="text-sm text-white truncate">{a.name || a.agent_id || a.id}</span>
              </div>
              <span className="text-[10px] text-neutral-600 font-mono">
                {(a.id || a.agent_id || '').slice(0, 8)}…
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
