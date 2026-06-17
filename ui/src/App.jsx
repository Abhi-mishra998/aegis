import React, { useState, useEffect, useCallback, useMemo, Suspense, lazy } from 'react';
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

// Critical-path imports — first paint cost.
// Dashboard is the Lighthouse LCP target so it stays eager; the auth-bridge
// must mount synchronously so ClerkAuthBridge can mirror the session into
// AuthContext before any ProtectedRoute decides to redirect to /login.
import Dashboard from './pages/Dashboard';
import ClerkAuthBridge from './components/Layout/ClerkAuthBridge';
import Toast from './components/Common/Toast';

// Lazy-loaded routes — split into their own chunks so the initial /dashboard
// load doesn't pay for FlightRecorder, IdentityGraph, Policies, etc.
// Order preserved from the original eager list for review.
const Login              = lazy(() => import('./pages/Login'));
const Signup             = lazy(() => import('./pages/Signup'));
const OnboardingWizard   = lazy(() => import('./pages/OnboardingWizard'));
const ShadowModeReview   = lazy(() => import('./pages/ShadowModeReview'));
const Policies           = lazy(() => import('./pages/Policies'));
const AgentSnapshot      = lazy(() => import('./pages/AgentSnapshot'));
const ThreatGraph        = lazy(() => import('./pages/ThreatGraph'));
const Settings           = lazy(() => import('./pages/Settings'));
const Agents             = lazy(() => import('./pages/Agents'));
const KillSwitch         = lazy(() => import('./pages/KillSwitch'));
const Forensics          = lazy(() => import('./pages/Forensics'));
const AuditLogs          = lazy(() => import('./pages/AuditLogs'));
const Billing            = lazy(() => import('./pages/Billing'));
const SecurityDashboard  = lazy(() => import('./pages/SecurityDashboard'));
const RiskEngine         = lazy(() => import('./pages/RiskEngine'));
const AgentPlayground    = lazy(() => import('./pages/AgentPlayground'));
const DeveloperPanel     = lazy(() => import('./pages/DeveloperPanel'));
const Observability      = lazy(() => import('./pages/Observability'));
const SystemHealth       = lazy(() => import('./pages/SystemHealth'));
const IdentityGraph      = lazy(() => import('./pages/IdentityGraph'));
const FlightRecorder     = lazy(() => import('./pages/FlightRecorder'));
const RBAC               = lazy(() => import('./pages/RBAC'));
const Incidents          = lazy(() => import('./pages/Incidents'));
const AttackSimulation   = lazy(() => import('./pages/AttackSimulation'));
const AutoResponse       = lazy(() => import('./pages/AutoResponse'));
const Compliance         = lazy(() => import('./pages/Compliance'));
const WebhookSettings    = lazy(() => import('./pages/WebhookSettings'));
const AdminConsole       = lazy(() => import('./pages/AdminConsole'));
const SiemSettings       = lazy(() => import('./pages/SiemSettings'));
const ScheduledReports   = lazy(() => import('./pages/ScheduledReports'));
const ThreatIntel        = lazy(() => import('./pages/ThreatIntel'));
const QuotaManagement    = lazy(() => import('./pages/QuotaManagement'));
const SsoSettings        = lazy(() => import('./pages/SsoSettings'));
const Notifications      = lazy(() => import('./pages/Notifications'));
const LiveFeed           = lazy(() => import('./pages/LiveFeed'));
const UserManagement     = lazy(() => import('./pages/UserManagement'));
const Playbooks          = lazy(() => import('./pages/Playbooks'));
const Team               = lazy(() => import('./pages/Team'));
const EmployeeProfile    = lazy(() => import('./pages/EmployeeProfile'));
const Replay             = lazy(() => import('./pages/Replay'));
const Landing            = lazy(() => import('./pages/Landing'));
const DecisionExplorer   = lazy(() => import('./pages/DecisionExplorer'));
const SessionExplorer    = lazy(() => import('./pages/SessionExplorer'));
const Fleet              = lazy(() => import('./pages/Fleet'));
const Evaluation         = lazy(() => import('./pages/Evaluation'));
const ShadowMode         = lazy(() => import('./pages/ShadowMode'));
const ApprovalInbox      = lazy(() => import('./pages/ApprovalInbox'));

