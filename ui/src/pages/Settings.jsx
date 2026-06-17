import React, { Suspense, lazy, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Calendar,
  Code2,
  Database,
  DollarSign,
  Gauge,
  Key,
  MessagesSquare,
  Settings as SettingsIcon,
  ShieldCheck,
  Users,
  Webhook,
} from 'lucide-react';
import SystemValuesTab from '../components/settings/SystemValuesTab';
import SlackApprovalsTab from '../components/settings/SlackApprovalsTab';
import PolicyPacksTab from '../components/settings/PolicyPacksTab';
import TabErrorBoundary from '../components/Common/TabErrorBoundary';

// Existing pages, lazy-imported so each tab only pulls its chunk on
// activation. Each tab's underlying page is still reachable at its
// legacy URL for analyst bookmarks.
const UserManagement   = lazy(() => import('./UserManagement'));
const RBAC             = lazy(() => import('./RBAC'));
const SsoSettings      = lazy(() => import('./SsoSettings'));
const DeveloperPanel   = lazy(() => import('./DeveloperPanel'));
const WebhookSettings  = lazy(() => import('./WebhookSettings'));
const SiemSettings     = lazy(() => import('./SiemSettings'));
const ScheduledReports = lazy(() => import('./ScheduledReports'));
const QuotaManagement  = lazy(() => import('./QuotaManagement'));

// Visual grouping only — the `?tab=<id>` URL contract is unchanged.
const GROUP = {
  IDENTITY:     'identity',
  INTEGRATIONS: 'integrations',
  WORKSPACE:    'workspace',
};
const GROUP_LABELS = {
  [GROUP.IDENTITY]:     'Access & Identity',
  [GROUP.INTEGRATIONS]: 'Integrations',
  [GROUP.WORKSPACE]:    'Workspace',
};
const GROUP_ORDER = [GROUP.IDENTITY, GROUP.INTEGRATIONS, GROUP.WORKSPACE];

const TABS = [
  { id: 'team',          label: 'Team',            icon: Users,          Component: UserManagement,   group: GROUP.IDENTITY },
  { id: 'roles',         label: 'Roles',           icon: SettingsIcon,   Component: RBAC,             group: GROUP.IDENTITY },
  { id: 'sso',           label: 'SSO',             icon: Key,            Component: SsoSettings,      group: GROUP.IDENTITY },
  { id: 'api-keys',      label: 'API Keys',        icon: Code2,          Component: DeveloperPanel,   group: GROUP.IDENTITY },
  { id: 'siem',          label: 'SIEM',            icon: Database,       Component: SiemSettings,     group: GROUP.INTEGRATIONS },
  { id: 'webhooks',      label: 'Webhooks',        icon: Webhook,        Component: WebhookSettings,  group: GROUP.INTEGRATIONS },
  { id: 'slack',         label: 'Slack approvals', icon: MessagesSquare, Component: SlackApprovalsTab, group: GROUP.INTEGRATIONS },
  { id: 'system-values', label: 'System Values',   icon: DollarSign,     Component: SystemValuesTab,  group: GROUP.WORKSPACE },
  { id: 'policy-packs',  label: 'Policy packs',    icon: ShieldCheck,    Component: PolicyPacksTab,   group: GROUP.WORKSPACE },
  { id: 'quota',         label: 'Quota',           icon: Gauge,          Component: QuotaManagement,  group: GROUP.WORKSPACE },
  { id: 'reports',       label: 'Reports',         icon: Calendar,       Component: ScheduledReports, group: GROUP.WORKSPACE },
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

  const renderTabButton = ({ id, label, icon: Icon }) => {
    const isActive = id === activeTab;
    return (
      <button
        key={id}
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
  };

  const sections = GROUP_ORDER.map((g) => ({
    key: g,
    label: GROUP_LABELS[g],
    tabs: TABS.filter((t) => t.group === g),
  }));

  return (
    <div className="space-y-5">
      <div className="space-y-1">
        <h1 className="text-2xl font-bold tracking-tight text-white">Settings</h1>
        <p className="text-xs text-neutral-400">
          Access &amp; Identity · Integrations · Workspace
        </p>
      </div>

      <div
        className="flex flex-wrap items-end gap-x-4 gap-y-2 pb-1 border-b border-white/[0.06]"
        role="tablist"
        aria-label="Settings sections"
      >
        {sections.map(({ key, label, tabs }) => (
          <div key={key} className="flex flex-col gap-0.5">
            <div className="text-[10px] uppercase tracking-wider text-neutral-500 px-1">
              {label}
            </div>
            <div className="flex gap-1" role="group" aria-label={label}>
              {tabs.map(renderTabButton)}
            </div>
          </div>
        ))}
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
