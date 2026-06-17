import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { BrowserRouter, Routes, Route, Navigate, useNavigate } from 'react-router-dom';
import { AuthContext } from './context/AuthContext';
import { AgentProvider } from './context/AgentContext';
import ProtectedRoute from './components/Layout/ProtectedRoute';
import ErrorBoundary from './components/Common/ErrorBoundary';
import IncidentOverlay from './components/Common/IncidentOverlay';
import KeyboardCheatsheet from './components/Common/KeyboardCheatsheet';
import CommandPalette from './components/Common/CommandPalette';
import { useHotkeys } from './hooks/useHotkeys';
import { onAuthFailure } from './lib/authEvents';
import { clearSessionMetadata } from './services/api';

import Login from './pages/Login';
import Signup from './pages/Signup';
import OnboardingWizard from './pages/OnboardingWizard';
import ShadowModeReview from './pages/ShadowModeReview';
import Dashboard from './pages/Dashboard';
import Policies from './pages/Policies';
import AgentSnapshot from './pages/AgentSnapshot';
import ThreatGraph from './pages/ThreatGraph';
import ClerkAuthBridge from './components/Layout/ClerkAuthBridge';
import Settings from './pages/Settings';
import Agents from './pages/Agents';
import KillSwitch from './pages/KillSwitch';
import Forensics from './pages/Forensics';
import AuditLogs from './pages/AuditLogs';
import Billing from './pages/Billing';
import SecurityDashboard from './pages/SecurityDashboard';
import RiskEngine from './pages/RiskEngine';
import AgentPlayground from './pages/AgentPlayground';
import DeveloperPanel from './pages/DeveloperPanel';
import Observability from './pages/Observability';
import SystemHealth from './pages/SystemHealth';
import IdentityGraph from './pages/IdentityGraph';
import FlightRecorder from './pages/FlightRecorder';
import AutonomyContracts from './pages/AutonomyContracts';
import PolicyBuilder from './pages/PolicyBuilder';
import RBAC from './pages/RBAC';
import Incidents from './pages/Incidents';
import AttackSimulation from './pages/AttackSimulation';
import AutoResponse from './pages/AutoResponse';
import Compliance from './pages/Compliance';
import WebhookSettings from './pages/WebhookSettings';
import AdminConsole from './pages/AdminConsole';
import SiemSettings from './pages/SiemSettings';
import PolicyAnalytics from './pages/PolicyAnalytics';
import ScheduledReports from './pages/ScheduledReports';
import ThreatIntel from './pages/ThreatIntel';
import QuotaManagement from './pages/QuotaManagement';
import SsoSettings from './pages/SsoSettings';
import Notifications from './pages/Notifications';
import LiveFeed from './pages/LiveFeed';
import PolicySim from './pages/PolicySim';
import UserManagement from './pages/UserManagement';
import Playbooks from './pages/Playbooks';
// Sprint 3 — Decision Explorer + Session Explorer
import DecisionExplorer from './pages/DecisionExplorer';
import SessionExplorer from './pages/SessionExplorer';
// Sprint 4 — Fleet dashboards + Agent FinOps + Topology
import Fleet from './pages/Fleet';
// Sprint 5 — Attack Evaluation Suite
import Evaluation from './pages/Evaluation';
// Sprint 6 — Shadow-mode policies + online evaluation
import ShadowMode from './pages/ShadowMode';
// Sprint 7 — Policy Playground
import PolicyPlayground from './pages/PolicyPlayground';
// Days 70-90 — Approval Inbox (operator surface for ESCALATE actions)
import ApprovalInbox from './pages/ApprovalInbox';
import Toast from './components/Common/Toast';

// Auth state is based on session metadata (tenant_id + expiry), not the token itself.
// The JWT lives exclusively in the httpOnly cookie.
const readSessionState = () => {
  const tenantId = localStorage.getItem('tenant_id');
  const expiry   = parseInt(localStorage.getItem('acp_token_expiry') || '0', 10);
  const isValid  = !!tenantId && expiry > Date.now();
  return {
    isAuthenticated: isValid,
    user:            localStorage.getItem('user_email'),
    tenant_id:       isValid ? tenantId : null,
    role:            isValid ? (localStorage.getItem('user_role') || null) : null,
    token:           null,
  };
};

// Inner component — needs access to useNavigate (must be inside BrowserRouter)
function AuthEventHandler({ onIncident }) {
  const navigate = useNavigate()

  useEffect(() => {
    const unsub = onAuthFailure((e) => {
      // Surface the SOC-grade incident overlay before resetting state
      onIncident(e.detail)
    })
    return unsub
  }, [onIncident])

  return null
}

