// Module-local: callers consume the wrapper functions below, not the raw
// event-name constant. Keep this private to prevent stringly-typed
// `window.addEventListener('acp:auth:failure', …)` from leaking to callers.
const AUTH_EVENTS = {
  FAILURE: 'acp:auth:failure',
}

const REASON_LABELS = {
  session_expired:  'Session Expired',
  unauthorized:     'Unauthorized Access',
  token_invalid:    'Token Invalid',
  token_revoked:    'Token Revoked',
  csrf_failure:     'CSRF Validation Failed',
}

// Demo sessions (anonymous "Spawn demo workspace" CTA) carry a tightly-
// scoped 30-min JWT against a fresh per-visitor sandbox tenant. Not every
// endpoint is authorised for that token — /auth/me requires
// typ=ACP_ACCESS which the demo JWT doesn't carry; some user-management
// reads require a real user row, etc. We deliberately swallow auth
// failures in demo mode so the occasional 401 doesn't blow the session
// out: the affected widget just renders empty + the rest of the dashboard
// keeps working.
//
// 2026-06-24: the canonical demo marker is `session_kind=demo` in
// sessionStorage (set by the IIFE at main.jsx:17 when a ?demo_token=…
// URL arrives). The legacy `aegis_demo_mode=1` flag from an earlier
// sprint was never wired to anything that actually sets it — every
// demo session ran with `isDemoMode()` returning False, which caused
// /auth/me 401s on a brand-new demo to fire the SOC incident overlay,
// redirect to /login, and (after the demo CTA spawned a fresh tenant)
// loop. Adding the canonical marker fixes both the false-positive
// incident AND the resulting redirect loop.
function isDemoMode() {
  try {
    if (sessionStorage.getItem('session_kind') === 'demo') return true
    if (sessionStorage.getItem('aegis_demo_mode') === '1') return true
    return new URLSearchParams(window.location.search).get('demo') === '1'
  } catch {
    return false
  }
}

export function emitAuthFailure({ reason = 'unauthorized', url, statusCode } = {}) {
  if (isDemoMode()) {
    // Telemetry-friendly trace, but no SOC overlay + no redirect.
    console.debug('[demo] suppressed auth failure', { reason, url, statusCode })
    return
  }
  window.dispatchEvent(
    new CustomEvent(AUTH_EVENTS.FAILURE, {
      detail: {
        incidentId: crypto.randomUUID(),
        reason,
        reasonLabel: REASON_LABELS[reason] ?? REASON_LABELS.unauthorized,
        url:         url ?? window.location.pathname,
        statusCode:  statusCode ?? null,
        timestamp:   new Date().toISOString(),
      },
    })
  )
}

export function onAuthFailure(handler) {
  window.addEventListener(AUTH_EVENTS.FAILURE, handler)
  return () => window.removeEventListener(AUTH_EVENTS.FAILURE, handler)
}
