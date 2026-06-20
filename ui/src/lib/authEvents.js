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

// Demo sessions (anonymous "Try live demo" CTA) carry a tightly-scoped
// 30-min JWT against a shared sandbox tenant. Not every endpoint is
// authorised for that token (e.g. /webhooks/config requires a real user
// row). We deliberately swallow auth failures in demo mode so the
// occasional 401 doesn't blow the session out — the affected widget
// just renders empty.
function isDemoMode() {
  try {
    return (
      sessionStorage.getItem('aegis_demo_mode') === '1' ||
      new URLSearchParams(window.location.search).get('demo') === '1'
    )
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