// Global keyboard navigation — Linear-style. Lives inside <BrowserRouter> so
// it can call `navigate()`. Bindings deliberately use `g <letter>` sequences
// to avoid conflicting with browser shortcuts and form input.
function GlobalShortcuts({ onShowHelp, onShowPalette }) {
  const navigate = useNavigate()
  const bindings = useMemo(() => ([
    { key: 'g f', handler: () => navigate('/flight-recorder') },
    { key: 'g p', handler: () => navigate('/policy-builder')  },
    { key: 'g a', handler: () => navigate('/audit-logs')      },
    { key: 'g i', handler: () => navigate('/incidents')       },
    { key: 'g s', handler: () => navigate('/settings')        },
    { key: 'g g', handler: () => navigate('/identity-graph')  },
    { key: 'g o', handler: () => navigate('/observability')   },
    { key: 'g h', handler: () => navigate('/system-health')   },
    { key: 'g d', handler: () => navigate('/developer')       },
    { key: 'g l', handler: () => navigate('/live-feed')       },
    { key: 'mod+k', handler: onShowPalette },
    { key: '?',   handler: onShowHelp },
  ]), [navigate, onShowHelp, onShowPalette])
  useHotkeys(bindings)
  return null
}

const HOTKEY_GROUPS = [
  {
    label: 'Navigate',
    items: [
      { key: 'g f', desc: 'Flight Recorder' },
      { key: 'g p', desc: 'Policies' },
      { key: 'g a', desc: 'Audit logs' },
      { key: 'g i', desc: 'Incidents' },
      { key: 'g g', desc: 'Identity graph' },
      { key: 'g o', desc: 'Observability' },
      { key: 'g h', desc: 'System health' },
      { key: 'g s', desc: 'Settings' },
      { key: 'g d', desc: 'Developer panel' },
      { key: 'g l', desc: 'Live event feed' },
    ],
  },
  {
    label: 'Actions',
    items: [
      { key: 'mod+k', desc: 'Open command palette' },
      { key: '?',     desc: 'Show this cheatsheet' },
      { key: 'esc',   desc: 'Close modal / dismiss' },
    ],
  },
]

