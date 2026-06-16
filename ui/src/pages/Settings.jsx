import React, { Suspense, lazy, useEffect, useMemo, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import {
  Building,
  Calendar,
  Code2,
  Database,
  Gauge,
  Key,
  Settings as SettingsIcon,
  Users,
  Webhook,
} from 'lucide-react';

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

const TABS = [
  { id: 'workspace',  label: 'Workspace',   icon: Building,     Component: WorkspaceTab,    legacy: '/settings' },
  { id: 'team',       label: 'Team',        icon: Users,        Component: UserManagement,  legacy: '/users' },
  { id: 'roles',      label: 'Roles',       icon: SettingsIcon, Component: RBAC,            legacy: '/rbac' },
  { id: 'sso',        label: 'SSO',         icon: Key,          Component: SsoSettings,     legacy: '/sso' },
  { id: 'api-keys',   label: 'API Keys',    icon: Code2,        Component: DeveloperPanel,  legacy: '/developer' },
  { id: 'webhooks',   label: 'Webhooks',    icon: Webhook,      Component: WebhookSettings, legacy: '/webhook-settings' },
  { id: 'siem',       label: 'SIEM',        icon: Database,     Component: SiemSettings,    legacy: '/siem' },
  { id: 'reports',    label: 'Reports',     icon: Calendar,     Component: ScheduledReports, legacy: '/scheduled-reports' },
  { id: 'quota',      label: 'Quota',       icon: Gauge,        Component: QuotaManagement, legacy: '/quota' },
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

  return (
    <div className="space-y-5">
      <div className="space-y-1">
        <h1 className="text-2xl font-bold tracking-tight text-white">Settings</h1>
        <p className="text-xs text-neutral-400">
          Workspace · Team · Roles · SSO · API Keys · Webhooks · SIEM · Reports · Quota
        </p>
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
