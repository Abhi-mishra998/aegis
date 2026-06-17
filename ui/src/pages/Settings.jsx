import React, { Suspense, lazy, useEffect, useMemo, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import {
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
 * Sprint 6 — Workspace tab. New, lightweight; will grow as Phase 6
 * (Stripe + residency) lands. For now it surfaces the same workspace
 * summary the Dashboard uses, plus a deep-link to Shadow Review.
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
  { id: 'workspace',     label: 'Workspace',     icon: Building,     Component: WorkspaceTab,     legacy: '/settings',                   group: null },
  { id: 'team',          label: 'Team',          icon: Users,        Component: UserManagement,   legacy: '/users',                      group: GROUP.IDENTITY },
  { id: 'roles',         label: 'Roles',         icon: SettingsIcon, Component: RBAC,             legacy: '/rbac',                       group: GROUP.IDENTITY },
  { id: 'sso',           label: 'SSO',           icon: Key,          Component: SsoSettings,      legacy: '/sso',                        group: GROUP.IDENTITY },
  { id: 'api-keys',      label: 'API Keys',      icon: Code2,        Component: DeveloperPanel,   legacy: '/developer',                  group: GROUP.IDENTITY },
  { id: 'siem',          label: 'SIEM',          icon: Database,     Component: SiemSettings,     legacy: '/siem',                       group: GROUP.INTEGRATIONS },
  { id: 'webhooks',      label: 'Webhooks',      icon: Webhook,      Component: WebhookSettings,  legacy: '/webhook-settings',           group: GROUP.INTEGRATIONS },
  { id: 'system-values', label: 'System Values', icon: DollarSign,   Component: SystemValuesTab,  legacy: '/settings?tab=system-values', group: GROUP.WORKSPACE },
  { id: 'quota',         label: 'Quota',         icon: Gauge,        Component: QuotaManagement,  legacy: '/quota',                      group: GROUP.WORKSPACE },
  { id: 'reports',       label: 'Reports',       icon: Calendar,     Component: ScheduledReports, legacy: '/scheduled-reports',          group: GROUP.WORKSPACE },
];

export default function Settings() {
  const { search } = useLocation();
  const navigate = useNavigate();

  const initialTab = useMemo(() => {
    const param = new URLSearchParams(search).get('tab');
    return TABS.some((t) => t.id === param) ? param : TABS[0].id;
  }, [search]);
  const [activeTab, setActiveTab] = useState(initialTab);

  useEffect(() => {
    const params = new URLSearchParams(search);
    if (params.get('tab') !== activeTab) {
      params.set('tab', activeTab);
      navigate(`?${params.toString()}`, { replace: true });
    }
  }, [activeTab, search, navigate]);

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

  const renderTabButton = ({ id, label, icon: Icon }) => {
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
  };

  // Workspace tab renders first with an invisible label-row so it aligns
  // vertically with the labeled groups beside it.
  const sections = [
    { key: '__ungrouped', label: null,                tabs: TABS.filter((t) => !t.group) },
    ...GROUP_ORDER.map((g) => ({
      key: g,
      label: GROUP_LABELS[g],
      tabs: TABS.filter((t) => t.group === g),
    })),
  ];

  return (
    <div className="space-y-5">
      <div className="space-y-1">
        <h1 className="text-2xl font-bold tracking-tight text-white">Settings</h1>
        <p className="text-xs text-neutral-400">
          Workspace · Access &amp; Identity · Integrations · Workspace controls
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
              {label ?? ' '}
            </div>
            <div
              className="flex gap-1"
              role={label ? 'group' : undefined}
              aria-label={label || undefined}
            >
              {tabs.map(renderTabButton)}
            </div>
          </div>
        ))}
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