function App() {
  const [auth,        setAuth]        = useState(readSessionState);
  const [toasts,      setToasts]      = useState([]);
  const [incident,    setIncident]    = useState(null);
  const [helpOpen,    setHelpOpen]    = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);

  const updateAuth = (newAuth) => setAuth((prev) => ({ ...prev, ...newAuth }));

  const addToast = (message, type = 'info') => {
    const id = Date.now();
    setToasts((prev) => [...prev, { id, message, type }]);
    setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 5000);
  };

  const removeToast = (id) => setToasts((prev) => prev.filter((t) => t.id !== id));

  // Called by AuthEventHandler when auth:failure fires
  const handleIncident = useCallback((detail) => {
    clearSessionMetadata();
    setAuth({ isAuthenticated: false, user: null, tenant_id: null, token: null });
    setIncident(detail);
  }, []);

  // Dismiss overlay → navigate to login
  const handleIncidentDismiss = useCallback(() => {
    setIncident(null);
    // Navigate after state clears — the Routes below will redirect to /login automatically
    // since isAuthenticated is now false, but we also push imperatively just in case
    if (window.location.pathname !== '/login') {
      window.location.href = '/login';
    }
  }, []);

  // Multi-tab sync (other tabs calling logout / expiry)
  useEffect(() => {
    const handleStorage = () => setAuth(readSessionState());
    window.addEventListener('storage', handleStorage);
    return () => window.removeEventListener('storage', handleStorage);
  }, []);

  // Proactive client-side expiry timer
  useEffect(() => {
    const expiry    = parseInt(localStorage.getItem('acp_token_expiry') || '0', 10);
    const remaining = expiry - Date.now();
    if (remaining <= 0) return;
    const timer = setTimeout(() => {
      clearSessionMetadata();
      setAuth({ isAuthenticated: false, user: null, tenant_id: null, token: null });
      setIncident({
        incidentId:  crypto.randomUUID(),
        reason:      'session_expired',
        reasonLabel: 'Session Expired',
        url:         window.location.pathname,
        statusCode:  null,
        timestamp:   new Date().toISOString(),
      });
    }, remaining);
    return () => clearTimeout(timer);
  }, [auth.isAuthenticated]);

  return (
    <ErrorBoundary>
      <AuthContext.Provider value={{ ...auth, updateAuth, addToast }}>
        <AgentProvider>
          <BrowserRouter>
            {/* Wires auth event bus → incident overlay (needs Router context for useNavigate) */}
            <AuthEventHandler onIncident={handleIncident} />
            {auth.isAuthenticated && (
              <GlobalShortcuts
                onShowHelp={() => setHelpOpen(true)}
                onShowPalette={() => setPaletteOpen(true)}
              />
            )}

            {/* Mirrors Clerk session → legacy AuthContext + localStorage so the
                existing ProtectedRoute / API client keep working without a
                Clerk-specific rewrite of every consumer. */}
            <ClerkAuthBridge />

            <Routes>
              {/* Clerk's <SignIn /> / <SignUp /> components own sub-routes
                  (e.g. /signup/verify-email-address) — the `/*` is required. */}
              <Route path="/login/*"  element={auth.isAuthenticated ? <Navigate to="/dashboard" /> : <Login />} />
              <Route path="/signup/*" element={auth.isAuthenticated ? <Navigate to="/dashboard" /> : <Signup />} />
              <Route path="/onboarding" element={<ProtectedRoute><OnboardingWizard /></ProtectedRoute>} />
              <Route path="/shadow-review" element={<ProtectedRoute><ShadowModeReview /></ProtectedRoute>} />
              <Route path="/threat-graph"  element={<ProtectedRoute><ThreatGraph /></ProtectedRoute>} />

              {/* Sprint 4 — Dashboard is the landing page; FlightRecorder
                  moves to /audit-feed for analysts. */}
              <Route path="/"          element={<Navigate to="/dashboard" />} />
              <Route path="/dashboard" element={<ProtectedRoute><Dashboard /></ProtectedRoute>} />
              <Route path="/audit-feed" element={<ProtectedRoute><FlightRecorder /></ProtectedRoute>} />

              {/* Primary nav (5) */}
              <Route path="/flight-recorder" element={<ProtectedRoute><FlightRecorder /></ProtectedRoute>} />
              {/* Sprint 3 — Decision Explorer + Session Explorer */}
              <Route path="/decision-explorer" element={<ProtectedRoute><DecisionExplorer /></ProtectedRoute>} />
              <Route path="/session-explorer"  element={<ProtectedRoute><SessionExplorer /></ProtectedRoute>} />
              {/* Fleet dashboard (Sprint 4-era; kept for analyst use). */}
              <Route path="/fleet"             element={<ProtectedRoute><Fleet /></ProtectedRoute>} />
              {/* Attack Evaluation Suite */}
              <Route path="/evaluation"        element={<ProtectedRoute><Evaluation /></ProtectedRoute>} />
              {/* Legacy shadow-mode analytics page (Sprint 3 review surface
                  lives at /shadow-review). */}
              <Route path="/shadow-mode"       element={<ProtectedRoute><ShadowMode /></ProtectedRoute>} />
              {/* Approval Inbox — operator surface for ESCALATE actions */}
              <Route path="/approval-inbox"    element={<ProtectedRoute><ApprovalInbox /></ProtectedRoute>} />
              {/* Sprint 6 — Demo-only pages deleted; redirect to the
                  Phase-2 onboarding flow so external links don't 404. */}
              <Route path="/live-demo" element={<Navigate to="/onboarding" replace />} />

              {/* Sprint 6 — Policies tab router replaces the 5 individual
                  policy pages. Legacy paths redirect with ?tab=… so
                  analyst bookmarks keep working. */}
              <Route path="/policies"         element={<ProtectedRoute><Policies /></ProtectedRoute>} />
              <Route path="/policy-builder"   element={<Navigate to="/policies?tab=editor"     replace />} />
              <Route path="/policy-sim"       element={<Navigate to="/policies?tab=simulator"  replace />} />
              <Route path="/policy-playground" element={<Navigate to="/policies?tab=staging"    replace />} />
              <Route path="/policy-analytics" element={<Navigate to="/policies?tab=analytics"  replace />} />
              <Route path="/autonomy"         element={<Navigate to="/policies?tab=autonomy"   replace />} />

              {/* Sprint 6 — AgentSnapshot replaces 4 per-agent pages. */}
              <Route path="/agents/:id"           element={<ProtectedRoute><AgentSnapshot /></ProtectedRoute>} />
              <Route path="/agents/:id/profile"   element={<Navigate to="/agents/:id?tab=overview" replace />} />
              <Route path="/agent-profile/:id"    element={<ProtectedRoute><AgentSnapshot /></ProtectedRoute>} />
              <Route path="/agent-health"         element={<ProtectedRoute><AgentSnapshot /></ProtectedRoute>} />
              <Route path="/agent-cost"           element={<ProtectedRoute><AgentSnapshot /></ProtectedRoute>} />
              <Route path="/agent-topology"       element={<ProtectedRoute><AgentSnapshot /></ProtectedRoute>} />

              <Route path="/audit-logs"      element={<ProtectedRoute><AuditLogs /></ProtectedRoute>} />
              <Route path="/incidents"       element={<ProtectedRoute><Incidents /></ProtectedRoute>} />
              <Route path="/settings"        element={<ProtectedRoute><Settings /></ProtectedRoute>} />

              {/* Operations (secondary nav, collapsed by default) */}
              <Route path="/agents"          element={<ProtectedRoute><Agents /></ProtectedRoute>} />
              <Route path="/identity-graph"  element={<ProtectedRoute><IdentityGraph /></ProtectedRoute>} />
              <Route path="/forensics"       element={<ProtectedRoute><Forensics /></ProtectedRoute>} />
              <Route path="/playground"      element={<ProtectedRoute><AgentPlayground /></ProtectedRoute>} />
              <Route path="/auto-response"   element={<ProtectedRoute><AutoResponse /></ProtectedRoute>} />
              <Route path="/compliance"      element={<ProtectedRoute><Compliance /></ProtectedRoute>} />
              {/* Sprint 6 — Pricing/marketing pages out of the authenticated
                  app per PRODUCT_PLAN §12.3. External links land on dashboard. */}
              <Route path="/open-source" element={<Navigate to="/dashboard" replace />} />
              <Route path="/pricing"     element={<Navigate to="/dashboard" replace />} />
              <Route path="/attack-sim"      element={<ProtectedRoute><AttackSimulation /></ProtectedRoute>} />
              <Route path="/kill-switch"     element={<ProtectedRoute><KillSwitch /></ProtectedRoute>} />

              {/* Admin / surfaced via Settings hub (hidden from sidebar) */}
              <Route path="/rbac"            element={<ProtectedRoute><RBAC /></ProtectedRoute>} />
              <Route path="/security"        element={<ProtectedRoute><SecurityDashboard /></ProtectedRoute>} />
              <Route path="/system-health"   element={<ProtectedRoute><SystemHealth /></ProtectedRoute>} />
              <Route path="/observability"   element={<ProtectedRoute><Observability /></ProtectedRoute>} />
              <Route path="/developer"       element={<ProtectedRoute><DeveloperPanel /></ProtectedRoute>} />
              <Route path="/billing"         element={<ProtectedRoute><Billing /></ProtectedRoute>} />
              <Route path="/risk"            element={<ProtectedRoute><RiskEngine /></ProtectedRoute>} />
              <Route path="/webhook-settings" element={<ProtectedRoute><WebhookSettings /></ProtectedRoute>} />
              <Route path="/admin"           element={<ProtectedRoute><AdminConsole /></ProtectedRoute>} />
              <Route path="/siem"            element={<ProtectedRoute><SiemSettings /></ProtectedRoute>} />
              <Route path="/scheduled-reports" element={<ProtectedRoute><ScheduledReports /></ProtectedRoute>} />
              <Route path="/threat-intel"     element={<ProtectedRoute><ThreatIntel /></ProtectedRoute>} />
              <Route path="/quota"            element={<ProtectedRoute><QuotaManagement /></ProtectedRoute>} />
              <Route path="/sso"              element={<ProtectedRoute><SsoSettings /></ProtectedRoute>} />
              <Route path="/notifications"    element={<ProtectedRoute><Notifications /></ProtectedRoute>} />
              <Route path="/live-feed"        element={<ProtectedRoute><LiveFeed /></ProtectedRoute>} />
              <Route path="/users"            element={<ProtectedRoute><UserManagement /></ProtectedRoute>} />
              <Route path="/playbooks"        element={<ProtectedRoute><Playbooks /></ProtectedRoute>} />

              {/* Sprint 6 — ExecutiveDashboard merged into /dashboard. */}
              <Route path="/executive-summary" element={<Navigate to="/dashboard" replace />} />
              <Route path="/executive"         element={<Navigate to="/dashboard" replace />} />

              <Route path="*" element={<Navigate to="/dashboard" />} />
            </Routes>

            {/* Command palette — inside BrowserRouter so useNavigate() has context */}
            <CommandPalette isOpen={paletteOpen} onClose={() => setPaletteOpen(false)} />
          </BrowserRouter>
        </AgentProvider>

        {/* Toast stack — z-[80] sits above modals (z-50/z-[60]) so confirmations
            from a dialog action are still visible after the dialog closes. */}
        <div
          aria-live="polite"
          aria-atomic="true"
          className="fixed bottom-4 right-4 z-[80] flex flex-col gap-2 pointer-events-none max-w-[calc(100vw-2rem)]"
        >
          {toasts.map((toast) => (
            <div key={toast.id} className="pointer-events-auto">
              <Toast message={toast.message} type={toast.type} onClose={() => removeToast(toast.id)} />
            </div>
          ))}
        </div>

        {/* SOC Incident Overlay — renders above everything including ErrorBoundary siblings */}
        <IncidentOverlay incident={incident} onDismiss={handleIncidentDismiss} />

        {/* Keyboard cheatsheet — triggered by `?` */}
        <KeyboardCheatsheet
          isOpen={helpOpen}
          onClose={() => setHelpOpen(false)}
          groups={HOTKEY_GROUPS}
        />
      </AuthContext.Provider>
    </ErrorBoundary>
  );
}

export default App;
