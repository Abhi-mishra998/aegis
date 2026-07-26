// Pathname → browser tab title map. Kept in one place so a route rename in
// App.jsx only requires one follow-up edit here. Values are the tab suffix
// only; TitleUpdater prepends "Aegis · " (matching the index.html base title
// domain but shorter so the tab text stays readable at narrow widths).
//
// Dynamic segments are matched by longest static-prefix (`/agents/:id`,
// `/team/:email`, `/replay/:request_id`) so `/agents/abc` still gets a
// meaningful title without the id leaking into the tab.

const STATIC_TITLES = {
  '/':                  'Home',
  '/login':             'Sign in',
  '/signup':            'Create workspace',
  '/onboarding':        'Onboarding',
  '/trust':             'Trust Center',
  '/status':            'System Status',
  '/security':          'Security & Disclosure',
  '/security/policy':   'Security Policy',
  '/security/report':   'Report a Vulnerability',

  '/dashboard':         'Dashboard',
  '/team':              'Team',
  '/live-feed':         'Live Feed',
  '/agents':            'Agents',
  '/incidents':         'Incidents',
  '/policies':          'Policies',
  '/approval-inbox':    'Approval Inbox',
  '/compliance':        'Compliance',
  '/settings':          'Settings',

  '/audit-logs':        'Audit Logs',
  '/forensics':         'Forensics',
  '/playground':        'Agent Playground',
  '/threat-intel':      'Threat Intel',
  '/evaluation':        'Evaluation',
  '/playbooks':         'Playbooks',
  '/auto-response':     'Auto-Response',
  '/identity-graph':    'Identity Graph',
  '/threat-graph':      'Threat Graph',
  '/shadow-mode':       'Shadow Mode',
  '/shadow-review':     'Shadow Review',
  '/flight-recorder':   'Flight Recorder',
  '/decision-explorer': 'Decision Explorer',
  '/session-explorer':  'Session Explorer',
  '/fleet':             'Fleet',

  '/system-health':     'System Health',
  '/lifecycle':         'Lifecycle Admin',
  '/kill-switch':       'Kill Switch',
  '/admin':             'Admin Console',
  '/developer':         'Developer',
  '/notifications':     'Notifications',
  '/users':             'User Management',
  '/sso':               'SSO Settings',
};

// Dynamic-segment routes: longest-prefix wins. Order matters — most specific first.
const PREFIX_TITLES = [
  ['/agents/',    'Agent'],
  ['/team/',      'Team member'],
  ['/replay/',    'Replay'],
  ['/login/',     'Sign in'],
  ['/signup/',    'Create workspace'],
];

export const BASE_TITLE = 'Aegis';

export function titleForPath(pathname) {
  if (!pathname) return BASE_TITLE;
  const clean = pathname.replace(/\/+$/, '') || '/';
  const exact = STATIC_TITLES[clean];
  if (exact) return `${exact} · ${BASE_TITLE}`;
  for (const [prefix, label] of PREFIX_TITLES) {
    if (clean.startsWith(prefix)) return `${label} · ${BASE_TITLE}`;
  }
  return BASE_TITLE;
}
