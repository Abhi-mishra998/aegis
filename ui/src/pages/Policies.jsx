import React, { Suspense, lazy, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Activity,
  GitMerge,
  PlayCircle,
  Sparkles,
  Workflow,
} from 'lucide-react';
import TabErrorBoundary from '../components/Common/TabErrorBoundary';

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
const DEFAULT_TAB_ID = TABS[0].id;
const VALID_TAB_IDS = new Set(TABS.map((t) => t.id));

/**
 * Sprint 6 — Policies tab router.
 *
 * Lazy-loads each existing page so the bundle for `/policies` is
 * dominated only by the active tab + the shared chrome. URL state is
 * `?tab=editor|simulator|staging|analytics|autonomy` so refreshes +
 * deep links Just Work.
 *
 * Uses React Router 6's `useSearchParams` as the SINGLE source of truth
 * for the active tab. An earlier implementation kept a useState mirror
 * + two useEffects that pushed activeTab back into the URL via
 * `navigate('?tab=…')`; unrelated background re-renders (Topbar poll,
 * Sidebar poll, ClerkAuthBridge refresh) could land between the
 * setState and the navigate, causing the child tab to unmount in the
 * middle of its initial render — the user perceived this as "tabs
 * blink one time, no content shows."
 *
 * The 5 legacy routes (/policy-builder etc.) keep working — App.jsx
 * redirects them here with the matching ?tab=… so analyst bookmarks
 * don't 404.
 */
export default function Policies() {
  const [searchParams, setSearchParams] = useSearchParams();

  const activeTab = useMemo(() => {
    const param = searchParams.get('tab');
    return param && VALID_TAB_IDS.has(param) ? param : DEFAULT_TAB_ID;
  }, [searchParams]);

  const handleTabClick = (id) => {
    setSearchParams({ tab: id }, { replace: true });
  };

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

      <div
        className="flex gap-1 overflow-x-auto pb-1 border-b border-white/[0.06] -mx-1 px-1 scrollbar-thin"
        role="tablist"
        aria-label="Policy tabs"
      >
        {TABS.map(({ id, label, icon: Icon }) => {
          const isActive = id === activeTab;
          return (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={isActive}
              onClick={() => handleTabClick(id)}
              className={
                'flex items-center gap-1.5 px-3 h-9 rounded-t-md text-xs font-medium transition-all whitespace-nowrap shrink-0 ' +
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
            <div className="space-y-3 py-2" role="status" aria-label={`Loading ${activeTab}`}>
              <div className="h-3 bg-white/[0.06] rounded w-1/3 animate-pulse" />
              <div className="h-32 bg-white/[0.03] border border-white/[0.04] rounded-xl animate-pulse" />
              <div className="h-32 bg-white/[0.03] border border-white/[0.04] rounded-xl animate-pulse" />
            </div>
          }
        >
          <ActiveComponent />
        </Suspense>
      </TabErrorBoundary>
    </div>
  );
}
