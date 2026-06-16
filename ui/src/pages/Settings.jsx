import React, { Suspense, lazy, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  AlertTriangle,
  Building,
  Calendar,
  Code2,
  Database,
  DollarSign,
  Gauge,
  Key,
  Settings as SettingsIcon,
  Users,
  Webhook,
} from 'lucide-react';
import SystemValuesTab from '../components/settings/SystemValuesTab';

// Inline error boundary scoped to a single Settings tab. Without this,
// a render error in (say) WebhookSettings bubbles all the way to the
// root ErrorBoundary, which renders a full-screen red overlay — the
// user perceived the tab content as "blank" because the overlay covered
// it. Catching at the tab level lets the rest of /settings stay usable
// and surfaces a real error message instead of a silent blank.
class TabErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }
  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }
  componentDidCatch(error, errorInfo) {
    // eslint-disable-next-line no-console
    console.error('Settings tab render error', this.props.tabId, error, errorInfo);
  }
  componentDidUpdate(prevProps) {
    // Reset on tab change so the user can try a different one.
    if (prevProps.tabId !== this.props.tabId && this.state.hasError) {
      // eslint-disable-next-line react/no-direct-mutation-state
      this.setState({ hasError: false, error: null });
    }
  }
  render() {
    if (!this.state.hasError) return this.props.children;
    return (
      <div className="max-w-3xl mx-auto py-8">
        <div className="flex items-start gap-3 p-4 rounded-xl border border-red-500/20 bg-red-500/[0.05]">
          <AlertTriangle size={16} className="text-red-400 shrink-0 mt-0.5" aria-hidden="true" />
          <div className="space-y-1">
            <div className="text-sm font-semibold text-red-300">
              This tab failed to render.
            </div>
            <div className="text-xs text-red-300/80 font-mono break-words">
              {this.state.error?.message || 'Unknown render error'}
            </div>
            <div className="text-[11px] text-neutral-500 mt-2">
              Try a different tab from the bar above. If every tab fails the
              same way, refresh the page — your session may have expired.
            </div>
          </div>
        </div>
      </div>
    );
  }
}

// Existing pages, lazy-imported so /settings?tab=workspace only pulls
// the Workspace tab's chunk on initial render. Each tab's underlying
// page is still reachable at its legacy URL for analyst bookmarks.
const UserManagement   = lazy(() => import('./UserManagement'));
const RBAC             = lazy(() => import('./RBAC'));
const SsoSettings      = lazy(() => import('./SsoSettings'));
const DeveloperPanel   = lazy(() => import('./DeveloperPanel'));
const WebhookSettings  = lazy(() => import('./WebhookSettings'));
const SiemSettings     = lazy(() => import('./SiemSettings'));
const ScheduledReports = lazy(() => import('./ScheduledReports'));
const QuotaManagement  = lazy(() => import('./QuotaManagement'));

/**
 * Workspace tab — pure JSX, no API calls. Surfaces the same summary the
 * Dashboard uses and offers a deep-link to Shadow Review.
 */
function WorkspaceTab() {
  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-4 space-y-2">
        <div className="text-xs font-bold text-white">Workspace</div>
        <p className="text-[11px] text-neutral-400 leading-snug max-w-xl">
          Top-level workspace settings live here. Plan + residency + audit-chain
          configuration arrive in Phase 6. For now, jump to specific surfaces
          via the tabs above.
        </p>
        <div className="text-[10px] text-neutral-600 mt-1">
          Tip: the legacy <code>/settings</code> URL still serves this page; each
          sub-page is also reachable at its original path (e.g. <code>/users</code>,
          <code>/rbac</code>, <code>/sso</code>).
        </div>
      </div>
    </div>
  );
}

const TABS = [
  { id: 'workspace',     label: 'Workspace',     icon: Building,     Component: WorkspaceTab },
  { id: 'system-values', label: 'System Values', icon: DollarSign,   Component: SystemValuesTab },
  { id: 'team',          label: 'Team',          icon: Users,        Component: UserManagement },
  { id: 'roles',         label: 'Roles',         icon: SettingsIcon, Component: RBAC },
  { id: 'sso',           label: 'SSO',           icon: Key,          Component: SsoSettings },
  { id: 'api-keys',      label: 'API Keys',      icon: Code2,        Component: DeveloperPanel },
  { id: 'webhooks',      label: 'Webhooks',      icon: Webhook,      Component: WebhookSettings },
  { id: 'siem',          label: 'SIEM',          icon: Database,     Component: SiemSettings },
  { id: 'reports',       label: 'Reports',       icon: Calendar,     Component: ScheduledReports },
  { id: 'quota',         label: 'Quota',         icon: Gauge,        Component: QuotaManagement },
];
const DEFAULT_TAB_ID = TABS[0].id;
const VALID_TAB_IDS = new Set(TABS.map((t) => t.id));

export default function Settings() {
  // Use React Router 6's `useSearchParams` so the URL is the SOLE source of
  // truth for the active tab. Previously we kept a useState mirror + two
  // useEffects to push it back into the URL via `navigate('?tab=...')`. The
  // mirror created a tab-click → setState → re-render → navigate → re-render
  // sequence where, on some renders, an unrelated state churn caused the
  // child tab component to remount before the navigate landed — which the
  // user perceived as "tabs blink one time, no content shows."
  //
  // useSearchParams eliminates the mirror entirely: click handler sets the
  // URL, React Router re-renders with the new query, derived activeTab
  // matches, child component renders. One render path, no race.
  const [searchParams, setSearchParams] = useSearchParams();

  const activeTab = useMemo(() => {
    const param = searchParams.get('tab');
    return param && VALID_TAB_IDS.has(param) ? param : DEFAULT_TAB_ID;
  }, [searchParams]);

  const handleTabClick = (id) => {
    // replace: true keeps the back-button behavior tied to navigation INTO
    // /settings, not between tabs. Tab switches feel like in-place edits,
    // not new history entries.
    setSearchParams({ tab: id }, { replace: true });
  };

  const ActiveComponent = useMemo(() => {
    const tab = TABS.find((t) => t.id === activeTab) || TABS[0];
    return tab.Component;
  }, [activeTab]);

  return (
    <div className="space-y-5">
      <div className="space-y-1">
        <h1 className="text-2xl font-bold tracking-tight text-white">Settings</h1>
        <p className="text-xs text-neutral-400">
          Workspace · System Values · Team · Roles · SSO · API Keys · Webhooks · SIEM · Reports · Quota
        </p>
      </div>

      <div className="flex gap-1 overflow-x-auto pb-1 border-b border-white/[0.06]" role="tablist">
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
