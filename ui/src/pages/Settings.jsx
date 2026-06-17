import React, { Suspense, lazy, useEffect, useMemo, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import {
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

const TABS = [
  { id: 'system-values', label: 'System Values', icon: DollarSign,   Component: SystemValuesTab, legacy: '/settings?tab=system-values' },
  { id: 'team',          label: 'Team',          icon: Users,        Component: UserManagement,  legacy: '/users' },
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
          System Values · Team · Roles · SSO · API Keys · Webhooks · SIEM · Reports · Quota
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
