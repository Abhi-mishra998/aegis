import React, { Suspense, lazy, useEffect, useMemo, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import {
  Activity,
  GitMerge,
  PlayCircle,
  Sparkles,
  Workflow,
} from 'lucide-react';

const PolicyBuilder      = lazy(() => import('./PolicyBuilder'));
const PolicySim          = lazy(() => import('./PolicySim'));
const PolicyPlayground   = lazy(() => import('./PolicyPlayground'));
const PolicyAnalytics    = lazy(() => import('./PolicyAnalytics'));
const AutonomyContracts  = lazy(() => import('./AutonomyContracts'));

const TABS = [
  { id: 'editor',     label: 'Editor',      icon: GitMerge,   Component: PolicyBuilder,     hint: 'Author + lint OPA Rego.' },
  { id: 'simulator',  label: 'Simulator',   icon: PlayCircle, Component: PolicySim,         hint: 'Replay a request through draft + prod policies side-by-side.' },
  { id: 'staging',    label: 'Staging',     icon: Sparkles,   Component: PolicyPlayground,  hint: 'Test a draft against the last 24 h of real traffic.' },
  { id: 'analytics',  label: 'Analytics',   icon: Activity,   Component: PolicyAnalytics,   hint: 'Hit-rate, latency, deny-leaderboard per policy.' },
  { id: 'autonomy',   label: 'Autonomy',    icon: Workflow,   Component: AutonomyContracts, hint: 'Bounded-autonomy contracts that the gateway enforces.' },
];

/**
 * Sprint 6 — Policies tab router.
 *
 * Lazy-loads each existing page so the bundle for `/policies` is
 * dominated only by the active tab + the shared chrome. URL state is
 * `?tab=editor|simulator|staging|analytics|autonomy` so refreshes +
 * deep links Just Work.
 *
 * The 5 legacy routes (/policy-builder etc.) keep working — App.jsx
 * redirects them here with the matching ?tab=… so analyst bookmarks
 * don't 404.
 */
export default function Policies() {
  const { search } = useLocation();
  const navigate = useNavigate();

  const initialTab = useMemo(() => {
    const param = new URLSearchParams(search).get('tab');
    return TABS.some((t) => t.id === param) ? param : TABS[0].id;
  }, [search]);
  const [activeTab, setActiveTab] = useState(initialTab);

  // Keep URL in sync when the operator clicks a tab.
  useEffect(() => {
    const params = new URLSearchParams(search);
    if (params.get('tab') !== activeTab) {
      params.set('tab', activeTab);
      navigate(`?${params.toString()}`, { replace: true });
    }
  }, [activeTab, search, navigate]);

  // Mirror back when external nav changes the URL.
  useEffect(() => {
    const param = new URLSearchParams(search).get('tab');
    if (param && param !== activeTab && TABS.some((t) => t.id === param)) {
      setActiveTab(param);
    }
  }, [search, activeTab]);

  const ActiveComponent = useMemo(() => {
    const tab = TABS.find((t) => t.id === activeTab) || TABS[0];
    return tab.Component;
  }, [activeTab]);
  const activeHint = TABS.find((t) => t.id === activeTab)?.hint;

  return (
    <div className="space-y-5">
      <div className="space-y-1">
        <h1 className="text-2xl font-bold tracking-tight text-white">Policies</h1>
        <p className="text-xs text-neutral-400">{activeHint}</p>
      </div>

      <div className="flex gap-1 overflow-x-auto pb-1 border-b border-white/[0.06]" role="tablist">
        {TABS.map(({ id, label, icon: Icon }) => {
          const isActive = id === activeTab;
          return (
            <button
              key={id}
              role="tab"
              aria-selected={isActive}
              onClick={() => setActiveTab(id)}
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

      <Suspense
        fallback={
          <div className="text-xs text-neutral-500 py-8 text-center">Loading {activeTab}…</div>
        }
      >
        <ActiveComponent />
      </Suspense>
    </div>
  );
}