// Lightweight fallback while the route chunk streams in.
const RouteFallback = () => (
  <div className="flex items-center justify-center min-h-[60vh]" role="status" aria-live="polite">
    <div className="h-8 w-8 rounded-full border-2 border-slate-700 border-t-slate-300 animate-spin" />
    <span className="sr-only">Loading…</span>
  </div>
);

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

  // BOTH callbacks MUST be stable — pages like Incidents.jsx put `addToast`
  // in a useCallback dep, so an unstable reference would re-build their
  // fetchAll on every render, re-arming setInterval + eventBus.on each
  // time. That's how a transient 5xx turns into hundreds of stacked
  // "Failed to load incidents" toasts within a few seconds.
  const updateAuth = useCallback(
    (newAuth) => setAuth((prev) => ({ ...prev, ...newAuth })),
    [],
  );

  const addToast = useCallback((message, type = 'info') => {
    // crypto.randomUUID() (or a fallback) avoids Date.now() collisions
    // when multiple toasts fire in the same millisecond — colliding IDs
    // make React drop one or both via the duplicate-key bail-out.
    const id = (typeof crypto !== 'undefined' && crypto.randomUUID)
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    setToasts((prev) => {
      // De-dupe identical error messages within the current window so a
      // poll loop hitting a transient 5xx doesn't paint 50 copies of the
      // same error. Identical-text toast resets the dismissal timer
      // rather than stacking.
      const sameText = prev.findIndex(
        (t) => t.message === message && t.type === type,
      );
      if (sameText >= 0) return prev;
      return [...prev, { id, message, type }];
    });
    setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 5000);
  }, []);

  const removeToast = useCallback(
    (id) => setToasts((prev) => prev.filter((t) => t.id !== id)),
    [],
  );

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

  // Proactive client-side expiry timer.
  //
  // Polls every 5s rather than arming a one-shot setTimeout. The reason:
  // ClerkAuthBridge runs a 45-second background refresh that extends
  // acp_token_expiry forward (Clerk's default JWT is 60s — the refresh
  // moves the expiry past the next refresh). A one-shot setTimeout
  // captured the original 60s expiry on mount and fired the
  // session_expired incident even though the cookie + JWT were freshly
  // refreshed underneath it, which made the dashboard "blink" with the
  // overlay every minute. Polling re-reads the value each tick so the
  // refresh wins the race.
  useEffect(() => {
    if (!auth.isAuthenticated) return;
    const fireExpired = () => {
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
    };
    const tick = () => {
      const expiry = parseInt(localStorage.getItem('acp_token_expiry') || '0', 10);
      if (expiry > 0 && Date.now() >= expiry) {
        fireExpired();
      }
    };
    // Immediate check + every 5s
    tick();
    const interval = setInterval(tick, 5_000);
    return () => clearInterval(interval);
  }, [auth.isAuthenticated]);

  // Memoize the context value so consumers don't see a new object on every
  // render. Without this, every consumer that puts an Auth-context-derived
  // value in a useCallback/useEffect dep would re-bind every render even
  // though the underlying values are unchanged.
  const authContextValue = useMemo(
    () => ({ ...auth, updateAuth, addToast }),
    [auth, updateAuth, addToast],
  );

  return (
    <ErrorBoundary>
      <AuthContext.Provider value={authContextValue}>
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

            <Suspense fallback={<RouteFallback />}>
            <Routes>
              {/* Clerk's <SignIn /> / <SignUp /> components own sub-routes
                  (e.g. /signup/verify-email-address) — the `/*` is required. */}
              <Route path="/login/*"  element={auth.isAuthenticated ? <Navigate to="/dashboard" /> : <Login />} />
              <Route path="/signup/*" element={auth.isAuthenticated ? <Navigate to="/dashboard" /> : <Signup />} />
              <Route path="/onboarding" element={<ProtectedRoute><OnboardingWizard /></ProtectedRoute>} />
              <Route path="/shadow-review" element={<ProtectedRoute><ShadowModeReview /></ProtectedRoute>} />
              <Route path="/threat-graph"  element={<ProtectedRoute><ThreatGraph /></ProtectedRoute>} />

              {/* Sprint 11 — public-facing marketing landing. Authenticated
                  users skip straight to /dashboard; everyone else sees the
                  value-prop page with the founder's positioning. */}
              <Route
                path="/"
                element={auth.isAuthenticated ? <Navigate to="/dashboard" /> : <Landing />}
              />
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
              {/* Sprint 17 — Aegis for Teams: per-employee Claude usage + spend */}
              <Route path="/team"              element={<ProtectedRoute><Team /></ProtectedRoute>} />
              {/* Sprint 17.6 — per-employee drill-down (token burn + recent calls) */}
              <Route path="/team/:email"       element={<ProtectedRoute><EmployeeProfile /></ProtectedRoute>} />
              {/* Sprint 15 — Unified replay (single-URL audit timeline) */}
              <Route path="/replay/:request_id" element={<ProtectedRoute><Replay /></ProtectedRoute>} />

              {/* Sprint 6 — ExecutiveDashboard merged into /dashboard. */}
              <Route path="/executive-summary" element={<Navigate to="/dashboard" replace />} />
              <Route path="/executive"         element={<Navigate to="/dashboard" replace />} />

              <Route path="*" element={<Navigate to="/dashboard" />} />
            </Routes>
            </Suspense>

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
