import React, { Suspense, lazy, useMemo } from 'react';
import { useSearchParams, useParams, Link } from 'react-router-dom';
import {
  ArrowLeft,
  DollarSign,
  HeartPulse,
  Network,
  Share2,
  User,
} from 'lucide-react';
import TabErrorBoundary from '../components/Common/TabErrorBoundary';

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
export default function AgentSnapshot() {
  const { id } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();

  const activeTab = useMemo(() => {
    const param = searchParams.get('tab');
    return param && VALID_TAB_IDS.has(param) ? param : DEFAULT_TAB_ID;
  }, [searchParams]);

  const handleTabClick = (tabId) => {
    setSearchParams({ tab: tabId }, { replace: true });
  };

  const ActiveComponent = useMemo(() => {
    const tab = TABS.find((t) => t.id === activeTab) || TABS[0];
    return tab.Component;
  }, [activeTab]);

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
            Agent <span className="font-mono text-base text-neutral-400">{(id || '').slice(0, 12)}…</span>
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
          <ActiveComponent />
        </Suspense>
      </TabErrorBoundary>
    </div>
  );
}
