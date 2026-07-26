import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup } from '@testing-library/react';
import { renderWithProviders } from '../test-utils';

// Global module mocks — every page reads services/api + hooks/useSSE at
// minimum. Real implementations blow up under jsdom (SSE opens EventSource).
vi.mock('../services/api',        () => import('../__mocks__/apiMock'));
vi.mock('../services/auth',       () => ({
  attachAuth:            async (h) => h,
  hasSession:            () => false,
  getAccessToken:        () => null,
  login:                 async () => ({}),
  register:              async () => ({}),
  requestPasswordReset:  async () => ({}),
  confirmPasswordReset:  async () => ({}),
  changePassword:        async () => ({}),
  logout:                async () => {},
  clearSession:          () => {},
}));
vi.mock('../hooks/useSSE',        () => ({ useSSE: () => ({ connected: false }) }));
vi.mock('../lib/eventBus',        () => ({ eventBus: { on: () => () => {}, emit: () => {} } }));

// EventSource + IntersectionObserver aren't in jsdom; a few pages construct
// them at mount and would throw. Stub with no-ops.
if (typeof window !== 'undefined') {
  if (!window.EventSource) {
    window.EventSource = class { constructor() {} close() {} addEventListener() {} };
  }
  if (!window.IntersectionObserver) {
    window.IntersectionObserver = class {
      constructor() {}
      observe() {} unobserve() {} disconnect() {}
    };
  }
  if (!window.ResizeObserver) {
    window.ResizeObserver = class {
      constructor() {}
      observe() {} unobserve() {} disconnect() {}
    };
  }
}

// Pages that render without needing route params. Each import is dynamic so
// one page's throw doesn't take down the whole suite.
const PAGES = [
  ['Dashboard',        () => import('./Dashboard')],
  ['Agents',           () => import('./Agents')],
  ['AuditLogs',        () => import('./AuditLogs')],
  ['Incidents',        () => import('./Incidents')],
  ['Compliance',       () => import('./Compliance')],
  ['Settings',         () => import('./Settings')],
  ['Landing',          () => import('./Landing')],
  ['Login',            () => import('./Login')],
  ['Signup',           () => import('./Signup')],
  ['StatusPage',       () => import('./StatusPage')],
  ['SecurityPage',     () => import('./SecurityPage')],
  ['TrustCenter',      () => import('./TrustCenter')],
  ['Notifications',    () => import('./Notifications')],
  ['ApprovalInbox',    () => import('./ApprovalInbox')],
  ['KillSwitch',       () => import('./KillSwitch')],
  ['Fleet',            () => import('./Fleet')],
  ['Playbooks',        () => import('./Playbooks')],
  ['ThreatIntel',      () => import('./ThreatIntel')],
  ['Team',             () => import('./Team')],
  ['UserManagement',   () => import('./UserManagement')],
  ['RBAC',             () => import('./RBAC')],
  ['Policies',         () => import('./Policies')],
  ['ShadowMode',       () => import('./ShadowMode')],
  ['ShadowModeReview', () => import('./ShadowModeReview')],
  ['SystemHealth',     () => import('./SystemHealth')],
  ['DeveloperPanel',   () => import('./DeveloperPanel')],
  ['Forensics',        () => import('./Forensics')],
  ['LiveFeed',         () => import('./LiveFeed')],
  ['SsoSettings',      () => import('./SsoSettings')],
  ['ThreatGraph',      () => import('./ThreatGraph')],
  ['IdentityGraph',    () => import('./IdentityGraph')],
  ['AgentPlayground',  () => import('./AgentPlayground')],
  ['FlightRecorder',   () => import('./FlightRecorder')],
  ['Evaluation',       () => import('./Evaluation')],
  ['DecisionExplorer', () => import('./DecisionExplorer')],
  ['SessionExplorer',  () => import('./SessionExplorer')],
  ['PolicyBuilder',    () => import('./PolicyBuilder')],
  ['PolicySim',        () => import('./PolicySim')],
  ['PolicyPlayground', () => import('./PolicyPlayground')],
  ['PolicyAnalytics',  () => import('./PolicyAnalytics')],
  ['AutonomyContracts',() => import('./AutonomyContracts')],
  ['AutoResponse',     () => import('./AutoResponse')],
  ['ScheduledReports', () => import('./ScheduledReports')],
  ['SiemSettings',     () => import('./SiemSettings')],
  ['WebhookSettings',  () => import('./WebhookSettings')],
  ['QuotaManagement',  () => import('./QuotaManagement')],
  ['AdminConsole',     () => import('./AdminConsole')],
  ['LifecycleAdmin',   () => import('./LifecycleAdmin')],
  ['OnboardingWizard', () => import('./OnboardingWizard')],
  ['Team-employee',    () => import('./EmployeeProfile')],
  ['AgentCost',        () => import('./AgentCost')],
  ['AgentHealth',      () => import('./AgentHealth')],
  ['AgentProfile',     () => import('./AgentProfile')],
  ['AgentSnapshot',    () => import('./AgentSnapshot')],
  ['AgentTopology',    () => import('./AgentTopology')],
  ['TeamSettings',     () => import('./TeamSettings')],
  ['Replay',           () => import('./Replay')],
];

describe('page smoke tests', () => {
  afterEach(() => cleanup());
  for (const [name, load] of PAGES) {
    it(`${name} module resolves`, async () => {
      const mod = await load();
      expect(typeof mod.default).toBe('function');
    });
  }

  it('Dashboard renders without throwing (with providers + mocks)', async () => {
    const { default: Dashboard } = await import('./Dashboard');
    const { container } = renderWithProviders(<Dashboard />, { route: '/dashboard' });
    expect(container).toBeTruthy();
  });

  it('Landing renders without throwing', async () => {
    const { default: Landing } = await import('./Landing');
    const { container } = renderWithProviders(<Landing />, { route: '/' });
    expect(container).toBeTruthy();
  });

  it('StatusPage renders without throwing', async () => {
    const { default: StatusPage } = await import('./StatusPage');
    const { container } = renderWithProviders(<StatusPage />, { route: '/status' });
    expect(container).toBeTruthy();
  });

  it('KillSwitch renders without throwing', async () => {
    const { default: KillSwitch } = await import('./KillSwitch');
    const { container } = renderWithProviders(<KillSwitch />, { route: '/kill-switch' });
    expect(container).toBeTruthy();
  });

  it('Settings renders without throwing', async () => {
    const { default: Settings } = await import('./Settings');
    const { container } = renderWithProviders(<Settings />, { route: '/settings' });
    expect(container).toBeTruthy();
  });
});
